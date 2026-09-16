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
from app.models import CodeChunk, Suggestion


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


def _fill_examples(
    session: Session, base_query, exclude_ids: set[int], examples: list, limit: int
) -> None:
    """Acrescenta a `examples` (in-place) resultados de `base_query` que ainda
    não estão em `exclude_ids`, até atingir `limit` no total."""
    if len(examples) >= limit:
        return
    for ex in session.exec(base_query).all():
        if ex.id not in exclude_ids:
            examples.append(ex)
            exclude_ids.add(ex.id)
            if len(examples) >= limit:
                return


def get_top_rated_examples(
    owner: str,
    repo: str,
    limit: int = 2,
    filename: str | None = None,
    function_name: str | None = None,
) -> list[Suggestion]:
    """
    Sugestões que os devs marcaram como "positivo", usadas como exemplos
    (few-shot) para calibrar a IA em análises futuras — uma forma simples e
    honesta de "aprendizado" com feedback, já que fine-tuning não é viável
    no tier gratuito do Gemini.

    Prioriza em 3 níveis, do mais específico ao mais genérico, só descendo de
    nível quando o anterior não tem exemplos suficientes pra completar
    `limit` (evita ficar sem exemplo nenhum num repositório/função novos):

    1. Essa MESMA função, no mesmo arquivo, já avaliada como boa antes — a
       referência mais relevante possível (mesmo código, mesmo contexto,
       possivelmente até o mesmo tipo de risco recorrente).
    2. Qualquer função do MESMO repositório — convenções/domínio parecidos.
    3. Qualquer repositório — só pra nunca ficar sem exemplo nenhum.
    """
    with Session(engine) as session:
        examples: list[Suggestion] = []
        exclude_ids: set[int] = set()

        if filename and function_name:
            _fill_examples(
                session,
                select(Suggestion)
                .where(
                    Suggestion.rating == "positivo",
                    Suggestion.owner == owner,
                    Suggestion.repo == repo,
                    Suggestion.filename == filename,
                    Suggestion.function_name == function_name,
                )
                .order_by(Suggestion.feedback_at.desc()),
                exclude_ids, examples, limit,
            )

        _fill_examples(
            session,
            select(Suggestion)
            .where(
                Suggestion.rating == "positivo",
                Suggestion.owner == owner,
                Suggestion.repo == repo,
            )
            .order_by(Suggestion.feedback_at.desc()),
            exclude_ids, examples, limit,
        )

        _fill_examples(
            session,
            select(Suggestion)
            .where(Suggestion.rating == "positivo")
            .order_by(Suggestion.feedback_at.desc()),
            exclude_ids, examples, limit,
        )

        return examples


def get_all_suggestions() -> list[Suggestion]:
    """
    Todas as sugestões já geradas, da mais antiga pra mais nova. Usado pelo
    export em CSV — pra um dataset pequeno como o de um experimento de TCC,
    trazer tudo de uma vez é simples e suficiente (sem paginação).
    """
    with Session(engine) as session:
        statement = select(Suggestion).order_by(Suggestion.created_at)
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

        # Comparação entre as condições COM e SEM recuperação semântica
        # (RAG) — é a métrica central do experimento: as sugestões que
        # tiveram contexto do repositório foram mais aceitas que as que não
        # tiveram? Só conta sugestões que receberam avaliação, já que sem
        # feedback não há o que comparar.
        rag_comparison = {}
        for label, flag in (("com_rag", True), ("sem_rag", False)):
            avaliadas = session.exec(
                select(sql_func.count())
                .select_from(Suggestion)
                .where(Suggestion.used_rag == flag, Suggestion.rating.is_not(None))
            ).one()
            positivas = session.exec(
                select(sql_func.count())
                .select_from(Suggestion)
                .where(Suggestion.used_rag == flag, Suggestion.rating == "positivo")
            ).one()
            rag_comparison[label] = {
                "avaliadas": avaliadas,
                "positivas": positivas,
                "taxa_aprovacao": round(positivas / avaliadas, 3) if avaliadas else None,
            }

        return {
            "total_suggestions": total,
            "by_risk_level": by_risk,
            "by_rating": by_rating,
            "by_llm_model": by_model,
            "rag_comparison": rag_comparison,
            "feedback_rate": round(with_feedback / total, 3) if total else 0,
            "positive_rate_among_rated": (
                round(positive / with_feedback, 3) if with_feedback else None
            ),
        }


def pr_already_analyzed(owner: str, repo: str, pr_number: int) -> bool:
    """
    Se já existe alguma sugestão salva para esse PR. Usado pelo modo
    experimento para retomar de onde parou, sem reprocessar (e sem gastar
    cota de LLM de novo) o que já foi analisado numa rodada anterior.
    """
    with Session(engine) as session:
        found = session.exec(
            select(Suggestion.id).where(
                Suggestion.owner == owner,
                Suggestion.repo == repo,
                Suggestion.pr_number == pr_number,
            ).limit(1)
        ).first()
        return found is not None


# --- Chunks indexados para recuperação semântica (RAG) ---


def get_indexed_file_hashes(owner: str, repo: str) -> dict[str, str]:
    """
    Mapa {arquivo: hash} do que já está indexado para esse repositório.
    Usado para pular o reprocessamento de arquivos que não mudaram desde a
    última indexação (economiza chamadas de embedding e da API do GitHub).
    """
    with Session(engine) as session:
        rows = session.exec(
            select(CodeChunk.filename, CodeChunk.file_hash)
            .where(CodeChunk.owner == owner, CodeChunk.repo == repo)
            .distinct()
        ).all()
        return {filename: file_hash for filename, file_hash in rows}


def replace_file_chunks(
    owner: str, repo: str, filename: str, chunks: list[CodeChunk]
) -> None:
    """
    Substitui os chunks de UM arquivo: apaga os antigos e grava os novos.
    Substituir (em vez de acrescentar) evita acumular versões velhas de uma
    função que mudou.
    """
    with Session(engine) as session:
        existing = session.exec(
            select(CodeChunk).where(
                CodeChunk.owner == owner,
                CodeChunk.repo == repo,
                CodeChunk.filename == filename,
            )
        ).all()
        for row in existing:
            session.delete(row)

        for chunk in chunks:
            session.add(chunk)

        session.commit()


def get_repo_chunks(owner: str, repo: str) -> list[CodeChunk]:
    """Todos os chunks indexados de um repositório (usado na busca por similaridade)."""
    with Session(engine) as session:
        return list(session.exec(
            select(CodeChunk).where(CodeChunk.owner == owner, CodeChunk.repo == repo)
        ).all())


def count_repo_chunks(owner: str, repo: str) -> int:
    with Session(engine) as session:
        return session.exec(
            select(sql_func.count())
            .select_from(CodeChunk)
            .where(CodeChunk.owner == owner, CodeChunk.repo == repo)
        ).one()
