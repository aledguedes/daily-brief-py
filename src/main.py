# src/main.py
import asyncio
import logging
import os
import json
from datetime import datetime, timezone
import time
import uuid

from src.config import Config
from src.auth import Auth
from src.api import get_existing_posts, send_post, send_social_post, send_logs_to_backend, send_trending_suggestions_to_backend
from src.scraping import scrape_sources, scrape_trending_data
from src.content import generate_content, determine_content_type, extract_trending_topics_with_gemini
from src.utils import save_report, save_payload_to_file
from typing import Optional

logger = logging.getLogger(__name__)

async def process_theme(theme_config, headers, existing_titles, output_format_override=None):
    tema = theme_config.get("tema")
    if not tema:
        logger.error("Configuração de tema inválida: 'tema' não encontrado.")
        return []

    logger.info(f"Iniciando processamento para o tema: '{tema}'")
    posts_for_theme = []
    post_start_time = time.time()

    try:
        compiled_raw_material, source_urls = await scrape_sources([tema])
        logger.info(f"Material bruto coletado para '{tema}'. Tamanho: {len(compiled_raw_material)} chars. URLs: {len(source_urls)}")

        if not compiled_raw_material or len(compiled_raw_material.strip()) < 50:
            logger.warning(f"Não foi possível coletar material bruto suficiente para o tema '{tema}'. Pulando geração de conteúdo.")
            return posts_for_theme

        content_type = output_format_override if output_format_override else determine_content_type(theme_config)
        if content_type not in ["summary", "article", "social", "informative"]:
            logger.warning(f"Tipo de conteúdo inválido '{content_type}' para o tema '{tema}'. Usando 'summary'.")
            content_type = "summary"

        logger.info(f"Gerando conteúdo com Gemini para o tema '{tema}' com content_type '{content_type}'...")

        generated_content_data = generate_content(tema, compiled_raw_material, content_type)

        if not generated_content_data or not all(generated_content_data.get(field) for field in ["title", "excerpt", "content", "metaDescription"]):
            logger.error(f"Falha na geração ou parsing do conteúdo do Gemini para '{tema}' ({content_type}). Conteúdo gerado: {generated_content_data}")
            return posts_for_theme

        title_pt = generated_content_data.get("title", {}).get("PT", "")
        if content_type != "social" and title_pt and title_pt.strip() in existing_titles:
            logger.warning(f"Título duplicado encontrado para o tema '{tema}', tipo '{content_type}': '{title_pt}'. Pulando preparação do post.")
            return posts_for_theme

        post_data = {
            "title": generated_content_data.get("title", {}),
            "content": generated_content_data.get("content", {}),
            "excerpt": generated_content_data.get("excerpt", {}),
            "metaDescription": generated_content_data.get("metaDescription", {}),
            "image": theme_config.get("image", "https://placehold.co/1200x630/000000/FFFFFF?text=DailyBrief"),
            "author": theme_config.get("author", Config.DEFAULT_AUTHOR),
            "tags": theme_config.get("tags", [tema, "DailyBrief", "Automação"]),
            "category": theme_config.get("category", "Geral"),
            "affiliateLinks": theme_config.get("affiliateLinks", {}),
            "status": Config.DEFAULT_STATUS,
            "publishedAt": datetime.now(timezone.utc).isoformat(timespec='microseconds') + 'Z',
            "readTime": theme_config.get("readTime", "5 min"),
        }

        posts_for_theme.append({"tema": tema, "post_data": post_data, "content_type": content_type, "source_urls": source_urls})

        if Config.ENABLE_SOCIAL_API:
            logger.info(f"Gerando post social para o tema '{tema}'...")
            social_generated_data = generate_content(tema, compiled_raw_material, "social")
            if social_generated_data and all(social_generated_data.get(field) for field in ["title", "excerpt", "content", "metaDescription"]):
                social_title = {
                    "PT": f"Social: {social_generated_data.get('title', {}).get('PT', '')}",
                    "EN": f"Social: {social_generated_data.get('title', {}).get('EN', '')}",
                    "ES": f"Social: {social_generated_data.get('title', {}).get('ES', '')}"
                }
                social_post_data = {
                    "socialTitle": social_title,
                    "socialContent": social_generated_data.get("content", {}),
                    "socialImageUrl": theme_config.get("image", "https://placehold.co/800x400/000000/FFFFFF?text=DailyBriefSocial"),
                    "socialMediaPlatform": "GENERIC_SOCIAL_MEDIA",
                    "originalPostId": None,
                    "status": "DRAFT",
                    "publishedSocialAt": datetime.now(timezone.utc).isoformat(timespec='microseconds') + 'Z',
                    "impressions": 0,
                    "clicks": 0,
                    "shares": 0,
                    "likes": 0,
                    "comments": 0,
                    "link": ""
                }
                posts_for_theme.append({"tema": tema, "post_data": social_post_data, "content_type": "social", "source_urls": source_urls})
            else:
                logger.warning(f"Falha na geração ou parsing do conteúdo social do Gemini para '{tema}'.")
        else:
            logger.info("Geração de post social desativada via configuração.")

        elapsed_time = time.time() - post_start_time
        logger.info(f"Processamento para '{tema}' concluído. Tempo: {elapsed_time:.2f}s. {len(posts_for_theme)} posts preparados.")
        return posts_for_theme

    except Exception as e:
        logger.error(f"Erro inesperado durante o processamento do tema '{tema}': {str(e)}", exc_info=True)
        raise

