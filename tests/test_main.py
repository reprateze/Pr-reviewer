from app.main import _build_extra_context, _parse_rating


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
