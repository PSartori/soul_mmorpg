"""Testes de NPCs (conversa) e lojas (compra e venda)."""
import dataclasses
import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from server import config as C
from server.content import Content, ContentError
from server.db import Database
from server.gamedata import load_game_data
from server.tiled import TiledError, parse_tmj
from server.tilemap import load_maps, parse_map
from server.world import World
from tools.maps import to_tiled

DT = 1.0 / C.TICK_RATE
CONTENT = dataclasses.replace(Content.load(C.DATA_DIR), rules=())

#           0123456789
MAP = """
##########
#S:......#
#........#
#........#
##########
"""
# NPCs de teste, independentes do conteúdo de exemplo de data/npcs.yaml
NPCS_YAML = """
vendedor:
  name: Vendedor
  title: Mercador
  dialogue:
    inicio:
      text: "Olá, {name}."
      options:
        - text: Loja
          shop: loja
        - text: Só para Despertos
          if: { race: desperto }
        - text: Presente
          if: { not_flags: [ganhou] }
          do:
            - give_item: { id: pocao_vida, qty: 2 }
            - set_flag: ganhou
          goto: depois
        - text: Ricos
          if: { gold: 100 }
          goto: depois
        - text: Tchau
    depois:
      text: "Volte sempre."
"""
SHOPS_YAML = """
loja:
  name: Loja de teste
  sells:
    - pocao_vida
    - { item: espada_curta, price: 50 }
  buys: [material, pocao_vida]
"""


def make_content(npcs: str = NPCS_YAML, shops: str = SHOPS_YAML) -> Content:
    tmp = Path(tempfile.mkdtemp())
    try:
        for path in C.DATA_DIR.glob("*.yaml"):
            shutil.copy(path, tmp / path.name)
        (tmp / "npcs.yaml").write_text(npcs, encoding="utf-8")
        (tmp / "shops.yaml").write_text(shops, encoding="utf-8")
        return dataclasses.replace(Content.load(tmp), rules=())
    finally:
        shutil.rmtree(tmp)


def make_world(content: Content | None = None, npcs=({"id": "vendedor", "at": [4, 2]},)) -> World:
    tilemap = parse_map(MAP, "teste", CONTENT.enemy_by_mark, zones={"npcs": list(npcs)})
    return World(tilemap, Database(":memory:"), content or make_content(), rng=random.Random(1))


class Base(unittest.TestCase):
    def setUp(self):
        self.world = make_world()
        self.p, _ = self.world.login("Teste", "1234", "humano")
        self.npc = next(iter(self.world.npcs.entities.values()))
        self.p.x, self.p.y = self.npc.x - 40, self.npc.y  # ao alcance
        self.world.drain_outbox()

    def send(self, **msg):
        self.world.handle_message(self.p, {"t": "npc", **msg})
        return [m for target, m in self.world.drain_outbox() if target == self.p.id]

    def last(self, kind, msgs):
        found = [m for m in msgs if m["t"] == kind]
        self.assertTrue(found, f"nenhuma mensagem {kind!r} em {msgs}")
        return found[-1]

    def option_texts(self, dialog):
        return [o["text"] for o in dialog["options"]]

    def open_shop(self):
        self.send(op="talk", id=self.npc.id)
        return self.last("shop", self.send(op="choose", i=0))


