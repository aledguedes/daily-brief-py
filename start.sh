#!/usr/bin/env bash
# start.sh - script de inicialização para Render
set -o errexit  # encerra se ocorrer erro
set -o pipefail
set -o nounset

echo "Iniciando aplicação FastAPI no Render..."
exec uvicorn main:app --host 0.0.0.0 --port 10000
