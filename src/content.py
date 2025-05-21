# src/content.py
import json
import re
import logging
from datetime import datetime
from src.config import Config
# CORREÇÃO AQUI: Removida a importação de translate_text, pois não é mais usada
# from src.utils import translate_text # Se ainda precisar de tradução separada
from tenacity import retry, stop_after_attempt, wait_fixed # Para retries
import requests # Para a chamada real à API do Gemini

logger = logging.getLogger(__name__)

# Adicionado retry para a chamada à API do Gemini
@retry(stop=stop_after_attempt(5), wait=wait_fixed(10)) # Tenta 5 vezes com 10 segundos de espera
def call_gemini_api(prompt):
    """
    Faz a chamada real para a API do Gemini.
    Recebe o prompt formatado e retorna o texto gerado.
    Implementa retry para aumentar a robustez.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{Config.GEMINI_MODEL}:generateContent?key={Config.GEMINI_API_KEY}"
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ]
    }

    logger.info(f"Chamando Gemini API ({Config.GEMINI_MODEL})...")
    logger.debug(f"Prompt enviado para Gemini:\n{prompt[:500]}...") # Loga parte do prompt

    try:
        # Adicionado timeout
        response = requests.post(url, headers=headers, json=payload, timeout=Config.REQUEST_TIMEOUT)
        response.raise_for_status() # Levanta exceção para status de erro (4xx ou 5xx)

        response_json = response.json()
        # Extrai o texto gerado. Adapte o caminho se a estrutura da resposta mudar.
        generated_text = response_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")

        if not generated_text:
             logger.warning(f"Resposta da Gemini API não contém texto gerado. Resposta completa: {json.dumps(response_json, indent=2)}")
             raise ValueError("Resposta da Gemini API vazia ou sem texto gerado.")

        logger.info("Resposta da Gemini API recebida com sucesso.")
        logger.debug(f"Texto gerado pelo Gemini (início):\n{generated_text[:500]}...")
        return generated_text

    except requests.exceptions.Timeout:
        logger.error(f"Timeout ao chamar Gemini API em {url}. Tentando novamente...", exc_info=True)
        raise # Levanta para o retry
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro HTTP/Requisição ao chamar Gemini API em {url}: {str(e)}. Tentando novamente...", exc_info=True)
        if hasattr(e, 'response') and e.response is not None:
             logger.error(f"Resposta de erro da Gemini API: Status {e.response.status_code}, Corpo: {e.response.text}")
        raise # Levanta para o retry
    except Exception as e:
        logger.error(f"Erro inesperado ao chamar Gemini API em {url}: {str(e)}", exc_info=True)
        raise # Levanta para o retry


def generate_content(theme, compiled_raw_material, content_type):
    """
    Gera conteúdo multilíngue para um tema usando a API do Gemini.
    Recebe material bruto e tipo de conteúdo desejado.
    """
    logger.info(f"Iniciando geração de conteúdo para tema '{theme}' com tipo '{content_type}'...")

    # Construir o prompt para o Gemini
    prompt = build_gemini_prompt(theme, compiled_raw_material, content_type, Config.TARGET_LANGUAGES)
    logger.debug(f"Prompt construído para Gemini:\n{prompt[:500]}...")

    try:
        # Chamar a API do Gemini com o prompt
        generated_text = call_gemini_api(prompt)
        logger.debug(f"Texto bruto recebido do Gemini:\n{generated_text[:500]}...")

        # Parsear a resposta do Gemini para extrair os campos multilíngues
        generated_data = parse_gemini_output(generated_text, Config.TARGET_LANGUAGES)

        # Verificação básica para garantir que os campos principais foram parseados
        if not generated_data or not all(generated_data.get(field) for field in ["title", "excerpt", "content", "metaDescription"]):
             logger.error(f"Parsing da resposta do Gemini falhou ou campos principais ausentes para o tema '{theme}'. Dados parseados: {generated_data}")
             raise ValueError("Falha ao parsear ou conteúdo principal ausente na resposta do Gemini.")

        logger.info(f"Conteúdo gerado e parseado com sucesso para o tema '{theme}'.")
        logger.debug(f"Dados gerados e parseados: {json.dumps(generated_data, ensure_ascii=False, indent=2)}")
        return generated_data

    except Exception as e:
        logger.error(f"Erro no processo de geração ou parsing de conteúdo para '{theme}': {str(e)}", exc_info=True)
        # Re-levanta a exceção para ser tratada no main.py
        raise


def build_gemini_prompt(theme, compiled_raw_material, content_type, target_languages):
    """Constrói o prompt otimizado para a API do Gemini."""
    logger.debug(f"Construindo prompt Gemini para tema '{theme}', tipo '{content_type}', idiomas: {target_languages}")

    format_instructions = ""
    if content_type == "summary":
        format_instructions = "Synthesize a *concise and engaging summary* in a newsletter/summary style. Focus on the key developments and main points. Keep the content brief and to the point."
    elif content_type == "article":
        format_instructions = "Synthesize a *detailed and comprehensive article*. Expand on the key developments, provide insights, explore the future outlook, and include relevant details from the snippets. Use paragraphs and a clear structure with potential subheadings (use markdown ## for subheadings)."
    elif content_type == "social":
         format_instructions = "Synthesize a *very short and engaging social media post* (max ~280 characters total, including title/excerpt). Focus on a hook and a call to action (e.g., 'Leia mais: [link]'). Keep the Title and Excerpt extremely brief or omit them in the final text if they exceed the character limit.";
    elif content_type == "informative":
         format_instructions = "Synthesize an *informative and neutral text* based on the provided snippets. Focus on presenting facts and key information clearly and objectively. Use paragraphs."
    else:
         logger.warning(f"Tipo de conteúdo '{content_type}' desconhecido. Usando 'summary' como padrão para instruções do prompt.")
         format_instructions = "Synthesize a *concise and engaging summary*."


    prompt = f"""You are a content curator for a blog focused on trending topics.
