from sqlalchemy import Column, Integer, String, DateTime, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from datetime import datetime

from src.database import Base

class AutomationRequest(Base):
    __tablename__ = "tbl_automation_requests"

    id = Column(Integer, primary_key=True, index=True)
    output_format = Column(String(50), nullable=False)
    theme = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<AutomationRequest(id={self.id}, theme='{self.theme}', format='{self.output_format}')>"

class TrendingTopicSuggestion(Base):
    __tablename__ = "tbl_trending_topic_suggestions"

    id = Column(Integer, primary_key=True, index=True)
    topic_name = Column(String(255), nullable=False)
    source = Column(String(50), nullable=False)
    relevance_reason = Column(String(1000), nullable=False)
    url = Column(String(500), nullable=True)
    status = Column(Enum('NEW', 'APPROVED', 'REJECTED', name='trend_status'), default='NEW')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<TrendingTopicSuggestion(id={self.id}, topic_name='{self.topic_name}', status='{self.status}')>"

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

class SocialPostRequestDTO(BaseModel):
    socialTitle: Dict[str, str] = Field(..., alias="title")
    socialContent: Dict[str, str] = Field(..., alias="content")
    socialImageUrl: Optional[str] = None
    socialMediaPlatform: Optional[str] = None
    originalPostId: Optional[int] = None
    status: Optional[str] = None
    publishedSocialAt: Optional[str] = None
    impressions: Optional[int] = 0
    clicks: Optional[int] = 0
    shares: Optional[int] = 0
    likes: Optional[int] = 0
    comments: Optional[int] = 0
    link: Optional[str] = None

class TrendingTopicSuggestionDTO(BaseModel):
    topic_name: str
    source: str
    relevance_reason: str
    url: Optional[str] = None
    status: Optional[str] = "NEW"

class LogRequestDTO(BaseModel):
    reportId: str
    level: str
    action: str
    details: Dict[str, object]
    timestamp: str