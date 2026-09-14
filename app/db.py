"""
Configuração da conexão com o banco e funções de acesso aos dados de
sugestões/feedback (ver app/models.py).

Usa SQLModel por cima do SQLAlchemy, o que permite rodar em SQLite local
(padrão, zero configuração) ou Postgres em produção — basta apontar
DATABASE_URL (ver app/config.py) para o Postgres do Render/Supabase.
"""
from datetime import datetime, timezone

from sqlalchemy import func as sql_func
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


def find_unchanged_suggestion(
    owner: str,
    repo: str,
    pr_number: int,
    filename: str,
    function_name: str,
    code_hash: str,
) -> Suggestion | None:
    """
    Procura uma análise já feita, neste mesmo PR, para essa função com esse
    exato código (mesmo hash) — usado para pular a chamada ao LLM quando um
    novo push no PR não alterou de fato essa função (economiza cota).
    """
    with Session(engine) as session:
        statement = select(Suggestion).where(
            Suggestion.owner == owner,
            Suggestion.repo == repo,
            Suggestion.pr_number == pr_number,
            Suggestion.filename == filename,
            Suggestion.function_name == function_name,
            Suggestion.code_hash == code_hash,
        )
        return session.exec(statement).first()


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


def get_stats() -> dict:
    """
    Resumo agregado de todas as sugestões geradas — pensado para alimentar
    diretamente a análise/experimento do TCC (comparação de qualidade entre
    modelos, taxa de aprovação dos devs, distribuição de risco) sem precisar
    exportar dados manualmente da Supabase.
    """
    with Session(engine) as session:
        total = session.exec(select(sql_func.count()).select_from(Suggestion)).one()

        by_risk = dict(
            session.exec(
                select(Suggestion.risk_level, sql_func.count())
                .group_by(Suggestion.risk_level)
            ).all()
        )

        by_rating = dict(
            session.exec(
                select(Suggestion.rating, sql_func.count())
                .group_by(Suggestion.rating)
            ).all()
        )
        # None vira chave "sem_feedback" — mais legível que `null` no JSON.
        by_rating["sem_feedback"] = by_rating.pop(None, 0)

        by_model = dict(
            session.exec(
                select(Suggestion.llm_model, sql_func.count())
                .group_by(Suggestion.llm_model)
            ).all()
        )

        with_feedback = sum(v for k, v in by_rating.items() if k != "sem_feedback")
        positive = by_rating.get("positivo", 0)

        return {
            "total_suggestions": total,
            "by_risk_level": by_risk,
            "by_rating": by_rating,
            "by_llm_model": by_model,
            "feedback_rate": round(with_feedback / total, 3) if total else 0,
            "positive_rate_among_rated": (
                round(positive / with_feedback, 3) if with_feedback else None
            ),
        }
