# src/scraping.py
import requests
import logging
import time
import os
import asyncio # Importar asyncio
import asyncpraw # Usar asyncpraw para o Reddit
from bs4 import BeautifulSoup
from datetime import datetime, timezone # Importar datetime e timezone
from src.config import Config
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

# Adicionado retry para a chamada à API do Reddit
@retry(stop=stop_after_attempt(3), wait=wait_fixed(5)) # Tenta 3 vezes com 5 segundos de espera
async def scrape_reddit(theme):
    """Busca posts relevantes no Reddit usando asyncpraw."""
    logger.info(f"Buscando posts do Reddit para o tema: '{theme}'...")
    try:
        reddit = asyncpraw.Reddit(
            client_id=Config.REDDIT_CLIENT_ID,
            client_secret=Config.REDDIT_CLIENT_SECRET,
            user_agent=Config.REDDIT_USER_AGENT # User agent é importante para identificar sua aplicação
        )
        # Exemplo: buscar em subreddits relacionados ao tema
        # Adapte os subreddits conforme os temas que você processa
        subreddit_names = "technology+programming+angularjs+javascript" # Exemplo, ajuste conforme necessário
        subreddit = await reddit.subreddit(subreddit_names)
        posts = []
        # Busca por posts quentes (hot) ou relevantes (relevance)
        async for submission in subreddit.hot(limit=20): # Aumentado limite para ter mais material
            # Filtra posts por tema no título ou corpo (adapte a lógica de filtragem)
            if theme.lower() in submission.title.lower() or (submission.selftext and theme.lower() in submission.selftext.lower()):
                 # Limita o tamanho do conteúdo para evitar prompts muito longos
                 content = submission.selftext[:1000] if submission.selftext else ""
                 posts.append({
                     "title": submission.title,
                     "url": submission.url,
                     "content": content,
                     "source": "Reddit", # Identifica a fonte
                     "date": datetime.fromtimestamp(submission.created_utc, tz=timezone.utc).isoformat(timespec='microseconds') + 'Z' # Data de criação em UTC ISO 8601
                 })
        await reddit.close() # Fechar a sessão assíncrona do Reddit
        logger.info(f"Coletados {len(posts)} posts do Reddit para '{theme}'.")
        return posts
    except Exception as e:
        logger.error(f"Erro ao buscar posts do Reddit para '{theme}': {str(e)}", exc_info=True)
        raise # Levanta para o retry


