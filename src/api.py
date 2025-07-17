# src/api.py
import requests
import logging
import json
import re
from src.config import Config
from src.auth import Auth
import jsonschema
from jsonschema import ValidationError
from tenacity import retry, stop_after_attempt, wait_fixed
from datetime import datetime, timezone
import uuid

logger = logging.getLogger(__name__)

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
                "ES": {"type": "string"}
            },
            "additionalProperties": False,
            "minProperties": 3,
            "maxProperties": 3
        },
        "excerpt": {
            "type": "object",
            "required": ["PT", "EN", "ES"],
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"}
            },
            "additionalProperties": False,
            "minProperties": 3,
            "maxProperties": 3
        },
        "content": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"}
            },
            "required": ["PT", "EN", "ES"]
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
                "ES": {"type": "string"}
            },
            "required": ["PT", "EN", "ES"]
        },
        "affiliateLinks": {
            "type": "object",
            "patternProperties": {
                ".*": {"type": "string"}
            },
            "additionalProperties": True
        },
        "status": {"type": "string", "enum": ["PENDING", "APPROVED", "REJECTED"]},
        "publishedAt": {"type": ["string", "null"], "format": "date-time"},
        "readTime": {"type": ["string", "null"]}
    },
    "additionalProperties": False
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
                "ES": {"type": "string"}
            },
            "additionalProperties": False,
            "minProperties": 3,
            "maxProperties": 3
        },
        "socialContent": {
            "type": "object",
            "required": ["PT", "EN", "ES"],
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"}
            },
            "additionalProperties": False,
            "minProperties": 3,
            "maxProperties": 3
        },
        "socialImageUrl": {"type": ["string", "null"]},
        "socialMediaPlatform": {"type": "string"},
        "originalPostId": {"type": "integer"},
        "status": {"type": "string", "enum": ["DRAFT", "SCHEDULED", "PUBLISHED", "FAILED"]},
        "publishedSocialAt": {"type": ["string", "null"], "format": "date-time"},
        "impressions": {"type": "integer"},
        "clicks": {"type": "integer"},
        "shares": {"type": "integer"},
        "likes": {"type": "integer"},
        "comments": {"type": "integer"},
        "link": {"type": ["string", "null"]}
    },
    "additionalProperties": False
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
            "status": {"type": "string", "enum": ["NEW", "APPROVED", "REJECTED"]}
        },
        "required": ["topic_name", "source", "relevance_reason"]
    }
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
    "readTime"
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
    "link"
]

EXPECTED_TRENDING_FIELDS = [
    "topic_name",
    "source",
    "relevance_reason",
    "url",
    "status"
]

def clean_post_payload(post_data: dict, is_social: bool = False, is_trending: bool = False):
    expected_fields = (
        EXPECTED_SOCIAL_FIELDS if is_social else
        EXPECTED_TRENDING_FIELDS if is_trending else
        EXPECTED_POST_FIELDS
    )
    cleaned_data = {}
    for field in expected_fields:
        if field in post_data:
            cleaned_data[field] = post_data[field]
    
    logger.debug(f"Payload gerado (completo): {json.dumps(post_data, ensure_ascii=False, indent=2)}")
    logger.debug(f"Payload limpo (para envio, {'trending' if is_trending else 'social' if is_social else 'post'}): {json.dumps(cleaned_data, ensure_ascii=False, indent=2)}")
    return cleaned_data

