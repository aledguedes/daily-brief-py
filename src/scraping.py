# src/scraping.py
import asyncio
import logging
import aiohttp
import asyncpraw
import requests
from asyncprawcore.exceptions import Forbidden as RedditForbidden # Importar Forbidden especificamente
from newsapi import NewsApiClient
from serpapi import GoogleSearch
from src.config import Config
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

logger = logging.getLogger(__name__)

# Configuração do NewsApiClient
newsapi = NewsApiClient(api_key=Config.NEWSAPI_KEY)

# Configuração do Reddit
reddit = asyncpraw.Reddit(
    client_id=Config.REDDIT_CLIENT_ID,
    client_secret=Config.REDDIT_CLIENT_SECRET,
    user_agent=Config.REDDIT_USER_AGENT
)

# Configuração do SerpApi (Google Search)
# A chave da API do Serper é usada diretamente na chamada da API, não precisa de objeto global aqui.

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type(aiohttp.ClientError))
async def scrape_reddit(query):
    """
    Busca posts relevantes no Reddit usando a biblioteca asyncpraw.
    Retorna uma lista de dicionários com 'title', 'url' e 'content'.
    """
    logger.info(f"Buscando posts do Reddit para: '{query}'")
    results = []
    try:
        # Busca subreddits relacionados à query
        # Usamos search_by_name para encontrar subreddits relevantes
        subreddits = [
            await reddit.subreddit("technology"),
            await reddit.subreddit("news"),
            await reddit.subreddit("worldnews")
        ]
        
        # Tenta encontrar um subreddit mais específico se a query for muito direcionada
        try:
            # Isso pode levantar um asyncprawcore.exceptions.NotFound se o subreddit não existir
            search_subreddit = await reddit.subreddit(query.replace(" ", "")) # Tenta um subreddit com o nome da query
            subreddits.insert(0, search_subreddit) # Prioriza o subreddit específico
        except asyncprawcore.exceptions.NotFound:
            logger.warning(f"Subreddit '{query.replace(' ', '')}' não encontrado. Usando subreddits gerais.")
        except Exception as e:
            logger.warning(f"Erro ao tentar encontrar subreddit específico para '{query}': {e}")


        for subreddit in subreddits:
            logger.debug(f"Buscando em r/{subreddit.display_name} para '{query}'")
            async for submission in subreddit.hot(limit=20): # Aumentado limite para ter mais material
                if query.lower() in submission.title.lower() or query.lower() in submission.selftext.lower():
                    results.append({
                        "title": submission.title,
                        "url": submission.url,
                        "content": submission.selftext if submission.selftext else submission.title # Usar selftext se existir, senão o título
                    })
                    if len(results) >= 10: # Limitar a 10 resultados por fonte para não sobrecarregar
                        break
            if len(results) >= 10:
                break # Se já temos 10 de qualquer subreddit, paramos

        logger.info(f"Encontrados {len(results)} resultados do Reddit para '{query}'.")
        return results
    except RedditForbidden as e: # Captura o erro 403 especificamente
        logger.error(f"Erro ao buscar posts do Reddit para '{query}': {e}. Verifique suas credenciais REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET e REDDIT_USER_AGENT.")
        return [] # Retorna lista vazia em caso de 403
    except Exception as e:
        logger.error(f"Erro inesperado ao buscar posts do Reddit para '{query}': {e}", exc_info=True)
        return []


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type(requests.exceptions.RequestException))
def scrape_newsapi(query):
    """
    Busca artigos de notícias relevantes usando a NewsAPI.
    Retorna uma lista de dicionários com 'title', 'url' e 'content'.
    """
    logger.info(f"Buscando artigos do NewsAPI para: '{query}'")
    results = []
    try:
        # Busca artigos em português, inglês e espanhol
        languages = ['pt', 'en', 'es']
        for lang in languages:
            top_headlines = newsapi.get_everything(
                q=query,
                language=lang,
                sort_by='relevancy',
                page_size=10 # Limitar a 10 resultados por idioma
            )
            for article in top_headlines.get('articles', []):
                if article.get('title') and article.get('description'):
                    results.append({
                        "title": article['title'],
                        "url": article['url'],
                        "content": article['description'] # NewsAPI geralmente tem descrição
                    })
            if len(results) >= 20: # Limitar total de resultados para não sobrecarregar
                break

        logger.info(f"Encontrados {len(results)} resultados do NewsAPI para '{query}'.")
        return results
    except Exception as e:
        logger.error(f"Erro ao buscar artigos do NewsAPI para '{query}': {e}", exc_info=True)
        return []


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type(requests.exceptions.RequestException))
def scrape_serper(query):
    """
    Realiza uma busca no Google usando SerpApi para encontrar artigos e informações.
    Retorna uma lista de dicionários com 'title', 'url' e 'content'.
    """
    logger.info(f"Buscando no Google via SerpApi para: '{query}'")
    results = []
    try:
        params = {
            "q": query,
            "api_key": Config.SERPER_API_KEY,
            "hl": "pt", # Idioma da interface de busca
            "gl": "br", # País da busca
            "num": 20 # Número de resultados
        }
        search = GoogleSearch(params)
        data = search.get_dict()

        # Processar resultados de 'organic_results'
        for result in data.get("organic_results", []):
            if result.get('title') and result.get('snippet') and result.get('link'):
                results.append({
                    "title": result['title'],
                    "url": result['link'],
                    "content": result['snippet']
                })
            if len(results) >= 15: # Limitar a 15 resultados
                break
        
        # Opcional: Processar resultados de 'news_results' se houver
        for result in data.get("news_results", []):
            if result.get('title') and result.get('snippet') and result.get('link'):
                results.append({
                    "title": result['title'],
                    "url": result['link'],
                    "content": result['snippet']
                })
            if len(results) >= 25: # Limitar total de resultados
                break

        logger.info(f"Encontrados {len(results)} resultados do SerpApi para '{query}'.")
        return results
    except Exception as e:
        logger.error(f"Erro ao buscar no Google via SerpApi para '{query}': {e}", exc_info=True)
        return []


