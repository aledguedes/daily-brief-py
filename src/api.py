# src/api.py
import sqlite3
import requests
import logging
import json
import re
import uuid
from datetime import datetime, timezone
import asyncio

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl, Field, ValidationError
from typing import List, Optional, Dict, Any, Union

from sqlalchemy.orm import Session

from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from dotenv import load_dotenv
import os

from src.config import Config
from src.auth import Auth
import src.database_service as db_service
from src.database_service import (
    AsyncDatabaseManager,
    DB_FILE,
    get_db_connection,
    save_automation_config,
    get_status_id_by_name,
    save_material,
)

from src.scraping_service import (
    fetch_url_content, 
    clean_html_content,
    save_selector_data,
    extract_content_by_selectors,
)
from src.database import get_db
from src.models import AutomationRequest
from src.utils import send_logs_to_backend
from src.automation_service import run_automation
from src.providers import get_provider_instance
from src.content import build_generation_prompt, build_image_prompt
from src.schemas import RESPONSE_SCHEMA_V1

logger = logging.getLogger(__name__)

router = APIRouter()

AI_RESPONSE_SCHEMA_CONFIG = {
    "type": "object",
    "properties": {
        "theme": {
            "type": "string",
            "description": "O tema central e conciso do textContent.",
        },
        "search_factors": {
            "type": "string",
            "description": 'Objeto JSON serializado com os termos e configurações de busca refinados (ex: \'{"keywords": ["termo1", "termo2"]}\')',
        },
    },
    "required": ["theme", "search_factors"],
}


class RawContentResponse(BaseModel):
    raw_content: str


class RawMaterialsListResponse(BaseModel):
    raw_materials: List[Dict[str, Any]]


class StatusResponse(BaseModel):
    id: int
    name: str
    display_name: str
    bg_class: str
    text_class: str


class MaterialResponse(BaseModel):
    user_id: str
    automation_request_id: Optional[Union[int, str]] = None
    task_id: str
    status: StatusResponse
    theme: Optional[str] = None
    content_type: Optional[str] = None
    raw_material_ids: Optional[List[str]] = None
    generated_content: Optional[Union[str, Dict[str, Any]]] = None
    suggested_image_prompt: Optional[str] = None
    created_at: str
    updated_at: str


class TriggerResponse(BaseModel):
    trigger_id: Optional[str] = None
    message: str
    task_id: str
    status: StatusResponse


# Modelos Pydantic
class UrlPayload(BaseModel):
    url: HttpUrl = Field(..., description="URL para extrair o conteúdo.")


class GenerationRequest(BaseModel):
    raw_material: str = Field(
        ..., description="O material bruto a ser usado para a geração de conteúdo."
    )
    theme: str = Field(..., description="O tema principal do conteúdo.")
    content_type: str = Field(..., description="O tipo de conteúdo a ser gerado.")


class SelectorData(BaseModel):
    url: HttpUrl
    parent_selector: Optional[str] = None
    title_selector: Optional[str] = None
    content_selector: Optional[str] = None
    image_selector: Optional[str] = None


class GenerateContentManualRequest(BaseModel):
    user_id: str
    task_id: str
    raw_material: str
    theme: str
    content_type: str
    keywords: Optional[List[str]] = None
    tone: Optional[str] = None
    cta_instruction: Optional[str] = None
    article_structure: Optional[List[str]] = None
    audience: Optional[str] = None
    ideal_article_example: Optional[str] = None
    provider: str = Field(
        "gemini", description="O provedor de IA a ser usado (ex: 'gemini', 'deepseek')."
    )


class SubmitFinalPostRequest(BaseModel):
    task_id_to_delete: Optional[str] = None
    post_data: Dict[str, Any]


class ImageGenerationRequest(BaseModel):
    prompt: str
    provider: str = Field(
        "gemini", description="O provedor de IA a ser usado para gerar a imagem."
    )


class ImageGenerationResponse(BaseModel):
    image_base64: str


class AutomationRequestDetails(BaseModel):
    id: int
    outputFormat: str
    theme: str
    createdAt: datetime
    updatedAt: datetime


class TriggerRequest(BaseModel):
    theme: Optional[str] = Field(None, alias="theme")
    output_format: Optional[str] = Field(None, alias="outputFormat")
    provider: str = Field(
        "gemini", description="O provedor de IA a ser usado (ex: 'gemini', 'deepseek')."
    )

    class Config:
        allow_population_by_field_name = True


class UrlRequest(BaseModel):
    url: HttpUrl = Field(..., description="A URL da qual o conteúdo será extraído.")


class ExtractFromUrlsRequest(BaseModel):
    urls: List[HttpUrl] = Field(
        ..., description="Lista de URLs para extrair o conteúdo."
    )

    class Config:
        allow_population_by_field_name = True


class GenerateContentRequest(BaseModel):
    task_id: str = Field(
        ..., alias="taskId", description="ID da task com materiais brutos salvos."
    )
    theme: str = Field(..., description="O tema principal do conteúdo.")
    output_format: str = Field(
        ..., alias="outputFormat", description="O tipo de conteúdo a ser gerado."
    )
    provider: str = Field(
        "gemini", description="O provedor de IA a ser usado (ex: 'gemini', 'deepseek')."
    )

    class Config:
        allow_population_by_field_name = True


class RawMaterialRequest(BaseModel):
    ids: List[str]


class UpdateRawMaterialRequest(BaseModel):
    content: str
    user_id: str


class TriggerByTextRequest(BaseModel):
    textContent: str = Field(
        ...,
        max_length=10000,
        description="Material bruto para análise (máx. 10.000 caracteres).",
    )
    content_type: str = Field(
        ..., description="O tipo de conteúdo que será gerado (ex: 'article', 'tweet')."
    )
    provider: str = Field("openai", description="O provedor de IA a ser usado.")


