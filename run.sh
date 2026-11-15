#!/bin/sh
# Sobe o servidor do jogo em http://127.0.0.1:8080
# O ambiente virtual fica fora do Google Drive para não sincronizar milhares de arquivos.
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/rpg-online}"
cd "$(dirname "$0")" || exit 1
exec uv run python -m server.main "$@"
