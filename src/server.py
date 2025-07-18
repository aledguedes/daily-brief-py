# src/server.py

import logging
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import requests
from bs4 import BeautifulSoup
import re

# Importações dos módulos do nosso projeto
from src.database import get_db, engine, Base
from src.models import (
    Pauta,
    PautaManualRequest,
    ScrapeURLRequest,
    CuratedContentRequest,
    PautaResponse,
)
from src.config import Config
from src.auth import Auth
from src.api import send_post
from src.content import generate_content
from src.scraping import scrape_trending_data
from src.main import extract_trending_topics_with_gemini

# Cria as tabelas no banco de dados, se não existirem
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="DailyBrief Content Engine API",
    description="API unificada para geração de conteúdo automatizada e por curadoria.",
    version="1.0.0",
)

logger = logging.getLogger(__name__)


@app.get("/health", summary="Verifica a saúde da API", tags=["Status"])
def health_check():
    """Endpoint para verificar se a aplicação está funcionando corretamente."""
    return {"status": "ok"}


# --- ETAPA 1: ALIMENTANDO A MESA DE PAUTAS ---


@app.post(
    "/pautas/discover",
    response_model=List[PautaResponse],
    summary="Descobre novas tendências e cria pautas",
    tags=["Pautas"],
)
async def discover_new_pautas(db: Session = Depends(get_db)):
    """
    Dispara o processo de descoberta de tendências, cria pautas
    e as retorna para o frontend.
    """
    logger.info("Iniciando descoberta de tendências para criar pautas...")
    try:
        raw_data = await scrape_trending_data()
        if not raw_data:
            raise HTTPException(
                status_code=404, detail="Nenhum dado bruto de tendência foi encontrado."
            )

        topics = await extract_trending_topics_with_gemini(raw_data)
        if not topics:
            raise HTTPException(
                status_code=404, detail="Nenhum tópico relevante foi extraído pela IA."
            )

        new_pautas = []
        for topic in topics:
            pauta = Pauta(
                title=topic.get("topic_name"),
                source_urls=topic.get("url"),
                relevance_reason=topic.get("relevance_reason"),
                status="SUGGESTED",
                origin="AUTOMATIC",
            )
            db.add(pauta)
            new_pautas.append(pauta)

        db.commit()
        for pauta in new_pautas:
            db.refresh(pauta)

        logger.info(f"{len(new_pautas)} novas pautas criadas a partir de tendências.")
        return new_pautas
    except Exception as e:
        logger.error(f"Erro ao descobrir pautas: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Erro interno ao descobrir tendências: {str(e)}"
        )


@app.post(
    "/pautas/manual",
    response_model=PautaResponse,
    summary="Cria uma pauta a partir de uma URL manual",
    tags=["Pautas"],
)
def create_manual_pauta(request: PautaManualRequest, db: Session = Depends(get_db)):
    """
    Cria uma nova pauta com base em uma URL e tema fornecidos manualmente.
    """
    logger.info(f"Criando pauta manual para o tema: {request.title}")
    pauta = Pauta(
        title=request.title,
        source_urls=request.source_url,
        status="SUGGESTED",
        origin="MANUAL",
    )
    db.add(pauta)
    db.commit()
    db.refresh(pauta)
    logger.info(f"Pauta manual ID {pauta.id} criada com sucesso.")
    return pauta


@app.get(
    "/pautas",
    response_model=List[PautaResponse],
    summary="Lista todas as pautas para a mesa de curadoria",
    tags=["Pautas"],
)
def get_all_pautas(db: Session = Depends(get_db)):
    """
    Retorna todas as pautas do banco de dados para serem exibidas na 'Mesa de Pautas'.
    """
    pautas = db.query(Pauta).order_by(Pauta.created_at.desc()).all()
    return pautas


# --- ETAPA 2: CURADORIA ---


