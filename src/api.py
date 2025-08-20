# src/api.py
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

logger = logging.getLogger(__name__)

router = APIRouter()

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
    automation_request_id: Optional[int] = None
    task_id: str
    status: str
    theme: Optional[str] = None
    content_type: Optional[str] = None
    raw_material: Optional[Union[str, Dict[str, Any]]] = None
    generated_content: Optional[Union[str, Dict[str, Any]]] = None
    suggested_image_prompt: Optional[str] = None
    created_at: str
    updated_at: str


class TriggerRequest(BaseModel):
    output_format: str = Config.OUTPUT_FORMAT
    theme: Optional[str] = None


class TriggerResponse(BaseModel):
    trigger_id: Optional[int] = None
    message: str
    task_id: str
    status: str


class ExtractFromUrlsRequest(BaseModel):
    urls: List[str]
    theme: Optional[str] = None
    output_format: Optional[str] = None
    user_id: str


class GenerateContentRequest(BaseModel):
    task_id: str
    user_id: str
    theme: Optional[str] = None
    output_format: Optional[str] = None


class UrlRequest(BaseModel):
    url: HttpUrl = Field(..., description="A URL da qual o conteúdo será extraído.")
    theme: str = Field(
        ..., min_length=3, description="O tema a ser usado para a geração do conteúdo."
    )
    content_type: str = Field(
        "summary",
        description="O tipo de conteúdo a ser gerado (ex: 'summary', 'article', 'social_media_post').",
    )


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
        generated_data = json.loads(response.text)
        logger.info(f"Conteúdo gerado para o tema '{theme}' com tipo '{content_type}'.")
        return generated_data
    except Exception as e:
        logger.error(f"Erro ao gerar conteúdo com Gemini: {str(e)}")
        raise


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
    tags=["Automação"],
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
    tags=["Automação"],
    summary="Submeter post final",
    description="Envia o post final para o backend e opcionalmente deleta a task associada.",
)
async def submit_final_post(
    request: SubmitFinalPostRequest,
    user: dict = Depends(Auth.verify_token),
):
    try:
        headers = {"Authorization": f"Bearer {user['payload'].get('token')}"}
        response = await send_post(request.post_data, headers)

        if request.task_id_to_delete:
            db_service.delete_material(
                user["payload"]["sub"], request.task_id_to_delete
            )
            logger.info(
                f"Task {request.task_id_to_delete} deletada após envio do post."
            )

        return {"message": "Post enviado com sucesso!", "response": response}
    except Exception as e:
        logger.error(f"Erro ao submeter post final: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao submeter post: {str(e)}")


