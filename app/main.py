"""
PR Reviewer AI — API principal.

Fluxo:
1. Recebe uma requisição (via GitHub Action) informando owner/repo/pr_number.
2. Busca os arquivos alterados no PR através da API do GitHub.
3. Para cada arquivo .py alterado, extrai as funções com AST.
4. Envia cada função relevante para o LLM, que sugere casos de teste.
5. Posta um comentário de review por função, ancorado na linha alterada
   (quando possível — ver `_post_suggestion`), e registra a sugestão no
   banco para depois receber feedback do dev via webhook.
"""
import csv
import hashlib
import hmac
import io
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from app.config import settings
from app.code_analyzer import CodeAnalyzer, build_dependency_context
from app.dashboard import render_dashboard
from app.diff_utils import commentable_lines, pick_comment_line
from app.github_client import GitHubClient
from app.llm_client import LLMClient
from app.context_gatherer import ContextGatherer
from app.comment_formatter import (
    format_pr_comment,
    format_single_suggestion_comment,
    format_summary_comment,
)
from app.db import (
    find_suggestion_by_comment_id,
    find_unchanged_suggestion,
    get_all_suggestions,
    get_stats,
    get_top_rated_examples,
    init_db,
    save_feedback,
    save_suggestion,
)
from app.models import Suggestion

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Cria as tabelas no banco (SQLite local ou Postgres, ver DATABASE_URL)
    # se ainda não existirem. Idempotente — seguro rodar em todo startup.
    init_db()
    yield


app = FastAPI(
    title="PR Reviewer AI",
    description="Sugere casos de teste automaticamente em Pull Requests.",
    version="0.1.0",
    lifespan=lifespan,
)


def _build_extra_context(parts: list[str], max_chars: int) -> str | None:
    """
    Junta os blocos de contexto extra (dependências + testes relacionados) e
    aplica um teto de caracteres. Com cota gratuita de tokens/requisições
    (ex: Gemini Flash free tier), preferimos truncar a mandar um prompt
    gigante e estourar o limite.
    """
    if not parts:
        return None

    combined = "\n\n".join(parts)
    if len(combined) > max_chars:
        combined = (
            combined[:max_chars].rstrip()
            + "\n[...contexto truncado para caber no orçamento de tokens...]"
        )
    return combined


def _functions_touched_by_diff(functions: list, commentable: set[int]) -> list:
    """
    Filtra, entre as funções extraídas de um arquivo, só as que têm de fato
    alguma linha alterada no diff do PR. Sem esse filtro, um PR que só mexe
    em código fora de função (ex: um print solto no nível do módulo) faria a
    IA analisar arbitrariamente "a primeira função do arquivo" — que não tem
    nada a ver com a mudança.
    """
    return [
        f for f in functions
        if pick_comment_line(f.start_line, f.end_line, commentable) is not None
    ]


def _post_suggestion(
    github: GitHubClient,
    owner: str,
    repo: str,
    pr_number: int,
    commit_sha: str,
    filename: str,
    func,
    analysis: dict,
    commentable: set[int],
    llm_model: str | None = None,
    code_hash: str | None = None,
) -> int | None:
    """
    Tenta postar a sugestão como um comentário de review ancorado na linha
    alterada da função — isso permite que o dev responda em thread, e é o
    que possibilita correlacionar feedback com a sugestão exata depois (via
    webhook, usando in_reply_to_id).

    Sempre registra a sugestão no banco (com ou sem comentário ancorado).
    Retorna o id do comentário postado no GitHub, ou None quando não foi
    possível ancorar (função não está de fato no diff) ou a API do GitHub
    rejeitou o comentário por qualquer motivo — nesses casos quem chamou
    deve incluir essa sugestão no comentário único de fallback.
    """
    comment_id = None
    line = pick_comment_line(func.start_line, func.end_line, commentable)

    if line is not None:
        body = format_single_suggestion_comment(filename, func.name, analysis)
        try:
            comment = github.post_review_comment(
                owner, repo, pr_number, commit_sha, filename, line, body
            )
            comment_id = comment.get("id")
        except Exception:
            comment_id = None  # cai no fallback (comentário único no final)

    try:
        save_suggestion(
            Suggestion(
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                commit_sha=commit_sha,
                filename=filename,
                function_name=func.name,
                risk_level=analysis.get("risk_level", "desconhecido"),
                risk_reason=analysis.get("risk_reason", ""),
                suggested_tests=analysis.get("suggested_tests", []),
                github_comment_id=comment_id,
                llm_model=llm_model,
                code_hash=code_hash,
            )
        )
    except Exception:
        # Falha ao persistir não pode derrubar a análise inteira — o
        # comentário (se houver) já foi postado no GitHub nesse ponto. Sem
        # o registro no banco, essa sugestão específica só fica sem feedback
        # rastreável depois, mas o PR não fica sem review por causa disso.
        pass

    return comment_id


