from app.comment_formatter import (
    format_pr_comment,
    format_single_suggestion_comment,
    format_summary_comment,
)

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


def test_format_single_suggestion_comment_includes_improvements_when_present():
    analysis = {
        "risk_level": "medio",
        "risk_reason": "-",
        "suggested_tests": [],
        "suggested_improvements": [
            {"issue": "isinstance aceita bool", "suggestion": "Excluir bool explicitamente."},
        ],
    }
    body = format_single_suggestion_comment("app/main.py", "validar", analysis)

    assert "Melhorias sugeridas" in body
    assert "isinstance aceita bool" in body
    assert "Excluir bool explicitamente." in body


def test_format_single_suggestion_comment_omits_improvements_section_when_empty():
    body = format_single_suggestion_comment("app/main.py", "foo", ANALYSIS)

    assert "Melhorias sugeridas" not in body


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


def test_format_summary_comment_counts_risk_levels():
    results = [
        {"filename": "a.py", "function_name": "foo", "anchored": True,
         "analysis": {"risk_level": "alto", "risk_reason": "-", "suggested_tests": []}},
        {"filename": "b.py", "function_name": "bar", "anchored": True,
         "analysis": {"risk_level": "alto", "risk_reason": "-", "suggested_tests": []}},
        {"filename": "c.py", "function_name": "baz", "anchored": True,
         "analysis": {"risk_level": "baixo", "risk_reason": "-", "suggested_tests": []}},
    ]

    body = format_summary_comment(results)

    assert "3 função(ões) analisada(s)" in body
    assert "| Alto | 2 |" in body
    assert "| Baixo | 1 |" in body
    assert "3 comentário(s) postado(s) inline" in body


def test_format_summary_comment_details_only_non_anchored_items():
    results = [
        {"filename": "a.py", "function_name": "foo", "anchored": True, "analysis": ANALYSIS},
        {"filename": "b.py", "function_name": "bar", "anchored": False, "analysis": ANALYSIS},
    ]

    body = format_summary_comment(results)

    assert "1 comentário(s) postado(s) inline" in body
    assert "1 sugestão(ões) não puderam ser ancoradas" in body
    # Detalhe completo (com sugestões de teste) só deve aparecer pra quem não foi ancorado
    assert "bar()" in body
    assert "foo()" not in body
