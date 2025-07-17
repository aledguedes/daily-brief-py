import asyncio
import logging
import requests
from newsapi import NewsApiClient
from serpapi import GoogleSearch
import asyncpraw as praw
from pytrends.request import TrendReq
import pandas as pd
from typing import List, Dict, Tuple
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from .config import Config

logger = logging.getLogger(__name__)

newsapi = NewsApiClient(api_key=Config.NEWSAPI_KEY)
reddit = praw.Reddit(client_id=Config.REDDIT_CLIENT_ID, client_secret=Config.REDDIT_CLIENT_SECRET, user_agent="TrendScraper/1.0")

def format_query(base_terms: List[str]) -> str:
    full_query = " OR ".join(base_terms)
    if len(full_query) > 496:
        full_query = full_query[:496]
        logger.warning(f"Query truncada para {len(full_query)} caracteres.")
    return full_query

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type(requests.exceptions.RequestException))
def scrape_newsapi(query: str) -> List[Dict]:
    logger.info(f"Buscando artigos do NewsAPI para: '{query}'")
    results = []
    languages = ['pt', 'en', 'es']
    for lang in languages:
        top_headlines = newsapi.get_everything(
            q=query,
            language=lang,
            sort_by='relevancy',
            page_size=40
        )
        for article in top_headlines.get('articles', []):
            results.append({
                "title": article['title'],
                "url": article['url'],
                "content": article['description'] or article['title'],
                "source": "NewsAPI"
            })
    return results

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type(requests.exceptions.RequestException))
def scrape_serper(query: str) -> List[Dict]:
    logger.info(f"Buscando no Google via SerpApi para: '{query}'")
    params = {
        "q": query,
        "api_key": Config.SERPER_API_KEY,
        "hl": "pt",
        "gl": "br",
        "num": 40
    }
    search = GoogleSearch(params)
    results = []
    for result in search.get_dict().get('organic_results', []):
        results.append({
            "title": result.get('title'),
            "url": result.get('link'),
            "content": result.get('snippet') or result.get('title'),
            "source": "SerpAPI"
        })
    return results

@retry(stop=stop_after_attempt(3), wait=wait_fixed(5), retry=retry_if_exception_type(Exception))
async def scrape_reddit(terms: List[str]) -> List[Dict]:
    query = " ".join(terms).lower()
    logger.info(f"🔍 Scraping Reddit com termos: {terms}")
    results = []
    subreddits = [
        await reddit.subreddit("technology"),
        await reddit.subreddit("news"),
        await reddit.subreddit("worldnews"),
        await reddit.subreddit("science"),
        await reddit.subreddit("futurology")
    ]
    for subreddit in subreddits:
        async for submission in subreddit.hot(limit=20):
            if any(term.lower() in submission.title.lower() for term in terms):
                results.append({
                    "title": submission.title,
                    "url": submission.url,
                    "content": submission.title,
                    "source": "Reddit"
                })
    return results

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type(Exception))
def scrape_google_trends(terms: List[str]) -> List[Dict]:
    logger.info(f"Buscando tendências no Google Trends para: {terms}")
    results = []
    pytrends = TrendReq(hl=Config.PYTRENDS_HL, tz=Config.PYTRENDS_TZ, retries=2, backoff_factor=0.1)
    terms = [str(term).strip() for term in terms if term and isinstance(term, str)]
    if not terms:
        logger.warning("Nenhum termo válido fornecido para Google Trends")
        return []

    try:
        trending_searches = pytrends.trending_searches(pn='brazil')
        if not trending_searches.empty:
            for _, row in trending_searches.head(10).iterrows():
                topic = row[0]
                if topic:
                    results.append({
                        "title": topic,
                        "url": f"https://trends.google.com/trends/explore?q={topic.replace(' ', '+')}&geo=BR",
                        "content": f"Tendência de busca no Google Trends: {topic}",
                        "source": "GoogleTrends"
                    })

        for term in terms[:5]:
            pytrends.build_payload(kw_list=[term], timeframe='now 7-d', geo='BR')
            related_queries = pytrends.related_queries()
            for query_type in ['top', 'rising']:
                queries = related_queries.get(term, {}).get(query_type, None)
                if queries is not None and not queries.empty:
                    for _, query_row in queries.head(5).iterrows():
                        query = query_row['query']
                        if query:
                            results.append({
                                "title": query,
                                "url": f"https://trends.google.com/trends/explore?q={query.replace(' ', '+')}&geo=BR",
                                "content": f"Consulta relacionada no Google Trends: {query}",
                                "source": "GoogleTrends"
                            })
    except Exception as e:
        logger.error(f"Erro ao coletar tendências do Google Trends: {str(e)}", exc_info=True)
        return results
    logger.info(f"Google Trends retornou {len(results)} itens")
    return results

async def scrape_trending_data() -> List[Dict]:
    terms = Config.TRENDING_TOPICS_TERMS
    query = format_query(terms)
    try:
        tasks = [
            asyncio.to_thread(scrape_newsapi, query),
            asyncio.to_thread(scrape_serper, query),
            scrape_reddit(terms),
            asyncio.to_thread(scrape_google_trends, terms),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_results = []
        for i, result in enumerate(results):
            source = ["NewsAPI", "SerpApi", "Reddit", "GoogleTrends"][i]
            if isinstance(result, list):
                all_results.extend(result)
                logger.info(f"Sucesso em {source}: {len(result)} itens coletados")
            elif isinstance(result, Exception):
                logger.error(f"Erro em {source}: {str(result)}", exc_info=True)
        logger.info(f"Total de resultados coletados: {len(all_results)}")
        return all_results
    except Exception as e:
        logger.error(f"Erro ao coletar dados de tendências: {str(e)}", exc_info=True)
        raise

async def scrape_sources(terms: List[str]) -> Tuple[List[Dict], List[str]]:
    logger.info(f"Coletando material bruto para os termos: {terms}")
    query = format_query(terms)
    try:
        tasks = [
            asyncio.to_thread(scrape_newsapi, query),
            asyncio.to_thread(scrape_serper, query),
            scrape_reddit(terms)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_results = []
        for result in results:
            if isinstance(result, list):
                all_results.extend(result)
            elif isinstance(result, Exception):
                logger.error(f"Erro em uma tarefa de scraping: {str(result)}")
        
        source_urls = [item['url'] for item in all_results if item.get('url')]
        logger.info(f"Material bruto coletado: {len(all_results)} itens, {len(source_urls)} URLs")
        return all_results, source_urls
    except Exception as e:
        logger.error(f"Erro ao coletar material bruto: {str(e)}")
        raise