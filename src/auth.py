# src/auth.py
import jwt
import requests
import os
import base64
from datetime import datetime, timezone, timedelta
import logging
from src.config import Config
from tenacity import retry, stop_after_attempt, wait_fixed
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer

logger = logging.getLogger(__name__)

security = HTTPBearer()

TOKEN_FILE = "output/token.txt"

def verify_token(credentials: dict = Depends(security)):
    token = credentials.credentials
    logger.debug(f"Token recebido: {token[:20]}...")
    try:
        # Decodificar a chave JWT de base64
        decoded_key = base64.b64decode(Config.JWT_SECRET_KEY)
        logger.debug(f"Chave JWT decodificada (primeiros 10 bytes): {decoded_key[:10].hex()}...")
        payload = jwt.decode(token, decoded_key, algorithms=["HS512"])
        logger.info(f"Token verificado com sucesso. Payload: {payload}")
        return {"payload": payload, "token": token}
    except base64.binascii.Error as e:
        logger.error(f"Erro ao decodificar chave JWT de base64: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Erro interno: Chave JWT inválida")
    except jwt.InvalidSignatureError as e:
        logger.error(f"Assinatura inválida: {str(e)}. Token: {token[:20]}...", exc_info=True)
        raise HTTPException(status_code=401, detail="Token inválido: Assinatura inválida")
    except jwt.ExpiredSignatureError:
        logger.warning("Token JWT expirado.")
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError as e:
        logger.error(f"Token JWT inválido: {str(e)}", exc_info=True)
        raise HTTPException(status_code=401, detail=f"Token inválido: {str(e)}")
    except Exception as e:
        logger.error(f"Erro inesperado ao verificar token: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Erro interno ao verificar token")

class Auth:
    @staticmethod
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
    def authenticate():
        logger.info("Iniciando processo de autenticação...")
        token = None
        try:
            if os.path.exists(TOKEN_FILE):
                with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                    token = f.read().strip()
                logger.debug(f"Token lido de {TOKEN_FILE}")

                if token:
                    try:
                        payload = jwt.decode(token, options={"verify_signature": False})
                        exp = payload.get("exp")
                        if exp and datetime.fromtimestamp(exp, tz=timezone.utc) > datetime.now(timezone.utc) + timedelta(minutes=5):
                            logger.info(f"Token existente em {TOKEN_FILE} válido até {datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()}.")
                            return {"Authorization": f"Bearer {token}"}
                        else:
                            logger.warning(f"Token existente em {TOKEN_FILE} expirado ou próximo da expiração ({datetime.fromtimestamp(exp, tz=timezone.utc).isoformat() if exp else 'sem expiração'}). Gerando novo token.")
                            return Auth.authenticate_new()
                    except jwt.InvalidTokenError as e:
                        logger.warning(f"Token existente em {TOKEN_FILE} inválido: {str(e)}. Gerando novo token.", exc_info=True)
                        return Auth.authenticate_new()
                else:
                    logger.warning(f"Arquivo {TOKEN_FILE} encontrado, mas vazio. Gerando novo token.")
                    return Auth.authenticate_new()
            else:
                logger.warning(f"Arquivo {TOKEN_FILE} não encontrado. Gerando novo token.")
                return Auth.authenticate_new()
        except Exception as e:
            logger.error(f"Erro inesperado ao tentar ler ou verificar token em {TOKEN_FILE}: {str(e)}. Tentando gerar novo token.", exc_info=True)
            return Auth.authenticate_new()

    @staticmethod
    def authenticate_new():
        logger.info(f"Iniciando processo de autenticação para gerar um novo token em {Config.AUTH_URL}.")
        if not Config.ADMIN_EMAIL or not Config.ADMIN_PASSWORD:
            logger.error("Credenciais de administrador (ADMIN_EMAIL ou ADMIN_PASSWORD) não configuradas. Não é possível autenticar.")
            raise ValueError("Credenciais de administrador ausentes.")

        auth_data = {
            "email": Config.ADMIN_EMAIL,
            "password": Config.ADMIN_PASSWORD
        }
        try:
            auth_response = requests.post(Config.AUTH_URL, json=auth_data, timeout=Config.REQUEST_TIMEOUT)
            auth_response.raise_for_status()

            token = auth_response.json().get("token")
            if not token:
                logger.error(f"Resposta de autenticação de {Config.AUTH_URL} não contém token. Resposta: {auth_response.text}")
                raise ValueError("Falha na autenticação: token ausente na resposta do backend")

            os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(token)
            logger.info(f"Novo token gerado e salvo com sucesso em {TOKEN_FILE}.")
            return {"Authorization": f"Bearer {token}"}
        except requests.exceptions.Timeout:
            logger.error(f"Timeout ao autenticar com {Config.AUTH_URL}", exc_info=True)
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro HTTP/Requisição ao autenticar com {Config.AUTH_URL}: {str(e)}", exc_info=True)
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Resposta de erro do backend: Status {e.response.status_code}, Corpo: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Erro inesperado ao autenticar com {Config.AUTH_URL}: {str(e)}", exc_info=True)
            raise