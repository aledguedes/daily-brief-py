# src/models.py
from sqlalchemy import Column, Integer, String, DateTime, JSON
from datetime import datetime
from src.database import Base

class AutomationRequest(Base):
    __tablename__ = "automation_requests"

    id = Column(Integer, primary_key=True, index=True)
    output_format = Column(String, default="summary") # Default para summary
    theme = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)