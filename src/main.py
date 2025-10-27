# src/main.py
import asyncio
import logging
import os
import json
from datetime import datetime, timezone
import time
from typing import Optional
import uuid

from src.config import Config
from src.auth import Auth
from src.api import (
    get_existing_posts,
    process_material_task,
    send_post,
    generate_content_with_gemini_service,
)
from src.scraping import scrape_sources
from src.content import determine_content_type
from src.utils import save_report, save_payload_to_file, send_logs_to_backend

import src.database_service as db_service

logger = logging.getLogger(__name__)


async def process_theme(
    theme_config,
    headers,
    existing_titles,
    user_id,
    task_id,
    output_format,
    return_raw_material_only=False,
):
    """
    Processa um tema: extrai material bruto e salva em raw_materials (se return_raw_material_only=True)
    ou delega geração completa para process_material_task.
    """
    tema = theme_config.get("tema", "Desconhecido")
    content_type = theme_config.get("tipo", output_format)
    post_start_time = time.time()
    posts_for_theme = []
    logger.info(f"Processando tema '{tema}' com content_type '{content_type}'...")

    try:
        # Etapa 1: Coleta de material bruto
        compiled_text, source_urls = await scrape_sources(tema)
        if not compiled_text:
            logger.error(f"Nenhum material bruto coletado para o tema '{tema}'.")
            db_service.update_material_status(user_id, task_id, "COLLECTION_FAILED")
            return None

        # Etapa 2: Verificar duplicatas
        if any(title in compiled_text for title in existing_titles):
            logger.warning(f"Conteúdo para '{tema}' parece duplicado. Pulando geração.")
            db_service.update_material_status(user_id, task_id, "DUPLICATE_FOUND")
            return None

        # Etapa 3: Salvar materiais brutos em raw_materials
        raw_material_ids = []
        raw_materials = compiled_text.split("\n\n")
        for content in raw_materials:
            if content.strip():
                raw_id = db_service.save_raw_material(task_id, content)
                raw_material_ids.append(raw_id)

        db_service.update_material_raw_material_ids(user_id, task_id, raw_material_ids)
        db_service.update_material_status(user_id, task_id, "RAW_COLLECTED")
        logger.info(
            f"Material bruto salvo para task_id '{task_id}' com {len(raw_material_ids)} IDs."
        )

        if return_raw_material_only:
            logger.info(
                f"Finalizando processamento de '{tema}' com apenas coleta de material."
            )
            return {"task_id": task_id, "status": "RAW_COLLECTED"}

        # Etapa 4: Delegar geração para process_material_task
        asyncio.create_task(
            process_material_task(
                user={
                    "payload": {"sub": user_id},
                    "token": headers.get("Authorization", "").replace("Bearer ", ""),
                },
                automation_request_id=None,
                task_id=task_id,
            )
        )
        logger.info(f"Geração para task_id '{task_id}' iniciada em background.")

        return {"task_id": task_id, "status": "PENDING_GENERATION"}

    except Exception as e:
        logger.error(f"Erro ao processar tema '{tema}': {str(e)}", exc_info=True)
        db_service.update_material_status(user_id, task_id, "FAILED")
        return None


