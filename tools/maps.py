"""Ferramentas de mapa.

Uso:
  ./mapas.sh para-tiled inicio   converte data/maps/inicio.txt (+ .yaml) para inicio.tmj,
                                 que abre no Tiled; o .txt e o .yaml vão para data/maps/antigos/
  ./mapas.sh tileset             (re)gera o conjunto de tiles do Tiled (terreno.tsj + terreno.png)
  ./mapas.sh verificar           carrega todos os mapas e mostra um resumo (ou o erro)

Para criar um mapa novo no Tiled: Arquivo > Novo mapa (ortogonal, tamanho fixo, tiles
de 32 px), adicione o conjunto data/maps/terreno.tsj, crie as camadas descritas em
server/tiled.py e salve como .tmj em data/maps/. O jeito mais fácil é partir de um
mapa convertido (copie inicio.tmj com outro nome).
"""
from __future__ import annotations

import argparse
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path

from server import config as C
from server.content import Content
from server.tilemap import load_map, load_maps

TILESET_NAME = "terreno"
# ordem dos tiles no conjunto (o id local de cada um é a posição aqui)
TILE_ORDER = [".", ",", "#", "~", "T", ":"]
TILE_LABELS = {".": "chão", ",": "mato", "#": "parede", "~": "água", "T": "árvore", ":": "zona segura"}
TILED_VERSION = "1.10"


# ------------------------------------------------------------ imagem do conjunto de tiles


def _png(width: int, height: int, pixels: list[list[tuple[int, int, int]]]) -> bytes:
    """PNG RGB simples, sem bibliotecas externas."""
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in pixels)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def _tile_pixels(char: str, ts: int) -> list[list[tuple[int, int, int]]]:
    """Desenho simples de cada tile, parecido com o do jogo."""
    base = {".": (79, 107, 52), ",": (70, 98, 45), "#": (58, 61, 71), "~": (43, 85, 120),
            "T": (79, 107, 52), ":": (139, 132, 116)}[char]
    px = [[base] * ts for _ in range(ts)]
    for y in range(ts):
        for x in range(ts):
            if char == "#" and y < ts * 0.35:
                px[y][x] = (74, 78, 90)
            if char in "#:" and (x in (0, ts - 1) or y in (0, ts - 1)):
                px[y][x] = tuple(max(0, c - 25) for c in base)  # type: ignore[assignment]
            if char == "," and (x * 7 + y * 3) % 11 == 0 and y % 4 < 2:
                px[y][x] = (32, 52, 20)
            if char == "~" and y in (10, 20) and 4 < x < ts - 4:
                px[y][x] = (90, 130, 160)
            if char == "T":
                dx, dy = x - ts / 2, y - ts * 0.42
                if dx * dx + dy * dy < (ts * 0.42) ** 2:
                    px[y][x] = (44, 74, 34)
                elif abs(dx) < 3 and y > ts * 0.55:
                    px[y][x] = (90, 61, 36)
    return px


