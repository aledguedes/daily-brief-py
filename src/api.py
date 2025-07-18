# src/api.py
import requests
import logging
import json
import re
import uuid
from datetime import datetime, timezone

# Importações do FastAPI e Pydantic para os novos endpoints
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
from typing import List, Optional, Dict, Any

# Importações para a configuração do Gemini e retries
import google.generativeai as genai
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from dotenv import load_dotenv
import os

# Importações do seu código original (resumo daily-py.txt)
from src.config import Config
from src.auth import Auth
import jsonschema
from jsonschema import ValidationError

# Importar o novo módulo de scraping
from src.scraping_service import (
    fetch_url_content,
    extract_content_by_selectors,
    get_parent_selector_func,
    clean_html_content,
    save_selector_data,
)

# Carregar variáveis de ambiente do arquivo .env
load_dotenv()

logger = logging.getLogger(__name__)

# Inicialização do FastAPI
app = FastAPI()

# --- Configuração da API Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError(
        "A chave GEMINI_API_KEY não foi encontrada. Verifique seu arquivo .env"
    )
genai.configure(api_key=GEMINI_API_KEY)

# --- ESQUEMAS DE DADOS ORIGINAIS (DO SEU ARQUIVO RESUMO DAILY-PY.TXT) ---
post_schema = {
    "type": "object",
    "required": ["title", "excerpt", "content", "metaDescription"],
    "properties": {
        "title": {
            "type": "object",
            "required": ["PT", "EN", "ES"],
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
            "additionalProperties": False,
            "minProperties": 3,
            "maxProperties": 3,
        },
        "excerpt": {
            "type": "object",
            "required": ["PT", "EN", "ES"],
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
            "additionalProperties": False,
            "minProperties": 3,
            "maxProperties": 3,
        },
        "content": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
            "required": ["PT", "EN", "ES"],
        },
        "image": {"type": ["string", "null"]},
        "author": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "category": {"type": ["string", "null"]},
        "metaDescription": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
            "required": ["PT", "EN", "ES"],
        },
        "affiliateLinks": {
            "type": "object",
            "patternProperties": {".*": {"type": "string"}},
            "additionalProperties": True,
        },
        "status": {"type": "string", "enum": ["PENDING", "APPROVED", "REJECTED"]},
        "publishedAt": {"type": ["string", "null"], "format": "date-time"},
        "readTime": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}

social_schema = {
    "type": "object",
    "required": ["socialTitle", "socialContent", "originalPostId"],
    "properties": {
        "socialTitle": {
            "type": "object",
            "required": ["PT", "EN", "ES"],
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
            "additionalProperties": False,
            "minProperties": 3,
            "maxProperties": 3,
        },
        "socialContent": {
            "type": "object",
            "required": ["PT", "EN", "ES"],
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
            "additionalProperties": False,
            "minProperties": 3,
            "maxProperties": 3,
        },
        "socialImageUrl": {"type": ["string", "null"]},
        "socialMediaPlatform": {"type": "string"},
        "originalPostId": {"type": "integer"},
        "status": {
            "type": "string",
            "enum": ["DRAFT", "SCHEDULED", "PUBLISHED", "FAILED"],
        },
        "publishedSocialAt": {"type": ["string", "null"], "format": "date-time"},
        "impressions": {"type": "integer"},
        "clicks": {"type": "integer"},
        "shares": {"type": "integer"},
        "likes": {"type": "integer"},
        "comments": {"type": "integer"},
        "link": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}

trending_suggestion_schema = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "topic_name": {"type": "string"},
            "source": {"type": "string"},
            "relevance_reason": {"type": "string"},
            "url": {"type": ["string", "null"]},
            "status": {"type": "string", "enum": ["NEW", "APPROVED", "REJECTED"]},
        },
        "required": ["topic_name", "source", "relevance_reason"],
    },
}