@app.post(
    "/scrape/url",
    summary="Extrai conteúdo de uma URL para curadoria",
    tags=["Curadoria"],
)
def scrape_url_for_curation(request: ScrapeURLRequest):
    """
    Recebe uma URL, faz o scraping do conteúdo e retorna o texto limpo
    para o usuário editar no frontend.
    """
    logger.info(f"Iniciando scraping da URL: {request.url}")
    try:
        resp = requests.get(
            request.url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            },
            timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for script_or_style in soup(
            ["script", "style", "nav", "footer", "header", "aside"]
        ):
            script_or_style.decompose()

        text = soup.get_text(separator="\n", strip=True)
        lines = (line.strip() for line in text.splitlines())
        cleaned_text = "\n".join(
            line for line in lines if len(line) > 2
        )  # Remove linhas muito curtas

        logger.info(
            f"Scraping da URL {request.url} concluído. Tamanho do texto: {len(cleaned_text)} caracteres."
        )
        return {"scraped_content": cleaned_text}
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Erro de requisição ao raspar a URL {request.url}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=400, detail=f"Não foi possível acessar a URL: {str(e)}"
        )


@app.put(
    "/pautas/{pauta_id}/content",
    response_model=PautaResponse,
    summary="Salva o conteúdo curado em uma pauta",
    tags=["Curadoria"],
)
def save_curated_content(
    pauta_id: int, request: CuratedContentRequest, db: Session = Depends(get_db)
):
    """
    Recebe o texto editado pelo usuário e o salva na pauta correspondente,
    atualizando seu status para 'CURATED'.
    """
    logger.info(f"Salvando conteúdo curado para a pauta ID: {pauta_id}")
    pauta = db.query(Pauta).filter(Pauta.id == pauta_id).first()
    if not pauta:
        raise HTTPException(status_code=404, detail="Pauta não encontrada.")

    pauta.curated_content = request.curated_content
    pauta.status = "CURATED"
    db.commit()
    db.refresh(pauta)

    logger.info(
        f"Pauta ID {pauta.id} atualizada com conteúdo curado e status 'CURATED'."
    )
    return pauta


# --- ETAPA 3: GERAÇÃO FINAL ---


@app.post(
    "/posts/generate-from-pauta/{pauta_id}",
    summary="Gera e envia um post a partir de uma pauta curada",
    tags=["Geração"],
)
def generate_post_from_pauta(pauta_id: int, db: Session = Depends(get_db)):
    """
    A rota final. Pega uma pauta pronta, gera o conteúdo com a IA e o envia
    para o backend final.
    """
    logger.info(f"Iniciando geração de post para a pauta ID: {pauta_id}")
    pauta = db.query(Pauta).filter(Pauta.id == pauta_id).first()

    if not pauta:
        raise HTTPException(status_code=404, detail="Pauta não encontrada.")
    if pauta.status != "CURATED":
        raise HTTPException(
            status_code=400,
            detail=f"A pauta deve ter o status 'CURATED'. Status atual: {pauta.status}",
        )
    if not pauta.curated_content:
        raise HTTPException(
            status_code=400,
            detail="A pauta não possui conteúdo curado para gerar o artigo.",
        )

    try:
        generated_data = generate_content(pauta.title, pauta.curated_content, "article")
        if not generated_data:
            pauta.status = "FAILED"
            db.commit()
            raise HTTPException(
                status_code=500,
                detail="Falha ao gerar conteúdo com a IA. O resultado foi vazio.",
            )

        post_payload = {
            "title": generated_data.get("title"),
            "content": generated_data.get("content"),
            "excerpt": generated_data.get("excerpt"),
            "metaDescription": generated_data.get("metaDescription"),
            "author": Config.DEFAULT_AUTHOR,
            "tags": [pauta.title],
            "status": Config.DEFAULT_STATUS,
        }

        headers = Auth.authenticate()
        response = send_post(post_payload, headers)

        pauta.status = "GENERATED"
        db.commit()

        logger.info(f"Post para a pauta ID {pauta.id} gerado e enviado com sucesso.")
        return {
            "message": "Post gerado e enviado com sucesso!",
            "pauta_id": pauta_id,
            "generated_content_titles": generated_data.get("title"),
            "backend_response": response.json(),
        }
    except Exception as e:
        pauta.status = "FAILED"
        db.commit()
        logger.error(
            f"Erro na geração do post para a pauta ID {pauta_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Erro ao gerar o post: {str(e)}")
