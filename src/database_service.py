# src/database_service.py
import sqlite3
import uuid
import json
import logging
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

import os

DB_FILE = os.getenv("DB_FILE", os.path.join(os.getcwd(), "dailyBrief.db"))


async def update_material_status_utility(
    user_id: str, task_id: str, new_status_name: str
):
    async with AsyncDatabaseManager(DB_FILE) as conn:
        update_material_status(conn, user_id, task_id, new_status_name)


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
        self.conn.execute("PRAGMA foreign_keys = ON")
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


def _get_initial_status_data() -> List[tuple]:
    """Retorna os dados iniciais de status com classes Tailwind (bg_class e text_class)."""
    return [
        (1, "PENDING", "Pendente (Início)", "bg-blue-100", "text-blue-700"),
        (2, "RAW_COLLECTED", "Matéria Bruta Salva", "bg-yellow-100", "text-yellow-700"),
        (3, "COLLECTION_FAILED", "Falha na Coleta", "bg-red-100", "text-red-700"),
        (4, "PENDING_GENERATION", "Aguardando IA", "bg-indigo-100", "text-indigo-700"),
        (5, "GENERATED", "Conteúdo Gerado", "bg-green-100", "text-green-700"),
        (6, "FAILED_GENERATION", "Falha na Geração", "bg-red-200", "text-red-800"),
        (7, "PENDING_IMAGE", "Aguardando Imagem", "bg-purple-100", "text-purple-700"),
        (8, "FAILED_IMAGE", "Falha na Imagem", "bg-orange-100", "text-orange-700"),
        (9, "IMAGE_GENERATED", "Imagem Gerada", "bg-teal-100", "text-teal-700"),
        (
            10,
            "PENDING_PUBLISH",
            "Aguardando Publicação",
            "bg-gray-200",
            "text-gray-800",
        ),
        (11, "PUBLISHED", "Publicado com Sucesso", "bg-green-200", "text-green-800"),
        (12, "FAILED_PUBLISH", "Falha na Publicação", "bg-red-300", "text-red-900"),
        (13, "EDITED", "Editado Manualmente", "bg-pink-100", "text-pink-700"),
        (14, "PENDING_COLLECTION", "Aguardando Coleta", "bg-blue-200", "text-blue-800"),
        (15, "COMPLETED", "Concluído", "bg-green-300", "text-green-900"),
    ]


def seed_initial_status(conn: sqlite3.Connection):
    """Popula a tabela status com os dados iniciais se eles não existirem."""
    cursor = conn.cursor()
    status_data = _get_initial_status_data()
    cursor.executemany(
        """
        INSERT OR IGNORE INTO status (id, name, display_name, bg_class, text_class)
        VALUES (?, ?, ?, ?, ?)
        """,
        status_data,
    )
    logger.info(f"Tabela 'status' populada com {len(status_data)} status.")
    conn.commit()


_STATUS_MAP = {}


def get_status_id_by_name(
    name: str, conn: Optional[sqlite3.Connection] = None
) -> Optional[int]:
    """Busca o ID do status pelo seu nome na tabela status. Usa cache para performance."""
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        close_conn = True

    try:
        if not _STATUS_MAP:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM status")
            for row in cursor.fetchall():
                _STATUS_MAP[row["name"]] = row["id"]

        return _STATUS_MAP.get(name)
    except Exception as e:
        logger.error(f"Erro ao buscar ID do status '{name}': {str(e)}")
        return None
    finally:
        if close_conn:
            conn.close()


