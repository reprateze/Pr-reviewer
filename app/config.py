"""
Configurações centrais da aplicação.
Lê variáveis de ambiente (definidas no .env ou nos Secrets do GitHub Actions).
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

# Carrega as variáveis do arquivo .env para o ambiente do processo.
# Sem isso, os.getenv() não encontra as chaves definidas no .env local.
load_dotenv()


@dataclass
class Settings:
    # Token de acesso ao GitHub (para postar comentários no PR)
    github_token: str = os.getenv("GITHUB_TOKEN", "")

    # Chave da API do provedor de LLM (Anthropic, OpenAI, etc.)
    llm_api_key: str = os.getenv("LLM_API_KEY", "")

    # Nome do modelo a ser usado (Gemini, via Google AI Studio).
    #
    # O padrão é um "flash-lite" por causa da cota: os modelos "flash"
    # comuns dão 20 requisições por dia no plano gratuito, enquanto os
    # "lite" dão 500 — a diferença entre analisar 10 e 250 PRs por dia.
    #
    # A versão é fixada de propósito (em vez de um alias tipo
    # "flash-lite-latest"): um alias muda de modelo sozinho com o tempo, o
    # que quebraria a reprodutibilidade dos resultados do experimento.
    llm_model: str = os.getenv("LLM_MODEL", "gemini-3.5-flash-lite")

    # Intervalo mínimo (em segundos) entre chamadas ao LLM, para respeitar o
    # limite de requisições por minuto (RPM) do plano gratuito.
    #
    # O padrão (4s) acompanha o modelo padrão, um "flash-lite" de 15 RPM —
    # 4s dá 15 chamadas por minuto com folga. Se trocar LLM_MODEL para um
    # "flash" comum (5 RPM), suba este valor para 15, senão as chamadas
    # começam a tomar erro 429.
    llm_min_request_interval_seconds: int = int(
        os.getenv("LLM_MIN_REQUEST_INTERVAL_SECONDS", "4")
    )

    # Quantas funções (no máximo) são analisadas por arquivo alterado em um
    # PR. Cada função analisada custa 1 requisição ao LLM — em planos
    # gratuitos com cota de requisições por dia (RPD) baixa, um número maior
    # aqui esgota a cota mais rápido. Ajuste conforme a cota do modelo em uso.
    max_functions_per_pr: int = int(os.getenv("MAX_FUNCTIONS_PER_PR", "2"))

    # Extensões de arquivo que serão analisadas (MVP: só Python)
    supported_extensions: tuple = (".py",)

    # Tamanho máximo (em linhas) de um arquivo alterado que será enviado à IA
    # Evita mandar diffs gigantes para o modelo
    max_diff_lines: int = 400

    # Tamanho máximo (em caracteres) do bloco de "contexto adicional" (código
    # de dependências + testes já existentes) enviado no prompt. Com cota
    # gratuita de tokens/requisições (ex: Gemini Flash free tier), precisamos
    # de um teto duro pra não estourar o limite mesmo quando a função analisada
    # tem muitas dependências.
    max_extra_context_chars: int = int(os.getenv("MAX_EXTRA_CONTEXT_CHARS", "2000"))

    # Onde persistir as sugestões geradas e o feedback dos devs sobre elas.
    # Por padrão usa SQLite local (zero configuração, ótimo para desenvolver
    # ou rodar o experimento do TCC sem depender de infra externa). Em
    # produção, aponte para um Postgres (Render/Supabase) via esta env var.
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data.db")

    # Segredo compartilhado com o GitHub para validar a assinatura
    # (X-Hub-Signature-256) dos webhooks recebidos em /webhook/github.
    # Configurado ao criar o webhook em Settings > Webhooks do repositório.
    github_webhook_secret: str = os.getenv("GITHUB_WEBHOOK_SECRET", "")

    # --- RAG (recuperação contextual por embeddings) ---

    # Liga/desliga a recuperação semântica de trechos do repositório. Fica
    # como flag justamente para permitir comparar as MESMAS análises com e
    # sem RAG (o experimento do TCC precisa dos dois cenários).
    rag_enabled: bool = os.getenv("RAG_ENABLED", "false").lower() == "true"

    # Modelo de embeddings (cota separada da cota de geração de texto).
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")

    # Tamanho do vetor. O modelo devolve 3072 por padrão, mas suporta truncar
    # para tamanhos menores — 768 reduz em 4x o espaço no banco e o custo de
    # comparar vetores, com perda pequena de precisão.
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "768"))

    # Quantos trechos semelhantes são recuperados e enviados como contexto.
    rag_top_k: int = int(os.getenv("RAG_TOP_K", "3"))

    # Orçamento de caracteres SÓ para o bloco recuperado por RAG, somado ao
    # max_extra_context_chars quando o RAG está ligado. Sem esse orçamento
    # próprio, os trechos recuperados seriam cortados pelo teto geral e o
    # RAG ficaria ligado "no papel", sem efeito real no prompt.
    rag_max_context_chars: int = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "1500"))

    # Teto de arquivos .py indexados por repositório. Cada arquivo custa 1
    # chamada à API do GitHub + N chamadas de embedding (uma por função),
    # então em repositório grande isso precisa de limite.
    rag_max_files_to_index: int = int(os.getenv("RAG_MAX_FILES_TO_INDEX", "50"))


settings = Settings()