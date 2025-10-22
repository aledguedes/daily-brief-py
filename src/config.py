# src/config.py
import os
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

# Carrega variáveis do arquivo .env
load_dotenv()


class Config:
    """
    Classe para carregar e gerenciar as configurações da aplicação
    a partir de variáveis de ambiente.
    """

    # Backend API URLs
    BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8080")
    API_URL = os.getenv("API_URL", f"{BACKEND_URL}/api/posts")
    AUTH_URL = os.getenv("AUTH_URL", f"{BACKEND_URL}/api/auth/login")
    LOGS_API_URL = os.getenv("LOGS_API_URL", f"{BACKEND_URL}/api/logs")
    SPRING_BOOT_API_URL = os.getenv("SPRING_BOOT_API_URL", f"{BACKEND_URL}/api")

    # Credenciais do Admin para Autenticação no Backend
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

    # Configurações de Idioma e Formato
    LANGUAGE = os.getenv("LANGUAGE", "pt-BR")
    OUTPUT_FORMAT = os.getenv("OUTPUT_FORMAT", "summary").lower()
    TARGET_LANGUAGES = [
        lang.strip() for lang in os.getenv("TARGET_LANGUAGES", "PT,EN,ES").split(",")
    ]

    # Chaves das APIs Externas
    NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    SERPER_API_KEY = os.getenv("SERPER_API_KEY")
    UNSPLASH_API_KEY = os.getenv("UNSPLASH_API_KEY")

    # Configuração DeepSeek (NOVO)
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v2")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

    # Configurações do Gemini
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    MAX_TEXT_LEN = int(os.getenv("MAX_TEXT_LEN", 30000))

    # Outras Configurações
    REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", 30))
    CACHE_DURATION_HOURS = int(os.getenv("CACHE_DURATION_HOURS", 24))
    DEFAULT_AUTHOR = os.getenv("DEFAULT_AUTHOR", "Equipe DailyBrief")
    DEFAULT_STATUS = os.getenv("DEFAULT_STATUS", "PENDING")
    MAX_THEMES_PER_RUN = int(os.getenv("MAX_THEMES_PER_RUN", 5))  # Adicionado

    # Configurações de Email
    EMAIL_FROM = os.getenv("EMAIL_FROM")
    EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
    EMAIL_TO = os.getenv("EMAIL_TO")

    # Configurações de Reddit
    REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
    REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
    REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT")

    # Configuração de JWT
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

    # Configuração de Banco de Dados PostgreSQL
    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DB_HOST = os.getenv("DB_HOST")
    DB_PORT = os.getenv("DB_PORT")
    DB_NAME = os.getenv("DB_NAME")
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    IMAGE_PROMPT_PROVIDER = ""

    # Validação de Variáveis de Ambiente Obrigatórias
    REQUIRED_ENV_VARS = [
        "API_URL",
        "AUTH_URL",
        "ADMIN_EMAIL",
        "ADMIN_PASSWORD",
        "GEMINI_API_KEY",
        "SERPER_API_KEY",
        "JWT_SECRET_KEY",
        "DB_USER",
        "DB_PASSWORD",
        "DB_HOST",
        "DB_NAME",
        "MAX_THEMES_PER_RUN",  # Adicionado
    ]

    for var in REQUIRED_ENV_VARS:
        if not os.getenv(var):
            logger.critical(
                f"Variável de ambiente obrigatória '{var}' não configurada no .env."
            )
            raise EnvironmentError(
                f"Variável de ambiente obrigatória '{var}' não configurada no .env"
            )


# Loga as configurações carregadas (apenas para depuração)
logger.debug("Configurações carregadas:")
for attr in dir(Config):
    if not attr.startswith("__") and not callable(getattr(Config, attr)):
        if "KEY" in attr or "PASSWORD" in attr or "SECRET" in attr:
            logger.debug(f"{attr}: {'*' * 8}")
        else:
            logger.debug(f"{attr}: {getattr(Config, attr)}")
