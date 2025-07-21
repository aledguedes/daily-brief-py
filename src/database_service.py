# src/database_service.py
import sqlite3
import json
import logging
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)

DATABASE_FILE = "dailybrief.db"


def get_db_connection():
    """Obtém uma conexão com o banco de dados SQLite."""
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row  # Permite acessar colunas por nome
    return conn


def init_db():
    """Inicializa o banco de dados, criando a tabela 'materials' se ela não existir."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            task_id TEXT UNIQUE NOT NULL,
            theme TEXT,
            raw_material TEXT,
            source_urls TEXT, -- Armazenado como JSON string
            content_type TEXT,
            status TEXT NOT NULL, -- Ex: 'PENDING', 'RAW_COLLECTED', 'GENERATING', 'GENERATED', 'FAILED'
            generated_content TEXT, -- Armazenado como JSON string
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    conn.commit()
    conn.close()
    logger.info("Banco de dados SQLite inicializado e tabela 'materials' verificada.")


def save_material(
    user_id: str,
    task_id: str,
    theme: str,
    raw_material: str,
    source_urls: List[str],
    content_type: str,
    status: str,
) -> bool:
    """Salva um novo registro de material no banco de dados."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO materials (user_id, task_id, theme, raw_material, source_urls, content_type, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                task_id,
                theme,
                raw_material,
                json.dumps(source_urls),
                content_type,
                status,
            ),
        )
        conn.commit()
        logger.info(
            f"Material para task_id '{task_id}' (user '{user_id}') salvo com status '{status}'."
        )
        return True
    except sqlite3.IntegrityError as e:
        logger.error(
            f"Erro de integridade ao salvar material para task_id '{task_id}': {e}",
            exc_info=True,
        )
        return False
    except Exception as e:
        logger.error(
            f"Erro ao salvar material para task_id '{task_id}': {e}", exc_info=True
        )
        return False
    finally:
        conn.close()


def update_material_status(
    user_id: str,
    task_id: str,
    status: str,
    generated_content: Optional[Dict[str, Any]] = None,
) -> bool:
    """Atualiza o status e, opcionalmente, o conteúdo gerado de um material."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if generated_content:
            cursor.execute(
                """
                UPDATE materials
                SET status = ?, generated_content = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND task_id = ?
                """,
                (status, json.dumps(generated_content), user_id, task_id),
            )
        else:
            cursor.execute(
                """
                UPDATE materials
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND task_id = ?
                """,
                (status, user_id, task_id),
            )
        conn.commit()
        if cursor.rowcount > 0:
            logger.info(
                f"Status para task_id '{task_id}' (user '{user_id}') atualizado para '{status}'."
            )
            return True
        else:
            logger.warning(
                f"Nenhum material encontrado para atualizar com task_id '{task_id}' e user_id '{user_id}'."
            )
            return False
    except Exception as e:
        logger.error(
            f"Erro ao atualizar status para task_id '{task_id}': {e}", exc_info=True
        )
        return False
    finally:
        conn.close()


def get_material(user_id: str, task_id: str) -> Optional[Dict[str, Any]]:
    """Recupera um material específico pelo user_id e task_id."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM materials WHERE user_id = ? AND task_id = ?",
            (user_id, task_id),
        )
        row = cursor.fetchone()
        if row:
            material = dict(row)
            # Deserializar campos JSON
            if material.get("source_urls"):
                material["source_urls"] = json.loads(material["source_urls"])
            if material.get("generated_content"):
                material["generated_content"] = json.loads(
                    material["generated_content"]
                )
            return material
        return None
    except Exception as e:
        logger.error(
            f"Erro ao obter material para task_id '{task_id}': {e}", exc_info=True
        )
        return None
    finally:
        conn.close()


def list_user_materials(user_id: str) -> List[Dict[str, Any]]:
    """Lista todos os materiais associados a um user_id."""
    conn = get_db_connection()
    cursor = conn.cursor()
    materials = []
    try:
        cursor.execute(
            "SELECT * FROM materials WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        rows = cursor.fetchall()
        for row in rows:
            material = dict(row)
            # Deserializar campos JSON
            if material.get("source_urls"):
                material["source_urls"] = json.loads(material["source_urls"])
            if material.get("generated_content"):
                material["generated_content"] = json.loads(
                    material["generated_content"]
                )
            materials.append(material)
        return materials
    except Exception as e:
        logger.error(
            f"Erro ao listar materiais para user_id '{user_id}': {e}", exc_info=True
        )
        return []
    finally:
        conn.close()


def delete_material(user_id: str, task_id: str) -> bool:
    """Deletes a material record from the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM materials WHERE user_id = ? AND task_id = ?",
            (user_id, task_id),
        )
        conn.commit()
        if cursor.rowcount > 0:
            logger.info(
                f"Material com task_id '{task_id}' (user '{user_id}') deletado do DB."
            )
            return True
        else:
            logger.warning(
                f"Nenhum material encontrado para deletar com task_id '{task_id}' e user_id '{user_id}'."
            )
            return False
    except Exception as e:
        logger.error(
            f"Erro ao deletar material para task_id '{task_id}': {e}", exc_info=True
        )
        return False
    finally:
        conn.close()


# Inicializa o banco de dados na primeira importação
init_db()
