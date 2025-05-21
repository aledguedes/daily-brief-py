# src/api.py
import requests
import logging
import json
from src.config import Config
from src.auth import Auth
import jsonschema
from jsonschema import ValidationError
from tenacity import retry, stop_after_attempt, wait_fixed
from datetime import datetime, timezone # Importar timezone para logs

logger = logging.getLogger(__name__)

# Esquema de validação para o PostRequestDTO (adaptado exatamente ao seu DTO Java)
post_schema = {
    "type": "object",
    "required": ["title", "excerpt", "content", "metaDescription"], # Campos @NotEmpty no DTO
    "properties": {
        "title": { # Map<String, String> title // PT, EN, ES
            "type": "object",
            "required": ["PT", "EN", "ES"], # Garante que essas chaves existem
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"}
            },
            "additionalProperties": False, # Não permite outras chaves além de PT, EN, ES
            "minProperties": 3, # Garante que tem pelo menos 3 (do @Size)
            "maxProperties": 3  # Garante que tem no máximo 3 (do @Size)
        },
        "excerpt": { # Map<String, String> excerpt // PT, EN, ES
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
        "content": { # Map<String, String> content // PT, EN, ES
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
        "image": {"type": ["string", "null"]}, # String ou null
        "author": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}}, # List<String>
        "category": {"type": ["string", "null"]}, # String ou null
        "metaDescription": { # Map<String, String> metaDescription // SEO
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
        "affiliateLinks": { # Map<String, String> affiliateLinks
            "type": "object",
            "patternProperties": {
                ".*": {"type": "string"} # Permite qualquer chave com valor string
            },
            "additionalProperties": True # Permite chaves não predefinidas
        },
        "status": {"type": "string", "enum": ["PENDING", "APPROVED", "REJECTED"]}, # String com valores específicos
        "publishedAt": {"type": ["string", "null"], "format": "date-time"}, # String (ISO 8601) ou null
        "readTime": {"type": ["string", "null"]} # String ou null
    },
    "additionalProperties": False # Não permite propriedades adicionais não definidas no esquema
}


def get_existing_posts(headers):
    """
    Busca posts existentes no backend para verificar duplicados.
    Espera que o endpoint retorne um objeto Page do Spring Boot.
    Retorna uma lista de títulos em PT.
    """
    url = Config.API_URL
    logger.info(f"Buscando posts existentes em: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=Config.REQUEST_TIMEOUT)
        response.raise_for_status()
        response_data = response.json()

        # CORREÇÃO: Acessar a lista de posts dentro da chave "content" do objeto Page do Spring Boot
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
    """
    Envia os dados de um post para o backend.
    Implementado retry para aumentar a robustez.
    """
    url = Config.API_URL
    logger.info(f"Enviando post para o backend em: {url}")
    logger.debug(f"Payload de envio: {json.dumps(post_data, ensure_ascii=False, indent=2)}")
    try:
        validate_post(post_data)
        logger.debug("Payload validado com sucesso contra o esquema.")

        response = requests.post(url, json=post_data, headers=headers, timeout=Config.REQUEST_TIMEOUT)
        response.raise_for_status()

        logger.info(f"Post enviado com sucesso para {url}. Status: {response.status_code}")
        return response

    except ValidationError as e:
        logger.error(f"Erro de validação do esquema do post antes de enviar: {str(e)}. Payload: {json.dumps(post_data, ensure_ascii=False)}", exc_info=True)
        raise ValueError(f"Erro de validação do esquema do post: {e.message}") from e
    except requests.exceptions.Timeout:
        logger.error(f"Timeout ao enviar post para {url}. Tentando novamente...", exc_info=True)
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro HTTP/Requisição ao enviar post para {url}: {str(e)}. Tentando novamente...", exc_info=True)
        if hasattr(e, 'response') and e.response is not None:
             logger.error(f"Resposta de erro do backend: Status {e.response.status_code}, Corpo: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Erro inesperado ao enviar post para {url}: {str(e)}", exc_info=True)
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def send_logs_to_backend(log_data, headers=None):
    """
    Envia dados de log ou relatório para um endpoint de logs no backend.
    Headers são opcionais se o endpoint de logs não exigir autenticação,
    mas é boa prática incluir se a API for protegida.
    """
    url = Config.LOGS_API_URL
    if not url:
        logger.warning("LOGS_API_URL não configurada. Pulando envio de logs para o backend.")
        return

    logger.info(f"Enviando log para o backend em: {url}")
    logger.debug(f"Dados de log a enviar: {json.dumps(log_data, ensure_ascii=False)}")
    try:
        # CORREÇÃO: Garantir que o timestamp esteja no formato ISO 8601 com 'Z' para UTC
        # Isso é crucial para que o Spring Boot deserialize corretamente java.time.Instant
        if "timestamp" in log_data and isinstance(log_data["timestamp"], datetime):
            log_data["timestamp"] = log_data["timestamp"].isoformat(timespec='microseconds') + 'Z'
        elif "timestamp" in log_data and isinstance(log_data["timestamp"], str):
             # Se já for string, garantir que termina com 'Z' se for UTC
             if not log_data["timestamp"].endswith('Z') and not re.search(r'[+-]\d{2}:\d{2}$', log_data["timestamp"]):
                  logger.warning(f"Timestamp '{log_data['timestamp']}' não tem offset ou Z. Adicionando Z.")
                  log_data["timestamp"] = log_data["timestamp"] + 'Z'


        response = requests.post(url, json=log_data, headers=headers, timeout=Config.REQUEST_TIMEOUT)
        response.raise_for_status()
        logger.info(f"Log enviado com sucesso para {url}. Status: {response.status_code}")
        return response

    except requests.exceptions.Timeout:
        logger.error(f"Timeout ao enviar log para {url}. Tentando novamente...", exc_info=True)
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro HTTP/Requisição ao enviar log para {url}: {str(e)}. Tentando novamente...", exc_info=True)
        if hasattr(e, 'response') and e.response is not None:
             logger.error(f"Resposta de erro do backend: Status {e.response.status_code}, Corpo: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Erro inesperado ao enviar log para {url}: {str(e)}", exc_info=True)
        raise


def validate_post(post_data):
    """Valida os dados de um post contra o esquema definido."""
    try:
        jsonschema.validate(instance=post_data, schema=post_schema)
        logger.debug("Validação do post bem-sucedida.")
    except ValidationError as e:
        logger.error(f"Erro de validação do esquema do post: {e.message}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Erro inesperado durante a validação do post: {str(e)}", exc_info=True)
        raise