"""
Geração de embeddings (vetores numéricos que representam o "significado" de
um texto) e cálculo de similaridade entre eles.

Usa a API de embeddings do Gemini, que tem cota SEPARADA da cota de geração
de texto — ou seja, indexar o repositório não consome as requisições
reservadas para as análises em si.

A alternativa seria rodar um modelo de embeddings localmente
(sentence-transformers), mas isso puxa PyTorch: ~2GB de instalação e
centenas de MB de RAM, inviável num container pequeno como o do Render.
"""
import math

from google import genai
from google.genai import types

from app.config import settings


class EmbeddingClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
    ):
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.embedding_model
        self.dimensions = dimensions or settings.embedding_dimensions
        self.client = genai.Client(api_key=self.api_key)

    def embed(self, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
        """
        Gera o embedding de vários textos numa chamada só (a API aceita lote,
        o que é bem mais barato em número de requisições do que uma por vez).

        `task_type` diz à API para que o vetor vai servir. Usar
        RETRIEVAL_DOCUMENT no que é indexado e RETRIEVAL_QUERY no que é
        buscado melhora a qualidade da recuperação — os dois lados são
        otimizados para se encontrarem, em vez de tratados como texto genérico.

        Retorna uma lista de vetores, na mesma ordem dos textos recebidos.
        """
        if not texts:
            return []

        response = self.client.models.embed_content(
            model=self.model,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type=task_type,
                # O modelo devolve 3072 dimensões por padrão. Truncar para um
                # tamanho menor (o modelo suporta isso nativamente) reduz em
                # 4x o armazenamento e o custo de comparar vetores, com perda
                # pequena de precisão — troca vantajosa nesta escala.
                output_dimensionality=self.dimensions,
            ),
        )
        # `embeddings` e `values` são opcionais no tipo do SDK — tratar como
        # vetor vazio evita quebrar o fluxo se a API devolver algo incompleto.
        return [list(e.values or []) for e in (response.embeddings or [])]

    def embed_one(self, text: str, task_type: str = "RETRIEVAL_QUERY") -> list[float]:
        """
        Embedding de um texto só. O padrão aqui é RETRIEVAL_QUERY porque o
        uso típico é embedar a função sendo analisada para BUSCAR trechos
        parecidos — o outro lado (o que está indexado) usa
        RETRIEVAL_DOCUMENT.
        """
        vectors = self.embed([text], task_type=task_type)
        return vectors[0] if vectors else []


def vector_norm(vector: list[float]) -> float:
    """Comprimento (norma euclidiana) do vetor — o denominador do cosseno."""
    return math.sqrt(sum(v * v for v in vector))


def cosine_similarity(
    vector_a: list[float],
    vector_b: list[float],
    norm_a: float | None = None,
    norm_b: float | None = None,
) -> float:
    """
    Similaridade do cosseno entre dois vetores: 1.0 = idênticos em direção,
    0.0 = sem relação, -1.0 = opostos. É a métrica padrão para comparar
    embeddings.

    As normas podem ser passadas prontas (pré-calculadas na indexação) para
    evitar recalcular a cada comparação.
    """
    if not vector_a or not vector_b or len(vector_a) != len(vector_b):
        return 0.0

    norm_a = vector_norm(vector_a) if norm_a is None else norm_a
    norm_b = vector_norm(vector_b) if norm_b is None else norm_b

    if norm_a == 0 or norm_b == 0:
        return 0.0

    dot = sum(a * b for a, b in zip(vector_a, vector_b))
    return dot / (norm_a * norm_b)
