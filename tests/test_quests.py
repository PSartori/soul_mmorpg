"""Testes de missões: oferta, progresso, entrega, repetição e save."""
import random
import unittest

import yaml

from server import config as C
from server.content import ContentError
from server.db import Database
from server.saves import migrate
from server.tilemap import parse_map
from server.world import World
from tests.test_npcs import make_content

DT = 1.0 / C.TICK_RATE

#           0123456789
MAP = """
##########
#S:......#
#........#
#........#
##########
"""
NPCS = """
anciao:
  name: Ancião
  dialogue:
    inicio:
      text: Olá.
      options:
        - text: Tchau
guarda:
  name: Guarda
  dialogue:
    inicio:
      text: Alto lá.
"""
QUESTS = """
caca:
  name: Caça
  giver: anciao
  description: "Derrote dois espectros, {name}."
  objectives:
    - { kill: espectro, count: 2 }
    - { collect: couro_grosso, count: 2 }
  rewards:
    - give_gold: 30
    - give_item: { id: pocao_vida, qty: 2 }
  complete_text: "Bom trabalho, {name}."

recado:
  name: Recado
  giver: anciao
  turn_in: guarda
  if: { quest_done: caca }
  objectives:
    - { talk: guarda }
    - { reach: teste, area: [7, 1, 8, 3], label: Ir ao canto leste }
  rewards:
    - set_flag: recado_feito

diaria:
  name: Diária
  giver: anciao
  repeatable: true
  cooldown: 60
  objectives:
    - { kill: espectro }
  rewards:
    - give_gold: 5

marca:
  name: Marca
  objectives:
    - { flag: achou, label: Achar a marca }
  rewards:
    - give_gold: 7
"""


class Base(unittest.TestCase):
    quests = QUESTS

    def setUp(self):
        self.now = 1000.0
        self.content = make_content(npcs=NPCS, quests=self.quests)
        tilemap = parse_map(MAP, "teste", self.content.enemy_by_mark, zones={"npcs": [
            {"id": "anciao", "at": [3, 2]}, {"id": "guarda", "at": [5, 2]}]})
        self.world = World(tilemap, Database(":memory:"), self.content, rng=random.Random(1),
                           clock=lambda: self.now)
        self.p, _ = self.world.login("Teste", "1234", "humano")
        self.npcs = {n.defn.id: n for n in self.world.npcs.entities.values()}
        self.p.x, self.p.y = self.npcs["anciao"].x + 20, self.npcs["anciao"].y
        self.world.drain_outbox()

    def send(self, **msg):
        self.world.handle_message(self.p, {"t": "npc", **msg})
        return [m for target, m in self.world.drain_outbox() if target == self.p.id]

    def dialog(self, msgs):
        return [m for m in msgs if m["t"] == "dialog"][-1]

    def talk(self, npc="anciao"):
        return self.dialog(self.send(op="talk", id=self.npcs[npc].id))

    def options(self, dialog):
        return {o["text"]: o["i"] for o in dialog["options"]}

    def accept(self, quest_id):
        self.talk()
        offer = self.dialog(self.send(op="choose", i=f"missao:{quest_id}"))
        self.assertEqual(offer["quest"], quest_id)
        self.send(op="choose", i=f"aceitar:{quest_id}")
        self.assertIn(quest_id, self.p.quests)

    def sys_texts(self):
        return [m["text"] for t, m in self.world.drain_outbox() if t == self.p.id and m["t"] == "sys"]

    def tick(self):
        self.world.step(DT)


