from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlmodel import create_engine

import app.db as db
from app.code_analyzer import CodeAnalyzer
from app.comment_formatter import SUMMARY_MARKER
from app.main import (
    _build_extra_context,
    _functions_touched_by_diff,
    _parse_rating,
    _post_or_update_summary_comment,
    app,
)


def test_build_extra_context_returns_none_when_no_parts():
    assert _build_extra_context([], max_chars=100) is None


def test_build_extra_context_joins_parts_within_budget():
    parts = ["Dependências: foo()", "Testes: nenhum encontrado"]
    result = _build_extra_context(parts, max_chars=1000)

    assert "Dependências: foo()" in result
    assert "Testes: nenhum encontrado" in result


def test_build_extra_context_truncates_when_over_budget():
    parts = ["x" * 50, "y" * 50]
    result = _build_extra_context(parts, max_chars=30)

    assert len(result) > 30  # inclui a mensagem de aviso do corte
    assert result.startswith("x" * 30)
    assert "truncado" in result


def test_parse_rating_recognizes_positive_command():
    assert _parse_rating("/rate bom") == ("positivo", None)


def test_parse_rating_recognizes_negative_command_with_reason():
    result = _parse_rating("/rate ruim - já temos teste disso")
    assert result == ("negativo", "já temos teste disso")


def test_parse_rating_is_case_insensitive():
    assert _parse_rating("/RATE Bom ótima sugestão") == ("positivo", "ótima sugestão")


def test_parse_rating_returns_none_when_no_command():
    assert _parse_rating("só um comentário qualquer, sem comando") is None


def test_parse_rating_returns_none_for_empty_body():
    assert _parse_rating("") is None
    assert _parse_rating(None) is None


def test_functions_touched_by_diff_ignores_functions_outside_the_diff():
    """
    Reproduz o caso real: um PR que só altera uma linha fora de qualquer
    função (ex: um print solto no nível do módulo) não deve fazer nenhuma
    função "por acaso presente no arquivo" ser escolhida para análise.
    """
    code = '''
def calcular_porcentagem(valor, percentual):
    return (valor * percentual) / 100


print("linha alterada fora de qualquer função")
'''
    functions = CodeAnalyzer().analyze_source(code)
    # Só a linha 5 (o print) está no diff — nenhuma função foi tocada.
    commentable = {5}

    assert _functions_touched_by_diff(functions, commentable) == []


def test_functions_touched_by_diff_returns_functions_that_overlap_the_diff():
    code = '''
def foo():
    return 1


def bar():
    return 2
'''
    functions = CodeAnalyzer().analyze_source(code)
    bar = next(f for f in functions if f.name == "bar")
    # Linha dentro do corpo de bar() está no diff.
    commentable = {bar.start_line}

    result = _functions_touched_by_diff(functions, commentable)

    assert [f.name for f in result] == ["bar"]


def test_health_endpoint_returns_ok(tmp_path, monkeypatch):
    # Isola o banco num arquivo temporário — sem isso, o lifespan (init_db)
    # criaria um data.db de verdade na raiz do projeto.
    monkeypatch.setattr(db, "engine", create_engine(f"sqlite:///{tmp_path}/test.db"))

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_stats_endpoint_returns_data_from_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", create_engine(f"sqlite:///{tmp_path}/test.db"))

    with TestClient(app) as client:
        response = client.get("/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["total_suggestions"] == 0
    assert body["positive_rate_among_rated"] is None


def test_dashboard_endpoint_returns_html(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", create_engine(f"sqlite:///{tmp_path}/test.db"))

    with TestClient(app) as client:
        response = client.get("/dashboard")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "PR Reviewer AI" in response.text


def test_export_csv_endpoint_returns_csv_with_seeded_data(tmp_path, monkeypatch):
    from app.models import Suggestion

    monkeypatch.setattr(db, "engine", create_engine(f"sqlite:///{tmp_path}/test.db"))

    with TestClient(app) as client:
        db.save_suggestion(
            Suggestion(
                owner="o", repo="r", pr_number=1, commit_sha="a",
                filename="a.py", function_name="foo", risk_level="alto",
                risk_reason="-", llm_model="gemini-3.6-flash",
            )
        )
        response = client.get("/stats/export.csv")

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    assert "function_name" in response.text  # cabeçalho
    assert "foo" in response.text and "gemini-3.6-flash" in response.text


def test_blind_export_nao_revela_a_condicao_do_experimento(tmp_path, monkeypatch):
    """
    O CSV de avaliação cega não pode conter a coluna used_rag — se o
    avaliador souber quais sugestões tiveram contexto do repositório, a
    comparação entre os grupos perde o valor.
    """
    from app.models import Suggestion

    monkeypatch.setattr(db, "engine", create_engine(f"sqlite:///{tmp_path}/test.db"))

    with TestClient(app) as client:
        db.save_suggestion(
            Suggestion(
                owner="o", repo="r", pr_number=1, commit_sha="a",
                filename="a.py", function_name="com_contexto", risk_level="alto",
                risk_reason="-", used_rag=True,
            )
        )
        db.save_suggestion(
            Suggestion(
                owner="o", repo="r", pr_number=2, commit_sha="a",
                filename="b.py", function_name="sem_contexto", risk_level="baixo",
                risk_reason="-", used_rag=False,
            )
        )
        response = client.get("/experiment/blind-export.csv")

    assert response.status_code == 200
    assert "used_rag" not in response.text
    assert "True" not in response.text and "False" not in response.text
    # Mas mantém o id, que é a chave pra recombinar com a condição depois
    assert "com_contexto" in response.text and "sem_contexto" in response.text
    assert "avaliacao_util_1_a_5" in response.text


def test_post_or_update_summary_comment_creates_when_none_exists():
    fake_github = MagicMock()
    fake_github.list_issue_comments.return_value = []

    _post_or_update_summary_comment(fake_github, "o", "r", 1, "corpo novo")

    fake_github.post_comment.assert_called_once_with("o", "r", 1, "corpo novo")
    fake_github.update_comment.assert_not_called()


def test_post_or_update_summary_comment_edits_existing_one():
    fake_github = MagicMock()
    fake_github.list_issue_comments.return_value = [
        {"id": 111, "body": "comentário qualquer de outra pessoa"},
        {"id": 222, "body": f"{SUMMARY_MARKER}\n## PR Reviewer AI — Resumo"},
    ]

    _post_or_update_summary_comment(fake_github, "o", "r", 1, "corpo atualizado")

    fake_github.update_comment.assert_called_once_with("o", "r", 222, "corpo atualizado")
    fake_github.post_comment.assert_not_called()


def test_post_or_update_summary_comment_falls_back_to_create_on_list_failure():
    fake_github = MagicMock()
    fake_github.list_issue_comments.side_effect = Exception("boom")

    _post_or_update_summary_comment(fake_github, "o", "r", 1, "corpo novo")

    fake_github.post_comment.assert_called_once_with("o", "r", 1, "corpo novo")
