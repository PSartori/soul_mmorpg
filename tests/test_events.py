"""Testes dos eventos em ondas (invasões): ondas, vitória, derrota, recompensas, agenda e equipe."""
import dataclasses
import random
import unittest

import yaml

from server import config as C
from server.content import ContentError, parse_events, parse_rules
from server.db import Database
from server.tilemap import parse_map
from server.world import World
from tests.test_world import CONTENT, TEST_MAP, TEST_ZONES, run, tile_center

DT = 1.0 / C.TICK_RATE

#            0123456789
# TEST_MAP:  ##########
#            #::S:....#   linha 1
#            #:::::...#   linha 2
#            #........#   linha 3
#            #....E...#   linha 4
#            ##########
EVENTS = """
teste_invasao:
  name: Invasão de teste
  map: teste
  text: "Começou!"
  spawn: [6, 3, 8, 4]
  march: [5, 3]
  time: 120
  waves:
    - { espectro: 2 }
    - { espectro: 1, bruto: 1, text: "Agora com bruto." }
  rewards:
    - give_item: { id: pocao_energia, qty: 2 }
    - message: "Obrigado, {name}."
  min_damage: 5
  win_text: "Venceram!"
  lose_text: "Perderam."
"""


def events_content(text: str = EVENTS, rules: str = ""):
    content = dataclasses.replace(CONTENT, events=parse_events(yaml.safe_load(text), CONTENT))
    if rules:  # regras conferidas contra os eventos de teste
        content = dataclasses.replace(content, rules=parse_rules(yaml.safe_load(rules), content))
    return content


def world_with(content, db=None):
    tilemap = parse_map(TEST_MAP, "teste", content.enemy_by_mark, zones=TEST_ZONES)
    return World(tilemap, db or Database(":memory:"), content, rng=random.Random(1))


class Base(unittest.TestCase):
    def setUp(self):
        self.world = world_with(events_content())
        self.world.enemies.clear()
        self.a = self.login("Ana")
        self.b = self.login("Beto")
        self.world.drain_outbox()

    def login(self, name, world=None):
        p, err = (world or self.world).login(name, "1234", "humano")
        self.assertIsNone(err, err)
        p.x, p.y = tile_center(2, 1)  # zona segura: os invasores não atacam
        return p

    def invaders(self):
        return [e for e in self.world.enemies.values() if e.event_id == "teste_invasao" and e.alive]

    def kill_all(self, by):
        for e in self.invaders():
            self.world._damage(e, e.hp + 1, by)

    def sys_texts(self, p=None):
        return [m["text"] for t, m in self.world.drain_outbox()
                if m["t"] in ("sys", "announce") and (p is None or t in (p.id, None))]


