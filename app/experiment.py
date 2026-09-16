"""
Modo experimento: roda a análise sobre Pull Requests JÁ EXISTENTES de um
repositório, sem postar nada no GitHub (dry-run).

Para que serve: o fluxo normal reage a um PR por vez, em tempo real — bom
para demonstrar a ferramenta, ruim para gerar volume de dados. Aqui a ideia
é processar dezenas ou centenas de PRs históricos de um projeto real e
acumular sugestões suficientes para uma análise que se sustente.

Duas decisões que existem por causa do desenho do experimento:

1. **Alternância de condição (com/sem RAG).** Os PRs são processados
   alternando as duas condições, o que mantém os grupos balanceados e
   distribuídos ao longo de todo o período/repositório — em vez de "os 50
   primeiros com RAG, os 50 últimos sem", que confundiria a condição com
   qualquer característica que varie ao longo do tempo no projeto.

2. **Retomada (resume).** Uma rodada longa pode cair no meio (cota da API,
   rede, reinício). PRs que já têm sugestão salva são pulados, então é
   seguro rodar de novo de onde parou.
"""
from app.db import pr_already_analyzed


def run_experiment(
    github,
    owner: str,
    repo: str,
    max_prs: int = 20,
    state: str = "closed",
    alternate_rag: bool = True,
    fixed_rag: bool | None = None,
    analyze=None,
    on_progress=None,
) -> dict:
    """
    Processa até `max_prs` Pull Requests do repositório em modo dry-run.

    - `alternate_rag=True`: alterna com/sem RAG a cada PR processado.
    - `fixed_rag`: força uma única condição para todos (ignora a alternância).
    - `analyze`: injetável para teste; por padrão usa o pipeline real.
    - `on_progress`: callback opcional (útil para imprimir andamento na CLI).
    """
    # Importado aqui (e não no topo) para evitar import circular: main.py
    # importa este módulo, e a função de análise vive lá.
    if analyze is None:
        from app.main import ReviewRequest, analyze_pull_request

        def analyze(owner, repo, pr_number, head_ref, use_rag):
            return analyze_pull_request(
                ReviewRequest(
                    owner=owner, repo=repo, pr_number=pr_number, head_ref=head_ref
                ),
                dry_run=True,
                use_rag=use_rag,
            )

    pulls = github.list_pull_requests(owner, repo, state=state, limit=max_prs)

    processed = 0
    skipped_already_done = 0
    failed = 0
    functions_total = 0
    per_condition = {"com_rag": 0, "sem_rag": 0}
    # Contagem de SUGESTÕES por condição (não de PRs): é o que precisa ficar
    # equilibrado, porque a comparação final é feita sobre sugestões.
    suggestions_per_condition = {"com_rag": 0, "sem_rag": 0}

    for pull in pulls:
        pr_number = pull["number"]

        if pr_already_analyzed(owner, repo, pr_number):
            skipped_already_done += 1
            continue

        if fixed_rag is not None:
            use_rag = fixed_rag
        elif alternate_rag:
            # Manda o PR para o grupo que está atrás em número de sugestões.
            # Alternar cegamente (par/ímpar) desequilibra, porque cada PR
            # rende uma quantidade diferente de funções analisadas — numa
            # rodada real deu 6 contra 12 com 3 PRs para cada lado.
            use_rag = (
                suggestions_per_condition["com_rag"]
                <= suggestions_per_condition["sem_rag"]
            )
        else:
            use_rag = False

        head_ref = pull.get("head", {}).get("sha")
        if not head_ref:
            failed += 1
            continue

        try:
            result = analyze(owner, repo, pr_number, head_ref, use_rag)
        except Exception:
            # Um PR problemático (arquivo removido, cota momentânea) não pode
            # derrubar uma rodada de horas — conta como falha e segue.
            failed += 1
            continue

        processed += 1
        analisadas = result.get("functions_analyzed", 0)
        functions_total += analisadas

        condicao = "com_rag" if use_rag else "sem_rag"
        per_condition[condicao] += 1
        suggestions_per_condition[condicao] += analisadas

        if on_progress:
            on_progress(pr_number, use_rag, result)

    return {
        "status": "ok",
        "repositorio": f"{owner}/{repo}",
        "prs_encontrados": len(pulls),
        "prs_processados": processed,
        "prs_pulados_ja_analisados": skipped_already_done,
        "prs_com_falha": failed,
        "funcoes_analisadas": functions_total,
        "prs_por_condicao": per_condition,
        # O que importa pra comparação final: quantas SUGESTÕES cada grupo
        # tem. É esse número que precisa estar equilibrado, não o de PRs.
        "sugestoes_por_condicao": suggestions_per_condition,
    }
