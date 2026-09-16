"""
Renderiza o painel HTML em /dashboard: visão geral do /stats + últimas
sugestões geradas. HTML puro + CSS embutido (sem JS, sem dependência
externa) — simples, rápido, funciona até offline depois de carregado.
"""
import html

from app.models import Suggestion

_RISK_ORDER = ["alto", "medio", "baixo", "desconhecido"]
_RISK_LABELS = {"alto": "Alto", "medio": "Médio", "baixo": "Baixo", "desconhecido": "Desconhecido"}
_RISK_COLORS = {"alto": "#ef4444", "medio": "#f59e0b", "baixo": "#22c55e", "desconhecido": "#9ca3af"}

_RATING_ORDER = ["positivo", "negativo", "sem_feedback"]
_RATING_LABELS = {"positivo": "Positivo", "negativo": "Negativo", "sem_feedback": "Sem feedback"}
_RATING_COLORS = {"positivo": "#22c55e", "negativo": "#ef4444", "sem_feedback": "#9ca3af"}


def _esc(value) -> str:
    """Escapa qualquer valor pra texto seguro em HTML (dado vindo do GitHub é externo)."""
    return html.escape(str(value)) if value is not None else "—"


def _bar_rows(counts: dict, order: list[str], labels: dict, colors: dict, total: int) -> str:
    rows = []
    for key in order:
        count = counts.get(key, 0)
        pct = round((count / total) * 100, 1) if total else 0
        rows.append(f"""
        <div class="bar-row">
          <span class="bar-label">{_esc(labels[key])}</span>
          <div class="bar-track">
            <div class="bar-fill" style="width:{pct}%;background:{colors[key]}"></div>
          </div>
          <span class="bar-count">{count}</span>
        </div>""")
    return "".join(rows)


def _risk_badge(risk: str) -> str:
    color = _RISK_COLORS.get(risk, "#9ca3af")
    return f'<span class="badge" style="background:{color}22;color:{color}">{_esc(_RISK_LABELS.get(risk, risk))}</span>'


def _rating_badge(rating: str | None) -> str:
    key = rating or "sem_feedback"
    color = _RATING_COLORS.get(key, "#9ca3af")
    return f'<span class="badge" style="background:{color}22;color:{color}">{_esc(_RATING_LABELS.get(key, key))}</span>'


def _format_rate(data: dict) -> str:
    """Taxa de aprovação com o denominador junto: '80% (4/5)' ou '—' se não há avaliação."""
    taxa = data.get("taxa_aprovacao")
    avaliadas = data.get("avaliadas", 0)
    if taxa is None or not avaliadas:
        return "—"
    return f"{taxa * 100:.0f}% ({data.get('positivas', 0)}/{avaliadas})"


def _recent_rows(recent: list[Suggestion]) -> str:
    if not recent:
        return '<tr><td colspan="6" class="empty">Nenhuma sugestão gerada ainda.</td></tr>'

    rows = []
    for s in recent:
        rows.append(f"""
        <tr>
          <td>{_esc(s.repo)} <span class="muted">#{_esc(s.pr_number)}</span></td>
          <td><code>{_esc(s.function_name)}()</code></td>
          <td class="muted">{_esc(s.filename)}</td>
          <td>{_risk_badge(s.risk_level)}</td>
          <td>{_rating_badge(s.rating)}</td>
          <td class="muted">{_esc(s.created_at)[:16]}</td>
        </tr>""")
    return "".join(rows)


def _repo_filter_links(repos: list[dict], escopo_atual: str) -> str:
    """
    Links para filtrar o painel por repositório. Importa porque os dados do
    experimento (projeto de terceiros, em dry-run) e os da demonstração
    (auto-avaliados) se misturariam num número só, sem serem comparáveis.
    """
    if not repos:
        return ""

    def link(href: str, rotulo: str, ativo: bool) -> str:
        estilo = "font-weight:700;text-decoration:underline" if ativo else ""
        return f'<a href="{href}" style="{estilo}">{_esc(rotulo)}</a>'

    partes = [link("/dashboard", "todos", escopo_atual == "todos")]
    for r in repos:
        alvo = f"{r['owner']}/{r['repo']}"
        partes.append(link(
            f"/dashboard?owner={r['owner']}&repo={r['repo']}",
            f"{alvo} ({r['sugestoes']})",
            escopo_atual == alvo,
        ))

    return '<p class="subtitle">Filtrar: ' + " · ".join(partes) + "</p>"


