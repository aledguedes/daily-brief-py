# src/schemas.py

# Esquema de resposta JSON para conteúdo multilíngue
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
        # ... (restante da estrutura do excerpt, content, metaDescription) ...
        # ... (Copie o restante do schema JSON do seu arquivo original aqui) ...
        "content": {  # Finalizando o JSON...
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
