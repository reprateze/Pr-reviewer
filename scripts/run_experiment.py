#!/usr/bin/env python3
"""
Roda o experimento sobre PRs históricos de um repositório, em modo dry-run
(não posta nada no GitHub).

Por que um script e não só um endpoint HTTP: uma rodada de 100+ PRs leva
horas por causa do intervalo entre chamadas ao LLM. Uma requisição HTTP não
sobrevive a isso (timeout de proxy/servidor), enquanto um script local roda
o tempo que precisar, mostra o andamento e pode ser interrompido e retomado.

Uso:
    python scripts/run_experiment.py --owner pallets --repo flask --max-prs 40

    # só uma condição (ex: gerar a linha de base sem RAG primeiro)
    python scripts/run_experiment.py --owner pallets --repo flask --fixed-rag off

Requer um .env local com GITHUB_TOKEN, LLM_API_KEY e (opcionalmente)
DATABASE_URL apontando pro mesmo banco de produção.
"""
import argparse
import sys
import time
from pathlib import Path

# Permite rodar direto (`python scripts/run_experiment.py`) sem instalar o
# pacote: coloca a raiz do projeto no path de import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db  # noqa: E402
from app.experiment import run_experiment  # noqa: E402
from app.github_client import GitHubClient  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Roda o experimento sobre PRs históricos.")
    parser.add_argument("--owner", required=True, help="dono do repositório (ex: pallets)")
    parser.add_argument("--repo", required=True, help="nome do repositório (ex: flask)")
    parser.add_argument("--max-prs", type=int, default=20, help="quantos PRs processar")
    parser.add_argument(
        "--state", default="closed", choices=["open", "closed", "all"],
        help="estado dos PRs a buscar (padrão: closed, já mergeados/fechados)",
    )
    parser.add_argument(
        "--fixed-rag", choices=["on", "off"], default=None,
        help="força uma única condição em vez de alternar com/sem RAG",
    )
    args = parser.parse_args()

    fixed_rag = None
    if args.fixed_rag == "on":
        fixed_rag = True
    elif args.fixed_rag == "off":
        fixed_rag = False

    init_db()

    inicio = time.time()

    def progresso(pr_number, use_rag, resultado):
        condicao = "COM RAG" if use_rag else "SEM RAG"
        funcoes = resultado.get("functions_analyzed", 0)
        decorrido = int(time.time() - inicio)
        print(
            f"[{decorrido:>5}s] PR #{pr_number:<6} {condicao:<8} "
            f"{funcoes} função(ões) analisada(s)",
            flush=True,
        )

    print(f"Iniciando experimento em {args.owner}/{args.repo} "
          f"(até {args.max_prs} PRs, estado={args.state})\n", flush=True)

    resumo = run_experiment(
        GitHubClient(),
        args.owner,
        args.repo,
        max_prs=args.max_prs,
        state=args.state,
        fixed_rag=fixed_rag,
        on_progress=progresso,
    )

    print("\n=== Resumo ===")
    for chave, valor in resumo.items():
        print(f"{chave}: {valor}")
    print(f"tempo_total: {int(time.time() - inicio)}s")


if __name__ == "__main__":
    main()