async def scrape_sources(theme):
    """
    Orquestra o scraping de múltiplas fontes (Reddit, NewsAPI, SerpApi)
    e compila o material bruto.
    """
    logger.info(f"Iniciando scraping de fontes para o tema: '{theme}'")
    
    all_raw_material = []
    all_source_urls = []

    # Executar scraping de forma concorrente
    # Note que scrape_reddit é async, enquanto newsapi e serper são síncronos (usando requests)
    # Para executá-los em paralelo com asyncio, usamos loop.run_in_executor para os síncronos.
    loop = asyncio.get_event_loop()

    tasks = []
    tasks.append(scrape_reddit(theme))
    tasks.append(loop.run_in_executor(None, scrape_newsapi, theme))
    tasks.append(loop.run_in_executor(None, scrape_serper, theme))

    results = await asyncio.gather(*tasks, return_exceptions=True) # Captura exceções

    # Processar resultados do Reddit
    reddit_results = results[0]
    if isinstance(reddit_results, list):
        for item in reddit_results:
            all_raw_material.append(f"Título: {item['title']}\nConteúdo: {item['content']}")
            all_source_urls.append(item['url'])
    elif isinstance(reddit_results, Exception):
        logger.error(f"Erro ao obter resultados do Reddit: {reddit_results}")

    # Processar resultados do NewsAPI
    newsapi_results = results[1]
    if isinstance(newsapi_results, list):
        for item in newsapi_results:
            all_raw_material.append(f"Título: {item['title']}\nConteúdo: {item['content']}")
            all_source_urls.append(item['url'])
    elif isinstance(newsapi_results, Exception):
        logger.error(f"Erro ao obter resultados do NewsAPI: {newsapi_results}")

    # Processar resultados do SerpApi
    serper_results = results[2]
    if isinstance(serper_results, list):
        for item in serper_results:
            all_raw_material.append(f"Título: {item['title']}\nConteúdo: {item['content']}")
            all_source_urls.append(item['url'])
    elif isinstance(serper_results, Exception):
        logger.error(f"Erro ao obter resultados do SerpApi: {serper_results}")

    compiled_text = "\n\n".join(all_raw_material)
    unique_source_urls = list(set(all_source_urls)) # Remover URLs duplicadas

    logger.info(f"Scraping concluído para '{theme}'. Total de material: {len(compiled_text)} caracteres. Total de URLs únicas: {len(unique_source_urls)}")
    return compiled_text, unique_source_urls