class TriggerByTextResponse(BaseModel):
    task_id: str
    theme: str
    status: str
    message: str = (
        "Configuração de busca gerada e tarefa iniciada. Use o task_id para monitorar e acionar a coleta."
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(2),
    retry=retry_if_exception_type((Exception)),
)
async def get_existing_posts(headers: dict) -> list:
    """Obtém posts existentes do backend para evitar duplicatas."""
    try:
        response = requests.get(
            f"{Config.SPRING_BOOT_API_URL}/posts",
            headers=headers,
            timeout=Config.REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        posts = response.json()
        logger.info(f"Obtidos {len(posts)} posts existentes do backend.")
        return posts
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao buscar posts existentes: {str(e)}")
        raise


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(2),
    retry=retry_if_exception_type((Exception)),
)
async def send_post(post_data: dict, headers: dict):
    """Envia o post gerado para o backend."""
    try:
        response = requests.post(
            f"{Config.SPRING_BOOT_API_URL}/posts",
            json=post_data,
            headers=headers,
            timeout=Config.REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        logger.info("Post enviado com sucesso para o backend.")
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao enviar post para o backend: {str(e)}")
        raise


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(2),
    retry=retry_if_exception_type((Exception)),
)
async def generate_content_with_provider_service(
    provider_name: str, theme: str, raw_material: str, content_type: str
) -> dict:
    """Gera conteúdo (texto) e um prompt de imagem usando o provedor de IA dinâmico."""
    logger.info(f"Iniciando geração de conteúdo com provedor: {provider_name}")

    IMAGE_PROMPT_SCHEMA = {
        "type": "object",
        "properties": {
            "image_prompt": {
                "type": "string",
                "description": "O prompt de imagem em inglês, pronto para um gerador de imagens.",
            }
        },
        "required": ["image_prompt"],
    }

    try:
        content_provider = get_provider_instance(provider_name)
        content_prompt = build_generation_prompt(
            theme=theme, raw_material=raw_material, content_type=content_type
        )
        generated_content_data = await content_provider.generate_content(
            prompt=content_prompt, response_schema=RESPONSE_SCHEMA_V1
        )

        image_provider_name = Config.IMAGE_PROMPT_PROVIDER or provider_name
        image_provider = get_provider_instance(image_provider_name)
        image_prompt_request = build_image_prompt(
            generated_content=generated_content_data, content_type=content_type
        )
        image_prompt_result_json = await image_provider.generate_content(
            prompt=image_prompt_request, response_schema=IMAGE_PROMPT_SCHEMA
        )

        suggested_image_prompt = image_prompt_result_json.get(
            "image_prompt", f"A beautiful image for the theme {theme}."
        )

        fallback_title = {
            "PT": f"Erro na geração para '{theme}'",
            "EN": f"Error in generation for '{theme}'",
            "ES": f"Error en la generación para '{theme}'",
        }
        fallback_content = {
            "PT": f"<p>Ocorreu um erro durante a geração de conteúdo com o provedor {provider_name}.</p>",
            "EN": f"<p>An error occurred during content generation with provider {provider_name}.</p>",
            "ES": f"<p>Se produjo un error durante la geração de conteúdo com o provedor {provider_name}.</p>",
        }

        final_result = {
            "title": generated_content_data.get("title") or fallback_title,
            "excerpt": generated_content_data.get("excerpt")
            or {
                "PT": "Não foi possível gerar resumo.",
                "EN": "Could not generate excerpt.",
                "ES": "No se pudo generar el extracto.",
            },
            "content": generated_content_data.get("content") or fallback_content,
            "metaDescription": generated_content_data.get("metaDescription")
            or {
                "PT": f"Falha na geração para {theme}.",
                "EN": f"Generation failed for {theme}.",
                "ES": f"Fallo en la geração para {theme}.",
            },
            "tags": generated_content_data.get("tags") or [],
            "category": generated_content_data.get("category") or "General",
            "suggested_image_prompt": suggested_image_prompt,
        }

        logger.info(
            f"Conteúdo e prompt de imagem gerados com sucesso para o tema '{theme}' com provedor '{provider_name}'."
        )
        return final_result

    except Exception as e:
        logger.error(
            f"Erro CRÍTICO ao gerar conteúdo com provedor {provider_name}: {str(e)}",
            exc_info=True,
        )
        return {
            "title": {
                "PT": f"Erro na geração para '{theme}'",
                "EN": f"Error in generation for '{theme}'",
                "ES": f"Error en la generación para '{theme}'",
            },
            "excerpt": {
                "PT": "Não foi possível gerar resumo.",
                "EN": "Could not generate excerpt.",
                "ES": "No se pudo gerar el extracto.",
            },
            "content": {
                "PT": f"<p>Ocorreu um erro durante a geração de conteúdo com o provedor {provider_name}.</p>",
                "EN": f"<p>An error occurred during content generation with provider {provider_name}.</p>",
                "ES": f"<p>Se produjo un error durante la geração de conteúdo com o provedor {provider_name}.</p>",
            },
            "metaDescription": {
                "PT": f"Falha na geração para {theme}.",
                "EN": f"Generation failed for {theme}.",
                "ES": f"Fallo en la geração para {theme}.",
            },
            "tags": [],
            "category": "Error",
            "suggested_image_prompt": f"Error generating content for {theme} using {provider_name}",
        }


@router.get(
    "/test-ok",
    tags=["Teste"],
    summary="Testar conexão com o servidor",
    description="Retorna uma mensagem de sucesso para verificar a conectividade do servidor Python.",
)
async def test_endpoint():
    return {"status": "ok", "message": "Conexão com servidor Python bem-sucedida!"}


@router.post(
    "/generate-content-manual",
    tags=["generate-content"],
    summary="Gerar conteúdo manualmente",
    description="Gera conteúdo com base em material bruto fornecido manualmente, usando o provedor de IA especificado.",
)
async def generate_content_manual(
    request: GenerateContentManualRequest,
    user: dict = Depends(Auth.verify_token),
):
    user_id = user.get("sub")
    task_id = request.task_id
    provider_name = request.provider

    try:
        async with AsyncDatabaseManager(DB_FILE) as conn:
            generated_data = await generate_content_with_provider_service(
                provider_name=provider_name,
                theme=request.theme,
                raw_material=request.raw_material[: Config.MAX_TEXT_LEN],
                content_type=request.content_type,
            )

            status_id = db_service.get_status_id_by_name("GENERATED", conn=conn)
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status GENERATED não encontrado."
                )

            db_service.save_material(
                user_id=user_id,
                automation_request_id=None,
                task_id=task_id,
                status_id=status_id,
                theme=request.theme,
                content_type=request.content_type,
                raw_material=request.raw_material,
                generated_content=json.dumps(generated_data, ensure_ascii=False),
                suggested_image_prompt=generated_data.get("suggested_image_prompt"),
                conn=conn,
            )

            logger.info(f"Task {task_id} de geração manual salva no DB com sucesso.")

            status = db_service.get_status_by_id(status_id, conn=conn)
            if not status:
                raise HTTPException(
                    status_code=500, detail=f"Status ID {status_id} não encontrado."
                )
            return {
                "message": "Conteúdo gerado com sucesso!",
                "task_id": task_id,
                "status": status,
                "generated_content": generated_data,
            }
    except Exception as e:
        logger.error(
            f"Erro na geração manual de conteúdo para task_id {task_id} (Provedor: {provider_name}): {str(e)}"
        )
        async with AsyncDatabaseManager(DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name("FAILED_GENERATION", conn=conn)
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status FAILED_GENERATION não encontrado."
                )
            db_service.update_material_status(
                user_id, task_id, "FAILED_GENERATION", conn=conn
            )
        raise HTTPException(status_code=500, detail=f"Erro na geração: {str(e)}")


