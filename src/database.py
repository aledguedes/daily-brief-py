# src/database.py

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import logging
from src.config import Config

logger = logging.getLogger(__name__)

# [cite_start]A URL do banco de dados é montada a partir da classe de configuração [cite: 61]
DATABASE_URL = Config.DATABASE_URL

# [cite_start]Logging da configuração para depuração, sem expor senhas [cite: 103]
logger.info(
    f"Configurando engine SQLAlchemy para URL: postgresql://{Config.DB_USER}:******@{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}"
)

try:
    engine = create_engine(DATABASE_URL)
    # [cite_start]logger.info("Engine SQLAlchemy criado com sucesso.") [cite: 103]
    logger.info("Engine SQLAlchemy criado com sucesso.")

    # Teste opcional de conexão
    with engine.connect() as connection:
        logger.info(
            "Teste de conexão com banco de dados PostgreSQL via SQLAlchemy bem-sucedido."
        )
except Exception as e:
    logger.critical(
        f"Erro CRÍTICO ao configurar engine SQLAlchemy: {str(e)}", exc_info=True
    )
    raise RuntimeError(f"Falha ao configurar engine SQLAlchemy: {str(e)}")

# Criação da sessão que será usada pelas rotas da API
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
logger.info("SessionLocal para SQLAlchemy configurada.")

# Base para os modelos declarativos que serão definidos em models.py
Base = declarative_base()


# Função de dependência para o FastAPI, para injetar a sessão do DB nas rotas
def get_db():
    """Dependência do FastAPI para obter uma sessão de banco de dados."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
