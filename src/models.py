# src/models.py

from sqlalchemy import Column, Integer, String, DateTime, Enum, Text
from sqlalchemy.sql import func
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from src.database import Base

# -----------------------------------------------
# Modelos do Banco de Dados (SQLAlchemy)
# -----------------------------------------------


class Pauta(Base):
    """
    Modelo da tabela 'tbl_pautas' que armazena as sugestões de conteúdo,
    sejam elas automáticas ou manuais.
    """

    __tablename__ = "tbl_pautas"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False, comment="O tema ou título da pauta")
    source_urls = Column(
        Text, nullable=True, comment="URLs de fonte, separadas por vírgula"
    )
    relevance_reason = Column(
        String(1000),
        nullable=True,
        comment="Justificativa da relevância (gerado pela IA)",
    )

    # O conteúdo curado pelo humano no frontend
    curated_content = Column(
        Text, nullable=True, comment="Conteúdo bruto editado e limpo pelo usuário"
    )

    # Status para controlar o fluxo de trabalho
    status = Column(
        Enum(
            "SUGGESTED", "MANUAL", "CURATED", "GENERATED", "FAILED", name="pauta_status"
        ),
        default="SUGGESTED",
    )

    # Origem da pauta para fácil filtragem
    origin = Column(
        Enum("AUTOMATIC", "MANUAL", name="pauta_origin"), default="AUTOMATIC"
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<Pauta(id={self.id}, title='{self.title}', status='{self.status}')>"


# -----------------------------------------------
# Modelos da API (Pydantic) - Os "Moldes"
# -----------------------------------------------


class PautaManualRequest(BaseModel):
    """Modelo para a requisição de criação de uma pauta manual."""

    title: str = Field(..., min_length=3, description="O tema do artigo a ser gerado.")
    source_url: str = Field(..., description="A URL de fonte para a pauta manual.")


class ScrapeURLRequest(BaseModel):
    """Modelo para a requisição de scraping de uma URL."""

    url: str = Field(..., description="A URL da qual o conteúdo deve ser extraído.")


class CuratedContentRequest(BaseModel):
    """Modelo para a requisição de atualização de uma pauta com o conteúdo curado."""

    curated_content: str = Field(
        ..., description="O texto bruto, limpo e editado pelo usuário."
    )


class PautaResponse(BaseModel):
    """Modelo para a resposta ao retornar uma pauta para o frontend."""

    id: int
    title: str
    source_urls: Optional[str] = None
    relevance_reason: Optional[str] = None
    status: str
    origin: str
    created_at: datetime

    class Config:
        orm_mode = True  # Permite que o Pydantic leia dados de um objeto SQLAlchemy
