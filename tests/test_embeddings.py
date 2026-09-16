import math

from app.embeddings import cosine_similarity, vector_norm


def test_vector_norm():
    # Vetor (3, 4) tem norma 5 (triângulo 3-4-5)
    assert vector_norm([3.0, 4.0]) == 5.0
    assert vector_norm([0.0, 0.0]) == 0.0


def test_cosine_similarity_identical_vectors_is_one():
    v = [1.0, 2.0, 3.0]
    assert math.isclose(cosine_similarity(v, v), 1.0)


def test_cosine_similarity_orthogonal_vectors_is_zero():
    # Vetores perpendiculares não têm nada em comum
    assert math.isclose(cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)


def test_cosine_similarity_opposite_vectors_is_minus_one():
    assert math.isclose(cosine_similarity([1.0, 1.0], [-1.0, -1.0]), -1.0)


def test_cosine_similarity_uses_precomputed_norms():
    """
    As normas pré-calculadas (guardadas na indexação) devem dar o mesmo
    resultado que calcular na hora — é só otimização, não pode mudar o valor.
    """
    a = [1.0, 2.0, 2.0]
    b = [2.0, 0.0, 1.0]

    sem_norma = cosine_similarity(a, b)
    com_norma = cosine_similarity(a, b, norm_a=vector_norm(a), norm_b=vector_norm(b))

    assert math.isclose(sem_norma, com_norma)


def test_cosine_similarity_handles_degenerate_input():
    # Vetor vazio, tamanhos diferentes ou vetor nulo -> 0.0, sem explodir
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
