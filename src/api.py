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

import google.generativeai as genai
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from dotenv import load_dotenv
import os

from src.config import Config
from src.auth import Auth
import src.database_service as db_service
from src.database_service import AsyncDatabaseManager, DB_FILE, get_db_connection
from src.scraping_service import (
    fetch_url_content,
    clean_html_content,
    save_selector_data,
    extract_content_by_selectors,
)
from src.database import get_db
from src.models import AutomationRequest
from src.utils import send_logs_to_backend
from sqlalchemy.orm import Session
import src.postgresql_service as pg_service
from src.automation_service import run_automation

logger = logging.getLogger(__name__)

router = APIRouter()


class RawContentResponse(BaseModel):
    raw_content: str


class RawMaterialsListResponse(BaseModel):
    raw_materials: List[Dict[str, Any]]


# Configuração da API Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError(
        "A chave GEMINI_API_KEY não foi encontrada. Verifique seu arquivo .env"
    )
genai.configure(api_key=GEMINI_API_KEY)


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


class SubmitFinalPostRequest(BaseModel):
    task_id_to_delete: Optional[str] = None
    post_data: Dict[str, Any]


class ImageGenerationRequest(BaseModel):
    prompt: str


class ImageGenerationResponse(BaseModel):
    image_base64: str


class AutomationRequestDetails(BaseModel):
    id: int
    outputFormat: str
    theme: str
    createdAt: datetime
    updatedAt: datetime


class MaterialResponse(BaseModel):
    user_id: str
    automation_request_id: Optional[Union[int, str]] = None
    task_id: str
    status: str
    theme: Optional[str] = None
    content_type: Optional[str] = None
    raw_material_ids: Optional[List[str]] = None
    generated_content: Optional[Union[str, Dict[str, Any]]] = None
    suggested_image_prompt: Optional[str] = None
    created_at: str
    updated_at: str


class TriggerRequest(BaseModel):
    theme: Optional[str] = Field(None, alias="theme")
    output_format: Optional[str] = Field(None, alias="outputFormat")

    class Config:
        allow_population_by_field_name = True


class TriggerResponse(BaseModel):
    trigger_id: Optional[str] = None
    message: str
    task_id: str
    status: str


class ExtractFromUrlsRequest(BaseModel):
    urls: List[str]
    theme: Optional[str] = None
    output_format: Optional[str] = Field(None, alias="outputFormat")
    user_id: str = Field(..., alias="userId")

    class Config:
        allow_population_by_field_name = True


class GenerateContentRequest(BaseModel):
    user_id: str = Field(..., alias="userId")
    task_id: str = Field(..., alias="taskId")
    theme: Optional[str] = Field(None, alias="theme")
    output_format: Optional[str] = Field(None, alias="outputFormat")

    class Config:
        allow_population_by_field_name = True


class UrlRequest(BaseModel):
    url: HttpUrl = Field(..., description="A URL da qual o conteúdo será extraído.")
    theme: str = Field(
        ..., min_length=3, description="O tema a ser usado para a geração do conteúdo."
    )
    content_type: str = Field(
        "summary",
        description="O tipo de conteúdo a ser gerado (ex: 'summary', 'article', 'social_media_post').",
    )


class RawMaterialRequest(BaseModel):
    ids: List[str]


class UpdateRawMaterialRequest(BaseModel):
    content: str
    user_id: str


# Esquemas para Gemini
GEMINI_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
        },
        "excerpt": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
        },
        "content": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
        },
        "metaDescription": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tags relevantes para o artigo",
        },
        "category": {
            "type": "string",
            "description": "Categoria principal do artigo",
        },
        "suggested_image_prompt": {
            "type": "string",
            "description": "Prompt sugerido para geração de imagem",
        },
    },
    "required": ["title", "excerpt", "content", "metaDescription", "tags", "category"],
}


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