@router.post(
    "/submit-final-post",
    tags=["generate-content"],
    summary="Submeter post final",
    description="Envia o post final para o backend e opcionalmente deleta a task associada.",
)
async def submit_final_post(
    request: SubmitFinalPostRequest,
    user: dict = Depends(Auth.verify_token),
):
    try:
        headers = {"Authorization": f"Bearer {user.get('token')}"}
        response = await send_post(request.post_data, headers)

        if request.task_id_to_delete:
            async with AsyncDatabaseManager(DB_FILE) as conn:
                db_service.delete_material(
                    conn, user.get("sub"), request.task_id_to_delete
                )
                logger.info(
                    f"Task {request.task_id_to_delete} deletada após envio do post."
                )

        return {"message": "Post enviado com sucesso!", "response": response}
    except Exception as e:
        logger.error(f"Erro ao submeter post final: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao submeter post: {str(e)}")


@router.get(
    "/get-task-result",
    response_model=MaterialResponse,
    tags=["Automação"],
    summary="Obter resultado de uma task",
    description="Retorna o status e os dados de uma task específica com base no task_id.",
)
async def get_task_result(
    task_id: str = Query(..., description="ID da task a ser consultada"),
    user: dict = Depends(Auth.verify_token),
):
    try:
        async with AsyncDatabaseManager(DB_FILE) as conn:
            material = db_service.get_material(conn, user.get("sub"), task_id)
            if not material:
                raise HTTPException(status_code=404, detail="Task não encontrada")

            raw_material_ids_data = material.get("raw_material_ids")
            if isinstance(raw_material_ids_data, str):
                try:
                    raw_material_ids_list = json.loads(raw_material_ids_data)
                except json.JSONDecodeError:
                    raw_material_ids_list = []
            elif isinstance(raw_material_ids_data, list):
                raw_material_ids_list = raw_material_ids_data
            else:
                raw_material_ids_list = []

            status_id = material.get("status_id")
            status = db_service.get_status_by_id(status_id, conn=conn)
            if not status:
                raise HTTPException(
                    status_code=500, detail=f"Status ID {status_id} não encontrado."
                )

            response = MaterialResponse(
                user_id=user.get("sub"),
                automation_request_id=material.get("automation_request_id"),
                task_id=task_id,
                status=status,
                theme=material.get("theme"),
                content_type=material.get("content_type"),
                raw_material_ids=raw_material_ids_list,
                generated_content=material.get("generated_content"),
                suggested_image_prompt=material.get("suggested_image_prompt"),
                created_at=material.get("created_at"),
                updated_at=material.get("updated_at"),
            )
            return response
    except Exception as e:
        logger.error(f"Erro ao obter resultado da task {task_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao obter task: {str(e)}")


@router.get(
    "/list-user-materials",
    response_model=List[MaterialResponse],
    tags=["Automação"],
    summary="Listar materiais do usuário",
    description="Retorna todos os materiais associados ao usuário autenticado.",
)
async def list_user_materials(
    user: dict = Depends(Auth.verify_token),
):
    try:
        async with AsyncDatabaseManager(DB_FILE) as conn:
            materials = db_service.list_user_materials(conn, user.get("sub"))
            if not materials:
                return []
            response = []
            for material in materials:
                status_id = material.get("status_id")
                status = db_service.get_status_by_id(status_id, conn=conn)
                if not status:
                    logger.warning(
                        f"Status ID {status_id} não encontrado para material {material.get('task_id')}"
                    )
                    status = StatusResponse(
                        id=status_id,
                        name="UNKNOWN",
                        display_name="Desconhecido",
                        bg_class="from-gray-400 to-gray-500",
                        text_class="text-gray-800",
                    )

                raw_material_ids_data = material.get("raw_material_ids", "[]")
                if isinstance(raw_material_ids_data, str):
                    try:
                        raw_material_ids_list = json.loads(raw_material_ids_data)
                    except json.JSONDecodeError:
                        raw_material_ids_list = []
                elif isinstance(raw_material_ids_data, list):
                    raw_material_ids_list = raw_material_ids_data
                else:
                    raw_material_ids_list = []

                response.append(
                    MaterialResponse(
                        user_id=material.get("user_id"),
                        automation_request_id=material.get("automation_request_id"),
                        task_id=material.get("task_id"),
                        status=status,
                        theme=material.get("theme"),
                        content_type=material.get("content_type"),
                        raw_material_ids=raw_material_ids_list,
                        generated_content=material.get("generated_content"),
                        suggested_image_prompt=material.get("suggested_image_prompt"),
                        created_at=material.get("created_at"),
                        updated_at=material.get("updated_at"),
                    )
                )
            return response
    except Exception as e:
        logger.error(f"Erro ao listar materiais do usuário: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Erro ao listar materiais: {str(e)}"
        )


@router.post(
    "/generate-image",
    response_model=ImageGenerationResponse,
    tags=["Automação"],
    summary="Gerar imagem com base em prompt",
    description="Gera uma imagem usando um prompt fornecido e retorna em base64, usando o provedor especificado.",
)
async def generate_image(
    request: ImageGenerationRequest,
    user: dict = Depends(Auth.verify_token),
):
    provider_name = request.provider
    try:
        image_provider = get_provider_instance(provider_name)
        image_base64 = await image_provider.generate_image(request.prompt)

        logger.info(
            f"Imagem gerada para prompt: {request.prompt} com provedor: {provider_name}"
        )
        return ImageGenerationResponse(image_base64=image_base64)
    except Exception as e:
        logger.error(
            f"Erro ao gerar imagem com provedor {provider_name}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Erro na geração de imagem com {provider_name}: {str(e)}",
        )