class WaveTests(Base):
    def test_start_spawns_first_wave_marching(self):
        ok, text = self.world.events.start("teste_invasao", by="Equipe")
        self.assertTrue(ok, text)
        invaders = self.invaders()
        self.assertEqual(len(invaders), 2)
        self.assertTrue(all(e.defn.id == "espectro" for e in invaders))
        self.assertTrue(all((e.home_x, e.home_y) == tile_center(5, 3) for e in invaders))
        self.assertTrue(all(6 * C.TILE_SIZE <= e.x <= 9 * C.TILE_SIZE for e in invaders))
        texts = self.sys_texts()
        self.assertIn("Começou!", texts)
        self.assertIn("Invasão de teste: onda 1/2 — 2 inimigos.", texts)
        self.assertEqual(invaders[0].snapshot()["inv"], 1)

    def test_cannot_start_twice_or_unknown(self):
        self.world.events.start("teste_invasao")
        self.assertEqual(self.world.events.start("teste_invasao"), (False, "Invasão de teste já está acontecendo."))
        self.assertFalse(self.world.events.start("nao_existe")[0])

    def test_waves_then_victory_with_rewards(self):
        self.world.events.start("teste_invasao")
        self.kill_all(self.a)
        run(self.world, DT)
        self.assertEqual(self.invaders(), [])
        self.assertTrue(any("onda 1/2 derrotada" in t for t in self.sys_texts()))
        run(self.world, C.EVENT_WAVE_PAUSE + DT)
        wave2 = sorted(e.defn.id for e in self.invaders())
        self.assertEqual(wave2, ["bruto", "espectro"])
        self.assertTrue(any("Agora com bruto." in t for t in self.sys_texts()))
        self.world._damage(self.invaders()[0], 3, self.b)  # Beto: só 3 de dano (mínimo 5)
        self.kill_all(self.a)
        run(self.world, DT)
        self.assertNotIn("teste_invasao", self.world.events.running)
        self.assertEqual(self.a.inventory.count("pocao_energia"), 2)
        self.assertEqual(self.b.inventory.count("pocao_energia"), 0)  # não chegou ao mínimo
        texts = self.sys_texts()
        self.assertIn("Venceram!", texts)
        self.assertIn("Obrigado, Ana.", texts)

    def test_invaders_do_not_respawn(self):
        self.world.events.start("teste_invasao")
        self.kill_all(self.a)
        run(self.world, C.SUMMON_CORPSE_SECONDS + 1)
        self.assertFalse(any(e.event_id for e in self.world.enemies.values() if e.alive))

    def test_timeout_is_defeat_and_invaders_leave(self):
        self.world.events.start("teste_invasao")
        self.world._damage(self.invaders()[0], 10, self.a)
        run(self.world, 121)
        self.assertNotIn("teste_invasao", self.world.events.running)
        self.assertFalse(any(e.event_id for e in self.world.enemies.values()))
        self.assertEqual(self.a.inventory.count("pocao_energia"), 0)
        self.assertIn("Perderam.", self.sys_texts())

    def test_staff_stop_removes_without_rewards(self):
        self.world.events.start("teste_invasao")
        self.world._damage(self.invaders()[0], 10, self.a)
        ok, _ = self.world.events.stop("teste_invasao")
        self.assertTrue(ok)
        self.assertFalse(any(e.event_id for e in self.world.enemies.values()))
        self.assertIn("O evento Invasão de teste foi encerrado pela equipe.", self.sys_texts())
        self.assertFalse(self.world.events.stop("teste_invasao")[0])

    def test_invaders_ignore_leash_and_see_farther(self):
        self.world.events.start("teste_invasao")
        e = self.invaders()[0]
        e.x, e.y = tile_center(8, 4)
        e.home_x, e.home_y = -5000, -5000  # muito longe de casa: um inimigo comum desistiria
        self.a.x, self.a.y = tile_center(8, 3)  # fora da zona segura, ao lado
        self.assertIs(self.world._enemy_target(e), self.a)


class RulesAndScheduleTests(Base):
    def test_event_end_rule_gets_result_and_damage(self):
        rules = """
recompensa_extra:
  when: event_end
  if: { event: teste_invasao, result: lose, damage: { min: 10 } }
  do:
    - message: "Lutou bem, mesmo perdendo."
"""
        self.world = world_with(events_content(rules=rules))
        self.world.enemies.clear()
        a = self.login("Ana")
        self.world.events.start("teste_invasao")
        self.world._damage(self.invaders()[0], 12, a)
        self.world.drain_outbox()
        run(self.world, 121)
        self.assertIn("Lutou bem, mesmo perdendo.", self.sys_texts(a))

    def test_schedule_starts_with_enough_players(self):
        text = EVENTS.replace("  min_damage: 5", "  min_damage: 5\n  every: 1\n  min_players: 3")
        self.world = world_with(events_content(text))
        self.world.enemies.clear()
        self.login("Ana")
        self.login("Beto")
        run(self.world, 61)
        self.assertNotIn("teste_invasao", self.world.events.running)  # só 2 online
        self.login("Caio")
        run(self.world, 61)
        self.assertIn("teste_invasao", self.world.events.running)

    def test_reload_ends_running_events(self):
        self.world.events.start("teste_invasao")
        self.world.events.reset()
        self.assertEqual(self.world.events.running, {})

    def test_panel_goes_to_client_only_when_changed(self):
        def you():
            run(self.world, DT)
            return [m for t, m in self.world.drain_outbox() if t == self.a.id and m["t"] == "snap"][-1]["you"]

        self.assertEqual(you()["ev"], [])
        self.assertNotIn("ev", you())
        self.world.events.start("teste_invasao")
        info = you()["ev"][0]
        self.assertEqual((info["name"], info["wave"], info["waves"], info["left"], info["here"]),
                         ("Invasão de teste", 1, 2, 2, True))
        self.assertNotIn("ev", you())  # mesmo segundo: nada mudou


