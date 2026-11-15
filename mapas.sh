#!/bin/sh
# Ferramentas de mapa. Exemplos:
#   ./mapas.sh verificar
#   ./mapas.sh para-tiled bosque
#   ./mapas.sh tileset
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/rpg-online}"
cd "$(dirname "$0")" || exit 1
exec uv run python -m tools.maps "$@"