@router.get(
    "/get-raw-material/{id}",
    response_model=RawContentResponse,
    tags=["Matéria Prima"],
    summary="Obter conteúdo bruto por ID",
    description="Retorna o conteúdo bruto (raw content) de um material específico.",
)
async def get_raw_material(
    id: str,
    user: dict = Depends(Auth.verify_token),
):
    try:
        async with AsyncDatabaseManager(DB_FILE) as conn:
            raw_material = db_service.get_raw_material_by_id(conn, id)
            if not raw_material:
                raise HTTPException(
                    status_code=404, detail="Material bruto não encontrado"
                )
            return RawContentResponse(raw_content=raw_material.get("content", ""))
    except Exception as e:
        logger.error(f"Erro ao obter material bruto {id}: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Erro ao buscar material: {str(e)}"
        )


@router.put(
    "/update-raw-material/{id}",
    tags=["Matéria Prima"],
    summary="Atualizar conteúdo bruto por ID",
    description="Atualiza o conteúdo bruto de um material, salvando o conteúdo antigo no histórico.",
)
async def update_raw_material(
    id: str,
    request: UpdateRawMaterialRequest,
    user: dict = Depends(Auth.verify_token),
):
    try:
        async with AsyncDatabaseManager(DB_FILE) as conn:
            raw_material = db_service.get_raw_material_by_id(conn, id)
            if not raw_material:
                raise HTTPException(
                    status_code=404, detail=f"Material com ID {id} não encontrado."
                )

            old_content = raw_material["content"]
            user_id = request.user_id or raw_material.get("user_id")
            task_id = raw_material.get("task_id")

            # Salvar histórico
            conn.execute(
                """
                INSERT INTO raw_materials_history (raw_material_id, old_content, updated_at, user_id, task_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    id,
                    old_content,
                    datetime.now(timezone.utc).isoformat(),
                    user_id,
                    task_id,
                ),
            )

            # Atualizar material
            conn.execute(
                """
                UPDATE raw_materials
                SET content = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    request.content,
                    datetime.now(timezone.utc).isoformat(),
                    id,
                ),
            )

            conn.commit()
            return {
                "message": "Conteúdo atualizado com sucesso.",
                "id": id,
                "history_saved": True,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao atualizar material bruto {id}: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Erro ao atualizar material: {str(e)}"
        )


async def process_material_task(
    user: dict,
    automation_request_id: Optional[str],
    task_id: str,
    raw_material_ids: List[str],
    provider_name: str = "gemini",
):
    """
    Função de processamento de material em background.
    Esta função deve ser chamada pelos seus endpoints de 'trigger'.
    """
    user_id = user.get("sub") if isinstance(user, dict) else None
    if not user_id:
        logger.error(f"User ID não encontrado no token para task_id {task_id}")
        return
    try:
        if not task_id:
            logger.error("Task ID inválido")
            return
        if raw_material_ids is None:
            raw_material_ids = []
            logger.warning(f"Lista de raw_material_ids é None para task_id {task_id}")

        async with AsyncDatabaseManager(DB_FILE) as conn:
            material = db_service.get_material(
                conn=conn, user_id=user_id, task_id=task_id
            )
            if not material:
                logger.error(f"Material não encontrado para task_id {task_id}")
                status_id = db_service.get_status_id_by_name(
                    "FAILED_GENERATION", conn=conn
                )
                if not status_id:
                    raise HTTPException(
                        status_code=500,
                        detail="Status FAILED_GENERATION não encontrado.",
                    )
                db_service.update_material_status(
                    user_id, task_id, "FAILED_GENERATION", conn=conn
                )
                return

            theme = material.get("theme")
            content_type = material.get("content_type")

            raw_materials = db_service.get_raw_materials_by_ids(conn, raw_material_ids)
            raw_material_content = "\n\n---\n\n".join(
                [rm.get("content", "") for rm in raw_materials]
            )

            generated_data = await generate_content_with_provider_service(
                provider_name=provider_name,
                theme=theme,
                raw_material=raw_material_content,
                content_type=content_type,
            )

            status_id = db_service.get_status_id_by_name("GENERATED", conn=conn)
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status GENERATED não encontrado."
                )

            db_service.save_material(
                user_id=user_id,
                automation_request_id=automation_request_id,
                task_id=task_id,
                status_id=status_id,
                theme=theme,
                content_type=content_type,
                raw_material=raw_material_content,
                generated_content=json.dumps(generated_data, ensure_ascii=False),
                suggested_image_prompt=generated_data.get("suggested_image_prompt"),
                conn=conn,
            )

            logger.info(f"Task {task_id} processada e salva com status GENERATED.")

    except Exception as e:
        logger.error(
            f"Erro CRÍTICO no processamento da task {task_id}: {str(e)}", exc_info=True
        )
        async with AsyncDatabaseManager(DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name("FAILED_GENERATION", conn=conn)
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status FAILED_GENERATION não encontrado."
                )
            db_service.update_material_status(
                user_id, task_id, "FAILED_GENERATION", conn=conn
            )


