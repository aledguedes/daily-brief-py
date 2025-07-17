# src/content.py
import json
import logging
import google.generativeai as genai
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from src.config import Config
from src.resources import trending_topics_schema

logger = logging.getLogger(__name__)

# Configuração da API do Gemini
genai.configure(api_key=Config.GEMINI_API_KEY)
model = genai.GenerativeModel(Config.GEMINI_MODEL)

# Esquema de resposta JSON para o Gemini (conteúdo)
response_schema = {
    "type": "object",
    "properties": {
        "title": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"}
            },
            "required": ["PT", "EN", "ES"]
        },
        "excerpt": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"}
            },
            "required": ["PT", "EN", "ES"]
        },
        "content": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"}
            },
            "required": ["PT", "EN", "ES"]
        },
        "metaDescription": {
            "type": "object",
            "properties": {
                "PT": {"type": "string"},
                "EN": {"type": "string"},
                "ES": {"type": "string"}
            },
            "required": ["PT", "EN", "ES"]
        }
    },
    "required": ["title", "excerpt", "content", "metaDescription"]
}

def determine_content_type(theme_config):
    content_type = theme_config.get("tipo", Config.OUTPUT_FORMAT)
    if content_type not in ["summary", "article", "social", "informative"]:
        logger.warning(f"Tipo de conteúdo inválido '{content_type}' na configuração. Usando 'summary'.")
        return "summary"
    return content_type

