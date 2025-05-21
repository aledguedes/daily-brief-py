# src/main.py
import asyncio
import logging
import os
import json
from datetime import datetime, timezone # Importar timezone para logs
import time # Para time.time()
import uuid # Para gerar IDs únicos para payloads

from src.config import Config
from src.auth import Auth
from src.api import get_existing_posts, send_post, send_logs_to_backend
from src.scraping import scrape_sources
from src.content import generate_content, determine_content_type
from src.utils import save_report, save_payload_to_file # Importar save_payload_to_file

logger = logging.getLogger(__name__)

# Cache para temas (dicionário simples com timestamp)
# Em um ambiente de produção, isso seria um cache mais robusto (Redis, Memcached, etc.)
theme_cache = {}

def fetch_trending_themes():
    """
    Retorna uma lista de temas de trends para processar, usando cache se disponível e válido.
    Se não houver cache ou estiver expirado, usa a lista fixa TRENDING_TOPICS da Config.
    """
    logger.info(f"Buscando temas de trends. Cache duration: {Config.CACHE_DURATION_HOURS} horas.")
    try:
        cached_data = theme_cache.get("themes")
        cache_timestamp = theme_cache.get("timestamp")

        # Verifica se o cache existe e ainda é válido
        if cached_data and cache_timestamp and \
           (datetime.now() - cache_timestamp).total_seconds() < Config.CACHE_DURATION_HOURS * 3600:
            logger.info(f"Cache de temas encontrado e válido (menos de {Config.CACHE_DURATION_HOURS} horas).")
            return cached_data
        else:
            logger.info("Cache de temas expirado ou não encontrado. Usando lista fixa TRENDING_TOPICS.")
            themes = Config.TRENDING_TOPICS # Usar a lista de TRENDING_TOPICS da Config
            # Salvar a lista fixa no cache para a próxima execução
            theme_cache["themes"] = themes
            theme_cache["timestamp"] = datetime.now()
            logger.info(f"Gerados {len(themes)} novos temas (da lista fixa).")
            return themes
    except Exception as e:
        logger.error(f"Erro ao buscar ou verificar cache de temas: {str(e)}. Usando lista fixa.", exc_info=True)
        return Config.TRENDING_TOPICS # Retorna lista fixa em caso de erro no cache


