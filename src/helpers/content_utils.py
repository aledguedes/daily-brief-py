# src/utils/content_utils.py
import logging
from typing import Dict, Any
from src.config import Config

logger = logging.getLogger(__name__)

# 💡 Lista central de tipos válidos
VALID_CONTENT_TYPES = [
    "summary", "article", "social", "informative", "news_summary", "blog",
    "newsletter", "thread", "listicle", "opinion", "faq", "infographic",
    "tutorial", "review", "whitepaper"
]

# 💡 Dicionário enriquecido com instruções completas por tipo
FORMAT_INSTRUCTIONS: Dict[str, str] = {
    "article": """
    Escreva um artigo completo e estruturado com introdução, desenvolvimento e conclusão.
    - Tom: Jornalístico e informativo.
    - Público: Leitores gerais interessados em tecnologia e negócios.
    - Estrutura: <h2> para subtítulos, listas quando útil, foco em clareza.
    - Objetivo: Informar e educar, mantendo fluidez e SEO natural.
    """,

    "social": """
    Crie um texto curto (até 280 caracteres) para redes sociais.
    - Tom: Envolvente, direto e humano.
    - Público: Usuários de Twitter/X ou LinkedIn.
    - Estrutura: Frase de impacto + resumo curto. Não incluir hashtags automáticas.
    - Objetivo: Gerar engajamento rápido.
    """,

    "news_summary": """
    Compile resumos dos materiais brutos em formato de boletim de notícias.
    - Tom: Neutro e objetivo.
    - Estrutura: Seções 'Notícia 1', 'Notícia 2', etc.
    - Público: Leitores que desejam atualização rápida.
    - Objetivo: Consolidar informações recentes em um artigo coeso.
    """,

    "blog": """
    Crie um post narrativo e analítico (800–1000 palavras).
    - Tom: Editorial e fluido.
    - Estrutura: História envolvente, transições suaves e subtítulos claros.
    - Objetivo: Entreter e informar com profundidade.
    """,

    "newsletter": """
    Monte um texto formatado para e-mails, com seções curtas e chamadas para ação.
    - Estrutura: 'Destaques da Semana', 'Atualizações sobre [Tema]'.
    - Tom: Conversacional e informativo.
    - Objetivo: Engajar leitores recorrentes e aumentar retenção.
    """,

    "thread": """
    Crie uma sequência de 3 a 5 posts curtos (máx. 280 caracteres cada).
    - Estrutura: numere implicitamente os posts.
    - Tom: Didático e fluido.
    - Público: Usuários de Twitter/X.
    - Objetivo: Explicar um conceito ou tendência passo a passo.
    """,

    "listicle": """
    Escreva um artigo no formato de lista numerada.
    - Estrutura: '10 fatos sobre [Tema]'.
    - Tom: Dinâmico, direto e informativo.
    - Objetivo: Facilitar leitura escaneável e leve.
    """,

    "opinion": """
    Redija um artigo de opinião.
    - Tom: Persuasivo, crítico e argumentativo.
    - Estrutura: introdução da tese, defesa e conclusão reflexiva.
    - Objetivo: Expor um ponto de vista fundamentado.
    """,

    "faq": """
    Crie um artigo de perguntas e respostas.
    - Estrutura: 'Pergunta: ...', 'Resposta: ...'.
    - Público: Leitores que buscam esclarecimento rápido.
    - Objetivo: Tornar o tema acessível e prático.
    """,

    "infographic": """
    Produza um texto descritivo para infográficos.
    - Estrutura: bullet points curtos, dados e fatos diretos.
    - Objetivo: Servir de base textual para design visual.
    """,

    "tutorial": """
    Monte um guia passo a passo.
    - Estrutura: numerada (1, 2, 3...) e clara.
    - Público: Pessoas buscando aprender algo prático.
    - Objetivo: Ensinar de forma simples e objetiva.
    """,

    "review": """
    Escreva uma análise crítica de produto, evento ou conceito.
    - Estrutura: introdução, prós, contras e conclusão.
    - Tom: Neutro, comparativo e técnico.
    - Objetivo: Ajudar na tomada de decisão.
    """,

    "whitepaper": """
    Crie um documento técnico detalhado e analítico (1000+ palavras).
    - Tom: Formal, profundo e baseado em dados.
    - Estrutura: seções técnicas, análises, conclusões.
    - Objetivo: Informar especialistas e decisores.
    """
}

# 💡 Função centralizada — usada em todo o sistema
def determine_content_type(theme_config: Dict[str, Any]) -> str:
    content_type = theme_config.get("tipo", Config.OUTPUT_FORMAT)
    if content_type not in VALID_CONTENT_TYPES:
        logger.warning(
            f"Tipo de conteúdo inválido '{content_type}' na configuração. Usando 'summary'."
        )
        return "summary"
    return content_type
