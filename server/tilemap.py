"""Mapa de tiles carregado de um arquivo de texto (ASCII), zonas e colisão com caixas.

Cada mapa é um par de arquivos:
  data/maps/<nome>.txt   o terreno (legenda no topo do arquivo)
  data/maps/<nome>.yaml  (opcional) nome, zonas, portais e onde ficam os NPCs
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

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

# Tipos de zona. "safe" vem dos tiles ':' do mapa (é o que impede inimigos de entrar);
# as áreas do arquivo .yaml só podem ser pve ou pvp.
ZONE_TYPES = ("safe", "pve", "pvp")
AREA_ZONE_TYPES = ("pve", "pvp")

Blocker = Callable[[str], bool]  # recebe o caractere do tile e diz se bloqueia
Solid = tuple[float, float, float]  # corpo que bloqueia: (x, y, metade do lado)


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    type: str  # safe | pve | pvp
    area: tuple[int, int, int, int] = (0, 0, -1, -1)  # x0, y0, x1, y1 em tiles (inclusive)

    def contains_tile(self, tx: int, ty: int) -> bool:
        x0, y0, x1, y1 = self.area
        return x0 <= tx <= x1 and y0 <= ty <= y1

    def client_info(self) -> dict:
        return {"id": self.id, "name": self.name, "type": self.type, "area": list(self.area)}


SAFE_TILE_ZONE = Zone(id="segura", name="", type="safe")


@dataclass(frozen=True)
class Portal:
    """Área do mapa que leva a outro mapa: quem pisa nela aparece em `at` do mapa `to`."""

    area: tuple[int, int, int, int]  # x0, y0, x1, y1 em tiles (inclusive)
    to: str  # id do mapa de destino (nome do arquivo sem extensão)
    at: tuple[int, int]  # tile de chegada no destino

    def contains_tile(self, tx: int, ty: int) -> bool:
        x0, y0, x1, y1 = self.area
        return x0 <= tx <= x1 and y0 <= ty <= y1


@dataclass
class TileMap:
    name: str
    rows: list[str]
    tile_size: int
    spawn: tuple[float, float]
    enemy_spawns: list[tuple[str, float, float]] = field(default_factory=list)  # (mark, x, y)
    zones: list[Zone] = field(default_factory=list)  # áreas do .yaml; a primeira que contém vence
    default_zone: Zone = Zone(id="", name="", type="pve")  # onde não há tile ':' nem área
    title: str = ""  # nome exibido do mapa
    portals: list[Portal] = field(default_factory=list)
    npc_spawns: list[tuple[str, float, float]] = field(default_factory=list)  # (id do NPC, x, y)

    def portal_at(self, x: float, y: float) -> Portal | None:
        ts = self.tile_size
        tx, ty = math.floor(x / ts), math.floor(y / ts)
        return next((p for p in self.portals if p.contains_tile(tx, ty)), None)

    def tile_center(self, tx: int, ty: int) -> tuple[float, float]:
        return (tx + 0.5) * self.tile_size, (ty + 0.5) * self.tile_size

    def client_info(self) -> dict:
        return {
            "id": self.name,
            "title": self.title or self.name,
            "tile_size": self.tile_size,
            "rows": self.rows,
            "zones": [z.client_info() for z in self.zones],
            "portals": [{"area": list(p.area), "to": p.to} for p in self.portals],
        }

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

    def zone_at(self, x: float, y: float) -> Zone:
        """Zona num ponto: tile ':' é sempre segura; senão a primeira área que contém; senão a padrão."""
        ts = self.tile_size
        tx, ty = math.floor(x / ts), math.floor(y / ts)
        if self.tile_at(tx, ty) == SAFE:
            return SAFE_TILE_ZONE
        for zone in self.zones:
            if zone.contains_tile(tx, ty):
                return zone
        return self.default_zone

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
        self, x: float, y: float, half: float, dx: float, dy: float, blocks: Blocker,
        solids: Iterable[Solid] = (),
    ) -> tuple[float, float]:
        """Move uma caixa, um eixo de cada vez.

        Se o passo bater num tile, a caixa encosta na borda dele e para naquele
        eixo; o outro eixo continua livre (efeito de "deslizar" na parede).
        Supõe passos menores que um tile, o que vale com 20 ticks/s.
        `solids` são outros corpos (jogadores, inimigos, NPCs): a caixa encosta
        neles do mesmo jeito (veja clamp_by_solids).

        ATENÇÃO: client/js/physics.js tem uma cópia desta lógica (previsão de
        movimento). Se mudar aqui, mude lá também.
        """
        solids = list(solids)
        new_x = self._move_axis(x, y, half, dx, blocks, horizontal=True)
        x = clamp_by_solids(x, y, half, new_x, solids, horizontal=True)
        new_y = self._move_axis(y, x, half, dy, blocks, horizontal=False)
        y = clamp_by_solids(y, x, half, new_y, solids, horizontal=False)
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


def clamp_by_solids(pos: float, other: float, half: float, new: float, solids: list[Solid],
                    horizontal: bool) -> float:
    """Encurta um passo num eixo (de pos para new) para a caixa só encostar nos corpos do caminho.

    Corpos já sobrepostos no começo do passo são ignorados (deixa se separarem). A ordem
    dos corpos não importa (só min/max). client/js/physics.js repete esta conta.
    """
    if new == pos:
        return new
    for sx, sy, sh in solids:
        s_pos, s_other = (sx, sy) if horizontal else (sy, sx)
        reach = half + sh
        if abs(other - s_other) >= reach or abs(pos - s_pos) < reach:
            continue  # não se cruzam no outro eixo, ou já estavam sobrepostos
        if new > pos and s_pos > pos:
            new = min(new, max(pos, s_pos - reach - EPS))
        elif new < pos and s_pos < pos:
            new = max(new, min(pos, s_pos + reach + EPS))
    return new


def parse_map(
    text: str,
    name: str,
    spawn_marks: Iterable[str] = (),
    tile_size: int = C.TILE_SIZE,
    zones: dict | None = None,
) -> TileMap:
    """Converte o texto do mapa. Linhas vazias e linhas começando com ';' são ignoradas.

    spawn_marks: caracteres que marcam nascimento de inimigos (vêm de data/enemies.yaml).
    zones: conteúdo do arquivo de zonas (.yaml), já lido; None = tudo PvE.
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
    tilemap = TileMap(name=name, rows=rows, tile_size=tile_size, spawn=spawn, enemy_spawns=enemy_spawns)
    if zones:
        tilemap.default_zone, tilemap.zones = _parse_zones(zones, f"{name}.yaml", tilemap.width, tilemap.height)
        tilemap.title = str(zones.get("name") or "")
        tilemap.portals = _parse_portals(zones.get("portals") or [], f"{name}.yaml", tilemap.width, tilemap.height)
        tilemap.npc_spawns = _parse_npcs(zones.get("npcs") or [], f"{name}.yaml", tilemap)
    return tilemap