async def process_theme(theme_config, headers, existing_titles, output_format_override=None):
    """
    Processa um único tema: coleta material, gera conteúdo e prepara posts para envio.
    Retorna uma lista de posts preparados para envio para este tema.
    """
    tema = theme_config.get("tema")
    if not tema:
        logger.error("Configuração de tema inválida: 'tema' não encontrado.")
        return []

    logger.info(f"Iniciando processamento para o tema: '{tema}'")
    posts_for_theme = []
    post_start_time = time.time()

    try:
        # 1. Coleta de Material
        # CORREÇÃO: scrape_sources agora é uma função async e deve ser await-ada
        compiled_raw_material, source_urls = await scrape_sources(tema)
        logger.info(f"Material bruto coletado para '{tema}'. Tamanho: {len(compiled_raw_material)} chars. URLs: {len(source_urls)}")

        # Verifica se o material coletado é substancial
        if not compiled_raw_material or len(compiled_raw_material.strip()) < 50:
            logger.warning(f"Não foi possível coletar material bruto suficiente para o tema '{tema}'. Pulando geração de conteúdo.")
            return posts_for_theme # Retorna lista vazia se não houver material

        # 2. Determinar Tipo de Conteúdo
        content_type = output_format_override if output_format_override else determine_content_type(theme_config)
        if content_type not in ["summary", "article", "social", "informative"]:
            logger.warning(f"Tipo de conteúdo inválido '{content_type}' para o tema '{tema}'. Usando 'summary'.")
            content_type = "summary"

        logger.info(f"Gerando conteúdo com Gemini para o tema '{tema}' com content_type '{content_type}'...")

        # 3. Gerar Conteúdo com Gemini
        generated_content_data = generate_content(tema, compiled_raw_material, content_type)

        # Verifica se a geração foi bem-sucedida e os campos principais estão presentes
        if not generated_content_data or not all(generated_content_data.get(field) for field in ["title", "excerpt", "content", "metaDescription"]):
            logger.error(f"Falha na geração ou parsing do conteúdo do Gemini para '{tema}' ({content_type}). Conteúdo gerado: {generated_content_data}")
            return posts_for_theme # Retorna lista vazia em caso de falha na geração

        # 4. Verificar Duplicidade (Apenas para o tipo principal, não social, e antes de preparar o post)
        title_pt = generated_content_data.get("title", {}).get("PT", "")
        if content_type != "social" and title_pt and title_pt.strip() in existing_titles:
            logger.warning(f"Título duplicado encontrado para o tema '{tema}', tipo '{content_type}': '{title_pt}'. Pulando preparação do post.")
            return posts_for_theme # Retorna lista vazia se for duplicado

        # 5. Preparar Dados do Post Principal
        post_data = {
            "title": generated_content_data.get("title", {}),
            "content": generated_content_data.get("content", {}),
            "excerpt": generated_content_data.get("excerpt", {}),
            "metaDescription": generated_content_data.get("metaDescription", {}),
            "image": theme_config.get("image", "https://placehold.co/1200x630/000000/FFFFFF?text=DailyBrief"), # Placeholder padrão
            "author": theme_config.get("author", Config.DEFAULT_AUTHOR),
            "tags": theme_config.get("tags", [tema, "DailyBrief", "Automação"]),
            "category": theme_config.get("category", "Geral"),
            "affiliateLinks": theme_config.get("affiliateLinks", {}),
            "status": theme_config.get("status", Config.DEFAULT_STATUS),
            # publishedAt pode ser definido como None e preenchido no backend, ou com um timestamp aqui
            "publishedAt": datetime.now(timezone.utc).isoformat(timespec='microseconds') + 'Z', # Formato ISO 8601 com 'Z' para UTC
            "readTime": theme_config.get("readTime", "5 min"),
            "sources": source_urls # Adiciona as URLs fonte
        }

        # Adiciona o post principal à lista de posts para enviar
        posts_for_theme.append({"tema": tema, "post_data": post_data, "content_type": content_type, "source_urls": source_urls})

        # 6. Gerar e Preparar Post Social (se configurado e não for o tipo principal)
        if theme_config.get("generateSocial", False) and content_type != "social":
            logger.info(f"Gerando post social para o tema '{tema}'...")
            social_generated_data = generate_content(tema, compiled_raw_material, "social")
            if social_generated_data and all(social_generated_data.get(field) for field in ["title", "excerpt", "content", "metaDescription"]):
                social_post_data = {
                    "title": social_generated_data.get("title", {}),
                    "content": social_generated_data.get("content", {}),
                    "excerpt": social_generated_data.get("excerpt", {}),
                    "metaDescription": social_generated_data.get("metaDescription", {}),
                    "image": theme_config.get("image", "https://placehold.co/1200x630/000000/FFFFFF?text=DailyBrief"),
                    "author": theme_config.get("author", Config.DEFAULT_AUTHOR),
                    "tags": theme_config.get("tags", [tema, "DailyBrief", "Automação"]) + ["social"], # Adiciona tag 'social'
                    "category": theme_config.get("category", "Geral"),
                    "affiliateLinks": theme_config.get("affiliateLinks", {}),
                    "status": theme_config.get("status", Config.DEFAULT_STATUS),
                    "publishedAt": datetime.now(timezone.utc).isoformat(timespec='microseconds') + 'Z', # Também com Z
                    "readTime": "1 min", # Posts sociais geralmente são curtos
                    "sources": source_urls,
                    "link": "" # Link será preenchido após o envio do post principal
                }
                posts_for_theme.append({"tema": tema, "post_data": social_post_data, "content_type": "social", "source_urls": source_urls})
            else:
                logger.warning(f"Falha na geração ou parsing do conteúdo social do Gemini para '{tema}'.")

        elapsed_time = time.time() - post_start_time
        logger.info(f"Processamento para '{tema}' concluído. Tempo: {elapsed_time:.2f}s. {len(posts_for_theme)} posts preparados.")
        return posts_for_theme

    except Exception as e:
        logger.error(f"Erro inesperado durante o processamento do tema '{tema}': {str(e)}", exc_info=True)
        # Re-levanta a exceção para ser tratada na função main
        raise