class AdminTests(Base):
    def staff(self, p, role):
        self.world.db.set_role(p.name, role)
        self.world.admin.load_player(p)

    def admin(self, p, **data):
        self.world.handle_message(p, {"t": "admin", **data})
        return [m for t, m in self.world.drain_outbox() if t == p.id and m["t"] in ("admin_result", "admin_data")]

    def test_gm_starts_and_stops(self):
        self.staff(self.a, "gm")
        out = self.admin(self.a, op="refresh")
        self.assertEqual(out[-1]["events"][0]["id"], "teste_invasao")
        out = self.admin(self.a, op="event_start", id="teste_invasao")
        self.assertTrue(next(m for m in out if m["t"] == "admin_result")["ok"])
        self.assertTrue(next(m for m in out if m["t"] == "admin_data")["events"][0]["running"])
        self.admin(self.a, op="event_stop", id="teste_invasao")
        self.assertEqual(self.world.events.running, {})
        self.assertEqual(self.world.db.recent_log(1)[0][1:4], ("Ana", "encerrou o evento", "teste_invasao"))

    def test_moderator_cannot(self):
        self.staff(self.a, "moderador")
        out = self.admin(self.a, op="event_start", id="teste_invasao")
        self.assertFalse(out[0]["ok"])
        self.assertNotIn("events", self.admin(self.a, op="refresh")[-1])
        self.assertEqual(self.world.events.running, {})


class ContentTests(unittest.TestCase):
    def assert_error(self, text, fragment):
        with self.assertRaises(ContentError) as ctx:
            parse_events(yaml.safe_load(text), CONTENT)
        self.assertIn(fragment, str(ctx.exception))

    def test_bad_definitions(self):
        self.assert_error(EVENTS.replace("espectro: 2", "fantasma: 2"), "fantasma")
        self.assert_error(EVENTS.replace("espectro: 2", "espectro: 0"), "quantidade")
        self.assert_error(EVENTS.replace("give_item: { id: pocao_energia, qty: 2 }", "voar: 25"), "voar")
        self.assert_error(EVENTS.replace("give_item: { id: pocao_energia, qty: 2 }", "give_gold: 25"), "give_gold")
        self.assert_error(EVENTS.replace("spawn: [6, 3, 8, 4]", "spawn: [6, 3]"), "spawn")
        self.assert_error(EVENTS.replace("  waves:\n    - { espectro: 2 }\n    - { espectro: 1, bruto: 1, "
                                         "text: \"Agora com bruto.\" }\n", "  waves: []\n"), "pelo menos uma onda")

    def test_maps_checked(self):
        tilemap = parse_map(TEST_MAP, "teste", CONTENT.enemy_by_mark, zones=TEST_ZONES)
        for change, fragment in ((("map: teste", "map: longe"), "não existe"),
                                 (("spawn: [6, 3, 8, 4]", "spawn: [6, 3, 80, 4]"), "fora do mapa"),
                                 (("spawn: [6, 3, 8, 4]", "spawn: [1, 1, 2, 2]"), "nenhum tile livre"),
                                 (("march: [5, 3]", "march: [0, 0]"), "march")):
            content = events_content(EVENTS.replace(*change))
            with self.assertRaises(ContentError) as ctx:
                content.check_maps({"teste": tilemap})
            self.assertIn(fragment, str(ctx.exception))

    def test_real_events_load(self):
        from server.gamedata import load_game_data
        content, _ = load_game_data()
        self.assertIn("invasao_campos", content.events)


if __name__ == "__main__":
    unittest.main()
