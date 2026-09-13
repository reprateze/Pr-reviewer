"""
Utilidades para trabalhar com o `patch` (diff unificado) que a API do GitHub
retorna para cada arquivo alterado num PR.

Usado para descobrir em quais linhas do arquivo NOVO é possível ancorar um
comentário de review — a API de review comments do GitHub só aceita `line`
quando ela faz parte de algum hunk do diff daquele arquivo.
"""
import re


def commentable_lines(patch: str | None) -> set[int]:
    """
    A partir do `patch` de um arquivo, retorna o conjunto de números de linha
    (no arquivo novo) que fazem parte de algum hunk do diff.
    """
    lines_ok: set[int] = set()
    new_line = None

    for line in (patch or "").splitlines():
        if line.startswith("@@"):
            # Ex: "@@ -12,7 +12,9 @@ def foo():" -> começo do hunk é a linha 12
            match = re.search(r"\+(\d+)", line)
            new_line = int(match.group(1)) if match else None
            continue

        if new_line is None:
            continue

        if line.startswith("-"):
            continue  # linha só existia no arquivo antigo; não avança a numeração nova

        # Linha de contexto (" ") ou adicionada ("+"): existe no arquivo novo.
        lines_ok.add(new_line)
        new_line += 1

    return lines_ok


def pick_comment_line(start_line: int, end_line: int, commentable: set[int]) -> int | None:
    """
    Escolhe a primeira linha dentro do intervalo [start_line, end_line] (ex:
    o corpo de uma função) que está de fato no diff — é aí que o comentário
    de review pode ser ancorado. Retorna None se nenhuma linha do intervalo
    fizer parte do diff (função não foi realmente alterada, só está no
    arquivo alterado por outro motivo).
    """
    for line in range(start_line, end_line + 1):
        if line in commentable:
            return line
    return None
