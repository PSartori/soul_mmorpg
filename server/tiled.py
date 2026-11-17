"""Mapas feitos no Tiled (https://www.mapeditor.org), salvos como JSON (.tmj).

O mapa precisa ter:
  - o conjunto de tiles do jogo (data/maps/terreno.tsj, gerado por ./mapas.sh);
    cada tile tem a propriedade "caractere" (#, ., ,, ~, T ou :), igual aos mapas em texto;
  - uma camada de tiles chamada "terreno";
  - (opcional) camadas de objetos:
      "zonas"    retângulos; nome = nome da zona; propriedade "tipo" = pve | pvp
      "portais"  retângulos; propriedades "destino" (mapa), "chegada_x" e "chegada_y" (tile)
      "pontos"   pontos; propriedade "tipo" = nascimento | inimigo | npc; se inimigo,
                 propriedade "inimigo" = id do inimigo (data/enemies.yaml); se npc,
                 propriedade "npc" = id do NPC (data/npcs.yaml)
  - propriedades do mapa (opcionais): "nome" (nome exibido), "zona_padrao" (pve | pvp)
    e "nome_zona_padrao".

O resultado é o mesmo TileMap dos mapas em texto, então o resto do jogo não muda.
"""
from __future__ import annotations

import base64
import gzip
import json
import math
import struct
import zlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .tilemap import AREA_ZONE_TYPES, SAFE, TILES, WALK_BLOCKERS, Portal, TileMap, Zone

TILE_PROPERTY = "caractere"
FLIP_FLAGS = 0xE0000000  # bits de espelhamento/rotação que o Tiled guarda no id do tile


class TiledError(ValueError):
    pass


def _props(obj: dict) -> dict[str, Any]:
    return {p["name"]: p.get("value") for p in obj.get("properties", []) if isinstance(p, dict) and "name" in p}


def _load_tileset(entry: dict, map_dir: Path, where: str) -> tuple[int, dict[int, str]]:
    """Devolve (firstgid, {id local: caractere})."""
    firstgid = entry.get("firstgid")
    if not isinstance(firstgid, int):
        raise TiledError(f"{where}: conjunto de tiles sem firstgid")
    data = entry
    if "source" in entry:
        source = map_dir / entry["source"]
        if source.suffix != ".tsj":
            raise TiledError(f"{where}: o conjunto de tiles {source.name} precisa ser JSON (.tsj); "
                             "no Tiled, exporte o tileset como JSON")
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TiledError(f"{where}: não consegui ler {source.name}: {exc}") from exc
    chars = {}
    for tile in data.get("tiles", []):
        char = _props(tile).get(TILE_PROPERTY)
        if char is not None:
            if char not in TILES:
                raise TiledError(f"{where}: tile {tile.get('id')} tem {TILE_PROPERTY}={char!r}, que não existe "
                                 f"(válidos: {' '.join(sorted(TILES))})")
            chars[tile["id"]] = char
    return firstgid, chars


def _layer_data(layer: dict, size: int, where: str) -> list[int]:
    data = layer.get("data")
    if isinstance(data, list):
        values = data
    elif isinstance(data, str):  # base64, com ou sem compressão
        raw = base64.b64decode(data)
        compression = layer.get("compression", "")
        if compression == "zlib":
            raw = zlib.decompress(raw)
        elif compression == "gzip":
            raw = gzip.decompress(raw)
        elif compression:
            raise TiledError(f"{where}: compressão {compression!r} não suportada (use CSV, base64, zlib ou gzip)")
        values = list(struct.unpack(f"<{len(raw) // 4}I", raw))
    else:
        raise TiledError(f"{where}: camada 'terreno' sem dados")
    if len(values) != size:
        raise TiledError(f"{where}: camada 'terreno' tem {len(values)} tiles, esperado {size}")
    return values


