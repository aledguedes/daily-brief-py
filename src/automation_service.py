# src/automation_service.py
import asyncio
import logging
import time
from typing import Optional, List, Dict, Any
import uuid
from datetime import datetime, timezone
import json
import requests

from src.config import Config
from src.auth import Auth
from src.scraping import scrape_sources
from src.content import determine_content_type
from src.utils import save_report, save_payload_to_file, send_logs_to_backend
import src.database_service as db_service
import google.generativeai as genai

logger = logging.getLogger(__name__)


async def get_existing_posts(user_id: str) -> List[str]:
    """
    Obtém os títulos dos posts existentes para evitar duplicatas.
    """
    try:
        async with db_service.AsyncDatabaseManager(db_service.DB_FILE) as conn:
            posts = await db_service.list_user_materials(conn, user_id)
        return [post.get("theme", "") for post in posts if post.get("theme")]
    except Exception as e:
        logger.error(f"Erro ao buscar posts existentes: {str(e)}")
        return []


def send_post(payload: dict):
    """Envia o post gerado para a API configurada."""
    if Config.POST_API_URL:
        try:
            response = requests.post(
                Config.POST_API_URL, json=payload, timeout=Config.POST_TIMEOUT
            )
            response.raise_for_status()
            logger.info(f"Postagem enviada com sucesso para {Config.POST_API_URL}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro ao enviar postagem: {e}")
            raise


async def generate_content_with_gemini_service(
    prompt: str, user_id: str, task_id: str, log_payload: bool
) -> Dict[str, Any]:
    """Gera conteúdo usando o modelo Gemini."""
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        generation_config = genai.GenerationConfig(
            response_mime_type="application/json", temperature=0.7, top_p=0.95
        )
        response = await model.generate_content_async(
            prompt, generation_config=generation_config
        )

        content = response.text
        if log_payload:
            save_payload_to_file(
                task_id,
                "gemini_response_payload.json",
                {"prompt": prompt, "response": content},
            )

        gemini_response = json.loads(content)
        return gemini_response
    except json.JSONDecodeError as e:
        error_message = f"Erro ao decodificar JSON da resposta do Gemini: {e}. Resposta bruta: {content}"
        logger.error(error_message, exc_info=True)
        raise ValueError(error_message)
    except Exception as e:
        error_message = f"Erro inesperado na chamada da API do Gemini: {e}"
        logger.error(error_message, exc_info=True)
        raise RuntimeError(error_message)


async def scrape_sources_with_factors(
    search_factors: str, theme: str
) -> List[Dict[str, str]]:
    """
    Simula a execução de web scraping avançado usando os fatores de busca.
    Esta função deve ser implementada para usar os search_factors
    (deserializados de JSON) para realizar buscas mais refinadas.
    """
    logger.info(
        f"Executando scraping avançado. Tema: {theme}, Fatores: {search_factors[:50]}..."
    )

    # Lógica para deserializar e usar os fatores...
    try:
        factors = json.loads(search_factors)
        # Ex: buscar no Reddit pelos factors['keywords']
    except json.JSONDecodeError:
        logger.error("Erro ao deserializar search_factors.")

    # Retorna uma lista de materiais brutos individuais (Estrutura idêntica à de rotas de URL)
    return [
        {
            "url": "https://reddit.com/post/123",
            "content": f"Conteúdo do post 1 sobre {theme} (coletado com fatores)",
        },
        {
            "url": "https://google.com/snippet/456",
            "content": f"Snippet de busca 2 sobre {theme} (coletado com fatores)",
        },
    ]


