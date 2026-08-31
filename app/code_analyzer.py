"""
Analisador estático simples baseado em AST (Abstract Syntax Tree).

Objetivo do MVP: dado o código-fonte de um arquivo Python alterado em um PR,
identificar as funções definidas/alteradas e calcular métricas básicas
(quantidade de linhas, complexidade aproximada, presença de tratamento de
exceção) para ajudar a decidir quais funções merecem sugestão de teste.
"""
import ast
from dataclasses import dataclass, field


@dataclass
class FunctionInfo:
    name: str
    start_line: int
    end_line: int
    num_lines: int
    num_branches: int          # if/for/while/try -> aproximação de complexidade ciclomática
    has_try_except: bool
    args: list = field(default_factory=list)
    docstring: str | None = None
    source: str = ""
    called_names: list = field(default_factory=list)  # nomes de funções chamadas dentro dela


class CodeAnalyzer:
    """Extrai funções e métricas simples de um arquivo Python."""

    def analyze_source(self, source_code: str) -> list[FunctionInfo]:
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            # Código inválido/incompleto (comum em diffs parciais) -> não quebra o pipeline
            return []

        source_lines = source_code.splitlines()
        functions: list[FunctionInfo] = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = node.lineno
                end = getattr(node, "end_lineno", start)
                num_lines = end - start + 1

                branches = sum(
                    1 for n in ast.walk(node)
                    if isinstance(n, (ast.If, ast.For, ast.While, ast.Try))
                )
                has_try_except = any(
                    isinstance(n, ast.Try) for n in ast.walk(node)
                )

                # Identifica chamadas diretas a outras funções dentro do corpo
                # (ex: calcular_media() chamando sum()). Usado depois para
                # buscar o código dessas dependências e dar mais contexto à IA.
                called_names = sorted({
                    n.func.id
                    for n in ast.walk(node)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id != node.name  # ignora recursão direta
                })

                func_source = "\n".join(source_lines[start - 1:end])

                functions.append(
                    FunctionInfo(
                        name=node.name,
                        start_line=start,
                        end_line=end,
                        num_lines=num_lines,
                        num_branches=branches,
                        has_try_except=has_try_except,
                        args=[a.arg for a in node.args.args],
                        docstring=ast.get_docstring(node),
                        source=func_source,
                        called_names=called_names,
                    )
                )

        return functions

    def summarize(self, functions: list[FunctionInfo]) -> dict:
        """Gera um resumo agregado, útil para logs/dashboard futuro."""
        if not functions:
            return {
                "total_functions": 0,
                "avg_lines": 0,
                "avg_complexity": 0,
                "functions_without_try_except": 0,
            }

        total = len(functions)
        avg_lines = sum(f.num_lines for f in functions) / total
        avg_complexity = sum(f.num_branches for f in functions) / total
        without_try = sum(1 for f in functions if not f.has_try_except)

        return {
            "total_functions": total,
            "avg_lines": round(avg_lines, 1),
            "avg_complexity": round(avg_complexity, 1),
            "functions_without_try_except": without_try,
        }


def build_dependency_context(
    target: FunctionInfo, all_functions: list[FunctionInfo], max_deps: int = 3
) -> str:
    """
    Monta um bloco de texto com o código de outras funções, definidas no
    mesmo arquivo, que a função alvo chama. Isso dá à IA visibilidade sobre
    o comportamento das dependências, não só da função isolada.

    Limita a `max_deps` funções para não estourar o tamanho do prompt.
    """
    by_name = {f.name: f for f in all_functions}
    dependencies = [
        by_name[name]
        for name in target.called_names
        if name in by_name and name != target.name
    ][:max_deps]

    if not dependencies:
        return ""

    blocks = []
    for dep in dependencies:
        blocks.append(f"# Função auxiliar: {dep.name}()\n{dep.source}")

    return "\n\n".join(blocks)


def extract_changed_python_files(diff_text: str) -> dict[str, str]:
    """
    Placeholder simples para extrair, de um diff unificado (formato git),
    o conteúdo "depois" de cada arquivo .py alterado.

    No MVP real, isso normalmente é substituído por uma chamada à API do
    GitHub (GET /repos/{owner}/{repo}/pulls/{pr}/files), que já retorna
    o patch de cada arquivo. Esta função fica aqui como fallback/local test.
    """
    files: dict[str, str] = {}
    current_file = None
    buffer: list[str] = []

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            if current_file and current_file.endswith(".py"):
                files[current_file] = "\n".join(buffer)
            current_file = line[6:]
            buffer = []
        elif line.startswith("+") and not line.startswith("+++"):
            buffer.append(line[1:])

    if current_file and current_file.endswith(".py"):
        files[current_file] = "\n".join(buffer)

    return files