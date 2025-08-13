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
    extract_content_by_selectors,
    get_parent_selector_func,
    clean_html_content,
    save_selector_data,
)
from src.database import get_db
from src.models import AutomationRequest
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)

# --- Crie uma instância de APIRouter ---
# Este é o roteador que será incluído na aplicação FastAPI principal em src/server.py
router = APIRouter()

# --- Configuração da API Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError(
        "A chave GEMINI_API_KEY não foi encontrada. Verifique seu arquivo .env"
    )
genai.configure(api_key=GEMINI_API_KEY)

# --- ESQUEMAS DE DADOS E MODELOS PYDANTIC ---
# Esquema para o formato de saída do Gemini
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
        "seo_keywords": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Palavras-chave de SEO",
        },
        "read_time_minutes": {
            "type": "number",
            "description": "Tempo de leitura estimado em minutos",
        },
        "suggested_image_prompt": {
            "type": "string",
            "description": "Sugestão de prompt para geração de imagem, baseada no conteúdo do artigo.",
        },
    },
    "required": [
        "title",
        "excerpt",
        "content",
        "metaDescription",
        "tags",
        "category",
        "seo_keywords",
        "read_time_minutes",
        "suggested_image_prompt",
    ],
}

# Esquema para o formato de saída do Gemini para "resumo de notícias"
NEWS_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Título do resumo de notícias"},
        "summary": {"type": "string", "description": "Resumo conciso das notícias"},
        "key_points": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Pontos chave das notícias",
        },
        "source_references": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["title", "url"],
            },
            "description": "Referências das fontes das notícias",
        },
        "category": {
            "type": "string",
            "description": "Categoria principal das notícias",
        },
        "suggested_image_prompt": {
            "type": "string",
            "description": "Sugestão de prompt para geração de imagem, baseada no conteúdo do resumo.",
        },
    },
    "required": [
        "title",
        "summary",
        "key_points",
        "source_references",
        "category",
        "suggested_image_prompt",
    ],
}


# Modelos Pydantic para validação de entrada/saída
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


# --- FUNÇÕES AUXILIARES (NÃO SÃO ENDPOINTS, MAS SÃO USADAS POR ELES) ---