async def main(output_format=None, theme=None):
    """
    Função principal da automação: orquestra a busca, geração e envio de posts.
    Pode ser acionada com um formato de saída e/ou tema específicos.
    """
    report_lines = [f"Relatório de Envio - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ({datetime.now().astimezone().tzinfo})\n"]
    metrics = {"created": 0, "failed": 0, "categories": {}, "retries": 0}
    start_time = time.time()

    try:
        # 1. Autenticar no Backend
        logger.info("Autenticando no backend...")
        headers = Auth.authenticate()
        logger.info("Autenticação no backend bem-sucedida.")

        # 2. Buscar Temas para Processar
        themes_to_process = fetch_trending_themes()

        # Filtrar ou adicionar tema específico se fornecido
        if theme:
            # Tenta encontrar o tema específico na lista de trends
            found_theme_config = next((t for t in themes_to_process if t["tema"].lower() == theme.lower()), None)
            if found_theme_config:
                themes_to_process = [found_theme_config]
                logger.info(f"Processando tema específico encontrado na lista de trends: '{theme}'")
            else:
                # Se o tema específico não está na lista de trends, cria uma nova configuração para ele
                report_lines.append(f"[AVISO] Tema '{theme}' não encontrado na lista de trends. Tentando processar como novo tema.")
                logger.warning(f"Tema '{theme}' não encontrado na lista de trends. Tentando processar como novo tema.")
                themes_to_process = [{"tema": theme, "categoria": "Específico", "tipo": output_format if output_format else "informative", "generateSocial": True}]
        
        if not themes_to_process:
            report_lines.append("[INFO] Nenhum tema para processar após filtragem.")
            logger.info("Nenhum tema para processar. Processo finalizado.")
            # Salvar relatório mesmo que vazio
            save_report(report_lines)
            return "\n".join(report_lines)

        # Limitar o número de temas processados por execução (opcional, para controle)
        max_themes_per_run = int(os.getenv("MAX_THEMES_PER_RUN", 5)) # Leia de variável de ambiente
        if len(themes_to_process) > max_themes_per_run:
            logger.warning(f"Limitando o processamento a {max_themes_per_run} temas dos {len(themes_to_process)} encontrados.")
            themes_to_process = themes_to_process[:max_themes_per_run]

        # 3. Buscar Posts Existentes (para verificar duplicados)
        logger.info("Buscando posts existentes no backend para verificar duplicados...")
        existing_titles = get_existing_posts(headers)
        logger.info(f"Encontrados {len(existing_titles)} títulos de posts existentes em PT.")

        all_posts_to_send = [] # Lista para armazenar todos os posts prontos para envio

        # 4. Processar Cada Tema
        for theme_config in themes_to_process:
            try:
                # Processa o tema e obtém a lista de posts preparados para este tema
                posts_for_theme = await process_theme(theme_config, headers, existing_titles, output_format)
                all_posts_to_send.extend(posts_for_theme) # Adiciona os posts à lista geral

            except Exception as e:
                logger.error(f"Erro ao processar tema '{theme_config.get('tema', 'Tema Desconhecido')}': {str(e)}", exc_info=True)
                report_lines.append(f"[ERRO] Falha no processamento do tema '{theme_config.get('tema', 'Tema Desconhecido')}': {str(e)}")
                metrics["failed"] += 1 # Conta a falha no processamento do tema

        # 5. Enviar Posts para o Backend
        logger.info(f"Iniciando fase de envio para o backend. {len(all_posts_to_send)} posts para enviar.")
        if not all_posts_to_send:
            report_lines.append("[INFO] Nenhum post preparado para envio.")
            logger.info("Nenhum post preparado para envio. Pulando fase de envio.")
        else:
            for post_item in all_posts_to_send:
                tema = post_item["tema"]
                post_data = post_item["post_data"]
                content_type = post_item["content_type"]
                source_urls = post_item["source_urls"]

                metrics["categories"][tema] = metrics["categories"].get(tema, 0) + 1 # Conta por tema/categoria

                # Salvar payload JSON antes de enviar (útil para depuração)
                post_uuid = str(uuid.uuid4())
                safe_tema_name = tema.replace(' ', '_').replace('/', '_').replace('\\', '_')
                filename = f"output/payloads/post_{safe_tema_name}_{content_type}_{post_uuid}.json" # Salva em subpasta 'payloads'
                os.makedirs("output/payloads", exist_ok=True)
                try:
                    with open(filename, "w", encoding="utf-8") as file:
                        json.dump(post_data, file, ensure_ascii=False, indent=4)
                    logger.info(f"Payload salvo em: {filename}")
                except Exception as e:
                    logger.error(f"Erro ao salvar payload em {filename}: {str(e)}", exc_info=True)
                    report_lines.append(f"[ERRO] Falha ao salvar payload para '{tema}' ({content_type}): {str(e)}")

                try:
                    # Envia o post para o backend (com retry implementado em api.send_post)
                    # A função send_post_to_backend é mais robusta e lida com o ID de retorno
                    response = send_post(post_data, headers) # send_post já está em api.py
                    response_json = response.json()
                    post_id = response_json.get("id")

                    if post_id:
                        report_lines.append(f"[SUCESSO] Post '{post_data.get('title', {}).get('PT', 'Sem Título')}' ({content_type}) criado! ID: {post_id}")
                        report_lines.append(f"Fontes usadas: {', '.join(source_urls)}")
                        metrics["created"] += 1

                        # Atualizar link do post social APÓS o envio do post principal
                        if content_type != "social": # Se este é o post principal
                            # Encontra o post social correspondente na lista all_posts_to_send
                            social_post_item = next((item for item in all_posts_to_send if item["tema"] == tema and item["content_type"] == "social"), None)
                            if social_post_item:
                                # Constrói o link (adapte a URL base conforme seu frontend)
                                generated_link = f"https://dailybrief.com/post/{post_id}" # Exemplo de URL base
                                social_post_item["post_data"]["link"] = generated_link
                                logger.info(f"Link do post social para '{tema}' atualizado para: {generated_link}")
                                # Nota: Esta atualização acontece na lista all_posts_to_send.
                                # Se o post social já foi enviado, o link não será atualizado no backend.
                                # Uma abordagem mais robusta seria enviar o post social DEPOIS do principal
                                # ou ter um endpoint para atualizar posts existentes.
                    else:
                        raise ValueError(f"ID do post não retornado pelo backend na resposta de sucesso (status {response.status_code}). Resposta completa: {response_json}")

                except Exception as e:
                    report_lines.append(f"[ERRO] Falha ao enviar Post '{post_data.get('title', {}).get('PT', 'Sem Título')}' ({content_type}): {str(e)}")
                    logger.error(f"Detalhes da falha no envio para '{tema}' ({content_type}): {str(e)}", exc_info=True)
                    metrics["failed"] += 1
                    # A contagem de retries é feita dentro de api.send_post pela tenacity


        # 6. Enviar Relatório/Logs para o Backend (Opcional)
        if Config.LOGS_API_URL:
            try:
                log_report_data = {
                    "action": "Relatório de Execução da Automação",
                    "timestamp": datetime.now(timezone.utc), # Passa objeto datetime para ser formatado em api.py
                    "level": "INFO",
                    "report_summary": "\n".join(report_lines),
                    "metrics": metrics,
                    "duration_seconds": (time.time() - start_time)
                }
                send_logs_to_backend(log_report_data, headers) # Passa headers para autenticação se necessário
                logger.info("Relatório de execução enviado para o backend de logs.")
            except Exception as e:
                logger.error(f"Falha ao enviar relatório de execução para o backend de logs: {str(e)}", exc_info=True)
        else:
            logger.warning("LOGS_API_URL não configurada. Pulando envio do relatório para o backend de logs.")


        # 7. Finalizar e Salvar Relatório Local
        total_time = time.time() - start_time
        report_lines.append(f"\n--- Resumo da Execução ---\nMétricas:\n- Posts criados: {metrics['created']}\n- Falhas (processamento/envio): {metrics['failed']}\n- Tentativas extras (envio - retries): (Contado internamente na função send_post)\n- Categorias Processadas: {', '.join(f'{k}: {v}' for k, v in metrics['categories'].items()) if metrics['categories'] else 'Nenhuma'}\n- Tempo total: {total_time:.2f}s")
        logger.info("Processo de automação finalizado.")
        logger.info(f"Métricas finais: Criados={metrics['created']}, Falhas={metrics['failed']}")

        # Salvar relatório final localmente
        save_report(report_lines)

        # Retorna o relatório final como string (útil para o servidor FastAPI)
        return "\n".join(report_lines)

    except Exception as e:
        # Captura erros críticos que interrompem o fluxo principal
        logger.critical(f"Erro CRÍTICO na automação: {str(e)}", exc_info=True)
        report_lines.append(f"\n[ERRO CRÍTICO] Automação interrompida: {str(e)}")
        # Salvar relatório de erro crítico localmente
        save_report(report_lines, is_error=True)
        # Re-levanta a exceção para ser tratada pelo chamador (ex: servidor FastAPI)
        raise


# Ponto de entrada principal se rodar o script diretamente
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DailyBrief Automation Script")
    parser.add_argument("--format", type=str, help="Output format: 'summary' or 'article'", default=Config.OUTPUT_FORMAT)
    parser.add_argument("--theme", type=str, help="Specific theme to process", default=None)
    args = parser.parse_args()

    logger.info(f"Rodando script main.py diretamente com output_format='{args.format}', theme='{args.theme}'.")
    # Executa a função main de forma assíncrona
    try:
        asyncio.run(main(output_format=args.format, theme=args.theme))
        logger.info("Execução direta de main.py concluída.")
    except Exception as e:
        logger.error(f"Execução direta de main.py falhou: {str(e)}", exc_info=True)
        # O erro já foi logado e o relatório de erro salvo dentro de main()
        # exit(1) # Opcional: Sair com código de erro se a execução falhar