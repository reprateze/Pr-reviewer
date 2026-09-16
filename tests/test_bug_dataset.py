from unittest.mock import MagicMock

from app.bug_dataset import coletar_casos_de_bug, linhas_com_defeito, parece_correcao


def test_parece_correcao_reconhece_mensagens_de_fix():
    assert parece_correcao("Fix LocalCache evicting a different key") is True
    assert parece_correcao("Fixes crash when response has no body") is True
    assert parece_correcao("Correct handling of empty input") is True
    assert parece_correcao("Resolve regression in parser") is True


def test_parece_correcao_ignora_o_que_nao_e_defeito_de_codigo():
    """
    Essas casam com as palavras-chave mas não são correção de código —
    entrariam como caso de bug sem ter bug nenhum.
    """
    assert parece_correcao("Fix typo in README") is False
    assert parece_correcao("Fix docs formatting") is False
    assert parece_correcao("Merge pull request #123 from x/fix-thing") is False
    assert parece_correcao("Revert 'Fix broken parser'") is False
    assert parece_correcao("Bump version 2.1 -> 2.2") is False
    assert parece_correcao("Add support for async handlers") is False


def test_linhas_com_defeito_aponta_o_lado_ANTIGO_do_diff():
    """
    O bug mora no arquivo antes da correção. Então interessam os números de
    linha do lado antigo (as linhas que a correção removeu), não do novo.
    """
    patch = """@@ -10,7 +10,7 @@ def processar(x):
 def processar(x):
     if x is None:
-        return x / 0
+        return 0
     return x
"""
    # Hunk começa na linha 10 do arquivo antigo:
    # 10 = "def processar(x):", 11 = "if x is None:", 12 = a linha removida
    assert linhas_com_defeito(patch) == {12}


def test_linhas_com_defeito_vazio_quando_correcao_so_acrescenta():
    patch = """@@ -5,3 +5,5 @@ def f():
 def f():
+    if x is None:
+        return 0
     return x
"""
    assert linhas_com_defeito(patch) == set()


def _commit(sha, mensagem, pai="pai1"):
    return {
        "sha": sha,
        "commit": {"message": mensagem},
        "parents": [{"sha": pai}],
    }


PATCH_COM_REMOCAO = """@@ -10,5 +10,5 @@ def f():
 def f():
-    return x / 0
+    return 0
"""


def test_coletar_casos_monta_o_caso_com_o_commit_pai():
    github = MagicMock()
    github.list_commits.return_value = [_commit("sha-fix", "Fix division by zero")]
    github.get_commit.return_value = {
        "files": [
            {"filename": "app/core.py", "patch": PATCH_COM_REMOCAO, "changes": 2},
        ]
    }

    casos = coletar_casos_de_bug(github, "o", "r")

    assert len(casos) == 1
    caso = casos[0]
    assert caso["sha_da_correcao"] == "sha-fix"
    # O commit-pai é o que ainda contém o defeito — é ele que será analisado
    assert caso["sha_com_bug"] == "pai1"
    assert caso["arquivos"][0]["filename"] == "app/core.py"
    assert caso["arquivos"][0]["linhas_com_defeito"] == [11]


def test_coletar_casos_descarta_correcao_grande_demais():
    """
    Correção que mexe em 300 linhas é refatoração, não defeito pontual —
    não serve como caso de teste claro ("a ferramenta achou ISTO?").
    """
    github = MagicMock()
    github.list_commits.return_value = [_commit("sha", "Fix everything")]
    github.get_commit.return_value = {
        "files": [{"filename": "a.py", "patch": PATCH_COM_REMOCAO, "changes": 300}]
    }

    assert coletar_casos_de_bug(github, "o", "r", max_linhas_alteradas=40) == []


def test_coletar_casos_ignora_merge_commit():
    github = MagicMock()
    commit = _commit("sha", "Fix something")
    commit["parents"] = [{"sha": "p1"}, {"sha": "p2"}]  # merge
    github.list_commits.return_value = [commit]

    assert coletar_casos_de_bug(github, "o", "r") == []
    github.get_commit.assert_not_called()


def test_parece_correcao_ignora_conserto_de_teste_e_ferramental():
    """
    Num repositório real, a maioria dos "Fix ..." é teste instável, tipagem
    ou CI — nada que a ferramenta devesse detectar numa revisão de código.
    """
    assert parece_correcao("Fix mypy, pylint and codspeed CI failures") is False
    assert parece_correcao("Fix test_closespider flakiness") is False
    assert parece_correcao("Fix a typing issue") is False
    assert parece_correcao("Fix coverage report") is False


def test_coletar_casos_ignora_correcao_so_em_arquivo_de_teste():
    """
    Conserto de teste não é defeito de produção. Entrar no dataset
    corromperia a taxa de detecção — a ferramenta seria penalizada por não
    apontar algo que não é trabalho dela.
    """
    github = MagicMock()
    github.list_commits.return_value = [_commit("sha", "Fix wrong assertion")]
    github.get_commit.return_value = {
        "files": [
            {
                "filename": "tests/test_core.py",
                "patch": PATCH_COM_REMOCAO,
                "changes": 2,
            }
        ]
    }

    assert coletar_casos_de_bug(github, "o", "r") == []


def test_coletar_casos_ignora_commit_sem_python():
    github = MagicMock()
    github.list_commits.return_value = [_commit("sha", "Fix docs build")]
    github.get_commit.return_value = {
        "files": [{"filename": "docs/index.rst", "patch": PATCH_COM_REMOCAO, "changes": 2}]
    }

    assert coletar_casos_de_bug(github, "o", "r") == []