async def generate_content_with_gemini_service(
    theme: str, raw_material: str, content_type: str
) -> dict:
    """Gera conteúdo usando a API Gemini com base no material bruto."""
    try:
        model = genai.GenerativeModel("gemini-1.5-pro")
        prompt = (
            f"Baseado no tema '{theme}', gere um {content_type} em português, inglês e espanhol. "
            f"Use o seguinte material bruto como referência: {raw_material[:Config.MAX_TEXT_LEN]}. "
            f"Retorne o resultado no formato JSON conforme o esquema: {json.dumps(GEMINI_OUTPUT_SCHEMA)}. "
            f"Inclua um prompt sugerido para geração de imagem."
        )
        response = await model.generate_content_async(prompt)

        # Verificar se a resposta é válida
        if not response or not response.text:
            logger.error("Resposta vazia ou inválida do Gemini")
            return {
                "title": f"Erro na geração para '{theme}'",
                "content_pt": "Não foi possível gerar conteúdo. Tente novamente mais tarde.",
                "content_en": "Content generation failed. Please try again later.",
                "content_es": "No se pudo generar contenido. Inténtelo de nuevo más tarde.",
                "suggested_image_prompt": f"Error image for {theme}",
            }

        try:
            generated_data = json.loads(response.text)
            logger.info(
                f"Conteúdo gerado para o tema '{theme}' com tipo '{content_type}'."
            )
            return generated_data
        except json.JSONDecodeError as json_err:
            logger.error(f"Erro ao decodificar JSON da resposta: {str(json_err)}")
            return {
                "title": f"Erro de formato para '{theme}'",
                "content_pt": "Erro no formato da resposta. Tente novamente.",
                "content_en": "Response format error. Please try again.",
                "content_es": "Error en el formato de respuesta. Inténtelo de nuevo.",
                "suggested_image_prompt": f"Error formatting for {theme}",
            }
    except Exception as e:
        logger.error(f"Erro ao gerar conteúdo com Gemini: {str(e)}")
        # Retornar um objeto válido em vez de lançar exceção
        return {
            "title": f"Falha na geração para '{theme}'",
            "content_pt": "Ocorreu um erro durante a geração de conteúdo.",
            "content_en": "An error occurred during content generation.",
            "content_es": "Se produjo un error durante la generación de contenido.",
            "suggested_image_prompt": f"Error generating content for {theme}",
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
    description="Gera conteúdo com base em material bruto fornecido manualmente.",
)
async def generate_content_manual(
    request: GenerateContentManualRequest,
    user: dict = Depends(Auth.verify_token),
):
    user_id = request.user_id
    task_id = request.task_id
    try:
        generated_data = await generate_content_with_gemini_service(
            theme=request.theme,
            raw_material=request.raw_material[: Config.MAX_TEXT_LEN],
            content_type=request.content_type,
        )

        db_service.save_material(
            user_id=user_id,
            automation_request_id=None,
            task_id=task_id,
            status="GENERATED",
            theme=request.theme,
            content_type=request.content_type,
            raw_material=request.raw_material,
            generated_content=json.dumps(generated_data, ensure_ascii=False),
            suggested_image_prompt=generated_data.get("suggested_image_prompt"),
        )

        return {
            "message": "Conteúdo gerado com sucesso!",
            "task_id": task_id,
            "status": "GENERATED",
        }
    except Exception as e:
        logger.error(
            f"Erro na geração manual de conteúdo para task_id {task_id}: {str(e)}"
        )
        db_service.update_material_status(user_id, task_id, "FAILED_GENERATION")
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
        # Acessa o token diretamente do dicionário retornado por Auth.verify_token
        # Se a chave 'token' estiver presente
        headers = {"Authorization": f"Bearer {user.get('token')}"}
        response = await send_post(request.post_data, headers)

        if request.task_id_to_delete:
            # Usa AsyncDatabaseManager para gerenciar a conexão
            async with AsyncDatabaseManager(DB_FILE) as conn:
                # Acessa o user_id diretamente da chave 'sub'
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
        # Usa AsyncDatabaseManager para gerenciar a conexão
        async with AsyncDatabaseManager(DB_FILE) as conn:
            # Acessa o user_id diretamente da chave 'sub'
            material = db_service.get_material(conn, user.get("sub"), task_id)
            if not material:
                raise HTTPException(status_code=404, detail="Task não encontrada")

            response = MaterialResponse(
                # Acessa o user_id diretamente da chave 'sub'
                user_id=user.get("sub"),
                automation_request_id=material.get("automation_request_id"),
                task_id=task_id,
                status=material.get("status"),
                theme=material.get("theme"),
                content_type=material.get("content_type"),
                raw_material=material.get("raw_material"),
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

            response = [
                MaterialResponse(
                    user_id=material.get("user_id"),
                    automation_request_id=material.get("automation_request_id"),
                    task_id=material.get("task_id"),
                    status=material.get("status"),
                    theme=material.get("theme"),
                    content_type=material.get("content_type"),
                    raw_material_ids=(
                        json.loads(material.get("raw_material_ids", "[]"))
                        if material.get("raw_material_ids")
                        else []
                    ),
                    generated_content=material.get("generated_content"),
                    suggested_image_prompt=material.get("suggested_image_prompt"),
                    created_at=material.get("created_at"),
                    updated_at=material.get("updated_at"),
                )
                for material in materials
            ]
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
    description="Gera uma imagem usando um prompt fornecido e retorna em base64.",
)
async def generate_image(
    request: ImageGenerationRequest,
    user: dict = Depends(Auth.verify_token),
):
    try:
        model = genai.GenerativeModel("gemini-1.5-pro")
        response = await model.generate_content_async(request.prompt)
        image_base64 = response.text  # Supondo que a API retorna base64
        logger.info(f"Imagem gerada para prompt: {request.prompt}")
        return ImageGenerationResponse(image_base64=image_base64)
    except Exception as e:
        logger.error(f"Erro ao gerar imagem: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Erro na geração de imagem: {str(e)}"
        )