def write_tileset(maps_dir: Path, ts: int = C.TILE_SIZE) -> None:
    tiles = [_tile_pixels(ch, ts) for ch in TILE_ORDER]
    pixels = [[p for tile in tiles for p in tile[y]] for y in range(ts)]
    (maps_dir / f"{TILESET_NAME}.png").write_bytes(_png(ts * len(TILE_ORDER), ts, pixels))
    tileset = {
        "type": "tileset", "version": TILED_VERSION, "name": TILESET_NAME,
        "image": f"{TILESET_NAME}.png", "imagewidth": ts * len(TILE_ORDER), "imageheight": ts,
        "tilewidth": ts, "tileheight": ts, "tilecount": len(TILE_ORDER), "columns": len(TILE_ORDER),
        "margin": 0, "spacing": 0,
        "tiles": [
            {"id": i, "properties": [
                {"name": "caractere", "type": "string", "value": ch},
                {"name": "descricao", "type": "string", "value": TILE_LABELS[ch]},
            ]}
            for i, ch in enumerate(TILE_ORDER)
        ],
    }
    (maps_dir / f"{TILESET_NAME}.tsj").write_text(json.dumps(tileset, ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------------------------------------ conversão


def to_tiled(name: str, maps_dir: Path, content: Content) -> Path:
    txt = maps_dir / f"{name}.txt"
    if not txt.exists():
        raise FileNotFoundError(f"{txt} não existe")
    tilemap = load_map(txt, content.enemy_by_mark)
    ts = tilemap.tile_size
    if not (maps_dir / f"{TILESET_NAME}.tsj").exists():
        write_tileset(maps_dir, ts)

    gid = {ch: i + 1 for i, ch in enumerate(TILE_ORDER)}  # firstgid 1
    data = [gid[ch] for row in tilemap.rows for ch in row]
    next_id = 1

    def obj(name_: str, x: float, y: float, w: float, h: float, props: dict, point: bool = False) -> dict:
        nonlocal next_id
        o = {"id": next_id, "name": name_, "type": "", "x": x, "y": y, "width": w, "height": h,
             "rotation": 0, "visible": True,
             "properties": [{"name": k, "type": "int" if isinstance(v, int) else "string", "value": v}
                            for k, v in props.items()]}
        if point:
            o["point"] = True
        next_id += 1
        return o

    def rect(area: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = area
        return x0 * ts, y0 * ts, (x1 - x0 + 1) * ts, (y1 - y0 + 1) * ts

    zones = [obj(z.name, *rect(z.area), {"tipo": z.type}) for z in tilemap.zones]
    portals = [obj(f"para {p.to}", *rect(p.area), {"destino": p.to, "chegada_x": p.at[0], "chegada_y": p.at[1]})
               for p in tilemap.portals]
    points = [obj("nascimento", tilemap.spawn[0], tilemap.spawn[1], 0, 0, {"tipo": "nascimento"}, point=True)]
    for mark, x, y in tilemap.enemy_spawns:
        enemy = content.enemy_by_mark[mark]
        points.append(obj(enemy.name, x, y, 0, 0, {"tipo": "inimigo", "inimigo": enemy.id}, point=True))
    for npc_id, x, y in tilemap.npc_spawns:
        npc = content.npcs.get(npc_id)
        points.append(obj(npc.name if npc else npc_id, x, y, 0, 0, {"tipo": "npc", "npc": npc_id}, point=True))

    def objectgroup(layer_id: int, layer_name: str, objects: list[dict]) -> dict:
        return {"id": layer_id, "name": layer_name, "type": "objectgroup", "draworder": "topdown",
                "objects": objects, "opacity": 1, "visible": True, "x": 0, "y": 0}

    doc = {
        "type": "map", "version": TILED_VERSION, "tiledversion": "1.10.2",
        "orientation": "orthogonal", "renderorder": "right-down", "infinite": False, "compressionlevel": -1,
        "width": tilemap.width, "height": tilemap.height, "tilewidth": ts, "tileheight": ts,
        "nextlayerid": 5, "nextobjectid": next_id,
        "properties": [
            {"name": "nome", "type": "string", "value": tilemap.title or name},
            {"name": "zona_padrao", "type": "string", "value": tilemap.default_zone.type},
            {"name": "nome_zona_padrao", "type": "string", "value": tilemap.default_zone.name},
        ],
        "tilesets": [{"firstgid": 1, "source": f"{TILESET_NAME}.tsj"}],
        "layers": [
            {"id": 1, "name": "terreno", "type": "tilelayer", "width": tilemap.width, "height": tilemap.height,
             "data": data, "opacity": 1, "visible": True, "x": 0, "y": 0},
            objectgroup(2, "zonas", zones),
            objectgroup(3, "portais", portals),
            objectgroup(4, "pontos", points),
        ],
    }
    out = maps_dir / f"{name}.tmj"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    old_dir = maps_dir / "antigos"
    old_dir.mkdir(exist_ok=True)
    for suffix in (".txt", ".yaml"):
        source = maps_dir / f"{name}{suffix}"
        if source.exists():
            shutil.move(str(source), old_dir / source.name)
    return out


# ------------------------------------------------------------ linha de comando


def main(argv: list[str] | None = None, maps_dir: Path = C.MAPS_DIR, data_dir: Path = C.DATA_DIR) -> int:
    parser = argparse.ArgumentParser(description="Ferramentas de mapa.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    conv = sub.add_parser("para-tiled", help="converte um mapa em texto para o Tiled (.tmj)")
    conv.add_argument("mapa")
    sub.add_parser("tileset", help="(re)gera o conjunto de tiles do Tiled")
    sub.add_parser("verificar", help="carrega todos os mapas e mostra um resumo")
    args = parser.parse_args(argv)

    content = Content.load(data_dir)
    try:
        if args.cmd == "para-tiled":
            out = to_tiled(args.mapa, maps_dir, content)
            print(f"Criado {out.name}. O .txt e o .yaml antigos foram para {maps_dir.name}/antigos/.")
            print("Abra o .tmj no Tiled para editar; salve como JSON (.tmj) no mesmo lugar.")
        elif args.cmd == "tileset":
            write_tileset(maps_dir)
            print(f"Conjunto de tiles gerado em {maps_dir / (TILESET_NAME + '.tsj')}.")
        maps = load_maps(maps_dir, content.enemy_by_mark, enemy_ids=content.enemies, npc_ids=content.npcs)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    for tilemap in maps.values():
        fmt = "Tiled" if (maps_dir / f"{tilemap.name}.tmj").exists() else "texto"
        print(f"• {tilemap.name} ({fmt}): {tilemap.title or '—'}, {tilemap.width}×{tilemap.height}, "
              f"{len(tilemap.enemy_spawns)} inimigos, {len(tilemap.npc_spawns)} NPCs, {len(tilemap.zones)} zonas, "
              f"portais para {', '.join(p.to for p in tilemap.portals) or 'nenhum'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
