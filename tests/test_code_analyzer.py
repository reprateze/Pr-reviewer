from app.code_analyzer import CodeAnalyzer


SAMPLE_CODE = '''
def login(email, password):
    """Realiza login do usuário."""
    if not email or not password:
        raise ValueError("Campos obrigatórios")
    try:
        user = find_user(email)
    except Exception:
        return None
    if user.password != password:
        return None
    return user


def simple_add(a, b):
    return a + b
'''


def test_analyze_source_finds_functions():
    analyzer = CodeAnalyzer()
    functions = analyzer.analyze_source(SAMPLE_CODE)

    names = [f.name for f in functions]
    assert "login" in names
    assert "simple_add" in names


def test_login_function_has_try_except_and_branches():
    analyzer = CodeAnalyzer()
    functions = analyzer.analyze_source(SAMPLE_CODE)
    login_func = next(f for f in functions if f.name == "login")

    assert login_func.has_try_except is True
    assert login_func.num_branches >= 2  # dois ifs + um try


def test_simple_add_has_no_branches():
    analyzer = CodeAnalyzer()
    functions = analyzer.analyze_source(SAMPLE_CODE)
    add_func = next(f for f in functions if f.name == "simple_add")

    assert add_func.has_try_except is False
    assert add_func.num_branches == 0


def test_invalid_code_returns_empty_list():
    analyzer = CodeAnalyzer()
    functions = analyzer.analyze_source("def broken(:\n  pass")
    assert functions == []


def test_summarize_aggregates_metrics():
    analyzer = CodeAnalyzer()
    functions = analyzer.analyze_source(SAMPLE_CODE)
    summary = analyzer.summarize(functions)

    assert summary["total_functions"] == 2
    assert summary["functions_without_try_except"] == 1
