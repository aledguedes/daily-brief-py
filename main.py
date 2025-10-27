# main.py (raiz) — entrypoint para o Render
import logging
from fastapi import FastAPI
from src.api import router as api_router
import src.database_service as db_service

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("dailybrief")

app = FastAPI(title="DailyBrief API")


@app.on_event("startup")
def startup_init_db():
    logger.info(
        "Startup: inicializando banco de dados via src.database_service.init_db()..."
    )
    try:
        # chama a função que já existe no seu database_service.py
        db_service.init_db()
        logger.info("✅ Banco inicializado com sucesso (init_db executado).")
    except Exception as e:
        logger.exception("❌ Falha ao inicializar o banco de dados no startup: %s", e)
        # Re-raise para que o deploy falhe e você veja o erro imediatamente.
        raise


# inclui suas rotas (prefix /api)
app.include_router(api_router, prefix="/api")


@app.get("/")
def root():
    return {
        "message": "Xandão, boas notícias!!! API DailyBrief rodando com sucesso! IHULLL"
    }
