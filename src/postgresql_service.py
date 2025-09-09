import psycopg2
from psycopg2 import extras
import logging
from typing import Dict, Any, Optional

# Importa a classe de configuração centralizada
from src.config import Config

logger = logging.getLogger(__name__)


def get_pg_connection():
    """
    Cria e retorna uma conexão com o banco de dados PostgreSQL usando a classe Config.
    """
    try:
        conn = psycopg2.connect(
            host=Config.DB_HOST,
            dbname=Config.DB_NAME,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            port=Config.DB_PORT,
        )
        return conn
    except psycopg2.OperationalError as e:
        logger.error(f"Erro de conexão com o PostgreSQL: {e}")
        raise


def save_automation_data_to_postgres(data: Dict[str, Any]) -> Optional[Dict]:
    """
    Salva os dados de automação no banco de dados PostgreSQL.
    Retorna o registro salvo ou None em caso de erro.
    """
    sql = """
        INSERT INTO materials (user_id, automation_request_id, task_id, status, theme, content_type)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *;
    """
    conn = None
    try:
        conn = get_pg_connection()
        cur = conn.cursor(cursor_factory=extras.RealDictCursor)

        # Adapte a ordem dos campos e os dados de acordo com sua tabela real
        values = (
            data.get("user_id"),
            data.get("automation_request_id"),
            data.get("task_id"),
            data.get("status"),
            data.get("theme"),
            data.get("content_type"),
        )

        cur.execute(sql, values)
        saved_record = cur.fetchone()
        conn.commit()
        logger.info(f"Dados salvos no PostgreSQL para o tema: {data.get('theme')}")
        return saved_record
    except (Exception, psycopg2.DatabaseError) as error:
        logger.error(f"Erro ao salvar dados no PostgreSQL: {error}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()