Based on the following information snippets related to the topic '{theme}', {format_instructions}
Ensure the content is original (do not copy verbatim from snippets).
Generate the following elements, optimized for SEO:

1.  A compelling and keyword-rich Title (max ~60 characters).
2.  A concise and informative Excerpt/Summary (max ~160 characters, suitable for social media/previews).
3.  The main Content of the blog post.
4.  A relevant and keyword-rich Meta Description (max ~160 characters, for search engines).

Provide these elements for each of the following languages: {', '.join(target_languages)}.
Ensure the response is clearly structured for each language and field using the following markers:

### [LANG]
## Title:
[Generated Title]
## Excerpt:
[Generated Excerpt]
## Content:
[Generated Content]
## Meta Description:
[Generated Meta Description]

Information Snippets to synthesize from:

{compiled_raw_material}""" # Inclui o material bruto compilado

    return prompt


def parse_gemini_output(generated_text, target_languages):
    """
    Parseia o texto gerado pela API do Gemini para extrair os campos multilíngues.
    Espera que o texto use os marcadores ### [LANG] e ## Field:.
    """
    logger.debug("Iniciando parsing da saída do Gemini...")
    generated_data = {
        "title": {},
        "excerpt": {},
        "content": {},
        "metaDescription": {}
    }

    lines = generated_text.split('\n')
    current_lang = None
    current_field = None
    current_content_lines = []

    # Mapeamento dos marcadores para os nomes dos campos no dicionário
    field_mapping = {
        "## Title:": "title",
        "## Excerpt:": "excerpt",
        "## Content:": "content",
        "## Meta Description:": "metaDescription",
        "## MetaDescription:": "metaDescription", # Adicionado variação comum
    }

    for line in lines:
        trimmed_line = line.strip()

        if trimmed_line.startswith("### "):
            # Encontrou um marcador de idioma
            if current_field is not None and current_lang is not None:
                # Salva o conteúdo do campo anterior antes de mudar de idioma
                generated_data[current_field][current_lang] = "\n".join(current_content_lines).strip()

            current_lang_candidate = trimmed_line.replace("### ", "").strip()
            # Verifica se o idioma candidato está na lista de idiomas alvo
            if current_lang_candidate in target_languages:
                 current_lang = current_lang_candidate
                 logger.debug(f"Parseando conteúdo para idioma: {current_lang}")
            else:
                 logger.warning(f"Idioma '{current_lang_candidate}' encontrado na saída do Gemini, mas não está na lista de idiomas alvo. Ignorando conteúdo para este idioma.")
                 current_lang = None # Ignora este bloco de idioma
            current_content_lines = []
            current_field = None # Reseta o campo ao mudar de idioma
            continue

        is_field_marker = False
        for marker, field_name in field_mapping.items():
            if trimmed_line.startswith(marker):
                # Encontrou um marcador de campo
                if current_field is not None and current_lang is not None:
                    # Salva o conteúdo do campo anterior antes de mudar de campo
                    generated_data[current_field][current_lang] = "\n".join(current_content_lines).strip()

                current_field = field_name
                current_content_lines = []
                is_field_marker = True
                logger.debug(f"  Parseando campo: {current_field}")
                break # Sai do loop de marcadores de campo

        if not is_field_marker and current_field is not None and current_lang is not None:
            # A linha não é um marcador e estamos dentro de um campo e idioma válidos
            current_content_lines.append(line) # Adiciona a linha ao conteúdo atual (mantém a formatação/markdown)

    # Salva o conteúdo do último campo após o loop
    if current_field is not None and current_lang is not None:
        generated_data[current_field][current_lang] = "\n".join(current_content_lines).strip()

    # Remove campos de idiomas que não foram encontrados ou foram ignorados
    for field in generated_data:
        generated_data[field] = {lang: text for lang, text in generated_data[field].items() if lang in target_languages and text} # Filtra por idiomas alvo e remove vazios


    logger.debug("Parsing da saída do Gemini concluído.")
    return generated_data


def determine_content_type(config):
    """Determina o tipo de conteúdo com base na configuração do tema."""
    # Esta função pode ser mais complexa se houver outras regras
    return config.get("output_format", Config.OUTPUT_FORMAT).lower()