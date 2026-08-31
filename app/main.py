"""
PR Reviewer AI — API principal.

Fluxo:
1. Recebe uma requisição (via GitHub Action) informando owner/repo/pr_number.
2. Busca os arquivos alterados no PR através da API do GitHub.
3. Para cada arquivo .py alterado, extrai as funções com AST.
4. Envia cada função relevante para o LLM, que sugere casos de teste.
5. Formata tudo em Markdown e posta como comentário no PR.
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.code_analyzer import CodeAnalyzer, build_dependency_context
from app.github_client import GitHubClient
from app.llm_client import LLMClient
from app.context_gatherer import ContextGatherer
from app.comment_formatter import format_pr_comment

app = FastAPI(
    title="PR Reviewer AI",
    description="Sugere casos de teste automaticamente em Pull Requests.",
    version="0.1.0",
)


class ReviewRequest(BaseModel):
    owner: str
    repo: str
    pr_number: int
    head_ref: str  # branch/commit do PR, usado para buscar o conteúdo dos arquivos


@app.get("/health")
def health_check():
    return {"status": "ok"}


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

    results = []

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

        functions = analyzer.analyze_source(content)

        # NOTA: limitado a 1 função por arquivo por enquanto, para economizar
        # a cota gratuita da API (5 req/min, 20 req/dia) durante os testes.
        # Remover o "[:1]" quando estiver pronto para rodar o experimento
        # completo (ou trocar para um plano pago / outra chave).
        for func in functions[:1]:
            if func.num_lines > settings.max_diff_lines:
                continue  # evita mandar funções gigantes para a IA

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

            # Contexto de dependências: código de outras funções do mesmo
            # arquivo que esta função chama (ex: validar_email() dentro de
            # cadastrar_usuario()). Ajuda a IA a entender o comportamento
            # completo, não só um pedaço isolado.
            dependency_context = build_dependency_context(func, functions)

            context_parts = []
            if dependency_context:
                context_parts.append(
                    f"Funções auxiliares chamadas por esta função:\n{dependency_context}"
                )
            if tests_summary:
                context_parts.append(tests_summary)

            extra_context = "\n\n".join(context_parts) if context_parts else None

            try:
                analysis = llm.suggest_tests_for_function(
                    func, filename, extra_context=extra_context
                )
            except Exception as exc:
                analysis = {
                    "risk_level": "desconhecido",
                    "risk_reason": f"Erro ao consultar IA: {exc}",
                    "suggested_tests": [],
                }

            results.append(
                {
                    "filename": filename,
                    "function_name": func.name,
                    "analysis": analysis,
                }
            )

    comment_body = format_pr_comment(results)

    try:
        github.post_comment(payload.owner, payload.repo, payload.pr_number, comment_body)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erro ao postar comentário no PR: {exc}")

    return {
        "status": "success",
        "functions_analyzed": len(results),
        "comment_preview": comment_body,
    }