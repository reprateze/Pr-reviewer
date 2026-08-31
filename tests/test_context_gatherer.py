from unittest.mock import MagicMock

from app.context_gatherer import ContextGatherer, _looks_like_test_file


def test_looks_like_test_file_recognizes_common_patterns():
    assert _looks_like_test_file("tests/test_calculadora.py") is True
    assert _looks_like_test_file("calculadora_test.py") is True
    assert _looks_like_test_file("calculadora.py") is False
    assert _looks_like_test_file("app/main.py") is False
    assert _looks_like_test_file("README.md") is False


def test_find_related_tests_returns_matching_files():
    fake_github = MagicMock()
    fake_github.get_repo_tree.return_value = [
        {"path": "calculadora.py", "type": "blob"},
        {"path": "tests/test_calculadora.py", "type": "blob"},
        {"path": "README.md", "type": "blob"},
    ]
    fake_github.get_file_content.return_value = (
        "def test_somar():\n    assert somar(2, 3) == 5"
    )

    gatherer = ContextGatherer(github_client=fake_github)
    related = gatherer.find_related_tests("owner", "repo", "main", "somar")

    assert related == {"somar": ["tests/test_calculadora.py"]}


def test_find_related_tests_returns_empty_when_no_match():
    fake_github = MagicMock()
    fake_github.get_repo_tree.return_value = [
        {"path": "tests/test_calculadora.py", "type": "blob"},
    ]
    fake_github.get_file_content.return_value = "def test_somar():\n    pass"

    gatherer = ContextGatherer(github_client=fake_github)
    related = gatherer.find_related_tests("owner", "repo", "main", "funcao_inexistente")

    assert related == {"funcao_inexistente": []}


def test_find_related_tests_checks_dependency_names_too():
    """
    Um teste que menciona só a função auxiliar (dependência) deve ser
    encontrado mesmo quando a busca principal é por outra função.
    """
    fake_github = MagicMock()
    fake_github.get_repo_tree.return_value = [
        {"path": "test_calculadora.py", "type": "blob"},
    ]
    fake_github.get_file_content.return_value = (
        "def test_validar_numero():\n    assert validar_numero(5) is True"
    )

    gatherer = ContextGatherer(github_client=fake_github)
    related = gatherer.find_related_tests(
        "owner", "repo", "main", "calcular_porcentagem",
        also_check_names=["validar_numero"],
    )

    assert related["calcular_porcentagem"] == []
    assert related["validar_numero"] == ["test_calculadora.py"]


def test_find_related_tests_deduplicates_repeated_paths():
    """
    Se a árvore do repositório retornar o mesmo caminho mais de uma vez
    (pode acontecer em certos históricos de commits/merges), o resultado
    não deve listar o arquivo duplicado.
    """
    fake_github = MagicMock()
    fake_github.get_repo_tree.return_value = [
        {"path": "test_calculadora.py", "type": "blob"},
        {"path": "test_calculadora.py", "type": "blob"},  # duplicata proposital
    ]
    fake_github.get_file_content.return_value = "def test_somar():\n    assert somar(2, 3) == 5"

    gatherer = ContextGatherer(github_client=fake_github)
    related = gatherer.find_related_tests("owner", "repo", "main", "somar")

    assert related["somar"] == ["test_calculadora.py"]


def test_find_related_tests_reuses_provided_tree():
    """Quando a árvore já é fornecida, get_repo_tree não deve ser chamado de novo."""
    fake_github = MagicMock()
    fake_github.get_file_content.return_value = "def test_somar():\n    pass"

    gatherer = ContextGatherer(github_client=fake_github)
    tree = [{"path": "tests/test_calculadora.py", "type": "blob"}]

    gatherer.find_related_tests("owner", "repo", "main", "somar", tree=tree)

    fake_github.get_repo_tree.assert_not_called()


def test_find_related_tests_handles_github_error_gracefully():
    fake_github = MagicMock()
    fake_github.get_repo_tree.side_effect = Exception("erro de rede simulado")

    gatherer = ContextGatherer(github_client=fake_github)
    related = gatherer.find_related_tests("owner", "repo", "main", "somar")

    assert related == {}


def test_build_context_summary_with_related_tests():
    gatherer = ContextGatherer(github_client=MagicMock())
    summary = gatherer.build_context_summary({"somar": ["tests/test_calculadora.py"]})

    assert "tests/test_calculadora.py" in summary
    assert "evite duplicar" in summary.lower()


def test_build_context_summary_without_related_tests():
    gatherer = ContextGatherer(github_client=MagicMock())
    summary = gatherer.build_context_summary({"somar": []})

    assert "nenhum teste" in summary.lower()