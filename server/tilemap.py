"""Mapa de tiles carregado de um arquivo de texto (ASCII) e colisão com caixas."""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import config as C

# Legenda dos caracteres do mapa
WALL = "#"
FLOOR = "."
GRASS = ","
WATER = "~"
SAFE = ":"  # zona segura: sem PvP, inimigos não entram
TREE = "T"
SPAWN_MARK = "S"  # nascimento dos jogadores; vira SAFE ao carregar
# Pontos de nascimento de inimigos usam o "mark" de cada tipo (data/enemies.yaml); viram FLOOR.

TILES = frozenset({WALL, FLOOR, GRASS, WATER, SAFE, TREE})
RESERVED_MARKS = TILES | {SPAWN_MARK, ";"}
WALK_BLOCKERS = frozenset({WALL, WATER, TREE})
SHOT_BLOCKERS = frozenset({WALL, TREE})  # projéteis passam por cima da água

EPS = 1e-3

Blocker = Callable[[str], bool]  # recebe o caractere do tile e diz se bloqueia


@dataclass
class TileMap:
    name: str
    rows: list[str]
    tile_size: int
    spawn: tuple[float, float]
    enemy_spawns: list[tuple[str, float, float]] = field(default_factory=list)  # (mark, x, y)

    @property
    def width(self) -> int:
        return len(self.rows[0])

    @property
    def height(self) -> int:
        return len(self.rows)

    def tile_at(self, tx: int, ty: int) -> str:
        """Fora do mapa conta como parede."""
        if 0 <= ty < self.height and 0 <= tx < self.width:
            return self.rows[ty][tx]
        return WALL

    def tile_at_px(self, x: float, y: float) -> str:
        ts = self.tile_size
        return self.tile_at(math.floor(x / ts), math.floor(y / ts))

    def is_safe(self, x: float, y: float) -> bool:
        return self.tile_at_px(x, y) == SAFE

    def box_blocked(self, x: float, y: float, half: float, blocks: Blocker) -> bool:
        """A caixa centrada em (x, y) toca algum tile bloqueante?"""
        ts = self.tile_size
        x0 = math.floor((x - half) / ts)
        x1 = math.floor((x + half - 1e-6) / ts)
        y0 = math.floor((y - half) / ts)
        y1 = math.floor((y + half - 1e-6) / ts)
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                if blocks(self.tile_at(tx, ty)):
                    return True
        return False

    def move_box(
        self, x: float, y: float, half: float, dx: float, dy: float, blocks: Blocker
    ) -> tuple[float, float]:
        """Move uma caixa, um eixo de cada vez.

        Se o passo bater num tile, a caixa encosta na borda dele e para naquele
        eixo; o outro eixo continua livre (efeito de "deslizar" na parede).
        Supõe passos menores que um tile, o que vale com 20 ticks/s.

        ATENÇÃO: client/js/physics.js tem uma cópia desta lógica (previsão de
        movimento). Se mudar aqui, mude lá também.
        """
        x = self._move_axis(x, y, half, dx, blocks, horizontal=True)
        y = self._move_axis(y, x, half, dy, blocks, horizontal=False)
        return x, y

    def _move_axis(
        self, pos: float, other: float, half: float, d: float, blocks: Blocker, horizontal: bool
    ) -> float:
        if not d:
            return pos

        def blocked(p: float) -> bool:
            return self.box_blocked(p, other, half, blocks) if horizontal else self.box_blocked(other, p, half, blocks)

        new = pos + d
        if not blocked(new):
            return new
        ts = self.tile_size
        if d > 0:
            new = math.floor((new + half) / ts) * ts - half - EPS
        else:
            new = (math.floor((new - half) / ts) + 1) * ts + half + EPS
        # Não pode "andar para trás" nem acabar dentro de outro tile
        if (d > 0 and new < pos) or (d < 0 and new > pos) or blocked(new):
            return pos
        return new


def parse_map(text: str, name: str, spawn_marks: Iterable[str] = (), tile_size: int = C.TILE_SIZE) -> TileMap:
    """Converte o texto do mapa. Linhas vazias e linhas começando com ';' são ignoradas.

    spawn_marks: caracteres que marcam nascimento de inimigos (vêm de data/enemies.yaml).
    """
    marks = set(spawn_marks)
    lines = [line.rstrip("\r\n") for line in text.splitlines()]
    lines = [line for line in lines if line.strip() and not line.startswith(";")]
    if not lines:
        raise ValueError(f"Mapa {name!r} está vazio.")
    width = max(len(line) for line in lines)

    rows: list[str] = []
    spawn: tuple[float, float] | None = None
    enemy_spawns: list[tuple[str, float, float]] = []
    for ty, line in enumerate(lines):
        chars = []
        for tx, ch in enumerate(line.ljust(width, WALL)):
            cx, cy = (tx + 0.5) * tile_size, (ty + 0.5) * tile_size
            if ch == SPAWN_MARK:
                spawn = (cx, cy)
                ch = SAFE
            elif ch in marks:
                enemy_spawns.append((ch, cx, cy))
                ch = FLOOR
            elif ch not in TILES:
                raise ValueError(f"Mapa {name!r}: caractere desconhecido {ch!r} em ({tx}, {ty}).")
            chars.append(ch)
        rows.append("".join(chars))

    if spawn is None:
        raise ValueError(f"Mapa {name!r} não tem ponto de nascimento ({SPAWN_MARK}).")
    return TileMap(name=name, rows=rows, tile_size=tile_size, spawn=spawn, enemy_spawns=enemy_spawns)


def load_map(path: str | Path, spawn_marks: Iterable[str] = (), tile_size: int = C.TILE_SIZE) -> TileMap:
    path = Path(path)
    return parse_map(path.read_text(encoding="utf-8"), path.stem, spawn_marks, tile_size)
