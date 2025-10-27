# src/server.py
from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    Query,
    Request,
    Body,
    BackgroundTasks,
)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging
import jwt
import base64
import os
import asyncio
import requests
from datetime import datetime, timezone
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session
import json
from typing import Optional, List, Dict
import uuid

from src.config import Config
from src.database import get_db
from src.models import AutomationRequest
from src.api import (
    send_logs_to_backend,
    MaterialResponse,
    SubmitFinalPostRequest,
    router as api_router,
)
from src.auth import Auth
import src.database_service as db_service

logger = logging.getLogger(__name__)

# Configuração do FastAPI com metadados para o Swagger
app = FastAPI(
    title="DailyBrief API",
    description="API para automação de geração de conteúdo, extração de URLs e integração com Gemini, otimizada para SEO e monetização.",
    version="1.0.0",
    openapi_tags=[
        {
            "name": "Automação",
            "description": "Endpoints para iniciar e gerenciar processos de automação de conteúdo.",
        },
        {
            "name": "trigger-automation",
            "description": "Endpoints para acionar automação de conteúdo.",
        },
        {
            "name": "generate-content",
            "description": "Endpoints para gerar conteúdo com base em materiais brutos.",
        },
        {
            "name": "Teste",
            "description": "Endpoints para testar a conectividade da API.",
        },
    ],
)
security = HTTPBearer()

# Configuração de CORS
origins = [
    "http://localhost:5500",
    "http://localhost:4200",
    "http://localhost:3300",
    "http://127.0.0.1:5500",
    "http://127.0.0.1:3300",  # Adicionado para cobrir variações
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Incluir rotas do api.py
app.include_router(api_router, prefix="/api")


# Modelos Pydantic
class TriggerRequest(BaseModel):
    output_format: str = Config.OUTPUT_FORMAT
    theme: Optional[str] = None


class TriggerResponse(BaseModel):
    trigger_id: str
    message: str
    task_id: str
    status: str


@app.on_event("startup")
async def startup_event():
    """
    Inicializa o banco de dados SQLite no startup.
    """
    logger.info("Inicializando banco de dados SQLite...")
    try:
        db_service.init_db()
        logger.info("Banco de dados SQLite inicializado com sucesso.")
    except Exception as e:
        logger.critical(
            f"Erro ao inicializar o banco de dados SQLite: {e}", exc_info=True
        )
        raise


@app.get(
    "/test-ok",
    tags=["Teste"],
    summary="Testar conexão",
    description="Endpoint para verificar a conectividade com o servidor Python.",
)
async def test_ok_endpoint():
    logger.info("Endpoint /test-ok acionado. Retornando OK.")
    return JSONResponse(
        content={"status": "ok", "message": "Conexão com servidor Python bem-sucedida!"}
    )


@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(
        f"Requisição: {request.method} {request.url} - Origin: {request.headers.get('origin')}"
    )
    response = await call_next(request)
    logger.info(f"Resposta: {response.status_code} - Headers: {response.headers}")
    return response


if __name__ == "__main__":
    import uvicorn

    logger.info("Iniciando servidor FastAPI...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
