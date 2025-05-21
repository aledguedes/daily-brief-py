# src/config.py
import os
from dotenv import load_dotenv
import logging # Importar logging

logger = logging.getLogger(__name__)

# Carrega variáveis do arquivo .env
load_dotenv()

class Config:
    # Backend API URLs
    API_URL = os.getenv("API_URL")
    AUTH_URL = os.getenv("AUTH_URL")
    LOGS_API_URL = os.getenv("LOGS_API_URL")

    # Credenciais do Admin para Autenticação no Backend
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

    # Configurações de Idioma e Formato
    LANGUAGE = os.getenv("LANGUAGE", "pt-BR") 
    TARGET_LANGUAGES = ["PT", "EN", "ES"] # Idiomas alvo para geração de conteúdo
    OUTPUT_FORMAT = os.getenv("OUTPUT_FORMAT", "summary").lower() 

    # Chaves das APIs Externas
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    SERPER_API_KEY = os.getenv("SERPER_API_KEY")
    NEWSAPI_KEY = os.getenv("NEWSAPI_KEY") 
    UNSPLASH_API_KEY = os.getenv("UNSPLASH_API_KEY") 

    # Configurações do Gemini
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash-latest")
    MAX_TEXT_LEN = int(os.getenv("MAX_TEXT_LEN", 30000)) 

    # Outras Configurações
    REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", 30)) 
    CACHE_DURATION_HOURS = int(os.getenv("CACHE_DURATION_HOURS", 24)) 
    DEFAULT_AUTHOR = os.getenv("DEFAULT_AUTHOR", "Equipe DailyBrief") 
    DEFAULT_STATUS = os.getenv("DEFAULT_STATUS", "PENDING") 

    # Configurações de Email (se usadas no seu script)
    EMAIL_FROM = os.getenv("EMAIL_FROM")
    EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
    EMAIL_TO = os.getenv("EMAIL_TO")

    # Configurações de Reddit (se usadas no seu script)
    REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
    REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
    REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "daily-brief-bot/1.0")

    # Configuração de JWT (para o servidor Python verificar tokens recebidos)
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

    # Configuração de Banco de Dados PostgreSQL
    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DB_HOST = os.getenv("DB_HOST")
    DB_PORT = os.getenv("DB_PORT")
    DB_NAME = os.getenv("DB_NAME")

    # --- Validação de Variáveis de Ambiente Obrigatórias ---
    # Adicionado validação explícita para variáveis críticas
    REQUIRED_ENV_VARS = [
        "API_URL", "AUTH_URL", "ADMIN_EMAIL", "ADMIN_PASSWORD",
        "GEMINI_API_KEY", "SERPER_API_KEY", "JWT_SECRET_KEY",
        "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_NAME"
        # Adicione outras chaves de API ou URLs que sejam obrigatórias
    ]

    for var in REQUIRED_ENV_VARS:
        if not os.getenv(var):
            logger.critical(f"Variável de ambiente obrigatória '{var}' não configurada no .env. O script pode não funcionar corretamente.")
            # Opcional: Levantar um erro fatal para interromper a execução se uma variável crítica faltar
            # raise EnvironmentError(f"Variável de ambiente obrigatória '{var}' não configurada no .env")

    # Construída aqui para ser usada no database.py
    DATABASE_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"