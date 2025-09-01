# src/database_service.py
import sqlite3
import uuid
import json
import logging
from typing import Optional, List, Dict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_FILE = "dailyBrief.db"


class AsyncDatabaseManager:
    """
    Gerenciador de contexto para conexões assíncronas do SQLite.
    Isso garante que a conexão seja sempre fechada, mesmo em caso de erro.
    """

    def __init__(self, db_file):
        self.db_file = db_file
        self.conn = None

    async def __aenter__(self):
        self.conn = sqlite3.connect(self.db_file, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        return self.conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.conn.rollback()
        else:
            self.conn.commit()
        self.conn.close()


async def get_db_connection():
    """
    Função geradora para obter a conexão com o banco de dados.
    Usada como dependência no FastAPI.
    """
    async with AsyncDatabaseManager(DB_FILE) as conn:
        yield conn


def init_db():
    """Inicializa o banco de dados SQLite com as tabelas necessárias."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()

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

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_materials (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES materials(task_id)
                )
                """
            )

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
            logger.info(
                "Banco de dados SQLite inicializado com as tabelas necessárias."
            )
    except Exception as e:
        logger.error(f"Erro ao inicializar o banco de dados: {str(e)}")
        raise


def create_automation_request(
    conn: sqlite3.Connection,
    user_id: str,
    task_id: str,
    url: str,
    theme: str,
    output_format: str,
) -> str:
    """
    Cria um registro na tabela `materials`. Recebe a conexão como parâmetro.
    """
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO materials (
                user_id,
                automation_request_id,
                task_id,
                status,
                theme,
                content_type,
                raw_material_ids,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                task_id,
                task_id,
                "PENDING",
                theme,
                output_format,
                "[]",
                datetime.now(timezone.utc).isoformat().replace("+00:00", ""),
                datetime.now(timezone.utc).isoformat().replace("+00:00", ""),
            ),
        )
        logger.info(
            f"Requisição de automação {task_id} criada com sucesso na tabela materials."
        )
        return task_id
    except sqlite3.IntegrityError as e:
        logger.error(
            f"Integrity Error ao criar requisição de automação {task_id}: {str(e)}"
        )
        raise
    except Exception as e:
        logger.error(f"Erro ao criar requisição de automação {task_id}: {str(e)}")
        raise


def save_raw_material(
    conn: sqlite3.Connection, user_id: str, task_id: str, url: str, content: str
):
    """
    Salva o conteúdo bruto de uma URL no banco de dados. Recebe a conexão como parâmetro.
    """
    raw_id = str(uuid.uuid4())
    try:
        cursor = conn.cursor()
        cursor.execute(
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
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        logger.info(f"Material bruto para task_id {task_id} salvo com id {raw_id}")
        return raw_id
    except Exception as e:
        logger.error(f"Erro ao salvar material bruto para task_id {task_id}: {str(e)}")
        raise


def update_material_with_raw_id(
    conn: sqlite3.Connection, task_id: str, raw_material_id: str
):
    """
    Atualiza a tabela materials com o ID do material bruto salvo. Recebe a conexão como parâmetro.
    """
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE materials
            SET raw_material_ids = ?
            WHERE task_id = ?
            """,
            (json.dumps([raw_material_id]), task_id),
        )
        logger.info(
            f"raw_material_id {raw_material_id} salvo na tabela materials para a task {task_id}"
        )
    except Exception as e:
        logger.error(
            f"Erro ao atualizar a tabela materials com raw_material_id: {str(e)}"
        )
        raise


def update_material_status(
    conn: sqlite3.Connection, user_id: str, task_id: str, new_status: str
):
    """
    Atualiza o status de uma tarefa específica na tabela `materials` para um determinado usuário.
    Recebe a conexão como parâmetro.
    """
    try:
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
        logger.info(
            f"Status da tarefa {task_id} do usuário {user_id} atualizado para {new_status}"
        )
    except Exception as e:
        logger.error(f"Erro ao atualizar status da tarefa {task_id}: {str(e)}")
        raise


def get_material(conn: sqlite3.Connection, user_id: str, task_id: str):
    """
    Busca os dados de um material (tarefa) no banco de dados. Recebe a conexão como parâmetro.
    """
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM materials WHERE user_id = ? AND task_id = ?
            """,
            (user_id, task_id),
        )
        material_data = cursor.fetchone()
        if material_data:
            return dict(material_data)
        return None
    except Exception as e:
        logger.error(f"Erro ao buscar material para task_id {task_id}: {str(e)}")
        return None


