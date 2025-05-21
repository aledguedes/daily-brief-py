# src/database.py
#PostgresQL
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.config import Config
import logging
from sqlalchemy.ext.declarative import declarative_base # IMPORTAR AQUI!

logger = logging.getLogger(__name__)

# Configuração do PostgreSQL
# DATABASE_URL é construída na classe Config
DATABASE_URL = Config.DATABASE_URL

logger.info(f"Configurando engine SQLAlchemy para URL: postgresql://{Config.DB_USER}:******@{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}")

try:
    # Criação do engine usando o dialeto postgresql+psycopg2
    engine = create_engine(DATABASE_URL)
    logger.info("Engine SQLAlchemy criado com sucesso.")

    # Opcional: Testar a conexão ao iniciar o módulo
    try:
        with engine.connect() as connection:
            logger.info("Teste de conexão inicial com banco de dados PostgreSQL via SQLAlchemy bem-sucedido.")
    except Exception as e:
        logger.error(f"Falha no teste de conexão inicial com banco de dados PostgreSQL via SQLAlchemy: {str(e)}", exc_info=True)
        # Dependendo da sua aplicação, pode ser necessário sair ou lidar com isso
        # exit(1) # Saia se a conexão com o DB for crítica para iniciar


except Exception as e:
    logger.critical(f"Erro CRÍTICO ao configurar engine SQLAlchemy para {Config.DB_NAME}: {str(e)}", exc_info=True)
    # É importante levantar a exceção para que a aplicação não inicie com BD não configurado
    raise RuntimeError(f"Falha ao configurar engine SQLAlchemy: {str(e)}")


# Criação da sessão
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
logger.info("SessionLocal para SQLAlchemy configurada.")

# Base para modelos declarativos (AGORA DEFINIDA AQUI)
Base = declarative_base() # ESTA LINHA ESTAVA FALTANDO/COMENTADA!


# Função para obter sessão de banco de dados (dependência FastAPI)
def get_db():
    """Dependency para obter uma sessão de banco de dados."""
    db = SessionLocal()
    try:
        logger.debug("Obtendo sessão de banco de dados.")
        yield db
    finally:
        logger.debug("Fechando sessão de banco de dados.")
        db.close()