@router.post(
    "/trigger-by-url",
    response_model=TriggerResponse,
    tags=["trigger-automation"],
    summary="Acionar scraping por URL",
    description="Extrai conteúdo de uma URL específica, salva o material bruto e **retorna o task_id** para acionamento posterior da geração.",
)
async def trigger_by_url(
    request: UrlRequest,
    user: dict = Depends(Auth.verify_token),
):
    """
    Aciona o scraping para uma URL, gerando um task_id e salvando
    o material bruto sem iniciar a geração.
    """
    task_id = str(uuid.uuid4())
    user_id = user.get("sub")

    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found in token")

    try:
        async with AsyncDatabaseManager(DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name(
                "PENDING_COLLECTION", conn=conn
            )
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status PENDING_COLLECTION não encontrado."
                )

            db_service.save_material(
                user_id=user_id,
                automation_request_id=None,
                task_id=task_id,
                status_id=status_id,
                theme=None,
                content_type=None,
                source_urls=[str(request.url)],
                conn=conn,
            )

            content = await fetch_url_content(str(request.url))
            if not content:
                status_id = db_service.get_status_id_by_name(
                    "COLLECTION_FAILED", conn=conn
                )
                if not status_id:
                    raise HTTPException(
                        status_code=500,
                        detail="Status COLLECTION_FAILED não encontrado.",
                    )
                db_service.update_material_status(
                    conn=conn,
                    user_id=user_id,
                    task_id=task_id,
                    new_status_name="COLLECTION_FAILED",
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Não foi possível extrair conteúdo da URL: {request.url}",
                )

            cleaned_content = clean_html_content(content)
            if not cleaned_content:
                status_id = db_service.get_status_id_by_name(
                    "COLLECTION_FAILED", conn=conn
                )
                if not status_id:
                    raise HTTPException(
                        status_code=500,
                        detail="Status COLLECTION_FAILED não encontrado.",
                    )
                db_service.update_material_status(
                    conn=conn,
                    user_id=user_id,
                    task_id=task_id,
                    new_status_name="COLLECTION_FAILED",
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Não foi possível extrair conteúdo limpo da URL: {request.url}",
                )

            raw_id = db_service.save_raw_material(
                conn=conn,
                user_id=user_id,
                task_id=task_id,
                url=str(request.url),
                content=cleaned_content,
            )

            db_service.update_material_raw_material_ids(
                conn=conn,
                task_id=task_id,
                raw_material_ids=[raw_id],
            )

            status_id = db_service.get_status_id_by_name("RAW_COLLECTED", conn=conn)
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status RAW_COLLECTED não encontrado."
                )
            db_service.update_material_status(
                conn=conn,
                user_id=user_id,
                task_id=task_id,
                new_status_name="RAW_COLLECTED",
            )

            status = db_service.get_status_by_id(status_id, conn=conn)
            if not status:
                raise HTTPException(
                    status_code=500, detail=f"Status ID {status_id} não encontrado."
                )
            return TriggerResponse(
                trigger_id=None,
                message=f"Extração concluída para {request.url}. Use o task_id para iniciar a geração.",
                task_id=task_id,
                status=status,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro na extração para task_id {task_id}: {str(e)}")
        async with AsyncDatabaseManager(DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name("COLLECTION_FAILED", conn=conn)
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status COLLECTION_FAILED não encontrado."
                )
            db_service.update_material_status(
                conn=conn,
                user_id=user_id,
                task_id=task_id,
                new_status_name="COLLECTION_FAILED",
            )
        raise HTTPException(status_code=500, detail=f"Erro na extração: {str(e)}")


@router.post(
    "/trigger-multiple-urls",
    response_model=TriggerResponse,
    tags=["trigger-automation"],
    summary="Acionar scraping por várias URLs",
    description="Extrai conteúdo de várias URLs, salva os materiais brutos e retorna o task_id.",
)
async def trigger_multiple_urls(
    request: ExtractFromUrlsRequest,
    user: dict = Depends(Auth.verify_token),
):
    task_id = str(uuid.uuid4())
    user_id = user.get("sub")
    raw_material_ids = []

    try:
        async with AsyncDatabaseManager(DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name(
                "PENDING_COLLECTION", conn=conn
            )
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status PENDING_COLLECTION não encontrado."
                )

            db_service.save_material(
                user_id=user_id,
                automation_request_id=None,
                task_id=task_id,
                status_id=status_id,
                theme=None,
                content_type=None,
                source_urls=[str(url) for url in request.urls],
                conn=conn,
            )

            for url in request.urls:
                url_str = str(url)
                content = await fetch_url_content(url_str)
                if not content:
                    logger.warning(f"Nenhum conteúdo retornado para a URL: {url_str}")
                    continue

                cleaned_content = clean_html_content(content)
                if cleaned_content:
                    raw_id = db_service.save_raw_material(
                        conn=conn,
                        user_id=user_id,
                        task_id=task_id,
                        url=url_str,
                        content=cleaned_content,
                    )
                    raw_material_ids.append(raw_id)

            if not raw_material_ids:
                status_id = db_service.get_status_id_by_name(
                    "COLLECTION_FAILED", conn=conn
                )
                if not status_id:
                    raise HTTPException(
                        status_code=500,
                        detail="Status COLLECTION_FAILED não encontrado.",
                    )
                db_service.update_material_status(
                    conn=conn,
                    user_id=user_id,
                    task_id=task_id,
                    new_status_name="COLLECTION_FAILED",
                )
                raise HTTPException(
                    status_code=400,
                    detail="Nenhum conteúdo extraído das URLs fornecidas",
                )

            db_service.update_material_raw_material_ids(
                conn=conn,
                task_id=task_id,
                raw_material_ids=raw_material_ids,
            )
            status_id = db_service.get_status_id_by_name("RAW_COLLECTED", conn=conn)
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status RAW_COLLECTED não encontrado."
                )
            db_service.update_material_status(
                conn=conn,
                user_id=user_id,
                task_id=task_id,
                new_status_name="RAW_COLLECTED",
            )

            status = db_service.get_status_by_id(status_id, conn=conn)
            if not status:
                raise HTTPException(
                    status_code=500, detail=f"Status ID {status_id} não encontrado."
                )
            return TriggerResponse(
                trigger_id=None,
                message=f"Extração de {len(raw_material_ids)} URLs concluída. Use o task_id para iniciar a geração de conteúdo.",
                task_id=task_id,
                status=status,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro na extração de URLs para task_id {task_id}: {str(e)}")
        async with AsyncDatabaseManager(DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name("COLLECTION_FAILED", conn=conn)
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status COLLECTION_FAILED não encontrado."
                )
            db_service.update_material_status(
                conn=conn,
                user_id=user_id,
                task_id=task_id,
                new_status_name="COLLECTION_FAILED",
            )
        raise HTTPException(status_code=500, detail=f"Erro na extração: {str(e)}")


@router.post(
    "/generate-content",
    tags=["generate-content"],
    summary="Gerar conteúdo sob demanda",
    description="Gera conteúdo com base em um task_id existente, usando materiais brutos salvos no banco e o provedor de IA especificado.",
)
async def generate_content(
    request: GenerateContentRequest,
    user: dict = Depends(Auth.verify_token),
):
    user_id = user.get("sub")
    task_id = request.task_id
    theme = request.theme
    content_type = request.output_format
    provider_name = request.provider

    try:
        async with AsyncDatabaseManager(DB_FILE) as conn:
            material = db_service.get_material(conn, user_id, task_id)
            if not material or not material.get("raw_material_ids"):
                raise HTTPException(
                    status_code=404,
                    detail="Nenhum material bruto encontrado para o task_id",
                )

            raw_material_ids = material["raw_material_ids"]
            if isinstance(raw_material_ids, str):
                try:
                    raw_material_ids = json.loads(raw_material_ids)
                except json.JSONDecodeError:
                    raw_material_ids = []

            raw_materials_records = db_service.get_raw_materials_by_ids(
                conn, raw_material_ids
            )

            raw_materials = [
                record.get("content", "")
                for record in raw_materials_records
                if record.get("content")
            ]

            if not raw_materials:
                raise HTTPException(
                    status_code=400,
                    detail="Nenhum conteúdo válido encontrado nos raw_material_ids",
                )

            compiled_raw_material = "\n\n---\n\n".join(raw_materials)[
                : Config.MAX_TEXT_LEN
            ]

            status_id = db_service.get_status_id_by_name(
                "PENDING_GENERATION", conn=conn
            )
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status PENDING_GENERATION não encontrado."
                )
            db_service.update_material_status(
                user_id, task_id, "PENDING_GENERATION", conn=conn
            )

            generated_data = await generate_content_with_provider_service(
                provider_name=provider_name,
                theme=theme,
                raw_material=compiled_raw_material,
                content_type=content_type,
            )

            status_id = db_service.get_status_id_by_name("GENERATED", conn=conn)
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status GENERATED não encontrado."
                )
            db_service.save_material(
                user_id=user_id,
                automation_request_id=material.get("automation_request_id"),
                task_id=task_id,
                status_id=status_id,
                theme=theme,
                content_type=content_type,
                raw_material=compiled_raw_material,
                generated_content=json.dumps(generated_data, ensure_ascii=False),
                suggested_image_prompt=generated_data.get("suggested_image_prompt"),
                conn=conn,
            )

            logger.info(
                f"Conteúdo gerado sob demanda para task_id {task_id} com sucesso. Provedor: {provider_name}"
            )

            status = db_service.get_status_by_id(status_id, conn=conn)
            if not status:
                raise HTTPException(
                    status_code=500, detail=f"Status ID {status_id} não encontrado."
                )
            return {
                "message": "Conteúdo gerado com sucesso!",
                "task_id": task_id,
                "status": status,
                "generated_content": generated_data,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Erro na geração de conteúdo sob demanda para task_id {task_id}: {str(e)}",
            exc_info=True,
        )
        try:
            async with AsyncDatabaseManager(DB_FILE) as conn:
                status_id = db_service.get_status_id_by_name(
                    "FAILED_GENERATION", conn=conn
                )
                if not status_id:
                    raise HTTPException(
                        status_code=500,
                        detail="Status FAILED_GENERATION não encontrado.",
                    )
                db_service.update_material_status(
                    user_id, task_id, "FAILED_GENERATION", conn=conn
                )
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Erro na geração: {str(e)}")


