"""Caminho de verdade para os inimigos: A* na grade de tiles do mapa.

Um tile é "passável" para um corpo se a caixa dele, centrada no tile, não encosta em
nada que o bloqueia (assim um chefe maior que um tile não tenta passar por frestas).
Anda nas 8 direções, mas só corta na diagonal se os dois lados estiverem livres
(não atravessa quinas). A busca tem limite de nós: sem caminho dentro dele, desiste.

Também tem o teste de "linha livre" (dá para ir reto de um ponto a outro?), que os
inimigos usam para andar direto quando nada está no meio e para encurtar o caminho.
"""
from __future__ import annotations

import heapq
import math
from typing import Callable

from .tilemap import Blocker, TileMap

Tile = tuple[int, int]

STRAIGHT = 1.0
DIAGONAL = math.sqrt(2.0)
NEIGHBORS = [(1, 0, STRAIGHT), (-1, 0, STRAIGHT), (0, 1, STRAIGHT), (0, -1, STRAIGHT),
             (1, 1, DIAGONAL), (1, -1, DIAGONAL), (-1, 1, DIAGONAL), (-1, -1, DIAGONAL)]


def tile_of(tilemap: TileMap, x: float, y: float) -> Tile:
    ts = tilemap.tile_size
    return math.floor(x / ts), math.floor(y / ts)


def walkable_fn(tilemap: TileMap, half: float, blocks: Blocker) -> Callable[[int, int], bool]:
    """Diz se a caixa (de metade `half`) cabe centrada no tile; guarda as respostas."""
    cache: dict[Tile, bool] = {}

    def walkable(tx: int, ty: int) -> bool:
        key = (tx, ty)
        if key not in cache:
            cx, cy = tilemap.tile_center(tx, ty)
            cache[key] = (0 <= tx < tilemap.width and 0 <= ty < tilemap.height
                          and not tilemap.box_blocked(cx, cy, half, blocks))
        return cache[key]

    return walkable


def find_path(tilemap: TileMap, start: Tile, goal: Tile, half: float, blocks: Blocker,
              max_nodes: int) -> list[Tile] | None:
    """Tiles do caminho de start até goal (sem o start), ou None se não achar dentro do limite.

    O start e o goal contam como passáveis mesmo encostados em parede (o corpo já está
    lá, e o alvo pode estar colado num muro).
    """
    if start == goal:
        return []
    walkable = walkable_fn(tilemap, half, blocks)

    def ok(t: Tile) -> bool:
        return t == goal or t == start or walkable(*t)

    def h(t: Tile) -> float:  # distância "octil": diagonais custam √2
        dx, dy = abs(t[0] - goal[0]), abs(t[1] - goal[1])
        return STRAIGHT * (dx + dy) + (DIAGONAL - 2 * STRAIGHT) * min(dx, dy)

    open_heap: list[tuple[float, float, Tile]] = [(h(start), 0.0, start)]
    came_from: dict[Tile, Tile] = {}
    cost: dict[Tile, float] = {start: 0.0}
    expanded = 0
    while open_heap:
        _, g, current = heapq.heappop(open_heap)
        if current == goal:
            path = [current]
            while path[-1] in came_from and came_from[path[-1]] != start:
                path.append(came_from[path[-1]])
            return path[::-1]
        if g > cost.get(current, math.inf):
            continue  # entrada velha na fila
        expanded += 1
        if expanded > max_nodes:
            return None
        cx, cy = current
        for dx, dy, step in NEIGHBORS:
            nxt = (cx + dx, cy + dy)
            if not ok(nxt):
                continue
            if dx and dy and not (ok((cx + dx, cy)) and ok((cx, cy + dy))):
                continue  # não corta quina
            new_cost = g + step
            if new_cost < cost.get(nxt, math.inf):
                cost[nxt] = new_cost
                came_from[nxt] = current
                heapq.heappush(open_heap, (new_cost + h(nxt), new_cost, nxt))
    return None


def free_spot(tilemap: TileMap, x: float, y: float, half: float, blocks: Blocker) -> tuple[float, float]:
    """O ponto mais perto de (x, y) onde a caixa cabe (para corpos maiores que um tile, o
    centro do tile pode encostar num muro vizinho). Sem ponto livre por perto, devolve (x, y)."""
    if not tilemap.box_blocked(x, y, half, blocks):
        return x, y
    reach = max(2.0, half - tilemap.tile_size / 2 + 2.0)
    d = 2.0
    while d <= reach:
        for dx, dy in ((0, d), (0, -d), (d, 0), (-d, 0), (d, d), (d, -d), (-d, d), (-d, -d)):
            if not tilemap.box_blocked(x + dx, y + dy, half, blocks):
                return x + dx, y + dy
        d += 2.0
    return x, y


def clear_line(tilemap: TileMap, x0: float, y0: float, x1: float, y1: float, half: float,
               blocks: Blocker) -> bool:
    """A caixa consegue ir em linha reta de (x0, y0) até (x1, y1) sem encostar em nada?"""
    dist = math.hypot(x1 - x0, y1 - y0)
    steps = max(1, math.ceil(dist / max(4.0, min(half, 8.0))))
    for i in range(1, steps + 1):
        t = i / steps
        if tilemap.box_blocked(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, half, blocks):
            return False
    return True
