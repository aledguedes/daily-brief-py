# src/content.py
import json
import logging
from typing import Dict, Any, Optional

from src.config import Config
from src.schemas import RESPONSE_SCHEMA_V1  # 💡 IMPORT do Schema Centralizado

logger = logging.getLogger(__name__)


def determine_content_type(theme_config: Dict[str, Any]) -> str:
    # ... (Corpo da função é o mesmo) ...
    content_type = theme_config.get("tipo", Config.OUTPUT_FORMAT)
    if content_type not in ["summary", "article", "social", "informative"]:
        logger.warning(
            f"Tipo de conteúdo inválido '{content_type}' na configuração. Usando 'summary'."
        )
        return "summary"
    return content_type


def build_generation_prompt(theme: str, raw_material: str, content_type: str) -> str:
    logger.info(f"Construindo prompt para o tema '{theme}' (tipo: {content_type})...")

    # 💡 1 & 2. PERSONA E PÚBLICO HÍBRIDO
    persona_instruction = """
    Você é um Editor Sênior e Especialista em Transformação Digital e Tecnologia. Sua missão é escrever um artigo de blog com uma linguagem natural, envolvente e informativa. O público é amplo (público geral), mas seu conteúdo deve ser profundo o suficiente para satisfazer especialistas da área de tecnologia.
    Mantenha o tom profissional, didático e inspirador.
    """

    # 💡 3. OBJETIVO DE MONETIZAÇÃO E ESTRUTURA (AdSense)
    format_instruction = f"""
    Siga o formato estrito de resposta JSON e estas regras de formatação HTML no campo 'content':
    1. Utilize parágrafos curtos (máximo 4-5 linhas) para garantir alta escaneabilidade (Crucial para AdSense).
    2. O corpo do artigo deve ter entre 800 e 1500 palavras.
    3. Use subtítulos em H2 (<h2>) para organizar o texto.
    4. Use listas (<ul> ou <ol>) sempre que for útil para detalhar pontos.
    5. O SEO deve ser natural e focado na intenção do usuário, não em repetição forçada de palavras-chave.
    """

    # 💡 4. ESTRUTURA DE INSERÇÃO DE AFILIADOS
    affiliate_suggestion = """
    **NO FINAL do seu conteúdo**, logo antes do encerramento, crie uma seção com o subtítulo '<h2>Aprofunde Seu Conhecimento e Ferramentas</h2>'.
    Nessa seção, escreva um parágrafo de 3-4 linhas sugerindo ao leitor uma CATEGORIA de produto ou serviço que complemente o tema do artigo (Ex: 'Se você se interessou por [TÓPICO], um bom próximo passo é procurar por [TIPO DE PRODUTO/FERRAMENTA] para começar sua jornada'). NÃO inclua links nem tags HTML de afiliação, apenas a sugestão em texto plano.
    """

    # ... (Instruções específicas do conteúdo mantidas, se houver)

    # CONTEXTUALIZAÇÃO DO MATERIAL BRUTO
    raw_material_context = f"""
    Use o material bruto a seguir como **principal e única fonte de informação**. Não use conhecimento externo.
    MATERIAL BRUTO:
    ---
    {raw_material[:Config.MAX_TEXT_LEN]}
    """

    # Monta o prompt final
    prompt = f"""
    {persona_instruction}

    {format_instruction}

    {affiliate_suggestion}

    {raw_material_context}

    # INSTRUÇÃO PRINCIPAL:
    Gere um {content_type} completo e detalhado sobre o tema '{theme}' em Português (PT), Inglês (EN) e Espanhol (ES). A estrutura JSON de saída é mandatória.
    """

    return prompt


def build_image_prompt(generated_content: Dict[str, Any], content_type: str) -> str:
    """
    Gera o prompt de tradução e descrição da imagem.
    """
    logger.info(f"Gerando prompt de imagem mandatório para tipo: {content_type}...")

    # 1. Extrai o título e o excerto do conteúdo em Português
    title_pt = generated_content.get("title", {}).get("PT", "")
    excerpt_pt = generated_content.get("excerpt", {}).get("PT", "")

    # 2. Constrói a instrução
    final_prompt = f"""
    Baseado no conteúdo principal a seguir (Título e Excerto em Português), gere um prompt curto, detalhado e imaginativo para uma IA geradora de imagens (como Midjourney ou Stable Diffusion).
    O prompt de imagem deve estar **integralmente em Inglês**.
    
    Características Obrigatórias do Prompt de Imagem:
    1. **Estilo:** O estilo da imagem deve ser visualmente impactante, como "cinematic lighting", "digital art", ou "photorealistic".
    2. **Foco:** Deve focar no tema principal do artigo.
    3. **Detalhes:** Inclua 3-4 adjetivos descritivos para a cena.
    4. **Formato:** Não inclua barras ou comandos (ex: /imagine). Apenas o texto do prompt.
    
    Tipo de Conteúdo: {content_type}
    Título (PT): {title_pt}
    Excerto (PT): {excerpt_pt}
    
    Retorne APENAS o texto do prompt de imagem em Inglês, sem aspas ou qualquer outra formatação de texto explicativa.
    """

    return final_prompt
