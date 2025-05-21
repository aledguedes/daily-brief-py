# src/auth.py
import jwt
import requests
import os
from datetime import datetime, timezone, timedelta # Importar timedelta
import logging
from src.config import Config
from tenacity import retry, stop_after_attempt, wait_fixed # Para retries

# O logger básico já está configurado em logging_config.py ou server.py
# Remova a configuração básica aqui se já estiver em outro lugar
# logging.basicConfig(...)
logger = logging.getLogger(__name__)

# Caminho do arquivo de token
TOKEN_FILE = "output/token.txt" # Salvar token na pasta output

class Auth:
    @staticmethod
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(5)) # Retenta a autenticação se falhar
    def authenticate():
        """
        Autentica no backend, lendo um token existente ou gerando um novo se necessário.
        Implementa retry para o processo de autenticação.
        """
        logger.info("Iniciando processo de autenticação...")
        token = None
        try:
            # Verifica se o arquivo de token existe
            if os.path.exists(TOKEN_FILE):
                with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                    token = f.read().strip()
                logger.debug(f"Token lido de {TOKEN_FILE}")

                # Verifica a expiração do token (sem verificar a assinatura aqui)
                if token:
                    try:
                        # Adicionado options={"verify_signature": False} para apenas decodificar e verificar expiração
                        payload = jwt.decode(token, options={"verify_signature": False})
                        exp = payload.get("exp")
                        # Verifica se 'exp' existe e se o timestamp é futuro
                        if exp and datetime.fromtimestamp(exp, tz=timezone.utc) > datetime.now(timezone.utc) + timedelta(minutes=5): # Considera válido se expirar em mais de 5 minutos
                            logger.info(f"Token existente em {TOKEN_FILE} válido até {datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()}.")
                            return {"Authorization": f"Bearer {token}"}
                        else:
                            logger.warning(f"Token existente em {TOKEN_FILE} expirado ou próximo da expiração ({datetime.fromtimestamp(exp, tz=timezone.utc).isoformat() if exp else 'sem expiração'}). Gerando novo token.")
                            # Se expirado ou próximo, força a geração de um novo
                            return Auth.authenticate_new()
                    except jwt.InvalidTokenError as e:
                        logger.warning(f"Token existente em {TOKEN_FILE} inválido: {str(e)}. Gerando novo token.", exc_info=True)
                        # Se inválido, força a geração de um novo
                        return Auth.authenticate_new()
                else:
                    logger.warning(f"Arquivo {TOKEN_FILE} encontrado, mas vazio. Gerando novo token.")
                    # Se o arquivo estiver vazio, força a geração de um novo
                    return Auth.authenticate_new()

            else:
                logger.warning(f"Arquivo {TOKEN_FILE} não encontrado. Gerando novo token.")
                # Se o arquivo não existe, força a geração de um novo
                return Auth.authenticate_new()

        except Exception as e:
            logger.error(f"Erro inesperado ao tentar ler ou verificar token em {TOKEN_FILE}: {str(e)}. Tentando gerar novo token.", exc_info=True)
            # Em caso de qualquer erro na leitura/verificação, tenta gerar um novo token
            return Auth.authenticate_new()

    @staticmethod
    def authenticate_new():
        """Gera um novo token de autenticação fazendo uma requisição para o backend."""
        logger.info(f"Iniciando processo de autenticação para gerar um novo token em {Config.AUTH_URL}.")
        if not Config.ADMIN_EMAIL or not Config.ADMIN_PASSWORD:
             logger.error("Credenciais de administrador (ADMIN_EMAIL ou ADMIN_PASSWORD) não configuradas. Não é possível autenticar.")
             raise ValueError("Credenciais de administrador ausentes.")

        auth_data = {
            "email": Config.ADMIN_EMAIL,
            "password": Config.ADMIN_PASSWORD
        }
        try:
            # Adicionado timeout
            auth_response = requests.post(Config.AUTH_URL, json=auth_data, timeout=Config.REQUEST_TIMEOUT)
            auth_response.raise_for_status() # Levanta exceção para status de erro

            token = auth_response.json().get("token")
            if not token:
                logger.error(f"Resposta de autenticação de {Config.AUTH_URL} não contém token. Resposta: {auth_response.text}")
                raise ValueError("Falha na autenticação: token ausente na resposta do backend")

            # Garante que a pasta output existe antes de salvar
            os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(token)
            logger.info(f"Novo token gerado e salvo com sucesso em {TOKEN_FILE}.")

            return {"Authorization": f"Bearer {token}"}

        except requests.exceptions.Timeout:
            logger.error(f"Timeout ao autenticar com {Config.AUTH_URL}", exc_info=True)
            raise # Levanta para o retry da tenacity em authenticate()
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro HTTP/Requisição ao autenticar com {Config.AUTH_URL}: {str(e)}", exc_info=True)
            if hasattr(e, 'response') and e.response is not None:
                 logger.error(f"Resposta de erro do backend: Status {e.response.status_code}, Corpo: {e.response.text}")
            raise # Levanta para o retry da tenacity em authenticate()
        except Exception as e:
            logger.error(f"Erro inesperado ao autenticar com {Config.AUTH_URL}: {str(e)}", exc_info=True)
            raise # Levanta para o retry da tenacity em authenticate()