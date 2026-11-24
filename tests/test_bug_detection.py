from unittest.mock import MagicMock

from sqlmodel import SQLModel, create_engine

import app.db as db
from app.bug_detection import analisar_caso, funcoes_com_defeito
from app.code_analyzer import CodeAnalyzer
from app.models import BugDetectionCase


def _fresh_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    return engine


CODIGO_COM_BUG = '''
def trivial():
    return 1


def com_defeito(valores):
    total = 0
    for v in valores:
        if v > 0:
            total += v
    return total / len(valores)
'''


def test_funcoes_com_defeito_seleciona_pela_linha_do_bug():
    """
    Diferente do fluxo de PR, aqui as linhas vêm do lado ANTIGO do diff —
    onde o defeito ainda existe, antes da correção.
    """
    analyzer = CodeAnalyzer()
    # A linha 11 é o `return total / len(valores)` (divisão por zero)
    resultado = funcoes_com_defeito(analyzer, CODIGO_COM_BUG, [11])

    assert [f.name for f in resultado] == ["com_defeito"]


def test_funcoes_com_defeito_ordena_pela_complexidade():
    analyzer = CodeAnalyzer()
    # Linhas que pegam as duas funções
    resultado = funcoes_com_defeito(analyzer, CODIGO_COM_BUG, [3, 11])

    assert [f.name for f in resultado] == ["com_defeito", "trivial"]


def test_funcoes_com_defeito_vazio_quando_linha_fora_de_funcao():
    analyzer = CodeAnalyzer()
    assert funcoes_com_defeito(analyzer, CODIGO_COM_BUG, [1]) == []


CASO = {
    "sha_da_correcao": "sha-fix",
    "sha_com_bug": "sha-pai",
    "mensagem": "Fix division by zero on empty input",
    "arquivos": [{"filename": "app/core.py", "linhas_com_defeito": [11]}],
}


def test_analisar_caso_usa_o_commit_pai_e_registra_a_correcao_real(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    github = MagicMock()
    github.get_file_content.return_value = CODIGO_COM_BUG

    llm = MagicMock()
    llm.model = "modelo-teste"
    # A gravação usa o modelo que de fato respondeu — com a cadeia de
    # reserva, o principal pode não ser quem atendeu.
    llm.last_model_used = "modelo-teste"
    llm.prompt_version = "v2"
    llm.suggest_tests_for_function.return_value = {
        "risk_level": "alto",
        "risk_reason": "divisao sem checar lista vazia",
        "suggested_tests": [{"title": "lista vazia", "description": "..."}],
        "suggested_improvements": [],
    }

    registros = analisar_caso(github, llm, "o", "r", CASO, use_rag=False)

    # O conteúdo tem que vir do commit que AINDA tem o defeito
    github.get_file_content.assert_called_once_with("o", "r", "app/core.py", "sha-pai")

    assert len(registros) == 1
    r = registros[0]
    assert r.function_name == "com_defeito"
    assert r.risk_level == "alto"
    # A correção real fica guardada junto: é contra ela que o julgamento compara
    assert r.mensagem_da_correcao == "Fix division by zero on empty input"
    assert r.sha_da_correcao == "sha-fix"
    assert r.detectou is None  # ainda não julgado
    # A versão do prompt fica registrada: sem ela não dá pra interpretar o
    # dado depois, nem comparar versões entre si.
    assert r.prompt_version == "v2"

    salvos = db.get_bug_cases("o", "r")
    assert len(salvos) == 1


def test_analisar_caso_nao_quebra_quando_arquivo_nao_existe(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    github = MagicMock()
    github.get_file_content.side_effect = Exception("404")
    llm = MagicMock()

    assert analisar_caso(github, llm, "o", "r", CASO) == []
    llm.suggest_tests_for_function.assert_not_called()


def test_deteccao_stats_conta_so_os_casos_ja_julgados(tmp_path, monkeypatch):
    """
    Caso ainda não julgado (`detectou` vazio) não pode entrar na taxa — isso
    inflaria ou deprimiria o número conforme o andamento do julgamento.
    """
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    def caso(rag, detectou):
        return BugDetectionCase(
            owner="o", repo="r", sha_da_correcao=f"s{detectou}{rag}",
            sha_com_bug="p", mensagem_da_correcao="Fix x",
            filename="a.py", function_name="f",
            used_rag=rag, risk_level="alto", risk_reason="-",
            detectou=detectou,
        )

    db.save_bug_case(caso(True, True))
    db.save_bug_case(caso(True, False))
    db.save_bug_case(caso(True, None))   # não julgado
    db.save_bug_case(caso(False, True))

    stats = db.get_deteccao_stats("o", "r")

    com = stats["por_condicao"]["com_rag"]
    assert com["analisados"] == 3
    assert com["julgados"] == 2      # o não julgado ficou de fora
    assert com["detectados"] == 1
    assert com["taxa_deteccao"] == 0.5

    assert stats["por_condicao"]["sem_rag"]["taxa_deteccao"] == 1.0


def test_bug_case_ja_analisado_permite_retomar(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    assert db.bug_case_ja_analisado("o", "r", "sha-fix") is False

    db.save_bug_case(BugDetectionCase(
        owner="o", repo="r", sha_da_correcao="sha-fix", sha_com_bug="p",
        mensagem_da_correcao="Fix x", filename="a.py", function_name="f",
        risk_level="alto", risk_reason="-",
    ))

    assert db.bug_case_ja_analisado("o", "r", "sha-fix") is True


# --- Condições experimentais -----------------------------------------------


def test_modo_pareado_submete_o_mesmo_caso_as_duas_condicoes():
    """
    É o que permite atribuir a diferença à recuperação semântica. Alternando
    entre casos, a diferença observada poderia vir de os defeitos serem
    diferentes, e não da condição aplicada.
    """
    from app.bug_detection import condicoes_do_caso

    assert condicoes_do_caso(0, pareado=True) == [False, True]
    assert condicoes_do_caso(7, pareado=True) == [False, True]


def test_modo_padrao_alterna_a_condicao_entre_casos():
    from app.bug_detection import condicoes_do_caso

    assert [condicoes_do_caso(i) for i in range(4)] == [
        [True], [False], [True], [False]
    ]


def test_linha_de_base_ignora_o_pareamento():
    """Pedir a linha de base é pedir só a condição sem recuperação."""
    from app.bug_detection import condicoes_do_caso

    assert condicoes_do_caso(0, sem_rag=True) == [False]
    assert condicoes_do_caso(0, sem_rag=True, pareado=True) == [False]


def test_analisar_caso_registra_o_numero_da_repeticao(tmp_path, monkeypatch):
    """
    Os modelos não são determinísticos: guardar cada execução separadamente é
    o que permite medir a variação, em vez de atribuí-la ao acaso.
    """
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    github = MagicMock()
    github.get_file_content.return_value = CODIGO_COM_BUG

    llm = MagicMock()
    llm.model = "modelo-teste"
    llm.last_model_used = "modelo-teste"
    llm.prompt_version = "v3"
    llm.last_usage = {}
    llm.suggest_tests_for_function.return_value = {
        "risk_level": "alto",
        "risk_reason": "divisao sem checar lista vazia",
        "suggested_tests": [],
        "suggested_improvements": [],
    }

    registros = analisar_caso(
        github, llm, "o", "r", CASO, use_rag=False, repeticao=2
    )

    assert registros
    assert all(r.repeticao == 2 for r in registros)