class QuestFlowTests(Base):
    def test_offer_shows_in_dialog_with_conditions(self):
        opts = self.options(self.talk())
        self.assertIn("Missão: Caça", opts)
        self.assertIn("Missão: Diária", opts)
        self.assertNotIn("Missão: Recado", opts)  # precisa de caca concluída
        self.assertNotIn("Missão: Marca", opts)  # sem giver: só por start_quest
        self.assertIn("Tchau", opts)  # as opções do arquivo continuam lá

    def test_offer_text_and_accept(self):
        self.talk()
        offer = self.dialog(self.send(op="choose", i="missao:caca"))
        self.assertEqual(offer["text"], "Derrote dois espectros, Teste.")
        self.assertEqual([o["i"] for o in offer["options"]], ["aceitar:caca", "voltar"])
        back = self.dialog(self.send(op="choose", i="aceitar:caca"))
        self.assertEqual(back["text"], "Olá.")  # volta ao começo da conversa
        self.assertNotIn("Missão: Caça", self.options(back))
        self.assertEqual(self.p.quests["caca"], [0, 0])

    def test_cannot_accept_without_offer_or_turn_in_unready(self):
        self.talk()
        self.send(op="choose", i="aceitar:caca")  # pulou a oferta
        self.assertNotIn("caca", self.p.quests)
        self.send(op="choose", i="missao:marca")  # o ancião não oferece esta
        self.send(op="choose", i="missao:inexistente")
        self.send(op="choose", i="entregar:caca")
        self.assertEqual(self.p.quests, {})

    def test_progress_ready_and_turn_in(self):
        self.accept("caca")
        self.world.quests.on_kill(self.p, "espectro")
        self.world.quests.on_kill(self.p, "bruto")  # não conta
        self.assertEqual(self.p.quests["caca"][0], 1)
        self.world.quests.on_kill(self.p, "espectro")
        self.world.quests.on_kill(self.p, "espectro")  # não passa do pedido
        self.assertEqual(self.p.quests["caca"][0], 2)
        self.tick()
        self.assertFalse(self.world.quests.is_ready(self.p, self.content.quests["caca"]))  # falta o couro
        self.p.inventory.add(self.content.items["couro_grosso"], 3)
        self.sys_texts()
        self.tick()
        self.assertTrue(any("pronta" in t for t in self.sys_texts()))
        self.tick()
        self.assertFalse(any("pronta" in t for t in self.sys_texts()))  # avisa uma vez só
        self.assertIn("Entregar: Caça", self.options(self.talk()))

        done = self.dialog(self.send(op="choose", i="entregar:caca"))
        self.assertEqual(done["text"], "Bom trabalho, Teste.")
        self.assertNotIn("caca", self.p.quests)
        self.assertEqual(self.p.quests_done["caca"][0], 1)
        self.assertEqual(self.p.inventory.count("couro_grosso"), 1)  # entregou 2 dos 3
        self.assertEqual(self.p.inventory.gold, 30)
        self.assertEqual(self.p.inventory.count("pocao_vida"), 2)
        self.assertNotIn("Missão: Caça", self.options(self.talk()))  # não repetível

    def test_selling_items_makes_quest_unready(self):
        self.accept("caca")
        self.p.quests["caca"][0] = 2
        self.p.inventory.add(self.content.items["couro_grosso"], 2)
        self.tick()
        self.p.inventory.remove_item("couro_grosso", 1)
        self.tick()
        self.assertNotIn("caca", self.p.quests_ready)
        self.assertNotIn("Entregar: Caça", self.options(self.talk()))

    def test_talk_reach_and_turn_in_to_other_npc(self):
        self.p.quests_done["caca"] = [1, 0.0]
        self.accept("recado")
        self.p.x, self.p.y = self.npcs["guarda"].x - 20, self.npcs["guarda"].y
        self.assertNotIn("Entregar: Recado", self.options(self.talk("guarda")))  # falar conta, mas falta ir ao canto
        self.assertEqual(self.p.quests["recado"], [1, 0])
        self.p.x, self.p.y = (7.5 * C.TILE_SIZE, 2.5 * C.TILE_SIZE)
        self.tick()
        self.assertEqual(self.p.quests["recado"], [1, 1])
        self.p.x, self.p.y = self.npcs["guarda"].x - 20, self.npcs["guarda"].y
        self.send(op="choose", i="entregar:recado")
        self.assertIn("recado_feito", self.p.flags)

    def test_repeatable_with_cooldown(self):
        self.accept("diaria")
        self.world.quests.on_kill(self.p, "espectro")
        self.tick()
        self.talk()
        self.send(op="choose", i="entregar:diaria")
        self.assertEqual(self.p.inventory.gold, 5)
        self.assertNotIn("Missão: Diária", self.options(self.talk()))
        self.now += 61
        self.accept("diaria")
        self.world.quests.on_kill(self.p, "espectro")
        self.tick()
        self.talk()
        self.send(op="choose", i="entregar:diaria")
        self.assertEqual(self.p.quests_done["diaria"][0], 2)

    def test_auto_complete_and_start_quest_action(self):
        self.world._do_action(self.p, "start_quest", "marca", {}, 0)
        self.assertIn("marca", self.p.quests)
        self.world._do_action(self.p, "start_quest", "marca", {}, 0)  # já em andamento: nada
        self.p.flags.add("achou")
        self.tick()
        self.assertNotIn("marca", self.p.quests)  # sem turn_in: conclui sozinha
        self.assertEqual(self.p.inventory.gold, 7)

    def test_abandon_and_limit(self):
        self.accept("caca")
        self.world.handle_message(self.p, {"t": "quest", "op": "abandon", "id": "caca"})
        self.assertNotIn("caca", self.p.quests)
        self.world.handle_message(self.p, {"t": "quest", "op": "abandon", "id": 5})  # ignorado
        for i in range(C.MAX_ACTIVE_QUESTS):
            self.p.quests[f"x{i}"] = []
        self.assertFalse(self.world.quests.start(self.p, "caca"))

    def test_quest_complete_rule(self):
        from server.content import parse_rules
        import dataclasses
        rules = parse_rules(yaml.safe_load("""
festa:
  when: quest_complete
  if: { quest: marca }
  do: [ { give_gold: 100 } ]
"""), self.content)
        self.world.content = dataclasses.replace(self.content, rules=rules)
        self.world.quests.start(self.p, "marca")
        self.p.flags.add("achou")
        self.tick()
        self.assertEqual(self.p.inventory.gold, 107)

    def test_snapshot_sends_diary_only_when_changed_and_marks(self):
        self.tick()
        snaps = [m for t, m in self.world.drain_outbox() if t == self.p.id and m["t"] == "snap"]
        info = snaps[-1]["you"]["q"]
        self.assertEqual(info["marks"], {self.npcs["anciao"].id: "!"})
        self.tick()
        snaps = [m for t, m in self.world.drain_outbox() if t == self.p.id and m["t"] == "snap"]
        self.assertNotIn("q", snaps[-1]["you"])
        self.accept("caca")
        self.world.quests.on_kill(self.p, "espectro")
        self.tick()
        info = [m for t, m in self.world.drain_outbox() if t == self.p.id and m["t"] == "snap"][-1]["you"]["q"]
        self.assertEqual(info["active"], [{"id": "caca", "p": [1, 0], "ready": False}])

    def test_welcome_has_quest_defs(self):
        info = self.world.welcome_payload(self.p)["quests"]["caca"]
        self.assertEqual(info["giver"], "Ancião")
        self.assertEqual([o["label"] for o in info["objectives"]], ["Derrotar Espectro", "Conseguir Couro grosso"])
        self.assertEqual(info["rewards"], ["30 moedas", "2× Poção de vida"])