@router.get(
    "/raw-material/{raw_material_id}",
    tags=["Automação"],
    summary="Busca material bruto a partir do seu ID",
    description="Retorna o conteúdo bruto de uma URL para revisão, usando o ID do material.",
    response_model=Dict[str, str],
)
async def get_raw_material(
    raw_material_id: str, conn: sqlite3.Connection = Depends(get_db_connection)
):
    """
    Busca o conteúdo bruto de uma URL a partir do ID do material.
    """
    raw_material = db_service.get_raw_material_by_id(conn, raw_material_id)

    if not raw_material:
        raise HTTPException(status_code=404, detail="Material bruto não encontrado.")

    return {"raw_content": raw_material.get("content")}


@router.get(
    "/raw-materials-by-task/{task_id}",
    tags=["Automação"],
    summary="Busca todos os materiais brutos de uma tarefa",
    description="Retorna o conteúdo de todas as URLs de uma única requisição de automação.",
    response_model=RawMaterialsListResponse,
)
async def get_raw_materials_by_task(
    task_id: str, conn: sqlite3.Connection = Depends(get_db_connection)
):
    """
    Busca todos os materiais brutos associados a um task_id.
    """
    material = db_service.get_material_by_task_id(conn, task_id)
    if not material:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")

    raw_material_ids = material.get("raw_material_ids", [])
    if isinstance(raw_material_ids, str):
        try:
            raw_material_ids = json.loads(raw_material_ids)
        except json.JSONDecodeError:
            raw_material_ids = []

    raw_materials = db_service.get_raw_materials_by_ids(conn, raw_material_ids)

    return {"raw_materials": raw_materials}


class ContentInput(BaseModel):
    user_id: str = Field(..., alias="userId")
    text_content: str = Field(..., alias="textContent")
    content_type: str = Field(..., alias="contentType")
    provider: str = Field(
        "gemini", description="O provedor de IA a ser usado (ex: 'gemini', 'deepseek')."
    )

    class Config:
        allow_population_by_field_name = True


THEME_SCHEMA = {
    "type": "object",
    "properties": {
        "theme": {"type": "string", "description": "O tema principal do texto."},
    },
    "required": ["theme"],
}