@retry(stop=stop_after_attempt(3), wait=wait_fixed(5), retry=retry_if_exception_type(Exception))
def generate_content(theme, raw_material, content_type="summary"):
    logger.info(f"Gerando conteúdo para o tema '{theme}' (tipo: {content_type})...")
    if content_type == "summary":
        content_instruction = "Gere um resumo conciso e informativo, com 3-5 parágrafos, formatado em HTML. Use tags <p> para parágrafos. Inclua um título, excerto e meta descrição. O conteúdo deve ser otimizado para SEO."
    elif content_type == "article":
        content_instruction = "Gere um artigo detalhado e aprofundado, com 8-15 parágrafos, formatado em HTML. Use tags <p> para parágrafos e tags <h2>, <h3> para subtítulos. Inclua um título, excerto e meta descrição. O conteúdo deve ser otimizado para SEO e incluir informações relevantes do material bruto."
    elif content_type == "social":
        content_instruction = "Gere um post curto e envolvente para redes sociais (máximo 3 parágrafos), formatado em HTML. Use tags <p> para parágrafos. Inclua um título (curto), excerto e meta descrição. O conteúdo deve ser direto e chamar a atenção."
    elif content_type == "informative":
        content_instruction = "Gere um texto informativo, com 5-10 parágrafos, formatado em HTML. Use tags <p> para parágrafos e, se necessário, tags <ul> ou <ol> para listas. Inclua um título, excerto e meta descrição. O conteúdo deve ser claro e objetivo."
    else:
        content_instruction = "Gere um texto informativo em HTML. Use tags <p> para parágrafos. Inclua um título, excerto e meta descrição."

    prompt = f"""
    Com base no seguinte material bruto, gere conteúdo para um post de blog sobre '{theme}'.

    Instruções Detalhadas para a Estrutura de Saída:
    1.  O campo 'content' (para PT, EN, ES) DEVE ser formatado como HTML válido.
        -   Use tags HTML semânticas como <p> (parágrafos), <h2>, <h3> (subtítulos), <ul>, <ol>, <li> (listas), <strong> (negrito), <em> (itálico), e <a> (links).
        -   Para links externos, use <a href="URL_COMPLETA" target="_blank" rel="noopener noreferrer">Texto do Link</a>.
        -   **NÃO** inclua classes CSS, IDs ou estilos inline nas tags HTML.
        -   **NÃO** inclua tags <html>, <head>, <body>. Apenas o HTML do corpo do artigo.
        -   Se apropriado, inclua tags <img> com URLs de placeholder.
    2.  Os campos 'title', 'excerpt' e 'metaDescription' (para PT, EN, ES) DEVEM ser texto puro, sem nenhuma tag HTML.
    {content_instruction}
    O resultado final deve ser um objeto JSON, seguindo o esquema fornecido no 'response_schema'.
    **GARANTA QUE TODOS OS CARACTERES ESPECIAIS E ACENTUADOS (ex: ç, ã, é, ó, í, à, ü) SEJAM PRESERVADOS CORRETAMENTE EM UTF-8.**

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
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
                temperature=0.7
            )
        )
        generated_json_str = response.text
        logger.debug(f"Resposta bruta do Gemini: {generated_json_str[:500]}...")
        content_data = json.loads(generated_json_str)
        logger.info(f"Conteúdo gerado pelo Gemini para '{theme}' (tipo: {content_type}) parseado com sucesso.")
        return content_data
    except json.JSONDecodeError as e:
        logger.error(f"Erro ao decodificar JSON da resposta do Gemini: {e}. Resposta: {generated_json_str}", exc_info=True)
        raise ValueError(f"Resposta inválida do Gemini: Não é um JSON válido. Erro: {e}") from e
    except Exception as e:
        logger.error(f"Erro na geração de conteúdo com Gemini para '{theme}': {e}", exc_info=True)
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(5), retry=retry_if_exception_type(Exception))
async def extract_trending_topics_with_gemini(raw_trending_data):
    logger.info("Extraindo tópicos em alta com Gemini...")
    raw_data_text = "\n\n".join([f"Título: {item['title']}\nConteúdo: {item['content']}\nFonte: {item['source']}\nURL: {item['url']}" for item in raw_trending_data])

    prompt = f"""
    Com base no seguinte material bruto de tendências, identifique de 3 a {Config.MAX_TRENDING_TOPICS} temas mais emergentes e relevantes para o nicho do DailyBrief (tecnologia, inteligência artificial, sustentabilidade, negócios, inovação, finanças).

    Instruções Detalhadas:
    1. Analise os títulos, conteúdos e fontes fornecidos.
    2. Identifique temas específicos e emergentes (ex: "Blockchain na Logística", "Energia Solar Avançada", "IA Generativa em Finanças").
    3. Para cada tema, forneça:
       - topic_name: Nome conciso do tema (máximo 255 caracteres).
       - source: Nome da fonte (ex: Reddit, NewsAPI, SerpAPI).
       - relevance_reason: Breve justificativa (máximo 1000 caracteres) explicando por que o tema é relevante.
       - url: URL de exemplo associada ao tema, se disponível.
    4. Retorne um array JSON com no máximo {Config.MAX_TRENDING_TOPICS} temas.
    5. GARANTA QUE TODOS OS CARACTERES ESPECIAIS E ACENTUADOS (ex: ç, ã, é, ó, í, à, ü) SEJAM PRESERVADOS CORRETAMENTE EM UTF-8.

    Material Bruto:
    {raw_data_text[:Config.MAX_TEXT_LEN]}

    Exemplo de formato JSON esperado:
    [
      {{
        "topic_name": "Blockchain na Logística",
        "source": "NewsAPI",
        "relevance_reason": "Crescente adoção de blockchain para rastreamento em cadeias de suprimento, mencionado em artigos recentes.",
        "url": "https://example.com/artigo-blockchain"
      }},
      ...
    ]
    """

    try:
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                response_schema=trending_topics_schema,
                temperature=0.7
            )
        )
        generated_json_str = response.text
        logger.debug(f"Resposta bruta do Gemini para tópicos: {generated_json_str[:500]}...")
        topics_data = json.loads(generated_json_str)
        logger.info(f"{len(topics_data)} tópicos em alta extraídos com sucesso.")
        return topics_data
    except json.JSONDecodeError as e:
        logger.error(f"Erro ao decodificar JSON da resposta do Gemini para tópicos: {e}. Resposta: {generated_json_str}", exc_info=True)
        raise ValueError(f"Resposta inválida do Gemini: Não é um JSON válido. Erro: {e}") from e
    except Exception as e:
        logger.error(f"Erro na extração de tópicos com Gemini: {e}", exc_info=True)
        raise