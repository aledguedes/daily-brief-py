trending_topics_schema = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "topic_name": {"type": "string"},
            "source": {"type": "string"},
            "relevance_reason": {"type": "string"},
            "url": {"type": ["string", "null"]}
        },
        "required": ["topic_name", "source", "relevance_reason"]
    }
}

trending_suggestion_schema = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "topic_name": {"type": "string"},
            "source": {"type": "string"},
            "relevance_reason": {"type": "string"},
            "url": {"type": ["string", "null"]},
            "status": {"type": "string", "enum": ["NEW", "APPROVED", "REJECTED"]}
        },
        "required": ["topic_name", "source", "relevance_reason"]
    }
}