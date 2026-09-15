"""
Monta o corpo do comentário (em Markdown) que será postado no Pull Request,
a partir das sugestões geradas pela IA para cada função analisada.
"""

FEEDBACK_INSTRUCTIONS = (
    "\n\n_Essa sugestão ajudou? Responda este comentário com `/rate bom` ou "
    "`/rate ruim` (pode incluir o motivo depois) — isso ajuda a calibrar as "
    "próximas análises._"
)


def _format_tests_section(analysis: dict) -> list[str]:
    tests = analysis.get("suggested_tests", [])
    if not tests:
        return ["_Nenhuma sugestão específica gerada para esta função._"]

    lines = ["**Sugestões de teste:**"]
    for t in tests:
        lines.append(f"- **{t.get('title', 'Sem título')}** — {t.get('description', '')}")
    return lines


def _format_improvements_section(analysis: dict) -> list[str]:
    """
    Problemas concretos que a IA identificou no código (não só cenário sem
    teste) e como corrigi-los. Fica de fora do comentário quando a IA não
    aponta nada — não força uma seção vazia.
    """
    improvements = analysis.get("suggested_improvements", [])
    if not improvements:
        return []

    lines = ["", "**Melhorias sugeridas:**"]
    for imp in improvements:
        lines.append(
            f"- **{imp.get('issue', 'Ponto de atenção')}** — {imp.get('suggestion', '')}"
        )
    return lines


def format_single_suggestion_comment(
    filename: str, function_name: str, analysis: dict
) -> str:
    """
    Corpo de UM comentário de review, ancorado na linha da função alterada.
    Usado no fluxo principal (um comentário por função, não um bloco só) —
    ver format_pr_comment() para o formato antigo, usado como fallback
    quando não é possível ancorar o comentário numa linha do diff.
    """
    risk = analysis.get("risk_level", "desconhecido")

    lines = [
        f"### PR Reviewer AI — `{function_name}()`",
        f"**Risco estimado:** {risk.upper()}  ",
        f"**Motivo:** {analysis.get('risk_reason', '—')}\n",
    ]
    lines.extend(_format_tests_section(analysis))
    lines.extend(_format_improvements_section(analysis))

    return "\n".join(lines) + FEEDBACK_INSTRUCTIONS


def _format_result_block(item: dict) -> list[str]:
    """Monta as linhas de markdown pra UM item {filename, function_name, analysis}."""
    analysis = item["analysis"]
    risk = analysis.get("risk_level", "desconhecido")

    lines = [
        f"### `{item['filename']}` → `{item['function_name']}()`",
        f"**Risco estimado:** {risk.upper()}  ",
        f"**Motivo:** {analysis.get('risk_reason', '—')}\n",
    ]
    lines.extend(_format_tests_section(analysis))
    lines.extend(_format_improvements_section(analysis))

    return lines


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
            "## PR Reviewer AI\n\n"
            "Nenhuma função Python nova ou alterada foi identificada neste PR."
        )

    lines = ["## PR Reviewer AI — Sugestões de Teste\n"]

    for item in results:
        lines.extend(_format_result_block(item))
        lines.append("\n---\n")

    lines.append(
        "_Comentário gerado automaticamente. As sugestões devem ser revisadas "
        "por um humano antes de serem aplicadas._"
    )

    return "\n".join(lines)


def format_summary_comment(all_results: list[dict]) -> str:
    """
    Comentário único de resumo, postado ao final de toda análise de um PR
    (quando pelo menos uma função foi analisada). Traz uma visão geral —
    quantas funções, distribuição de risco, quantas viraram comentário
    inline — e, para as que não puderam ser ancoradas numa linha do diff,
    o conteúdo completo da sugestão (fallback).

    `all_results`: TODAS as funções analisadas nesta rodada, cada item com
    um campo extra `anchored: bool` indicando se já tem comentário inline
    próprio (não precisa aparecer detalhado aqui de novo).
    """
    risk_counts = {"alto": 0, "medio": 0, "baixo": 0, "desconhecido": 0}
    for item in all_results:
        risk = item["analysis"].get("risk_level", "desconhecido")
        risk_counts[risk] = risk_counts.get(risk, 0) + 1

    fallback_results = [item for item in all_results if not item.get("anchored")]
    anchored_count = len(all_results) - len(fallback_results)

    lines = [
        "## PR Reviewer AI — Resumo\n",
        f"**{len(all_results)} função(ões) analisada(s)** neste Pull Request.\n",
        "| Risco | Quantidade |",
        "|---|---|",
        f"| Alto | {risk_counts['alto']} |",
        f"| Médio | {risk_counts['medio']} |",
        f"| Baixo | {risk_counts['baixo']} |",
    ]
    if risk_counts["desconhecido"]:
        lines.append(f"| Desconhecido | {risk_counts['desconhecido']} |")

    lines.append("")
    lines.append(
        f"{anchored_count} comentário(s) postado(s) inline na aba \"Files changed\"."
    )

    if fallback_results:
        lines.append(
            f"\n{len(fallback_results)} sugestão(ões) não puderam ser ancoradas numa "
            "linha do diff — detalhes abaixo:\n"
        )
        lines.append("---\n")
        for item in fallback_results:
            lines.extend(_format_result_block(item))
            lines.append("\n---\n")

    lines.append(
        "\n_Comentário gerado automaticamente. As sugestões devem ser revisadas "
        "por um humano antes de serem aplicadas._"
    )

    return "\n".join(lines)