EXPECTED_POST_FIELDS = [
    "title",
    "excerpt",
    "content",
    "image",
    "author",
    "tags",
    "category",
    "metaDescription",
    "affiliateLinks",
    "status",
    "publishedAt",
    "readTime",
]

EXPECTED_SOCIAL_FIELDS = [
    "socialTitle",
    "socialContent",
    "socialImageUrl",
    "socialMediaPlatform",
    "originalPostId",
    "status",
    "publishedSocialAt",
    "impressions",
    "clicks",
    "shares",
    "likes",
    "comments",
    "link",
]

EXPECTED_TRENDING_FIELDS = ["topic_name", "source", "relevance_reason", "url", "status"]


# --- FUNÇÕES DE VALIDAÇÃO E LIMPEZA DE PAYLOADS ORIGINAIS ---
def clean_post_payload(
    post_data: dict, is_social: bool = False, is_trending: bool = False
):
    expected_fields = (
        EXPECTED_SOCIAL_FIELDS
        if is_social
        else EXPECTED_TRENDING_FIELDS if is_trending else EXPECTED_POST_FIELDS
    )
    cleaned_data = {}
    for field in expected_fields:
        if field in post_data:
            cleaned_data[field] = post_data[field]

    logger.debug(
        f"Payload gerado (completo): {json.dumps(post_data, ensure_ascii=False, indent=2)}"
    )
    logger.debug(
        f"Payload limpo (para envio, {'trending' if is_trending else 'social' if is_social else 'post'}): {json.dumps(cleaned_data, ensure_ascii=False, indent=2)}"
    )
    return cleaned_data


def validate_post(post_data):
    try:
        jsonschema.validate(instance=post_data, schema=post_schema)
        logger.debug("Validação do post principal bem-sucedida.")
    except ValidationError as e:
        logger.error(
            f"Erro de validação do esquema do post principal: {e.message}",
            exc_info=True,
        )
        raise
    except Exception as e:
        logger.error(
            f"Erro inesperado durante a validação do post principal: {str(e)}",
            exc_info=True,
        )
        raise


def validate_social_post(post_data):
    try:
        jsonschema.validate(instance=post_data, schema=social_schema)
        logger.debug("Validação do post social bem-sucedida.")
    except ValidationError as e:
        logger.error(
            f"Erro de validação do esquema do post social: {e.message}", exc_info=True
        )
        raise
    except Exception as e:
        logger.error(
            f"Erro inesperado durante a validação do post social: {str(e)}",
            exc_info=True,
        )
        raise


