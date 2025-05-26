# src/server.py

from fastapi import FastAPI, HTTPException, Depends, Query, Request, Body
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
import logging
import jwt
import base64
import os
import asyncio
import requests # Necessário para send_logs_to_backend e tratamento de exceções
from datetime import datetime, timezone
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session
import json
from typing import Optional
import uuid # Importado para gerar report_id

from src.main import main as run_automation # Importar a função main do src.main
from src.config import Config # Importar Config
from src.database import get_db # Importar get_db
from src.models import AutomationRequest # Importar AutomationRequest model
from src.api import send_logs_to_backend # Importar função para enviar logs ao backend

logger = logging.getLogger(__name__)

app = FastAPI()
security = HTTPBearer()

# Carregar chave secreta do JWT da variável de ambiente
JWT_SECRET_BASE64 = Config.JWT_SECRET_KEY
try:
    JWT_SECRET = base64.b64decode(JWT_SECRET_BASE64)
    logger.info("Chave JWT decodificada com sucesso.")
except Exception as e:
    logger.critical(f"Erro CRÍTICO ao decodificar JWT_SECRET_BASE64: {str(e)}. Não será possível verificar tokens.", exc_info=True)
    JWT_SECRET = b"fallback_secret_para_evitar_erro_startup_insecure" # Fallback seguro

ALGORITHM = "HS512"