class DialogTests(Base):
    def test_talk_shows_text_and_visible_options(self):
        dialog = self.last("dialog", self.send(op="talk", id=self.npc.id))
        self.assertEqual(dialog["name"], "Vendedor")
        self.assertEqual(dialog["title"], "Mercador")
        self.assertEqual(dialog["text"], "Olá, Teste.")
        # Humano sem moedas: não vê "Só para Despertos" nem "Ricos"
        self.assertEqual(self.option_texts(dialog), ["Loja", "Presente", "Tchau"])
        self.assertEqual([o["i"] for o in dialog["options"]], [0, 2, 4])  # índices do arquivo

    def test_gold_condition_means_at_least(self):
        self.p.inventory.add_gold(100)
        dialog = self.last("dialog", self.send(op="talk", id=self.npc.id))
        self.assertIn("Ricos", self.option_texts(dialog))

    def test_too_far_to_talk(self):
        self.p.x = self.npc.x - C.NPC_TALK_RANGE - 5
        msgs = self.send(op="talk", id=self.npc.id)
        self.assertFalse([m for m in msgs if m["t"] == "dialog"])
        self.assertIn("perto", self.last("sys", msgs)["text"])

    def test_option_actions_run_once(self):
        self.send(op="talk", id=self.npc.id)
        dialog = self.last("dialog", self.send(op="choose", i=2))
        self.assertEqual(dialog["text"], "Volte sempre.")
        self.assertEqual(dialog["options"], [])  # nó sem opções: o cliente mostra "Encerrar"
        self.assertEqual(self.p.inventory.count("pocao_vida"), 2)
        self.assertIn("ganhou", self.p.flags)
        self.last("npc_close", self.send(op="choose", i=0))  # encerrar
        dialog = self.last("dialog", self.send(op="talk", id=self.npc.id))
        self.assertNotIn("Presente", self.option_texts(dialog))
        self.send(op="choose", i=2)  # escolher a opção escondida não faz nada
        self.assertEqual(self.p.inventory.count("pocao_vida"), 2)

    def test_option_without_goto_ends(self):
        self.send(op="talk", id=self.npc.id)
        self.last("npc_close", self.send(op="choose", i=4))
        self.assertNotIn(self.p.id, self.world.npcs.talks)

    def test_choose_without_conversation_or_bad_index(self):
        self.assertEqual(self.send(op="choose", i=0), [])
        self.send(op="talk", id=self.npc.id)
        for bad in (99, -1, "0", True, None):
            self.assertEqual(self.send(op="choose", i=bad), [])

    def test_walking_away_closes(self):
        self.send(op="talk", id=self.npc.id)
        self.p.x = self.npc.x - C.NPC_LEAVE_RANGE - 5
        self.world.step(DT)
        self.assertTrue([m for t, m in self.world.drain_outbox() if t == self.p.id and m["t"] == "npc_close"])
        self.assertNotIn(self.p.id, self.world.npcs.talks)

    def test_death_and_logout_close(self):
        self.send(op="talk", id=self.npc.id)
        self.p.state, self.p.dead_timer = "dead", C.RESPAWN_SECONDS
        self.world.step(DT)
        self.assertNotIn(self.p.id, self.world.npcs.talks)
        self.p.state = "idle"
        self.send(op="talk", id=self.npc.id)
        self.world.logout(self.p)
        self.assertNotIn(self.p.id, self.world.npcs.talks)

    def test_npc_in_snapshot_and_cannot_be_hit(self):
        self.world.step(DT)
        snap = [m for t, m in self.world.drain_outbox() if t == self.p.id and m["t"] == "snap"][-1]
        npc_snap = next(e for e in snap["ents"] if e["k"] == "npc")
        self.assertEqual((npc_snap["t"], npc_snap["n"]), ("vendedor", "Vendedor"))
        self.assertNotIn("hp", npc_snap)
        self.p.x, self.p.y, self.p.facing = self.npc.x - 20, self.npc.y, "right"
        self.world.handle_message(self.p, {"t": "skill", "id": "golpe"})
        self.world.step(DT)
        self.assertIn(self.npc.id, self.world.npcs.entities)
        self.assertIsNone(self.world._entity(self.npc.id))  # não é alvo de nada

    def test_dialog_actions_can_change_class(self):
        content = make_content(npcs="""
mestre:
  name: Mestre
  dialogue:
    inicio:
      text: Quer despertar?
      options:
        - text: Sim
          if: { race: humano }
          do: [ { set_race: desperto } ]
""")
        world = make_world(content, npcs=({"id": "mestre", "at": [4, 2]},))
        p, _ = world.login("Teste", "1234", "humano")
        npc = next(iter(world.npcs.entities.values()))
        p.x, p.y = npc.x, npc.y
        world.handle_message(p, {"t": "npc", "op": "talk", "id": npc.id})
        world.handle_message(p, {"t": "npc", "op": "choose", "i": 0})
        self.assertEqual((p.race_id, p.class_id), ("desperto", "iniciado"))


