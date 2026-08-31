"""
Monta o corpo do comentário (em Markdown) que será postado no Pull Request,
a partir das sugestões geradas pela IA para cada função analisada.
"""

RISK_EMOJI = {
    "alto": "🔴",
    "medio": "🟡",
    "baixo": "🟢",
    "desconhecido": "⚪",
}


def format_pr_comment(results: list[dict]) -> str:
    """
    results: lista de dicts no formato
    {
        "filename": str,
        "function_name": str,
        "analysis": {
            "risk_level": str,
            "risk_reason": str,
            "suggested_tests": [{"title": str, "description": str}, ...]
        }
    }
    """
    if not results:
        return (
            "## 🤖 PR Reviewer AI\n\n"
            "Nenhuma função Python nova ou alterada foi identificada neste PR."
        )

    lines = ["## 🤖 PR Reviewer AI — Sugestões de Teste\n"]

    for item in results:
        analysis = item["analysis"]
        risk = analysis.get("risk_level", "desconhecido")
        emoji = RISK_EMOJI.get(risk, "⚪")

        lines.append(f"### {emoji} `{item['filename']}` → `{item['function_name']}()`")
        lines.append(f"**Risco estimado:** {risk.upper()}  ")
        lines.append(f"**Motivo:** {analysis.get('risk_reason', '—')}\n")

        tests = analysis.get("suggested_tests", [])
        if tests:
            lines.append("**Sugestões de teste:**")
            for t in tests:
                lines.append(f"- **{t.get('title', 'Sem título')}** — {t.get('description', '')}")
        else:
            lines.append("_Nenhuma sugestão específica gerada para esta função._")

        lines.append("\n---\n")

    lines.append(
        "_Comentário gerado automaticamente. As sugestões devem ser revisadas "
        "por um humano antes de serem aplicadas._"
    )

    return "\n".join(lines)