class TriggerRequest(BaseModel):
    """Modelo Pydantic para o corpo da requisição POST /trigger."""
    output_format: str = Config.OUTPUT_FORMAT
    theme: Optional[str] = None # Tema é opcional

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Dependência para verificar o token JWT nos headers."""
    token = credentials.credentials
    logger.debug(f"Verificando token JWT: {token[:10]}...")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        logger.info(f"Token verificado com sucesso. Payload: {payload}")
        return {"payload": payload, "token": token}
    except jwt.ExpiredSignatureError:
        logger.warning("Token JWT expirado.")
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError as e:
        logger.error(f"Token JWT inválido: {str(e)}", exc_info=True)
        raise HTTPException(status_code=401, detail=f"Token inválido: {str(e)}")
    except Exception as e:
        logger.error(f"Erro inesperado ao verificar token: {str(e)}", exc_info=True)
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Erro interno ao verificar token: {str(e)}",
                    "timestamp": datetime.now(timezone.utc), # Usar datetime object aqui
                    "level": "ERROR"
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(f"Erro ao enviar log de erro de token para o backend: {str(log_err)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Erro interno ao verificar token")


# Endpoint para acionar a automação via ID do registro no BD
@app.get("/trigger-by-id/{id}")
async def trigger_by_id(
    id: int,
    user: dict = Depends(verify_token), # Requer token JWT
    db: Session = Depends(get_db) # Requer sessão de BD para buscar AutomationRequest
):
    """
    Aciona a automação de geração de posts com base em um registro existente no banco de dados.
    Requer um token JWT válido.
    """
    logger.info(f"Endpoint /trigger-by-id/{id} acionado pelo usuário: {user['payload'].get('sub', 'Desconhecido')}")
    try:
        # Busca o registro no banco de dados compartilhado
        request_entry = db.query(AutomationRequest).filter(AutomationRequest.id == id).first()
        if not request_entry:
            logger.warning(f"Registro com ID {id} não encontrado no banco de dados compartilhado.")
            if Config.LOGS_API_URL:
                try:
                    log_data = {
                        "action": f"Falha ao executar automação para ID {id}: Registro não encontrado.",
                        "timestamp": datetime.now(timezone.utc), # Usar datetime object aqui
                        "level": "WARNING"
                    }
                    send_logs_to_backend(log_data)
                except Exception as log_err:
                    logger.error(f"Erro ao enviar log de ID não encontrado para o backend: {str(log_err)}", exc_info=True)
            raise HTTPException(status_code=404, detail=f"Registro com ID {id} não encontrado")

        # Extrai os parâmetros do registro do DB
        output_format = request_entry.output_format
        theme = request_entry.theme
        logger.info(f"Parâmetros do DB para ID {id}: output_format='{output_format}', theme='{theme}'")

        # Chama a função principal de automação (src.main.main)
        # O token original é passado para que run_automation possa usá-lo em chamadas para o Spring Boot
        output_report = await run_automation(
            output_format=output_format,
            theme=theme,
            auth_headers={"Authorization": f"Bearer {user['token']}"} # Passa o token recebido
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
                    "timestamp": datetime.now(timezone.utc), # Usar datetime object aqui
                    "level": "ERROR"
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(f"Erro ao enviar log de HTTPException para o backend: {str(log_err)}", exc_info=True)
        raise # Re-levanta a HTTPException original
    except Exception as e:
        logger.error(f"Erro inesperado ao executar automação via /trigger-by-id/{id}: {str(e)}", exc_info=True)
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Erro inesperado na execução da automação para ID {id}. Erro: {str(e)}",
                    "timestamp": datetime.now(timezone.utc), # Usar datetime object aqui
                    "level": "CRITICAL"
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(f"Erro ao enviar log de erro inesperado para o backend: {str(log_err)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno ao executar automação: {str(e)}")


# Endpoint para acionar a automação via corpo da requisição (POST com JSON)
@app.post("/trigger")
async def trigger_automation_post(
    request_data: TriggerRequest, # Usa o modelo Pydantic para validação automática
    user: dict = Depends(verify_token)
):
    """
    Aciona a automação de geração de posts com base em parâmetros fornecidos no corpo da requisição JSON.
    Requer um token JWT válido.
    """
    logger.info(f"Endpoint POST /trigger acionado pelo usuário: {user['payload'].get('sub', 'Desconhecido')}")

    output_format = request_data.output_format
    theme = request_data.theme

    try:
        logger.info(f"Parâmetros recebidos: output_format='{output_format}', theme='{theme}'")

        # Chama a função principal de automação (src.main.main)
        # Reutiliza o token recebido para chamadas internas da automação
        output_report = await run_automation(
            output_format=output_format,
            theme=theme,
            auth_headers={"Authorization": f"Bearer {user['token']}"} # Passa o token recebido
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
                    "timestamp": datetime.now(timezone.utc), # Usar datetime object aqui
                    "level": "ERROR"
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(f"Erro ao enviar log de ValidationError para o backend: {str(log_err)}", exc_info=True)
        errors = e.errors()
        formatted_errors = [{"loc": err["loc"], "msg": err["msg"], "type": err["type"]} for err in errors]
        raise HTTPException(status_code=400, detail={"message": "Erro de validação do corpo da requisição.", "errors": formatted_errors})
    except HTTPException as http_exc:
        logger.error(f"HTTPException levantada durante a execução de POST /trigger: {str(http_exc.detail)}", exc_info=True)
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Falha na execução da automação POST /trigger. Erro: {str(http_exc.detail)}",
                    "timestamp": datetime.now(timezone.utc), # Usar datetime object aqui
                    "level": "ERROR"
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(f"Erro ao enviar log de HTTPException para o backend: {str(log_err)}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Erro inesperado ao executar automação via POST /trigger: {str(e)}", exc_info=True)
        if Config.LOGS_API_URL:
            try:
                log_data = {
                    "action": f"Erro inesperado na execução da automação POST /trigger. Erro: {str(e)}",
                    "timestamp": datetime.now(timezone.utc), # Usar datetime object aqui
                    "level": "CRITICAL"
                }
                send_logs_to_backend(log_data)
            except Exception as log_err:
                logger.error(f"Erro ao enviar log de erro inesperado para o backend: {str(log_err)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno ao executar automação: {str(e)}")

# Endpoint simples para testar a conexão (mantido)
@app.get("/test-ok")
async def test_ok_endpoint():
    """Endpoint simples para testar a conexão."""
    logger.info("Endpoint /test-ok acionado. Retornando OK.")
    return JSONResponse(content={"status": "ok", "message": "Conexão com servidor Python bem-sucedida!"})

# Ponto de entrada principal se rodar o servidor diretamente com uvicorn
if __name__ == "__main__":
    import uvicorn
    logger.info("Iniciando servidor FastAPI...")
    uvicorn.run(app, host="0.0.0.0", port=8000)