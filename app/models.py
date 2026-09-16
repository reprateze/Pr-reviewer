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

    # Lista de {"issue": str, "suggestion": str} — problemas concretos que a
    # IA identificou no código (não só cenário sem teste) e como corrigi-los.
    # Fica vazia quando a IA não aponta nada digno de nota.
    suggested_improvements: list = Field(default_factory=list, sa_column=Column(JSON))

    # Qual modelo do LLM gerou esta sugestão (ex: "gemini-3.6-flash-lite").
    # Guardado por sugestão (não só em config) porque o modelo configurado
    # pode mudar ao longo do tempo — útil para comparar qualidade entre
    # modelos na análise do experimento.
    llm_model: str | None = None

    # Hash (sha256) do código-fonte da função no momento da análise. Permite
    # detectar, num push seguinte ao mesmo PR, se essa função realmente
    # mudou desde a última análise — evita gastar cota do LLM reanalisando
    # uma função que não foi tocada entre um "synchronize" e outro.
    code_hash: str | None = Field(default=None, index=True)

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

    # Versão do prompt do sistema usada nesta análise (ver PROMPTS em
    # llm_client.py) — necessário para interpretar os dados depois.
    prompt_version: str | None = None

    # Se esta análise usou contexto recuperado por embeddings (RAG) ou não.
    # Guardado por sugestão pra permitir comparar as duas condições no
    # experimento — sem isso não dá pra saber, olhando o banco depois, quais
    # sugestões tiveram contexto semântico e quais não.
    used_rag: bool = False


class BugDetectionCase(SQLModel, table=True):
    """
    Um defeito REAL reconstituído do histórico do projeto, junto com o que a
    ferramenta disse sobre ele.

    Fica em tabela separada de `Suggestion` de propósito: aqui não existe
    Pull Request, não existe comentário postado e não existe dev avaliando —
    é medição contra verdade de referência, natureza diferente do uso normal.

    O campo `detectou` começa vazio e é preenchido depois, na avaliação:
    a ferramenta apontou o mesmo problema que a correção real resolveu?
    É a única pergunta que importa aqui, e ela tem resposta verificável —
    diferente de "você achou a sugestão útil?", que é opinião.
    """

    id: int | None = Field(default=None, primary_key=True)

    owner: str = Field(index=True)
    repo: str = Field(index=True)

    # O commit que corrigiu o defeito (a prova de que ele existia) e o pai,
    # onde o defeito ainda está presente e que foi efetivamente analisado.
    sha_da_correcao: str = Field(index=True)
    sha_com_bug: str
    mensagem_da_correcao: str

    filename: str
    function_name: str

    used_rag: bool = False
    llm_model: str | None = None
    # Versão do prompt usada. Sem isso, comparar resultados de épocas
    # diferentes fica impossível: não dá pra saber se a diferença veio da
    # condição testada ou de uma mudança de prompt no meio do caminho.
    prompt_version: str | None = None

    risk_level: str
    risk_reason: str
    suggested_tests: list = Field(default_factory=list, sa_column=Column(JSON))
    suggested_improvements: list = Field(default_factory=list, sa_column=Column(JSON))

    # Preenchido na avaliação: True = apontou o mesmo defeito, False = não
    # apontou, None = ainda não julgado.
    detectou: bool | None = None
    nota_do_avaliador: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CodeChunk(SQLModel, table=True):
    """
    Um trecho de código do repositório, indexado com seu embedding, para
    recuperação semântica (RAG).

    Cada chunk é UMA FUNÇÃO inteira (não um corte arbitrário a cada N
    tokens): a função é a unidade semântica natural do código, e o projeto
    já tem um extrator de funções via AST (ver code_analyzer.py). Cortar a
    cada N tokens partiria funções no meio e pioraria a recuperação.

    O embedding é guardado como JSON (lista de floats) em vez de um tipo
    vetorial nativo (pgvector). Motivo: funciona igual em SQLite (testes e
    dev local) e Postgres (produção), sem extensão nem código específico por
    backend. Na escala deste projeto — alguns milhares de chunks — calcular
    a similaridade em Python é rápido o suficiente. Se um dia o volume
    crescer muito (dezenas de milhares de chunks por repositório), aí vale
    migrar para pgvector com índice HNSW.
    """

    id: int | None = Field(default=None, primary_key=True)

    owner: str = Field(index=True)
    repo: str = Field(index=True)
    filename: str
    function_name: str

    # Código-fonte da função, que é o texto efetivamente embedado.
    content: str

    # Vetor do embedding, como lista de floats.
    embedding: list = Field(default_factory=list, sa_column=Column(JSON))

    # Norma euclidiana do vetor, pré-calculada na indexação — evita
    # recalcular a cada consulta de similaridade (é o denominador do cosseno).
    embedding_norm: float = 0.0

    # Hash do conteúdo do ARQUIVO inteiro no momento da indexação. Permite
    # pular o reprocessamento (e as chamadas de embedding) de arquivos que
    # não mudaram desde a última vez.
    file_hash: str = Field(index=True)

    indexed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