_RATING_PATTERN = re.compile(
    r"^/rate\s+(bom|ruim|positivo|negativo|\U0001F44D|\U0001F44E)\b\s*[:\-]?\s*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)
_POSITIVE_RATING_WORDS = {"bom", "positivo", "\U0001F44D"}


def _parse_rating(body: str) -> tuple[str, str | None] | None:
    """
    Procura um comando `/rate bom` ou `/rate ruim` (aceita variações e um
    motivo opcional depois) no texto de uma reply. Retorna None se não achar.
    """
    match = _RATING_PATTERN.search(body or "")
    if not match:
        return None

    raw_rating, reason = match.groups()
    rating = "positivo" if raw_rating.lower() in _POSITIVE_RATING_WORDS else "negativo"
    return rating, (reason.strip() or None)


class ReviewRequest(BaseModel):
    owner: str
    repo: str
    pr_number: int
    head_ref: str  # branch/commit do PR, usado para buscar o conteúdo dos arquivos


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/stats")
def stats():
    """
    Resumo agregado de todas as sugestões geradas até agora — total,
    distribuição por nível de risco, por avaliação do dev e por modelo do
    LLM usado. Pensado para alimentar direto a análise do experimento do
    TCC, sem precisar exportar dados manualmente do banco.
    """
    return get_stats()


@app.get("/stats/export.csv")
def export_stats_csv():
    """
    Exporta todas as sugestões (uma por linha) em CSV, pronto pra abrir em
    Excel/Google Sheets — mais útil pra análise do experimento do TCC do que
    só o resumo agregado do /stats.
    """
    suggestions = get_all_suggestions()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "id", "owner", "repo", "pr_number", "filename", "function_name",
        "risk_level", "risk_reason", "llm_model", "rating", "feedback_reason",
        "created_at", "feedback_at",
    ])
    for s in suggestions:
        writer.writerow([
            s.id, s.owner, s.repo, s.pr_number, s.filename, s.function_name,
            s.risk_level, s.risk_reason, s.llm_model, s.rating,
            s.feedback_reason, s.created_at, s.feedback_at,
        ])

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=pr_reviewer_suggestions.csv"},
    )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """Painel visual com o resumo do /stats + últimas sugestões geradas."""
    recent = get_all_suggestions()[-20:][::-1]  # 20 mais recentes, mais nova primeiro
    return render_dashboard(get_stats(), recent)


