# src/scraping_service.py
import requests
from bs4 import BeautifulSoup
import re
import os
import json
import logging

logger = logging.getLogger(__name__)


def clean_html_content(html_content: str) -> str:
    """
    Remove HTML tags from a string and return plain text.
    """
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    # Remove script and style elements
    for script_or_style in soup(["script", "style"]):
        script_or_style.decompose()
    # Get text and replace multiple spaces/newlines with a single space
    text = soup.get_text()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_url_content(url: str) -> str:
    """
    Fetches the body content of a given URL, cleaning non-printable characters.
    """
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            },
            timeout=10,  # Add a timeout to prevent hanging requests
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        body = soup.body
        if body is None:
            logger.warning(f"Conteúdo <body> não encontrado para URL: {url}")
            return "Erro: Conteúdo <body> não encontrado"
        else:
            body_html = str(
                body
            )  # Use str(body) instead of prettify() for raw HTML content
            # Remove non-printable ASCII characters
            body_html = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", body_html)
            return body_html
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao acessar a URL {url}: {e}")
        return f"Erro ao acessar a URL: {str(e)}"
    except Exception as e:
        logger.error(f"Erro inesperado ao buscar URL {url}: {e}")
        return f"Erro inesperado: {str(e)}"


def extract_content_by_selectors(url: str, selectors: list[str]) -> list[dict]:
    """
    Extracts content from a URL based on CSS selectors.
    Returns a list of dictionaries with 'selector' and 'content' (raw HTML).
    """
    results = []
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            },
            timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for selector in selectors:
            elements = soup.select(selector)
            if elements:
                # We return the outerHTML of the first found element, then clean it later
                results.append({"selector": selector, "content": str(elements[0])})
            else:
                logger.warning(
                    f"Nenhum elemento encontrado para o seletor '{selector}' na URL: {url}"
                )
        return results
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao acessar a URL {url} para extração: {e}")
        return [{"error": f"Erro ao acessar a URL: {str(e)}"}]
    except Exception as e:
        logger.error(f"Erro inesperado ao extrair conteúdo da URL {url}: {e}")
        return [{"error": f"Erro inesperado: {str(e)}"}]


def get_parent_selector_func(url: str, selectors: list[str]) -> list[str]:
    """
    Identifies a CSS selector for the parent of the first element found by the input selectors.
    """
    parent_selectors = []
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/50 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            },
            timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for selector in selectors:
            elements = soup.select(selector)
            if elements and elements[0].parent:
                parent = elements[0].parent
                tag = parent.name
                id_ = parent.get("id", "")
                classes = parent.get("class", [])
                parent_selector = tag
                if id_:
                    parent_selector += f"#{id_}"
                if classes:
                    parent_selector += "." + ".".join(classes)
                parent_selectors.append(parent_selector)
            else:
                logger.warning(
                    f"Nenhum elemento encontrado ou sem pai para o seletor '{selector}' na URL: {url}"
                )
                parent_selectors.append(
                    selector
                )  # Return original selector if no parent found/no element
        return parent_selectors
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao acessar a URL {url} para obter seletor pai: {e}")
        return [f"Erro: {str(e)}" for _ in selectors]  # Return errors for all selectors
    except Exception as e:
        logger.error(f"Erro inesperado ao obter seletor pai da URL {url}: {e}")
        return [f"Erro inesperado: {str(e)}" for _ in selectors]


def save_selector_data(entries: list[dict], save_dir: str = "saved_selectors") -> str:
    """
    Saves selector entries to a JSON file.
    """
    if not entries:
        return "Nenhuma entrada fornecida"

    os.makedirs(save_dir, exist_ok=True)
    file_path = os.path.join(save_dir, "mapeamento_seletores.json")
    saved_data = []

    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                saved_data = json.load(f)
        except json.JSONDecodeError:
            logger.warning(
                f"Arquivo '{file_path}' corrompido ou vazio. Iniciando um novo."
            )
            saved_data = []

    for entry in entries:
        url = entry.get("url")
        selector = entry.get("selector", "body")
        outer_html = entry.get("outerHTML_preview", "")
        timestamp = entry.get("timestamp")
        if not url or not timestamp:
            logger.warning(f"Entrada inválida ignorada: {entry}")
            continue
        saved_data.append(
            {
                "url": url,
                "selector": selector,
                "outerHTML_preview": outer_html,  # Keep raw HTML preview for context
                "timestamp": timestamp,
            }
        )
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(saved_data, f, indent=4, ensure_ascii=False)
        return "Seletores salvos com sucesso!"
    except Exception as e:
        logger.error(f"Erro ao salvar seletores em '{file_path}': {e}")
        return f"Erro ao salvar: {str(e)}"