def _parse_npcs(raw: Any, where: str, tilemap: TileMap) -> list[tuple[str, float, float]]:
    """npcs: - { id: mercadora, at: [x, y] }  (o id é conferido em load_maps, contra data/npcs.yaml)"""
    if not isinstance(raw, list):
        raise ValueError(f"{where} > npcs: deve ser uma lista")
    spawns = []
    for i, item in enumerate(raw):
        at = f"{where} > npcs[{i}]"
        if not isinstance(item, dict) or set(item) != {"id", "at"} or not isinstance(item["id"], str):
            raise ValueError(f"{at}: use {{ id: npc, at: [x, y] }}")
        pos = item["at"]
        if not (isinstance(pos, list) and len(pos) == 2 and all(isinstance(v, int) and not isinstance(v, bool)
                                                                 for v in pos)):
            raise ValueError(f"{at}: 'at' deve ser [x, y] (tile)")
        tx, ty = pos
        if not (0 <= tx < tilemap.width and 0 <= ty < tilemap.height):
            raise ValueError(f"{at}: tile {pos} fora do mapa ({tilemap.width}×{tilemap.height})")
        if tilemap.tile_at(tx, ty) in WALK_BLOCKERS:
            raise ValueError(f"{at}: o tile {pos} é bloqueado (parede, água ou árvore)")
        spawns.append((item["id"], *tilemap.tile_center(tx, ty)))
    return spawns


def _check_area(area: Any, at: str, width: int, height: int) -> tuple[int, int, int, int]:
    if (
        not isinstance(area, list)
        or len(area) != 4
        or not all(isinstance(v, int) and not isinstance(v, bool) for v in area)
    ):
        raise ValueError(f"{at}: area deve ser [x0, y0, x1, y1] em tiles (números inteiros)")
    x0, y0, x1, y1 = area
    if not (0 <= x0 <= x1 < width and 0 <= y0 <= y1 < height):
        raise ValueError(f"{at}: area {area} fora do mapa ({width}×{height} tiles) ou invertida")
    return x0, y0, x1, y1


def _parse_portals(raw: Any, where: str, width: int, height: int) -> list[Portal]:
    if not isinstance(raw, list):
        raise ValueError(f"{where} > portals: deve ser uma lista")
    portals = []
    for i, item in enumerate(raw):
        at = f"{where} > portals[{i}]"
        if not isinstance(item, dict) or set(item) != {"area", "to", "at"}:
            raise ValueError(f"{at}: use {{ area: [x0, y0, x1, y1], to: mapa, at: [x, y] }}")
        dest = item["at"]
        if not (isinstance(dest, list) and len(dest) == 2 and all(isinstance(v, int) for v in dest)):
            raise ValueError(f"{at}: 'at' deve ser [x, y] (tile de chegada no outro mapa)")
        portals.append(Portal(area=_check_area(item["area"], at, width, height), to=str(item["to"]),
                              at=(dest[0], dest[1])))
    return portals


