# src/content.py
import json
import logging
from typing import Dict, Any, Optional

from src.config import Config
from src.schemas import RESPONSE_SCHEMA_V1
from src.helpers.content_utils import FORMAT_INSTRUCTIONS, determine_content_type

logger = logging.getLogger(__name__)


def build_generation_prompt(theme: str, raw_material: str, content_type: str) -> str:
    logger.info(f"Construindo prompt para o tema '{theme}' (tipo: {content_type})...")

    # 💡 Persona base com escrita natural e tom humano
    persona_instruction = """
    Você é um redator sênior experiente, especializado em tecnologia, inovação e comportamento digital.
    Seu texto deve soar NATURAL e humano — como se fosse escrito por uma pessoa real e apaixonada pelo tema.
    Evite frases genéricas, clichês ou estruturas artificiais típicas de IA.
    Prefira uma linguagem leve, com fluidez e ritmo de conversa, mantendo profissionalismo e credibilidade jornalística.
    """

    # 💡 Regras universais de estrutura e monetização (AdSense + SEO)
    base_format_instruction = f"""
    O conteúdo deve seguir estas regras editoriais:
    1. Utilize parágrafos curtos (máximo 4–5 linhas) para alta escaneabilidade — essencial para AdSense.
    2. Use subtítulos em <h2> e, se necessário, <h3> para dividir seções.
    3. Insira listas (<ul> / <ol>) quando ajudar na compreensão.
    4. Mantenha o SEO natural: use palavras-chave somente se fluírem no texto.
    5. O artigo deve parecer escrito por um humano, com pausas, transições e ritmo narrativo.
    """

    # 💡 Instruções específicas para conteúdo com afiliados
    affiliate_guidelines = """
    Quando o artigo tiver espaço natural para recomendação de produtos, serviços, cursos ou ferramentas, insira o marcador:
    **link afiliado aqui**
    Nunca use HTML nem links reais.
    Essa marcação indica um ponto futuro de edição manual para inserir o link final.
    """

    # 💡 Contexto de afiliação — apenas para tipos comerciais
    content_with_affiliates = [
        "article",
        "blog",
        "review",
        "listicle",
        "newsletter",
        "tutorial",
        "opinion",
    ]
    include_affiliate_note = content_type in content_with_affiliates

    # 💡 Instruções específicas do tipo (centralizadas no content_utils)
    specific_format_instruction = FORMAT_INSTRUCTIONS.get(
        content_type, "Escreva um texto informativo e coeso sobre o tema."
    )

    # 💡 Material bruto — base de referência
    raw_material_context = f"""
    Use o material bruto a seguir como principal e única fonte de informação.
    Não invente dados e não use conhecimento externo.
    MATERIAL BRUTO:
    ---
    {raw_material[:Config.MAX_TEXT_LEN]}
    """

    # 💡 Montagem final do prompt (dinâmica conforme tipo)
    prompt_sections = [
        persona_instruction,
        specific_format_instruction,
        base_format_instruction,
    ]

    if include_affiliate_note:
        prompt_sections.append(affiliate_guidelines)

    prompt_sections.append(raw_material_context)

    # 💡 Estrutura final da tarefa
    main_instruction = f"""
    # INSTRUÇÃO PRINCIPAL:
    Gere um {content_type} completo e natural sobre o tema '{theme}' em Português (PT), Inglês (EN) e Espanhol (ES).
    A estrutura de saída deve ser estritamente JSON, conforme o schema definido.
    """

    prompt_sections.append(main_instruction)
    prompt = "\n\n".join(prompt_sections)

    return prompt


def build_image_prompt(generated_content: Dict[str, Any], content_type: str) -> str:
    """
    Gera o prompt de tradução e descrição da imagem.
    """
    logger.info(f"Gerando prompt de imagem mandatório para tipo: {content_type}...")

    title_pt = generated_content.get("title", {}).get("PT", "")
    excerpt_pt = generated_content.get("excerpt", {}).get("PT", "")

    final_prompt = f"""
    Baseado no conteúdo principal a seguir (Título e Excerto em Português), gere um prompt curto, detalhado e imaginativo para uma IA geradora de imagens (como Midjourney ou Stable Diffusion).
    O prompt de imagem deve estar integralmente em Inglês.
    
    Características Obrigatórias do Prompt de Imagem:
    1. Estilo: visualmente impactante (ex: cinematic lighting, digital art, photorealistic).
    2. Foco: o tema principal do artigo.
    3. Detalhes: 3-4 adjetivos descritivos.
    4. Formato: texto puro, sem comandos (/imagine) ou aspas.
    
    Tipo de Conteúdo: {content_type}
    Título (PT): {title_pt}
    Excerto (PT): {excerpt_pt}
    
    Retorne apenas o texto do prompt de imagem em Inglês.
    """

    return final_prompt