async def process_material_task(
    user: dict,
    automation_request_id: Optional[str],
    task_id: str,
    raw_material_ids: List[str],
):
    # Restante da função...
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

        # **Cria uma nova conexão aqui**
        async with AsyncDatabaseManager(DB_FILE) as conn:
            try:
                # Usa a nova conexão para buscar o material
                material = db_service.get_material(
                    conn=conn, user_id=user_id, task_id=task_id
                )
                if not material:
                    logger.error(f"Material não encontrado para task_id {task_id}")
                    db_service.update_material_status(
                        conn=conn,
                        user_id=user_id,
                        task_id=task_id,
                        new_status="FAILED_NO_MATERIAL",
                    )
                    return
            except Exception as mat_err:
                logger.error(
                    f"Erro ao obter material para task_id {task_id}: {str(mat_err)}"
                )
                db_service.update_material_status(
                    conn=conn,
                    user_id=user_id,
                    task_id=task_id,
                    new_status="FAILED_NO_MATERIAL",
                )
                return

            raw_materials = []

            for raw_id in raw_material_ids:
                if raw_id:
                    try:
                        content = db_service.get_raw_material(
                            conn=conn, raw_material_id=raw_id
                        )
                        if (
                            content
                            and isinstance(content, dict)
                            and content.get("content")
                        ):
                            raw_materials.append(content.get("content"))
                    except Exception as raw_err:
                        logger.error(
                            f"Erro ao obter raw material {raw_id}: {str(raw_err)}"
                        )

            if not raw_materials:
                logger.error(f"Nenhum conteúdo válido para task_id {task_id}")
                db_service.update_material_status(
                    conn=conn,
                    user_id=user_id,
                    task_id=task_id,
                    new_status="FAILED_NO_CONTENT",
                )
                return

            compiled_raw_material = "\n\n".join(raw_materials)

            max_text_len = 10000
            if len(compiled_raw_material) > max_text_len:
                compiled_raw_material = compiled_raw_material[:max_text_len]

            theme = "Desconhecido"
            content_type = "article"

            if material and isinstance(material, dict):
                theme = material.get("theme", "Desconhecido")
                content_type = material.get("content_type", "article")

            try:
                db_service.update_material_status(
                    conn=conn,
                    user_id=user_id,
                    task_id=task_id,
                    new_status="PENDING_GENERATION",
                )
            except Exception as status_err:
                logger.error(
                    f"Erro ao atualizar status para PENDING_GENERATION: {str(status_err)}"
                )

        # A lógica de geração de conteúdo deve ser executada fora do bloco 'with' para evitar bloqueio
        generated_data = None
        try:
            generated_data = await generate_content_with_gemini_service(
                theme=theme,
                raw_material=compiled_raw_material,
                content_type=content_type,
            )
        except Exception as gen_err:
            logger.error(f"Erro ao gerar conteúdo com Gemini: {str(gen_err)}")

        if generated_data is None or not isinstance(generated_data, dict):
            logger.warning(
                f"Dados gerados inválidos para task_id {task_id}, usando conteúdo padrão"
            )
            generated_data = {
                "title": f"Conteúdo para {theme}",
                "content_pt": "Não foi possível gerar conteúdo automaticamente.",
                "content_en": "Content could not be generated automatically.",
                "content_es": "No se pudo generar contenido automáticamente.",
                "suggested_image_prompt": f"Image for {theme}",
            }

        suggested_image_prompt = generated_data.get(
            "suggested_image_prompt", f"Image for {theme}"
        )

        try:
            # **Cria uma nova conexão para a gravação final**
            async with AsyncDatabaseManager(DB_FILE) as conn:
                db_service.save_material(
                    conn=conn,
                    user_id=user_id,
                    automation_request_id=automation_request_id,
                    task_id=task_id,
                    status="GENERATED",
                    theme=theme,
                    content_type=content_type,
                    raw_material_ids=(
                        ",".join(raw_material_ids) if raw_material_ids else ""
                    ),
                    generated_content=json.dumps(generated_data, ensure_ascii=False),
                    suggested_image_prompt=suggested_image_prompt,
                )
                logger.info(f"Material processado com sucesso para task_id {task_id}")
        except Exception as save_err:
            logger.error(f"Erro ao salvar material processado: {str(save_err)}")
            try:
                async with AsyncDatabaseManager(DB_FILE) as conn:
                    db_service.update_material_status(
                        conn=conn,
                        user_id=user_id,
                        task_id=task_id,
                        new_status="FAILED_GENERATION",
                    )
            except Exception as update_err:
                logger.error(
                    f"Erro ao atualizar status para FAILED_GENERATION: {str(update_err)}"
                )

    except Exception as e:
        logger.error(f"Erro ao processar material para task_id {task_id}: {str(e)}")
        try:
            async with AsyncDatabaseManager(DB_FILE) as conn:
                db_service.update_material_status(
                    conn=conn,
                    user_id=user_id,
                    task_id=task_id,
                    new_status="FAILED_PROCESSING",
                )
        except Exception as final_err:
            logger.error(f"Erro final ao atualizar status: {str(final_err)}")


