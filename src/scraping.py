# src/scraping.py
import asyncio
import logging
import aiohttp
import asyncpraw
import asyncprawcore
import requests
from asyncprawcore.exceptions import (
    NotFound,
    Forbidden as RedditForbidden,
    TooManyRequests,
)
from newsapi import NewsApiClient
from src.config import Config
from tenacity import (
    retry,
    wait_fixed,
    wait_random_exponential,
    stop_after_attempt,
    retry_if_exception_type,
)

logger = logging.getLogger(__name__)

# Configuração do NewsApiClient
newsapi = NewsApiClient(api_key=Config.NEWSAPI_KEY)

# Configuração do Reddit
reddit = asyncpraw.Reddit(
    client_id=Config.REDDIT_CLIENT_ID,
    client_secret=Config.REDDIT_CLIENT_SECRET,
    user_agent=Config.REDDIT_USER_AGENT,
)


@retry(
    stop=stop_after_attempt(5),
    wait=wait_random_exponential(min=1, max=60),
    retry=retry_if_exception_type(TooManyRequests),
)
async def scrape_reddit(query: str):
    """
    Busca posts relevantes no Reddit usando a biblioteca asyncpraw.
    Retorna uma lista de dicionários com 'title', 'url' e 'content'.
    """
    logger.info(f"Buscando posts do Reddit para: '{query}'")
    results = []

    subreddits_to_search = [
        "programming",
        "compsci",
        "technology",
        "webdev",
        "gamedev",
        "sysadmin",
        "brasil",
    ]

    try:
        for subreddit_name in subreddits_to_search:
            try:
                subreddit = await reddit.subreddit(subreddit_name)
                logger.debug(f"Buscando em r/{subreddit.display_name} por '{query}'")

                async for submission in subreddit.search(
                    query=query, sort="relevance", limit=10
                ):
                    results.append(
                        {
                            "title": submission.title,
                            "url": submission.url,
                            "content": (
                                submission.selftext
                                if submission.selftext
                                else submission.title
                            ),
                        }
                    )

                if len(results) >= 10:
                    break

            except asyncprawcore.exceptions.NotFound:
                logger.warning(
                    f"Subreddit 'r/{subreddit_name}' não encontrado. Pulando para o próximo."
                )
                continue
            except Exception as e:
                logger.error(
                    f"Erro inesperado ao buscar posts no r/{subreddit_name}: {e}"
                )

        logger.info(f"Encontrados {len(results)} resultados do Reddit para '{query}'.")
        return results

    except Exception as e:
        logger.error(
            f"Erro ao buscar posts no Reddit para '{query}': {e}", exc_info=True
        )
        return []


@retry(
    stop=stop_after_attempt(5),
    wait=wait_random_exponential(min=1, max=60),
    retry=retry_if_exception_type(Exception),
)
def scrape_newsapi(query: str):
    """
    Busca artigos de notícias usando a NewsAPI.
    Retorna uma lista de dicionários com 'title', 'url' e 'content'.
    """
    logger.info(f"Buscando notícias na NewsAPI para: '{query}'")
    results = []
    try:
        response = newsapi.get_everything(
            q=query,
            language="en",
            sort_by="relevancy",
            page_size=10,
        )
        for article in response.get("articles", []):
            results.append(
                {
                    "title": article.get("title", ""),
                    "url": article.get("url", ""),
                    "content": article.get("description", "")
                    or article.get("content", "")[:200],
                }
            )
        logger.info(f"Encontrados {len(results)} resultados da NewsAPI para '{query}'.")
        return results
    except Exception as e:
        logger.error(f"Erro ao buscar na NewsAPI para '{query}': {e}", exc_info=True)
        return []


@retry(
    stop=stop_after_attempt(5),
    wait=wait_random_exponential(min=1, max=60),
    retry=retry_if_exception_type(Exception),
)
def scrape_serper(query: str):
    """
    Busca resultados no Google usando a API do SerpApi via requests.
    Retorna uma lista de dicionários com 'title', 'url' e 'content'.
    """
    logger.info(f"Buscando no Google via SerpApi para: '{query}'")
    results = []
    url = "https://serpapi.com/search"
    params = {
        "api_key": Config.SERPER_API_KEY,
        "q": query,
        "num": 10,
        "tbm": "nws",  # Busca apenas notícias
    }
    try:
        response = requests.get(url, params=params, timeout=Config.REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        for result in data.get("news_results", []):
            if result.get("title") and result.get("snippet") and result.get("link"):
                results.append(
                    {
                        "title": result["title"],
                        "url": result["link"],
                        "content": result["snippet"],
                    }
                )
            if len(results) >= 25:  # Limitar total de resultados
                break
        logger.info(f"Encontrados {len(results)} resultados do SerpApi para '{query}'.")
        return results
    except Exception as e:
        logger.error(
            f"Erro ao buscar no Google via SerpApi para '{query}': {e}", exc_info=True
        )
        return []


async def scrape_sources(theme):
    """
    Orquestra o scraping de múltiplas fontes (Reddit, NewsAPI, SerpApi)
    e compila o material bruto.
    """
    logger.info(f"Iniciando scraping de fontes para o tema: '{theme}'")

    all_raw_material = []
    all_source_urls = []

    loop = asyncio.get_event_loop()
    tasks = []
    tasks.append(scrape_reddit(theme))
    tasks.append(loop.run_in_executor(None, scrape_newsapi, theme))
    tasks.append(loop.run_in_executor(None, scrape_serper, theme))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    reddit_results = results[0]
    if isinstance(reddit_results, list):
        for item in reddit_results:
            all_raw_material.append(
                f"Título: {item['title']}\nConteúdo: {item['content']}"
            )
            all_source_urls.append(item["url"])
    elif isinstance(reddit_results, Exception):
        logger.error(f"Erro ao obter resultados do Reddit: {reddit_results}")

    newsapi_results = results[1]
    if isinstance(newsapi_results, list):
        for item in newsapi_results:
            all_raw_material.append(
                f"Título: {item['title']}\nConteúdo: {item['content']}"
            )
            all_source_urls.append(item["url"])
    elif isinstance(newsapi_results, Exception):
        logger.error(f"Erro ao obter resultados do NewsAPI: {newsapi_results}")

    serper_results = results[2]
    if isinstance(serper_results, list):
        for item in serper_results:
            all_raw_material.append(
                f"Título: {item['title']}\nConteúdo: {item['content']}"
            )
            all_source_urls.append(item["url"])
    elif isinstance(serper_results, Exception):
        logger.error(f"Erro ao obter resultados do SerpApi: {serper_results}")

    compiled_text = "\n\n".join(all_raw_material)
    unique_source_urls = list(set(all_source_urls))

    logger.info(
        f"Scraping concluído para '{theme}'. Total de material: {len(compiled_text)} caracteres. Total de URLs únicas: {len(unique_source_urls)}"
    )
    return compiled_text, unique_source_urls
