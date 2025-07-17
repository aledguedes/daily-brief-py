# src/server.py
from fastapi import FastAPI, HTTPException, Depends, Query, Request, Body
from fastapi.responses import JSONResponse
import logging
import asyncio
import requests
from datetime import datetime, timezone
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session
import json
from typing import Optional
import uuid

from src.main import main as run_automation, main_discover_trends
from src.config import Config
from src.database import get_db
from src.models import AutomationRequest
from src.api import send_logs_to_backend
from src.auth import verify_token

logger = logging.getLogger(__name__)

app = FastAPI()

class TriggerRequest(BaseModel):
    output_format: str
    theme: Optional[str] = None

@app.get("/discover-trends")
async def discover_trends(user: dict = Depends(verify_token)):
    logger.info(f"Endpoint /discover-trends acionado pelo usuário: {user['payload'].get('sub', 'Desconhecido')}")
    try:
        output_report = await main_discover_trends(auth_headers={"Authorization": f"Bearer {user['token']}"})
        response_content = {
            "message": "Descoberta de tendências executada com sucesso!",
            "report_summary": output_report
        }
        logger.info("Descoberta de tendências concluída com sucesso.")
        return JSONResponse(content=response_content, media_type="application/json; charset=utf-8")
    except Exception as e:
        logger.error(f"Erro ao executar descoberta de tendências: {str(e)}", exc_info=True)
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Erro na descoberta de tendências: {str(e)}",
                    "timestamp": datetime.now(timezone.utc),
                    "level": "CRITICAL",
                    "report_id": str(uuid.uuid4())
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(f"Erro ao enviar log de erro para o backend: {str(log_err)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno ao executar descoberta de tendências: {str(e)}")

@app.get("/trigger-by-id/{id}")
async def trigger_by_id(
    id: int,
    user: dict = Depends(verify_token),
    db: Session = Depends(get_db)
):
    logger.info(f"Endpoint /trigger-by-id/{id} acionado pelo usuário: {user['payload'].get('sub', 'Desconhecido')}")
    try:
        request_entry = db.query(AutomationRequest).filter(AutomationRequest.id == id).first()
        if not request_entry:
            logger.warning(f"Registro com ID {id} não encontrado no banco de dados compartilhado.")
            if Config.LOGS_API_URL:
                try:
                    log_data = {
                        "action": f"Falha ao executar automação para ID {id}: Registro não encontrado.",
                        "timestamp": datetime.now(timezone.utc),
                        "level": "WARNING",
                        "report_id": str(id)
                    }
                    send_logs_to_backend(log_data)
                except Exception as log_err:
                    logger.error(f"Erro ao enviar log de ID não encontrado para o backend: {str(log_err)}", exc_info=True)
            raise HTTPException(status_code=404, detail=f"Registro com ID {id} não encontrado")

        output_format = request_entry.output_format
        theme = request_entry.theme
        logger.info(f"Parâmetros do DB para ID {id}: output_format='{output_format}', theme='{theme}'")

        output_report = await run_automation(
            output_format=output_format,
            theme=theme,
            auth_headers={"Authorization": f"Bearer {user['token']}"},
            automation_request_id=id
        )

        if not isinstance(output_report, str):
            output_report = str(output_report)

        response_content = {
            "message": "Automação executada com sucesso!",
            "report_summary": output_report,
            "parameters": {"output_format": output_format, "theme": theme}
        }
        logger.info(f"Automação executada com sucesso para ID {id}.")
        return JSONResponse(content=response_content, media_type="application/json; charset=utf-8")
    except HTTPException as http_exc:
        logger.error(f"HTTPException levantada durante a execução para ID {id}: {str(http_exc.detail)}", exc_info=True)
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Falha na execução da automação para ID {id}. Erro: {str(http_exc.detail)}",
                    "timestamp": datetime.now(timezone.utc),
                    "level": "ERROR",
                    "report_id": str(id)
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(f"Erro ao enviar log de HTTPException para o backend: {str(log_err)}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Erro inesperado ao executar automação via /trigger-by-id/{id}: {str(e)}", exc_info=True)
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Erro inesperado na execução da automação para ID {id}. Erro: {str(e)}",
                    "timestamp": datetime.now(timezone.utc),
                    "level": "CRITICAL",
                    "report_id": str(id)
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(f"Erro ao enviar log de erro inesperado para o backend: {str(log_err)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno ao executar automação: {str(e)}")

@app.post("/trigger")
async def trigger_automation_post(
    request_data: TriggerRequest,
    user: dict = Depends(verify_token),
    db: Session = Depends(get_db)
):
    logger.info(f"Endpoint POST /trigger acionado pelo usuário: {user['payload'].get('sub', 'Desconhecido')}")
    output_format = request_data.output_format
    theme = request_data.theme
    automation_request_id = None
    try:
        new_request_entry = AutomationRequest(output_format=output_format, theme=theme)
        db.add(new_request_entry)
        db.commit()
        db.refresh(new_request_entry)
        automation_request_id = new_request_entry.id
        logger.info(f"Nova AutomationRequest salva com ID: {automation_request_id}")
    except Exception as e:
        logger.error(f"Erro ao salvar AutomationRequest para POST /trigger: {str(e)}", exc_info=True)
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Erro ao salvar AutomationRequest para POST /trigger. Erro: {str(e)}",
                    "timestamp": datetime.now(timezone.utc),
                    "level": "ERROR",
                    "report_id": "NO_REQUEST_ID_GENERATED"
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(f"Erro ao enviar log de erro ao salvar request para o backend: {str(log_err)}", exc_info=True)

    try:
        logger.info(f"Parâmetros recebidos: output_format='{output_format}', theme='{theme}'")
        output_report = await run_automation(
            output_format=output_format,
            theme=theme,
            auth_headers={"Authorization": f"Bearer {user['token']}"},
            automation_request_id=automation_request_id
        )
        if not isinstance(output_report, str):
            output_report = str(output_report)
        response_content = {
            "message": "Automação executada com sucesso!",
            "report_summary": output_report,
            "parameters": {"output_format": output_format, "theme": theme}
        }
        logger.info("Retornando resposta de sucesso para POST /trigger.")
        return JSONResponse(content=response_content, media_type="application/json; charset=utf-8")
    except ValidationError as e:
        logger.error(f"Erro de validação Pydantic para POST /trigger: {str(e)}", exc_info=True)
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Falha de validação Pydantic para POST /trigger. Erro: {str(e)}",
                    "timestamp": datetime.now(timezone.utc),
                    "level": "ERROR",
                    "report_id": str(automation_request_id) if automation_request_id else "NO_REQUEST_ID_GENERATED"
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(f"Erro ao enviar log de ValidationError para o backend: {str(log_err)}", exc_info=True)
        raise HTTPException(status_code=422, detail=f"Erro de validação: {str(e)}")