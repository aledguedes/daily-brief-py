# src/models.py
from sqlalchemy import Column, Integer, String, DateTime, BigInteger
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from pydantic import BaseModel
from typing import List, Dict, Optional

from src.database import Base

class AutomationRequest(Base):
    __tablename__ = "automation_requests"

    id = Column(Integer, primary_key=True, index=True)
    output_format = Column(String(50), nullable=False)
    theme = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<AutomationRequest(id={self.id}, theme='{self.theme}', format='{self.output_format}')>"

class TriggerRequest(BaseModel):
    output_format: str
    theme: Optional[str] = None

class PostRequestDTO(BaseModel):
    title: Dict[str, str]
    excerpt: Dict[str, str]
    content: Dict[str, str]
    image: Optional[str] = None
    author: Optional[str] = None
    tags: Optional[List[str]] = None
    category: Optional[str] = None
    metaDescription: Dict[str, str]
    affiliateLinks: Optional[Dict[str, str]] = None
    status: Optional[str] = None
    publishedAt: Optional[str] = None
    readTime: Optional[str] = None
    sources: Optional[List[str]] = None