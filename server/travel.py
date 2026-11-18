"""Viagem rápida: NPCs com uma opção "travel: true" (data/npcs.yaml) levam o jogador até
outro NPC de viagem, pagando.

- Destinos: os outros NPCs de viagem, nos mapas que o jogador já visitou (Player.maps_seen,
  marcado ao entrar no jogo e a cada mudança de mapa). Os de mapas ainda não visitados
  aparecem bloqueados.
- Preço: C.TRAVEL_BASE_PRICE + C.TRAVEL_PRICE_PER_MAP por mapa de distância, contando os
  portais pelo caminho mais curto (mesmo mapa = só o preço base).
- Não dá para viajar em combate nem em duelo. Chega ao lado do NPC de destino.
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from . import config as C

if TYPE_CHECKING:
    from .entities import Npc, Player
    from .world import World

# arredores do NPC onde o jogador pode chegar, do mais perto para o mais longe (em tiles)
_AROUND = [(0, 1), (1, 0), (-1, 0), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1),
           (0, 2), (2, 0), (-2, 0), (0, -2)]


class TravelSystem:
    def __init__(self, world: World):
        self.world = world

    def stops(self) -> list[Npc]:
        """Os NPCs que levam e recebem viajantes."""
        return [npc for npc in self.world.npcs.entities.values()
                if any(o.travel for node in npc.defn.nodes.values() for o in node.options)]

    def hops(self, start: str, goal: str) -> int | None:
        """Mapas de distância pelos portais (None = não há caminho)."""
        maps = self.world.maps
        seen = {start: 0}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            if current == goal:
                return seen[current]
            for portal in maps[current].portals if current in maps else ():
                if portal.to not in seen:
                    seen[portal.to] = seen[current] + 1
                    queue.append(portal.to)
        return None

    def price(self, from_map: str, to_map: str) -> int | None:
        hops = self.hops(from_map, to_map)
        return None if hops is None else C.TRAVEL_BASE_PRICE + C.TRAVEL_PRICE_PER_MAP * hops

    def payload(self, p: Player, here: Npc) -> list[dict]:
        """Destinos a partir do NPC `here`, do mais barato para o mais caro."""
        maps = self.world.maps
        out = []
        for npc in self.stops():
            price = self.price(here.map_id, npc.map_id)
            if npc.id == here.id or price is None:
                continue
            tilemap = maps[npc.map_id]
            out.append({"id": npc.id, "npc": npc.name, "title": npc.defn.title,
                        "map": tilemap.title or tilemap.name, "price": price,
                        "known": npc.map_id in p.maps_seen})
        out.sort(key=lambda d: (d["price"], d["map"], d["npc"]))
        return out

    def go(self, p: Player, here: Npc, dest_id: object) -> bool:
        w = self.world
        dest = next((n for n in self.stops() if n.id == dest_id and n.id != here.id), None)
        price = self.price(here.map_id, dest.map_id) if dest else None
        if dest is None or price is None:
            return False
        if dest.map_id not in p.maps_seen:
            w._private(p, "Você ainda não conhece esse lugar: vá até lá a pé uma vez.")
            return False
        if p.combat_until > w.time:
            w._private(p, "Não dá para viajar no meio de um combate.")
            return False
        if w.duels.opponent_id(p) is not None:
            w._private(p, "Não dá para viajar durante um duelo.")
            return False
        if p.inventory.gold < price:
            w._private(p, f"Faltam {price - p.inventory.gold} moedas para a viagem ({price}).")
            return False
        x, y = self._arrival(p, dest)
        p.inventory.add_gold(-price)
        tilemap = w.maps[dest.map_id]
        place = tilemap.title or tilemap.name
        w.economy.record("viagem", place, price)  # moedas saem do jogo
        w.teleport(p, dest.map_id, x, y)
        w._private(p, f"{here.name} levou você até {dest.name} ({place}) por {price} moedas.")
        return True

    def _arrival(self, p: Player, dest: Npc) -> tuple[float, float]:
        """Um tile livre ao lado do NPC de destino."""
        from .world import _player_blocks
        tilemap = self.world.maps[dest.map_id]
        tx, ty = int(dest.x // C.TILE_SIZE), int(dest.y // C.TILE_SIZE)
        for dx, dy in _AROUND:
            nx, ny = tx + dx, ty + dy
            if 0 <= nx < tilemap.width and 0 <= ny < tilemap.height:
                x, y = tilemap.tile_center(nx, ny)
                if not tilemap.box_blocked(x, y, p.half, _player_blocks):
                    return x, y
        return tilemap.spawn
