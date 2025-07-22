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
from src.api import send_logs_to_backend, MaterialResponse, SubmitFinalPostRequest
from src.auth import Auth  # Importar Auth para usar Auth.verify_token
import src.database_service as db_service  # Importação adicionada para db_service

logger = logging.getLogger(__name__)

app = FastAPI()
security = HTTPBearer()

# JWT_SECRET_BASE64 e ALGORITHM agora são gerenciados pela classe Auth
# Remove a decodificação direta aqui, pois Auth.verify_token fará isso.


class TriggerRequest(BaseModel):
    output_format: str = Config.OUTPUT_FORMAT
    theme: Optional[str] = None


class TriggerResponse(BaseModel):
    # Adicionado o campo trigger_id
    trigger_id: int
    message: str
    task_id: str
    status: str


# A função verify_token do server.py foi removida e sua lógica movida para Auth.verify_token
# Agora, as dependências usarão diretamente Auth.verify_token


@app.on_event("startup")
async def startup_event():
    """
    Evento de startup para inicializar o banco de dados SQLite.
    """
    logger.info("Executando evento de startup: Inicializando banco de dados SQLite...")
    try:
        db_service.init_db()
        logger.info("Banco de dados SQLite inicializado com sucesso no startup.")
    except Exception as e:
        logger.critical(
            f"Erro CRÍTICO ao inicializar o banco de dados SQLite no startup: {e}",
            exc_info=True,
        )
        # Dependendo da sua estratégia de erro, você pode querer levantar a exceção
        # para impedir o início do servidor se o DB for essencial.
        raise


@app.get("/trigger-by-id/{id}", response_model=TriggerResponse)
async def trigger_by_id(
    id: int,  # Este é o trigger_id
    background_tasks: BackgroundTasks,
    user_payload: dict = Depends(
        Auth.verify_token
    ),  # Usar Auth.verify_token diretamente
    db: Session = Depends(get_db),
):
    """
    Aciona a automação para COLETAR E PREPARAR material bruto em segundo plano,
    com base em um registro existente no banco de dados.
    Retorna imediatamente um task_id para consulta de status.
    """
    logger.info(
        f"Endpoint /trigger-by-id/{id} acionado pelo usuário: {user_payload.get('sub', 'Desconhecido')} para coletar material bruto."
    )

    user_id = user_payload.get("sub", "anonymous_user")
    task_id = str(uuid.uuid4())

    try:
        request_entry = (
            db.query(AutomationRequest).filter(AutomationRequest.id == id).first()
        )
        if not request_entry:
            logger.warning(
                f"Registro com ID {id} não encontrado no banco de dados compartilhado."
            )
            if Config.LOGS_API_URL:
                try:
                    log_data = {
                        "action": f"Falha ao executar automação para ID {id}: Registro não encontrado.",
                        "timestamp": datetime.now(timezone.utc),
                        "level": "WARNING",
                        "report_id": task_id,
                    }
                    send_logs_to_backend(log_data)
                except Exception as log_err:
                    logger.error(
                        f"Erro ao enviar log de ID não encontrado para o backend: {str(log_err)}",
                        exc_info=True,
                    )
            raise HTTPException(
                status_code=404, detail=f"Registro com ID {id} não encontrado"
            )

        output_format = request_entry.output_format
        theme = request_entry.theme
        logger.info(
            f"Parâmetros do DB para ID {id}: output_format='{output_format}', theme='{theme}'"
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
        logger.info(
            f"Registro inicial da tarefa '{task_id}' para coleta de material salvo no DB."
        )

        background_tasks.add_task(
            run_automation,
            output_format=output_format,
            theme=theme,
            auth_headers={
                "Authorization": f"Bearer {user_payload.get('token')}"
            },  # Passar o token se necessário para run_automation
            user_id=user_id,
            task_id=task_id,
            return_raw_material_only=True,
        )

        logger.info(
            f"Coleta de material para ID {id} iniciada em segundo plano. Task ID: {task_id}"
        )
        return TriggerResponse(
            trigger_id=id,  # Adicionado o trigger_id aqui
            message="Coleta de material iniciada em segundo plano. Consulte o status usando o task_id.",
            task_id=task_id,
            status="PENDING_COLLECTION",
        )

    except HTTPException as http_exc:
        logger.error(
            f"HTTPException levantada durante a execução para ID {id}: {str(http_exc.detail)}",
            exc_info=True,
        )
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Falha na execução da automação para ID {id}. Erro: {str(http_exc.detail)}",
                    "timestamp": datetime.now(timezone.utc),
                    "level": "ERROR",
                    "report_id": task_id,
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(
                    f"Erro ao enviar log de HTTPException para o backend: {str(log_err)}",
                    exc_info=True,
                )
        raise
    except Exception as e:
        logger.error(
            f"Erro inesperado ao executar automação via /trigger-by-id/{id}: {str(e)}",
            exc_info=True,
        )
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Erro inesperado na execução da automação para ID {id}. Erro: {str(e)}",
                    "timestamp": datetime.now(timezone.utc),
                    "level": "CRITICAL",
                    "report_id": task_id,
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(
                    f"Erro ao enviar log de erro inesperado para o backend: {str(log_err)}",
                    exc_info=True,
                )
        raise HTTPException(
            status_code=500, detail=f"Erro interno ao executar automação: {str(e)}"
        )


@app.post("/trigger")
async def trigger_automation_post(
    request_data: TriggerRequest, user_payload: dict = Depends(Auth.verify_token)
):
    """
    Aciona a automação de geração de posts com base em parâmetros fornecidos no corpo da requisição JSON.
    Este endpoint continua a gerar conteúdo automaticamente (síncrono).
    Requer um token JWT válido.
    """
    logger.info(
        f"Endpoint POST /trigger acionado pelo usuário: {user_payload.get('sub', 'Desconhecido')}"
    )

    output_format = request_data.output_format
    theme = request_data.theme
    user_id = user_payload.get("sub", "anonymous_user")
    task_id = str(uuid.uuid4())

    try:
        logger.info(
            f"Parâmetros recebidos: output_format='{output_format}', theme='{theme}'"
        )

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

        response_content = {
            "message": "Automação executada com sucesso!",
            "report_summary": output_report,
            "parameters": {"output_format": output_format, "theme": theme},
        }
        logger.info("Retornando resposta de sucesso para POST /trigger.")
        return JSONResponse(
            content=response_content, media_type="application/json; charset=utf-8"
        )

    except ValidationError as e:
        logger.error(
            f"Erro de validação Pydantic para POST /trigger: {str(e)}", exc_info=True
        )
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Falha de validação Pydantic para POST /trigger. Erro: {str(e)}",
                    "timestamp": datetime.now(timezone.utc),
                    "level": "ERROR",
                    "report_id": task_id,
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(
                    f"Erro ao enviar log de ValidationError para o backend: {str(log_err)}",
                    exc_info=True,
                )
        errors = e.errors()
        formatted_errors = [
            {"loc": err["loc"], "msg": err["msg"], "type": err["type"]}
            for err in errors
        ]
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Erro de validação do corpo da requisição.",
                "errors": formatted_errors,
            },
        )
    except HTTPException as http_exc:
        logger.error(
            f"HTTPException levantada durante a execução de POST /trigger: {str(http_exc.detail)}",
            exc_info=True,
        )
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Falha na execução da automação POST /trigger. Erro: {str(http_exc.detail)}",
                    "timestamp": datetime.now(timezone.utc),
                    "level": "ERROR",
                    "report_id": task_id,
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(
                    f"Erro ao enviar log de HTTPException para o backend: {str(log_err)}",
                    exc_info=True,
                )
        raise
    except Exception as e:
        logger.error(
            f"Erro inesperado ao executar automação via POST /trigger: {str(e)}",
            exc_info=True,
        )
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Erro inesperado na execução da automação POST /trigger. Erro: {str(e)}",
                    "timestamp": datetime.now(timezone.utc),
                    "level": "CRITICAL",
                    "report_id": task_id,
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(
                    f"Erro ao enviar log de erro inesperado para o backend: {str(log_err)}",
                    exc_info=True,
                )
        raise HTTPException(
            status_code=500, detail=f"Erro interno ao executar automação: {str(e)}"
        )


