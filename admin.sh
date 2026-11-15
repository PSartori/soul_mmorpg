#!/bin/sh
# Administração pelo terminal. Exemplos:
#   ./admin.sh role SeuPersonagem admin
#   ./admin.sh staff
#   ./admin.sh unban Fulano
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/rpg-online}"
cd "$(dirname "$0")" || exit 1
exec uv run python -m tools.admin "$@"
