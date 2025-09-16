# src/automation_service.py
import asyncio
import logging
import os
import time
from typing import Optional
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
import src.postgresql_service as pg_service
import google.generativeai as genai

logger = logging.getLogger(__name__)


# Funções movidas de src/api.py
def get_existing_posts(user_id: str):
    try:
        if Config.USE_POSTGRES:
            posts = pg_service.get_all_posts(user_id)
        else:
            posts = db_service.get_all_posts(user_id)
        return [post["title"] for post in posts]
    except Exception as e:
        logger.error(f"Erro ao buscar posts existentes: {str(e)}")
        return []


def send_post(payload: dict):
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
):
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


# Funções movidas de src/main.py
async def process_theme(
    theme_config,
    headers,
    existing_titles,
    user_id,
    task_id,
    output_format,
    return_raw_material_only=False,
):
    """
    Processa um tema: extrai material bruto e salva em raw_materials (se return_raw_material_only=True)
    ou delega geração completa para process_material_task.
    """
    tema = theme_config.get("tema", "Desconhecido")
    content_type = theme_config.get("tipo", output_format)
    post_start_time = time.time()
    posts_for_theme = []
    logger.info(f"Processando tema '{tema}' com tipo de conteúdo: {content_type}")

    raw_material_count = 0
    try:
        raw_materials = await scrape_sources(theme_config)
        raw_material_count = len(raw_materials)
        if not raw_materials:
            logger.warning(
                f"Nenhum material bruto encontrado para o tema '{tema}'. Pulando geração de conteúdo."
            )
            return posts_for_theme

        if return_raw_material_only:
            logger.info(
                f"Modo 'apenas material bruto' ativado. Salvando {len(raw_materials)} materiais."
            )
            if Config.USE_POSTGRES:
                pg_service.save_raw_materials(task_id, user_id, raw_materials)
            else:
                db_service.save_raw_materials(task_id, user_id, raw_materials)

            await send_logs_to_backend(
                {
                    "action": f"Coleta de {len(raw_materials)} materiais brutos para task_id '{task_id}' concluída.",
                    "level": "INFO",
                    "report_id": task_id,
                },
                headers,
            )
            return raw_material_count

        if Config.USE_POSTGRES:
            pg_service.save_raw_materials(task_id, user_id, raw_materials)
        else:
            db_service.save_raw_materials(task_id, user_id, raw_materials)

        logger.info(
            f"Material bruto salvo. Gerando conteúdo com Gemini para {len(raw_materials)} itens."
        )

        for material in raw_materials:
            title_parts = material.get("title", "").split(" - ")
            source_title = title_parts[-1] if len(title_parts) > 1 else "Unknown"
            title = title_parts[0] if title_parts else "No Title"

            if title in existing_titles:
                logger.info(f"Título '{title}' já existe no banco de dados. Pulando...")
                continue

            try:
                post_payload = {
                    "request_type": content_type,
                    "material_bruto": material["content"],
                    "url_fonte": material.get("url"),
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
                    "url_fonte": material.get("url"),
                    "data_publicacao": datetime.now(timezone.utc),
                    "task_id": task_id,
                    "user_id": user_id,
                    "tema": tema,
                }

                if Config.USE_POSTGRES:
                    pg_service.save_post(post_db_payload)
                else:
                    db_service.save_post(post_db_payload)

                if Config.POST_API_URL:
                    post_api_payload = {
                        "post_title": post_title,
                        "post_content": content_final,
                    }
                    send_post(post_api_payload)

                posts_for_theme.append(post_db_payload)
                logger.info(f"Post '{post_title}' gerado e salvo.")

            except (ValueError, RuntimeError) as e:
                logger.error(
                    f"Erro ao processar material '{title}': {str(e)}", exc_info=True
                )
                await send_logs_to_backend(
                    {
                        "action": f"Falha na geração de conteúdo para URL {material.get('url')}. Erro: {str(e)}",
                        "level": "ERROR",
                        "report_id": task_id,
                    },
                    headers,
                )
                continue
            except Exception as e:
                logger.error(
                    f"Erro inesperado ao processar material '{title}': {str(e)}",
                    exc_info=True,
                )
                await send_logs_to_backend(
                    {
                        "action": f"Falha inesperada ao processar material para URL {material.get('url')}. Erro: {str(e)}",
                        "level": "CRITICAL",
                        "report_id": task_id,
                    },
                    headers,
                )
                continue

    except Exception as e:
        logger.error(f"Erro ao processar o tema '{tema}': {str(e)}", exc_info=True)
        await send_logs_to_backend(
            {
                "action": f"Falha na automação para o tema '{tema}'. Erro: {str(e)}",
                "level": "CRITICAL",
                "report_id": task_id,
            },
            headers,
        )

    return posts_for_theme


