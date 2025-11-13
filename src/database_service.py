# src/database_service.py
import sqlite3
import uuid
import json
import logging
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timezone
import asyncpg  # import necessário para conexões assíncronas com PostgreSQL
from src.config import Config

logger = logging.getLogger(__name__)

import os

DB_FILE = os.getenv("DB_FILE", os.path.join(os.getcwd(), "dailyBrief.db"))


async def update_material_status_utility(
    user_id: str, task_id: str, new_status_name: str
):
    async with AsyncDatabaseManager(DB_FILE) as conn:
        update_material_status(conn, user_id, task_id, new_status_name)


# SQLite remover após refatoração completa para PostgreSQL
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


# PostgreSQL - usar quando migrar completamente para PostgreSQL
class AsyncPostgresManager:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def __aenter__(self) -> asyncpg.Connection:
        if self.pool is None:
            self.pool = await asyncpg.create_pool(dsn=Config.DATABASE_URL)
        self.conn = await self.pool.acquire()
        return self.conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.pool.release(self.conn)


async def get_db_connection():
    """
    Função geradora para obter a conexão com o banco de dados.
    Usada como dependência no FastAPI.
    """
    async with AsyncDatabaseManager(DB_FILE) as conn:
        yield conn


async def get_status_id_by_name(
    name: str, conn: Optional[asyncpg.Connection] = None
) -> Optional[int]:
    close_conn = conn is None
    if close_conn:
        async with AsyncPostgresManager() as conn:
            return await get_status_id_by_name(name, conn)

    try:
        result = await conn.fetchval("SELECT id FROM tbl_status WHERE name = $1", name)
        return result
    except Exception as e:
        logger.error(f"Erro ao buscar status '{name}': {str(e)}")
        return None
    finally:
        if close_conn:
            await conn.close()


async def get_status_by_id(
    status_id: int, conn: asyncpg.Connection
) -> Optional[Dict[str, Any]]:
    row = await conn.fetchrow(
        "SELECT id, name, display_name, bg_class, text_class FROM tbl_status WHERE id = $1",
        status_id,
    )
    return dict(row) if row else None