def render_dashboard(
    stats: dict, recent: list[Suggestion], repos: list[dict] | None = None
) -> str:
    total = stats.get("total_suggestions", 0)
    feedback_rate = stats.get("feedback_rate", 0) or 0
    positive_rate = stats.get("positive_rate_among_rated")
    by_risk = stats.get("by_risk_level", {})
    by_rating = stats.get("by_rating", {})
    by_model = stats.get("by_llm_model", {})

    model_rows = "".join(
        f'<div class="model-row"><span>{_esc(model or "desconhecido")}</span><strong>{count}</strong></div>'
        for model, count in sorted(by_model.items(), key=lambda kv: -kv[1])
    ) or '<div class="empty">Sem dados ainda.</div>'

    # Comparação entre as duas condições do experimento. Mostra a taxa de
    # aprovação e, entre parênteses, quantas sugestões sustentam esse número
    # — sem o denominador visível, uma taxa de 100% em cima de 2 avaliações
    # parece tão sólida quanto uma em cima de 200.
    rag_comparison = stats.get("rag_comparison", {})
    rag_labels = {"com_rag": "Com RAG", "sem_rag": "Sem RAG"}
    rag_rows = "".join(
        f'<div class="model-row"><span>{_esc(rag_labels.get(key, key))}</span>'
        f'<strong>{_format_rate(data)}</strong></div>'
        for key, data in rag_comparison.items()
    ) or '<div class="empty">Sem dados ainda.</div>'

    filtros = _repo_filter_links(repos or [], stats.get("escopo", "todos"))

    return f"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PR Reviewer AI — Dashboard</title>
<style>
  :root {{
    --bg: #f8fafc; --card: #ffffff; --text: #0f172a; --muted: #64748b;
    --border: #e2e8f0; --accent: #6366f1;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0b0f19; --card: #131826; --text: #e5e7eb; --muted: #94a3b8;
      --border: #232a3b; --accent: #818cf8;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px 16px 48px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  .wrap {{ max-width: 1000px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 4px; }}
  .subtitle {{ color: var(--muted); margin: 0 0 28px; font-size: 0.9rem; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }}
  .card .value {{ font-size: 1.8rem; font-weight: 700; }}
  .card .label {{ color: var(--muted); font-size: 0.8rem; margin-top: 4px; }}
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }}
  @media (max-width: 640px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
  .panel {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }}
  .panel h2 {{ font-size: 1rem; margin: 0 0 16px; }}
  .bar-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 10px; font-size: 0.85rem; }}
  .bar-label {{ width: 90px; flex-shrink: 0; color: var(--muted); }}
  .bar-track {{ flex: 1; background: var(--border); border-radius: 6px; height: 10px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 6px; }}
  .bar-count {{ width: 28px; text-align: right; flex-shrink: 0; font-variant-numeric: tabular-nums; }}
  .model-row {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 0.9rem; }}
  .model-row:last-child {{ border-bottom: none; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th {{ text-align: left; color: var(--muted); font-weight: 500; padding: 8px 10px; border-bottom: 1px solid var(--border); }}
  td {{ padding: 10px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  .muted {{ color: var(--muted); }}
  .empty {{ color: var(--muted); text-align: center; padding: 20px; }}
  code {{ background: var(--border); padding: 2px 6px; border-radius: 4px; font-size: 0.85em; }}
  .badge {{ padding: 2px 10px; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }}
  .table-wrap {{ overflow-x: auto; }}
  a {{ color: var(--accent); }}
</style>
</head>
<body>
  <div class="wrap">
    <h1>PR Reviewer AI — Dashboard</h1>
    <p class="subtitle">Resumo das sugestões geradas e do feedback dos devs. Dados em tempo real via <a href="/stats">/stats</a> · <a href="/stats/export.csv">exportar CSV</a></p>
    {filtros}

    <div class="cards">
      <div class="card"><div class="value">{total}</div><div class="label">Sugestões geradas</div></div>
      <div class="card"><div class="value">{feedback_rate * 100:.0f}%</div><div class="label">Taxa de feedback</div></div>
      <div class="card"><div class="value">{f"{positive_rate * 100:.0f}%" if positive_rate is not None else "—"}</div><div class="label">Aprovação (entre avaliadas)</div></div>
      <div class="card"><div class="value">{by_risk.get("alto", 0)}</div><div class="label">De risco alto</div></div>
    </div>

    <div class="grid2">
      <div class="panel">
        <h2>Distribuição por risco</h2>
        {_bar_rows(by_risk, _RISK_ORDER, _RISK_LABELS, _RISK_COLORS, total)}
      </div>
      <div class="panel">
        <h2>Distribuição por avaliação</h2>
        {_bar_rows(by_rating, _RATING_ORDER, _RATING_LABELS, _RATING_COLORS, total)}
      </div>
    </div>

    <div class="grid2">
      <div class="panel">
        <h2>Modelos LLM usados</h2>
        {model_rows}
      </div>
      <div class="panel">
        <h2>Com RAG vs. sem RAG</h2>
        {rag_rows}
      </div>
    </div>

    <div class="panel">
      <h2>Sugestões recentes</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Repo / PR</th><th>Função</th><th>Arquivo</th><th>Risco</th><th>Avaliação</th><th>Data</th></tr>
          </thead>
          <tbody>
            {_recent_rows(recent)}
          </tbody>
        </table>
      </div>
    </div>
  </div>
</body>
</html>"""