# Rota para salvar o material bruto, sem disparar a automação
@router.post(
    "/trigger-by-url",
    response_model=TriggerResponse,
    tags=["trigger-automation"],
    summary="Acionar automação por URL",
    description="Extrai conteúdo de uma URL específica e salva o material bruto sem iniciar a geração.",
)
async def trigger_by_url(
    request: UrlRequest,
    user: dict = Depends(Auth.verify_token),
):
    """
    Aciona a automação para uma URL, extraindo o conteúdo e salvando
    a tarefa para ser processada posteriormente.
    """
    task_id = str(uuid.uuid4())
    user_id = user.get("sub")

    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found in token")

    try:
        # Usar uma única conexão para todas as operações e garantir que ela seja fechada corretamente
        async with AsyncDatabaseManager(DB_FILE) as conn:
            # Passa a conexão `conn` para as funções de serviço
            automation_request_id = db_service.create_automation_request(
                conn=conn,
                user_id=user_id,
                task_id=task_id,
                url=str(request.url),
                theme=request.theme,
                output_format=request.content_type,
            )

            content = await fetch_url_content(str(request.url))
            if not content:
                db_service.update_material_status(
                    conn=conn,
                    user_id=user_id,
                    task_id=task_id,
                    new_status="COLLECTION_FAILED",
                )
                raise HTTPException(
                    status_code=400, detail="Nenhum conteúdo extraído da URL"
                )

            cleaned_content = clean_html_content(content)
            if not cleaned_content:
                db_service.update_material_status(
                    conn=conn,
                    user_id=user_id,
                    task_id=task_id,
                    new_status="COLLECTION_FAILED",
                )
                raise HTTPException(status_code=400, detail="Conteúdo limpo inválido")

            raw_id = db_service.save_raw_material(
                conn=conn,
                user_id=user_id,
                task_id=task_id,
                url=str(request.url),
                content=cleaned_content,
            )

            db_service.update_material_with_raw_id(
                conn=conn, task_id=task_id, raw_material_id=raw_id
            )

            db_service.update_material_status(
                conn=conn, user_id=user_id, task_id=task_id, new_status="RAW_COLLECTED"
            )

        return TriggerResponse(
            message=f"Material bruto salvo com sucesso. A tarefa está pronta para ser processada. Use o task_id para iniciar a geração.",
            task_id=task_id,
            status="RAW_MATERIAL_SAVED",
        )
    except Exception as e:
        logger.error(f"Erro ao acionar automação por URL: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Erro ao acionar automação: {str(e)}"
        )