@app.get("/get_task_result/{user_id}/{task_id}", response_model=MaterialResponse)
async def get_task_result_endpoint(user_id: str, task_id: str):
    """
    Consulta o status e o resultado de uma tarefa específica (coleta ou geração) pelo task_id e user_id.
    """
    material = db_service.get_material(user_id, task_id)
    if not material:
        raise HTTPException(
            status_code=404, detail="Tarefa ou material não encontrado."
        )
    return MaterialResponse(**material)


@app.get("/list_user_materials/{user_id}", response_model=List[MaterialResponse])
async def list_user_materials_endpoint(user_id: str):
    """
    Lista todos os materiais (brutos ou gerados) associados a um user_id.
    """
    materials = db_service.list_user_materials(user_id)
    return [MaterialResponse(**m) for m in materials]


@app.post("/submit_final_post")
async def submit_final_post(
    request_body: SubmitFinalPostRequest,
    user_payload: dict = Depends(Auth.verify_token),
):
    """
    Recebe o conteúdo final, aprovado pelo usuário, e o envia para o backend PostgreSQL.
    Opcionalmente, deleta o registro temporário do SQLite.
    """
    logger.info(
        f"Endpoint /submit_final_post acionado pelo usuário: {user_payload.get('sub', 'Desconhecido')}"
    )

    headers = {"Authorization": f"Bearer {user_payload.get('token')}"}

    post_data_for_pg = request_body.dict(exclude_unset=True)

    task_id_to_delete = post_data_for_pg.pop("task_id_to_delete", None)

    try:
        from src.api import send_post  # Importação explícita para garantir escopo

        response = send_post(post_data_for_pg, headers)
        response.raise_for_status()

        if task_id_to_delete:
            user_id = user_payload.get("sub", "anonymous_user")
            if db_service.delete_material(user_id, task_id_to_delete):
                logger.info(
                    f"Material temporário com task_id '{task_id_to_delete}' deletado do SQLite."
                )
            else:
                logger.warning(
                    f"Falha ao deletar material temporário com task_id '{task_id_to_delete}' do SQLite."
                )

        return {
            "message": "Post final enviado com sucesso para o backend e material temporário limpo (se aplicável).",
            "status": "SUCCESS",
        }

    except requests.exceptions.RequestException as e:
        logger.error(
            f"Erro ao enviar post final para o backend: {str(e)}", exc_info=True
        )
        detail = (
            f"Erro ao enviar post final: {e.response.text}" if e.response else str(e)
        )
        raise HTTPException(
            status_code=e.response.status_code if e.response else 500, detail=detail
        )
    except Exception as e:
        logger.error(
            f"Erro inesperado no endpoint /submit_final_post: {str(e)}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


# Endpoint simples para testar a conexão (mantido)
@app.get("/test-ok")
async def test_ok_endpoint():
    """Endpoint simples para testar a conexão."""
    logger.info("Endpoint /test-ok acionado. Retornando OK.")
    return JSONResponse(
        content={"status": "ok", "message": "Conexão com servidor Python bem-sucedida!"}
    )


# Ponto de entrada principal se rodar o servidor diretamente com uvicorn
if __name__ == "__main__":
    import uvicorn

    logger.info("Iniciando servidor FastAPI...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
