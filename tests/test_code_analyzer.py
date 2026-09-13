from app.code_analyzer import CodeAnalyzer, build_dependency_context


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


DEPENDENCY_CODE = '''
def validar_email(email):
    return "@" in email


def cadastrar_usuario(nome, email):
    if not validar_email(email):
        raise ValueError("Email inválido")
    return {"nome": nome, "email": email}
'''


def test_called_names_identifies_function_calls():
    analyzer = CodeAnalyzer()
    functions = analyzer.analyze_source(DEPENDENCY_CODE)
    cadastrar = next(f for f in functions if f.name == "cadastrar_usuario")

    assert "validar_email" in cadastrar.called_names


def test_called_names_ignores_direct_recursion():
    code = "def fatorial(n):\n    return 1 if n == 0 else n * fatorial(n - 1)"
    analyzer = CodeAnalyzer()
    functions = analyzer.analyze_source(code)
    fatorial = functions[0]

    assert "fatorial" not in fatorial.called_names


def test_build_dependency_context_includes_called_function_source():
    analyzer = CodeAnalyzer()
    functions = analyzer.analyze_source(DEPENDENCY_CODE)
    cadastrar = next(f for f in functions if f.name == "cadastrar_usuario")

    context = build_dependency_context(cadastrar, functions)

    assert "validar_email" in context
    assert '"@" in email' in context


def test_build_dependency_context_empty_when_no_dependencies():
    analyzer = CodeAnalyzer()
    functions = analyzer.analyze_source(SAMPLE_CODE)
    simple_add = next(f for f in functions if f.name == "simple_add")

    context = build_dependency_context(simple_add, functions)

    assert context == ""


def test_build_dependency_context_uses_signature_only_for_external_function():
    """
    Dependência de OUTRO arquivo do PR: só assinatura + docstring, não o
    corpo inteiro (economiza tokens comparado a uma dependência local).
    """
    analyzer = CodeAnalyzer()
    functions = analyzer.analyze_source(DEPENDENCY_CODE)
    cadastrar = next(f for f in functions if f.name == "cadastrar_usuario")

    external_code = '''
def validar_email(email):
    """Confere se o email tem @."""
    return "@" in email
'''
    external_functions = {
        f.name: f for f in analyzer.analyze_source(external_code)
    }

    # Remove a versão local de validar_email para simular que ela só existe
    # em outro arquivo do PR.
    local_functions = [f for f in functions if f.name != "validar_email"]

    context = build_dependency_context(
        cadastrar, local_functions, external_functions=external_functions
    )

    assert "def validar_email(email):" in context
    assert "Confere se o email tem @." in context
    # Corpo da função não deve aparecer, só a assinatura/docstring
    assert '"@" in email' not in context


def test_build_dependency_context_respects_max_deps_across_sources():
    code = '''
def a():
    return 1


def b():
    return 2


def alvo():
    return a() + b() + c() + d()
'''
    analyzer = CodeAnalyzer()
    functions = analyzer.analyze_source(code)
    alvo = next(f for f in functions if f.name == "alvo")

    external_functions = {
        f.name: f for f in analyzer.analyze_source("def c():\n    return 3\n\n\ndef d():\n    return 4")
    }

    context = build_dependency_context(
        alvo, functions, external_functions=external_functions, max_deps=2
    )

    found = sum(name in context for name in ["a()", "b()", "c()", "d()"])
    assert found == 2