def get_status_by_id(
    status_id: int, conn: Optional[sqlite3.Connection] = None
) -> Optional[Dict[str, Any]]:
    """Busca o objeto completo de status pelo seu ID na tabela status."""
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        close_conn = True

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, name, display_name, bg_class, text_class
            FROM status
            WHERE id = ?
            """,
            (status_id,),
        )
        result = cursor.fetchone()
        if result:
            return {
                "id": result["id"],
                "name": result["name"],
                "display_name": result["display_name"],
                "bg_class": result["bg_class"],
                "text_class": result["text_class"],
            }
        return None
    except Exception as e:
        logger.error(f"Erro ao buscar status pelo ID {status_id}: {str(e)}")
        return None
    finally:
        if close_conn:
            conn.close()


def init_db():
    """Inicializa o banco de dados SQLite com as tabelas necessárias e popula a tabela status."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")

            # Tabela status
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS status (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    bg_class TEXT,
                    text_class TEXT
                );
                """
            )

            # Tabela posts
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS posts (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    summary TEXT,
                    content TEXT NOT NULL,
                    url_fonte TEXT,
                    data_publicacao TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    tema TEXT,
                    FOREIGN KEY (task_id) REFERENCES materials(task_id)
                );
                """
            )

            # Tabela materials
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS materials (
                    user_id TEXT NOT NULL,
                    automation_request_id INTEGER,
                    task_id TEXT PRIMARY KEY,
                    status_id INTEGER NOT NULL,
                    theme TEXT,
                    content_type TEXT,
                    raw_material_ids TEXT,
                    generated_content TEXT,
                    suggested_image_prompt TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source_urls TEXT,
                    FOREIGN KEY (status_id) REFERENCES status(id)
                );
                """
            )

            # Tabela raw_materials
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
                );
                """
            )

            # Tabela selectors
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS selectors (
                    user_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    parent_selector TEXT,
                    title_selector TEXT,
                    content_selector TEXT,
                    image_selector TEXT,
                    PRIMARY KEY (user_id, url)
                );
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_configs (
                    task_id TEXT PRIMARY KEY,
                    search_factors TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES materials(task_id) ON DELETE CASCADE
                );
                    """
            )

            # Popula a tabela status
            seed_initial_status(conn)
            get_status_id_by_name("PENDING", conn)
            conn.commit()

    except Exception as e:
        logger.error(f"Erro ao inicializar o banco de dados: {str(e)}")
        raise


def save_raw_material(
    conn: sqlite3.Connection,
    user_id: str,
    task_id: str,
    url: str,
    content: str,
) -> str:
    """Salva um material bruto no banco de dados e retorna seu ID."""
    raw_material_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "")
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO raw_materials (id, user_id, task_id, url, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (raw_material_id, user_id, task_id, url, content, now),
        )
        logger.info(f"Material bruto salvo com ID: {raw_material_id}")
        return raw_material_id
    except Exception as e:
        logger.error(f"Erro ao salvar material bruto para task_id {task_id}: {str(e)}")
        raise


def save_automation_config(
    conn: sqlite3.Connection,
    task_id: str,
    search_factors: str,
) -> None:
    """Salva a configuração de busca gerada pela IA na tabela automation_configs."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "")
    try:
        cursor = conn.cursor()
        # Usar INSERT OR REPLACE para garantir que a config seja sempre atualizada/criada (idempotência)
        cursor.execute(
            """
            INSERT OR REPLACE INTO automation_configs (task_id, search_factors, created_at)
            VALUES (?, ?, ?)
            """,
            (task_id, search_factors, now),
        )
        logger.info(
            f"Configuração de automação salva/atualizada para task_id: {task_id}"
        )
    except Exception as e:
        logger.error(f"Erro ao salvar configuração para task_id {task_id}: {str(e)}")
        raise


def save_post(post: Dict[str, Any], conn: Optional[sqlite3.Connection] = None) -> None:
    """Salva um post na tabela posts."""
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        close_conn = True

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO posts (id, title, summary, content, url_fonte, data_publicacao, task_id, user_id, tema)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                post["id"],
                post["title"],
                post["summary"],
                post["content"],
                post["url_fonte"],
                post["data_publicacao"],
                post["task_id"],
                post["user_id"],
                post["tema"],
            ),
        )
        conn.commit()
        logger.info(f"Post {post['id']} salvo com sucesso.")
    except Exception as e:
        logger.error(f"Erro ao salvar post {post['id']}: {str(e)}")
        raise
    finally:
        if close_conn:
            conn.close()