@app.post("/review")
def review_pull_request(payload: ReviewRequest):
    """
    Endpoint principal, chamado pela GitHub Action a cada PR aberto/atualizado.
    """
    github = GitHubClient()
    analyzer = CodeAnalyzer()
    llm = LLMClient()
    context_gatherer = ContextGatherer(github_client=github)

    try:
        pr_files = github.get_pr_files(payload.owner, payload.repo, payload.pr_number)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar arquivos do PR: {exc}")

    # Busca a árvore de arquivos do repositório uma única vez (reutilizada
    # para todas as funções analisadas neste PR, evitando chamadas repetidas).
    try:
        repo_tree = github.get_repo_tree(payload.owner, payload.repo, payload.head_ref)
    except Exception:
        repo_tree = []

    # Exemplos de sugestões que devs avaliaram como boas (few-shot), usados
    # pra calibrar o estilo/qualidade esperado das próximas análises — a
    # forma de "aprendizado" possível sem fine-tuning (fora de alcance no
    # tier gratuito do Gemini).
    try:
        few_shot_examples = [
            {
                "function_name": ex.function_name,
                "risk_level": ex.risk_level,
                "risk_reason": ex.risk_reason,
                "suggested_tests": ex.suggested_tests,
            }
            for ex in get_top_rated_examples(limit=2)
        ] or None
    except Exception:
        few_shot_examples = None

    all_results = []  # toda função analisada nesta rodada, ancorada ou não
    functions_analyzed = 0
    functions_skipped_unchanged = 0

    # Primeira passada: baixa e analisa TODOS os arquivos .py relevantes do PR
    # antes de consultar a IA. Isso permite resolver dependências que atravessam
    # arquivos (ex: função em utils.py chamada por uma função em main.py) — sem
    # essa passada só teríamos visibilidade do arquivo sendo processado no momento.
    files_functions: dict[str, list] = {}
    files_patches: dict[str, str] = {}

    for file_info in pr_files:
        filename = file_info["filename"]

        if not filename.endswith(settings.supported_extensions):
            continue
        if file_info.get("status") == "removed":
            continue

        try:
            content = github.get_file_content(
                payload.owner, payload.repo, filename, payload.head_ref
            )
        except Exception:
            # Arquivo pode ter sido movido/deletado depois; ignora sem quebrar o fluxo
            continue

        files_functions[filename] = analyzer.analyze_source(content)
        files_patches[filename] = file_info.get("patch", "")

    for filename, functions in files_functions.items():
        commentable = commentable_lines(files_patches.get(filename))
        # Funções de outros arquivos alterados no mesmo PR, indexadas por nome,
        # para resolver dependências que a função atual chama mas que não estão
        # definidas neste arquivo. Em caso de nomes duplicados entre arquivos,
        # a última ocorrência processada "vence" — aceitável para o MVP.
        external_functions = {
            f.name: f
            for other_filename, other_functions in files_functions.items()
            if other_filename != filename
            for f in other_functions
        }

        changed_functions = _functions_touched_by_diff(functions, commentable)

        # Limitado a MAX_FUNCTIONS_PER_PR funções por arquivo — cada uma
        # custa 1 requisição ao LLM, e planos gratuitos costumam ter cota de
        # requisições por dia (RPD) baixa. Ajuste essa env var conforme a
        # cota do modelo configurado em LLM_MODEL.
        for func in changed_functions[: settings.max_functions_per_pr]:
            if func.num_lines > settings.max_diff_lines:
                continue  # evita mandar funções gigantes para a IA

            code_hash = hashlib.sha256(func.source.encode("utf-8")).hexdigest()

            # Se um push anterior neste mesmo PR já analisou essa função com
            # esse EXATO código, pula — evita gastar cota do LLM reanalisando
            # uma função que não mudou (comum quando um "synchronize" só
            # tocou outra parte do arquivo).
            try:
                unchanged = find_unchanged_suggestion(
                    payload.owner, payload.repo, payload.pr_number,
                    filename, func.name, code_hash,
                )
            except Exception:
                unchanged = None

            if unchanged is not None:
                functions_skipped_unchanged += 1
                continue

            # Nomes de dependências (funções chamadas, existentes no mesmo
            # arquivo) também entram na busca por testes já existentes —
            # um teste de validar_numero() é relevante mesmo quando estamos
            # analisando calcular_porcentagem(), que a utiliza por dentro.
            dependency_names = [
                f.name for f in functions
                if f.name in func.called_names and f.name != func.name
            ]

            try:
                related_tests = context_gatherer.find_related_tests(
                    payload.owner,
                    payload.repo,
                    payload.head_ref,
                    func.name,
                    tree=repo_tree,
                    also_check_names=dependency_names,
                )
                tests_summary = context_gatherer.build_context_summary(related_tests)
            except Exception:
                tests_summary = None

            # Contexto de dependências: código de outras funções (do mesmo
            # arquivo, ou só a assinatura se forem de outro arquivo do PR) que
            # esta função chama. Ajuda a IA a entender o comportamento
            # completo, não só um pedaço isolado.
            dependency_context = build_dependency_context(
                func, functions, external_functions=external_functions
            )

            context_parts = []
            if dependency_context:
                context_parts.append(
                    f"Funções auxiliares chamadas por esta função:\n{dependency_context}"
                )
            if tests_summary:
                context_parts.append(tests_summary)

            extra_context = _build_extra_context(
                context_parts, settings.max_extra_context_chars
            )

            try:
                analysis = llm.suggest_tests_for_function(
                    func,
                    filename,
                    extra_context=extra_context,
                    few_shot_examples=few_shot_examples,
                )
            except Exception as exc:
                analysis = {
                    "risk_level": "desconhecido",
                    "risk_reason": f"Erro ao consultar IA: {exc}",
                    "suggested_tests": [],
                }

            functions_analyzed += 1

            comment_id = _post_suggestion(
                github,
                payload.owner,
                payload.repo,
                payload.pr_number,
                payload.head_ref,
                filename,
                func,
                analysis,
                commentable,
                llm_model=llm.model,
                code_hash=code_hash,
            )
            all_results.append(
                {
                    "filename": filename,
                    "function_name": func.name,
                    "analysis": analysis,
                    "anchored": comment_id is not None,
                }
            )

    # Comentário final: se nenhuma função foi analisada, mantém a mensagem
    # simples de "nada encontrado"; caso contrário, posta sempre um resumo
    # (contagem por risco, quantas ficaram inline) — e, quando houver
    # sugestões que não puderam ser ancoradas numa linha do diff, o conteúdo
    # completo delas entra dentro desse mesmo resumo (fallback).
    if functions_analyzed == 0:
        comment_preview = format_pr_comment([])
    else:
        comment_preview = format_summary_comment(all_results)

    try:
        github.post_comment(
            payload.owner, payload.repo, payload.pr_number, comment_preview
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Erro ao postar comentário no PR: {exc}"
        )

    anchored_count = sum(1 for item in all_results if item["anchored"])

    return {
        "status": "success",
        "functions_analyzed": functions_analyzed,
        "functions_with_inline_comment": anchored_count,
        "functions_skipped_unchanged": functions_skipped_unchanged,
        "comment_preview": comment_preview,
    }


