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


settings = Settings()