class QuestSaveTests(Base):
    def test_save_round_trip(self):
        self.accept("caca")
        self.world.quests.on_kill(self.p, "espectro")
        self.p.quests_done["diaria"] = [3, 500.0]
        self.world.logout(self.p)
        p, _ = self.world.login("Teste", "1234")
        self.assertEqual(p.quests, {"caca": [1, 0]})
        self.assertEqual(p.quests_done, {"diaria": [3, 500.0]})

    def test_migration_v4(self):
        self.assertEqual(migrate({"v": 4})["quests"], {"active": {}, "done": {}})

    def test_removed_quest_and_changed_objectives(self):
        self.p.quests = {"sumiu": [1], "caca": [5]}
        self.p.quests_done = {"sumiu": [1, 0.0]}
        self.world.reload(self.content, self.world.maps)
        self.assertEqual(self.p.quests, {"caca": [2, 0]})  # limitado ao pedido e com o objetivo novo
        self.assertEqual(self.p.quests_done, {})

    def test_corrupt_save_is_ignored(self):
        self.world.logout(self.p)
        self.world.db.save_character("Teste", {"v": 5, "race": "humano", "class": "aprendiz", "flags": [],
                                              "quests": {"active": {"caca": ["x"], "diaria": [0]}, "done": "?"}})
        p, _ = self.world.login("Teste", "1234")
        self.assertEqual(p.quests, {"diaria": [0]})
        self.assertEqual(p.quests_done, {})


class QuestContentTests(unittest.TestCase):
    def assert_error(self, quests, *fragments, npcs=NPCS):
        with self.assertRaises(ContentError) as ctx:
            make_content(npcs=npcs, quests=quests)
        for fragment in fragments:
            self.assertIn(fragment, str(ctx.exception))

    def test_validation(self):
        base = "q:\n  name: Q\n  objectives:\n"
        self.assert_error(base + "    - { kill: dragao }\n", "dragao")
        self.assert_error(base + "    - { kill: espectro, collect: couro_grosso }\n", "exatamente um tipo")
        self.assert_error(base + "    - { reach: teste }\n", "label")
        self.assert_error(base + "    - { talk: ninguem }\n", "ninguem")
        self.assert_error("q:\n  name: Q\n  objectives: []\n", "pelo menos um")
        self.assert_error(base + "    - { kill: espectro }\n  giver: ninguem\n", "giver", "ninguem")
        self.assert_error(base + "    - { kill: espectro }\n  cooldown: 10\n", "repeatable")
        self.assert_error(base + "    - { kill: espectro }\n  if: { quest_done: outra }\n", "outra")
        self.assert_error(base + "    - { kill: espectro }\n  rewards: [ { start_quest: outra } ]\n", "outra")
        self.assert_error(base + "    - { kill: espectro, count: 0 }\n", "count")

    def test_reach_checked_against_maps(self):
        content = make_content(npcs=NPCS, quests="q:\n  name: Q\n  objectives:\n"
                                                  "    - { reach: longe, label: Ir }\n")
        with self.assertRaises(ContentError):
            content.check_maps({"teste": parse_map(MAP, "teste")})
        content = make_content(npcs=NPCS, quests="q:\n  name: Q\n  objectives:\n"
                                                  "    - { reach: teste, area: [0, 0, 50, 1], label: Ir }\n")
        with self.assertRaises(ContentError) as ctx:
            content.check_maps({"teste": parse_map(MAP, "teste")})
        self.assertIn("fora do mapa", str(ctx.exception))

    def test_real_quests_load(self):
        from server.gamedata import load_game_data
        content, _ = load_game_data()
        self.assertTrue(content.quests)
        for quest in content.quests.values():
            self.assertTrue(quest.giver, f"{quest.id} de exemplo deveria ter quem a oferece")


if __name__ == "__main__":
    unittest.main()