async def main(
    output_format=None,
    theme=None,
    auth_headers=None,
    user_id: str = "anonymous",
    task_id: Optional[str] = None,
    return_raw_material_only: bool = False,
):
    """
    Função principal da automação: orquestra a busca, salvamento de materiais brutos e geração de posts.
    """
    report_lines = [
        f"Relatório de Execução - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ({datetime.now().astimezone().tzinfo})\n"
    ]
    metrics = {"created": 0, "failed": 0, "categories": {}, "retries": 0}
    start_time = time.time()

    if not task_id:
        task_id = str(uuid.uuid4())
        logger.info(f"Novo task_id gerado: {task_id}")

    try:
        # Autenticação
        if auth_headers:
            headers = auth_headers
            logger.info("Usando headers de autenticação passados para a automação.")
        else:
            headers = Auth.authenticate()
            logger.info("Autenticação no backend bem-sucedida.")

        # Buscar posts existentes para evitar duplicatas
        existing_posts = await get_existing_posts(headers)
        existing_titles = [
            post.get("title", {}).get("PT", "") for post in existing_posts
        ]

        themes_to_process = []
        if theme:
            themes_to_process.append(
                {
                    "tema": theme,
                    "categoria": "Específico",
                    "tipo": output_format if output_format else "informative",
                    "generateSocial": True,
                }
            )
            logger.info(f"Processando tema específico: '{theme}'")
        else:
            raise ValueError("Nenhum tema para processar.")

        max_themes_per_run = int(os.getenv("MAX_THEMES_PER_RUN", 5))
        if len(themes_to_process) > max_themes_per_run:
            themes_to_process = themes_to_process[:max_themes_per_run]

        for theme_config in themes_to_process:
            try:
                result = await process_theme(
                    theme_config,
                    headers,
                    existing_titles,
                    user_id,
                    task_id,
                    output_format,
                    return_raw_material_only=return_raw_material_only,
                )
                if result:
                    report_lines.append(
                        f"[SUCESSO] Tema '{theme_config['tema']}' processado: {result['status']}"
                    )
                else:
                    report_lines.append(
                        f"[ERRO] Falha ao processar tema '{theme_config['tema']}'"
                    )
                    metrics["failed"] += 1
            except Exception as e:
                logger.error(
                    f"Erro ao processar tema '{theme_config.get('tema', 'Desconhecido')}': {str(e)}"
                )
                report_lines.append(
                    f"[ERRO] Tema '{theme_config.get('tema', 'Desconhecido')}': {str(e)}"
                )
                metrics["failed"] += 1

        if return_raw_material_only:
            logger.info(
                f"Finalizando main.py com coleta de material bruto. Task ID: {task_id}"
            )
            return {"task_id": task_id, "status": "RAW_COLLECTED"}

        total_time = time.time() - start_time
        report_lines.append(
            f"\n--- Resumo da Execução ---\nMétricas:\n- Posts criados: {metrics['created']}\n- Falhas: {metrics['failed']}\n- Tempo total: {total_time:.2f}s"
        )
        save_report(report_lines)
        return "\n".join(report_lines)

    except Exception as e:
        logger.critical(f"Erro CRÍTICO na automação: {str(e)}", exc_info=True)
        report_lines.append(f"\n[ERRO CRÍTICO] Automação interrompida: {str(e)}")
        save_report(report_lines, is_error=True)
        db_service.update_material_status(user_id, task_id, "FAILED_CRITICAL")
        if Config.LOGS_API_URL:
            log_data = {
                "action": f"Erro CRÍTICO na automação: {str(e)}",
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", ""),
                "level": "CRITICAL",
                "report_id": task_id,
            }
            send_logs_to_backend(log_data)
        raise


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DailyBrief Automation Script")
    parser.add_argument(
        "--format",
        type=str,
        help="Output format: 'summary' or 'article'",
        default=Config.OUTPUT_FORMAT,
    )
    parser.add_argument(
        "--theme", type=str, help="Specific theme to process", default=None
    )
    parser.add_argument(
        "--raw-material-only",
        action="store_true",
        help="If set, only collects raw material and saves to DB, does not generate content with Gemini.",
    )
    parser.add_argument(
        "--user-id", type=str, default="cli_user", help="User ID for CLI execution."
    )
    parser.add_argument(
        "--task-id", type=str, default=None, help="Optional Task ID for CLI execution."
    )
    args = parser.parse_args()

    logger.info(
        f"Rodando script main.py diretamente com output_format='{args.format}', theme='{args.theme}', raw_material_only={args.raw_material_only}, user_id='{args.user_id}', task_id='{args.task_id}'."
    )
    try:
        result = asyncio.run(
            main(
                output_format=args.format,
                theme=args.theme,
                user_id=args.user_id,
                task_id=args.task_id,
                return_raw_material_only=args.raw_material_only,
            )
        )
        if args.raw_material_only:
            logger.info(
                f"Material bruto preparado e salvo no DB para task_id: {result['task_id']}. Status: {result['status']}"
            )
        else:
            logger.info("Execução direta de main.py concluída.")
    except Exception as e:
        logger.error(f"Execução direta de main.py falhou: {str(e)}", exc_info=True)
