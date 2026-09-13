"""
Modelos de persistência (SQLModel) para as sugestões geradas pela IA e o
feedback que o dev dá sobre elas.

Funciona tanto em SQLite (dev local / experimento do TCC) quanto em Postgres
(produção) — ver DATABASE_URL em app/config.py.
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Column
from sqlmodel import Field, SQLModel


class Suggestion(SQLModel, table=True):
    """
    Uma análise gerada pela IA para UMA função de UM PR, junto com o
    comentário de review correspondente postado no GitHub.

    `github_comment_id` é o que permite correlacionar, depois, a resposta do
    dev (via webhook, usando `in_reply_to_id`) com ESTA sugestão específica —
    sem isso não teríamos como saber qual das várias funções analisadas num
    PR está sendo avaliada.
    """

    id: int | None = Field(default=None, primary_key=True)

    owner: str
    repo: str
    pr_number: int
    commit_sha: str
    filename: str
    function_name: str

    risk_level: str
    risk_reason: str
    # Lista de {"title": str, "description": str}. JSON funciona tanto em
    # SQLite (guardado como texto) quanto em Postgres (guardado como jsonb).
    suggested_tests: list = Field(default_factory=list, sa_column=Column(JSON))

    # id do comentário de review no GitHub. None quando o comentário não pôde
    # ser ancorado numa linha do diff e a sugestão caiu no fallback de
    # comentário único (ver main.py) — nesse caso não há como capturar feedback
    # em thread para essa sugestão específica.
    #
    # BigInteger (não o Integer/int4 padrão): os ids de comentário do GitHub
    # já passam de 4 bilhões, muito além do limite de ~2.1 bilhões do int4.
    github_comment_id: int | None = Field(
        default=None, sa_column=Column(BigInteger, index=True)
    )

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Preenchidos depois, quando o dev responde ao comentário avaliando a
    # sugestão (ver /webhook/github em main.py).
    rating: str | None = None  # "positivo" | "negativo"
    feedback_reason: str | None = None
    feedback_at: datetime | None = None
