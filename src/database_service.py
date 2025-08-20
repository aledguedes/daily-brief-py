# src database_service.py
import logging
import sqlite3
from datetime import datetime, timezone
import json
import uuid
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

# Inicializa o banco de dados SQLite
DB_FILE = "database.db"


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
            raw_material_ids TEXT,  -- JSON com lista de IDs de raw_materials
            generated_content TEXT,
            suggested_image_prompt TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    # Tabela para raw_materials
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_materials (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
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
    conn.commit()
    conn.close()
    logger.info(
        "Banco de dados SQLite inicializado com tabelas materials, raw_materials e selectors."
    )


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
    """Salva ou atualiza um registro de material no SQLite."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO materials (
            user_id, automation_request_id, task_id, status, theme, content_type,
            raw_material_ids, generated_content, suggested_image_prompt, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            automation_request_id,
            task_id,
            status,
            theme,
            content_type,
            json.dumps(raw_material_ids) if raw_material_ids else None,
            generated_content,
            suggested_image_prompt,
            created_at,
            updated_at,
        ),
    )
    conn.commit()
    conn.close()


def save_raw_material(task_id: str, content: str) -> str:
    raw_id = str(uuid.uuid4())
    # Save to SQLite (example)
    with sqlite3.connect("database.db") as conn:
        conn.execute(
            "INSERT INTO raw_materials (id, task_id, content) VALUES (?, ?, ?)",
            (raw_id, task_id, content),
        )
        conn.commit()
    return raw_id


def update_material_status(user_id: str, task_id: str, status: str):
    with sqlite3.connect("database.db") as conn:
        conn.execute(
            "UPDATE materials SET status = ? WHERE user_id = ? AND task_id = ?",
            (status, user_id, task_id),
        )
        conn.commit()


def update_material_raw_material_ids(
    user_id: str, task_id: str, raw_material_ids: List[str]
):
    with sqlite3.connect("database.db") as conn:
        conn.execute(
            "UPDATE materials SET raw_material_ids = ? WHERE user_id = ? AND task_id = ?",
            (json.dumps(raw_material_ids), user_id, task_id),
        )
        conn.commit()


def get_raw_material(raw_id: str) -> Optional[str]:
    """Busca o conteúdo de um raw_material pelo ID."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT content FROM raw_materials WHERE id = ?", (raw_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return result[0]
    logger.warning(f"Raw material não encontrado para ID {raw_id}.")
    return None


def get_material(user_id: str, task_id: str) -> Optional[Dict]:
    """Busca um material pelo user_id e task_id."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM materials WHERE user_id = ? AND task_id = ?",
        (user_id, task_id),
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        columns = [desc[0] for desc in cursor.description]
        material = dict(zip(columns, row))
        if material.get("raw_material_ids"):
            material["raw_material_ids"] = json.loads(material["raw_material_ids"])
        return material
    return None


def list_user_materials(user_id: str) -> List[Dict]:
    """Lista todos os materiais de um usuário."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM materials WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    columns = [desc[0] for desc in cursor.description]
    materials = [dict(zip(columns, row)) for row in rows]
    for material in materials:
        if material.get("raw_material_ids"):
            material["raw_material_ids"] = json.loads(material["raw_material_ids"])
    return materials


def update_material_status(user_id: str, task_id: str, status: str):
    """Atualiza o status de um material."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE materials SET status = ?, updated_at = ? WHERE user_id = ? AND task_id = ?",
        (
            status,
            datetime.now(timezone.utc).isoformat().replace("+00:00", ""),
            user_id,
            task_id,
        ),
    )
    conn.commit()
    conn.close()


def update_material_raw_material_ids(
    user_id: str, task_id: str, raw_material_ids: List[str]
):
    """Atualiza a lista de raw_material_ids em um material."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE materials SET raw_material_ids = ?, updated_at = ? WHERE user_id = ? AND task_id = ?",
        (
            json.dumps(raw_material_ids),
            datetime.now(timezone.utc).isoformat().replace("+00:00", ""),
            user_id,
            task_id,
        ),
    )
    conn.commit()
    conn.close()


def delete_material(user_id: str, task_id: str) -> bool:
    """Deleta um material e seus raw_materials associados."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Deleta raw_materials associados
    cursor.execute("DELETE FROM raw_materials WHERE task_id = ?", (task_id,))
    # Deleta o material principal
    cursor.execute(
        "DELETE FROM materials WHERE user_id = ? AND task_id = ?", (user_id, task_id)
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def save_selector(
    user_id: str,
    url: str,
    parent_selector: Optional[str] = None,
    title_selector: Optional[str] = None,
    content_selector: Optional[str] = None,
    image_selector: Optional[str] = None,
):
    """Salva um seletor no banco de dados."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO selectors (user_id, url, parent_selector, title_selector, content_selector, image_selector)
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
        conn.commit()
        conn.close()
        logger.info(f"Seletor salvo para user_id {user_id}, url {url}.")
    except Exception as e:
        logger.error(f"Erro ao salvar seletor para user_id {user_id}: {str(e)}")
        raise


def get_selectors(user_id: str) -> List[Dict]:
    """Retorna a lista de seletores salvos para um usuário."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM selectors WHERE user_id = ?", (user_id,))
        rows = cursor.fetchall()
        conn.close()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        logger.error(f"Erro ao obter seletores para user_id {user_id}: {str(e)}")
        raise