def get_raw_materials_by_task_id(
    task_id: str, conn: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """Busca materiais brutos associados a um task_id."""
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        close_conn = True

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM raw_materials WHERE task_id = ?", (task_id,))
        raw_materials = cursor.fetchall()
        return [dict(row) for row in raw_materials]
    except Exception as e:
        logger.error(
            f"Erro ao buscar materiais brutos para task_id {task_id}: {str(e)}"
        )
        return []
    finally:
        if close_conn:
            conn.close()


def update_material_status(
    conn: sqlite3.Connection, user_id: str, task_id: str, new_status_name: str
):
    """Atualiza o status de um material na tabela materials."""
    try:
        cursor = conn.cursor()
        status_id = get_status_id_by_name(new_status_name, conn)
        if status_id is None:
            raise ValueError(
                f"Status '{new_status_name}' não encontrado no banco de dados."
            )

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "")
        cursor.execute(
            """
            UPDATE materials
            SET status_id = ?, updated_at = ?
            WHERE user_id = ? AND task_id = ?
            """,
            (status_id, now, user_id, task_id),
        )
        if cursor.rowcount == 0:
            logger.warning(
                f"Nenhum material encontrado para user_id: {user_id}, task_id: {task_id}"
            )
        else:
            logger.info(
                f"Status atualizado para '{new_status_name}' para task_id: {task_id}"
            )
        conn.commit()
    except Exception as e:
        logger.error(f"Erro ao atualizar status para task_id {task_id}: {str(e)}")
        raise


def save_material(
    conn: sqlite3.Connection,
    user_id: str,
    automation_request_id: Optional[Union[str, int]],
    task_id: str,
    status_id: int,
    theme: Optional[str] = None,
    content_type: Optional[str] = None,
    raw_material_ids: Optional[List[str]] = None,
    generated_content: Optional[str] = None,
    suggested_image_prompt: Optional[str] = None,
    source_urls: Optional[List[str]] = None,
):
    """Salva ou atualiza um material na tabela materials."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "")
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM materials WHERE task_id = ?",
            (task_id,),
        )
        existing_material = cursor.fetchone()

        if existing_material:
            cursor.execute(
                """
                UPDATE materials
                SET status_id = ?,
                    theme = ?,
                    content_type = ?,
                    generated_content = ?,
                    suggested_image_prompt = ?,
                    source_urls = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    status_id,
                    theme,
                    content_type,
                    generated_content,
                    suggested_image_prompt,
                    (
                        json.dumps(source_urls)
                        if source_urls
                        else existing_material["source_urls"]
                    ),
                    now,
                    task_id,
                ),
            )
            logger.info(f"Material atualizado para task_id: {task_id}")
        else:
            cursor.execute(
                """
                INSERT INTO materials (
                    user_id, automation_request_id, task_id, status_id, theme, content_type,
                    raw_material_ids, generated_content, suggested_image_prompt, source_urls,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    automation_request_id,
                    task_id,
                    status_id,
                    theme,
                    content_type,
                    "[]",
                    generated_content,
                    suggested_image_prompt,
                    json.dumps(source_urls) if source_urls else "[]",
                    now,
                    now,
                ),
            )
            logger.info(f"Novo material salvo para task_id: {task_id}")

        conn.commit()
    except Exception as e:
        logger.error(f"Erro ao salvar material para task_id {task_id}: {str(e)}")
        raise


def get_material(
    conn: sqlite3.Connection, user_id: str, task_id: str
) -> Optional[Dict[str, Any]]:
    """Busca os dados de um material (tarefa) no banco de dados."""
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


def get_material_by_task_id(
    conn: sqlite3.Connection, task_id: str
) -> Optional[Dict[str, Any]]:
    """Busca os dados de um material apenas pelo task_id, sem verificar o user_id."""
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM materials WHERE task_id = ?
            """,
            (task_id,),
        )
        material_data = cursor.fetchone()
        if material_data:
            return dict(material_data)
        return None
    except Exception as e:
        logger.error(f"Erro ao buscar material pelo task_id {task_id}: {str(e)}")
        return None