def get_gemini_model():
    """Retorna o modelo Gemini configurado."""
    return genai.GenerativeModel("gemini-1.5-flash")


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def generate_content_with_gemini_service(
    theme: str,
    raw_material: str,
    content_type: str = "summary",
    keywords: Optional[List[str]] = None,
    tone: Optional[str] = None,
    cta_instruction: Optional[str] = None,
    article_structure: Optional[List[str]] = None,
    audience: Optional[str] = None,
    ideal_article_example: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Gera conteúdo usando a API Gemini com base no prompt e tipo de conteúdo.
    Valida a saída contra o esquema apropriado.
    """
    model = get_gemini_model()
    content_instructions = {
        "summary": {
            "description": "Gere um resumo conciso e informativo, com 3-5 parágrafos (aprox. 200-400 palavras). Use tags <p> para parágrafos.",
            "min_paragraphs": 3,
            "max_paragraphs": 5,
            "min_words": 200,
            "max_words": 400,
        },
        "article": {
            "description": "Gere um artigo detalhado e aprofundado, com 8-15 parágrafos (aprox. 800-1500 palavras). Use tags <p> para parágrafos e tags <h2>, <h3> para subtítulos.",
            "min_paragraphs": 8,
            "max_paragraphs": 15,
            "min_words": 800,
            "max_words": 1500,
        },
        "social": {
            "description": "Gere um post curto e envolvente para redes sociais (máximo 3 parágrafos, aprox. 100-250 palavras). Use tags <p> para parágrafos.",
            "min_paragraphs": 1,
            "max_paragraphs": 3,
            "min_words": 100,
            "max_words": 250,
        },
        "informative": {
            "description": "Gere um texto informativo, com 5-10 parágrafos (aprox. 500-800 palavras), mesclando diferentes aspectos do tema. Use tags <p> para parágrafos e, se necessário, tags <ul> ou <ol> para listas.",
            "min_paragraphs": 5,
            "max_paragraphs": 10,
            "min_words": 500,
            "max_words": 800,
        },
        "news_brief": {
            "description": "Gere uma notícia breve e objetiva, focando nos fatos (quem, o quê, onde, quando, por que). Use 2-4 parágrafos (aprox. 150-300 palavras).",
            "min_paragraphs": 2,
            "max_paragraphs": 4,
            "min_words": 150,
            "max_words": 300,
        },
        "how_to_guide": {
            "description": "Gere um guia passo a passo detalhado. Use uma introdução, seções com <h2> para cada passo, e listas numeradas (<ol>) para as instruções. Inclua uma conclusão. (Aprox. 700-1200 palavras).",
            "min_paragraphs": 10,
            "max_paragraphs": 20,
            "min_words": 700,
            "max_words": 1200,
        },
        "listicle": {
            "description": "Gere um artigo em formato de lista (listicle). Inclua uma introdução, 5 a 10 itens de lista com <h2> para cada item, e um breve parágrafo para cada item. Finalize com uma conclusão. (Aprox. 600-1000 palavras).",
            "min_paragraphs": 8,
            "max_paragraphs": 15,
            "min_words": 600,
            "max_words": 1000,
        },
    }
    selected_instruction = content_instructions.get(
        content_type, content_instructions["informative"]
    )
    base_description = selected_instruction["description"]

    prompt_parts = [
        f"Com base no seguinte material bruto, gere conteúdo para um post de blog sobre '{theme}'.",
        "",
        "Instruções Detalhadas para a Estrutura de Saída (Formato JSON):",
        "1. O campo 'content' (para PT, EN, ES) DEVE ser formatado como HTML válido, apenas com o conteúdo do artigo. NÃO inclua tags <html>, <head> ou <body>.",
        "2. NÃO inclua classes CSS, IDs ou estilos inline nas tags HTML.",
        "3. Os campos 'title', 'excerpt' e 'metaDescription' DEVEM ser texto puro, sem tags HTML.",
        "4. O campo 'suggestedImagePrompt' DEVE ser um texto puro, conciso e descritivo, ideal para gerar uma imagem que represente visualmente o artigo. Pense em elementos visuais chave do tema e do conteúdo.",
        "",
        "Diretrizes de Qualidade e SEO (Otimização para Motores de Busca com foco em monetização via AdSense):",
        "- O conteúdo deve ser envolvente, natural, fluído e coeso. Evite frases repetitivas ou genéricas.",
        "- Mantenha um tone de voz consistente e adequado ao público-alvo.",
        "- Integre as palavras-chave de forma natural ao longo do texto para otimização de SEO.",
        "- Estruture o conteúdo em parágrafos de tamanho moderado (3-5 frases por parágrafo) para facilitar a inserção de anúncios do AdSense sem quebrar o fluxo de leitura. Evite blocos de texto muito longos ou muito curtos.",
        "- Se referenciar dados, estudos ou fontes, integre a informação no texto e mencione a fonte (ex: 'Segundo um estudo da Universidade X...', 'Conforme publicado no portal Y...'). Evite incluir URLs brutas diretamente no corpo do 'content'.",
        "- Crie um conteúdo que naturalmente apresente oportunidades para a futura inserção de links de afiliados (cursos, produtos), sem que você os insira diretamente. Pense em como o texto pode introduzir ou discutir tópicos onde esses links fariam sentido.",
        "- Otimize o título e a meta descrição para atrair cliques em motores de busca, sendo concisos e relevantes.",
    ]

    if tone:
        prompt_parts.append(f"- Adote um tone de voz: {tone}.")
    if keywords:
        prompt_parts.append(
            f"- Palavras-chave a serem integradas naturalmente: {', '.join(keywords)}."
        )
    if audience:
        prompt_parts.append(
            f"- O público-alvo principal deste artigo é: {audience}. Adapte a linguagem e a profundidade para este grupo."
        )
    if ideal_article_example:
        prompt_parts.append(
            f"- Considere o estilo, tone e estrutura do seguinte exemplo de artigo ideal: {ideal_article_example}"
        )
    if article_structure:
        structure_text = "\n".join([f"  - {item}" for item in article_structure])
        prompt_parts.append(
            f"- Estruture o 'content' com base nos seguintes tópicos, usando <h2> para os principais e <h3> para subtópicos, se necessário:\n{structure_text}"
        )
    else:
        prompt_parts.append(f"- {base_description}")
    if cta_instruction:
        prompt_parts.append(
            f"- Inclua uma chamada para ação (Call to Action) no final do 'content': '{cta_instruction}'."
        )

    prompt_parts.append("")
    prompt_parts.append(
        "O resultado final deve ser um objeto JSON, seguindo o esquema fornecido."
    )
    prompt_parts.append(
        "GARANTA QUE CARACTERES ESPECIAIS E ACENTUADOS SEJAM PRESERVADOS CORRETAMENTE EM UTF-8."
    )
    prompt_parts.append("")
    prompt_parts.append("Material Bruto:")
    prompt_parts.append(raw_material[:30000])

    prompt = "\n".join(prompt_parts)
    logger.debug(f"Prompt final enviado ao Gemini:\n{prompt}")

    try:
        response = await model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                response_schema=GEMINI_OUTPUT_SCHEMA,
                temperature=0.7,
            ),
        )
        logger.debug(f"Resposta bruta do Gemini: {response.text}")
        return json.loads(response.text)
    except json.JSONDecodeError as e:
        logger.error(
            f"Erro ao decodificar JSON do Gemini: {e}. Resposta: {response.text}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Resposta inválida do Gemini: Não é um JSON válido. Erro: {e}",
        )
    except Exception as e:
        logger.error(f"Erro na geração de conteúdo com Gemini: {e}")
        raise HTTPException(
            status_code=500, detail=f"Erro na geração de conteúdo com Gemini: {e}"
        )


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def generate_image_with_imagen(image_prompt: str) -> str:
    """
    Gera uma imagem usando a API Imagen 3.0 e retorna a imagem como string Base64.
    """
    api_key = ""  # Leave as-is
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key={api_key}"
    payload = {"instances": {"prompt": image_prompt}, "parameters": {"sampleCount": 1}}
    logger.info(
        f"Iniciando geração de imagem com Imagen 3.0 para o prompt: '{image_prompt}'"
    )
    try:
        response = requests.post(
            api_url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=Config.REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()
        if (
            result.get("predictions")
            and len(result["predictions"]) > 0
            and result["predictions"][0].get("bytesBase64Encoded")
        ):
            image_base64 = result["predictions"][0]["bytesBase64Encoded"]
            image_url = f"data:image/png;base64,{image_base64}"
            logger.info("Imagem gerada com sucesso em Base64.")
            return image_url
        else:
            logger.error(f"Resposta inválida da API Imagen 3.0: {result}")
            raise HTTPException(
                status_code=500, detail="Resposta inválida da API Imagen 3.0."
            )
    except requests.exceptions.Timeout:
        logger.error(
            f"Timeout ao gerar imagem com Imagen 3.0 para o prompt: '{image_prompt}'",
            exc_info=True,
        )
        raise HTTPException(status_code=504, detail="Timeout ao gerar imagem com IA.")
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Erro HTTP/Requisição ao gerar imagem com Imagen 3.0: {e}", exc_info=True
        )
        detail = f"Erro na API Imagen 3.0: {e.response.text}" if e.response else str(e)
        raise HTTPException(
            status_code=e.response.status_code if e.response else 500, detail=detail
        )
    except Exception as e:
        logger.error(
            f"Erro inesperado ao gerar imagem com Imagen 3.0: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail=f"Erro interno ao gerar imagem: {e}"
        )


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def send_post(post_data: Dict[str, Any], headers: Dict[str, str]):
    """Envia o post final para o backend principal."""
    backend_url = Config.get_backend_url()
    try:
        response = requests.post(
            f"{backend_url}/api/posts", json=post_data, headers=headers
        )
        response.raise_for_status()
        return response
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao enviar post para o backend principal: {e}")
        if e.response:
            logger.error(f"Resposta de erro do backend: {e.response.text}")
        raise


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def send_logs_to_backend(log_data: Dict[str, Any], headers: Dict[str, str]):
    """
    Envia dados de log para o backend de logs.
    """
    url = Config.LOGS_API_URL
    if not url:
        logger.warning("LOGS_API_URL não configurada. Não é possível enviar logs.")
        return
    try:
        response = requests.post(
            url, json=log_data, headers=headers, timeout=Config.REQUEST_TIMEOUT
        )
        response.raise_for_status()
        logger.info(
            f"Logs enviados com sucesso para {url}. Status: {response.status_code}"
        )
    except requests.exceptions.Timeout:
        logger.error(f"Timeout ao enviar logs para {url}")
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao enviar logs para {url}: {e}")
        if e.response is not None:
            logger.error(f"Resposta do erro: {e.response.text}")
        raise


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def get_existing_posts(headers: Dict[str, str]) -> List[str]:
    """
    Busca os títulos de posts existentes no backend Spring Boot para verificar duplicidade.
    Retorna uma lista de títulos em português (PT).
    """
    url = f"{Config.SPRING_BOOT_API_URL}/api/posts"
    logger.info(f"Buscando posts existentes de: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=Config.REQUEST_TIMEOUT)
        response.raise_for_status()
        posts = response.json()
        existing_titles_pt = []
        for post in posts:
            if (
                "title" in post
                and isinstance(post["title"], dict)
                and "PT" in post["title"]
            ):
                existing_titles_pt.append(post["title"]["PT"].strip())
        logger.info(
            f"Encontrados {len(existing_titles_pt)} títulos de posts existentes."
        )
        return existing_titles_pt
    except requests.exceptions.Timeout:
        logger.error(f"Timeout ao buscar posts existentes de {url}")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao buscar posts existentes de {url}: {e}")
        if e.response is not None:
            logger.error(f"Resposta do erro: {e.response.text}")
        return []
    except Exception as e:
        logger.error(
            f"Erro inesperado ao processar posts existentes: {e}", exc_info=True
        )
        return []


async def fetch_automation_request_details_from_spring_boot(
    automation_request_id: int, user_token: str
) -> AutomationRequestDetails:
    """
    Busca os detalhes de uma requisição de automação (tema, tipo de conteúdo)
    do backend Spring Boot.
    """
    backend_url = Config.get_backend_url()
    headers = {"Authorization": f"Bearer {user_token}"}
    url = f"{backend_url}/api/automation-requests/{automation_request_id}"

    logger.info(f"Buscando detalhes da automação do Spring Boot: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=Config.REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Detalhes da automação recebidos: {data}")
        return AutomationRequestDetails(**data)
    except requests.exceptions.Timeout:
        logger.error(
            f"Timeout ao buscar detalhes da automação do Spring Boot para ID: {automation_request_id}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=504, detail="Timeout ao buscar detalhes da automação."
        )
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Erro HTTP/Requisição ao buscar detalhes da automação do Spring Boot: {e}",
            exc_info=True,
        )
        detail = (
            f"Erro ao buscar detalhes da automação: {e.response.text}"
            if e.response
            else str(e)
        )
        raise HTTPException(
            status_code=e.response.status_code if e.response else 500, detail=detail
        )
    except Exception as e:
        logger.error(
            f"Erro inesperado ao buscar detalhes da automação do Spring Boot: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail=f"Erro interno ao buscar detalhes da automação: {e}"
        )


def _save_material_process_initial_data(
    user_id: str,
    automation_request_id: Optional[int],
    task_id: str,
    status: str,
    theme: Optional[str] = None,
    content_type: Optional[str] = None,
):
    """Salva os dados iniciais do processo de material no SQLite."""
    try:
        db_service.save_material(
            user_id,
            automation_request_id,
            task_id,
            status,
            theme,
            content_type,
            None,
            None,
            None,
            datetime.now(timezone.utc).isoformat().replace("+00:00", ""),
            datetime.now(timezone.utc).isoformat().replace("+00:00", ""),
        )
        logger.info(
            f"Dados iniciais do MaterialProcess salvos para user_id: {user_id}, task_id: {task_id}"
        )
    except Exception as e:
        logger.error(
            f"Erro ao salvar dados iniciais do MaterialProcess no SQLite: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Erro interno ao salvar dados")


async def process_material_task(
    user: Dict[str, Any],
    automation_request_id: Optional[int],
    task_id: str,
):
    """
    Tarefa em segundo plano para coletar material bruto e gerar conteúdo.
    Busca o tema e o tipo de conteúdo do banco de dados PostgreSQL via Spring Boot.
    """
    user_id = user["payload"].get("sub", "anonymous_user")
    user_token = user["token"]
    logger.info(
        f"Iniciando process_material_task para user_id: {user_id}, task_id: {task_id}, automation_request_id: {automation_request_id}"
    )
    theme = None
    content_type = None

    if automation_request_id:  # Só tenta buscar do Spring Boot se tiver um ID
        try:
            automation_details = (
                await fetch_automation_request_details_from_spring_boot(
                    automation_request_id, user_token
                )
            )
            theme = automation_details.theme
            content_type = automation_details.outputFormat
            db_service.update_material_theme_and_content_type(
                user_id, task_id, theme, content_type
            )
            logger.info(
                f"Tema e tipo de conteúdo atualizados no SQLite para task_id: {task_id}"
            )
        except HTTPException as e:
            logger.error(
                f"Falha ao obter detalhes da automação do Spring Boot: {e.detail}"
            )
            db_service.update_material_status(user_id, task_id, "FETCH_DETAILS_FAILED")
            return
        except Exception as e:
            logger.error(
                f"Erro inesperado ao buscar detalhes da automação: {e}", exc_info=True
            )
            db_service.update_material_status(user_id, task_id, "FETCH_DETAILS_FAILED")
            return
    else:  # Se não houver automation_request_id, usa o tema e content_type já salvos ou padrões
        material_data = db_service.get_material(user_id, task_id)
        if material_data:
            theme = material_data.get("theme")
            content_type = material_data.get("content_type")

    if not theme or not content_type:
        logger.error(f"Tema ou tipo de conteúdo não obtidos para task_id: {task_id}.")
        db_service.update_material_status(user_id, task_id, "FETCH_DETAILS_FAILED")
        return

    try:
        search_query = theme
        url_to_scrape = "https://www.tecmundo.com.br/inteligencia-artificial/290352-chatgpt-agora-faz-tudo-voce-quase-tudo.htm"
        saved_selectors = db_service.get_selectors_by_url(user_id, url_to_scrape)
        if saved_selectors:
            parent_selector = saved_selectors.get("parent_selector")
            title_selector = saved_selectors.get("title_selector")
            content_selector = saved_selectors.get("content_selector")
            image_selector = saved_selectors.get("image_selector")
            raw_material = fetch_url_content(url_to_scrape)
            extracted_data = extract_content_by_selectors(
                raw_material,
                parent_selector,
                title_selector,
                content_selector,
                image_selector,
            )
            raw_material_content = json.dumps(extracted_data, ensure_ascii=False)
        else:
            try:
                with open("artigos base.txt", "r", encoding="utf-8") as f:
                    raw_material_content = f.read()
                logger.info(
                    "Conteúdo de 'artigos base.txt' carregado como material bruto."
                )
            except FileNotFoundError:
                logger.warning(
                    "Arquivo 'artigos base.txt' não encontrado. Usando conteúdo de fallback."
                )
                raw_material_content = (
                    "Conteúdo de exemplo para testes. Nenhuma raspagem real foi feita."
                )

        db_service.update_material_raw_material(
            user_id, task_id, raw_material_content, "RAW_COLLECTED"
        )
        logger.info(f"Material bruto coletado e salvo para task_id: {task_id}")

    except Exception as e:
        logger.error(
            f"Erro na coleta de material bruto para task_id {task_id}: {e}",
            exc_info=True,
        )
        db_service.update_material_status(user_id, task_id, "COLLECTION_FAILED")
        return

    try:
        prompt = f"Crie um {content_type} detalhado sobre '{theme}' usando o seguinte material bruto:"
        generated_content = await generate_content_with_gemini_service(
            theme=theme,
            raw_material=raw_material_content,
            content_type=content_type,
            keywords=None,
            tone=None,
            cta_instruction=None,
            article_structure=None,
            audience=None,
            ideal_article_example=None,
        )
        suggested_image_prompt = generated_content.get(
            "suggested_image_prompt", "Imagem relevante para o artigo."
        )
        db_service.update_material_generated_content(
            user_id,
            task_id,
            json.dumps(generated_content, ensure_ascii=False),
            suggested_image_prompt,
            "GENERATED",
        )
        logger.info(f"Conteúdo gerado e salvo para task_id: {task_id}")

    except Exception as e:
        logger.error(
            f"Erro na geração de conteúdo Gemini para task_id {task_id}: {e}",
            exc_info=True,
        )
        db_service.update_material_status(user_id, task_id, "GENERATION_FAILED")
        return


async def _run_gemini_generation_in_background(
    user_id: str,
    task_id: str,
    theme: str,
    raw_material: str,
    content_type: str,
    keywords: Optional[List[str]],
    tone: Optional[str],
    cta_instruction: Optional[str],
    article_structure: Optional[List[str]],
    audience: Optional[str],
    ideal_article_example: Optional[str],
):
    """
    Função auxiliar para executar a geração Gemini em segundo plano e atualizar o DB.
    """
    try:
        generated_data = await generate_content_with_gemini_service(
            theme,
            raw_material,
            content_type,
            keywords=keywords,
            tone=tone,
            cta_instruction=cta_instruction,
            article_structure=article_structure,
            audience=audience,
            ideal_article_example=ideal_article_example,
        )
        db_service.update_material(
            user_id=user_id,
            task_id=task_id,
            status="GENERATED",
            generated_content=json.dumps(generated_data, ensure_ascii=False),
            suggested_image_prompt=generated_data.get("suggested_image_prompt"),
        )
        logger.info(
            f"Geração de conteúdo para task_id '{task_id}' (user '{user_id}') concluída e salva no DB."
        )
    except Exception as e:
        logger.error(
            f"Erro na tarefa de geração Gemini em segundo plano para task_id '{task_id}': {e}",
            exc_info=True,
        )
        db_service.update_material_status(
            user_id=user_id, task_id=task_id, status="FAILED_GENERATION"
        )


# --- ENDPOINTS FastAPI (usando router) ---


@router.get("/trigger-by-id/{automation_request_id}", response_model=TriggerResponse)
async def trigger_by_id_endpoint(
    automation_request_id: int,
    background_tasks: BackgroundTasks,
    user_payload: dict = Depends(Auth.verify_token),
    db: Session = Depends(get_db),
):
    """
    Aciona a automação para COLETAR E PREPARAR material bruto em segundo plano,
    com base em um registro existente no banco de dados.
    Retorna imediatamente um task_id para consulta de status.
    """
    logger.info(
        f"Endpoint /trigger-by-id/{automation_request_id} acionado pelo usuário: {user_payload.get('sub', 'Desconhecido')} para coletar material bruto."
    )

    user_id = user_payload.get("sub", "anonymous_user")
    task_id = str(uuid.uuid4())

    try:
        request_entry = (
            db.query(AutomationRequest)
            .filter(AutomationRequest.id == automation_request_id)
            .first()
        )
        if not request_entry:
            logger.warning(
                f"Registro com ID {automation_request_id} não encontrado no banco de dados compartilhado."
            )
            if Config.LOGS_API_URL:
                try:
                    log_data = {
                        "action": f"Falha ao executar automação para ID {automation_request_id}: Registro não encontrado.",
                        "timestamp": datetime.now(timezone.utc)
                        .isoformat()
                        .replace("+00:00", ""),
                        "level": "WARNING",
                        "report_id": task_id,
                    }
                    send_logs_to_backend(
                        log_data,
                        {"Authorization": f"Bearer {user_payload.get('token')}"},
                    )
                except Exception as log_err:
                    logger.error(
                        f"Erro ao enviar log de ID não encontrado para o backend: {str(log_err)}",
                        exc_info=True,
                    )
            raise HTTPException(
                status_code=404,
                detail=f"Registro com ID {automation_request_id} não encontrado",
            )

        output_format = request_entry.output_format
        theme = request_entry.theme
        logger.info(
            f"Parâmetros do DB para ID {automation_request_id}: output_format='{output_format}', theme='{theme}'"
        )

        _save_material_process_initial_data(
            user_id=user_id,
            automation_request_id=automation_request_id,
            task_id=task_id,
            status="PENDING_COLLECTION",
            theme=theme,
            content_type=output_format,
        )
        logger.info(
            f"Registro inicial da tarefa '{task_id}' para coleta de material salvo no DB."
        )

        background_tasks.add_task(
            process_material_task,
            user_payload,
            automation_request_id,
            task_id,
        )

        logger.info(
            f"Coleta de material para ID {automation_request_id} iniciada em segundo plano. Task ID: {task_id}"
        )
        return TriggerResponse(
            trigger_id=automation_request_id,
            message="Coleta de material iniciada em segundo plano. Consulte o status usando o task_id.",
            task_id=task_id,
            status="PENDING_COLLECTION",
        )

    except HTTPException as http_exc:
        logger.error(
            f"HTTPException levantada durante a execução para ID {automation_request_id}: {str(http_exc.detail)}",
            exc_info=True,
        )
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Falha na execução da automação para ID {automation_request_id}. Erro: {str(http_exc.detail)}",
                    "timestamp": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", ""),
                    "level": "ERROR",
                    "report_id": task_id,
                }
                send_logs_to_backend(
                    log_data, {"Authorization": f"Bearer {user_payload.get('token')}"}
                )
            except Exception as log_err:
                logger.error(
                    f"Erro ao enviar log de HTTPException para o backend: {str(log_err)}",
                    exc_info=True,
                )
        raise
    except Exception as e:
        logger.error(
            f"Erro inesperado ao executar automação via /trigger-by-id/{automation_request_id}: {str(e)}",
            exc_info=True,
        )
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Erro inesperado na execução da automação para ID {automation_request_id}. Erro: {str(e)}",
                    "timestamp": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", ""),
                    "level": "CRITICAL",
                    "report_id": task_id,
                }
                send_logs_to_backend(
                    log_data, {"Authorization": f"Bearer {user_payload.get('token')}"}
                )
            except Exception as log_err:
                logger.error(
                    f"Erro ao enviar log de erro inesperado para o backend: {str(log_err)}",
                    exc_info=True,
                )
        raise HTTPException(
            status_code=500, detail=f"Erro interno ao executar automação: {str(e)}"
        )


@router.post("/trigger", response_model=TriggerResponse)
async def trigger_automation_post_endpoint(
    request_data: TriggerRequest,
    background_tasks: BackgroundTasks,
    user_payload: dict = Depends(Auth.verify_token),
):
    """
    Aciona a automação de geração de posts com base em parâmetros fornecidos no corpo da requisição JSON.
    Este endpoint inicia o processo em segundo plano (assíncrono).
    Requer um token JWT válido.
    """
    logger.info(
        f"Endpoint POST /trigger acionado pelo usuário: {user_payload.get('sub', 'Desconhecido')}"
    )

    output_format = request_data.output_format
    theme = request_data.theme
    user_id = user_payload.get("sub", "anonymous_user")
    task_id = str(uuid.uuid4())

    try:
        logger.info(
            f"Parâmetros recebidos: output_format='{output_format}', theme='{theme}'"
        )

        _save_material_process_initial_data(
            user_id=user_id,
            automation_request_id=None,  # Não há automation_request_id para este trigger manual
            task_id=task_id,
            status="PENDING_COLLECTION",
            theme=theme,
            content_type=output_format,
        )
        logger.info(
            f"Registro inicial da tarefa '{task_id}' para coleta de material salvo no DB (trigger manual)."
        )

        background_tasks.add_task(
            process_material_task,  # Reutiliza a função de processamento principal
            user_payload,
            None,  # Não há automation_request_id para este trigger manual
            task_id,
        )

        logger.info(
            f"Coleta de material para trigger manual iniciada em segundo plano. Task ID: {task_id}"
        )
        return TriggerResponse(
            message="Coleta de material iniciada em segundo plano. Consulte o status usando o task_id.",
            task_id=task_id,
            status="PENDING_COLLECTION",
        )

    except ValidationError as e:
        logger.error(
            f"Erro de validação Pydantic para POST /trigger: {str(e)}", exc_info=True
        )
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Falha de validação Pydantic para POST /trigger. Erro: {str(e)}",
                    "timestamp": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", ""),
                    "level": "ERROR",
                    "report_id": task_id,
                }
                send_logs_to_backend(
                    log_data, {"Authorization": f"Bearer {user_payload.get('token')}"}
                )
            except Exception as log_err:
                logger.error(
                    f"Erro ao enviar log de ValidationError para o backend: {str(log_err)}",
                    exc_info=True,
                )
        errors = e.errors()
        formatted_errors = [
            {"loc": err["loc"], "msg": err["msg"], "type": err["type"]}
            for err in errors
        ]
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Erro de validação do corpo da requisição.",
                "errors": formatted_errors,
            },
        )
    except HTTPException as http_exc:
        logger.error(
            f"HTTPException levantada durante a execução de POST /trigger: {str(http_exc.detail)}",
            exc_info=True,
        )
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Falha na execução da automação POST /trigger. Erro: {str(http_exc.detail)}",
                    "timestamp": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", ""),
                    "level": "ERROR",
                    "report_id": task_id,
                }
                send_logs_to_backend(
                    log_data, {"Authorization": f"Bearer {user_payload.get('token')}"}
                )
            except Exception as log_err:
                logger.error(
                    f"Erro ao enviar log de HTTPException para o backend: {str(log_err)}",
                    exc_info=True,
                )
        raise
    except Exception as e:
        logger.error(
            f"Erro inesperado ao executar automação via POST /trigger: {str(e)}",
            exc_info=True,
        )
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Erro inesperado na execução da automação POST /trigger. Erro: {str(e)}",
                    "timestamp": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", ""),
                    "level": "CRITICAL",
                    "report_id": task_id,
                }
                send_logs_to_backend(
                    log_data, {"Authorization": f"Bearer {user_payload.get('token')}"}
                )
            except Exception as log_err:
                logger.error(
                    f"Erro ao enviar log de erro inesperado para o backend: {str(log_err)}",
                    exc_info=True,
                )
        raise HTTPException(
            status_code=500, detail=f"Erro interno ao executar automação: {str(e)}"
        )


@router.get("/get_task_result/{user_id}/{task_id}", response_model=MaterialResponse)
async def get_task_result_endpoint(user_id: str, task_id: str):
    """
    Consulta o status e o resultado de uma tarefa específica (coleta ou geração) pelo task_id e user_id.
    """
    material = db_service.get_material(user_id, task_id)
    if not material:
        raise HTTPException(
            status_code=404, detail="Tarefa ou material não encontrado."
        )
    # Tenta desserializar os campos JSON
    if material.get("raw_material") and isinstance(material["raw_material"], str):
        try:
            material["raw_material"] = json.loads(material["raw_material"])
        except json.JSONDecodeError:
            pass
    if material.get("generated_content") and isinstance(
        material["generated_content"], str
    ):
        try:
            material["generated_content"] = json.loads(material["generated_content"])
        except json.JSONDecodeError:
            pass
    return MaterialResponse(**material)


@router.get("/list_user_materials/{user_id}", response_model=List[MaterialResponse])
async def list_user_materials_endpoint(user_id: str):
    """
    Lista todos os materiais (brutos ou gerados) associados a um user_id.
    """
    materials = db_service.list_user_materials(user_id)
    response_list = []
    for material in materials:
        if material.get("raw_material") and isinstance(material["raw_material"], str):
            try:
                material["raw_material"] = json.loads(material["raw_material"])
            except json.JSONDecodeError:
                pass
        if material.get("generated_content") and isinstance(
            material["generated_content"], str
        ):
            try:
                material["generated_content"] = json.loads(
                    material["generated_content"]
                )
            except json.JSONDecodeError:
                pass
        response_list.append(MaterialResponse(**material))
    return response_list


@router.post("/submit_final_post")
async def submit_final_post_endpoint(
    request_body: SubmitFinalPostRequest,
    user_payload: dict = Depends(Auth.verify_token),
):
    """
    Recebe o conteúdo final, aprovado pelo usuário, e o envia para o backend PostgreSQL.
    Opcionalmente, deleta o registro temporário do SQLite.
    """
    logger.info(
        f"Endpoint /submit_final_post acionado pelo usuário: {user_payload.get('sub', 'Desconhecido')}"
    )
    headers = {"Authorization": f"Bearer {user_payload.get('token')}"}
    post_data_for_pg = request_body.post_data
    task_id_to_delete = request_body.task_id_to_delete
    try:
        response = send_post(post_data_for_pg, headers)
        response.raise_for_status()
        if task_id_to_delete:
            user_id = user_payload.get("sub", "anonymous_user")
            if db_service.delete_material(user_id, task_id_to_delete):
                logger.info(
                    f"Material temporário com task_id '{task_id_to_delete}' deletado do SQLite."
                )
            else:
                logger.warning(
                    f"Falha ao deletar material temporário com task_id '{task_id_to_delete}' do SQLite."
                )
        return {
            "message": "Post final enviado com sucesso para o backend e material temporário limpo (se aplicável).",
            "status": "SUCCESS",
        }
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Erro ao enviar post final para o backend: {str(e)}", exc_info=True
        )
        detail = (
            f"Erro ao enviar post final: {e.response.text}" if e.response else str(e)
        )
        raise HTTPException(
            status_code=e.response.status_code if e.response else 500, detail=detail
        )
    except Exception as e:
        logger.error(
            f"Erro inesperado no endpoint /submit_final_post: {str(e)}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@router.post("/save-selectors")
async def save_selectors_endpoint(
    selectors_data: List[SelectorData],
    user: Dict[str, Any] = Depends(Auth.verify_token),
):
    """
    Salva uma lista de seletores no banco de dados.
    """
    user_id = user["payload"].get("sub", "anonymous_user")
    try:
        save_selector_data(user_id, selectors_data)
        return {"message": "Seletores salvos com sucesso!"}
    except Exception as e:
        logger.error(f"Erro ao salvar seletores: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao salvar seletores: {e}")


@router.get("/get-selectors")
async def get_selectors_endpoint(user: Dict[str, Any] = Depends(Auth.verify_token)):
    """
    Obtém a lista de seletores salvos para o usuário autenticado.
    """
    user_id = user["payload"].get("sub", "anonymous_user")
    try:
        selectors = db_service.get_selectors(user_id)
        return [SelectorData(**s) for s in selectors]
    except Exception as e:
        logger.error(f"Erro ao obter seletores: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao obter seletores: {e}")


@router.get("/test-ok")
async def test_ok_endpoint():
    """Endpoint simples para testar a conexão."""
    logger.info("Endpoint /test-ok acionado. Retornando OK.")
    return JSONResponse(
        content={"status": "ok", "message": "Conexão com servidor Python bem-sucedida!"}
    )