@router.post(
    "/trigger-by-text",
    tags=["trigger-automation"],
    summary="Inicia automação a partir de um texto",
    description="Recebe um texto, extrai um tema com a IA, salva no banco e prepara a automação.",
    status_code=200,
)
async def trigger_by_text(
    request: TriggerByTextRequest,
    current_user: dict = Depends(Auth.verify_token),
):
    """
    Recebe um texto, utiliza IA para extrair tema e fatores de busca aprimorados,
    e salva a configuração no DB para acionamento posterior via /trigger-by-id.
    """
    user_id = current_user.get("sub")
    task_id = str(uuid.uuid4())
    provider_name = request.provider

    # 1. Extração de Tema e Fatores de Busca
    try:
        provider = get_provider_instance(provider_name)
        MAX_AI_INPUT_CHARS = 10000 
        
        prompt = f"Analise o seguinte texto: '{request.textContent[:MAX_AI_INPUT_CHARS]}'. Com base nele, extraia o tema central e gere um objeto JSON serializado de fatores de busca refinados (keywords, fontes, etc.) para coletar material bruto relevante."

        ai_response: Dict[str, Any] = await provider.generate_content(
        prompt=prompt,
        response_schema=AI_RESPONSE_SCHEMA_CONFIG,  
    )

        theme = ai_response.get("theme")
        search_factors = ai_response.get("search_factors")
        
        if not theme or not search_factors:
             raise ValueError("A IA não retornou o tema e/ou os fatores de busca esperados.")

    except Exception as e:
        logger.error(f"Erro na extração de tema/fatores de busca: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Falha na análise de IA: {str(e)}"
        )

    # 2. Persistência no DB
    try:
        async with AsyncDatabaseManager(DB_FILE) as conn:
            # Status: PENDING_COLLECTION (Aguardando Coleta via trigger-by-id)
            status_id = db_service.get_status_id_by_name("PENDING_COLLECTION", conn)

            # 2a. Criação do Registro Principal (Tabela materials)
            db_service.save_material(
                conn=conn,
                user_id=user_id,
                automation_request_id=None,
                task_id=task_id,
                status_id=status_id,
                theme=theme,
                content_type=request.content_type,
            )

            # 2b. Salva a Configuração de Busca (Tabela automation_configs - NOVO PASSO)
            db_service.save_automation_config(
                conn=conn,
                task_id=task_id,
                search_factors=search_factors,
            )

        return TriggerByTextResponse(
            task_id=task_id,
            theme=theme,
            status="PENDING_COLLECTION",
        )

    except Exception as e:
        logger.error(f"Erro ao salvar tarefa no DB para task_id {task_id}: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Falha ao persistir a tarefa: {str(e)}"
        )