@router.get(
    "/get_task_result",
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
        material = db_service.get_material(user["payload"]["sub"], task_id)
        if not material:
            raise HTTPException(status_code=404, detail="Task não encontrada")

        return MaterialResponse(
            user_id=user["payload"]["sub"],
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
    except Exception as e:
        logger.error(f"Erro ao obter resultado da task {task_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao obter task: {str(e)}")


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
    automation_request_id: Optional[int],
    task_id: str,
):
    """Processa materiais brutos para gerar conteúdo e enviar ao backend."""
    user_id = user["payload"]["sub"]
    try:
        material = db_service.get_material(user_id, task_id)
        if not material or not material.get("raw_material_ids"):
            logger.error(f"Nenhum material bruto encontrado para task_id {task_id}")
            db_service.update_material_status(user_id, task_id, "FAILED_NO_MATERIAL")
            return

        raw_materials = []
        for raw_id in material["raw_material_ids"]:
            content = db_service.get_raw_material(raw_id)
            if content:
                raw_materials.append(content)

        if not raw_materials:
            logger.error(f"Nenhum conteúdo válido para task_id {task_id}")
            db_service.update_material_status(user_id, task_id, "FAILED_NO_CONTENT")
            return

        compiled_raw_material = "\n\n".join(raw_materials)[: Config.MAX_TEXT_LEN]
        theme = material.get("theme", "Desconhecido")
        content_type = material.get("content_type", Config.OUTPUT_FORMAT)

        generated_data = await generate_content_with_gemini_service(
            theme=theme,
            raw_material=compiled_raw_material,
            content_type=content_type,
        )

        db_service.save_material(
            user_id=user_id,
            automation_request_id=automation_request_id,
            task_id=task_id,
            status="GENERATED",
            theme=theme,
            content_type=content_type,
            raw_material_ids=material["raw_material_ids"],
            generated_content=json.dumps(generated_data, ensure_ascii=False),
            suggested_image_prompt=generated_data.get("suggested_image_prompt"),
        )

        headers = {"Authorization": f"Bearer {user['payload'].get('token')}"}
        post_response = await send_post(generated_data, headers)

        db_service.update_material_status(user_id, task_id, "POSTED")
        logger.info(f"Post enviado para task_id {task_id}")

    except Exception as e:
        logger.error(f"Erro ao processar material para task_id {task_id}: {str(e)}")
        db_service.update_material_status(user_id, task_id, "FAILED_GENERATION")
        if Config.LOGS_API_URL:
            log_data = {
                "action": f"Erro ao processar material para task_id {task_id}: {str(e)}",
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", ""),
                "level": "ERROR",
                "report_id": task_id,
            }
            send_logs_to_backend(log_data)


@router.post(
    "/trigger-by-url",
    response_model=TriggerResponse,
    tags=["Automação"],
    summary="Acionar automação por URL",
    description="Extrai conteúdo de uma URL específica e inicia a geração de conteúdo em segundo plano.",
)
async def trigger_by_url(
    request: UrlRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(Auth.verify_token),
    db: Session = Depends(get_db),
):
    task_id = str(uuid.uuid4())
    user_id = user["payload"]["sub"]
    try:
        content = await fetch_url_content(str(request.url))
        if not content:
            db_service.update_material_status(user_id, task_id, "COLLECTION_FAILED")
            raise HTTPException(
                status_code=400, detail="Nenhum conteúdo extraído da URL"
            )

        cleaned_content = clean_html_content(content)
        if not cleaned_content:
            db_service.update_material_status(user_id, task_id, "COLLECTION_FAILED")
            raise HTTPException(status_code=400, detail="Conteúdo limpo inválido")

        db_service.save_material(
            user_id=user_id,
            automation_request_id=None,
            task_id=task_id,
            status="PENDING_COLLECTION",
            theme=request.theme,
            content_type=request.content_type,
        )

        raw_id = db_service.save_raw_material(task_id, cleaned_content)
        db_service.update_material_raw_material_ids(user_id, task_id, [raw_id])
        db_service.update_material_status(user_id, task_id, "RAW_COLLECTED")

        request_id = str(uuid.uuid4())
        request_details = {
            "url": str(request.url),
            "theme": request.theme,
            "content_type": request.content_type,
        }
        automation_request = await db_service.create_automation_request(
            db=db,
            request_id=request_id,
            user_id=user_id,
            request_type="URL_TRIGGER",
            request_details=request_details,
            status="PENDING",
        )

        background_tasks.add_task(
            process_material_task,
            user=user,
            automation_request_id=automation_request.id,
            task_id=task_id,
        )

        return TriggerResponse(
            trigger_id=automation_request.id,
            message=f"Automação acionada para URL {request.url}.",
            task_id=task_id,
            status="PENDING_GENERATION",
        )
    except Exception as e:
        logger.error(f"Erro ao acionar automação por URL: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Erro ao acionar automação: {str(e)}"
        )


@router.post(
    "/extract-from-urls",
    response_model=TriggerResponse,
    tags=["Automação"],
    summary="Extrair conteúdo de múltiplas URLs",
    description="Extrai conteúdo de uma lista de URLs, salva em raw_materials e inicia geração em segundo plano.",
)
async def extract_from_urls(
    request: ExtractFromUrlsRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(Auth.verify_token),
):
    task_id = str(uuid.uuid4())
    user_id = request.user_id
    theme = request.theme or "Desconhecido"
    content_type = request.output_format or Config.OUTPUT_FORMAT

    try:
        db_service.save_material(
            user_id=user_id,
            automation_request_id=None,
            task_id=task_id,
            status="PENDING_COLLECTION",
            theme=theme,
            content_type=content_type,
        )

        raw_material_ids = []
        for url in request.urls:
            content = await fetch_url_content(url)
            if content:
                cleaned_content = clean_html_content(content)
                if cleaned_content:
                    raw_id = db_service.save_raw_material(task_id, cleaned_content)
                    raw_material_ids.append(raw_id)

        if not raw_material_ids:
            db_service.update_material_status(user_id, task_id, "COLLECTION_FAILED")
            raise HTTPException(
                status_code=400, detail="Nenhum conteúdo extraído das URLs fornecidas"
            )

        db_service.update_material_raw_material_ids(user_id, task_id, raw_material_ids)
        db_service.update_material_status(user_id, task_id, "RAW_COLLECTED")

        background_tasks.add_task(
            process_material_task,
            user=user,
            automation_request_id=None,
            task_id=task_id,
        )

        return TriggerResponse(
            trigger_id=None,
            message="Extração de URLs concluída. Geração iniciada em segundo plano.",
            task_id=task_id,
            status="PENDING_GENERATION",
        )

    except Exception as e:
        logger.error(f"Erro na extração de URLs para task_id {task_id}: {str(e)}")
        db_service.update_material_status(user_id, task_id, "COLLECTION_FAILED")
        raise HTTPException(status_code=500, detail=f"Erro na extração: {str(e)}")


@router.post(
    "/generate-content",
    tags=["Automação"],
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