def parse_tmj(path: str | Path, enemy_ids: Iterable[str] = (), tile_size: int | None = None) -> TileMap:
    path = Path(path)
    name = path.stem
    where = path.name
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TiledError(f"{where}: não é um JSON válido do Tiled: {exc}") from exc
    if doc.get("orientation") != "orthogonal" or doc.get("infinite"):
        raise TiledError(f"{where}: o mapa precisa ser ortogonal e de tamanho fixo (não infinito)")
    width, height = doc.get("width"), doc.get("height")
    ts = doc.get("tilewidth")
    if not all(isinstance(v, int) and v > 0 for v in (width, height, ts)) or doc.get("tileheight") != ts:
        raise TiledError(f"{where}: largura/altura do mapa ou dos tiles inválida (tiles precisam ser quadrados)")
    if tile_size is not None and ts != tile_size:
        raise TiledError(f"{where}: tiles de {ts} px; o jogo usa {tile_size} px")

    gid_chars: dict[int, str] = {}
    for entry in doc.get("tilesets", []):
        firstgid, chars = _load_tileset(entry, path.parent, where)
        for local_id, char in chars.items():
            gid_chars[firstgid + local_id] = char

    layers = {layer.get("name"): layer for layer in doc.get("layers", [])}
    terrain = layers.get("terreno")
    if terrain is None or terrain.get("type") != "tilelayer":
        raise TiledError(f"{where}: falta a camada de tiles 'terreno'")
    values = _layer_data(terrain, width * height, where)
    rows = []
    for ty in range(height):
        row = []
        for tx in range(width):
            gid = values[ty * width + tx] & ~FLIP_FLAGS
            if gid == 0:
                raise TiledError(f"{where}: tile vazio em ({tx}, {ty}) na camada 'terreno' (preencha o mapa todo)")
            char = gid_chars.get(gid)
            if char is None:
                raise TiledError(f"{where}: o tile em ({tx}, {ty}) não tem a propriedade '{TILE_PROPERTY}'")
            row.append(char)
        rows.append("".join(row))

    def area_of(obj: dict, label: str) -> tuple[int, int, int, int]:
        x, y, w, h = (obj.get(k, 0) for k in ("x", "y", "width", "height"))
        if w <= 0 or h <= 0:
            raise TiledError(f"{where} > {label} '{obj.get('name', '')}': use um retângulo")
        x0, y0 = math.floor(x / ts), math.floor(y / ts)
        x1, y1 = math.ceil((x + w) / ts) - 1, math.ceil((y + h) / ts) - 1
        if not (0 <= x0 <= x1 < width and 0 <= y0 <= y1 < height):
            raise TiledError(f"{where} > {label} '{obj.get('name', '')}': fora do mapa")
        return x0, y0, x1, y1

    def objects(layer_name: str) -> list[dict]:
        layer = layers.get(layer_name)
        if layer is None:
            return []
        if layer.get("type") != "objectgroup":
            raise TiledError(f"{where}: '{layer_name}' precisa ser uma camada de objetos")
        return layer.get("objects", [])

    zones = []
    for obj in objects("zonas"):
        ztype = _props(obj).get("tipo")
        if ztype not in AREA_ZONE_TYPES:
            raise TiledError(f"{where} > zona '{obj.get('name', '')}': propriedade 'tipo' deve ser pve ou pvp")
        zones.append(Zone(id=str(obj.get("id")), name=obj.get("name") or "", type=ztype, area=area_of(obj, "zona")))

    portals = []
    for obj in objects("portais"):
        props = _props(obj)
        dest, ax, ay = props.get("destino"), props.get("chegada_x"), props.get("chegada_y")
        if not isinstance(dest, str) or not isinstance(ax, int) or not isinstance(ay, int):
            raise TiledError(f"{where} > portal '{obj.get('name', '')}': precisa das propriedades "
                             "'destino' (texto), 'chegada_x' e 'chegada_y' (inteiros)")
        portals.append(Portal(area=area_of(obj, "portal"), to=dest, at=(ax, ay)))

    known = set(enemy_ids)
    spawn = None
    enemy_spawns = []
    npc_spawns = []
    for obj in objects("pontos"):
        props = _props(obj)
        px, py = obj.get("x", 0), obj.get("y", 0)
        tx, ty = math.floor(px / ts), math.floor(py / ts)
        if not (0 <= tx < width and 0 <= ty < height):
            raise TiledError(f"{where} > ponto '{obj.get('name', '')}': fora do mapa")
        center = ((tx + 0.5) * ts, (ty + 0.5) * ts)
        kind = props.get("tipo")
        if kind == "nascimento":
            spawn = center
            rows[ty] = rows[ty][:tx] + SAFE + rows[ty][tx + 1:]  # nascimento é sempre zona segura
        elif kind == "inimigo":
            enemy = props.get("inimigo")
            if known and enemy not in known:
                raise TiledError(f"{where} > ponto em ({tx}, {ty}): inimigo {enemy!r} não existe em enemies.yaml")
            enemy_spawns.append((str(enemy), center[0], center[1]))
        elif kind == "npc":
            npc = props.get("npc")
            if not isinstance(npc, str) or not npc:
                raise TiledError(f"{where} > ponto em ({tx}, {ty}): falta a propriedade 'npc' (id em npcs.yaml)")
            if rows[ty][tx] in WALK_BLOCKERS:
                raise TiledError(f"{where} > NPC {npc!r} em ({tx}, {ty}): o tile é bloqueado")
            npc_spawns.append((npc, center[0], center[1]))
        else:
            raise TiledError(f"{where} > ponto '{obj.get('name', '')}': propriedade 'tipo' deve ser "
                             "nascimento, inimigo ou npc")
    if spawn is None:
        raise TiledError(f"{where}: falta um ponto com tipo = nascimento na camada 'pontos'")

    props = _props(doc)
    default_type = props.get("zona_padrao", "pve")
    if default_type not in AREA_ZONE_TYPES:
        raise TiledError(f"{where}: propriedade do mapa 'zona_padrao' deve ser pve ou pvp")
    return TileMap(
        name=name, rows=rows, tile_size=ts, spawn=spawn, enemy_spawns=enemy_spawns, zones=zones,
        default_zone=Zone(id="", name=str(props.get("nome_zona_padrao", "")), type=default_type),
        title=str(props.get("nome", "")), portals=portals, npc_spawns=npc_spawns,
    )
