from app.diff_utils import commentable_lines, pick_comment_line

SAMPLE_PATCH = """@@ -10,6 +10,8 @@ def foo():
 def existing():
     pass
+
+def nova_funcao():
+    return 1
 def depois():
     pass
"""


def test_commentable_lines_includes_added_and_context_lines():
    lines = commentable_lines(SAMPLE_PATCH)

    # Linha 10 e 11 são contexto (existem no arquivo novo); 12, 13, 14, 15
    # são as linhas adicionadas (hunk começa em +10).
    assert 12 in lines
    assert 13 in lines


def test_commentable_lines_handles_empty_patch():
    assert commentable_lines(None) == set()
    assert commentable_lines("") == set()


def test_pick_comment_line_returns_first_line_in_range_that_is_commentable():
    commentable = {5, 10, 11, 12}
    assert pick_comment_line(start_line=8, end_line=15, commentable=commentable) == 10


def test_pick_comment_line_returns_none_when_range_not_in_diff():
    commentable = {1, 2, 3}
    assert pick_comment_line(start_line=8, end_line=15, commentable=commentable) is None


def test_marcador_de_ausencia_de_quebra_de_linha_nao_conta_como_linha():
    """
    O git insere "\\ No newline at end of file" no meio do diff. Como a marca
    não começa com "-", ela era tratada como conteúdo e deslocava em 1 toda a
    numeração seguinte — o bastante para ancorar o comentário na linha errada
    em arquivos sem quebra de linha final.
    """
    patch = (
        "@@ -1,3 +1,3 @@\n"
        " primeira\n"
        "-segunda antiga\n"
        "\\ No newline at end of file\n"
        "+segunda nova\n"
        " terceira\n"
    )

    assert commentable_lines(patch) == {1, 2, 3}