class ShopTests(Base):
    def test_shop_lists_prices(self):
        shop = self.open_shop()
        self.assertEqual(shop["shop"], "Loja de teste")
        self.assertEqual(shop["sells"], [{"id": "pocao_vida", "price": 10}, {"id": "espada_curta", "price": 50}])
        # compra materiais e poção de vida, a 40% do valor (no mínimo 1)
        self.assertEqual(shop["buys"]["pocao_vida"], 4)
        self.assertEqual(shop["buys"]["fragmento_espectral"], 1)
        self.assertEqual(shop["buys"]["couro_grosso"], 2)
        self.assertNotIn("espada_curta", shop["buys"])
        self.assertNotIn("pocao_energia", shop["buys"])
        self.assertEqual(self.world.npcs.talks[self.p.id].shop, "loja")

    def test_buy(self):
        self.open_shop()
        self.p.inventory.add_gold(35)
        msgs = self.send(op="buy", item="pocao_vida", qty=3)
        self.assertEqual(self.p.inventory.count("pocao_vida"), 3)
        self.assertEqual(self.p.inventory.gold, 5)
        self.assertIn("30 moedas", self.last("sys", msgs)["text"])

    def test_buy_without_enough_gold(self):
        self.open_shop()
        self.p.inventory.add_gold(49)
        msgs = self.send(op="buy", item="espada_curta", qty=1)
        self.assertEqual(self.p.inventory.count("espada_curta"), 0)
        self.assertEqual(self.p.inventory.gold, 49)
        self.assertIn("Faltam 1", self.last("sys", msgs)["text"])

    def test_buy_needs_room(self):
        self.open_shop()
        self.p.inventory.add_gold(1000)
        for _ in range(C.INVENTORY_SIZE - 1):
            self.p.inventory.add(CONTENT.items["espada_curta"], 1)
        msgs = self.send(op="buy", item="espada_curta", qty=2)  # só cabe 1
        self.assertIn("Só cabem mais 1", self.last("sys", msgs)["text"])
        self.assertEqual(self.p.inventory.gold, 1000)
        self.send(op="buy", item="espada_curta", qty=1)
        self.assertEqual(self.p.inventory.gold, 950)
        self.assertIn("cheio", self.last("sys", self.send(op="buy", item="pocao_vida", qty=1))["text"])

    def test_buy_stacks_fill_partial_slots(self):
        self.open_shop()
        self.p.inventory.add_gold(1000)
        for _ in range(C.INVENTORY_SIZE):
            self.p.inventory.add(CONTENT.items["espada_curta"], 1)
        self.p.inventory.slots[0] = None
        self.send(op="buy", item="pocao_vida", qty=10)  # uma pilha cheia cabe no espaço livre
        self.assertEqual(self.p.inventory.count("pocao_vida"), 10)

    def test_cannot_buy_what_shop_does_not_sell_or_bad_qty(self):
        self.open_shop()
        self.p.inventory.add_gold(1000)
        for item, qty in (("pocao_energia", 1), ("nada", 1), ("pocao_vida", 0), ("pocao_vida", -3),
                          ("pocao_vida", "2"), ("pocao_vida", True), (None, 1)):
            self.send(op="buy", item=item, qty=qty)
        self.assertEqual(self.p.inventory.gold, 1000)

    def test_buy_needs_open_shop(self):
        self.p.inventory.add_gold(100)
        self.send(op="buy", item="pocao_vida", qty=1)
        self.send(op="talk", id=self.npc.id)  # conversando, mas a loja ainda não abriu
        self.send(op="buy", item="pocao_vida", qty=1)
        self.assertEqual(self.p.inventory.gold, 100)

    def test_sell(self):
        self.p.inventory.add(CONTENT.items["couro_grosso"], 5)
        slot = next(i for i, s in enumerate(self.p.inventory.slots) if s)
        self.open_shop()
        self.send(op="sell", slot=slot, qty=2)
        self.assertEqual(self.p.inventory.count("couro_grosso"), 3)
        self.assertEqual(self.p.inventory.gold, 4)
        self.send(op="sell", slot=slot, qty=99)  # vende só o que tem
        self.assertEqual(self.p.inventory.count("couro_grosso"), 0)
        self.assertEqual(self.p.inventory.gold, 10)

    def test_sell_refused(self):
        self.p.inventory.add(CONTENT.items["espada_curta"], 1)
        self.open_shop()
        msgs = self.send(op="sell", slot=0, qty=1)
        self.assertEqual(self.p.inventory.count("espada_curta"), 1)
        self.assertIn("não compra", self.last("sys", msgs)["text"])
        for slot in (5, 99, -1, "0", None):
            self.send(op="sell", slot=slot, qty=1)
        self.assertEqual(self.p.inventory.gold, 0)

    def test_shop_closes_when_walking_away(self):
        self.open_shop()
        self.p.inventory.add_gold(100)
        self.p.x = self.npc.x - C.NPC_LEAVE_RANGE - 5
        self.send(op="buy", item="pocao_vida", qty=1)
        self.assertEqual(self.p.inventory.gold, 100)

    def test_reload_closes_conversations(self):
        self.open_shop()
        self.world.reload(self.world.content, self.world.maps)
        msgs = [m for t, m in self.world.drain_outbox() if t == self.p.id]
        self.last("npc_close", msgs)
        self.assertEqual(len(self.world.npcs.entities), 1)


