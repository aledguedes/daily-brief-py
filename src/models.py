# src/models.py
from sqlalchemy import Column, Integer, String, DateTime, BigInteger
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Literal

from src.database import Base


class AutomationRequest(Base):
    __tablename__ = "tbl_automation_requests"

    id = Column(Integer, primary_key=True, index=True)
    output_format = Column(String(50), nullable=False)
    theme = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<AutomationRequest(id={self.id}, theme='{self.theme}', format='{self.output_format}')>"


class RawMaterial(Base):
    __tablename__ = "tbl_raw_materials"

    id = Column(String(36), primary_key=True, index=True)
    content = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<RawMaterial(id={self.id}, content_length={len(self.content)})>"


class TriggerRequest(BaseModel):
    output_format: str
    theme: Optional[str] = None

    # NOVOS CAMPOS: provider e include_image_prompt
    provider: Literal["gemini", "deepseek"] = (
        Field(  # Usa Literal para validação estrita
            default="gemini",
            description="Provedor de IA a ser usado: 'gemini' ou 'deepseek'.",
        )
    )

    # Campo para indicar a necessidade da geração do prompt de imagem (mantido para compatibilidade)
    # Apesar de ser obrigatório no backend, a flag no frontend pode ser útil para outras lógicas
    include_image_prompt: bool = Field(
        default=True,
        description="Se verdadeiro, gera o prompt de imagem após o conteúdo.",
    )


class PostRequestDTO(BaseModel):
    title: Dict[str, str]
    excerpt: Dict[str, str]
    content: Dict[str, str]
    image: Optional[str] = None
    author: Optional[str] = None
    tags: Optional[List[str]] = None
    category_id: Optional[str] = None
    metaDescription: Dict[str, str]
    affiliateLinks: Optional[Dict[str, str]] = None
    status: Optional[str] = None
    publishedAt: Optional[str] = None
    readTime: Optional[str] = None
    sources: Optional[List[str]] = None
    pass
