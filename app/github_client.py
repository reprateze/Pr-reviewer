"""
Client responsável por conversar com a API do GitHub:
- buscar os arquivos alterados de um Pull Request
- postar um comentário no PR com as sugestões geradas
"""
import httpx

from app.config import settings


class GitHubClient:
    def __init__(self, token: str | None = None):
        self.token = token or settings.github_token
        self.base_url = "https://api.github.com"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get_pr_files(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """
        Retorna a lista de arquivos alterados no PR.
        Cada item contém, entre outros campos: filename, status, patch (diff).
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}/files"
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers=self._headers())
            response.raise_for_status()
            return response.json()

    def get_file_content(
        self, owner: str, repo: str, path: str, ref: str
    ) -> str:
        """Busca o conteúdo completo de um arquivo em um determinado commit/branch."""
        url = f"{self.base_url}/repos/{owner}/{repo}/contents/{path}"
        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                url, headers=self._headers(), params={"ref": ref}
            )
            response.raise_for_status()
            data = response.json()

        import base64
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")

    def get_repo_tree(self, owner: str, repo: str, ref: str) -> list[dict]:
        """
        Lista todos os arquivos do repositório em um determinado commit/branch,
        de forma recursiva (inclui subpastas). Usado para localizar arquivos
        de teste já existentes no projeto.

        Cada item retornado tem, entre outros campos: path, type ("blob" para
        arquivo ou "tree" para pasta).
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/git/trees/{ref}"
        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                url, headers=self._headers(), params={"recursive": "1"}
            )
            response.raise_for_status()
            data = response.json()
        return data.get("tree", [])

    def post_comment(
        self, owner: str, repo: str, pr_number: int, body: str
    ) -> dict:
        """Posta um comentário no PR (issue comment, aparece na timeline do PR)."""
        url = f"{self.base_url}/repos/{owner}/{repo}/issues/{pr_number}/comments"
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                url, headers=self._headers(), json={"body": body}
            )
            response.raise_for_status()
            return response.json()

    def list_pull_requests(
        self,
        owner: str,
        repo: str,
        state: str = "closed",
        limit: int = 50,
    ) -> list[dict]:
        """
        Lista Pull Requests do repositório, do mais recente para o mais antigo.

        Usado pelo modo experimento, que roda a análise sobre PRs JÁ
        EXISTENTES (normalmente já mergeados) para gerar volume de dados —
        diferente do fluxo normal, que reage a um PR específico em tempo real.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls"
        pulls: list[dict] = []
        page = 1

        with httpx.Client(timeout=30.0) as client:
            while len(pulls) < limit:
                response = client.get(
                    url,
                    headers=self._headers(),
                    params={
                        "state": state,
                        "per_page": min(100, limit - len(pulls)),
                        "page": page,
                        "sort": "updated",
                        "direction": "desc",
                    },
                )
                response.raise_for_status()
                batch = response.json()
                if not batch:
                    break  # acabaram os PRs disponíveis
                pulls.extend(batch)
                page += 1

        return pulls[:limit]

    def list_issue_comments(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """Lista os comentários gerais (issue comments) já postados no PR."""
        url = f"{self.base_url}/repos/{owner}/{repo}/issues/{pr_number}/comments"
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers=self._headers(), params={"per_page": 100})
            response.raise_for_status()
            return response.json()

    def update_comment(self, owner: str, repo: str, comment_id: int, body: str) -> dict:
        """Edita um comentário geral (issue comment) já existente, no lugar."""
        url = f"{self.base_url}/repos/{owner}/{repo}/issues/comments/{comment_id}"
        with httpx.Client(timeout=30.0) as client:
            response = client.patch(url, headers=self._headers(), json={"body": body})
            response.raise_for_status()
            return response.json()

    def post_review_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_id: str,
        path: str,
        line: int,
        body: str,
    ) -> dict:
        """
        Posta um comentário de review ancorado numa linha específica do diff
        (aparece inline na aba "Files changed", como um comentário de revisão
        humano de verdade). Diferente de `post_comment`, esse tipo de
        comentário aceita reply em thread pelo dev — o que permite, via
        webhook, correlacionar o feedback com a sugestão exata que o gerou.

        `line` precisa ser uma linha que faça parte do diff desse arquivo
        nesse commit (ver app/diff_utils.py); caso contrário a API do GitHub
        responde 422.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}/comments"
        payload = {
            "body": body,
            "commit_id": commit_id,
            "path": path,
            "line": line,
            "side": "RIGHT",
        }
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, headers=self._headers(), json=payload)
            response.raise_for_status()
            return response.json()