async def main_discover_trends(auth_headers=None):
    report_lines = [f"Relatório de Descoberta de Tendências - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ({datetime.now().astimezone().tzinfo})\n"]
    metrics = {"suggested": 0, "failed": 0, "sources": {}, "retries": 0}
    start_time = time.time()
    report_id = str(uuid.uuid4())

    try:
        if auth_headers:
            headers = auth_headers
            logger.info("Usando headers de autenticação passados para descoberta de tendências.")
        else:
            logger.info("Autenticando no backend com credenciais de admin...")
            headers = Auth.authenticate()
            logger.info("Autenticação no backend bem-sucedida.")

        logger.info("Coletando dados brutos de tendências...")
        raw_trending_data = await scrape_trending_data()

        if not raw_trending_data:
            error_msg = "[ERRO] Nenhum dado bruto de tendências coletado."
            report_lines.append(error_msg)
            logger.error(error_msg)
            raise ValueError("Nenhum dado bruto de tendências coletado.")

        for item in raw_trending_data:
            metrics["sources"][item["source"]] = metrics["sources"].get(item["source"], 0) + 1

        logger.info("Extraindo tópicos em alta com Gemini...")
        trending_topics = await extract_trending_topics_with_gemini(raw_trending_data)

        if not trending_topics:
            error_msg = "[ERRO] Nenhum tópico em alta identificado pelo Gemini."
            report_lines.append(error_msg)
            logger.error(error_msg)
            raise ValueError("Nenhum tópico em alta identificado.")

        metrics["suggested"] = len(trending_topics)
        for topic in trending_topics:
            report_lines.append(f"[SUCESSO] Tópico sugerido: '{topic['topic_name']}' (Fonte: {topic['source']})")

        logger.info(f"Enviando {len(trending_topics)} sugestões de tendências para o backend...")
        response = send_trending_suggestions_to_backend(trending_topics, headers)
        response_json = response.json()
        report_lines.append(f"[SUCESSO] Sugestões enviadas ao backend. Resposta: {response_json}")

        if Config.LOGS_API_URL:
            try:
                log_report_data = {
                    "action": "Relatório de Descoberta de Tendências",
                    "timestamp": datetime.now(timezone.utc),
                    "level": "INFO",
                    "report_summary": "\n".join(report_lines),
                    "metrics": metrics,
                    "duration_seconds": (time.time() - start_time),
                    "report_id": report_id
                }
                send_logs_to_backend(log_report_data, headers)
                logger.info("Relatório de descoberta de tendências enviado para o backend de logs.")
            except Exception as e:
                logger.error(f"Falha ao enviar relatório de tendências para o backend de logs: {str(e)}", exc_info=True)

        total_time = time.time() - start_time
        report_lines.append(f"\n--- Resumo da Descoberta ---\nMétricas:\n- Tópicos sugeridos: {metrics['suggested']}\n- Falhas: {metrics['failed']}\n- Fontes: {', '.join(f'{k}: {v}' for k, v in metrics['sources'].items()) if metrics['sources'] else 'Nenhuma'}\n- Tempo total: {total_time:.2f}s")
        logger.info("Processo de descoberta de tendências finalizado.")
        save_report(report_lines)

        return "\n".join(report_lines)

    except Exception as e:
        logger.critical(f"Erro CRÍTICO na descoberta de tendências: {str(e)}", exc_info=True)
        report_lines.append(f"\n[ERRO CRÍTICO] Descoberta interrompida: {str(e)}")
        save_report(report_lines, is_error=True)
        if Config.LOGS_API_URL:
            try:
                log_report_data = {
                    "action": "Erro na Descoberta de Tendências",
                    "timestamp": datetime.now(timezone.utc),
                    "level": "CRITICAL",
                    "report_summary": "\n".join(report_lines),
                    "metrics": metrics,
                    "duration_seconds": (time.time() - start_time),
                    "report_id": report_id
                }
                send_logs_to_backend(log_report_data, headers)
            except Exception as log_err:
                logger.error(f"Erro ao enviar log de erro para o backend: {str(log_err)}", exc_info=True)
        raise