class NpcContentTests(unittest.TestCase):
    def assert_error(self, npcs=NPCS_YAML, shops=SHOPS_YAML, *fragments):
        with self.assertRaises(ContentError) as ctx:
            make_content(npcs, shops)
        for fragment in fragments:
            self.assertIn(fragment, str(ctx.exception))

    def test_real_content(self):
        content = Content.load(C.DATA_DIR)
        self.assertTrue(content.npcs)
        self.assertTrue(content.shops)
        _, maps = load_game_data()
        placed = {npc_id for m in maps.values() for npc_id, _, _ in m.npc_spawns}
        self.assertEqual(placed, set(content.npcs))  # todo NPC de exemplo está em algum mapa

    def test_files_are_optional(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            for path in C.DATA_DIR.glob("*.yaml"):
                if path.name not in ("npcs.yaml", "shops.yaml"):
                    shutil.copy(path, tmp / path.name)
            content = Content.load(tmp)
            self.assertEqual((content.npcs, content.shops), ({}, {}))
        finally:
            shutil.rmtree(tmp)

    def test_unknown_shop(self):
        self.assert_error(NPCS_YAML.replace("shop: loja", "shop: outra"), SHOPS_YAML, "vendedor", "outra")

    def test_unknown_goto(self):
        self.assert_error(NPCS_YAML.replace("goto: depois\n        - text: Ricos", "goto: sumiu\n        - text: Ricos"),
                          SHOPS_YAML, "goto 'sumiu'")

    def test_goto_and_shop_together(self):
        self.assert_error(NPCS_YAML.replace("shop: loja", "shop: loja\n          goto: depois"), SHOPS_YAML,
                          "goto OU shop")

    def test_bad_condition_and_action(self):
        self.assert_error(NPCS_YAML.replace("race: desperto", "killer: enemy"), SHOPS_YAML,
                          "killer", "diálogo")
        self.assert_error(NPCS_YAML.replace("race: desperto", "chance: 0.5"), SHOPS_YAML, "chance")
        self.assert_error(NPCS_YAML.replace("set_flag: ganhou", "voar: alto"), SHOPS_YAML, "voar")
        self.assert_error(NPCS_YAML.replace("id: pocao_vida, qty: 2", "id: pocao_magica"), SHOPS_YAML,
                          "pocao_magica")

    def test_shop_validation(self):
        self.assert_error(NPCS_YAML, SHOPS_YAML.replace("- pocao_vida\n", "- pocao_magica\n"), "pocao_magica")
        self.assert_error(NPCS_YAML, SHOPS_YAML.replace("[material, pocao_vida]", "[armas]"), "armas")
        self.assert_error(NPCS_YAML, SHOPS_YAML + "  buy_rate: 1.5\n", "buy_rate")
        self.assert_error(NPCS_YAML, SHOPS_YAML + "\nvazia:\n  name: Vazia\n", "vazia", "nada")

    def test_shop_cannot_buy_above_another_sell_price(self):
        shops = SHOPS_YAML + "\ngenerosa:\n  name: Generosa\n  buys: [all]\n  buy_rate: 1\n"
        # a Generosa pagaria 30 pela espada que a outra loja vende por... 50: ok.
        make_content(NPCS_YAML, shops)
        cheap = shops.replace("{ item: espada_curta, price: 50 }", "{ item: espada_curta, price: 20 }")
        self.assert_error(NPCS_YAML, cheap, "generosa", "espada_curta", "20")


class NpcMapTests(unittest.TestCase):
    def test_text_map_validation(self):
        for npcs, fragment in (
            ([{"id": "vendedor", "at": [0, 0]}], "bloqueado"),
            ([{"id": "vendedor", "at": [50, 2]}], "fora do mapa"),
            ([{"id": "vendedor"}], "use"),
            ("vendedor", "lista"),
        ):
            with self.assertRaises(ValueError) as ctx:
                parse_map(MAP, "teste", CONTENT.enemy_by_mark, zones={"npcs": npcs})
            self.assertIn(fragment, str(ctx.exception))

    def test_unknown_npc_in_map(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "teste.txt").write_text(MAP, encoding="utf-8")
            (tmp / "teste.yaml").write_text(yaml.safe_dump({"npcs": [{"id": "ninguem", "at": [3, 2]}]}),
                                            encoding="utf-8")
            load_maps(tmp)  # sem a lista de NPCs, não confere
            with self.assertRaises(ValueError) as ctx:
                load_maps(tmp, npc_ids=CONTENT.npcs)
            self.assertIn("ninguem", str(ctx.exception))
        finally:
            shutil.rmtree(tmp)

    def test_tiled_round_trip_keeps_npcs(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            for path in C.MAPS_DIR.iterdir():
                if path.is_file():
                    shutil.copy(path, tmp / path.name)
            before = load_maps(tmp, CONTENT.enemy_by_mark, enemy_ids=CONTENT.enemies)["inicio"].npc_spawns
            self.assertTrue(before)
            to_tiled("inicio", tmp, CONTENT)
            after = load_maps(tmp, CONTENT.enemy_by_mark, enemy_ids=CONTENT.enemies, npc_ids=CONTENT.npcs)
            self.assertEqual(after["inicio"].npc_spawns, before)

            doc = json.loads((tmp / "inicio.tmj").read_text(encoding="utf-8"))
            points = next(layer for layer in doc["layers"] if layer["name"] == "pontos")["objects"]
            npc_point = next(o for o in points if any(p["value"] == "npc" for p in o["properties"]))
            npc_point["properties"] = [{"name": "tipo", "type": "string", "value": "npc"}]  # sem o id
            (tmp / "inicio.tmj").write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaises(TiledError) as ctx:
                parse_tmj(tmp / "inicio.tmj")
            self.assertIn("npc", str(ctx.exception))
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