def get_raw_material(conn: sqlite3.Connection, raw_material_id: str):
    """
    Busca o conteúdo bruto de um material (tarefa) no banco de dados. Recebe a conexão como parâmetro.
    """
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT content FROM raw_materials WHERE id = ?
            """,
            (raw_material_id,),
        )
        raw_material_data = cursor.fetchone()
        if raw_material_data:
            return {"content": raw_material_data[0]}
        return None
    except Exception as e:
        logger.error(
            f"Erro ao buscar material bruto com ID {raw_material_id}: {str(e)}"
        )
        return None


def save_material(
    conn: sqlite3.Connection,
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
    """
    Salva ou atualiza um registro na tabela `materials`. Recebe a conexão como parâmetro.
    """
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO materials 
            (user_id, automation_request_id, task_id, status, theme, content_type, raw_material_ids, generated_content, suggested_image_prompt, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                automation_request_id,
                task_id,
                status,
                theme,
                content_type,
                json.dumps(raw_material_ids) if raw_material_ids is not None else "[]",
                generated_content,
                suggested_image_prompt,
                created_at,
                updated_at,
            ),
        )
        logger.info(f"Material salvo/atualizado para a tarefa: {task_id}")
    except Exception as e:
        logger.error(f"Erro ao salvar material: {str(e)}")
        raise


def delete_material(conn: sqlite3.Connection, user_id: str, task_id: str) -> bool:
    """
    Deleta um registro de material e seus materiais brutos associados. Recebe a conexão como parâmetro.
    """
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM materials WHERE task_id = ? AND user_id = ?",
            (task_id, user_id),
        )
        cursor.execute(
            "DELETE FROM raw_materials WHERE task_id = ? AND user_id = ?",
            (task_id, user_id),
        )
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(
            f"Erro ao deletar material para o user_id: {user_id}, task_id: {task_id}: {str(e)}"
        )
        return False


def save_selector(
    conn: sqlite3.Connection,
    user_id: str,
    url: str,
    parent_selector: Optional[str] = None,
    title_selector: Optional[str] = None,
    content_selector: Optional[str] = None,
    image_selector: Optional[str] = None,
):
    """
    Salva um seletor no banco de dados. Recebe a conexão como parâmetro.
    """
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO selectors 
            (user_id, url, parent_selector, title_selector, content_selector, image_selector)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                url,
                parent_selector,
                title_selector,
                content_selector,
                image_selector,
            ),
        )
        logger.info(f"Seletor salvo para user_id: {user_id}, url: {url}")
    except Exception as e:
        logger.error(f"Erro ao salvar seletor: {str(e)}")
        raise


def get_selectors(conn: sqlite3.Connection, user_id: str) -> List[Dict]:
    """
    Busca todos os seletores associados a um user_id. Recebe a conexão como parâmetro.
    """
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM selectors WHERE user_id = ?", (user_id,))
        selectors = [dict(row) for row in cursor.fetchall()]
        return selectors
    except Exception as e:
        logger.error(f"Erro ao buscar seletores para o user_id: {user_id}: {str(e)}")
        return []


def list_user_materials(conn: sqlite3.Connection, user_id: str) -> List[Dict]:
    """
    Lista todos os materiais associados a um user_id. Recebe a conexão como parâmetro.
    """
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM materials WHERE user_id = ?", (user_id,))
        materials = [dict(row) for row in cursor.fetchall()]
        return materials
    except Exception as e:
        logger.error(f"Erro ao listar materiais para o user_id: {user_id}: {str(e)}")
        return []


def get_raw_material_by_id(
    conn: sqlite3.Connection, raw_material_id: str
) -> Optional[Dict]:
    """
    Busca o conteúdo bruto de uma URL a partir do seu ID.
    """
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM raw_materials WHERE id = ?", (raw_material_id,))
        raw_material = cursor.fetchone()
        return dict(raw_material) if raw_material else None
    except Exception as e:
        logger.error(
            f"Erro ao buscar material bruto para o ID: {raw_material_id}: {str(e)}"
        )
        return None
