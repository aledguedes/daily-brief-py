# src/database_service.py
from datetime import datetime, timezone
import sqlite3
import uuid
import json
import logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

DB_FILE = "dailyBrief.db"


def init_db():
    """Inicializa o banco de dados SQLite com as tabelas necessárias."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Tabela para materiais (tarefas principais)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS materials (
            user_id TEXT NOT NULL,
            automation_request_id INTEGER,
            task_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            theme TEXT,
            content_type TEXT,
            raw_material_ids TEXT,
            generated_content TEXT,
            suggested_image_prompt TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )  # Tabela para materiais (tarefas principais)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS materials (
            user_id TEXT NOT NULL,
            automation_request_id INTEGER,
            task_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            theme TEXT,
            content_type TEXT,
            raw_material_ids TEXT,
            generated_content TEXT,
            suggested_image_prompt TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    # Tabela para raw_materials - COLUNA user_id INSERIDA AQUI
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_materials (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,  -- **NOVA COLUNA ADICIONADA**
            task_id TEXT NOT NULL,
            url TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES materials(task_id)
        )
        """
    )
    # Tabela para seletores
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS selectors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            url TEXT NOT NULL,
            parent_selector TEXT,
            title_selector TEXT,
            content_selector TEXT,
            image_selector TEXT
        )
        """
    )
    # Tabela para automation_requests
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tbl_automation_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            url TEXT NOT NULL,
            theme TEXT NOT NULL,
            output_format TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()
    logger.info(
        "Banco de dados SQLite inicializado com tabelas materials, raw_materials, selectors e tbl_automation_requests."
    )


def create_automation_request(
    user_id: str, task_id: str, url: str, theme: str, output_format: str
) -> str:
    """Cria um registro na tabela tbl_automation_requests."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO tbl_automation_requests (user_id, task_id, url, theme, output_format, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    task_id,
                    url,
                    theme,
                    output_format,
                    datetime.now(timezone.utc).isoformat().replace("+00:00", ""),
                ),
            )
            conn.commit()
            logger.info(
                f"Automation request criado para user_id: {user_id}, task_id: {task_id}"
            )
        return task_id
    except Exception as e:
        logger.error(f"Erro ao criar automation request: {str(e)}")
        raise


def save_raw_material(user_id: str, task_id: str, url: str, content: str) -> str:
    """Salva um material bruto no banco de dados."""
    raw_id = str(uuid.uuid4())
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute(
                """
                INSERT INTO raw_materials (id, user_id, task_id, url, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_id,
                    user_id,
                    task_id,
                    url,
                    content,
                    datetime.now(timezone.utc).isoformat().replace("+00:00", ""),
                ),
            )
            conn.commit()
            logger.info(
                f"Raw material salvo para user_id: {user_id}, task_id: {task_id}"
            )
        return raw_id
    except Exception as e:
        logger.error(f"Erro ao salvar raw material: {str(e)}")
        raise


# Funções existentes (mantidas)
def save_material(
    user_id: str,
    automation_request_id: Optional[int],
    task_id: str,
    status: str,
    theme: Optional[str] = None,
    content_type: Optional[str] = None,
    raw_material_ids: Optional[List[str]] = None,
    generated_content: Optional[str] = None,
    suggested_image_prompt: Optional[str] = None,
    created_at: str = datetime.now(timezone.utc).isoformat().replace("+00:00", ""),
    updated_at: str = datetime.now(timezone.utc).isoformat().replace("+00:00", ""),
):
    # Função existente (mantida)
    pass


def delete_material(user_id: str, task_id: str) -> bool:
    # Função existente (mantida)
    pass


def save_selector(
    user_id: str,
    url: str,
    parent_selector: Optional[str] = None,
    title_selector: Optional[str] = None,
    content_selector: Optional[str] = None,
    image_selector: Optional[str] = None,
):
    # Função existente (mantida)
    pass


def get_selectors(user_id: str) -> List[Dict]:
    # Função existente (mantida)
    pass


def update_material_status(user_id: str, task_id: str, new_status: str):
    """
    Atualiza o status de uma tarefa específica na tabela `materials` para um determinado usuário.

    Args:
        user_id (str): O ID do usuário associado à tarefa.
        task_id (str): O ID único da tarefa a ser atualizada.
        new_status (str): O novo status para a tarefa.
    """
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE materials
                SET status = ?, updated_at = ?
                WHERE user_id = ? AND task_id = ?
                """,
                (
                    new_status,
                    datetime.now(timezone.utc).isoformat().replace("+00:00", ""),
                    user_id,
                    task_id,
                ),
            )
            conn.commit()
            logger.info(
                f"Status da tarefa {task_id} do usuário {user_id} atualizado para {new_status}"
            )
    except Exception as e:
        logger.error(f"Erro ao atualizar status da tarefa {task_id}: {str(e)}")
        raise


def get_material(user_id: str, task_id: str):
    """
    Busca os dados de um material (tarefa) no banco de dados com base no ID da tarefa e no ID do usuário.

    Args:
        user_id (str): O ID do usuário associado à tarefa.
        task_id (str): O ID da tarefa a ser consultada.

    Returns:
        dict: Um dicionário com os dados do material ou None se não for encontrado.
    """
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT * FROM materials WHERE user_id = ? AND task_id = ?
            """,
            (user_id, task_id),
        )
        material_data = cursor.fetchone()
        if material_data:
            # Retorna os dados como um dicionário
            return dict(material_data)
        return None
    except Exception as e:
        logger.error(f"Erro ao buscar material para task_id {task_id}: {str(e)}")
        return None
    finally:
        conn.close()
