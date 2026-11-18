"""Testes da criação de itens: oficinas, receitas e o evento craft."""
import dataclasses
import random
import shutil
import tempfile
import unittest
from pathlib import Path

from server import config as C
from server.content import Content, ContentError
from server.db import Database
from server.rules import Rule
from server.stats import Stat
from server.tilemap import parse_map
from server.world import World

CONTENT = dataclasses.replace(Content.load(C.DATA_DIR), rules=())

MAP = """
##########
#S:......#
#........#
#........#
##########
"""
NPCS_YAML = """
artesao:
  name: Artesão
  dialogue:
    inicio:
      text: "Criar?"
      options:
        - text: Oficina
          craft: oficina
        - text: Tchau
"""
CRAFTING_YAML = """
oficina:
  name: Oficina de teste
  recipes:
    - make: gibao_de_couro
      needs: { couro_grosso: 4 }
      price: 5
    - make: pocao_vida
      qty: 2
      needs: { fragmento_espectral: 3 }
    - make: couraca_de_bruto
      needs: { couro_grosso: 2 }
      stats: { strength: 3 }
      skills: [onda]
    - make: espada_curta
      needs: { fragmento_espectral: 1 }
      if: { flags: [segredo] }
      hidden: true
"""


def make_content(crafting: str = CRAFTING_YAML, npcs: str = NPCS_YAML, extra: dict | None = None) -> Content:
    tmp = Path(tempfile.mkdtemp())
    try:
        for path in C.DATA_DIR.glob("*.yaml"):
            shutil.copy(path, tmp / path.name)
        (tmp / "npcs.yaml").write_text(npcs, encoding="utf-8")
        (tmp / "crafting.yaml").write_text(crafting, encoding="utf-8")
        (tmp / "quests.yaml").write_text("", encoding="utf-8")  # as missões reais citam outros NPCs
        for name, text in (extra or {}).items():
            with open(tmp / name, "a", encoding="utf-8") as f:
                f.write(text)
        return dataclasses.replace(Content.load(tmp), rules=())
    finally:
        shutil.rmtree(tmp)


def make_world(content: Content | None = None) -> World:
    tilemap = parse_map(MAP, "teste", CONTENT.enemy_by_mark, zones={"npcs": [{"id": "artesao", "at": [4, 2]}]})
    return World(tilemap, Database(":memory:"), content or make_content(), rng=random.Random(1))


class Base(unittest.TestCase):
    content: Content | None = None

    def setUp(self):
        self.world = make_world(self.content)
        self.p, _ = self.world.login("Teste", "1234", "humano")
        self.npc = next(iter(self.world.npcs.entities.values()))
        self.p.x, self.p.y = self.npc.x - 40, self.npc.y
        self.world.drain_outbox()

    def items(self):
        return self.world.content.items

    def give(self, item_id: str, qty: int) -> None:
        self.p.inventory.add(self.items()[item_id], qty)

    def ask(self, **msg) -> list[dict]:
        self.world.handle_message(self.p, {"t": "npc", **msg})
        return [m for target, m in self.world.drain_outbox() if target == self.p.id]

    def open_station(self) -> dict:
        self.ask(op="talk", id=self.npc.id)
        return next(m for m in self.ask(op="choose", i=0) if m["t"] == "craft")

    def craft(self, i: int, times=1) -> list[dict]:
        return self.ask(op="craft", i=i, times=times)

    @staticmethod
    def texts(msgs: list[dict]) -> list[str]:
        return [m["text"] for m in msgs if m["t"] == "sys"]


class StationTests(Base):
    def test_station_lists_visible_recipes(self):
        msg = self.open_station()
        self.assertEqual(msg["station"], "Oficina de teste")
        self.assertEqual(msg["name"], "Artesão")
        self.assertEqual([r["make"] for r in msg["recipes"]], ["gibao_de_couro", "pocao_vida", "couraca_de_bruto"])
        gibao = msg["recipes"][0]
        self.assertEqual((gibao["i"], gibao["needs"], gibao["price"], gibao["missing"]),
                         (0, [["couro_grosso", 4]], 5, []))
        self.assertEqual(msg["recipes"][1]["qty"], 2)
        self.assertEqual(msg["recipes"][2]["missing"], ["Força nível 3 (você: 1)", "Conhecer Onda de choque"])

    def test_hidden_recipe_appears_with_its_condition(self):
        self.p.flags.add("segredo")
        msg = self.open_station()
        self.assertEqual(msg["recipes"][-1]["make"], "espada_curta")
        self.assertEqual(msg["recipes"][-1]["i"], 3)