@app.post("/webhook/github")
async def github_webhook(request: Request):
    """
    Recebe eventos de webhook do GitHub. Hoje só é usado para capturar
    feedback: quando o dev responde (reply em thread) a um comentário de
    review da IA com "/rate bom" ou "/rate ruim", correlacionamos com a
    sugestão original via `in_reply_to_id` e registramos no banco.

    Configuração necessária no repositório (Settings > Webhooks):
    - Payload URL: <URL desta API>/webhook/github
    - Content type: application/json
    - Secret: o mesmo valor configurado em GITHUB_WEBHOOK_SECRET
    - Eventos: apenas "Pull request review comments"
    """
    raw_body = await request.body()

    if settings.github_webhook_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            settings.github_webhook_secret.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=401, detail="Assinatura do webhook inválida")

    if request.headers.get("X-GitHub-Event") != "pull_request_review_comment":
        return {"status": "ignored", "reason": "evento não tratado"}

    payload = await request.json()
    if payload.get("action") != "created":
        return {"status": "ignored", "reason": "não é um comentário novo"}

    comment = payload.get("comment", {})
    in_reply_to_id = comment.get("in_reply_to_id")
    if in_reply_to_id is None:
        return {"status": "ignored", "reason": "não é uma resposta a outro comentário"}

    parsed = _parse_rating(comment.get("body", ""))
    if parsed is None:
        return {"status": "ignored", "reason": "reply não contém um comando /rate"}

    rating, reason = parsed

    suggestion = find_suggestion_by_comment_id(in_reply_to_id)
    if suggestion is None:
        return {"status": "ignored", "reason": "comentário original não encontrado no banco"}

    save_feedback(suggestion.id, rating=rating, reason=reason)
    return {"status": "ok", "suggestion_id": suggestion.id, "rating": rating}