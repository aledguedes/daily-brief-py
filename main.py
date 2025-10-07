# main.py (na raiz)
from fastapi import FastAPI
from src.api import router as api_router

app = FastAPI(title="DailyBrief API")
app.include_router(api_router, prefix="/api")


# Opcional: rota de teste
@app.get("/")
def root():
    return {"message": "API DailyBrief rodando com sucesso!"}