async def process_theme(
    theme_config: Dict[str, Any],
    headers: Dict[str, str],
    existing_titles: List[str],
    user_id: str,
    task_id: str,
    output_format: Optional[str],
    return_raw_material_only: bool = False,
) -> List[Dict[str, Any]]:
    """Processa um tema: extrai material bruto e gera posts."""
    tema = theme_config.get("tema", "Desconhecido")
    content_type = theme_config.get("tipo", output_format)
    post_start_time = time.time()
    posts_for_theme = []
    logger.info(f"Processando tema '{tema}' com tipo de conteúdo: {content_type}")

    try:
        async with db_service.AsyncDatabaseManager(db_service.DB_FILE) as conn:
            # Atualiza o status para PENDING_COLLECTION
            status_id = db_service.get_status_id_by_name(
                "PENDING_COLLECTION", conn=conn
            )
            if not status_id:
                logger.error("Status PENDING_COLLECTION não encontrado.")
                raise ValueError("Status PENDING_COLLECTION não encontrado.")
            db_service.update_material_status(
                conn, user_id, task_id, "PENDING_COLLECTION"
            )

            # Obtém materiais brutos
            compiled_text, unique_source_urls = await scrape_sources(tema)
            if not compiled_text or not unique_source_urls:
                logger.warning(
                    f"Nenhum material bruto encontrado para o tema '{tema}'. Atualizando status para PENDING_GENERATION."
                )
                status_id = db_service.get_status_id_by_name(
                    "PENDING_GENERATION", conn=conn
                )
                if not status_id:
                    logger.error("Status PENDING_GENERATION não encontrado.")
                    raise ValueError("Status PENDING_GENERATION não encontrado.")
                db_service.update_material_status(
                    conn, user_id, task_id, "PENDING_GENERATION"
                )
                return posts_for_theme

            # Salva o material bruto como um único registro
            raw_material_id = db_service.save_raw_material(
                conn=conn,
                user_id=user_id,
                task_id=task_id,
                url=unique_source_urls[0],  # Usa a primeira URL como representativa
                content=compiled_text,
            )
            raw_material_ids = [raw_material_id]

            # Atualiza os raw_material_ids e source_urls na tabela materials
            db_service.update_material_raw_material_ids(
                conn=conn,
                task_id=task_id,
                raw_material_ids=raw_material_ids,
            )
            db_service.save_material(
                conn=conn,
                user_id=user_id,
                automation_request_id=None,
                task_id=task_id,
                status_id=status_id,
                theme=tema,
                content_type=content_type,
                source_urls=unique_source_urls,
            )

            # Atualiza o status para RAW_COLLECTED
            status_id = db_service.get_status_id_by_name("RAW_COLLECTED", conn=conn)
            if not status_id:
                logger.error("Status RAW_COLLECTED não encontrado.")
                raise ValueError("Status RAW_COLLECTED não encontrado.")
            db_service.update_material_status(conn, user_id, task_id, "RAW_COLLECTED")

            await send_logs_to_backend(
                {
                    "action": f"Coleta de material bruto para task_id '{task_id}' concluída.",
                    "level": "INFO",
                    "report_id": task_id,
                },
                headers,
            )

            if return_raw_material_only:
                logger.info(f"Modo 'apenas material bruto' ativado para tema '{tema}'.")
                return [raw_material_id]

            logger.info(f"Gerando conteúdo com Gemini para tema '{tema}'.")

            # Gera o post a partir do material bruto
            title = f"{tema} - Aggregated Content"
            if title in existing_titles:
                logger.info(f"Título '{title}' já existe no banco de dados. Pulando...")
                return posts_for_theme

            try:
                post_payload = {
                    "request_type": content_type,
                    "material_bruto": compiled_text,
                    "url_fonte": unique_source_urls[0] if unique_source_urls else "",
                    "tema": tema,
                    "user_id": user_id,
                    "task_id": task_id,
                    "titulo_original": title,
                }

                gemini_response = await generate_content_with_gemini_service(
                    prompt=json.dumps(post_payload),
                    user_id=user_id,
                    task_id=task_id,
                    log_payload=Config.SAVE_PROMPTS,
                )

                post_data = gemini_response.get("post")
                if not post_data:
                    raise ValueError("Resposta do Gemini não contém um 'post'.")

                content_final = post_data.get("content")
                post_title = post_data.get("title")
                post_summary = post_data.get("summary")

                post_db_payload = {
                    "id": str(uuid.uuid4()),
                    "title": post_title,
                    "summary": post_summary,
                    "content": content_final,
                    "url_fonte": unique_source_urls[0] if unique_source_urls else "",
                    "data_publicacao": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "task_id": task_id,
                    "user_id": user_id,
                    "tema": tema,
                }

                db_service.save_post(post_db_payload)

                if Config.POST_API_URL:
                    post_api_payload = {
                        "post_title": post_title,
                        "post_content": content_final,
                    }
                    send_post(post_api_payload)
                    status_name = "PUBLISHED"
                else:
                    status_name = "GENERATED"

                # Atualiza o status para GENERATED ou PUBLISHED
                status_id = db_service.get_status_id_by_name(status_name, conn=conn)
                if not status_id:
                    logger.error(f"Status {status_name} não encontrado.")
                    raise ValueError(f"Status {status_name} não encontrado.")
                db_service.update_material_status(conn, user_id, task_id, status_name)

                posts_for_theme.append(post_db_payload)
                logger.info(f"Post '{post_title}' gerado e salvo.")

            except (ValueError, RuntimeError) as e:
                logger.error(
                    f"Erro ao gerar conteúdo para tema '{tema}': {str(e)}",
                    exc_info=True,
                )
                await send_logs_to_backend(
                    {
                        "action": f"Falha na geração de conteúdo para tema '{tema}'. Erro: {str(e)}",
                        "level": "ERROR",
                        "report_id": task_id,
                    },
                    headers,
                )
                status_id = db_service.get_status_id_by_name(
                    "FAILED_GENERATION", conn=conn
                )
                if not status_id:
                    logger.error("Status FAILED_GENERATION não encontrado.")
                    raise ValueError("Status FAILED_GENERATION não encontrado.")
                db_service.update_material_status(
                    conn, user_id, task_id, "FAILED_GENERATION"
                )

    except Exception as e:
        logger.error(f"Erro ao processar o tema '{tema}': {str(e)}", exc_info=True)
        async with db_service.AsyncDatabaseManager(db_service.DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name("FAILED_GENERATION", conn=conn)
            if not status_id:
                logger.error("Status FAILED_GENERATION não encontrado.")
                raise ValueError("Status FAILED_GENERATION não encontrado.")
            db_service.update_material_status(
                conn, user_id, task_id, "FAILED_GENERATION"
            )
        await send_logs_to_backend(
            {
                "action": f"Falha na automação para o tema '{tema}'. Erro: {str(e)}",
                "level": "CRITICAL",
                "report_id": task_id,
            },
            headers,
        )

    return posts_for_theme


async def process_intelligent_collection(
    task_id: str,
    user_id: str,
    theme: str,
    content_type: str,
    search_factors: str,
    auth_headers: Dict[str, str],
) -> Dict[str, Any]:
    """
    Processa a coleta de material bruto utilizando os search_factors inteligentes.
    Reutiliza a lógica de salvamento de material bruto por URL.
    """
    logger.info(f"Task {task_id}: Iniciando coleta inteligente com search_factors.")

    try:
        # 1. Executa a Coleta Inteligente (usando a nova função)
        collected_materials = await scrape_sources_with_factors(search_factors, theme)

        if not collected_materials:
            logger.warning(
                f"Nenhum material bruto encontrado com fatores de busca para tema '{theme}'."
            )
            async with db_service.AsyncDatabaseManager(db_service.DB_FILE) as conn:
                db_service.update_material_status(
                    conn, user_id, task_id, "FAILED_GENERATION"
                )
            return {
                "task_id": task_id,
                "status": "NO_RAW_MATERIAL",
                "message": "Nenhum material bruto encontrado.",
            }

        raw_material_ids = []
        source_urls = []

        # 2. Persistência Unificada (Reutilização de Lógica de URL)
        async with db_service.AsyncDatabaseManager(db_service.DB_FILE) as conn:
            for material in collected_materials:
                # REUTILIZAÇÃO da função save_raw_material (existente e usada por rotas de URL)
                raw_id = db_service.save_raw_material(
                    conn=conn,
                    user_id=user_id,
                    task_id=task_id,
                    url=material["url"],
                    content=material["content"],
                )
                raw_material_ids.append(raw_id)
                source_urls.append(material["url"])

            # 3. Atualiza a tabela 'materials' com os IDs e URLs coletados
            db_service.update_material_raw_material_ids(  # Função implícita no DB Service
                conn=conn,
                task_id=task_id,
                raw_material_ids=raw_material_ids,
            )

            # Atualiza o status e metadados na tabela 'materials'
            db_service.save_material(  # Reutiliza save_material para atualizar metadados (source_urls, status, etc.)
                conn=conn,
                user_id=user_id,
                automation_request_id=None,
                task_id=task_id,
                status_id=db_service.get_status_id_by_name("RAW_COLLECTED", conn=conn),
                theme=theme,
                content_type=content_type,
                source_urls=source_urls,
            )

        await send_logs_to_backend(
            {
                "action": f"Coleta inteligente concluída. {len(raw_material_ids)} materiais brutos salvos.",
                "level": "INFO",
                "report_id": task_id,
            },
            auth_headers,
        )

        return {
            "task_id": task_id,
            "status": "RAW_COLLECTED",
            "message": "Coleta inteligente concluída.",
        }

    except Exception as e:
        logger.error(
            f"Erro na coleta inteligente para task_id {task_id}: {str(e)}",
            exc_info=True,
        )
        async with db_service.AsyncDatabaseManager(db_service.DB_FILE) as conn:
            db_service.update_material_status(
                conn, user_id, task_id, "COLLECTION_FAILED"  # Atualizar status
            )
        raise


async def run_automation(
    output_format: Optional[str] = None,
    theme: Optional[str] = None,
    auth_headers: Optional[Dict[str, str]] = None,
    user_id: str = "anonymous",
    task_id: Optional[str] = None,
    return_raw_material_only: bool = False,
    search_factors: Optional[str] = None,
) -> Dict[str, Any]:
    """Função principal da automação: orquestra a busca, salvamento de materiais brutos e geração de posts."""
    start_time = time.time()
    task_id = task_id or str(uuid.uuid4())
    logger.info(
        f"Iniciando automação com task_id: {task_id}, tema: '{theme}', retorno_material_bruto_apenas: {return_raw_material_only}"
    )

    if Config.LOGS_API_URL and not return_raw_material_only:
        await send_logs_to_backend(
            {
                "action": "Iniciando processo de automação de conteúdo.",
                "level": "INFO",
                "report_id": task_id,
            },
            auth_headers,
        )

    try:
        auth_instance = Auth()
        user_id = (
            user_id
            if user_id and user_id != "anonymous"
            else auth_instance.get_user_id()
        )
        existing_titles = (
            get_existing_posts(user_id) if not return_raw_material_only else []
        )
        themes = (
            Config.THEMES
            if theme is None
            else [t for t in Config.THEMES if t.get("tema") == theme]
        )

        if not themes:
            logger.error(f"Nenhum tema encontrado para o termo de pesquisa '{theme}'.")
            async with db_service.AsyncDatabaseManager(db_service.DB_FILE) as conn:
                status_id = db_service.get_status_id_by_name(
                    "FAILED_GENERATION", conn=conn
                )
                if not status_id:
                    logger.error("Status FAILED_GENERATION não encontrado.")
                    raise ValueError("Status FAILED_GENERATION não encontrado.")
                db_service.save_material(
                    conn=conn,
                    user_id=user_id,
                    automation_request_id=None,
                    task_id=task_id,
                    status_id=status_id,
                    theme=theme,
                    content_type=output_format,
                )
                status = db_service.get_status_by_id(status_id, conn=conn)
                if not status:
                    logger.error(f"Status ID {status_id} não encontrado.")
                    raise ValueError(f"Status ID {status_id} não encontrado.")
            await send_logs_to_backend(
                {
                    "action": f"Nenhum tema encontrado para o termo de pesquisa '{theme}'.",
                    "level": "ERROR",
                    "report_id": task_id,
                },
                auth_headers,
            )
            return {
                "task_id": task_id,
                "status": status,
                "message": "Nenhum tema encontrado.",
            }

        async with db_service.AsyncDatabaseManager(db_service.DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name("PENDING", conn=conn)
            if not status_id:
                logger.error("Status PENDING não encontrado.")
                raise ValueError("Status PENDING não encontrado.")
            db_service.save_material(
                conn=conn,
                user_id=user_id,
                automation_request_id=None,
                task_id=task_id,
                status_id=status_id,
                theme=theme,
                content_type=output_format,
            )

        for theme_config in themes:
            await process_theme(
                theme_config,
                auth_headers,
                existing_titles,
                user_id,
                task_id,
                output_format,
                return_raw_material_only,
            )

        if return_raw_material_only:
            end_time = time.time()
            duration = end_time - start_time
            logger.info(
                f"Coleta de material bruto para task_id: '{task_id}' finalizada em {duration:.2f} segundos."
            )
            async with db_service.AsyncDatabaseManager(db_service.DB_FILE) as conn:
                status_id = db_service.get_status_id_by_name("RAW_COLLECTED", conn=conn)
                if not status_id:
                    logger.error("Status RAW_COLLECTED não encontrado.")
                    raise ValueError("Status RAW_COLLECTED não encontrado.")
                db_service.update_material_status(
                    conn, user_id, task_id, "RAW_COLLECTED"
                )
                status = db_service.get_status_by_id(status_id, conn=conn)
                if not status:
                    logger.error(f"Status ID {status_id} não encontrado.")
                    raise ValueError(f"Status ID {status_id} não encontrado.")
            return {"task_id": task_id, "status": status}

        end_time = time.time()
        duration = end_time - start_time
        logger.info(
            f"Execução completa da automação para task_id: '{task_id}' finalizada em {duration:.2f} segundos."
        )

        if search_factors and return_raw_material_only:
            logger.info(
                f"Modo 'Intelligent Collection' ativado para task_id: {task_id}."
            )
            return await process_intelligent_collection(
                task_id=task_id,
                user_id=user_id,
                theme=theme,
                content_type=output_format,  # content_type foi passado como output_format do trigger-by-text
                search_factors=search_factors,
                auth_headers=auth_headers,
            )

        final_status_name = "PUBLISHED" if Config.POST_API_URL else "GENERATED"
        async with db_service.AsyncDatabaseManager(db_service.DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name(final_status_name, conn=conn)
            if not status_id:
                logger.error(f"Status {final_status_name} não encontrado.")
                raise ValueError(f"Status {final_status_name} não encontrado.")
            db_service.update_material_status(conn, user_id, task_id, final_status_name)
            status = db_service.get_status_by_id(status_id, conn=conn)
            if not status:
                logger.error(f"Status ID {status_id} não encontrado.")
                raise ValueError(f"Status ID {status_id} não encontrado.")

        if Config.LOGS_API_URL:
            await send_logs_to_backend(
                {
                    "action": "Processo de automação de conteúdo concluído com sucesso.",
                    "level": "INFO",
                    "report_id": task_id,
                },
                auth_headers,
            )

        await save_report(task_id, user_id)
        return {"task_id": task_id, "status": status}

    except Exception as e:
        logger.error(
            f"Erro fatal na função run_automation para task_id '{task_id}': {str(e)}",
            exc_info=True,
        )
        async with db_service.AsyncDatabaseManager(db_service.DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name("FAILED_GENERATION", conn=conn)
            if not status_id:
                logger.error("Status FAILED_GENERATION não encontrado.")
                raise ValueError("Status FAILED_GENERATION não encontrado.")
            db_service.save_material(
                conn=conn,
                user_id=user_id,
                automation_request_id=None,
                task_id=task_id,
                status_id=status_id,
                theme=theme,
                content_type=output_format,
            )
            status = db_service.get_status_by_id(status_id, conn=conn)
            if not status:
                logger.error(f"Status ID {status_id} não encontrado.")
                raise ValueError(f"Status ID {status_id} não encontrado.")
        if Config.LOGS_API_URL:
            await send_logs_to_backend(
                {
                    "action": f"Erro fatal na automação. Erro: {str(e)}",
                    "level": "CRITICAL",
                    "report_id": task_id,
                },
                auth_headers,
            )
        return {"task_id": task_id, "status": status, "message": str(e)}


async def process_material_task(
    user: dict,
    automation_request_id: Optional[str],
    task_id: str,
):
    """Processa o material bruto e gera o conteúdo final."""
    try:
        db_user_id = user["id"]
        headers = {"Authorization": f"Bearer {user['token']}"}
        logger.info(
            f"Processando task de material bruto para user_id: {db_user_id}, task_id: {task_id}"
        )

        async with db_service.AsyncDatabaseManager(db_service.DB_FILE) as conn:
            raw_materials = db_service.get_raw_materials_by_task_id(task_id, conn=conn)
            if not raw_materials:
                logger.warning(
                    f"Nenhum material bruto encontrado para a task_id: {task_id}. Atualizando status para FAILED_GENERATION."
                )
                status_id = db_service.get_status_id_by_name(
                    "FAILED_GENERATION", conn=conn
                )
                if not status_id:
                    logger.error("Status FAILED_GENERATION não encontrado.")
                    raise ValueError("Status FAILED_GENERATION não encontrado.")
                db_service.update_material_status(
                    conn, db_user_id, task_id, "FAILED_GENERATION"
                )
                return

            material = raw_materials[0]  # Assume um único material bruto
            existing_titles = get_existing_posts(db_user_id)
            title = f"{material.get('theme', 'Desconhecido')} - Aggregated Content"
            if title in existing_titles:
                logger.info(f"Título '{title}' já existe no banco de dados. Pulando...")
                return

            try:
                post_payload = {
                    "request_type": "article",
                    "material_bruto": material["content"],
                    "url_fonte": material.get("url", ""),
                    "tema": material.get("theme", "Desconhecido"),
                    "user_id": db_user_id,
                    "task_id": task_id,
                    "titulo_original": title,
                }

                gemini_response = await generate_content_with_gemini_service(
                    prompt=json.dumps(post_payload),
                    user_id=db_user_id,
                    task_id=task_id,
                    log_payload=Config.SAVE_PROMPTS,
                )

                post_data = gemini_response.get("post")
                if not post_data:
                    raise ValueError("Resposta do Gemini não contém um 'post'.")

                content_final = post_data.get("content")
                post_title = post_data.get("title")
                post_summary = post_data.get("summary")

                post_db_payload = {
                    "id": str(uuid.uuid4()),
                    "title": post_title,
                    "summary": post_summary,
                    "content": content_final,
                    "url_fonte": material.get("url", ""),
                    "data_publicacao": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "task_id": task_id,
                    "user_id": db_user_id,
                    "tema": material.get("theme", "Desconhecido"),
                }

                db_service.save_post(post_db_payload)

                if Config.POST_API_URL:
                    post_api_payload = {
                        "post_title": post_title,
                        "post_content": content_final,
                    }
                    send_post(post_api_payload)
                    current_status = "PUBLISHED"
                else:
                    current_status = "GENERATED"

                status_id = db_service.get_status_id_by_name(current_status, conn=conn)
                if not status_id:
                    logger.error(f"Status {current_status} não encontrado.")
                    raise ValueError(f"Status {current_status} não encontrado.")
                db_service.update_material_status(
                    conn, db_user_id, task_id, current_status
                )

                logger.info(
                    f"Post '{post_title}' gerado e salvo para task_id '{task_id}'."
                )

            except (ValueError, RuntimeError) as e:
                logger.error(
                    f"Erro ao gerar conteúdo para task_id '{task_id}': {str(e)}",
                    exc_info=True,
                )
                status_id = db_service.get_status_id_by_name(
                    "FAILED_GENERATION", conn=conn
                )
                if not status_id:
                    logger.error("Status FAILED_GENERATION não encontrado.")
                    raise ValueError("Status FAILED_GENERATION não encontrado.")
                db_service.update_material_status(
                    conn, db_user_id, task_id, "FAILED_GENERATION"
                )
                await send_logs_to_backend(
                    {
                        "action": f"Falha na geração de conteúdo para task '{task_id}'. Erro: {str(e)}",
                        "level": "ERROR",
                        "report_id": task_id,
                    },
                    headers,
                )

    except Exception as e:
        logger.error(
            f"Erro inesperado no processamento da task '{task_id}': {str(e)}",
            exc_info=True,
        )
        async with db_service.AsyncDatabaseManager(db_service.DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name("FAILED_GENERATION", conn=conn)
            if not status_id:
                logger.error("Status FAILED_GENERATION não encontrado.")
                raise ValueError("Status FAILED_GENERATION não encontrado.")
            db_service.update_material_status(
                conn, db_user_id, task_id, "FAILED_GENERATION"
            )
        await send_logs_to_backend(
            {
                "action": f"Erro inesperado no processamento da task '{task_id}'. Erro: {str(e)}",
                "level": "CRITICAL",
                "report_id": task_id,
            },
            headers,
        )