@router.post(
    "/trigger",
    tags=["trigger-automation"],
    summary="Acionar automação síncrona",
    description="Inicia a automação de geração de conteúdo com base em parâmetros fornecidos, executando de forma síncrona.",
)
async def trigger_automation_post(
    request_data: TriggerRequest,
    user_payload: dict = Depends(Auth.verify_token),
):
    logger.info(
        f"Endpoint POST /trigger acionado por {user_payload.get('sub', 'Desconhecido')}."
    )
    output_format = request_data.output_format
    theme = request_data.theme
    user_id = user_payload.get("sub", "anonymous_user")
    task_id = str(uuid.uuid4())

    try:
        async with AsyncDatabaseManager(DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name("PENDING", conn=conn)
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status PENDING não encontrado."
                )

            db_service.save_material(
                user_id=user_id,
                automation_request_id=None,
                task_id=task_id,
                status_id=status_id,
                theme=theme,
                content_type=output_format,
                conn=conn,
            )

        output_report = await run_automation(
            output_format=output_format,
            theme=theme,
            auth_headers={"Authorization": f"Bearer {user_payload.get('token')}"},
            user_id=user_id,
            task_id=task_id,
            return_raw_material_only=False,
        )

        if not isinstance(output_report, str):
            output_report = str(output_report)

        async with AsyncDatabaseManager(DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name("GENERATED", conn=conn)
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status GENERATED não encontrado."
                )
            status = db_service.get_status_by_id(status_id, conn=conn)
            if not status:
                raise HTTPException(
                    status_code=500, detail=f"Status ID {status_id} não encontrado."
                )

        return JSONResponse(
            content={
                "message": "Automação executada com sucesso!",
                "report_summary": output_report,
                "task_id": task_id,
                "status": status,
                "parameters": {"output_format": output_format, "theme": theme},
            },
            media_type="application/json; charset=utf-8",
        )

    except ValidationError as e:
        logger.error(f"Erro de validação Pydantic: {str(e)}")
        if Config.LOGS_API_URL:
            log_data = {
                "action": f"Falha de validação Pydantic para POST /trigger. Erro: {str(e)}",
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", ""),
                "level": "ERROR",
                "report_id": task_id,
            }
            send_logs_to_backend(log_data)
        async with AsyncDatabaseManager(DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name("FAILED_GENERATION", conn=conn)
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status FAILED_GENERATION não encontrado."
                )
            db_service.update_material_status(
                user_id, task_id, "FAILED_GENERATION", conn=conn
            )
        raise HTTPException(
            status_code=400,
            detail={"message": "Erro de validação.", "errors": e.errors()},
        )
    except HTTPException as http_exc:
        logger.error(f"Erro HTTP: {http_exc.detail}")
        if Config.LOGS_API_URL:
            log_data = {
                "action": f"Falha na automação POST /trigger. Erro: {http_exc.detail}",
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", ""),
                "level": "ERROR",
                "report_id": task_id,
            }
            send_logs_to_backend(log_data)
        async with AsyncDatabaseManager(DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name("FAILED_GENERATION", conn=conn)
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status FAILED_GENERATION não encontrado."
                )
            db_service.update_material_status(
                user_id, task_id, "FAILED_GENERATION", conn=conn
            )
        raise
    except Exception as e:
        logger.error(f"Erro inesperado: {str(e)}")
        if Config.LOGS_API_URL:
            log_data = {
                "action": f"Erro inesperado na automação POST /trigger. Erro: {str(e)}",
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", ""),
                "level": "CRITICAL",
                "report_id": task_id,
            }
            send_logs_to_backend(log_data)
        async with AsyncDatabaseManager(DB_FILE) as conn:
            status_id = db_service.get_status_id_by_name("FAILED_GENERATION", conn=conn)
            if not status_id:
                raise HTTPException(
                    status_code=500, detail="Status FAILED_GENERATION não encontrado."
                )
            db_service.update_material_status(
                user_id, task_id, "FAILED_GENERATION", conn=conn
            )
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@router.get(
    "/trigger-by-id/{id}",
    response_model=TriggerResponse,
    tags=["trigger-automation"],
    summary="Acionar automação por ID",
    description="Inicia a coleta de material bruto em segundo plano com base em um ID de requisição existente.",
)
async def trigger_by_id(
    id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(Auth.verify_token),
):
    """
    Aciona a automação de coleta de material bruto em segundo plano,
    usando o ID da tarefa que contém os fatores de busca inteligentes.
    """
    user_id = current_user.get("sub")
    task_id = id 

    try:
        async with AsyncDatabaseManager(DB_FILE) as conn:
            # 1. Busca os dados principais da tarefa
            material_data = db_service.get_material_by_task_id(conn, task_id)
            if not material_data:
                raise HTTPException(
                    status_code=404, detail=f"Tarefa com ID '{task_id}' não encontrada."
                )

            # 2. Busca a configuração de busca inteligente (search_factors)
            config_data = db_service.get_automation_config(conn, task_id)
            if not config_data or not config_data.get("search_factors"):
                raise HTTPException(
                    status_code=404,
                    detail=f"Fatores de busca (search_factors) não configurados para o ID '{task_id}'.",
                )

            # Extração dos parâmetros
            theme = material_data.get("theme")
            content_type = material_data.get("content_type")
            search_factors = config_data.get("search_factors") # String JSON

            # 3. Aciona a coleta em segundo plano (com o novo parâmetro)
            background_tasks.add_task(
                run_automation,
                task_id=task_id,
                user_id=user_id,
                theme=theme,
                content_type=content_type,
                search_factors=search_factors,
                return_raw_material_only=True,
                auth_headers={"Authorization": f"Bearer {current_user.get('token')}"}
            )

        return JSONResponse(
            status_code=202,  # Accepted
            content={
                "task_id": task_id,
                "status": "COLLECTION_INITIATED",
                "message": "Coleta de material bruto iniciada em segundo plano. Use o task_id para monitorar o status.",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Erro ao acionar automação para task_id {task_id}: {str(e)}"
        )
        raise HTTPException(
            status_code=500, detail=f"Erro interno ao acionar a automação: {str(e)}"
        )

@router.get(
    "/posts",
    tags=["Posts"],
    summary="Listar todos os posts/tarefas",
    description="Retorna uma lista paginada de todos os posts (tarefas) gerados, com informações de status e visualização.",
)
def list_posts():
    """
    Retorna todos os posts (tarefas) gerados, simulando a estrutura de data/articles.json.
    """
    try:
        conn = sqlite3.connect(db_service.DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM materials ORDER BY created_at DESC")
        materials = cursor.fetchall()

        posts = []
        for row in materials:
            status_id = row["status_id"]
            status = db_service.get_status_by_id(status_id, conn=conn)
            if not status:
                status = StatusResponse(
                    id=status_id,
                    name="UNKNOWN",
                    display_name="Desconhecido",
                    bg_class="from-gray-400 to-gray-500",
                    text_class="text-gray-800",
                )

            posts.append(
                {
                    "id": row["task_id"],
                    "title": row["theme"] or "Tema indefinido",
                    "subtitle": row["content_type"] or "Sem tipo definido",
                    "description": (
                        row["generated_content"] or "Material ainda não gerado."
                    )[:250],
                    "date": row["created_at"][:10] if row["created_at"] else "",
                    "status": status,
                    "statusLabel": status["display_name"],
                    "statusColor": status["text_class"].replace("text-", ""),
                    "tags": [row["content_type"] or "geral"],
                    "gradient": status["bg_class"],
                    "panelDetails": None,
                }
            )
        conn.close()
        return {"data": posts, "count": len(posts)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao listar posts: {str(e)}")


@router.get(
    "/posts/{task_id}",
    tags=["Posts"],
    summary="Obter detalhes de um post/tarefa por ID",
    description="Retorna os detalhes completos de um post/tarefa específica (para o side panel do admin), incluindo material bruto e conteúdo gerado.",
)
def get_post_by_id(task_id: str):
    """
    Retorna os detalhes completos de um post/tarefa (para o side panel do admin).
    """
    try:
        conn = sqlite3.connect(db_service.DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM materials WHERE task_id = ?", (task_id,))
        material = cursor.fetchone()

        if not material:
            conn.close()
            raise HTTPException(status_code=404, detail="Tarefa não encontrada")

        status_id = material["status_id"]
        status = db_service.get_status_by_id(status_id, conn=conn)
        if not status:
            status = StatusResponse(
                id=status_id,
                name="UNKNOWN",
                display_name="Desconhecido",
                bg_class="from-gray-400 to-gray-500",
                text_class="text-gray-800",
            )

        raw_material_ids_data = material["raw_material_ids"]
        if isinstance(raw_material_ids_data, str):
            try:
                raw_material_ids_list = json.loads(raw_material_ids_data)
            except json.JSONDecodeError:
                raw_material_ids_list = []
        else:
            raw_material_ids_list = raw_material_ids_data or []

        raw_materials = db_service.get_raw_materials_by_ids(conn, raw_material_ids_list)

        response = {
            "id": material["task_id"],
            "user_id": material["user_id"],
            "automation_request_id": material["automation_request_id"],
            "status": status,
            "theme": material["theme"],
            "content_type": material["content_type"],
            "raw_material_ids": raw_material_ids_list,
            "raw_materials": [
                {"id": rm["id"], "content": rm["content"], "url": rm["url"]}
                for rm in raw_materials
            ],
            "generated_content": material["generated_content"],
            "suggested_image_prompt": material["suggested_image_prompt"],
            "created_at": material["created_at"],
            "updated_at": material["updated_at"],
        }

        conn.close()
        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter post: {str(e)}")


@router.post(
    "/list-raw-materials-by-ids",
    response_model=RawMaterialsListResponse,
    tags=["Matéria Prima"],
    summary="Listar materiais brutos por IDs",
    description="Retorna uma lista de materiais brutos com base nos IDs fornecidos, com conteúdo resumido.",
)
async def list_raw_materials_by_ids(
    request: RawMaterialRequest,
    user: dict = Depends(Auth.verify_token),
):
    try:
        async with AsyncDatabaseManager(DB_FILE) as conn:
            raw_materials = db_service.get_raw_materials_by_ids(conn, request.ids)
            if not raw_materials:
                raise HTTPException(
                    status_code=404,
                    detail="Nenhum material bruto encontrado para os IDs fornecidos.",
                )

            # Limitar conteúdo retornado para performance
            MAX_CONTENT_LENGTH = 1000
            response = []
            for rm in raw_materials:
                content = rm.get("content", "")
                summarized_content = (
                    content[:MAX_CONTENT_LENGTH] + "..."
                    if len(content) > MAX_CONTENT_LENGTH
                    else content
                )
                response.append(
                    {
                        "id": rm.get("id"),
                        "content": summarized_content,
                        "url": rm.get("url"),
                        "task_id": rm.get("task_id"),
                        "created_at": rm.get("created_at"),
                        "updated_at": rm.get("updated_at"),
                    }
                )

            return RawMaterialsListResponse(raw_materials=response)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao listar materiais brutos por IDs: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Erro ao listar materiais: {str(e)}"
        )
