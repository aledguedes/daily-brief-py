# src/utils.py
import os
import json
import logging
from datetime import datetime, timedelta, timezone
import uuid
import requests

from src.config import Config

logger = logging.getLogger(__name__)

# Caminho para o arquivo de cache
CACHE_FILE = "output/cache.json"

# Garante que a pasta 'output' e suas subpastas existam
os.makedirs("output", exist_ok=True)
os.makedirs("output/reports", exist_ok=True)
os.makedirs("output/payloads", exist_ok=True)


def save_cache(data):
    """Salva dados no arquivo de cache."""
    try:
        cache_data = {"timestamp": datetime.now(timezone.utc), "data": data}
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=4)
        logger.info(f"Cache salvo em {CACHE_FILE}.")
    except Exception as e:
        logger.error(f"Erro ao salvar cache em {CACHE_FILE}: {str(e)}", exc_info=True)


def check_cache(cache_duration_hours):
    """Verifica se o cache existe e é válido (não expirado)."""
    if not os.path.exists(CACHE_FILE):
        logger.info("Arquivo de cache não encontrado.")
        return None

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache_data = json.load(f)

        cache_timestamp_str = cache_data.get("timestamp")
        cached_data = cache_data.get("data")

        if not cache_timestamp_str or not cached_data:
            logger.warning("Cache encontrado, mas incompleto ou inválido.")
            return None

        cache_timestamp = datetime.fromisoformat(cache_timestamp_str).replace(
            tzinfo=timezone.utc
        )
        expiration_time = cache_timestamp + timedelta(hours=cache_duration_hours)

        if datetime.now(timezone.utc) < expiration_time:
            logger.info(f"Cache válido. Expira em: {expiration_time}.")
            return cached_data
        else:
            logger.info(
                f"Cache expirado. Salvo em: {cache_timestamp}. Expiração: {expiration_time}."
            )
            return None
    except json.JSONDecodeError:
        logger.error(
            f"Erro ao decodificar JSON do cache em {CACHE_FILE}. O arquivo pode estar corrompido.",
            exc_info=True,
        )
        os.remove(CACHE_FILE)
        return None
    except Exception as e:
        logger.error(
            f"Erro ao verificar cache em {CACHE_FILE}: {str(e)}", exc_info=True
        )
        return None


def save_report(report_lines, is_error=False):
    """Salva o relatório de execução em um arquivo local."""
    report_dir = "output/reports"
    os.makedirs(report_dir, exist_ok=True)
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename_prefix = "relatorio_erro_critico" if is_error else "relatorio"
    report_filename = os.path.join(report_dir, f"{filename_prefix}_{timestamp_str}.txt")

    try:
        with open(report_filename, "w", encoding="utf-8") as report_file:
            report_file.write("\n".join(report_lines))
        logger.info(f"Relatório salvo em {report_filename}")
        return report_filename
    except Exception as e:
        logger.error(
            f"Erro ao salvar relatório em {report_filename}: {str(e)}", exc_info=True
        )
        return None


def save_payload_to_file(payload_data, theme, content_type):
    """Salva o payload JSON de um post em um arquivo local para auditoria."""
    payload_dir = "output/payloads"
    os.makedirs(payload_dir, exist_ok=True)

    post_uuid = str(uuid.uuid4())
    safe_tema_name = theme.replace(" ", "_").replace("/", "_").replace("\\", "_")

    filename = os.path.join(
        payload_dir, f"post_{safe_tema_name}_{content_type}_{post_uuid}.json"
    )

    try:
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(payload_data, file, ensure_ascii=False, indent=4)
        logger.info(f"Payload salvo em: {filename}")
        return filename
    except Exception as e:
        logger.error(f"Erro ao salvar payload em {filename}: {str(e)}", exc_info=True)
        return None


def send_logs_to_backend(log_data):
    """Envia logs para o backend configurado em Config.LOGS_API_URL."""
    if not Config.LOGS_API_URL:
        logger.warning("LOGS_API_URL não configurado. Log não enviado ao backend.")
        return
    try:
        response = requests.post(
            Config.LOGS_API_URL, json=log_data, timeout=Config.REQUEST_TIMEOUT
        )
        response.raise_for_status()
        logger.info(f"Log enviado com sucesso para {Config.LOGS_API_URL}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao enviar log para {Config.LOGS_API_URL}: {str(e)}")
