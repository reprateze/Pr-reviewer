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
from app.code_analyzer import CodeAnalyzer
from app.github_client import GitHubClient
from app.llm_client import LLMClient
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

    try:
        pr_files = github.get_pr_files(payload.owner, payload.repo, payload.pr_number)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar arquivos do PR: {exc}")

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

        for func in functions[:1]:
            if func.num_lines > settings.max_diff_lines:
                continue  # evita mandar funções gigantes para a IA

            try:
                analysis = llm.suggest_tests_for_function(func, filename)
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
