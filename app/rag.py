"""
Recuperação contextual por embeddings (RAG — Retrieval Augmented Generation).

Ideia: antes de pedir sugestões à IA para uma função alterada, buscar no
repositório outros trechos de código semanticamente parecidos e mandá-los
junto no prompt. Assim a IA consegue perceber padrões já estabelecidos no
projeto ("aqui a validação é feita com X", "já existe uma função que faz
isso") em vez de sugerir algo genérico e descolado da base de código.

Fluxo:
1. Indexação (uma vez por repositório, incremental depois): lista os
   arquivos .py, extrai as FUNÇÕES via AST, gera o embedding de cada uma e
   guarda no banco.
2. Recuperação (a cada função analisada): gera o embedding da função atual e
   devolve os trechos indexados mais similares.
"""
import hashlib

from app.code_analyzer import CodeAnalyzer
from app.config import settings
from app.db import (
    get_indexed_file_hashes,
    get_repo_chunks,
    replace_file_chunks,
)
from app.embeddings import EmbeddingClient, cosine_similarity, vector_norm
from app.models import CodeChunk


def _file_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def index_repository(
    github,
    owner: str,
    repo: str,
    ref: str,
    embedder: EmbeddingClient | None = None,
    analyzer: CodeAnalyzer | None = None,
) -> dict:
    """
    Indexa (ou reindexa incrementalmente) os arquivos .py do repositório.

    Só reprocessa arquivos cujo conteúdo mudou desde a última indexação —
    comparando o hash do arquivo com o que já está salvo. Num repositório
    estável, rodar isso de novo custa só a listagem da árvore.

    Retorna um resumo do que foi feito (útil pra log e pro endpoint manual).
    """
    embedder = embedder or EmbeddingClient()
    analyzer = analyzer or CodeAnalyzer()

    try:
        tree = github.get_repo_tree(owner, repo, ref)
    except Exception:
        return {"status": "erro", "reason": "não foi possível listar a árvore do repo"}

    python_files = [
        item["path"]
        for item in tree
        if item.get("type") == "blob" and item["path"].endswith(".py")
    ][: settings.rag_max_files_to_index]

    known_hashes = get_indexed_file_hashes(owner, repo)

    indexed_files = 0
    skipped_files = 0
    indexed_functions = 0

    for path in python_files:
        try:
            content = github.get_file_content(owner, repo, path, ref)
        except Exception:
            continue

        current_hash = _file_hash(content)
        if known_hashes.get(path) == current_hash:
            skipped_files += 1
            continue  # arquivo não mudou desde a última indexação

        functions = analyzer.analyze_source(content)
        if not functions:
            continue

        sources = [f.source for f in functions]
        try:
            vectors = embedder.embed(sources)
        except Exception:
            # Falha de embedding (cota, rede) não pode derrubar o fluxo —
            # esse arquivo simplesmente fica de fora do índice por enquanto.
            continue

        chunks = [
            CodeChunk(
                owner=owner,
                repo=repo,
                filename=path,
                function_name=func.name,
                content=func.source,
                embedding=vector,
                embedding_norm=vector_norm(vector),
                file_hash=current_hash,
            )
            for func, vector in zip(functions, vectors)
        ]

        replace_file_chunks(owner, repo, path, chunks)
        indexed_files += 1
        indexed_functions += len(chunks)

    return {
        "status": "ok",
        "files_indexed": indexed_files,
        "files_skipped_unchanged": skipped_files,
        "functions_indexed": indexed_functions,
    }


def retrieve_similar_chunks(
    owner: str,
    repo: str,
    query_source: str,
    exclude_function: str | None = None,
    exclude_filename: str | None = None,
    top_k: int | None = None,
    embedder: EmbeddingClient | None = None,
) -> list[tuple[CodeChunk, float]]:
    """
    Devolve os `top_k` trechos indexados mais parecidos com `query_source`,
    cada um com sua pontuação de similaridade.

    `exclude_function`/`exclude_filename` servem para não recuperar a própria
    função que está sendo analisada — ela já vai no prompt de qualquer jeito,
    seria desperdício de contexto.
    """
    top_k = top_k or settings.rag_top_k
    embedder = embedder or EmbeddingClient()

    chunks = get_repo_chunks(owner, repo)
    if not chunks:
        return []

    try:
        query_vector = embedder.embed_one(query_source)
    except Exception:
        return []

    if not query_vector:
        return []

    query_norm = vector_norm(query_vector)

    scored: list[tuple[CodeChunk, float]] = []
    for chunk in chunks:
        if (
            exclude_function
            and chunk.function_name == exclude_function
            and chunk.filename == exclude_filename
        ):
            continue

        score = cosine_similarity(
            query_vector, chunk.embedding, norm_a=query_norm, norm_b=chunk.embedding_norm
        )
        scored.append((chunk, score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]


def format_retrieved_context(
    results: list[tuple[CodeChunk, float]], max_chars: int | None = None
) -> str:
    """
    Formata os trechos recuperados como bloco de texto pro prompt da IA.
    Inclui de onde cada trecho veio, pra IA poder citar o arquivo/função ao
    sugerir reaproveitamento.

    Respeita um orçamento próprio de caracteres: em vez de cortar no meio de
    um trecho, para de incluir quando o próximo não couber inteiro.
    """
    if not results:
        return ""

    max_chars = max_chars if max_chars is not None else settings.rag_max_context_chars

    header = (
        "Trechos semelhantes já existentes neste repositório (recuperados por "
        "similaridade semântica). Use-os para perceber padrões já adotados no "
        "projeto e evitar sugerir algo que já existe ou que contraria a "
        "convenção local:"
    )

    blocks = [header]
    used = len(header)

    for chunk, _score in results:
        block = f"# {chunk.filename} → {chunk.function_name}()\n{chunk.content}"
        if used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block)

    if len(blocks) == 1:  # nada coube além do cabeçalho
        return ""

    return "\n\n".join(blocks)