async def save_raw_material(
    conn: asyncpg.Connection,
    user_id: str,
    task_id: str,
    url: str,
    content: str,
) -> str:
    raw_material_id = str(uuid.uuid4())
    now = datetime.now()
    await conn.execute(
        """
        INSERT INTO tbl_raw_materials (id, user_id, task_id, url, content, created_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        raw_material_id,
        user_id,
        task_id,
        url,
        content,
        now,
    )
    logger.info(f"Material bruto salvo com ID: {raw_material_id}")
    return raw_material_id


async def save_automation_config(
    conn: asyncpg.Connection,
    task_id: str,
    status_id: int,
    search_factors: Dict[str, Any],
) -> None:
    now = datetime.now(timezone.utc)
    try:
        await conn.execute(
            """
            INSERT INTO tbl_automation_configs (task_id, status_id, search_factors, created_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (task_id) DO UPDATE SET
                status_id = EXCLUDED.status_id,
                search_factors = EXCLUDED.search_factors,
                created_at = EXCLUDED.created_at
            """,
            task_id,
            status_id,
            search_factors,
            now,
        )
        logger.info(f"Configuração salva para task_id: {task_id}")
    except Exception as e:
        logger.error(f"Erro ao salvar automation_config para {task_id}: {str(e)}")
        raise


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
        cursor.execute("SELECT * FROM tbl_raw_materials WHERE task_id = ?", (task_id,))
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


async def update_material_status(
    conn: asyncpg.Connection, user_id: str, task_id: str, new_status_name: str
):
    await conn.execute("SET TIME ZONE 'UTC';")
    status_id = await get_status_id_by_name(new_status_name, conn)
    if status_id:
        now = datetime.now(timezone.utc)
        await conn.execute(
            "UPDATE tbl_materials SET status_id = $1, updated_at = $2 WHERE user_id = $3 AND task_id = $4",
            status_id,
            now,
            user_id,
            task_id,
        )


async def save_material(
    conn: asyncpg.Connection,
    user_id: str,
    automation_request_id: Optional[int],
    task_id: str,
    status_id: int,
    theme: Optional[str] = None,
    content_type: Optional[str] = None,
    suggested_image_prompt: Optional[str] = None,
    post_id: Optional[str] = None,
    source_urls: Optional[List[str]] = None,
):
    """
    Atualiza ou insere registro em `tbl_materials` com `post_id` e status.
    Não salva mais `generated_content`.
    """
    await conn.execute("SET TIME ZONE 'UTC';")
    now = datetime.now(timezone.utc)

    # Verifica se já existe
    existing = await conn.fetchrow(
        "SELECT post_id, source_urls FROM tbl_materials WHERE task_id = $1", task_id
    )

    if existing:
        # Atualiza apenas o necessário
        await conn.execute(
            """
            UPDATE tbl_materials
            SET status_id = $1, theme = $2, content_type = $3,
                suggested_image_prompt = $4, post_id = $5,
                source_urls = $6, updated_at = $7
            WHERE task_id = $8
            """,
            status_id,
            theme,
            content_type,
            suggested_image_prompt,
            post_id,
            source_urls or existing["source_urls"],
            now,
            task_id,
        )
    else:
        # Insere novo
        await conn.execute(
            """
            INSERT INTO tbl_materials (
                user_id, automation_request_id, task_id, status_id, theme, content_type,
                raw_material_ids, suggested_image_prompt, post_id, source_urls,
                created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """,
            user_id,
            automation_request_id,
            task_id,
            status_id,
            theme,
            content_type,
            [],
            suggested_image_prompt,
            post_id,
            source_urls or [],
            now,
            now,
        )


async def save_post(conn: asyncpg.Connection, post_data: Dict[str, Any]) -> str:
    """
    Salva o conteúdo gerado na tabela `tbl_post` e suas tabelas relacionadas.
    Retorna o `post_id` (UUID como string).
    """
    await conn.execute("SET TIME ZONE 'UTC';")
    post_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # 1. Inserir em tbl_post
    await conn.execute(
        """
        INSERT INTO tbl_posts (
            id, image, author, category_id, status_id, published_at, read_time,
            created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        post_id,
        post_data.get("image"),
        post_data.get("author"),
        post_data.get("category_id"),
        post_data.get("status_id", "PENDING"),
        None,
        post_data.get("readTime"),
        now,
        now,
    )

    # 2. Inserir títulos por idioma
    title = post_data.get("title", {})
    for lang, text in title.items():
        await conn.execute(
            "INSERT INTO tbl_post_title (post_id, lang, title) VALUES ($1, $2, $3)",
            post_id,
            lang.upper(),
            text,
        )

    # 3. Inserir conteúdo por idioma
    content = post_data.get("content", {})
    for lang, text in content.items():
        await conn.execute(
            "INSERT INTO tbl_post_content (post_id, lang, content) VALUES ($1, $2, $3)",
            post_id,
            lang.upper(),
            text,
        )

    # 4. Inserir excerpt por idioma
    excerpt = post_data.get("excerpt", {})
    for lang, text in excerpt.items():
        await conn.execute(
            "INSERT INTO tbl_post_excerpt (post_id, lang, excerpt) VALUES ($1, $2, $3)",
            post_id,
            lang.upper(),
            text,
        )

    # 5. Inserir metaDescription por idioma
    meta_desc = post_data.get("metaDescription", {})
    for lang, text in meta_desc.items():
        await conn.execute(
            "INSERT INTO tbl_post_meta_description (post_id, lang, meta_description) VALUES ($1, $2, $3)",
            post_id,
            lang.upper(),
            text,
        )

    # 6. Inserir affiliateLinks (se não vazio)
    affiliate_links = post_data.get("affiliateLinks", {})
    if affiliate_links:
        for lang, link in affiliate_links.items():
            await conn.execute(
                "INSERT INTO tbl_post_affiliate_link (post_id, lang, affiliate_link) VALUES ($1, $2, $3)",
                post_id,
                lang.upper(),
                link,
            )

    # 7. Inserir tags
    tags = post_data.get("tags", [])
    for tag in tags:
        await conn.execute(
            "INSERT INTO tbl_post_tags (post_id, tags) VALUES ($1, $2)", post_id, tag
        )

    return post_id


def get_material_by_task_id(
    conn: sqlite3.Connection, task_id: str
) -> Optional[Dict[str, Any]]:
    """Busca os dados de um material apenas pelo task_id, sem verificar o user_id."""
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM tbl_materials WHERE task_id = ?
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
            SELECT search_factors FROM tbl_automation_configs WHERE task_id = ?
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
        cursor.execute(
            "SELECT * FROM tbl_raw_materials WHERE id = ?", (raw_material_id,)
        )
        raw_material = cursor.fetchone()
        return dict(raw_material) if raw_material else None
    except Exception as e:
        logger.error(
            f"Erro ao buscar material bruto para o ID: {raw_material_id}: {str(e)}"
        )
        return None


def delete_material(conn: sqlite3.Connection, user_id: str, task_id: str) -> bool:
    """Deleta um registro de material e seus materiais brutos associados."""
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM tbl_materials WHERE task_id = ? AND user_id = ?",
            (task_id, user_id),
        )
        cursor.execute(
            "DELETE FROM tbl_raw_materials WHERE task_id = ? AND user_id = ?",
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


async def update_material_raw_material_ids(
    conn: asyncpg.Connection, task_id: str, raw_material_ids: List[str]
):
    now = datetime.now(timezone.utc)
    await conn.execute(
        "UPDATE tbl_materials SET raw_material_ids = $1, updated_at = $2 WHERE task_id = $3",
        raw_material_ids,
        now,
        task_id,
    )


def get_selectors(conn: sqlite3.Connection, user_id: str) -> List[Dict]:
    """Busca todos os seletores associados a um user_id."""
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tbl_selectors WHERE user_id = ?", (user_id,))
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
            SELECT * FROM tbl_materials WHERE user_id = ? ORDER BY created_at DESC
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
    e salva o JSON atualizado na tabela tbl_automation_configs.
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
            UPDATE tbl_automation_configs
            SET search_factors = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (
                updated_search_factors_json,
                datetime.now(timezone.utc).replace("+00:00", ""),
                task_id,
            ),
        )
        conn.commit()
        logger.info(f"Log de URLs de automação atualizado para task_id: {task_id}")
    except Exception as e:
        logger.error(f"Erro ao atualizar log de URLs para task_id {task_id}: {str(e)}")
        raise


async def get_automation_config(
    conn: asyncpg.Connection, task_id: str
) -> Optional[Dict[str, Any]]:
    try:
        row = await conn.fetchrow(
            "SELECT search_factors FROM tbl_automation_configs WHERE task_id = $1",
            task_id,
        )
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Erro ao buscar config para {task_id}: {str(e)}")
        return None


async def update_automation_config(
    conn: asyncpg.Connection, task_id: str, updated_data: dict
):
    await conn.execute(
        "UPDATE tbl_automation_configs SET search_factors = $1 WHERE task_id = $2",
        updated_data,
        task_id,
    )


async def update_task_status(conn: asyncpg.Connection, task_id: str, status_name: str):
    status_id = await get_status_id_by_name(status_name, conn)
    if status_id:
        await conn.execute(
            "UPDATE tbl_materials SET status_id = $1 WHERE task_id = $2",
            status_id,
            task_id,
        )


def get_automation_configs_list(
    conn: sqlite3.Connection,
    status_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Busca todas as configurações de automação, filtrando diretamente pelo status_id.
    """
    try:
        conn.row_factory = sqlite3.Row  # 🟢 Garantir que row vira dicionário
        cursor = conn.cursor()

        query = """
            SELECT 
                task_id, 
                status_id,  
                search_factors, 
                created_at 
            FROM tbl_automation_configs
        """
        params = []

        if status_id is not None:
            query += " WHERE status_id = ?"
            params.append(status_id)

        query += " ORDER BY created_at DESC"

        logger.info(f"Executando consulta: {query} com parâmetros: {params}")
        cursor.execute(query, tuple(params))

        return [dict(row) for row in cursor.fetchall()]

    except Exception as e:
        logger.error(f"Erro ao listar configurações de automação: {str(e)}")
        return []


# Exporta apenas o necessário para a rota
__all__ = [
    "AsyncPostgresManager",
    "get_status_id_by_name",
    "save_automation_config",
]


async def get_material(
    conn: asyncpg.Connection, user_id: str, task_id: str
) -> Optional[Dict[str, Any]]:
    """Busca os dados de um material (tarefa) no banco de dados."""
    try:
        row = await conn.fetchrow(
            "SELECT * FROM tbl_materials WHERE user_id = $1 AND task_id = $2",
            user_id,
            task_id,
        )
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Erro ao buscar material para task_id {task_id}: {str(e)}")
        return None


async def get_raw_materials_by_ids(
    conn: asyncpg.Connection, raw_material_ids: List[str]
) -> List[Dict]:
    """Busca o conteúdo de vários materiais brutos a partir de uma lista de IDs."""
    if not raw_material_ids:
        return []
    try:
        rows = await conn.fetch(
            "SELECT * FROM tbl_raw_materials WHERE id = ANY($1)", raw_material_ids
        )
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Erro ao buscar materiais brutos: {str(e)}")
        return []
