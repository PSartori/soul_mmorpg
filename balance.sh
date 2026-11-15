#!/bin/sh
# Relatório de balanceamento. Exemplos:
#   ./balance.sh
#   ./balance.sh --out docs/balanceamento.md
#   ./balance.sh --levels 1 20 50 --equip espada_curta,gibao_de_couro
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/rpg-online}"
cd "$(dirname "$0")" || exit 1
exec uv run python -m tools.balance "$@"
