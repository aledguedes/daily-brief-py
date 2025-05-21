# src/init_db.py

from sqlalchemy import create_engine, MetaData
from src.models import Base
from src.config import Config
import logging

logger = logging.getLogger(__name__)

# Configurar conexão com PostgreSQL
DATABASE_URL = Config.DATABASE_URL
logger.info(f"Configurando engine para criação de tabelas em: postgresql://{Config.DB_USER}:******@{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}")

try:
    engine = create_engine(DATABASE_URL)
    logger.info("Engine para criação de tabelas criado com sucesso.")

    # Criar tabelas definidas em models.py
    logger.info("Criando tabelas no banco de dados...")
    Base.metadata.create_all(engine)
    logger.info("Tabelas criadas (ou já existentes) com sucesso!")

except Exception as e:
    logger.critical(f"Erro CRÍTICO ao criar tabelas no banco de dados: {str(e)}", exc_info=True)
    raise RuntimeError(f"Falha ao criar tabelas no banco de dados: {str(e)}")

if __name__ == "__main__":
    logger.info("Executando script init_db.py diretamente.")
    print("Script de inicialização do banco de dados executado.")