"""
Localiza testes já existentes no repositório que possam estar relacionados
a uma função específica, para evitar que a IA sugira testes duplicados.

Estratégia simples (v2 do MVP): identificar arquivos que "parecem" ser de
teste (nome contém "test") e verificar, por busca de texto simples, se o
nome da função aparece dentro deles. Não é uma análise semântica profunda,
mas já reduz bastante a redundância nas sugestões.
"""
from app.github_client import GitHubClient


def _looks_like_test_file(path: str) -> bool:
    """Heurística simples para identificar arquivos de teste Python."""
    filename = path.rsplit("/", 1)[-1].lower()
    return (
        filename.endswith(".py")
        and (filename.startswith("test_") or filename.endswith("_test.py"))
    )


class ContextGatherer:
    def __init__(self, github_client: GitHubClient | None = None):
        self.github = github_client or GitHubClient()

    def find_related_tests(
        self,
        owner: str,
        repo: str,
        ref: str,
        function_name: str,
        max_files_to_check: int = 15,
        tree: list[dict] | None = None,
    ) -> list[str]:
        """
        Retorna uma lista de caminhos de arquivos de teste que já mencionam
        o nome da função analisada (indício de que já existe alguma
        cobertura para ela).

        Se `tree` for fornecida (lista já obtida via get_repo_tree), ela é
        reutilizada em vez de buscar a árvore do repositório de novo — útil
        quando várias funções do mesmo PR são analisadas em sequência.
        """
        if tree is None:
            try:
                tree = self.github.get_repo_tree(owner, repo, ref)
            except Exception:
                # Se não conseguir listar a árvore do repo, segue sem
                # contexto extra em vez de quebrar a análise inteira.
                return []

        test_file_paths = [
            item["path"]
            for item in tree
            if item.get("type") == "blob" and _looks_like_test_file(item["path"])
        ]

        related_files = []
        for path in test_file_paths[:max_files_to_check]:
            try:
                content = self.github.get_file_content(owner, repo, path, ref)
            except Exception:
                continue

            if function_name in content:
                related_files.append(path)

        return related_files

    def build_context_summary(self, related_tests: list[str]) -> str:
        """Formata os arquivos encontrados em um texto curto para o prompt da IA."""
        if not related_tests:
            return "Nenhum teste existente encontrado para esta função no repositório."

        files_list = ", ".join(related_tests)
        return (
            f"Já existem arquivos de teste que mencionam esta função: {files_list}. "
            "Considere isso ao sugerir novos testes: evite duplicar cenários que "
            "provavelmente já estão cobertos, e foque em lacunas."
        )