# --- FUNÇÕES DE COMUNICAÇÃO COM O BACKEND ORIGINAIS ---
def get_existing_posts(headers):
    url = Config.API_URL
    logger.info(f"Buscando posts existentes em: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=Config.REQUEST_TIMEOUT)
        response.raise_for_status()
        response_data = response.json()

        posts = response_data.get("content", [])

        if not isinstance(posts, list):
            logger.error(
                f"Resposta inesperada ao buscar posts existentes. Esperado lista em 'content', recebido: {type(posts)}. Conteúdo completo: {response_data}"
            )
            return []

        existing_titles_pt = []
        for post in posts:
            if (
                isinstance(post, dict)
                and "title" in post
                and isinstance(post["title"], dict)
            ):
                title_pt = post["title"].get("PT")
                if title_pt and isinstance(title_pt, str):
                    existing_titles_pt.append(title_pt.strip())
            else:
                logger.warning(f"Item inválido encontrado na lista de posts: {post}")

        logger.info(
            f"Posts existentes recuperados: {len(existing_titles_pt)} títulos em PT."
        )
        logger.debug(f"Títulos existentes: {existing_titles_pt}")
        return existing_titles_pt

    except requests.exceptions.Timeout:
        logger.error(f"Timeout ao buscar posts existentes em {url}", exc_info=True)
        return []
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Erro HTTP/Requisição ao buscar posts existentes em {url}: {str(e)}",
            exc_info=True,
        )
        if hasattr(e, "response") and e.response is not None:
            logger.error(
                f"Resposta de erro do backend: Status {e.response.status_code}, Corpo: {e.response.text}"
            )
        return []
    except Exception as e:
        logger.error(
            f"Erro inesperado ao buscar posts existentes em {url}: {str(e)}",
            exc_info=True,
        )
        return []


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def send_post(post_data, headers):
    url = Config.API_URL
    logger.info(f"Iniciando envio de post principal para o backend em: {url}")

    cleaned_post_data = clean_post_payload(post_data, is_social=False)

    try:
        validate_post(cleaned_post_data)
        logger.debug("Payload validado com sucesso contra o esquema de post.")

        response = requests.post(
            url, json=cleaned_post_data, headers=headers, timeout=Config.REQUEST_TIMEOUT
        )
        response.raise_for_status()

        logger.info(
            f"Post principal enviado com sucesso para {url}. Status: {response.status_code}"
        )
        return response

    except ValidationError as e:
        logger.error(
            f"Erro de validação do esquema do post principal antes de enviar: {str(e)}. Payload: {json.dumps(cleaned_post_data, ensure_ascii=False)}",
            exc_info=True,
        )
        raise ValueError(f"Erro de validação do esquema do post: {e.message}") from e
    except requests.exceptions.Timeout:
        logger.error(
            f"Timeout ao enviar post principal para {url}. Tentando novamente...",
            exc_info=True,
        )
        raise
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Erro HTTP/Requisição ao enviar post principal para {url}: {str(e)}. Tentando novamente...",
            exc_info=True,
        )
        if hasattr(e, "response") and e.response is not None:
            logger.error(
                f"Resposta de erro do backend: Status {e.response.status_code}, Corpo: {e.response.text}"
            )
        raise
    except Exception as e:
        logger.error(
            f"Erro inesperado ao enviar post principal para {url}: {str(e)}",
            exc_info=True,
        )
        raise


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def send_social_post(post_data, headers):
    if not Config.ENABLE_SOCIAL_API or not Config.SOCIAL_API_URL:
        logger.warning(
            "Envio de post social desativado ou URL não configurada. Pulando envio."
        )
        return None

    url = Config.SOCIAL_API_URL
    logger.info(f"Iniciando envio de post social para o backend em: {url}")

    cleaned_post_data = clean_post_payload(post_data, is_social=True)

    try:
        validate_social_post(cleaned_post_data)
        logger.debug("Payload validado com sucesso contra o esquema de social.")

        response = requests.post(
            url, json=cleaned_post_data, headers=headers, timeout=Config.REQUEST_TIMEOUT
        )
        response.raise_for_status()

        logger.info(
            f"Post social enviado com sucesso para {url}. Status: {response.status_code}"
        )
        return response

    except ValidationError as e:
        logger.error(
            f"Erro de validação do esquema do post social antes de enviar: {str(e)}. Payload: {json.dumps(cleaned_post_data, ensure_ascii=False)}",
            exc_info=True,
        )
        raise ValueError(
            f"Erro de validação do esquema do post social: {e.message}"
        ) from e
    except requests.exceptions.Timeout:
        logger.error(
            f"Timeout ao enviar post social para {url}. Tentando novamente...",
            exc_info=True,
        )
        raise
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Erro HTTP/Requisição ao enviar post social para {url}: {str(e)}. Tentando novamente...",
            exc_info=True,
        )
        if hasattr(e, "response") and e.response is not None:
            logger.error(
                f"Resposta de erro do backend: Status {e.response.status_code}, Corpo: {e.response.text}"
            )
        raise
    except Exception as e:
        logger.error(
            f"Erro inesperado ao enviar post social para {url}: {str(e)}", exc_info=True
        )
        raise


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def send_trending_suggestions_to_backend(suggestions, headers):
    url = Config.TREND_SUGGESTIONS_API_URL
    logger.info(f"Enviando sugestões de tendências para o backend em: {url}")

    cleaned_suggestions = [
        clean_post_payload(suggestion, is_trending=True) for suggestion in suggestions
    ]

    try:
        jsonschema.validate(
            instance=cleaned_suggestions, schema=trending_suggestion_schema
        )
        logger.debug("Sugestões de tendências validadas com sucesso contra o esquema.")

        response = requests.post(
            url,
            json=cleaned_suggestions,
            headers=headers,
            timeout=Config.REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        logger.info(
            f"Sugestões de tendências enviadas com sucesso para {url}. Status: {response.status_code}"
        )
        return response

    except ValidationError as e:
        logger.error(
            f"Erro de validação do esquema das sugestões de tendências: {str(e)}. Payload: {json.dumps(cleaned_suggestions, ensure_ascii=False)}",
            exc_info=True,
        )
        raise ValueError(
            f"Erro de validação do esquema das sugestões: {e.message}"
        ) from e
    except requests.exceptions.Timeout:
        logger.error(
            f"Timeout ao enviar sugestões para {url}. Tentando novamente...",
            exc_info=True,
        )
        raise
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Erro HTTP/Requisição ao enviar sugestões para {url}: {str(e)}. Tentando novamente...",
            exc_info=True,
        )
        if hasattr(e, "response") and e.response is not None:
            logger.error(
                f"Resposta de erro do backend: Status {e.response.status_code}, Corpo: {e.response.text}"
            )
        raise
    except Exception as e:
        logger.error(
            f"Erro inesperado ao enviar sugestões para {url}: {str(e)}", exc_info=True
        )
        raise


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def send_logs_to_backend(log_data, headers=None):
    url = Config.LOGS_API_URL
    if not url:
        logger.warning(
            "LOGS_API_URL não configurada. Pulando envio de logs para o backend."
        )
        return

    logger.info(f"Enviando log para o backend em: {url}")

    try:
        if "timestamp" in log_data and isinstance(log_data["timestamp"], datetime):
            timestamp = log_data["timestamp"].strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        else:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        payload = {
            "reportId": log_data.get("report_id", str(uuid.uuid4())),
            "level": log_data.get("level", "INFO"),
            "action": log_data.get("action", "Relatório de Execução"),
            "timestamp": timestamp,
            "details": {
                "summary": log_data.get("report_summary", ""),
                "metrics": log_data.get("metrics", {}),
                "duration_seconds": log_data.get("duration_seconds", 0),
            },
        }

        logger.debug(
            f"Payload de log a enviar: {json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

        response = requests.post(
            url, json=payload, headers=headers, timeout=Config.REQUEST_TIMEOUT
        )
        response.raise_for_status()
        logger.info(
            f"Log enviado com sucesso para {url}. Status: {response.status_code}"
        )
        return response

    except requests.exceptions.Timeout:
        logger.error(
            f"Timeout ao enviar log para {url}. Tentando novamente...", exc_info=True
        )
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro HTTP ao enviar log para {url}: {str(e)}", exc_info=True)
        if e.response is not None:
            logger.error(
                f"Resposta do backend: {e.response.status_code}, {e.response.text}"
            )
        raise
    except Exception as e:
        logger.error(
            f"Erro inesperado ao enviar log para {url}: {str(e)}", exc_info=True
        )
        raise


# --- ESQUEMA DE RESPOSTA JSON PARA O GEMINI (ADAPTADO DO APP.PY ORIGINAL) ---
gemini_response_schema = {
    "type": "object",
    "properties": {
        "title": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
            "required": ["PT", "EN", "ES"],
        },
        "excerpt": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
            "required": ["PT", "EN", "ES"],
        },
        "content": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
            "required": ["PT", "EN", "ES"],
        },
        "metaDescription": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
            "required": ["PT", "EN", "ES"],
        },
    },
    "required": ["title", "excerpt", "content", "metaDescription"],
}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(5),
    retry=retry_if_exception_type(Exception),
)
async def generate_content_with_gemini_service(
    theme: str, raw_material: str, content_type: str = "summary"
):
    """
    Função que chama a API do Gemini para gerar conteúdo de blog.
    """
    model = genai.GenerativeModel("gemini-1.5-flash")

    content_instructions = {
        "summary": "Gere um resumo conciso e informativo, com 3-5 parágrafos, formatado em HTML. Use tags <p> para parágrafos. Inclua um título, excerto e meta descrição.",
        "article": "Gere um artigo detalhado e aprofundado, com 8-15 parágrafos, formatado em HTML. Use tags <p> para parágrafos e tags <h2>, <h3> para subtítulos. Inclua um título, excerto e meta descrição.",
        "social": "Gere um post curto e envolvente para redes sociais (máximo 3 parágrafos), formatado em HTML. Use tags <p> para parágrafos. Inclua um título (curto), excerto e meta descrição.",
        "informative": "Gere um texto informativo, com 5-10 parágrafos, formatado em HTML. Use tags <p> para parágrafos e, se necessário, tags <ul> ou <ol> para listas. Inclua um título, excerto e meta descrição.",
    }
    instruction = content_instructions.get(
        content_type, content_instructions["informative"]
    )

    prompt = f"""
Com base no seguinte material bruto, gere conteúdo para um post de blog sobre '{theme}'.

Instruções Detalhadas para a Estrutura de Saída:
1. O campo 'content' (para PT, EN, ES) DEVE ser formatado como HTML válido, apenas com o conteúdo do artigo. NÃO inclua tags <html>, <head> ou <body>.
2. NÃO inclua classes CSS, IDs ou estilos inline nas tags HTML.
3. Os campos 'title', 'excerpt' e 'metaDescription' DEVEM ser texto puro, sem tags HTML.
{instruction}
O resultado final deve ser um objeto JSON, seguindo o esquema fornecido.
GARANTA QUE CARACTERES ESPECIAIS E ACENTUADOS SEJAM PRESERVADOS CORRETAMENTE EM UTF-8.

Material Bruto:
{raw_material[:30000]} # Aumentar limite para material bruto, Gemini 1.5 Flash suporta mais
    """

    try:
        response = await model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                response_schema=gemini_response_schema,
                temperature=0.7,
            ),
        )
        # Parse the JSON response. Gemini 1.5 Flash can directly return JSON if schema is set.
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


