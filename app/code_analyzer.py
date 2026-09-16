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

    # Quando a função é método, a classe a que pertence. Sem isso, o método
    # ia para a IA solto — sem saber de quem herda nem onde os atributos que
    # ele usa (`self.limit` e afins) são definidos.
    class_name: str | None = None
    class_header: str | None = None  # "class X(Base):" + primeira linha da docstring


def _extract_called_names(node) -> set[str]:
    """
    Nomes de funções/métodos chamados dentro de uma função.

    Captura duas formas de chamada:
    - `funcao(...)`        -> ast.Name, o nome é `.id`
    - `self.metodo(...)`   -> ast.Attribute, o nome é `.attr`

    A segunda forma faz toda a diferença em código orientado a objetos.
    Capturando só `ast.Name`, uma classe inteira ficava sem contexto: numa
    medição real, o método analisado chamava `self._normkey()`,
    `self.popitem()` e `super().__setitem__()`, e o contexto de dependência
    enviado à IA saía VAZIO — ela recebia oito linhas soltas, sem nada do
    que elas usavam.

    Nomes que não existem no arquivo (ex: `response.json()`) entram aqui mas
    são descartados depois, na montagem do contexto, que só inclui o que
    encontra de fato.
    """
    nomes: set[str] = set()

    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue

        if isinstance(n.func, ast.Name):
            nome = n.func.id
        elif isinstance(n.func, ast.Attribute):
            nome = n.func.attr
        else:
            continue

        if nome != node.name:  # ignora recursão direta
            nomes.add(nome)

    return nomes


def _nome_da_base(node) -> str | None:
    """
    Nome de uma classe base, lidando com as três formas que aparecem em
    código real:

    - `class X(Base)`                 -> ast.Name
    - `class X(modulo.Base)`          -> ast.Attribute
    - `class X(OrderedDict[K, V])`    -> ast.Subscript (base genérica)

    A terceira é comum em código com tipagem e foi o caso que apareceu na
    medição: sem tratá-la, a classe ia para a IA como `class LocalCache:`,
    escondendo justamente a herança de `OrderedDict` que explicava de onde
    vinha o `popitem()` usado no método analisado.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _nome_da_base(node.value)
    return None


def _mapa_de_classes(tree) -> list[tuple[int, int, str, str]]:
    """
    Para cada classe do arquivo: (linha inicial, linha final, nome, cabeçalho).

    O cabeçalho (`class X(Base):` + docstring) é o que permite dizer à IA de
    onde o método veio e de quem ele herda — informação que muda a análise e
    que hoje nunca era enviada.
    """
    classes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        bases = [nome for nome in (_nome_da_base(b) for b in node.bases) if nome]

        cabecalho = f"class {node.name}({', '.join(bases)}):" if bases else f"class {node.name}:"
        doc = ast.get_docstring(node)
        if doc:
            primeira_linha = doc.strip().split("\n", 1)[0]
            cabecalho += f'\n    """{primeira_linha}"""'

        classes.append(
            (node.lineno, getattr(node, "end_lineno", node.lineno), node.name, cabecalho)
        )

    return classes


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
        classes = _mapa_de_classes(tree)

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

                called_names = sorted(_extract_called_names(node))

                func_source = "\n".join(source_lines[start - 1:end])

                # A classe mais interna que contém esta função (classes
                # aninhadas existem; a de menor intervalo é a dona do método).
                donas = [
                    (ini, fim, nome, cab) for ini, fim, nome, cab in classes
                    if ini <= start and end <= fim
                ]
                dona = min(donas, key=lambda c: c[1] - c[0]) if donas else None

                functions.append(
                    FunctionInfo(
                        class_name=dona[2] if dona else None,
                        class_header=dona[3] if dona else None,
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


def format_function_signature(func: FunctionInfo) -> str:
    """
    Monta só a assinatura + docstring de uma função, sem o corpo. Usado para
    dar contexto de dependências definidas em OUTROS arquivos do PR sem pagar
    o custo (em tokens) de mandar o código inteiro delas.
    """
    header = f"def {func.name}({', '.join(func.args)}):"
    if func.docstring:
        return f'{header}\n    """{func.docstring}"""'
    return header


def build_class_context(
    target: FunctionInfo, all_functions: list[FunctionInfo]
) -> str:
    """
    Contexto da classe a que o método pertence: o cabeçalho (nome e classe
    base) e o `__init__`, onde os atributos que o método usa são definidos.

    Sem isso, um método chega à IA como um bloco solto. No defeito real que
    motivou esta função, a IA recebeu um `__setitem__` de oito linhas usando
    `self.limit` e `self.popitem()` — sem saber que a classe herdava de
    `OrderedDict` (de onde vem `popitem`) nem onde `self.limit` era definido.

    Retorna vazio para funções soltas (não-métodos), que não têm classe.
    """
    if not target.class_name:
        return ""

    partes = []
    if target.class_header:
        partes.append(target.class_header)

    init = next(
        (
            f for f in all_functions
            if f.name == "__init__"
            and f.class_name == target.class_name
            and f.name != target.name
        ),
        None,
    )
    if init:
        partes.append(init.source)

    if not partes:
        return ""

    return (
        f"Esta função é um método da classe abaixo. Cabeçalho da classe e "
        f"construtor (onde os atributos usados são definidos):\n"
        + "\n\n".join(partes)
    )


def build_dependency_context(
    target: FunctionInfo,
    all_functions: list[FunctionInfo],
    external_functions: dict[str, FunctionInfo] | None = None,
    max_deps: int = 3,
) -> str:
    """
    Monta um bloco de texto com o código de outras funções que a função alvo
    chama, para dar à IA visibilidade sobre o comportamento das dependências,
    não só da função isolada.

    - Dependências no MESMO arquivo (`all_functions`): manda o código completo.
    - Dependências de OUTROS arquivos alterados no mesmo PR
      (`external_functions`, um mapa {nome: FunctionInfo}): manda só a
      assinatura + docstring, bem mais barato em tokens do que o corpo inteiro.

    Limita a `max_deps` funções (somando as duas fontes) para não estourar o
    orçamento de contexto do prompt.
    """
    by_name = {f.name: f for f in all_functions}
    external_functions = external_functions or {}

    dependency_names = [
        name for name in dict.fromkeys(target.called_names) if name != target.name
    ]

    blocks = []
    for name in dependency_names:
        if len(blocks) >= max_deps:
            break
        if name in by_name:
            dep = by_name[name]
            blocks.append(f"# Função auxiliar: {dep.name}()\n{dep.source}")
        elif name in external_functions:
            dep = external_functions[name]
            blocks.append(
                f"# Função auxiliar (definida em outro arquivo do PR, só "
                f"assinatura para economizar tokens): {dep.name}()\n"
                f"{format_function_signature(dep)}"
            )

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