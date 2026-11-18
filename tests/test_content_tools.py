"""Testes das ferramentas de conteúdo: recarga com o jogo rodando e mapas do Tiled."""
import base64
import dataclasses
import json
import random
import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from server import config as C
from server.content import Content, ContentError
from server.db import Database
from server.gamedata import load_game_data, watched_files_state
from server.stats import Stat
from server.tiled import TiledError, parse_tmj
from server.tilemap import load_map, load_maps, parse_map
from server.world import World
from tools.maps import main as maps_main
from tools.maps import to_tiled, write_tileset

DT = 1.0 / C.TICK_RATE
CONTENT = dataclasses.replace(Content.load(C.DATA_DIR), rules=())
MAP = """
##########
#S:......#
#....E...#
#........#
##########
"""


def small_map(name="teste"):
    return parse_map(MAP, name, CONTENT.enemy_by_mark, zones={"name": "Teste"})


class ReloadTests(unittest.TestCase):
    def setUp(self):
        self.world = World({"teste": small_map()}, Database(":memory:"), CONTENT, rng=random.Random(1),
                           start_map="teste")
        self.p, _ = self.world.login("Teste", "1234", "humano")
        self.source = None
        self.world.reload_source = lambda: self.source()

    def reload_with(self, content, maps=None):
        self.source = lambda: (content, maps or {"teste": small_map()})
        self.world.drain_outbox()
        ok, text = self.world.reload_from_disk()
        return ok, text, self.world.drain_outbox()

    def test_changed_skill_and_enemy_apply_live(self):
        golpe = dataclasses.replace(CONTENT.skills["golpe"], damage_base=50.0)
        espectro = dataclasses.replace(CONTENT.enemies["espectro"], hp=999.0)
        content = dataclasses.replace(CONTENT, skills={**CONTENT.skills, "golpe": golpe},
                                      enemies={**CONTENT.enemies, "espectro": espectro})
        ok, _, out = self.reload_with(content)
        self.assertTrue(ok)
        self.assertEqual(self.world._skill_damage(self.p, self.world.content.skills["golpe"]), 50 + 2)
        enemy = next(iter(self.world.enemies.values()))
        self.assertEqual(enemy.max_hp, 999.0)
        self.assertEqual(enemy.hp, 999.0)
        kinds = [m["t"] for t, m in out]
        self.assertIn("content", kinds)
        self.assertLess(kinds.index("content"), kinds.index("map"))  # definições antes do mapa

    def test_removed_item_leaves_inventory_and_ground(self):
        self.p.inventory.add(CONTENT.items["pocao_vida"], 3)
        self.p.inventory.add(CONTENT.items["gibao_de_couro"], 1)
        self.world._item_action(self.p, "use", 1)  # equipa o gibão
        self.world._drop(CONTENT.items["pocao_vida"], 1, "teste", 100, 100, owner=None, scatter=False)
        items = {k: v for k, v in CONTENT.items.items() if k not in ("pocao_vida", "gibao_de_couro")}
        ok, _, _ = self.reload_with(dataclasses.replace(CONTENT, items=items))
        self.assertTrue(ok)
        self.assertEqual(self.p.inventory.count("pocao_vida"), 0)
        self.assertNotIn("armor", self.p.inventory.equipment)
        self.assertEqual(self.world.drops, {})

    def test_removed_class_falls_back(self):
        aprendiz = CONTENT.classes["aprendiz"]
        classes = {k: v for k, v in CONTENT.classes.items() if k != "aprendiz"}
        guerreiro = dataclasses.replace(aprendiz, id="guerreiro", name="Guerreiro", start_skills=("golpe",))
        races = {**CONTENT.races, "humano": dataclasses.replace(CONTENT.races["humano"], start_class="guerreiro")}
        content = dataclasses.replace(CONTENT, classes={**classes, "guerreiro": guerreiro}, races=races)
        ok, text, _ = self.reload_with(content)
        self.assertTrue(ok)
        self.assertEqual(self.p.class_id, "guerreiro")
        self.assertIn("golpe", self.p.bar)  # continua sabendo o que sabia
        self.assertIn("guerreiro", text)

    def test_removed_map_moves_player(self):
        other = small_map("outro")
        ok, text, _ = self.reload_with(CONTENT, {"outro": other})
        self.assertTrue(ok)
        self.assertEqual(self.p.map_id, "outro")
        self.assertEqual((self.p.x, self.p.y), other.spawn)

    def test_player_inside_new_wall_goes_to_spawn(self):
        self.p.x, self.p.y = (7.5 * 32, 3.5 * 32)
        walled = parse_map(MAP.replace("#........#", "#.....####"), "teste", CONTENT.enemy_by_mark)
        self.reload_with(CONTENT, {"teste": walled})
        self.assertEqual((self.p.x, self.p.y), walled.spawn)

    def test_invalid_content_changes_nothing(self):
        before = self.world.content
        enemies_before = dict(self.world.enemies)

        def broken():
            raise ContentError("skills.yaml > golpe: 'cooldown' deve ser um número")

        self.source = broken
        ok, text = self.world.reload_from_disk()
        self.assertFalse(ok)
        self.assertIn("cooldown", text)
        self.assertIs(self.world.content, before)
        self.assertEqual(self.world.enemies, enemies_before)

    def test_removed_org_and_role(self):
        self.world.handle_message(self.p, {"t": "chat", "text": "/entrar vigilia"})
        self.world.db.set_role("Teste", "gm")
        self.world.admin.load_player(self.p)
        content = dataclasses.replace(CONTENT, orgs={}, roles={k: v for k, v in CONTENT.roles.items() if k != "gm"})
        ok, text, _ = self.reload_with(content)
        self.assertTrue(ok)
        self.assertIsNone(self.world.orgs.of(self.p))
        self.assertIsNone(self.p.staff_role)
        self.assertIn("vigilia", text)

    def test_reload_needs_permission(self):
        self.world.db.set_role("Teste", "gm")  # gm não tem "reload"
        self.world.admin.load_player(self.p)
        self.source = lambda: (CONTENT, {"teste": small_map()})
        self.world.drain_outbox()
        self.world.handle_message(self.p, {"t": "admin", "op": "reload"})
        result = next(m for _, m in self.world.drain_outbox() if m["t"] == "admin_result")
        self.assertFalse(result["ok"])
        self.world.db.set_role("Teste", "admin")
        self.world.admin.load_player(self.p)
        self.world.handle_message(self.p, {"t": "admin", "op": "reload"})
        result = next(m for _, m in self.world.drain_outbox() if m["t"] == "admin_result")
        self.assertTrue(result["ok"], result)

    def test_game_continues_after_reload(self):
        self.reload_with(CONTENT)
        for _ in range(40):
            self.world.handle_message(self.p, {"t": "move", "seq": self.p.last_seq + 1, "right": True})
            self.world.step(DT)
        self.assertGreater(self.p.x, small_map().spawn[0])

    def test_real_game_data_loads_and_is_watched(self):
        content, maps = load_game_data()
        self.assertIn(C.START_MAP, maps)
        files = watched_files_state()
        self.assertTrue(any(f.endswith("skills.yaml") for f in files))
        self.assertTrue(any(f.endswith(".txt") or f.endswith(".tmj") for f in files))


class TiledTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        for path in C.MAPS_DIR.iterdir():
            if path.is_file():
                shutil.copy(path, self.dir / path.name)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_round_trip_is_exact(self):
        marks = {e.mark: e.id for e in CONTENT.enemies.values()}
        before = {name: load_map(self.dir / f"{name}.txt", CONTENT.enemy_by_mark) for name in ("inicio", "bosque")}
        for name in before:
            to_tiled(name, self.dir, CONTENT)
        after = load_maps(self.dir, CONTENT.enemy_by_mark, enemy_ids=CONTENT.enemies)
        self.assertTrue((self.dir / "antigos" / "inicio.txt").exists())
        self.assertFalse((self.dir / "inicio.txt").exists())
        for name, b in before.items():
            a = after[name]
            self.assertEqual(a.rows, b.rows)
            self.assertEqual(a.spawn, b.spawn)
            self.assertEqual(sorted(a.enemy_spawns), sorted((marks[m], x, y) for m, x, y in b.enemy_spawns))
            self.assertEqual([(z.name, z.type, z.area) for z in a.zones], [(z.name, z.type, z.area) for z in b.zones])
            self.assertEqual(a.portals, b.portals)
            self.assertEqual((a.title, a.default_zone), (b.title, b.default_zone))

    def test_converted_map_runs_in_world(self):
        to_tiled("bosque", self.dir, CONTENT)
        maps = load_maps(self.dir, CONTENT.enemy_by_mark, enemy_ids=CONTENT.enemies)
        world = World(maps, Database(":memory:"), CONTENT, start_map="inicio")
        bosque_enemies = [e for e in world.enemies.values() if e.map_id == "bosque"]
        self.assertEqual({e.defn.id for e in bosque_enemies}, {"devorador", "bruto"})

    def doc(self):
        """O bosque convertido para o Tiled (converte só na primeira vez: a conversão move o .txt)."""
        if not hasattr(self, "_converted"):
            to_tiled("bosque", self.dir, CONTENT)
            self._converted = (self.dir / "bosque.tmj").read_text(encoding="utf-8")
        return json.loads(self._converted)

    def write(self, doc):
        path = self.dir / "bosque.tmj"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def test_base64_and_zlib_layers(self):
        doc = self.doc()
        terrain = doc["layers"][0]
        raw = struct.pack(f"<{len(terrain['data'])}I", *terrain["data"])
        expected = parse_tmj(self.write(doc), CONTENT.enemies).rows
        terrain["data"] = base64.b64encode(raw).decode()
        terrain["encoding"] = "base64"
        self.assertEqual(parse_tmj(self.write(doc), CONTENT.enemies).rows, expected)
        terrain["data"] = base64.b64encode(zlib.compress(raw)).decode()
        terrain["compression"] = "zlib"
        self.assertEqual(parse_tmj(self.write(doc), CONTENT.enemies).rows, expected)

    def test_flipped_tiles_are_accepted(self):
        doc = self.doc()
        doc["layers"][0]["data"][40] |= 0x80000000  # tile espelhado no Tiled
        parse_tmj(self.write(doc), CONTENT.enemies)

    def test_validation_errors(self):
        cases = {
            "sem camada": lambda d: d["layers"].pop(0),
            "tile vazio": lambda d: d["layers"][0]["data"].__setitem__(40, 0),
            "inimigo desconhecido": lambda d: d["layers"][3]["objects"][1]["properties"][1].__setitem__("value", "dragao"),
            "sem nascimento": lambda d: d["layers"][3]["objects"].pop(0),
            "zona sem tipo": lambda d: d["layers"][1]["objects"][0].__setitem__("properties", []),
            "infinito": lambda d: d.__setitem__("infinite", True),
        }
        for label, change in cases.items():
            doc = self.doc()
            change(doc)
            with self.assertRaises(TiledError, msg=label):
                parse_tmj(self.write(doc), CONTENT.enemies)

    def test_both_formats_is_an_error(self):
        to_tiled("bosque", self.dir, CONTENT)
        shutil.copy(self.dir / "antigos" / "bosque.txt", self.dir / "bosque.txt")
        with self.assertRaises(ValueError):
            load_maps(self.dir, CONTENT.enemy_by_mark, enemy_ids=CONTENT.enemies)

    def test_tileset_png(self):
        write_tileset(self.dir)
        png = (self.dir / "terreno.png").read_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        tileset = json.loads((self.dir / "terreno.tsj").read_text(encoding="utf-8"))
        self.assertEqual(tileset["tilecount"], 6)

    def test_cli(self):
        self.assertEqual(maps_main(["verificar"], maps_dir=self.dir), 0)
        self.assertEqual(maps_main(["para-tiled", "nao_existe"], maps_dir=self.dir), 1)
        self.assertEqual(maps_main(["para-tiled", "inicio"], maps_dir=self.dir), 0)
        self.assertTrue((self.dir / "inicio.tmj").exists())


if __name__ == "__main__":
    unittest.main()