# --- PYDANTIC MODELS PARA OS NOVOS ENDPOINTS ---
class FetchURLsRequest(BaseModel):
    urls: List[HttpUrl]


class ExtractRequest(BaseModel):
    url: HttpUrl
    selectors: List[str]


class GetParentSelectorRequest(BaseModel):
    url: HttpUrl
    selectors: List[str]


class SaveSelectorEntry(BaseModel):
    url: HttpUrl
    selector: str = "body"
    outerHTML_preview: str = ""
    timestamp: str  # ISO formatted string


class SaveSelectorsRequest(BaseModel):
    entries: List[SaveSelectorEntry]


class PasteContentRequest(BaseModel):
    content: str
    keywords: Optional[List[str]] = None
    theme: Optional[str] = None


class GenerateContentManualRequest(BaseModel):
    theme: str
    raw_material: str  # This will be the aggregated and edited material from frontend
    content_type: str = "summary"


class ScrapedContentItem(BaseModel):
    url: str
    raw_html: str
    clean_text: str


# --- NOVOS ENDPOINTS PARA SCRAPING E ENTRADA MANUAL ---


@app.post("/fetch_urls")
async def fetch_urls(request_body: FetchURLsRequest):
    """
    Fetches raw HTML body content from multiple URLs.
    Returns both raw HTML and cleaned text for each URL.
    """
    results = []
    for url in request_body.urls:
        raw_html = fetch_url_content(str(url))
        clean_text = clean_html_content(raw_html) if "Erro" not in raw_html else ""
        results.append(
            {"url": str(url), "raw_html": raw_html, "clean_text": clean_text}
        )
    return {"results": results}


