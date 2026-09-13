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

    # Nome do modelo a ser usado (Gemini, via Google AI Studio)
    llm_model: str = os.getenv("LLM_MODEL", "gemini-2.0-flash")

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


settings = Settings()