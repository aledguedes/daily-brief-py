# src/content.py
import json
import logging
import google.generativeai as genai
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from src.config import Config

logger = logging.getLogger(__name__)

# Configuração da API do Gemini
genai.configure(api_key=Config.GEMINI_API_KEY)
model = genai.GenerativeModel(Config.GEMINI_MODEL)

# Esquema de resposta JSON para o Gemini
response_schema = {
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


def determine_content_type(theme_config):
    """
    Determina o tipo de conteúdo a ser gerado com base na configuração do tema.
    """
    content_type = theme_config.get("tipo", Config.OUTPUT_FORMAT)
    if content_type not in ["summary", "article", "social", "informative"]:
        logger.warning(
            f"Tipo de conteúdo inválido '{content_type}' na configuração. Usando 'summary'."
        )
        return "summary"
    return content_type


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(5),
    retry=retry_if_exception_type(Exception),
)
def generate_content(theme, raw_material, content_type="summary"):
    """
    Gera conteúdo (título, excerto, conteúdo principal, meta descrição) usando a API do Gemini.
    O conteúdo principal será gerado em HTML.
    """
    logger.info(f"Gerando conteúdo para o tema '{theme}' (tipo: {content_type})...")

    # Instruções específicas para o Gemini com base no tipo de conteúdo
    content_instruction = ""
    if content_type == "summary":
        content_instruction = (
            "Gere um resumo conciso (máximo 200 palavras por idioma) com base no material bruto fornecido. "
            "O resumo deve capturar os pontos principais do material, ser claro e direto, e adequado para uma visão geral rápida."
        )
    elif content_type == "article":
        content_instruction = (
            "Gere um artigo completo (400-600 palavras por idioma) com base no material bruto fornecido. "
            "Estruture o artigo com uma introdução, 2-3 seções com subtítulos, e uma conclusão. "
            "Use linguagem informativa e envolvente, adequada para publicação online."
        )
    elif content_type == "social":
        content_instruction = (
            "Gere um post para redes sociais (máximo 280 caracteres por idioma) com base no material bruto fornecido. "
            "O post deve ser cativante, incluir uma chamada para ação (CTA), e ser adequado para plataformas como Twitter."
        )
    elif content_type == "informative":
        content_instruction = (
            "Gere um conteúdo informativo (300-500 palavras por idioma) com base no material bruto fornecido. "
            "O conteúdo deve ser educativo, com linguagem clara e estruturada para leitores em busca de conhecimento detalhado."
        )

    prompt = f"""
    Gere conteúdo com base no tema '{theme}' e no material bruto fornecido, no formato '{content_type}'.
    Siga rigorosamente as instruções abaixo:

    1. O campo 'content' (para PT, EN, ES) DEVE ser formatado como HTML válido.
       - Use tags HTML semânticas como <p> (parágrafos), <h2>, <h3> (subtítulos), <ul>, <ol>, <li> (listas), <strong> (negrito), <em> (itálico), e <a> (links).
       - Para links externos, use <a href="URL_COMPLETA" target="_blank" rel="noopener noreferrer">Texto do Link</a>.
       - **NÃO** inclua classes CSS, IDs ou estilos inline nas tags HTML. A estilização será feita pelo frontend.
       - **NÃO** inclua tags <html>, <head>, <body>. Apenas o HTML do corpo do artigo.
       - Se apropriado, inclua tags <img> com URLs de placeholder (ex: <img src="https://placehold.co/800x400?text=Imagem+do+Artigo" alt="Descrição da Imagem">).

    2. Os campos 'title', 'excerpt' e 'metaDescription' (para PT, EN, ES) DEVEM ser texto puro, sem nenhuma tag HTML.

    {content_instruction}

    O resultado final deve ser um objeto JSON, seguindo o esquema fornecido no 'response_schema'.

    Material Bruto:
    {raw_material[:Config.MAX_TEXT_LEN]}

    Exemplo de formato JSON esperado:
    {{
      "title": {{ "PT": "...", "EN": "...", "ES": "..." }},
      "excerpt": {{ "PT": "...", "EN": "...", "ES": "..." }},
      "content": {{ "PT": "<p>...</p><h2>...</h2><ul><li>...</li></ul>", "EN": "<p>...</p>", "ES": "<p>...</p>" }},
      "metaDescription": {{ "PT": "...", "EN": "...", "ES": "..." }}
    }}
    """

    try:
        # Chamada à API do Gemini com o esquema de resposta JSON
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
                temperature=0.7,
            ),
        )

        # O resultado vem como um objeto, precisamos acessar o texto e parsear
        generated_json_str = response.text
        logger.debug(f"Resposta bruta do Gemini: {generated_json_str[:500]}...")

        # Tenta carregar o JSON
        content_data = json.loads(generated_json_str)
        logger.info(
            f"Conteúdo gerado pelo Gemini para '{theme}' (tipo: {content_type}) parseado com sucesso."
        )
        return content_data

    except json.JSONDecodeError as e:
        logger.error(
            f"Erro ao decodificar JSON da resposta do Gemini: {e}. Resposta: {generated_json_str}",
            exc_info=True,
        )
        raise ValueError(
            f"Resposta inválida do Gemini: Não é um JSON válido. Erro: {e}"
        ) from e
    except Exception as e:
        logger.error(
            f"Erro na geração de conteúdo com Gemini para '{theme}': {e}", exc_info=True
        )
        raise