def _parse_zones(raw: Any, where: str, width: int, height: int) -> tuple[Zone, list[Zone]]:
    """Valida o arquivo de zonas. Formato no topo de data/maps/inicio.yaml."""

    def zone_type(value: Any, allowed: tuple[str, ...], at: str) -> str:
        if value not in allowed:
            raise ValueError(f"{at}: type {value!r} inválido (válidos: {', '.join(allowed)})")
        return value

    if not isinstance(raw, dict):
        raise ValueError(f"{where}: o arquivo deve ser um mapa com default_zone e zones")
    unknown = set(raw) - {"name", "default_zone", "zones", "portals", "npcs"}
    if unknown:
        raise ValueError(f"{where}: campo(s) desconhecido(s): {', '.join(sorted(map(str, unknown)))}")

    default_raw = raw.get("default_zone") or {}
    if not isinstance(default_raw, dict) or set(default_raw) - {"type", "name"}:
        raise ValueError(f"{where} > default_zone: use {{ type: pve|pvp, name: texto }}")
    default = Zone(
        id="",
        name=str(default_raw.get("name", "")),
        type=zone_type(default_raw.get("type", "pve"), AREA_ZONE_TYPES, f"{where} > default_zone"),
    )

    zones = []
    for zid, zraw in (raw.get("zones") or {}).items():
        at = f"{where} > zones > {zid}"
        if not isinstance(zraw, dict) or set(zraw) - {"name", "type", "area"} or "area" not in zraw:
            raise ValueError(f"{at}: use {{ name: texto, type: pve|pvp, area: [x0, y0, x1, y1] }}")
        x0, y0, x1, y1 = _check_area(zraw["area"], at, width, height)
        zones.append(Zone(
            id=str(zid),
            name=str(zraw.get("name", zid)),
            type=zone_type(zraw.get("type"), AREA_ZONE_TYPES, at),
            area=(x0, y0, x1, y1),
        ))
    return default, zones


def load_maps(
    maps_dir: str | Path,
    spawn_marks: Iterable[str] = (),
    tile_size: int = C.TILE_SIZE,
    enemy_ids: Iterable[str] = (),
    npc_ids: Iterable[str] | None = None,
) -> dict[str, TileMap]:
    """Carrega todos os mapas da pasta e confere se os portais levam a lugares válidos.

    Cada mapa é um .txt (+ .yaml) ou um .tmj do Tiled (server/tiled.py) — nunca os dois.
    npc_ids: NPCs que existem (data/npcs.yaml); None = não confere.
    """
    from .tiled import parse_tmj  # import aqui: tiled.py importa este módulo

    maps_dir = Path(maps_dir)
    marks, ids = tuple(spawn_marks), tuple(enemy_ids)
    texts = {path.stem: path for path in maps_dir.glob("*.txt")}
    tiled = {path.stem: path for path in maps_dir.glob("*.tmj")}
    both = sorted(set(texts) & set(tiled))
    if both:
        raise ValueError(f"Mapa(s) {', '.join(both)} existem como .txt e como .tmj; deixe só um formato "
                         "(./mapas.sh para-tiled move o .txt para data/maps/antigos/)")
    maps = {name: load_map(path, marks, tile_size) for name, path in texts.items()}
    maps.update({name: parse_tmj(path, ids, tile_size) for name, path in tiled.items()})
    maps = dict(sorted(maps.items()))
    check_portals(maps)
    if npc_ids is not None:
        check_npcs(maps, npc_ids)
    return maps


def check_npcs(maps: dict[str, TileMap], npc_ids: Iterable[str]) -> None:
    known = set(npc_ids)
    for tilemap in maps.values():
        for npc_id, x, y in tilemap.npc_spawns:
            if npc_id not in known:
                tx, ty = int(x // tilemap.tile_size), int(y // tilemap.tile_size)
                raise ValueError(f"Mapa {tilemap.name!r}: NPC {npc_id!r} em ({tx}, {ty}) não existe em npcs.yaml")


def check_portals(maps: dict[str, TileMap]) -> None:
    for tilemap in maps.values():
        for portal in tilemap.portals:
            dest = maps.get(portal.to)
            where = f"{tilemap.name}.yaml > portal para {portal.to!r}"
            if dest is None:
                raise ValueError(f"{where}: mapa de destino não existe (mapas: {', '.join(maps)})")
            tx, ty = portal.at
            if dest.tile_at(tx, ty) in WALK_BLOCKERS:
                raise ValueError(f"{where}: o tile de chegada {list(portal.at)} é bloqueado em {portal.to}")
            if dest.portal_at(*dest.tile_center(tx, ty)):
                raise ValueError(f"{where}: o tile de chegada {list(portal.at)} cai dentro de outro portal")


def load_map(path: str | Path, spawn_marks: Iterable[str] = (), tile_size: int = C.TILE_SIZE) -> TileMap:
    """Lê <nome>.txt e, se existir, <nome>.yaml com as zonas."""
    path = Path(path)
    zones_path = path.with_suffix(".yaml")
    zones = yaml.safe_load(zones_path.read_text(encoding="utf-8")) if zones_path.exists() else None
    return parse_map(path.read_text(encoding="utf-8"), path.stem, spawn_marks, tile_size, zones)
