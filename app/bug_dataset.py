"""
Monta um catálogo de defeitos REAIS a partir do histórico de um repositório.

A ideia: todo projeto maduro tem centenas de commits de correção. Cada um
desses commits é a prova documentada de que existia um bug ali — e o
commit-pai contém esse bug ainda presente no código.

Isso dá ao experimento algo que ele não tinha: verdade de referência. Em vez
de perguntar "você achou a sugestão útil?" (opinião, e enviesada quando quem
responde é o autor da ferramenta), passa a ser possível perguntar "a
ferramenta apontou o defeito que comprovadamente existia nessas linhas?" —
que é verificável e não depende de ninguém julgar.

Como o defeito é localizado: a correção remove/altera linhas, e os números
dessas linhas no arquivo ANTIGO apontam exatamente onde o bug morava. É isso
que o analisador precisa receber para olhar a função certa.
"""
import re

from app.context_gatherer import looks_like_test_file

# Commits de correção raramente seguem convenção (`fix:`) em projetos
# antigos — geralmente é prosa ("Fix X when Y"). Estes padrões cobrem os
# formatos mais comuns sem depender de disciplina de commit.
_PADROES_DE_CORRECAO = re.compile(
    r"\b(fix|fixes|fixed|fixing|bug|bugfix|correct|corrects|resolve|resolves|"
    r"broken|regression|crash|error)\b",
    re.IGNORECASE,
)

# Mensagens que casam com os padrões acima mas não são defeito de produção.
# A lista cresceu depois de garimpar um repositório real: a maior parte dos
# "Fix ..." de um projeto maduro é conserto de teste instável, de tipagem ou
# de CI — nada que a ferramenta devesse detectar numa revisão de código.
_FALSOS_POSITIVOS = re.compile(
    r"\b(typo|docs?|documentation|changelog|readme|comment|spelling|"
    r"lint|format|whitespace|mypy|pylint|flake8|ruff|typing|type hints?|"
    r"flaky|flakiness|ci|benchmark|coverage|deprecat\w*)\b",
    re.IGNORECASE,
)


def parece_correcao(mensagem: str) -> bool:
    """Se a mensagem de commit indica correção de defeito em código."""
    primeira_linha = mensagem.split("\n", 1)[0]

    if primeira_linha.lower().startswith(("merge ", "revert ", "bump ")):
        return False
    if _FALSOS_POSITIVOS.search(primeira_linha):
        return False

    return bool(_PADROES_DE_CORRECAO.search(primeira_linha))


def linhas_com_defeito(patch: str) -> set[int]:
    """
    Números de linha, no arquivo ANTES da correção, que a correção
    removeu ou alterou — ou seja, onde o defeito estava.

    Diferente de `diff_utils.commentable_lines`, que devolve as linhas do
    arquivo NOVO: aqui interessa o lado antigo, porque é nele que o bug
    ainda existe.
    """
    linhas: set[int] = set()
    linha_antiga = None

    for linha in (patch or "").splitlines():
        if linha.startswith("@@"):
            # Ex: "@@ -12,7 +12,9 @@" -> o lado antigo começa na linha 12
            match = re.search(r"-(\d+)", linha)
            linha_antiga = int(match.group(1)) if match else None
            continue

        if linha_antiga is None:
            continue

        if linha.startswith("+"):
            continue  # só existe no arquivo novo, não avança a numeração antiga

        if linha.startswith("-"):
            linhas.add(linha_antiga)  # linha que a correção tirou: aqui estava o bug

        linha_antiga += 1

    return linhas


def coletar_casos_de_bug(
    github,
    owner: str,
    repo: str,
    max_commits: int = 300,
    max_casos: int = 50,
    max_linhas_alteradas: int = 40,
) -> list[dict]:
    """
    Varre o histórico e devolve casos de defeito reconstituíveis.

    `max_linhas_alteradas` descarta correções grandes: uma que mexe em 300
    linhas é refatoração ou mudança de comportamento, não um defeito
    pontual — e não serviria como caso de teste claro ("a ferramenta achou
    ISTO?").
    """
    casos: list[dict] = []

    for commit in github.list_commits(owner, repo, limit=max_commits):
        if len(casos) >= max_casos:
            break

        mensagem = commit.get("commit", {}).get("message", "")
        if not parece_correcao(mensagem):
            continue

        pais = commit.get("parents", [])
        if len(pais) != 1:
            continue  # merge commit: o diff não representa uma correção isolada

        try:
            detalhe = github.get_commit(owner, repo, commit["sha"])
        except Exception:
            continue

        # Só arquivos de PRODUÇÃO: uma correção que mexe apenas em teste
        # (teste instável, ajuste de asserção) não é um defeito que a
        # ferramenta deveria apontar numa revisão — contá-la como caso
        # corromperia a taxa de detecção. Num garimpo real do scrapy, 6 de
        # cada 10 "Fix ..." eram exatamente isso.
        arquivos_py = [
            a for a in detalhe.get("files", [])
            if a.get("filename", "").endswith(".py")
            and a.get("patch")
            and not looks_like_test_file(a["filename"])
        ]
        if not arquivos_py:
            continue

        alteradas = sum(a.get("changes", 0) for a in arquivos_py)
        if alteradas > max_linhas_alteradas:
            continue

        arquivos_do_caso = []
        for arquivo in arquivos_py:
            linhas = linhas_com_defeito(arquivo["patch"])
            if linhas:
                arquivos_do_caso.append({
                    "filename": arquivo["filename"],
                    "linhas_com_defeito": sorted(linhas),
                    "patch_da_correcao": arquivo["patch"],
                })

        if not arquivos_do_caso:
            continue  # correção que só acrescenta linhas: não há "antes" com bug

        casos.append({
            "sha_da_correcao": commit["sha"],
            "sha_com_bug": pais[0]["sha"],  # o pai ainda tem o defeito
            "mensagem": mensagem.split("\n", 1)[0],
            "arquivos": arquivos_do_caso,
            "linhas_alteradas": alteradas,
        })

    return casos