class CraftTests(Base):
    def test_craft_spends_materials_and_gold(self):
        self.give("couro_grosso", 5)
        self.p.inventory.add_gold(10)
        self.open_station()
        msgs = self.craft(0)
        inv = self.p.inventory
        self.assertEqual(inv.count("gibao_de_couro"), 1)
        self.assertEqual(inv.count("couro_grosso"), 1)
        self.assertEqual(inv.gold, 5)
        self.assertIn("Você criou 1× Gibão de couro por 5 moedas.", self.texts(msgs))
        self.assertTrue(any(m["t"] == "craft" for m in msgs))  # a janela é atualizada

    def test_craft_several_times_and_output_quantity(self):
        self.give("fragmento_espectral", 10)
        self.open_station()
        self.craft(1, times=3)
        self.assertEqual(self.p.inventory.count("pocao_vida"), 6)
        self.assertEqual(self.p.inventory.count("fragmento_espectral"), 1)

    def test_all_or_nothing(self):
        self.give("fragmento_espectral", 8)
        self.open_station()
        msgs = self.craft(1, times=3)  # precisa de 9
        self.assertEqual(self.p.inventory.count("pocao_vida"), 0)
        self.assertEqual(self.p.inventory.count("fragmento_espectral"), 8)
        self.assertIn("Faltam materiais: 1× Fragmento espectral.", self.texts(msgs))

    def test_not_enough_gold(self):
        self.give("couro_grosso", 4)
        self.open_station()
        msgs = self.craft(0)
        self.assertEqual(self.p.inventory.count("couro_grosso"), 4)
        self.assertIn("Faltam 5 moedas para criar Gibão de couro.", self.texts(msgs))

    def test_requirements(self):
        self.give("couro_grosso", 2)
        self.open_station()
        msgs = self.craft(2)
        self.assertEqual(self.p.inventory.count("couraca_de_bruto"), 0)
        self.assertTrue(any("Ainda não dá" in t for t in self.texts(msgs)))
        self.p.stats["strength"] = Stat(level=3)
        self.p.known.append("onda")
        self.craft(2)
        self.assertEqual(self.p.inventory.count("couraca_de_bruto"), 1)

    def test_hidden_recipe_cannot_be_crafted_without_condition(self):
        self.give("fragmento_espectral", 1)
        self.open_station()
        self.craft(3)
        self.assertEqual(self.p.inventory.count("espada_curta"), 0)
        self.p.flags.add("segredo")
        self.craft(3)
        self.assertEqual(self.p.inventory.count("espada_curta"), 1)

    def test_materials_free_room(self):
        inv = self.p.inventory
        self.give("couro_grosso", 4)  # um espaço, gasto inteiro pela receita
        for i in range(1, inv.size):
            inv.slots[i] = None
        inv.add(self.items()["espada_curta"], inv.size - 1)  # o resto cheio
        inv.add_gold(5)
        self.assertEqual(inv.free_slots(), 0)
        self.open_station()
        self.craft(0)
        self.assertEqual(inv.count("gibao_de_couro"), 1)

    def test_full_inventory_returns_materials(self):
        inv = self.p.inventory
        self.give("fragmento_espectral", 9)  # gasta 6: sobram 3, o espaço continua ocupado
        inv.add(self.items()["espada_curta"], inv.size - 1)  # o resto cheio (equipamento não empilha)
        self.open_station()
        msgs = self.craft(1, times=2)
        self.assertIn("Inventário cheio: libere espaço para o que vai criar.", self.texts(msgs))
        self.assertEqual(inv.count("fragmento_espectral"), 9)
        self.assertEqual(inv.count("pocao_vida"), 0)

    def test_bad_requests(self):
        self.give("couro_grosso", 4)
        self.p.inventory.add_gold(100)
        self.craft(0)  # sem a oficina aberta
        self.assertEqual(self.p.inventory.count("gibao_de_couro"), 0)
        self.open_station()
        for i, times in ((9, 1), (-1, 1), (True, 1), ("0", 1), (0, 0), (0, -2), (0, "3"), (0, True)):
            self.craft(i, times)
        self.assertEqual(self.p.inventory.count("gibao_de_couro"), 0)
        self.p.x += 200  # longe demais
        self.craft(0)
        self.assertEqual(self.p.inventory.count("gibao_de_couro"), 0)

    def test_times_is_capped(self):
        old = C.CRAFT_MAX_TIMES
        C.CRAFT_MAX_TIMES = 5
        try:
            self.give("fragmento_espectral", 30)
            self.open_station()
            self.craft(1, times=10_000)  # vira 5
            self.assertEqual(self.p.inventory.count("pocao_vida"), 10)
            self.assertEqual(self.p.inventory.count("fragmento_espectral"), 15)
        finally:
            C.CRAFT_MAX_TIMES = old


