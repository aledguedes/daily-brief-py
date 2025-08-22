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

from src.main import main as run_automation
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
            "name": "Materiais",
            "description": "Endpoints para gerenciar materiais brutos e conteúdos gerados.",
        },
        {
            "name": "Seletores",
            "description": "Endpoints para gerenciar seletores usados no scraping de conteúdo.",
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
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


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


# Incluir rotas do api.py
app.include_router(api_router, prefix="/api")


@app.get(
    "/trigger-by-id/{id}",
    response_model=TriggerResponse,
    tags=["Automação"],
    summary="Acionar automação por ID",
    description="Inicia a coleta de material bruto em segundo plano com base em um ID de requisição existente.",
)
async def trigger_by_id(
    id: int,
    background_tasks: BackgroundTasks,
    user_payload: dict = Depends(Auth.verify_token),
    db: Session = Depends(get_db),
):
    logger.info(
        f"Endpoint /trigger-by-id/{id} acionado por {user_payload.get('sub', 'Desconhecido')}."
    )
    user_id = user_payload.get("sub", "anonymous_user")
    task_id = str(uuid.uuid4())

    try:
        request_entry = (
            db.query(AutomationRequest).filter(AutomationRequest.id == id).first()
        )
        if not request_entry:
            logger.warning(f"Registro com ID {id} não encontrado.")
            if Config.LOGS_API_URL:
                log_data = {
                    "action": f"Falha ao executar automação para ID {id}: Registro não encontrado.",
                    "timestamp": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", ""),
                    "level": "WARNING",
                    "report_id": task_id,
                }
                send_logs_to_backend(log_data)
            raise HTTPException(
                status_code=404, detail=f"Registro com ID {id} não encontrado"
            )

        output_format = request_entry.output_format
        theme = request_entry.theme
        logger.info(
            f"Parâmetros do DB: output_format='{output_format}', theme='{theme}'"
        )

        db_service.save_material(
            user_id=user_id,
            task_id=task_id,
            theme=theme,
            raw_material="",
            source_urls=[],
            content_type=output_format,
            status="PENDING_COLLECTION",
        )
        logger.info(f"Tarefa '{task_id}' salva para coleta.")

        background_tasks.add_task(
            run_automation,
            output_format=output_format,
            theme=theme,
            auth_headers={"Authorization": f"Bearer {user_payload.get('token')}"},
            user_id=user_id,
            task_id=task_id,
            return_raw_material_only=True,
        )

        return TriggerResponse(
            trigger_id=id,
            message="Coleta de material iniciada em segundo plano.",
            task_id=task_id,
            status="PENDING_COLLECTION",
        )

    except HTTPException as http_exc:
        logger.error(f"Erro HTTP para ID {id}: {http_exc.detail}")
        if Config.LOGS_API_URL:
            log_data = {
                "action": f"Falha na automação para ID {id}. Erro: {http_exc.detail}",
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", ""),
                "level": "ERROR",
                "report_id": task_id,
            }
            send_logs_to_backend(log_data)
        raise
    except Exception as e:
        logger.error(f"Erro inesperado para ID {id}: {str(e)}")
        if Config.LOGS_API_URL:
            log_data = {
                "action": f"Erro inesperado na automação para ID {id}. Erro: {str(e)}",
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", ""),
                "level": "CRITICAL",
                "report_id": task_id,
            }
            send_logs_to_backend(log_data)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@app.post(
    "/trigger",
    tags=["Automação"],
    summary="Acionar automação síncrona",
    description="Inicia a automação de geração de conteúdo com base em parâmetros fornecidos, executando de forma síncrona.",
)
async def trigger_automation_post(
    request_data: TriggerRequest,
    user_payload: dict = Depends(Auth.verify_token),
):
    logger.info(
        f"Endpoint POST /trigger acionado por {user_payload.get('sub', 'Desconhecido')}."
    )
    output_format = request_data.output_format
    theme = request_data.theme
    user_id = user_payload.get("sub", "anonymous_user")
    task_id = str(uuid.uuid4())

    try:
        output_report = await run_automation(
            output_format=output_format,
            theme=theme,
            auth_headers={"Authorization": f"Bearer {user_payload.get('token')}"},
            user_id=user_id,
            task_id=task_id,
            return_raw_material_only=False,
        )

        if not isinstance(output_report, str):
            output_report = str(output_report)

        return JSONResponse(
            content={
                "message": "Automação executada com sucesso!",
                "report_summary": output_report,
                "parameters": {"output_format": output_format, "theme": theme},
            },
            media_type="application/json; charset=utf-8",
        )

    except ValidationError as e:
        logger.error(f"Erro de validação Pydantic: {str(e)}")
        if Config.LOGS_API_URL:
            log_data = {
                "action": f"Falha de validação Pydantic para POST /trigger. Erro: {str(e)}",
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", ""),
                "level": "ERROR",
                "report_id": task_id,
            }
            send_logs_to_backend(log_data)
        raise HTTPException(
            status_code=400,
            detail={"message": "Erro de validação.", "errors": e.errors()},
        )
    except HTTPException as http_exc:
        logger.error(f"Erro HTTP: {http_exc.detail}")
        if Config.LOGS_API_URL:
            log_data = {
                "action": f"Falha na automação POST /trigger. Erro: {http_exc.detail}",
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", ""),
                "level": "ERROR",
                "report_id": task_id,
            }
            send_logs_to_backend(log_data)
        raise
    except Exception as e:
        logger.error(f"Erro inesperado: {str(e)}")
        if Config.LOGS_API_URL:
            log_data = {
                "action": f"Erro inesperado na automação POST /trigger. Erro: {str(e)}",
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", ""),
                "level": "CRITICAL",
                "report_id": task_id,
            }
            send_logs_to_backend(log_data)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@app.get(
    "/get_task_result/{user_id}/{task_id}",
    response_model=MaterialResponse,
    tags=["Materiais"],
    summary="Consultar resultado de tarefa",
    description="Retorna o status e resultado de uma tarefa específica pelo user_id e task_id.",
)
async def get_task_result_endpoint(user_id: str, task_id: str):
    material = db_service.get_material(user_id, task_id)
    if not material:
        raise HTTPException(
            status_code=404, detail="Tarefa ou material não encontrado."
        )
    return MaterialResponse(**material)


@app.get(
    "/list_user_materials/{user_id}",
    response_model=List[MaterialResponse],
    tags=["Materiais"],
    summary="Listar materiais do usuário",
    description="Lista todos os materiais brutos ou gerados associados a um user_id.",
)
async def list_user_materials_endpoint(user_id: str):
    materials = db_service.list_user_materials(user_id)
    return [MaterialResponse(**m) for m in materials]


@app.post(
    "/submit_final_post",
    tags=["Materiais"],
    summary="Enviar post final",
    description="Envia o conteúdo final aprovado para o backend PostgreSQL e, opcionalmente, deleta o material temporário do SQLite.",
)
async def submit_final_post(
    request_body: SubmitFinalPostRequest,
    user_payload: dict = Depends(Auth.verify_token),
):
    logger.info(
        f"Endpoint /submit_final_post acionado por {user_payload.get('sub', 'Desconhecido')}."
    )
    headers = {"Authorization": f"Bearer {user_payload.get('token')}"}
    post_data_for_pg = request_body.dict(exclude_unset=True)
    task_id_to_delete = post_data_for_pg.pop("task_id_to_delete", None)

    try:
        from src.api import send_post

        response = send_post(post_data_for_pg, headers)
        response.raise_for_status()

        if task_id_to_delete:
            user_id = user_payload.get("sub", "anonymous_user")
            if db_service.delete_material(user_id, task_id_to_delete):
                logger.info(
                    f"Material temporário '{task_id_to_delete}' deletado do SQLite."
                )
            else:
                logger.warning(
                    f"Falha ao deletar material temporário '{task_id_to_delete}' do SQLite."
                )

        return {
            "message": "Post final enviado com sucesso e material temporário limpo (se aplicável).",
            "status": "SUCCESS",
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao enviar post final: {str(e)}")
        detail = (
            f"Erro ao enviar post final: {e.response.text}" if e.response else str(e)
        )
        raise HTTPException(
            status_code=e.response.status_code if e.response else 500, detail=detail
        )
    except Exception as e:
        logger.error(f"Erro inesperado no endpoint /submit_final_post: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


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


if __name__ == "__main__":
    import uvicorn

    logger.info("Iniciando servidor FastAPI...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