# Rota para disparar a geração de conteúdo
@router.post(
    "/generate/{task_id}",
    tags=["generate-content"],
    summary="Gerar conteúdo sob demanda",
    description="Inicia a tarefa de geração de conteúdo para um task_id existente.",
)
async def generate_content_api(
    task_id: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(Auth.verify_token),
):
    """
    Inicia a tarefa de segundo plano para gerar conteúdo.
    """
    try:
        user_id = user.get("sub")
        # Usar o gerenciador de contexto para a conexão
        async with AsyncDatabaseManager(DB_FILE) as conn:
            # Passa a conexão `conn` para a função de serviço
            material = db_service.get_material(
                conn=conn, user_id=user_id, task_id=task_id
            )

            if not material:
                raise HTTPException(status_code=404, detail="Task ID not found.")

        # Dispara a tarefa de segundo plano
        background_tasks.add_task(
            process_material_task,
            user=user,
            automation_request_id=material.get("automation_request_id"),
            task_id=task_id,
        )

        logger.info(f"Tarefa de geração de conteúdo para {task_id} iniciada.")

        return {
            "message": f"Tarefa de geração de conteúdo para {task_id} iniciada com sucesso. Verifique o status para o resultado."
        }
    except HTTPException as e:
        logger.error(f"Erro HTTP na API: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"Erro ao iniciar a tarefa de geração para {task_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Erro interno no servidor.")


@router.post(
    "/trigger-multiple-urls",
    response_model=TriggerResponse,
    tags=["trigger-automation"],
    summary="Extrair conteúdo de múltiplas URLs",
    description="Extrai conteúdo de uma lista de URLs, salva em raw_materials e inicia a geração em segundo plano.",
)
async def trigger_multiple_urls(
    request: ExtractFromUrlsRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(Auth.verify_token),
):
    task_id = str(uuid.uuid4())
    user_id = user.get("sub")
    theme = request.theme or "Desconhecido"
    content_type = request.output_format or Config.OUTPUT_FORMAT

    try:
        # Usa uma única transação para todas as operações
        async with AsyncDatabaseManager(DB_FILE) as conn:
            db_service.save_material(
                conn=conn,
                user_id=user_id,
                automation_request_id=None,
                task_id=task_id,
                status="PENDING_COLLECTION",
                theme=theme,
                content_type=content_type,
            )

            raw_material_ids = []
            for url in request.urls:
                logger.info(f"Tentando extrair conteúdo da URL: {url}")
                content = await fetch_url_content(url)
                if not content:
                    logger.warning(f"Nenhum conteúdo retornado para a URL: {url}")
                    continue

                cleaned_content = clean_html_content(content)
                if cleaned_content:
                    raw_id = db_service.save_raw_material(
                        conn=conn,
                        user_id=user_id,
                        task_id=task_id,
                        url=url,
                        content=cleaned_content,
                    )
                    raw_material_ids.append(raw_id)

            if not raw_material_ids:
                db_service.update_material_status(
                    conn=conn,
                    user_id=user_id,
                    task_id=task_id,
                    new_status="COLLECTION_FAILED",
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
            db_service.update_material_status(
                conn=conn, user_id=user_id, task_id=task_id, new_status="RAW_COLLECTED"
            )

        # Adicione um pequeno atraso para garantir o commit
        await asyncio.sleep(1)

        # Dispara a tarefa de segundo plano passando apenas os IDs
        # background_tasks.add_task(
        #     process_material_task,
        #     user=user,
        #     automation_request_id=None,
        #     task_id=task_id,
        #     raw_material_ids=raw_material_ids,
        # )

        return TriggerResponse(
            trigger_id=None,
            message="Extração de URLs concluída. Geração de conteúdo será iniciada separadamente.",
            task_id=task_id,
            status="RAW_COLLECTED",
        )

    except HTTPException:
        # Propaga o erro HTTP sem alterar
        raise
    except Exception as e:
        logger.error(f"Erro na extração de URLs para task_id {task_id}: {str(e)}")
        # A nova conexão para atualização de status já está no bloco principal
        async with AsyncDatabaseManager(DB_FILE) as conn:
            db_service.update_material_status(
                conn=conn,
                user_id=user_id,
                task_id=task_id,
                new_status="COLLECTION_FAILED",
            )
        raise HTTPException(status_code=500, detail=f"Erro na extração: {str(e)}")


@router.post(
    "/generate-content",
    tags=["generate-content"],
    summary="Gerar conteúdo sob demanda",
    description="Gera conteúdo com base em um task_id existente, usando materiais brutos salvos no banco.",
)
async def generate_content(
    request: GenerateContentRequest,
    user: dict = Depends(Auth.verify_token),
):
    user_id = request.user_id
    task_id = request.task_id
    theme = request.theme or "Desconhecido"
    content_type = request.output_format or Config.OUTPUT_FORMAT

    try:
        material = db_service.get_material(user_id, task_id)
        if not material or not material.get("raw_material_ids"):
            raise HTTPException(
                status_code=404,
                detail="Nenhum material bruto encontrado para o task_id",
            )

        raw_materials = []
        for raw_id in material["raw_material_ids"]:
            content = db_service.get_raw_material(raw_id)
            if content:
                raw_materials.append(content)

        if not raw_materials:
            raise HTTPException(
                status_code=400,
                detail="Nenhum conteúdo válido encontrado nos raw_material_ids",
            )

        compiled_raw_material = "\n\n".join(raw_materials)[: Config.MAX_TEXT_LEN]

        db_service.save_material(
            user_id=user_id,
            automation_request_id=material.get("automation_request_id"),
            task_id=task_id,
            status="PENDING_GENERATION",
            theme=theme,
            content_type=content_type,
            raw_material_ids=material["raw_material_ids"],
        )

        generated_data = await generate_content_with_gemini_service(
            theme=theme,
            raw_material=compiled_raw_material,
            content_type=content_type,
        )

        db_service.save_material(
            user_id=user_id,
            automation_request_id=material.get("automation_request_id"),
            task_id=task_id,
            status="GENERATED",
            theme=theme,
            content_type=content_type,
            raw_material_ids=material["raw_material_ids"],
            generated_content=json.dumps(generated_data, ensure_ascii=False),
            suggested_image_prompt=generated_data.get("suggested_image_prompt"),
        )

        return {
            "message": "Conteúdo gerado com sucesso!",
            "task_id": task_id,
            "status": "GENERATED",
        }

    except Exception as e:
        logger.error(f"Erro na geração de conteúdo para task_id {task_id}: {str(e)}")
        db_service.update_material_status(user_id, task_id, "FAILED_GENERATION")
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
    raw_materials = db_service.get_raw_materials_by_ids(conn, raw_material_ids)

    return {"raw_materials": raw_materials}


# Modelo Pydantic para o corpo da requisição
class ContentInput(BaseModel):
    user_id: str = Field(..., alias="userId")
    text_content: str = Field(..., alias="textContent")
    content_type: str = Field("artigo", alias="contentType")
    theme: str = Field("tema_padrao", alias="theme")

    class Config:
        allow_population_by_field_name = True


@router.post(
    "/trigger-by-text",
    tags=["trigger-automation"],
    summary="Inicia automação a partir de um texto",
    description="Recebe um texto, extrai um tema com a IA, salva no banco e prepara a automação.",
    status_code=200,
)
async def trigger_by_text(
    payload: ContentInput, conn: sqlite3.Connection = Depends(get_db_connection)
):
    """
    Recebe um texto de matéria, extrai um tema com o Gemini,
    salva o material bruto no SQLite e os metadados no PostgreSQL.
    """
    logger.info("Nova requisição recebida na rota /api/trigger-by-text")

    try:
        # 1. Usar a IA para extrair o tema
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-1.5-pro")

        prompt = (
            "A partir do seguinte texto, identifique um único tema principal ou título. "
            "Sua resposta deve ser apenas o tema, sem texto adicional. "
            "Texto: " + payload.text_content[:2000]  # Limita o texto para o prompt
        )

        response = model.generate_content(prompt)
        ai_theme = response.text.strip().replace('"', "")

        logger.info(f"Tema extraído pela IA: '{ai_theme}'")

        # 2. Salvar o material bruto no SQLite
        raw_material_id = str(uuid.uuid4())

        db_service.save_raw_material(
            conn=conn,
            raw_material_id=raw_material_id,
            url=None,  # Não há URL neste fluxo
            content=payload.text_content,
        )
        logger.info(f"Material bruto salvo no SQLite com ID: {raw_material_id}")

        # 3. Preparar e salvar os dados no PostgreSQL
        task_id = str(uuid.uuid4())

        postgres_payload = {
            "user_id": payload.user_id,
            "automation_request_id": str(uuid.uuid4()),
            "task_id": task_id,
            "status": "RAW_COLLECTED",
            "theme": ai_theme,
            "content_type": payload.content_type,
            "raw_material_ids": [raw_material_id],  # Salva a lista de IDs brutos
        }

        saved_record = pg_service.save_automation_data_to_postgres(postgres_payload)

        if not saved_record:
            raise HTTPException(
                status_code=500, detail="Erro ao salvar dados no PostgreSQL."
            )

        # 4. Retornar a resposta
        return {
            "message": "Tema gerado e dados salvos. Use o task_id para iniciar a automação.",
            "task_id": task_id,
            "theme": ai_theme,
            "automation_request_id": saved_record.get("automation_request_id"),
        }

    except HTTPException:
        # Propaga o erro HTTP
        raise
    except Exception as e:
        logger.error(f"Erro na automação por texto: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Erro interno no servidor: {str(e)}"
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

        return JSONResponse(
            content={
                "message": "Automação executada com sucesso!",
                "report_summary": output_report,
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
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@router.get(
    "/trigger-by-id/{id}",
    response_model=TriggerResponse,
    tags=["trigger-automation"],
    summary="Acionar automação por ID",
    description="Inicia a coleta de material bruto em segundo plano com base em um ID de requisição existente.",
)
async def trigger_by_id(
    id: int,
    background_tasks: BackgroundTasks,
    user_payload: dict = Depends(Auth.verify_token),
    db: Session = Depends(get_db),
):
    logger.info(
        f"Endpoint /trigger-by-id/{id} acionado por {user_payload.get('sub', 'Desconhecido')}."
    )
    user_id = user_payload.get("sub", "anonymous_user")
    task_id = str(uuid.uuid4())

    try:
        request_entry = (
            db.query(AutomationRequest).filter(AutomationRequest.id == id).first()
        )
        if not request_entry:
            logger.warning(f"Registro com ID {id} não encontrado.")
            if Config.LOGS_API_URL:
                log_data = {
                    "action": f"Falha ao executar automação para ID {id}: Registro não encontrado.",
                    "timestamp": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", ""),
                    "level": "WARNING",
                    "report_id": task_id,
                }
                send_logs_to_backend(log_data)
            raise HTTPException(
                status_code=404, detail=f"Registro com ID {id} não encontrado"
            )

        output_format = request_entry.output_format
        theme = request_entry.theme
        logger.info(
            f"Parâmetros do DB: output_format='{output_format}', theme='{theme}'"
        )

        db_service.save_material(
            user_id=user_id,
            task_id=task_id,
            theme=theme,
            raw_material="",
            source_urls=[],
            content_type=output_format,
            status="PENDING_COLLECTION",
        )
        logger.info(f"Tarefa '{task_id}' salva para coleta.")

        background_tasks.add_task(
            run_automation,
            output_format=output_format,
            theme=theme,
            auth_headers={"Authorization": f"Bearer {user_payload.get('token')}"},
            user_id=user_id,
            task_id=task_id,
            return_raw_material_only=True,
        )

        return TriggerResponse(
            trigger_id=id,
            message="Coleta de material iniciada em segundo plano.",
            task_id=task_id,
            status="PENDING_COLLECTION",
        )

    except HTTPException as http_exc:
        logger.error(f"Erro HTTP para ID {id}: {http_exc.detail}")
        if Config.LOGS_API_URL:
            log_data = {
                "action": f"Falha na automação para ID {id}. Erro: {http_exc.detail}",
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", ""),
                "level": "ERROR",
                "report_id": task_id,
            }
            send_logs_to_backend(log_data)
        raise
    except Exception as e:
        logger.error(f"Erro inesperado para ID {id}: {str(e)}")
        if Config.LOGS_API_URL:
            log_data = {
                "action": f"Erro inesperado na automação para ID {id}. Erro: {str(e)}",
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", ""),
                "level": "CRITICAL",
                "report_id": task_id,
            }
            send_logs_to_backend(log_data)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


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
        conn.close()

        posts = []
        for row in materials:
            status = row["status"]
            status_label = (
                "Finalizado"
                if status == "COMPLETED"
                else (
                    "Em andamento"
                    if status in ("PENDING", "RAW_COLLECTED")
                    else "Falhou"
                )
            )
            status_color = (
                "green"
                if status == "COMPLETED"
                else "yellow" if status in ("PENDING", "RAW_COLLECTED") else "red"
            )
            gradient = (
                "from-indigo-500 to-purple-500"
                if status == "COMPLETED"
                else (
                    "from-yellow-400 to-orange-400"
                    if status in ("PENDING", "RAW_COLLECTED")
                    else "from-red-400 to-pink-500"
                )
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
                    "statusLabel": status_label,
                    "statusColor": status_color,
                    "tags": [row["content_type"] or "geral"],
                    "gradient": gradient,
                    "panelDetails": None,
                }
            )
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
            raise HTTPException(status_code=404, detail="Post não encontrado")

        cursor.execute("SELECT * FROM raw_materials WHERE task_id = ?", (task_id,))
        raw_materials = cursor.fetchall()
        conn.close()

        raw_texts = [r["content"] for r in raw_materials]
        raw_material_combined = (
            "\n\n".join(raw_texts) if raw_texts else "(sem material bruto)"
        )

        status = material["status"]
        status_label = (
            "Finalizado"
            if status == "COMPLETED"
            else (
                "Em andamento" if status in ("PENDING", "RAW_COLLECTED") else "Falhou"
            )
        )
        status_color = (
            "green"
            if status == "COMPLETED"
            else "yellow" if status in ("PENDING", "RAW_COLLECTED") else "red"
        )

        post = {
            "id": material["task_id"],
            "title": material["theme"],
            "subtitle": f"Tipo: {material['content_type'] or 'Desconhecido'}",
            "statusLabel": status_label,
            "statusColor": status_color,
            "tags": [material["content_type"] or "Geral"],
            "date": material["created_at"][:10] if material["created_at"] else "",
            "description": (material["generated_content"] or "Aguardando geração...")[
                :300
            ],
            "panelDetails": {
                "raw_material": raw_material_combined,
                "generated_content_preview": {
                    "summary": (material["generated_content"] or "sem conteúdo gerado")[
                        :500
                    ],
                    "topics": ["Introdução", "Análise", "Conclusão"],
                },
                "image_prompt": material["suggested_image_prompt"]
                or "sem prompt definido",
                "logs": [
                    f"Status atual: {status_label}",
                    f"Atualizado em: {material['updated_at']}",
                    f"Task ID: {task_id}",
                ],
            },
        }

        return post

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Erro ao buscar post {task_id}: {str(e)}"
        )


@router.post(
    "/list-raw-materials-by-ids",
    tags=["Automação"],
    summary="Listar materiais brutos por IDs (conteúdo resumido)",
    description="Recebe uma lista de IDs e retorna os registros correspondentes da tabela raw_materials, limitando o campo content para melhor performance.",
)
async def list_raw_materials_by_ids(request: RawMaterialRequest):
    """
    Exemplo de requisição:
    {
      "ids": ["56598d6c-1b61-4a4d-9bf5-a2ed74f0ca6e", "253be6c8-8762-41b4-a218-5682434d42f5"]
    }
    """
    try:
        if not request.ids:
            raise HTTPException(status_code=400, detail="Lista de IDs vazia.")

        conn = sqlite3.connect(db_service.DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        placeholders = ",".join("?" * len(request.ids))
        query = f"SELECT * FROM raw_materials WHERE id IN ({placeholders})"
        cursor.execute(query, request.ids)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return []

        MAX_CONTENT_LENGTH = 1000  # 👈 limite de caracteres exibidos
        result = []
        for row in rows:
            content = row["content"] or ""
            preview = (
                content[:MAX_CONTENT_LENGTH].rstrip() + "..."
                if len(content) > MAX_CONTENT_LENGTH
                else content
            )

            result.append(
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "task_id": row["task_id"],
                    "url": row["url"],
                    "content": preview,
                    "created_at": row["created_at"],
                }
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Erro ao buscar materiais por IDs: {str(e)}"
        )


@router.put(
    "/update-raw-material-content/{id}",
    tags=["Automação"],
    summary="Atualizar conteúdo de um material bruto (com histórico)",
    description="Atualiza o campo 'content' da tabela raw_materials, registrando a versão anterior em raw_materials_history.",
)
async def update_raw_material_content(id: str, request: UpdateRawMaterialRequest):
    """
    Exemplo de requisição:
    PUT /api/update-raw-material-content/{id}
    {
      "content": "Novo texto atualizado do material bruto.",
      "user_id": "admin@dailybrief.com"
    }
    """
    try:
        conn = sqlite3.connect(db_service.DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Verifica se o material existe
        cursor.execute("SELECT * FROM raw_materials WHERE id = ?", (id,))
        material = cursor.fetchone()
        if not material:
            conn.close()
            raise HTTPException(
                status_code=404, detail=f"Material com ID {id} não encontrado."
            )

        old_content = material["content"]
        user_id = request.user_id or material["user_id"]
        task_id = material["task_id"]

        # Salva o conteúdo anterior no histórico
        cursor.execute(
            """
            INSERT INTO raw_materials_history (raw_material_id, old_content, updated_at, user_id, task_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                id,
                old_content,
                datetime.now().isoformat(),
                user_id,
                task_id,
            ),
        )

        # Atualiza o conteúdo principal e o campo updated_at
        cursor.execute(
            """
            UPDATE raw_materials
            SET content = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                request.content,
                datetime.now().isoformat(),
                id,
            ),
        )

        conn.commit()
        conn.close()

        return {
            "message": "Conteúdo atualizado com sucesso.",
            "id": id,
            "history_saved": True,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Erro ao atualizar conteúdo: {str(e)}"
        )