async def main(output_format=None, theme=None, auth_headers=None, automation_request_id: Optional[int] = None):
    report_lines = [f"Relatório de Envio - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ({datetime.now().astimezone().tzinfo})\n"]
    metrics = {"created": 0, "failed": 0, "categories": {}, "retries": 0}
    start_time = time.time()

    try:
        if auth_headers:
            headers = auth_headers
            logger.info("Usando headers de autenticação passados para a automação.")
        else:
            logger.info("Autenticando no backend com credenciais de admin...")
            headers = Auth.authenticate()
            logger.info("Autenticação no backend bem-sucedida.")

        themes_to_process = []
        if theme:
            themes_to_process.append({
                "tema": theme,
                "categoria": "Específico",
                "tipo": output_format if output_format else "informative",
                "generateSocial": Config.ENABLE_SOCIAL_API
            })
            logger.info(f"Processando tema específico: '{theme}'")
        else:
            error_msg = "[ERRO] Nenhum tema para processar. O tema deve ser fornecido via AutomationRequest ou CLI."
            report_lines.append(error_msg)
            logger.error(error_msg)
            raise ValueError("Nenhum tema para processar.")

        max_themes_per_run = int(os.getenv("MAX_THEMES_PER_RUN", 5))
        if len(themes_to_process) > max_themes_per_run:
            logger.warning(f"Limitando o processamento a {max_themes_per_run} temas dos {len(themes_to_process)} encontrados.")
            themes_to_process = themes_to_process[:max_themes_per_run]

        logger.info("Buscando posts existentes no backend para verificar duplicados...")
        existing_titles = get_existing_posts(headers)
        logger.info(f"Encontrados {len(existing_titles)} títulos de posts existentes em PT.")

        all_posts_to_send = []

        for theme_config in themes_to_process:
            try:
                posts_for_theme = await process_theme(theme_config, headers, existing_titles, output_format)
                all_posts_to_send.extend(posts_for_theme)
            except Exception as e:
                logger.error(f"Erro ao processar tema '{theme_config.get('tema', 'Tema Desconhecido')}': {str(e)}", exc_info=True)
                report_lines.append(f"[ERRO] Falha no processamento do tema '{theme_config.get('tema', 'Tema Desconhecido')}': {str(e)}")
                metrics["failed"] += 1

        logger.info(f"Iniciando fase de envio para o backend Spring Boot. {len(all_posts_to_send)} posts para enviar.")
        if not all_posts_to_send:
            report_lines.append("[INFO] Nenhum post preparado para envio.")
            logger.info("Nenhum post preparado para envio. Pulando fase de envio.")
        else:
            for post_item in all_posts_to_send:
                tema = post_item["tema"]
                post_data = post_item["post_data"]
                content_type = post_item["content_type"]
                source_urls = post_item["source_urls"]

                metrics["categories"][tema] = metrics["categories"].get(tema, 0) + 1

                save_payload_to_file(post_data, tema, content_type)

                try:
                    if content_type == "social":
                        response = send_social_post(post_data, headers)
                        table_name = "social"
                    else:
                        response = send_post(post_data, headers)
                        table_name = "post"

                    response_json = response.json()
                    post_id_spring = response_json.get("id")

                    if post_id_spring:
                        report_lines.append(f"[SUCESSO SPRING] Post '{post_data.get('title', {}).get('PT', 'N/A')}' ({content_type}) criado na tabela {table_name}! ID: {post_id_spring}")
                        report_lines.append(f"Fontes usadas: {', '.join(source_urls)}")
                        metrics["created"] += 1

                        if content_type != "social":
                            social_post_item = next((item for item in all_posts_to_send if item["tema"] == tema and item["content_type"] == "social"), None)
                            if social_post_item:
                                generated_link = f"https://dailybrief.com/post/{post_id_spring}"
                                social_post_item["post_data"]["link"] = generated_link
                                social_post_item["post_data"]["originalPostId"] = post_id_spring
                                logger.info(f"Link e originalPostId do post social para '{tema}' atualizados para: {generated_link}, {post_id_spring}")
                    else:
                        raise ValueError(f"ID do post não retornado pelo Spring Boot na resposta de sucesso (status {response.status_code}). Resposta completa: {response_json}")

                except Exception as e:
                    report_lines.append(f"[ERRO SPRING] Falha ao enviar Post '{post_data.get('title', {}).get('PT', 'Sem Título')}' ({content_type}) para tabela {table_name}: {str(e)}")
                    logger.error(f"Detalhes da falha no envio para Spring Boot para '{tema}' ({content_type}): {str(e)}", exc_info=True)
                    metrics["failed"] += 1

        if Config.LOGS_API_URL:
            try:
                log_report_data = {
                    "action": "Relatório de Execução da Automação",
                    "timestamp": datetime.now(timezone.utc),
                    "level": "INFO",
                    "report_summary": "\n".join(report_lines),
                    "metrics": metrics,
                    "duration_seconds": (time.time() - start_time),
                    "report_id": str(automation_request_id) if automation_request_id is not None else str(uuid.uuid4())
                }
                send_logs_to_backend(log_report_data, headers)
                logger.info("Relatório de execução enviado para o backend de logs.")
            except Exception as e:
                logger.error(f"Falha ao enviar relatório de execução para o backend de logs: {str(e)}", exc_info=True)
        else:
            logger.warning("LOGS_API_URL não configurada. Pulando envio do relatório para o backend de logs.")

        total_time = time.time() - start_time
        report_lines.append(f"\n--- Resumo da Execução ---\nMétricas:\n- Posts criados (Spring Boot): {metrics['created']}\n- Falhas (processamento/envio): {metrics['failed']}\n- Tentativas extras (envio - retries): (Contado internamente na função send_post)\n- Categorias Processadas: {', '.join(f'{k}: {v}' for k, v in metrics['categories'].items()) if metrics['categories'] else 'Nenhuma'}\n- Tempo total: {total_time:.2f}s")
        logger.info("Processo de automação finalizado.")
        logger.info(f"Métricas finais: Criados={metrics['created']}, Falhas={metrics['failed']}")

        save_report(report_lines)

        return "\n".join(report_lines)

    except Exception as e:
        logger.critical(f"Erro CRÍTICO na automação: {str(e)}", exc_info=True)
        report_lines.append(f"\n[ERRO CRÍTICO] Automação interrompida: {str(e)}")
        save_report(report_lines, is_error=True)
        raise