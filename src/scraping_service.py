# src/scraping_service.py
import logging
from typing import List
from pydantic import HttpUrl
import aiohttp
from bs4 import BeautifulSoup

from src.config import Config
import src.database_service as db_service

logger = logging.getLogger(__name__)


# src/scraping_service.py

# ... (suas importações)


async def fetch_url_content(url: str) -> str:
    """
    Busca o conteúdo HTML de uma URL fornecida, com headers de navegador para evitar bloqueios.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.8,en-US;q=0.5,en;q=0.3",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, timeout=Config.REQUEST_TIMEOUT
            ) as response:
                response.raise_for_status()
                return await response.text()
    except Exception as e:
        logger.error(f"Erro ao buscar conteúdo da URL {url}: {str(e)}")
        return ""


def clean_html_content(html_content: str) -> str:
    """
    Limpa o conteúdo HTML, removendo elementos de 'ruído' e focando no texto principal.

    Args:
        html_content (str): Conteúdo HTML bruto.

    Returns:
        str: Texto limpo extraído do HTML.
    """
    try:
        soup = BeautifulSoup(html_content, "html.parser")

        logger.info("Iniciando a limpeza do HTML...")

        # Remove elementos que geralmente não contêm o conteúdo principal
        for tag in soup(["script", "style", "nav", "footer", "aside"]):
            tag.decompose()

        logger.info("Elementos de 'ruído' removidos.")

        # Pega o texto de tags de conteúdo principal, incluindo 'span'
        main_text = []
        for tag in soup.find_all(
            ["p", "h1", "h2", "h3", "article", "blockquote", "span"]
        ):
            # Adicionei 'span' para capturar o conteúdo do site de teste
            text = tag.get_text(strip=True)
            if text and len(text.split()) > 5:  # Filtra frases muito curtas
                main_text.append(text)

        logger.info(f"Conteúdo extraído: {main_text}")

        # Se não encontrar tags de conteúdo principal, pega o texto do body
        if not main_text:
            logger.warning(
                "Nenhuma tag de conteúdo principal encontrada. Tentando extrair do <body>."
            )
            body = soup.find("body")
            if body:
                text = body.get_text(separator="\n", strip=True)
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                cleaned_text = "\n\n".join(lines)[: Config.MAX_TEXT_LEN]
                logger.info(
                    f"Texto do <body> extraído. Início do conteúdo: {cleaned_text[:100]}..."
                )
                return cleaned_text

            logger.error("Nenhum conteúdo encontrado no <body>.")
            return ""

        cleaned_text = "\n\n".join(main_text)[: Config.MAX_TEXT_LEN]
        logger.info(f"Conteúdo final pronto. Início: {cleaned_text[:100]}...")
        return cleaned_text

    except Exception as e:
        logger.error(f"Erro ao limpar HTML: {e}")
        return ""


def save_selector_data(user_id: str, selectors_data: List[dict]):
    """
    Salva uma lista de seletores no banco de dados para o usuário especificado.

    Args:
        user_id (str): ID do usuário.
        selectors_data (List[dict]): Lista de seletores (url, parent_selector, title_selector, etc.).
    """
    try:
        for selector in selectors_data:
            db_service.save_selector(
                user_id=user_id,
                url=str(selector["url"]),
                parent_selector=selector.get("parent_selector"),
                title_selector=selector.get("title_selector"),
                content_selector=selector.get("content_selector"),
                image_selector=selector.get("image_selector"),
            )
        logger.info(f"Seletores salvos com sucesso para o usuário {user_id}.")
    except Exception as e:
        logger.error(f"Erro ao salvar seletores para o usuário {user_id}: {str(e)}")
        raise


async def extract_content_by_selectors(
    url: HttpUrl,
    parent_selector: str = None,
    title_selector: str = None,
    content_selector: str = None,
    image_selector: str = None,
) -> dict:
    """
    Extrai conteúdo de uma URL usando seletores CSS fornecidos.

    Args:
        url (HttpUrl): URL para extração.
        parent_selector (str, opcional): Seletor CSS para elemento pai.
        title_selector (str, opcional): Seletor CSS para título.
        content_selector (str, opcional): Seletor CSS para conteúdo.
        image_selector (str, opcional): Seletor CSS para imagem.

    Returns:
        dict: Conteúdo extraído (título, conteúdo, imagem).
    """
    try:
        html_content = await fetch_url_content(str(url))
        if not html_content:
            return {}

        soup = BeautifulSoup(html_content, "html.parser")
        extracted_data = {}

        if parent_selector:
            parent = soup.select_one(parent_selector)
            if parent:
                soup = parent

        if title_selector:
            title = soup.select_one(title_selector)
            extracted_data["title"] = title.get_text(strip=True) if title else ""

        if content_selector:
            content = soup.select_one(content_selector)
            extracted_data["content"] = content.get_text(strip=True) if content else ""

        if image_selector:
            image = soup.select_one(image_selector)
            extracted_data["image"] = image["src"] if image and image.get("src") else ""

        return extracted_data
    except Exception as e:
        logger.error(f"Erro ao extrair conteúdo com seletores da URL {url}: {str(e)}")
        return {}
