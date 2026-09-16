from app.dashboard import _format_rate, render_dashboard
from app.models import Suggestion


def test_format_rate_mostra_denominador():
    """
    A taxa sozinha engana: 100% em cima de 2 avaliações parece tão forte
    quanto 100% em cima de 200. O denominador precisa aparecer.
    """
    assert _format_rate({"taxa_aprovacao": 0.857, "positivas": 6, "avaliadas": 7}) == "86% (6/7)"
    assert _format_rate({"taxa_aprovacao": None, "avaliadas": 0}) == "—"
    assert _format_rate({}) == "—"


def _suggestion(**overrides) -> Suggestion:
    defaults = dict(
        owner="o", repo="r", pr_number=1, commit_sha="a",
        filename="app.py", function_name="foo", risk_level="alto",
        risk_reason="-",
    )
    defaults.update(overrides)
    return Suggestion(**defaults)


def test_render_dashboard_shows_totals_and_rates():
    stats = {
        "total_suggestions": 4,
        "by_risk_level": {"alto": 2, "baixo": 2},
        "by_rating": {"positivo": 2, "sem_feedback": 2},
        "by_llm_model": {"gemini-3.6-flash": 4},
        "feedback_rate": 0.5,
        "positive_rate_among_rated": 1.0,
    }
    html = render_dashboard(stats, [])

    assert "PR Reviewer AI" in html
    assert ">4<" in html  # total de sugestões
    assert "50%" in html  # feedback_rate
    assert "100%" in html  # positive_rate_among_rated
    assert "gemini-3.6-flash" in html


def test_render_dashboard_handles_empty_stats():
    stats = {
        "total_suggestions": 0,
        "by_risk_level": {},
        "by_rating": {},
        "by_llm_model": {},
        "feedback_rate": 0,
        "positive_rate_among_rated": None,
    }
    html = render_dashboard(stats, [])

    assert "Nenhuma sugestão gerada ainda" in html
    assert "—" in html  # aprovação sem dado


def test_render_dashboard_escapes_untrusted_data():
    """
    filename/function_name vêm de PRs no GitHub — dado externo, não confiável.
    Precisa escapar pra não virar injeção de HTML no dashboard.
    """
    malicious = _suggestion(
        function_name="<script>alert(1)</script>",
        filename="<img src=x onerror=alert(1)>",
    )

    html = render_dashboard(
        {"total_suggestions": 1, "by_risk_level": {"alto": 1}, "by_rating": {},
         "by_llm_model": {}, "feedback_rate": 0, "positive_rate_among_rated": None},
        [malicious],
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html
