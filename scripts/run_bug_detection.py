#!/usr/bin/env python3
"""
Mede a detecção de defeitos reais: garimpa correções no histórico de um
repositório e roda a análise sobre o código defeituoso (o commit-pai de cada
correção), alternando com e sem RAG.

Nada é escrito no repositório analisado — é só leitura do histórico público.

Uso:
    python scripts/run_bug_detection.py --owner scrapy --repo scrapy --max-casos 20

O resultado fica no banco e sai em CSV para julgamento:
    GET /experiment/bug-cases.csv

O julgamento é a etapa humana: para cada caso, comparar o que a ferramenta
apontou com o que a correção real consertou, e responder se é o mesmo
problema. Isso é o que transforma "achei útil" em taxa de detecção medida.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bug_dataset import coletar_casos_de_bug  # noqa: E402
from app.bug_detection import analisar_caso  # noqa: E402
from app.db import bug_case_ja_analisado, get_deteccao_stats, init_db  # noqa: E402
from app.github_client import GitHubClient  # noqa: E402
from app.llm_client import LLMClient  # noqa: E402
from app.rag import index_repository  # noqa: E402


def main():
    p = argparse.ArgumentParser(description="Mede detecção de defeitos reais.")
    p.add_argument("--owner", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--max-commits", type=int, default=500,
                   help="quantos commits varrer no histórico")
    p.add_argument("--max-casos", type=int, default=20,
                   help="quantos defeitos reconstituir")
    p.add_argument("--sem-rag", action="store_true",
                   help="roda tudo sem RAG (linha de base), em vez de alternar")
    args = p.parse_args()

    init_db()
    github = GitHubClient()
    llm = LLMClient()
    inicio = time.time()

    print(f"Garimpando correções em {args.owner}/{args.repo} "
          f"({args.max_commits} commits)...", flush=True)
    casos = coletar_casos_de_bug(
        github, args.owner, args.repo,
        max_commits=args.max_commits, max_casos=args.max_casos,
    )
    print(f"{len(casos)} defeitos reconstituíveis encontrados.\n", flush=True)

    if not casos:
        return

    if not args.sem_rag:
        print("Indexando repositório para o RAG...", flush=True)
        resumo = index_repository(github, args.owner, args.repo, casos[0]["sha_com_bug"])
        print(f"  {resumo.get('functions_indexed', 0)} funções indexadas\n", flush=True)

    analisados = 0
    for i, caso in enumerate(casos):
        if bug_case_ja_analisado(args.owner, args.repo, caso["sha_da_correcao"]):
            print(f"  [pulado, já analisado] {caso['mensagem'][:60]}", flush=True)
            continue

        use_rag = False if args.sem_rag else (i % 2 == 0)

        registros = analisar_caso(
            github, llm, args.owner, args.repo, caso, use_rag=use_rag
        )
        analisados += len(registros)

        marcador = "COM RAG" if use_rag else "SEM RAG"
        riscos = ", ".join(r.risk_level for r in registros) or "nenhuma função"
        print(f"  [{int(time.time()-inicio):>4}s] {marcador} | "
              f"{caso['mensagem'][:48]:<48} | {riscos}", flush=True)

        # "desconhecido" significa que a resposta do modelo não pôde ser
        # interpretada — normalmente JSON cortado por limite de tokens. Isso
        # já descartou uma detecção correta uma vez, então tem que ser
        # barulhento em vez de virar só mais uma linha no log.
        falhas = sum(1 for r in registros if r.risk_level == "desconhecido")
        if falhas:
            print(f"       [ATENÇÃO] {falhas} resposta(s) não interpretável(is) — "
                  f"análise perdida. Verifique LLM_MAX_OUTPUT_TOKENS.", flush=True)

    print(f"\n{analisados} análises registradas em {int(time.time()-inicio)}s")
    print("\nSituação atual:")
    for chave, valor in get_deteccao_stats(args.owner, args.repo).items():
        print(f"  {chave}: {valor}")
    print("\nPróximo passo: baixe /experiment/bug-cases.csv e preencha a coluna")
    print("'detectou' comparando a sugestão com a correção real.")


if __name__ == "__main__":
    main()