def get_existing_posts(headers):
    url = Config.API_URL
    logger.info(f"Buscando posts existentes em: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=Config.REQUEST_TIMEOUT)
        response.raise_for_status()
        response_data = response.json()

        posts = response_data.get("content", [])

        if not isinstance(posts, list):
            logger.error(f"Resposta inesperada ao buscar posts existentes. Esperado lista em 'content', recebido: {type(posts)}. Conteúdo completo: {response_data}")
            return []

        existing_titles_pt = []
        for post in posts:
            if isinstance(post, dict) and "title" in post and isinstance(post["title"], dict):
                title_pt = post["title"].get("PT")
                if title_pt and isinstance(title_pt, str):
                    existing_titles_pt.append(title_pt.strip())
            else:
                logger.warning(f"Item inválido encontrado na lista de posts: {post}")

        logger.info(f"Posts existentes recuperados: {len(existing_titles_pt)} títulos em PT.")
        logger.debug(f"Títulos existentes: {existing_titles_pt}")
        return existing_titles_pt

    except requests.exceptions.Timeout:
        logger.error(f"Timeout ao buscar posts existentes em {url}", exc_info=True)
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro HTTP/Requisição ao buscar posts existentes em {url}: {str(e)}", exc_info=True)
        if hasattr(e, 'response') and e.response is not None:
             logger.error(f"Resposta de erro do backend: Status {e.response.status_code}, Corpo: {e.response.text}")
        return []
    except Exception as e:
        logger.error(f"Erro inesperado ao buscar posts existentes em {url}: {str(e)}", exc_info=True)
        return []

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def send_post(post_data, headers):
    url = Config.API_URL
    logger.info(f"Iniciando envio de post principal para o backend em: {url}")
    
    cleaned_post_data = clean_post_payload(post_data, is_social=False)

    try:
        validate_post(cleaned_post_data)
        logger.debug("Payload validado com sucesso contra o esquema de post.")

        response = requests.post(url, json=cleaned_post_data, headers=headers, timeout=Config.REQUEST_TIMEOUT)
        response.raise_for_status()

        logger.info(f"Post principal enviado com sucesso para {url}. Status: {response.status_code}")
        return response

    except ValidationError as e:
        logger.error(f"Erro de validação do esquema do post principal antes de enviar: {str(e)}. Payload: {json.dumps(cleaned_post_data, ensure_ascii=False)}", exc_info=True)
        raise ValueError(f"Erro de validação do esquema do post: {e.message}") from e
    except requests.exceptions.Timeout:
        logger.error(f"Timeout ao enviar post principal para {url}. Tentando novamente...", exc_info=True)
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro HTTP/Requisição ao enviar post principal para {url}: {str(e)}. Tentando novamente...", exc_info=True)
        if hasattr(e, 'response') and e.response is not None:
             logger.error(f"Resposta de erro do backend: Status {e.response.status_code}, Corpo: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Erro inesperado ao enviar post principal para {url}: {str(e)}", exc_info=True)
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def send_social_post(post_data, headers):
    if not Config.ENABLE_SOCIAL_API or not Config.SOCIAL_API_URL:
        logger.warning("Envio de post social desativado ou URL não configurada. Pulando envio.")
        return None

    url = Config.SOCIAL_API_URL
    logger.info(f"Iniciando envio de post social para o backend em: {url}")
    
    cleaned_post_data = clean_post_payload(post_data, is_social=True)

    try:
        validate_social_post(cleaned_post_data)
        logger.debug("Payload validado com sucesso contra o esquema de social.")

        response = requests.post(url, json=cleaned_post_data, headers=headers, timeout=Config.REQUEST_TIMEOUT)
        response.raise_for_status()

        logger.info(f"Post social enviado com sucesso para {url}. Status: {response.status_code}")
        return response

    except ValidationError as e:
        logger.error(f"Erro de validação do esquema do post social antes de enviar: {str(e)}. Payload: {json.dumps(cleaned_post_data, ensure_ascii=False)}", exc_info=True)
        raise ValueError(f"Erro de validação do esquema do post social: {e.message}") from e
    except requests.exceptions.Timeout:
        logger.error(f"Timeout ao enviar post social para {url}. Tentando novamente...", exc_info=True)
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro HTTP/Requisição ao enviar post social para {url}: {str(e)}. Tentando novamente...", exc_info=True)
        if hasattr(e, 'response') and e.response is not None:
             logger.error(f"Resposta de erro do backend: Status {e.response.status_code}, Corpo: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Erro inesperado ao enviar post social para {url}: {str(e)}", exc_info=True)
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def send_trending_suggestions_to_backend(suggestions, headers):
    url = Config.TREND_SUGGESTIONS_API_URL
    logger.info(f"Enviando sugestões de tendências para o backend em: {url}")

    cleaned_suggestions = [clean_post_payload(suggestion, is_trending=True) for suggestion in suggestions]

    try:
        jsonschema.validate(instance=cleaned_suggestions, schema=trending_suggestion_schema)
        logger.debug("Sugestões de tendências validadas com sucesso contra o esquema.")

        response = requests.post(url, json=cleaned_suggestions, headers=headers, timeout=Config.REQUEST_TIMEOUT)
        response.raise_for_status()

        logger.info(f"Sugestões de tendências enviadas com sucesso para {url}. Status: {response.status_code}")
        return response

    except ValidationError as e:
        logger.error(f"Erro de validação do esquema das sugestões de tendências: {str(e)}. Payload: {json.dumps(cleaned_suggestions, ensure_ascii=False)}", exc_info=True)
        raise ValueError(f"Erro de validação do esquema das sugestões: {e.message}") from e
    except requests.exceptions.Timeout:
        logger.error(f"Timeout ao enviar sugestões para {url}. Tentando novamente...", exc_info=True)
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro HTTP/Requisição ao enviar sugestões para {url}: {str(e)}. Tentando novamente...", exc_info=True)
        if hasattr(e, 'response') and e.response is not None:
             logger.error(f"Resposta de erro do backend: Status {e.response.status_code}, Corpo: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Erro inesperado ao enviar sugestões para {url}: {str(e)}", exc_info=True)
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def send_logs_to_backend(log_data, headers=None):
    url = Config.LOGS_API_URL
    if not url:
        logger.warning("LOGS_API_URL não configurada. Pulando envio de logs para o backend.")
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
                "duration_seconds": log_data.get("duration_seconds", 0)
            }
        }

        logger.debug(f"Payload de log a enviar: {json.dumps(payload, ensure_ascii=False, indent=2)}")

        response = requests.post(url, json=payload, headers=headers, timeout=Config.REQUEST_TIMEOUT)
        response.raise_for_status()
        logger.info(f"Log enviado com sucesso para {url}. Status: {response.status_code}")
        return response

    except requests.exceptions.Timeout:
        logger.error(f"Timeout ao enviar log para {url}. Tentando novamente...", exc_info=True)
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro HTTP ao enviar log para {url}: {str(e)}", exc_info=True)
        if e.response is not None:
            logger.error(f"Resposta do backend: {e.response.status_code}, {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Erro inesperado ao enviar log para {url}: {str(e)}", exc_info=True)
        raise

def validate_post(post_data):
    try:
        jsonschema.validate(instance=post_data, schema=post_schema)
        logger.debug("Validação do post principal bem-sucedida.")
    except ValidationError as e:
        logger.error(f"Erro de validação do esquema do post principal: {e.message}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Erro inesperado durante a validação do post principal: {str(e)}", exc_info=True)
        raise

def validate_social_post(post_data):
    try:
        jsonschema.validate(instance=post_data, schema=social_schema)
        logger.debug("Validação do post social bem-sucedida.")
    except ValidationError as e:
        logger.error(f"Erro de validação do esquema do post social: {e.message}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Erro inesperado durante a validação do post social: {str(e)}", exc_info=True)
        raise