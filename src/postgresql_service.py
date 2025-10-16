import psycopg2
from psycopg2 import extras
import logging
import asyncio
from typing import Dict, Any, Optional
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


# src/postgresql_service.py


def save_automation_data_to_postgres(data: Dict[str, Any]) -> Optional[Dict]:
    """
    Salva os dados de automação no banco de dados PostgreSQL.
    Retorna o registro salvo ou None em caso de erro.
    """
    sql = """
        INSERT INTO materials (user_id, automation_request_id, task_id, status_id, theme, content_type)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *;
    """
    conn = None
    try:
        conn = get_pg_connection()
        cur = conn.cursor(cursor_factory=extras.RealDictCursor)

        status_pending_id = 1

        values = (
            data.get("user_id"),
            data.get("automation_request_id"),
            data.get("task_id"),
            status_pending_id,
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


def get_status_id_by_name(conn, status_name: str) -> Optional[int]:
    """Busca o ID do status pelo nome na tabela material_status."""
    sql = "SELECT id FROM material_status WHERE name = %s;"
    try:
        # Cursor sem extras.RealDictCursor para fetchone simples
        cur = conn.cursor()
        cur.execute(sql, (status_name,))
        result = cur.fetchone()
        if result:
            return result[0]
        logger.error(
            f"Status '{status_name}' não encontrado na tabela material_status."
        )
        return None
    except Exception as e:
        logger.error(
            f"Erro ao buscar status ID para {status_name}: {str(e)}", exc_info=True
        )
        raise  # Re-levanta a exceção


def update_material_status(
    conn, user_id: str, task_id: str, new_status_name: str
) -> bool:
    """
    Atualiza o status de um material no PostgreSQL usando o nome do status.
    (Função síncrona para ser chamada por asyncio.to_thread)
    """
    status_id = get_status_id_by_name(conn, new_status_name)
    if status_id is None:
        return False

    sql = """
        UPDATE materials 
        SET status_id = %s, updated_at = NOW() 
        WHERE task_id = %s AND user_id = %s;
    """
    try:
        cur = conn.cursor()
        cur.execute(sql, (status_id, task_id, user_id))
        conn.commit()
        logger.info(
            f"Status da task '{task_id}' atualizado para '{new_status_name}' (ID: {status_id})."
        )
        return True
    except Exception as e:
        conn.rollback()
        logger.error(
            f"Erro ao atualizar status do material para task_id '{task_id}': {str(e)}",
            exc_info=True,
        )
        raise


async def update_material_status_utility(
    user_id: str, task_id: str, new_status_name: str
):
    """
    Função assíncrona wrapper que executa a atualização de status no PostgreSQL
    em um thread separado para evitar bloqueio do event loop.
    """
    logger.info(
        f"Iniciando atualização de status async para task_id '{task_id}' com status: {new_status_name}."
    )

    def sync_update():
        conn = None
        try:
            conn = get_pg_connection()
            update_material_status(conn, user_id, task_id, new_status_name)
        finally:
            if conn:
                conn.close()

    try:
        await asyncio.to_thread(sync_update)
    except Exception as e:
        logger.error(
            f"Erro na execução da atualização de status (async) para task_id '{task_id}': {str(e)}"
        )
        raise
