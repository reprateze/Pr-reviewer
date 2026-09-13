from app.comment_formatter import format_pr_comment, format_single_suggestion_comment

ANALYSIS = {
    "risk_level": "alto",
    "risk_reason": "Sem tratamento de exceção",
    "suggested_tests": [
        {"title": "Caso feliz", "description": "Testar o fluxo normal."},
    ],
}


def test_format_single_suggestion_comment_includes_key_fields():
    body = format_single_suggestion_comment("app/main.py", "calcular_media", ANALYSIS)

    assert "calcular_media" in body
    assert "ALTO" in body
    assert "Sem tratamento de exceção" in body
    assert "Caso feliz" in body
    assert "/rate bom" in body and "/rate ruim" in body


def test_format_single_suggestion_comment_handles_no_tests():
    body = format_single_suggestion_comment(
        "app/main.py", "foo", {"risk_level": "baixo", "risk_reason": "-", "suggested_tests": []}
    )

    assert "Nenhuma sugestão específica" in body


def test_format_pr_comment_returns_placeholder_when_empty():
    body = format_pr_comment([])
    assert "Nenhuma função Python" in body


def test_format_pr_comment_includes_all_results():
    results = [
        {"filename": "app/a.py", "function_name": "foo", "analysis": ANALYSIS},
        {"filename": "app/b.py", "function_name": "bar", "analysis": ANALYSIS},
    ]
    body = format_pr_comment(results)

    assert "app/a.py" in body and "foo()" in body
    assert "app/b.py" in body and "bar()" in body