class CraftEventTests(Base):
    def setUp(self):
        rule = Rule(id="r", when="craft", conditions=(("item", "pocao_vida"), ("station", "oficina")),
                    actions=(("gain_xp", {"stat": "spirit", "amount": 5.0}),))
        self.content = dataclasses.replace(make_content(), rules=(rule,))
        super().setUp()

    def test_event_fires_once_per_craft(self):
        self.give("fragmento_espectral", 6)
        self.open_station()
        before = self.p.stats["spirit"].xp
        self.craft(1, times=2)
        self.assertAlmostEqual(self.p.stats["spirit"].xp - before, 10.0)


class CraftingContentTests(unittest.TestCase):
    def assert_error(self, *fragments, crafting=CRAFTING_YAML, npcs=NPCS_YAML, extra=None):
        with self.assertRaises(ContentError) as ctx:
            make_content(crafting, npcs, extra)
        for fragment in fragments:
            self.assertIn(fragment, str(ctx.exception))

    def test_real_content(self):
        self.assertIn("forja", CONTENT.crafting)
        forja = CONTENT.crafting["forja"]
        self.assertTrue(any(r.hidden for r in forja.recipes))

    def test_recipe_validation(self):
        base = "oficina:\n  name: X\n  recipes:\n"
        self.assert_error("crafting.yaml > oficina.recipes[0]", "'nada'", crafting=base + "    - { make: nada, needs: { couro_grosso: 1 } }\n")
        self.assert_error("'sumiu'", crafting=base + "    - { make: pocao_vida, needs: { sumiu: 1 } }\n")
        self.assert_error("próprio item", crafting=base + "    - { make: pocao_vida, needs: { pocao_vida: 2 } }\n")
        self.assert_error("pelo menos um material", crafting=base + "    - { make: pocao_vida, needs: {} }\n")
        self.assert_error("hidden", crafting=base + "    - { make: pocao_vida, needs: { couro_grosso: 1 }, hidden: true }\n")
        self.assert_error("atributo 'sorte'",
                          crafting=base + "    - { make: pocao_vida, needs: { couro_grosso: 1 }, stats: { sorte: 1 } }\n")
        self.assert_error("pelo menos uma receita", crafting="oficina:\n  name: X\n  recipes: []\n")
        self.assert_error("tempo", crafting=base + "    - { make: pocao_vida, needs: { couro_grosso: 1 }, tempo: 3 }\n")

    def test_no_endless_profit(self):
        # poção custa 10 na mercearia; o armeiro compra espada curta por 40% de 30 = 12
        self.assert_error("oficina.recipes[0]", "dá para criar e vender sem parar",
                          crafting="oficina:\n  name: X\n  recipes:\n    - { make: espada_curta, needs: { pocao_vida: 1 } }\n")
        make_content("oficina:\n  name: X\n  recipes:\n    - { make: espada_curta, needs: { pocao_vida: 1 }, price: 2 }\n")

    def test_npc_station_reference(self):
        self.assert_error("oficina 'sumiu'", npcs=NPCS_YAML.replace("craft: oficina", "craft: sumiu"))
        self.assert_error("só um de goto, shop, trainer ou craft",
                          npcs=NPCS_YAML.replace("craft: oficina", "craft: oficina\n          shop: mercearia"))

    def test_craft_event_conditions(self):
        rule = "\nfez:\n  when: craft\n  if: { item: nada }\n  do: [ { message: oi } ]\n"
        self.assert_error("rules.yaml > fez", "'nada'", extra={"rules.yaml": rule})
        rule = "\nfez:\n  when: craft\n  if: { station: nada }\n  do: [ { message: oi } ]\n"
        self.assert_error("rules.yaml > fez", "'nada'", extra={"rules.yaml": rule})


if __name__ == "__main__":
    unittest.main()
