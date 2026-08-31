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
