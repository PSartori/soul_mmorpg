"""Carrega tudo o que vem de arquivos: conteúdo (data/*.yaml) e mapas (data/maps/).

Usado ao ligar o servidor e para recarregar com o jogo rodando. Qualquer erro
(YAML inválido, referência quebrada, mapa ou portal errado) vira uma exceção com
a mensagem do validador — e, numa recarga, o jogo continua com o conteúdo antigo.
"""
from __future__ import annotations

from pathlib import Path

from . import config as C
from .content import Content
from .tilemap import TileMap, load_maps

# Arquivos que, se mudarem, pedem recarga (modo --watch)
WATCHED_PATTERNS = ("*.yaml", "maps/*.txt", "maps/*.yaml", "maps/*.tmj", "maps/*.tsj")


def load_game_data(data_dir: str | Path = C.DATA_DIR, maps_dir: str | Path = C.MAPS_DIR) -> tuple[Content, dict[str, TileMap]]:
    content = Content.load(data_dir)
    maps = load_maps(maps_dir, content.enemy_by_mark, enemy_ids=content.enemies)
    if not maps:
        raise ValueError(f"Nenhum mapa em {maps_dir}")
    return content, maps


def watched_files_state(data_dir: str | Path = C.DATA_DIR) -> dict[str, float]:
    """Data de modificação de cada arquivo de conteúdo (para perceber mudanças)."""
    data_dir = Path(data_dir)
    state = {}
    for pattern in WATCHED_PATTERNS:
        for path in data_dir.glob(pattern):
            try:
                state[str(path)] = path.stat().st_mtime
            except OSError:
                pass
    return state