def get_automation_config(
    conn: sqlite3.Connection, task_id: str
) -> Optional[Dict[str, Any]]:
    """Busca a configuração de busca aprimorada a partir do task_id."""
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT search_factors FROM automation_configs WHERE task_id = ?
            """,
            (task_id,),
        )
        config_data = cursor.fetchone()
        if config_data:
            return dict(config_data)
        return None
    except Exception as e:
        logger.error(
            f"Erro ao buscar configuração de automação para task_id {task_id}: {str(e)}"
        )
        return None


def get_raw_material_by_id(
    conn: sqlite3.Connection, raw_material_id: str
) -> Optional[Dict]:
    """Busca o conteúdo bruto de uma URL a partir do seu ID."""
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


def get_raw_materials_by_ids(
    conn: sqlite3.Connection, raw_material_ids: List[str]
) -> List[Dict]:
    """Busca o conteúdo de vários materiais brutos a partir de uma lista de IDs."""
    if not raw_material_ids:
        return []
    try:
        placeholders = ",".join("?" for _ in raw_material_ids)
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM raw_materials WHERE id IN ({placeholders})",
            raw_material_ids,
        )
        raw_materials = cursor.fetchall()
        return [dict(row) for row in raw_materials]
    except Exception as e:
        logger.error(f"Erro ao buscar materiais brutos: {str(e)}")
        return []


def delete_material(conn: sqlite3.Connection, user_id: str, task_id: str) -> bool:
    """Deleta um registro de material e seus materiais brutos associados."""
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
    """Salva um seletor no banco de dados."""
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


def update_material_raw_material_ids(
    conn: sqlite3.Connection, task_id: str, raw_material_ids: List[str]
):
    """Atualiza a tabela materials com os IDs dos materiais brutos."""
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE materials
            SET raw_material_ids = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (
                json.dumps(raw_material_ids),
                datetime.now(timezone.utc).isoformat().replace("+00:00", ""),
                task_id,
            ),
        )
        logger.info(f"raw_material_ids atualizados para a tarefa: {task_id}")
    except Exception as e:
        logger.error(
            f"Erro ao atualizar raw_material_ids para a tarefa {task_id}: {str(e)}"
        )
        raise


def get_selectors(conn: sqlite3.Connection, user_id: str) -> List[Dict]:
    """Busca todos os seletores associados a um user_id."""
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM selectors WHERE user_id = ?", (user_id,))
        selectors = [dict(row) for row in cursor.fetchall()]
        return selectors
    except Exception as e:
        logger.error(f"Erro ao buscar seletores para o user_id: {user_id}: {str(e)}")
        return []


def list_user_materials(conn: sqlite3.Connection, user_id: str) -> List[Dict[str, Any]]:
    """Lista todos os materiais associados a um user_id."""
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM materials WHERE user_id = ? ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Erro ao listar materiais para o user_id: {user_id}: {str(e)}")
        return []


def update_automation_config_log(
    conn: sqlite3.Connection,
    task_id: str,
    urls_log: List[Dict[str, str]],
) -> None:
    """
    Busca a configuração JSON existente, adiciona/sobrescreve o log de URLs
    e salva o JSON atualizado na tabela automation_configs.
    """
    try:
        cursor = conn.cursor()

        config_row = get_automation_config(conn, task_id)
        if not config_row or not config_row.get("search_factors"):
            logger.error(f"Configuração não encontrada para o task_id {task_id}")
            return

        config_data = json.loads(config_row["search_factors"])
        config_data["collected_urls_log"] = urls_log

        updated_search_factors_json = json.dumps(config_data)

        cursor.execute(
            """
            UPDATE automation_configs
            SET search_factors = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (
                updated_search_factors_json,
                datetime.now(timezone.utc).isoformat().replace("+00:00", ""),
                task_id,
            ),
        )
        conn.commit()
        logger.info(f"Log de URLs de automação atualizado para task_id: {task_id}")
    except Exception as e:
        logger.error(f"Erro ao atualizar log de URLs para task_id {task_id}: {str(e)}")
        raise


def update_automation_config(conn, task_id, updated_json):
    query = "UPDATE automation_configs SET search_factors = ? WHERE task_id = ?"
    conn.execute(query, (updated_json, task_id))
    conn.commit()


def update_task_status(conn, task_id, status_name):
    status_id = get_status_id_by_name(status_name, conn)
    query = "UPDATE tasks SET status_id = ? WHERE task_id = ?"
    conn.execute(query, (status_id, task_id))
    conn.commit()
