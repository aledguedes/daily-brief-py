# src/auth.py
import requests
import logging
import jwt
import base64
from datetime import datetime, timedelta, timezone
from src.config import Config
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)
security = HTTPBearer()


class Auth:
    _jwt_secret = None

    @classmethod
    def _get_jwt_secret(cls):
        """Decodifica e retorna a chave secreta JWT, armazenando-a em cache."""
        if cls._jwt_secret is None:
            try:
                cls._jwt_secret = base64.b64decode(Config.JWT_SECRET_KEY)
                logger.info("Chave JWT decodificada com sucesso.")
            except Exception as e:
                logger.critical(
                    f"Erro CRÍTICO ao decodificar JWT_SECRET_BASE64: {str(e)}. Não será possível verificar tokens.",
                    exc_info=True,
                )
                cls._jwt_secret = b"fallback_secret_para_evitar_erro_startup_insecure"  # Fallback seguro
        return cls._jwt_secret

    @classmethod
    def authenticate(cls):
        """
        Autentica no backend Spring Boot e retorna os headers de autorização.
        """
        url = Config.AUTH_URL
        payload = {"email": Config.ADMIN_EMAIL, "password": Config.ADMIN_PASSWORD}
        logger.info(f"Tentando autenticar como admin em: {url}")
        try:
            response = requests.post(url, json=payload, timeout=Config.REQUEST_TIMEOUT)
            response.raise_for_status()
            token = response.json().get("token")
            if not token:
                raise ValueError("Token não recebido na resposta de autenticação.")
            logger.info("Autenticação bem-sucedida. Token recebido.")
            return {"Authorization": f"Bearer {token}"}
        except requests.exceptions.Timeout:
            logger.error(f"Timeout ao autenticar em {url}", exc_info=True)
            raise
        except requests.exceptions.RequestException as e:
            logger.error(
                f"Erro HTTP/Requisição ao autenticar em {url}: {str(e)}", exc_info=True
            )
            if hasattr(e, "response") and e.response is not None:
                logger.error(
                    f"Resposta de erro do backend: Status {e.response.status_code}, Corpo: {e.response.text}"
                )
            raise
        except Exception as e:
            logger.error(
                f"Erro inesperado durante a autenticação: {str(e)}", exc_info=True
            )
            raise

    @staticmethod
    def create_token(data: dict):
        """
        Cria um token JWT.
        """
        to_encode = data.copy()
        expire = datetime.now(timezone.utc) + timedelta(
            seconds=Config.TOKEN_EXPIRATION_TIME
        )
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, Auth._get_jwt_secret(), algorithm="HS512")
        return encoded_jwt

    @staticmethod
    def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
        """
        Verifica um token JWT. Usado como dependência do FastAPI.
        """
        token = credentials.credentials
        logger.debug(f"Verificando token JWT: {token[:10]}...")
        try:
            payload = jwt.decode(token, Auth._get_jwt_secret(), algorithms=["HS512"])
            logger.info(f"Token verificado com sucesso. Payload: {payload}")
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("Token JWT expirado.")
            raise HTTPException(status_code=401, detail="Token expirado")
        except jwt.InvalidTokenError as e:
            logger.error(f"Token JWT inválido: {str(e)}", exc_info=True)
            raise HTTPException(status_code=401, detail=f"Token inválido: {str(e)}")
        except Exception as e:
            logger.error(f"Erro inesperado ao verificar token: {str(e)}", exc_info=True)
            # Não enviar logs de erro de token para o backend aqui para evitar loops ou sobrecarga
            raise HTTPException(
                status_code=500, detail="Erro interno ao verificar token"
            )
