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
        also_check_names: list[str] | None = None,
    ) -> dict[str, list[str]]:
        """
        Retorna um dict {nome_da_função: [arquivos de teste que a mencionam]}.

        Por padrão verifica só `function_name`, mas também aceita nomes
        extras via `also_check_names` (ex: as dependências dela) — assim
        conseguimos avisar a IA mesmo quando o teste existente cobre uma
        função auxiliar, não a função principal analisada.

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
                return {}

        test_file_paths = list(dict.fromkeys(
            item["path"]
            for item in tree
            if item.get("type") == "blob" and _looks_like_test_file(item["path"])
        ))  # dict.fromkeys remove duplicatas mantendo a ordem original

        names_to_check = [function_name] + list(also_check_names or [])
        results: dict[str, list[str]] = {name: [] for name in names_to_check}

        for path in test_file_paths[:max_files_to_check]:
            try:
                content = self.github.get_file_content(owner, repo, path, ref)
            except Exception:
                continue

            for name in names_to_check:
                if name in content and path not in results[name]:
                    results[name].append(path)

        return results

    def build_context_summary(self, related_tests: dict[str, list[str]]) -> str:
        """Formata os arquivos encontrados em um texto curto para o prompt da IA."""
        found = {name: files for name, files in related_tests.items() if files}

        if not found:
            return "Nenhum teste existente encontrado para esta função (ou suas dependências) no repositório."

        parts = []
        for name, files in found.items():
            files_list = ", ".join(files)
            parts.append(f"'{name}' já é mencionada em: {files_list}")

        return (
            "Testes já existentes encontrados no repositório: "
            + "; ".join(parts)
            + ". Considere isso ao sugerir novos testes: evite duplicar cenários "
            "que provavelmente já estão cobertos, e foque em lacunas."
        )