async def run_automation(
    output_format=None,
    theme=None,
    auth_headers=None,
    user_id: str = "anonymous",
    task_id: Optional[str] = None,
    return_raw_material_only: bool = False,
):
    """
    Função principal da automação: orquestra a busca, salvamento de materiais brutos e geração de posts.
    """
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
                "status": "FAILED",
                "message": "Nenhum tema encontrado.",
            }

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
            return {"task_id": task_id, "status": "COLLECTION_COMPLETED"}

        end_time = time.time()
        duration = end_time - start_time
        logger.info(
            f"Execução completa da automação para task_id: '{task_id}' finalizada em {duration:.2f} segundos."
        )

        if Config.LOGS_API_URL:
            await send_logs_to_backend(
                {
                    "action": "Processo de automação de conteúdo concluído com sucesso.",
                    "level": "INFO",
                    "report_id": task_id,
                },
                auth_headers,
            )

        save_report(task_id, user_id)
        return {"task_id": task_id, "status": "COMPLETED"}

    except Exception as e:
        logger.error(
            f"Erro fatal na função run_automation para task_id '{task_id}': {str(e)}",
            exc_info=True,
        )
        if Config.LOGS_API_URL:
            await send_logs_to_backend(
                {
                    "action": f"Erro fatal na automação. Erro: {str(e)}",
                    "level": "CRITICAL",
                    "report_id": task_id,
                },
                auth_headers,
            )
        return {"task_id": task_id, "status": "FAILED", "message": str(e)}


async def process_material_task(
    user: dict,
    automation_request_id: Optional[str],
    task_id: str,
):
    """
    Processa o material bruto e gera o conteúdo final.
    """
    try:
        db_user_id = user["id"]
        headers = {"Authorization": f"Bearer {user['token']}"}
        logger.info(
            f"Processando task de material bruto para user_id: {db_user_id}, task_id: {task_id}"
        )

        raw_materials = (
            pg_service.get_raw_materials_by_task_id(task_id)
            if Config.USE_POSTGRES
            else db_service.get_raw_materials_by_task_id(task_id)
        )

        if not raw_materials:
            logger.warning(
                f"Nenhum material bruto encontrado para a task_id: {task_id}. Pulando geração de conteúdo."
            )
            return

        existing_titles = get_existing_posts(db_user_id)

        for material in raw_materials:
            title_parts = material.get("title", "").split(" - ")
            title = title_parts[0] if title_parts else "No Title"

            if title in existing_titles:
                logger.info(f"Título '{title}' já existe no banco de dados. Pulando...")
                continue

            try:
                # O payload do Gemini é construído aqui com base no material bruto
                post_payload = {
                    "request_type": "article",  # Assume 'article' se não especificado
                    "material_bruto": material["content"],
                    "url_fonte": material.get("url"),
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
                    "url_fonte": material.get("url"),
                    "data_publicacao": datetime.now(timezone.utc),
                    "task_id": task_id,
                    "user_id": db_user_id,
                    "tema": material.get("theme", "Desconhecido"),
                }

                if Config.USE_POSTGRES:
                    pg_service.save_post(post_db_payload)
                else:
                    db_service.save_post(post_db_payload)

                if Config.POST_API_URL:
                    post_api_payload = {
                        "post_title": post_title,
                        "post_content": content_final,
                    }
                    send_post(post_api_payload)

                logger.info(
                    f"Post '{post_title}' gerado e salvo para task_id '{task_id}'."
                )

            except (ValueError, RuntimeError) as e:
                logger.error(
                    f"Erro ao gerar conteúdo para task_id '{task_id}': {str(e)}",
                    exc_info=True,
                )
                send_logs_to_backend(
                    {
                        "action": f"Falha na geração de conteúdo para task '{task_id}'. Erro: {str(e)}",
                        "level": "ERROR",
                        "report_id": task_id,
                    },
                    headers,
                )
                continue
    except Exception as e:
        logger.error(
            f"Erro inesperado no processamento da task '{task_id}': {str(e)}",
            exc_info=True,
        )
        send_logs_to_backend(
            {
                "action": f"Erro inesperado no processamento da task '{task_id}'. Erro: {str(e)}",
                "level": "CRITICAL",
                "report_id": task_id,
            },
            headers,
        )
