from unittest.mock import MagicMock

from sqlmodel import SQLModel, create_engine

import app.db as db
from app.models import CodeChunk
from app.rag import (
    format_retrieved_context,
    index_repository,
    retrieve_similar_chunks,
)


def _fresh_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    return engine


ARQUIVO = '''
def somar(a, b):
    return a + b


def subtrair(a, b):
    return a - b
'''


def _fake_embedder(vetores_por_chamada):
    """
    Embedder falso: devolve vetores fixos, sem chamar API nenhuma.
    `vetores_por_chamada` é consumido a cada embed()/embed_one().
    """
    embedder = MagicMock()
    embedder.embed.side_effect = list(vetores_por_chamada)
    return embedder


def test_index_repository_indexa_uma_funcao_por_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    github = MagicMock()
    github.get_repo_tree.return_value = [
        {"path": "calc.py", "type": "blob"},
        {"path": "README.md", "type": "blob"},  # não é .py, deve ser ignorado
    ]
    github.get_file_content.return_value = ARQUIVO

    # 2 funções no arquivo -> 1 chamada de embed com 2 textos
    embedder = _fake_embedder([[[1.0, 0.0], [0.0, 1.0]]])

    resultado = index_repository(
        github, "o", "r", "main", embedder=embedder
    )

    assert resultado["status"] == "ok"
    assert resultado["files_indexed"] == 1
    assert resultado["functions_indexed"] == 2

    chunks = db.get_repo_chunks("o", "r")
    assert sorted(c.function_name for c in chunks) == ["somar", "subtrair"]
    # Cada chunk guarda a função INTEIRA, não um pedaço arbitrário
    assert "return a + b" in next(c.content for c in chunks if c.function_name == "somar")


def test_index_repository_pula_arquivo_inalterado(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    github = MagicMock()
    github.get_repo_tree.return_value = [{"path": "calc.py", "type": "blob"}]
    github.get_file_content.return_value = ARQUIVO

    embedder = _fake_embedder([[[1.0, 0.0], [0.0, 1.0]]])
    index_repository(github, "o", "r", "main", embedder=embedder)

    # Segunda rodada, mesmo conteúdo: não pode gerar embedding de novo
    embedder2 = _fake_embedder([])  # qualquer chamada a embed() estouraria
    resultado = index_repository(github, "o", "r", "main", embedder=embedder2)

    assert resultado["files_skipped_unchanged"] == 1
    assert resultado["functions_indexed"] == 0
    embedder2.embed.assert_not_called()


def test_retrieve_similar_chunks_ordena_por_similaridade(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    db.replace_file_chunks("o", "r", "a.py", [
        CodeChunk(
            owner="o", repo="r", filename="a.py", function_name="parecida",
            content="def parecida(): ...", embedding=[1.0, 0.0],
            embedding_norm=1.0, file_hash="h",
        ),
        CodeChunk(
            owner="o", repo="r", filename="a.py", function_name="distante",
            content="def distante(): ...", embedding=[0.0, 1.0],
            embedding_norm=1.0, file_hash="h",
        ),
    ])

    embedder = MagicMock()
    embedder.embed_one.return_value = [1.0, 0.0]  # igual à "parecida"

    resultados = retrieve_similar_chunks(
        "o", "r", "def qualquer(): ...", top_k=2, embedder=embedder
    )

    assert [c.function_name for c, _ in resultados] == ["parecida", "distante"]
    assert resultados[0][1] > resultados[1][1]


def test_retrieve_similar_chunks_exclui_a_propria_funcao(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    db.replace_file_chunks("o", "r", "a.py", [
        CodeChunk(
            owner="o", repo="r", filename="a.py", function_name="alvo",
            content="def alvo(): ...", embedding=[1.0, 0.0],
            embedding_norm=1.0, file_hash="h",
        ),
        CodeChunk(
            owner="o", repo="r", filename="a.py", function_name="outra",
            content="def outra(): ...", embedding=[0.9, 0.1],
            embedding_norm=1.0, file_hash="h",
        ),
    ])

    embedder = MagicMock()
    embedder.embed_one.return_value = [1.0, 0.0]

    resultados = retrieve_similar_chunks(
        "o", "r", "def alvo(): ...",
        exclude_function="alvo", exclude_filename="a.py",
        embedder=embedder,
    )

    assert [c.function_name for c, _ in resultados] == ["outra"]


def test_retrieve_similar_chunks_sem_indice_retorna_vazio(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    embedder = MagicMock()
    resultados = retrieve_similar_chunks("o", "r", "def x(): ...", embedder=embedder)

    assert resultados == []
    # Sem nada indexado, nem gasta chamada de embedding
    embedder.embed_one.assert_not_called()


def test_format_retrieved_context_respeita_orcamento():
    chunk_grande = CodeChunk(
        owner="o", repo="r", filename="a.py", function_name="grande",
        content="x" * 500, embedding=[], embedding_norm=0.0, file_hash="h",
    )
    resultados = [(chunk_grande, 0.9), (chunk_grande, 0.8), (chunk_grande, 0.7)]

    texto = format_retrieved_context(resultados, max_chars=800)

    # Cabeçalho (~230 chars) + 1 chunk de 500 cabe; o segundo não.
    assert texto.count("# a.py") == 1
    assert len(texto) <= 800


def test_format_retrieved_context_vazio_quando_nada_cabe():
    chunk = CodeChunk(
        owner="o", repo="r", filename="a.py", function_name="f",
        content="y" * 1000, embedding=[], embedding_norm=0.0, file_hash="h",
    )

    assert format_retrieved_context([(chunk, 0.9)], max_chars=100) == ""
    assert format_retrieved_context([]) == ""