@app.post("/extract_content")
async def extract_content(request_body: ExtractRequest):
    """
    Extracts content from a URL using CSS selectors.
    Returns raw HTML and cleaned text for each extracted item.
    """
    extracted_items = extract_content_by_selectors(
        str(request_body.url), request_body.selectors
    )
    processed_items = []
    for item in extracted_items:
        if "error" not in item:
            clean_text = clean_html_content(item["content"])
            processed_items.append(
                {
                    "selector": item["selector"],
                    "raw_html": item["content"],
                    "clean_text": clean_text,
                }
            )
        else:
            processed_items.append(item)  # Pass error messages through
    return {"extracted_contents": processed_items}


@app.post("/get_parent_selector")
async def get_parent_selector_endpoint(request_body: GetParentSelectorRequest):
    """
    Identifies a CSS selector for the parent of the first element found by the input selectors.
    """
    parent_selectors = get_parent_selector_func(
        str(request_body.url), request_body.selectors
    )
    return {"parent_selectors": parent_selectors}


@app.post("/save_selectors")
async def save_selectors(request_body: SaveSelectorsRequest):
    """
    Saves selector entries to a JSON file.
    """
    # Define a directory for saving selectors, ideally from config
    save_dir = os.getenv("SAVED_SELECTORS_DIR", "./saved_selectors")
    result = save_selector_data(
        [entry.dict() for entry in request_body.entries], save_dir
    )
    if "Erro" in result:
        raise HTTPException(status_code=500, detail=result)
    return {"message": result}


