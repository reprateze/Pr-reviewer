from sqlmodel import SQLModel, create_engine

import app.db as db
from app.models import Suggestion


def _fresh_engine(tmp_path):
    """
    Cria um engine SQLite isolado (arquivo temporário) e troca o engine global
    do módulo app.db por ele, pra não depender/poluir o data.db real do
    projeto durante os testes.
    """
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    return engine


def test_save_and_find_suggestion_by_comment_id(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    suggestion = db.save_suggestion(
        Suggestion(
            owner="reprateze",
            repo="Pr-reviewer",
            pr_number=1,
            commit_sha="abc123",
            filename="app/main.py",
            function_name="review_pull_request",
            risk_level="alto",
            risk_reason="Sem tratamento de erro",
            suggested_tests=[{"title": "Caso feliz", "description": "..."}],
            github_comment_id=999,
        )
    )

    assert suggestion.id is not None

    found = db.find_suggestion_by_comment_id(999)
    assert found is not None
    assert found.function_name == "review_pull_request"
    assert found.suggested_tests == [{"title": "Caso feliz", "description": "..."}]


def test_save_suggestion_accepts_comment_ids_beyond_32_bit_range(tmp_path, monkeypatch):
    """
    Regressão: github_comment_id precisa ser BigInteger, não o Integer (int4)
    padrão — ids de comentário do GitHub já passam de 4 bilhões, muito além
    do limite de ~2.1 bilhões do int4. Em Postgres isso quebrava o INSERT
    (silenciosamente, sem essa cobertura de teste); no SQLite (usado aqui)
    não quebraria mesmo com o tipo errado, mas o teste documenta o contrato.
    """
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    big_id = 4_000_087_333  # maior que 2**31 - 1 (limite do int4)
    suggestion = db.save_suggestion(
        Suggestion(
            owner="o", repo="r", pr_number=1, commit_sha="a",
            filename="a.py", function_name="foo", risk_level="alto",
            risk_reason="-", github_comment_id=big_id,
        )
    )

    assert suggestion.github_comment_id == big_id
    assert db.find_suggestion_by_comment_id(big_id) is not None


def test_find_suggestion_by_comment_id_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    assert db.find_suggestion_by_comment_id(12345) is None


def test_save_feedback_updates_rating_and_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    suggestion = db.save_suggestion(
        Suggestion(
            owner="reprateze",
            repo="Pr-reviewer",
            pr_number=1,
            commit_sha="abc123",
            filename="app/main.py",
            function_name="foo",
            risk_level="baixo",
            risk_reason="-",
            github_comment_id=1,
        )
    )

    updated = db.save_feedback(suggestion.id, rating="positivo", reason="ótima sugestão")

    assert updated.rating == "positivo"
    assert updated.feedback_reason == "ótima sugestão"
    assert updated.feedback_at is not None


def test_find_unchanged_suggestion_matches_same_code_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    db.save_suggestion(
        Suggestion(
            owner="o", repo="r", pr_number=5, commit_sha="a1",
            filename="app.py", function_name="foo", risk_level="baixo",
            risk_reason="-", code_hash="hash-v1",
        )
    )

    found = db.find_unchanged_suggestion("o", "r", 5, "app.py", "foo", "hash-v1")
    assert found is not None

    # Hash diferente (código de fato mudou) -> não deve casar
    assert db.find_unchanged_suggestion("o", "r", 5, "app.py", "foo", "hash-v2") is None
    # PR diferente -> não deve casar mesmo com o mesmo hash
    assert db.find_unchanged_suggestion("o", "r", 6, "app.py", "foo", "hash-v1") is None


def test_get_stats_aggregates_totals_risk_rating_and_model(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    s1 = db.save_suggestion(
        Suggestion(
            owner="o", repo="r", pr_number=1, commit_sha="a",
            filename="a.py", function_name="foo", risk_level="alto",
            risk_reason="-", llm_model="gemini-3.6-flash", github_comment_id=1,
        )
    )
    db.save_suggestion(
        Suggestion(
            owner="o", repo="r", pr_number=1, commit_sha="a",
            filename="a.py", function_name="bar", risk_level="baixo",
            risk_reason="-", llm_model="gemini-3.6-flash-lite", github_comment_id=2,
        )
    )
    db.save_feedback(s1.id, rating="positivo", reason=None)

    stats = db.get_stats()

    assert stats["total_suggestions"] == 2
    assert stats["by_risk_level"] == {"alto": 1, "baixo": 1}
    assert stats["by_rating"]["positivo"] == 1
    assert stats["by_rating"]["sem_feedback"] == 1
    assert stats["by_llm_model"] == {
        "gemini-3.6-flash": 1, "gemini-3.6-flash-lite": 1,
    }
    assert stats["feedback_rate"] == 0.5
    assert stats["positive_rate_among_rated"] == 1.0


def test_get_stats_filtra_por_repositorio(tmp_path, monkeypatch):
    """
    Sem filtro, os dados do experimento (projeto de terceiros) se somariam
    aos da demonstração — que têm natureza diferente e não são comparáveis.
    """
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    for repo, funcao in [("experimento", "a"), ("experimento", "b"), ("demo", "c")]:
        db.save_suggestion(
            Suggestion(
                owner="o", repo=repo, pr_number=1, commit_sha="x",
                filename="f.py", function_name=funcao,
                risk_level="alto", risk_reason="-",
            )
        )

    assert db.get_stats()["total_suggestions"] == 3
    assert db.get_stats(owner="o", repo="experimento")["total_suggestions"] == 2
    assert db.get_stats(owner="o", repo="demo")["total_suggestions"] == 1
    assert db.get_stats(owner="o", repo="demo")["escopo"] == "o/demo"


def test_get_analyzed_repos_lista_repositorios_com_contagem(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    for repo in ["experimento", "experimento", "demo"]:
        db.save_suggestion(
            Suggestion(
                owner="o", repo=repo, pr_number=1, commit_sha="x",
                filename="f.py", function_name="f",
                risk_level="alto", risk_reason="-",
            )
        )

    repos = db.get_analyzed_repos()

    assert {r["repo"]: r["sugestoes"] for r in repos} == {"experimento": 2, "demo": 1}
    # Ordenado por quantidade, maior primeiro
    assert repos[0]["repo"] == "experimento"


def test_get_all_suggestions_filtra_por_repositorio(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    for repo in ["experimento", "demo"]:
        db.save_suggestion(
            Suggestion(
                owner="o", repo=repo, pr_number=1, commit_sha="x",
                filename="f.py", function_name=repo,
                risk_level="alto", risk_reason="-",
            )
        )

    filtradas = db.get_all_suggestions(owner="o", repo="demo")

    assert [s.function_name for s in filtradas] == ["demo"]


def test_get_stats_handles_empty_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    stats = db.get_stats()

    assert stats["total_suggestions"] == 0
    assert stats["feedback_rate"] == 0
    assert stats["positive_rate_among_rated"] is None


def test_get_top_rated_examples_only_returns_positive_feedback(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    good = db.save_suggestion(
        Suggestion(
            owner="o", repo="r", pr_number=1, commit_sha="a",
            filename="a.py", function_name="boa", risk_level="alto",
            risk_reason="-", github_comment_id=1,
        )
    )
    bad = db.save_suggestion(
        Suggestion(
            owner="o", repo="r", pr_number=1, commit_sha="a",
            filename="a.py", function_name="ruim", risk_level="alto",
            risk_reason="-", github_comment_id=2,
        )
    )
    db.save_feedback(good.id, rating="positivo", reason=None)
    db.save_feedback(bad.id, rating="negativo", reason=None)

    examples = db.get_top_rated_examples("o", "r", limit=5)

    names = [e.function_name for e in examples]
    assert "boa" in names
    assert "ruim" not in names


def test_get_top_rated_examples_prioritizes_same_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    # Exemplo positivo de OUTRO repositório — não deve ter nada a ver com o
    # domínio/convenções do repo sendo analisado agora.
    outro_repo = db.save_suggestion(
        Suggestion(
            owner="outro-dono", repo="outro-repo", pr_number=1, commit_sha="a",
            filename="x.py", function_name="de_outro_projeto", risk_level="alto",
            risk_reason="-", github_comment_id=1,
        )
    )
    db.save_feedback(outro_repo.id, rating="positivo", reason=None)

    mesmo_repo = db.save_suggestion(
        Suggestion(
            owner="o", repo="r", pr_number=2, commit_sha="a",
            filename="y.py", function_name="do_mesmo_projeto", risk_level="baixo",
            risk_reason="-", github_comment_id=2,
        )
    )
    db.save_feedback(mesmo_repo.id, rating="positivo", reason=None)

    # Com limit=1, só cabe 1 exemplo — tem que ser o do MESMO repositório,
    # mesmo o outro tendo sido avaliado há mais tempo/rating igual.
    examples = db.get_top_rated_examples("o", "r", limit=1)

    assert [e.function_name for e in examples] == ["do_mesmo_projeto"]


def test_get_top_rated_examples_falls_back_to_other_repos_when_not_enough(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    outro_repo = db.save_suggestion(
        Suggestion(
            owner="outro-dono", repo="outro-repo", pr_number=1, commit_sha="a",
            filename="x.py", function_name="de_outro_projeto", risk_level="alto",
            risk_reason="-", github_comment_id=1,
        )
    )
    db.save_feedback(outro_repo.id, rating="positivo", reason=None)

    # Repositório "o/r" ainda não tem NENHUM exemplo positivo próprio —
    # precisa completar com o de outro repositório em vez de vir vazio.
    examples = db.get_top_rated_examples("o", "r", limit=2)

    assert [e.function_name for e in examples] == ["de_outro_projeto"]


def test_get_top_rated_examples_prioritizes_same_function_and_file(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    # Outra função do MESMO repositório, mas em lugar diferente do que está
    # sendo analisado agora.
    outro_lugar = db.save_suggestion(
        Suggestion(
            owner="o", repo="r", pr_number=1, commit_sha="a",
            filename="outro_arquivo.py", function_name="outra_funcao",
            risk_level="alto", risk_reason="-", github_comment_id=1,
        )
    )
    db.save_feedback(outro_lugar.id, rating="positivo", reason=None)

    # A MESMA função, no mesmo arquivo, já avaliada como boa antes — deve
    # vir primeiro, mesmo tendo sido avaliada depois (ordem cronológica não
    # deveria ser o critério de desempate aqui, prioridade de nível ganha).
    mesmo_lugar = db.save_suggestion(
        Suggestion(
            owner="o", repo="r", pr_number=2, commit_sha="a",
            filename="calculadora.py", function_name="calcular_porcentagem",
            risk_level="medio", risk_reason="-", github_comment_id=2,
        )
    )
    db.save_feedback(mesmo_lugar.id, rating="positivo", reason=None)

    examples = db.get_top_rated_examples(
        "o", "r", limit=1,
        filename="calculadora.py", function_name="calcular_porcentagem",
    )

    assert [e.function_name for e in examples] == ["calcular_porcentagem"]
