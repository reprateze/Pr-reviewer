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

    examples = db.get_top_rated_examples(limit=5)

    names = [e.function_name for e in examples]
    assert "boa" in names
    assert "ruim" not in names