@app.post("/paste_material")
async def paste_material(request_body: PasteContentRequest):
    """
    Receives raw text content pasted by the user.
    """
    # The content is already plain text, so no HTML cleaning is needed.
    # It's ready to be aggregated with other raw materials in the frontend.
    return {
        "status": "success",
        "message": "Conteúdo colado recebido e pronto para processamento.",
        "pasted_content": request_body.content,
        "keywords": request_body.keywords,
        "theme": request_body.theme,
    }


# --- ENDPOINT PARA GERAÇÃO DE CONTEÚDO MANUAL ---


@app.post("/generate_content_manual")
async def generate_content_manual(request_body: GenerateContentManualRequest):
    """
    Triggers content generation using Gemini with user-provided/edited raw material.
    """
    try:
        generated_data = await generate_content_with_gemini_service(
            request_body.theme, request_body.raw_material, request_body.content_type
        )
        return generated_data
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Erro no endpoint /generate_content_manual: {e}")
        raise HTTPException(status_code=500, detail=f"Erro na geração de conteúdo: {e}")


# --- Exemplo de um endpoint existente (mantido para contexto) ---
# from src.config import Config # Assuming Config is in src/config.py
# from src.auth import Auth # Assuming Auth is in src/auth.py

# @app.post("/send_post_to_backend")
# async def send_post_to_backend_example(post_data: Dict[str, Any]):
#     """
#     Example of an existing endpoint that might send posts to a backend API.
#     """
#     # Placeholder for original backend communication logic
#     # This would typically use requests to send data to another API
#     # logger.info(f"Enviando post para o backend: {post_data.get('title', 'N/A')}")
#     # auth_token = await Auth.get_admin_token() # Assuming Auth has an async get_admin_token
#     # headers = {"Authorization": f"Bearer {auth_token}"}
#     # response = requests.post(Config.BACKEND_API_URL, json=post_data, headers=headers)
#     # response.raise_for_status()
#     return {"message": "Post enviado com sucesso (exemplo)."}
