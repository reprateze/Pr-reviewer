"""
Configuração da conexão com o banco e funções de acesso aos dados de
sugestões/feedback (ver app/models.py).

Usa SQLModel por cima do SQLAlchemy, o que permite rodar em SQLite local
(padrão, zero configuração) ou Postgres em produção — basta apontar
DATABASE_URL (ver app/config.py) para o Postgres do Render/Supabase.
"""
from datetime import datetime, timezone

from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.models import Suggestion


def _normalize_database_url(url: str) -> str:
    """
    Alguns provedores (Render, Heroku, Supabase) ainda entregam a connection
    string com o esquema antigo "postgres://", que o SQLAlchemy 2.x não
    aceita mais diretamente. Normaliza para usar o driver psycopg (v3).
    """
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DATABASE_URL = _normalize_database_url(settings.database_url)

# `check_same_thread=False` só é necessário (e só é aceito) pelo SQLite — o
# FastAPI pode acessar a mesma conexão a partir de threads diferentes.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args)


def init_db() -> None:
    """Cria as tabelas que ainda não existem. Chamado no startup da API."""
    SQLModel.metadata.create_all(engine)


def save_suggestion(suggestion: Suggestion) -> Suggestion:
    with Session(engine) as session:
        session.add(suggestion)
        session.commit()
        session.refresh(suggestion)
        return suggestion


def find_suggestion_by_comment_id(github_comment_id: int) -> Suggestion | None:
    with Session(engine) as session:
        statement = select(Suggestion).where(
            Suggestion.github_comment_id == github_comment_id
        )
        return session.exec(statement).first()


def save_feedback(suggestion_id: int, rating: str, reason: str | None) -> Suggestion | None:
    with Session(engine) as session:
        suggestion = session.get(Suggestion, suggestion_id)
        if suggestion is None:
            return None
        suggestion.rating = rating
        suggestion.feedback_reason = reason
        suggestion.feedback_at = datetime.now(timezone.utc)
        session.add(suggestion)
        session.commit()
        session.refresh(suggestion)
        return suggestion


def get_top_rated_examples(limit: int = 2) -> list[Suggestion]:
    """
    Sugestões que os devs marcaram como "positivo", usadas como exemplos
    (few-shot) para calibrar a IA em análises futuras — uma forma simples e
    honesta de "aprendizado" com feedback, já que fine-tuning não é viável
    no tier gratuito do Gemini.
    """
    with Session(engine) as session:
        statement = (
            select(Suggestion)
            .where(Suggestion.rating == "positivo")
            .order_by(Suggestion.feedback_at.desc())
            .limit(limit)
        )
        return list(session.exec(statement).all())
