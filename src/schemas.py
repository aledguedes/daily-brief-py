# src/schemas.py
from pydantic import BaseModel, HttpUrl, Field
from typing import List, Optional, Dict, Any, Union


RESPONSE_SCHEMA_V1 = {
    "type": "object",
    "properties": {
        "title": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
            "required": ["PT", "EN", "ES"],
        },
        "excerpt": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
            "required": ["PT", "EN", "ES"],
        },
        "content": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
            "required": ["PT", "EN", "ES"],
        },
        "metaDescription": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"},
            },
            "required": ["PT", "EN", "ES"],
        },
    },
    "required": ["title", "excerpt", "content", "metaDescription"],
}


class UrlStatus(BaseModel):
    """Modelo para logar o status de uma URL após a coleta."""

    url: str
    status: str
    raw_material_id: Optional[str] = None


class AutomationConfigData(BaseModel):
    search_factors: Dict[str, Any]
    collected_urls_log: List[UrlStatus] = Field(default_factory=list)