# AGORA COM CHAMADA DIRETA VIA REQUESTS (GET) e retry
@retry(stop=stop_after_attempt(3), wait=wait_fixed(5)) # Tenta 3 vezes com 5 segundos de espera
def scrape_serper(theme, num_results=10):
    """Busca notícias e resultados web relevantes usando a API do Serper.dev via requisição GET."""
    logger.info(f"Buscando resultados do Serper.dev (GET) para o tema: '{theme}'...")
    if not Config.SERPER_API_KEY:
        logger.warning("SERPER_API_KEY não configurada. Pulando busca no Serper.dev.")
        return []

    try:
        serper_url = "https://serper.dev/search"
        headers = {
            "X-API-KEY": Config.SERPER_API_KEY,
            "Content-Type": "application/json" # Serper ainda pode preferir este header mesmo para GET
        }
        params = { # Parâmetros para requisição GET
          "q": theme,
          "hl": Config.LANGUAGE.split('-')[0].lower(),
          "gl": Config.LANGUAGE.split('-')[-1].lower(),
          "num": num_results,
          "tbm": "nws", # Tipo de busca: notícias ('nws'), busca web geral ('')
        }
        logging.debug(f"Chamando Serper.dev com params: {params}")
        time.sleep(1) # Delay para evitar limites de taxa

        # Faz a requisição GET para a API do Serper.dev
        response = requests.get(serper_url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        results = response.json()

        search_results = []
        if "news_results" in results and params.get("tbm") == "nws":
             search_results = results.get("news_results", [])
             logging.info(f"Encontrados {len(search_results)} resultados de notícias no Serper.dev.")
        elif "organic_results" in results:
             search_results = results.get("organic_results", [])
             logging.info(f"Encontrados {len(search_results)} resultados orgânicos no Serper.dev.")
        else:
             logging.warning("Nenhum resultado de busca relevante encontrado no Serper.dev.")


        compiled_results = []
        for item in search_results[:num_results]:
             title = item.get("title", "Sem Título")
             link = item.get("link", "#")
             snippet = item.get("snippet", "Sem resumo disponível.")
             source_name = item.get("source", "Fonte Desconhecida")
             date = item.get("date", "Data Desconhecida") # Data já deve vir formatada da API

             if snippet and snippet.strip():
                  compiled_results.append({
                      "title": title,
                      "url": link,
                      "content": snippet, # Usar snippet como conteúdo
                      "source": source_name,
                      "date": date
                  })
             if link and link != "#":
                  pass # URLs serão coletadas no final

        logger.info(f"Coletados {len(compiled_results)} resultados do Serper.dev para '{theme}'.")
        logger.debug(f"Resultados do Serper.dev (primeiros 5): {compiled_results[:5]}")
        return compiled_results

    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de requisição HTTP ao buscar com Serper.dev para '{theme}': {str(e)}", exc_info=True)
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Resposta de erro do Serper.dev: Status {e.response.status_code}, Corpo: {e.response.text}")
        raise # Levanta para o retry
    except Exception as e:
        logger.error(f"Erro inesperado ao buscar com Serper.dev para '{theme}': {str(e)}", exc_info=True)
        raise # Levanta para o retry


# Funções de scraping síncronas para outras fontes (usar run_in_executor)
@retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
def _scrape_techcrunch_sync(theme):
    """Função síncrona para scraping da TechCrunch."""
    logger.info(f"Buscando na TechCrunch para '{theme}'...")
    collected_items = []
    try:
        techcrunch_url = f"https://techcrunch.com/?s={theme.replace(' ', '+')}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        response = requests.get(techcrunch_url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        articles = soup.find_all("a", class_="post-block__title__link", limit=3)

        for article_link in articles:
             title = article_link.text.strip()
             link = article_link['href']
             summary_element = article_link.find_next_sibling("div", class_="post-block__content")
             snippet = summary_element.text.strip() if summary_element else "Sem resumo disponível na listagem."
             if title:
                  collected_items.append({
                      "title": title,
                      "url": link,
                      "content": snippet,
                      "source": "TechCrunch",
                      "date": datetime.now(timezone.utc).isoformat(timespec='microseconds') + 'Z'
                  })
        if not articles:
            logger.info(f"Sem artigos na TechCrunch encontrados para '{theme}'.")
        return collected_items
    except Exception as e:
        logger.error(f"Erro ao buscar artigos da TechCrunch para '{theme}': {str(e)}", exc_info=True)
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
def _scrape_hackernews_sync(theme):
    """Função síncrona para scraping do HackerNews."""
    logger.info(f"Buscando no HackerNews para '{theme}'...")
    collected_items = []
    try:
        hn_url = "https://hacker-news.firebaseio.com/v0/topstories.json"
        hn_response = requests.get(hn_url, timeout=10)
        hn_response.raise_for_status()
        story_ids = hn_response.json()[:20]
        for story_id in story_ids:
            story_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
            story_response = requests.get(story_url, timeout=10)
            story_response.raise_for_status()
            story = story_response.json()
            if story.get("title") and theme.lower() in story.get("title", "").lower():
                collected_items.append({
                    "title": story.get("title"),
                    "url": story.get("url", f"https://news.ycombinator.com/item?id={story.get('id')}"),
                    "content": story.get("text", ""), # HackerNews pode ter 'text' para conteúdo
                    "source": "HackerNews",
                    "date": datetime.fromtimestamp(story.get("time", 0), tz=timezone.utc).isoformat(timespec='microseconds') + 'Z'
                })
                if len(collected_items) >= 3: break
        if not collected_items:
            logger.info(f"Sem posts relevantes no HackerNews encontrados para '{theme}'.")
        return collected_items
    except Exception as e:
        logger.error(f"Erro ao buscar posts do HackerNews para '{theme}': {str(e)}", exc_info=True)
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
def _scrape_medium_sync(theme):
    """Função síncrona para scraping do Medium."""
    logger.info(f"Buscando no Medium para '{theme}'...")
    collected_items = []
    try:
        medium_url = f"https://medium.com/search?q={theme.replace(' ', '+')}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        response = requests.get(medium_url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        articles = soup.find_all("h2", limit=3) # Ajuste o seletor conforme a estrutura do Medium

        for article_title in articles:
             title = article_title.text.strip()
             # Pode ser necessário um scraping mais profundo para o resumo ou conteúdo completo
             summary_element = article_title.find_next_sibling("h3") # Exemplo, verifique a estrutura real
             snippet = summary_element.text.strip() if summary_element else "Sem resumo disponível na listagem."
             if title:
                  collected_items.append({
                      "title": title,
                      "url": medium_url, # URL da busca, não do artigo específico
                      "content": snippet,
                      "source": "Medium",
                      "date": datetime.now(timezone.utc).isoformat(timespec='microseconds') + 'Z'
                  })
        if not articles:
            logger.info(f"Sem artigos no Medium encontrados para '{theme}'.")
        return collected_items
    except Exception as e:
        logger.error(f"Erro ao buscar artigos do Medium para '{theme}': {str(e)}", exc_info=True)
        raise


# Função principal de scraping que orquestra as chamadas assíncronas e síncronas
async def scrape_sources(theme):
    """
    Agrega resultados de diferentes fontes de scraping para um tema.
    Retorna o material bruto compilado e a lista de URLs fonte.
    """
    logger.info(f"Agregando fontes de scraping para o tema: '{theme}'")
    all_results = []
    all_urls = set() # Usar set para garantir URLs únicas

    # Executa as funções de scraping assíncronas diretamente
    reddit_results = await scrape_reddit(theme)
    all_results.extend(reddit_results)

    # Executa as funções de scraping síncronas em um executor separado
    # para não bloquear o loop de eventos asyncio
    loop = asyncio.get_event_loop()
    serper_results = await loop.run_in_executor(None, scrape_serper, theme)
    techcrunch_results = await loop.run_in_executor(None, _scrape_techcrunch_sync, theme)
    hackernews_results = await loop.run_in_executor(None, _scrape_hackernews_sync, theme)
    medium_results = await loop.run_in_executor(None, _scrape_medium_sync, theme)

    all_results.extend(serper_results)
    all_results.extend(techcrunch_results)
    all_results.extend(hackernews_results)
    all_results.extend(medium_results)

    # Compila o material bruto e coleta URLs
    compiled_text_parts = [f"Informações e snippets sobre '{theme}':\n\n"]
    for item in all_results:
        title = item.get("title", "Sem Título")
        url = item.get("url", "#")
        content = item.get("content", "Sem resumo disponível.")
        source = item.get("source", "Fonte Desconhecida")
        date = item.get("date", "Data Desconhecida")

        if content and content.strip():
             compiled_text_parts.append(f"Título: {title}\nFonte: {source}\nData: {date}\nURL: {url}\nSnippet: {content}\n")

        if url and url != "#":
            all_urls.add(url)

    compiled_text = "\n---\n".join(compiled_text_parts)

    # Limitar o tamanho do texto compilado para evitar prompts do Gemini muito longos
    if len(compiled_text) > Config.MAX_TEXT_LEN:
        logger.warning(f"Material bruto compilado para '{theme}' ({len(compiled_text)} chars) excede o limite ({Config.MAX_TEXT_LEN}). Truncando.")
        compiled_text = compiled_text[:Config.MAX_TEXT_LEN]

    logger.info(f"Material bruto compilado ({len(compiled_text)} chars) e {len(all_urls)} URLs fonte agregados para '{theme}'.")
    logger.debug(f"URLs fonte agregadas: {all_urls}")

    return compiled_text, list(all_urls)