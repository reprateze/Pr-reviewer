"""
Mede se a ferramenta detecta defeitos REAIS, usando o histórico do projeto
como verdade de referência.

Como funciona: `bug_dataset.py` encontra commits de correção e, para cada
um, as linhas do commit-PAI onde o defeito ainda existe. Aqui essas linhas
são entregues ao mesmo pipeline de análise usado nos PRs — a ferramenta
olha o código defeituoso sem saber que há um defeito ali, e o resultado é
comparado depois com a correção real.

Por que isso vale mais que a avaliação atual: hoje a métrica é "o dev achou
a sugestão útil", respondida por quem construiu a ferramenta, com amostra
pequena. Aqui a pergunta é "ela apontou o defeito que comprovadamente
existia?" — verificável, e sem depender de ninguém gostar da resposta.
"""
from app.code_analyzer import (
    CodeAnalyzer,
    build_class_context,
    build_dependency_context,
)
from app.config import settings
from app.db import save_bug_case
from app.models import BugDetectionCase
from app.rag import format_retrieved_context, retrieve_similar_chunks


def funcoes_com_defeito(analyzer: CodeAnalyzer, conteudo: str, linhas: list[int]) -> list:
    """
    Funções que contêm alguma das linhas defeituosas.

    Note a diferença para o fluxo de PR: lá as linhas vêm do lado NOVO do
    diff (o que foi escrito); aqui vêm do lado ANTIGO (onde o defeito estava
    antes de ser corrigido).
    """
    alvo = set(linhas)
    funcoes = [
        f for f in analyzer.analyze_source(conteudo)
        if any(f.start_line <= linha <= f.end_line for linha in alvo)
    ]
    # Mesma lógica do fluxo normal: a mais complexa primeiro, porque só as
    # primeiras entram na cota.
    funcoes.sort(key=lambda f: (f.num_branches, f.num_lines), reverse=True)
    return funcoes


def analisar_caso(
    github,
    llm,
    owner: str,
    repo: str,
    caso: dict,
    use_rag: bool = False,
    analyzer: CodeAnalyzer | None = None,
    max_funcoes: int | None = None,
) -> list[BugDetectionCase]:
    """
    Roda a análise sobre UM caso de defeito e salva o resultado.

    Nada é postado no GitHub: é leitura do histórico de um projeto de
    terceiros, análise local e registro no banco.
    """
    analyzer = analyzer or CodeAnalyzer()
    max_funcoes = max_funcoes or settings.max_functions_per_pr

    registros: list[BugDetectionCase] = []

    for arquivo in caso["arquivos"]:
        filename = arquivo["filename"]

        try:
            # Conteúdo no commit-PAI: é aqui que o defeito ainda existe.
            conteudo = github.get_file_content(
                owner, repo, filename, caso["sha_com_bug"]
            )
        except Exception:
            continue

        funcoes = funcoes_com_defeito(
            analyzer, conteudo, arquivo["linhas_com_defeito"]
        )
        todas_do_arquivo = analyzer.analyze_source(conteudo)

        for func in funcoes[:max_funcoes]:
            if func.num_lines > settings.max_diff_lines:
                continue

            partes_de_contexto = []

            classe = build_class_context(func, todas_do_arquivo)
            if classe:
                partes_de_contexto.append(classe)

            dependencias = build_dependency_context(func, todas_do_arquivo)
            if dependencias:
                partes_de_contexto.append(
                    f"Funções auxiliares chamadas por esta função:\n{dependencias}"
                )

            if use_rag:
                try:
                    similares = retrieve_similar_chunks(
                        owner, repo, func.source,
                        exclude_function=func.name, exclude_filename=filename,
                    )
                    recuperado = format_retrieved_context(similares)
                    if recuperado:
                        partes_de_contexto.append(recuperado)
                except Exception:
                    pass

            orcamento = settings.max_extra_context_chars
            if use_rag:
                orcamento += settings.rag_max_context_chars

            contexto = "\n\n".join(partes_de_contexto)[:orcamento] or None

            try:
                analise = llm.suggest_tests_for_function(
                    func, filename, extra_context=contexto
                )
            except Exception as exc:
                analise = {
                    "risk_level": "desconhecido",
                    "risk_reason": f"Erro ao consultar IA: {exc}",
                    "suggested_tests": [],
                }

            registro = BugDetectionCase(
                owner=owner,
                repo=repo,
                sha_da_correcao=caso["sha_da_correcao"],
                sha_com_bug=caso["sha_com_bug"],
                mensagem_da_correcao=caso["mensagem"],
                filename=filename,
                function_name=func.name,
                used_rag=use_rag,
                llm_model=getattr(llm, "model", None),
                prompt_version=getattr(llm, "prompt_version", None),
                risk_level=analise.get("risk_level", "desconhecido"),
                risk_reason=analise.get("risk_reason", ""),
                suggested_tests=analise.get("suggested_tests", []),
                suggested_improvements=analise.get("suggested_improvements", []),
            )

            try:
                save_bug_case(registro)
            except Exception as exc:
                # Não derruba a rodada (seriam dezenas de casos perdidos),
                # mas também não some em silêncio: aqui uma falha significa
                # que uma chamada de LLM foi gasta e o resultado se perdeu.
                print(
                    f"[AVISO] falhou ao salvar {filename}::{func.name} — {exc}",
                    flush=True,
                )

            registros.append(registro)

    return registros
