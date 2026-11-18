"""Inimigos por nível: elites, chefes com fases, invocados e corpos maiores."""
import dataclasses
import random
import unittest

from server import config as C
from server.content import ContentError
from server.db import Database
from server.gamedata import load_game_data
from server.tilemap import parse_map
from server.world import World
from tests import test_world
from tests.test_world import CONTENT, DT, tile_center

#           0123456789012345
MAP = """
################
#S:............#
#..............#
#.......G......#
#..............#
#..............#
################
"""


class Base(unittest.TestCase):
    def setUp(self):
        tilemap = parse_map(MAP, "teste", CONTENT.enemy_by_mark)
        self.world = World(tilemap, Database(":memory:"), CONTENT, rng=random.Random(3))
        self.boss = next(iter(self.world.enemies.values()))
        self.p, _ = self.world.login("Heroi", "1234", "humano")
        self.p.hp = 10_000
        self.world.drain_outbox()

    def hit_boss_to(self, fraction):
        self.world._damage(self.boss, self.boss.hp - fraction * self.boss.max_hp, self.p)

    def texts(self):
        return [m["text"] for t, m in self.world.drain_outbox() if m["t"] == "sys" and t in (None, self.p.id)]


class BossTests(Base):
    def test_boss_is_big_and_never_elite(self):
        self.assertEqual(self.boss.defn.id, "guardiao")
        self.assertFalse(self.boss.elite)
        self.assertAlmostEqual(self.boss.half, C.ENTITY_HALF * 1.8)
        snap = self.boss.snapshot()
        self.assertEqual((snap["lv"], snap["boss"], snap["ph"]), (30, 1, 0))
        self.assertEqual(snap["h"], 18.0)

    def test_phases_change_skills_summon_and_enrage(self):
        self.p.x, self.p.y = self.boss.x - 60, self.boss.y  # perto: recebe os avisos
        self.boss.target_id = self.p.id
        self.hit_boss_to(0.7)
        self.assertEqual(self.boss.phase, 0)
        self.hit_boss_to(0.55)
        self.assertEqual(self.boss.phase, 1)
        self.assertIn("tremor", self.boss.skills)
        minions = [e for e in self.world.enemies.values() if e.summoner_id == self.boss.id]
        self.assertEqual(len(minions), 3)
        self.assertTrue(all(m.target_id == self.p.id for m in minions))
        self.assertTrue(any("ganham vida" in t for t in self.texts()))
        power = self.boss.power
        self.hit_boss_to(0.2)
        self.assertEqual(self.boss.phase, 2)
        self.assertIn("furia", self.boss.effects)
        self.assertEqual(self.boss.power, power)  # a fúria vem do efeito (modificador), não da fase

    def test_crossing_two_limits_at_once(self):
        self.hit_boss_to(0.1)
        self.assertEqual(self.boss.phase, 2)

    def test_death_despawns_summons_and_announces(self):
        self.hit_boss_to(0.5)
        self.world._damage(self.boss, 1e9, self.p)
        self.assertFalse(self.boss.alive)
        self.assertFalse([e for e in self.world.enemies.values() if e.summoner_id == self.boss.id])
        self.assertTrue(any("Guardião das Ruínas foi derrotado por Heroi" in t for t in self.texts()))

    def test_reset_when_leashed(self):
        self.hit_boss_to(0.5)
        self.boss.x += self.boss.defn.leash + 10  # arrastado para longe de casa
        self.world.step(DT)
        self.assertEqual(self.boss.phase, 0)
        self.assertEqual(self.boss.hp, self.boss.max_hp)
        self.assertFalse([e for e in self.world.enemies.values() if e.summoner_id == self.boss.id])

    def test_reset_when_nobody_fights(self):
        self.p.x, self.p.y = self.boss.x - 60, self.boss.y
        self.hit_boss_to(0.5)
        self.world.logout(self.p)  # quem lutava saiu (ou morreu)
        for _ in range(round((C.BOSS_RESET_SECONDS + 0.5) / DT)):
            self.world.step(DT)
        self.assertEqual((self.boss.phase, self.boss.hp), (0, self.boss.max_hp))
        self.assertFalse([e for e in self.world.enemies.values() if e.summoner_id == self.boss.id])

    def test_summons_do_not_respawn(self):
        self.hit_boss_to(0.5)
        minion = next(e for e in self.world.enemies.values() if e.summoner_id == self.boss.id)
        self.world._damage(minion, 1e9, self.p)
        for _ in range(round((C.SUMMON_CORPSE_SECONDS + 0.2) / DT)):
            self.world.step(DT)
        self.assertNotIn(minion.id, self.world.enemies)

    def test_big_body_reaches_player(self):
        """Com colisão, o corpo grande não chega a 26 px do centro do jogador; o golpe parte da borda."""
        self.p.x, self.p.y = self.boss.x - 70, self.boss.y
        hp = self.p.hp
        for _ in range(round(4 / DT)):
            self.world.step(DT)
        self.assertLess(self.p.hp, hp)


class EliteTests(Base):
    def setUp(self):
        super().setUp()
        defn = CONTENT.enemies["bruto"]
        self.always = dataclasses.replace(defn, elite_chance=1.0)

    def test_elite_stats(self):
        e = self.world._new_enemy(self.always, self.p.map_id, *tile_center(10, 4))
        self.assertTrue(e.elite)
        self.assertEqual(e.max_hp, self.always.hp * C.ELITE_HP)
        self.assertEqual(e.hp, e.max_hp)
        self.assertAlmostEqual(e.power, self.always.damage * C.ELITE_DAMAGE)
        self.assertAlmostEqual(e.half, C.ENTITY_HALF * C.ELITE_SIZE)
        self.assertEqual(e.snapshot()["el"], 1)

    def test_elite_rewards(self):
        loot = (dataclasses.replace(CONTENT.enemies["espectro"].loot[0], chance=1.0, qty_min=1, qty_max=1),)
        e = self.world._new_enemy(dataclasses.replace(self.always, loot=loot), self.p.map_id, *tile_center(10, 4))
        self.world._damage(e, 1e9, self.p)
        drops = list(self.world.drops.values())
        self.assertEqual(len(drops), C.ELITE_LOOT_ROLLS)  # saque sorteado mais vezes; moedas não caem
        self.assertTrue(all(d.item is not None for d in drops))

    def test_elite_rerolled_on_respawn(self):
        e = self.world._new_enemy(dataclasses.replace(self.always, elite_chance=0.5, respawn=0.1), self.p.map_id,
                                  *tile_center(10, 4))
        seen = set()
        for _ in range(30):
            self.world._damage(e, 1e9, self.p)
            for _ in range(5):
                self.world.step(DT)
            seen.add(e.elite)
            self.assertEqual(e.hp, e.max_hp)
        self.assertEqual(seen, {True, False})

    def test_summons_are_never_elite(self):
        e = self.world._new_enemy(self.always, self.p.map_id, *tile_center(10, 4), summoner=self.boss)
        self.assertFalse(e.elite)


class EnemyContentTests(test_world.ContentTests):
    def enemy(self, extra: str) -> None:
        self.append("enemies.yaml", "teste:\n  name: T\n  mark: Q\n  hp: 10\n  speed: 10\n  aggro_radius: 1\n"
                                    "  leash: 1\n  wander_radius: 1\n  respawn: 1\n  skills: [garra]\n" + extra)

    def test_phases_need_boss(self):
        self.enemy("  phases:\n    - { hp_below: 0.5 }\n")
        self.assert_error("teste", "boss")

    def test_phases_in_order(self):
        self.enemy("  boss: true\n  phases:\n    - { hp_below: 0.3 }\n    - { hp_below: 0.6 }\n")
        self.assert_error("teste", "ordem")

    def test_phase_limits_and_refs(self):
        self.enemy("  boss: true\n  phases:\n    - { hp_below: 1.5 }\n")
        self.assert_error("hp_below")

    def test_summon_must_exist_and_not_be_boss(self):
        self.enemy("  boss: true\n  phases:\n    - { hp_below: 0.5, summon: [ { enemy: nada } ] }\n")
        self.assert_error("nada")

    def test_boss_cannot_summon_boss(self):
        self.enemy("  boss: true\n  phases:\n    - { hp_below: 0.5, summon: [ guardiao ] }\n")
        self.assert_error("chefe")

    def test_elite_chance(self):
        self.enemy("  elite_chance: 2\n")
        self.assert_error("elite_chance")
        self.setUp()
        self.enemy("  boss: true\n  elite_chance: 0.1\n")
        self.assert_error("elite")

    def test_phase_skill_ai_type(self):
        self.enemy("  boss: true\n  phases:\n    - { hp_below: 0.5, skills: [passo] }\n")
        self.assert_error("IA")


class WorldContentTests(unittest.TestCase):
    def test_new_maps_and_levels(self):
        content, maps = load_game_data()
        self.assertTrue({"ruinas", "fenda"} <= set(maps))
        reach = {m.name: {p.to for p in m.portals} for m in maps.values()}
        self.assertIn("ruinas", reach["bosque"])
        self.assertEqual(reach["ruinas"], {"bosque", "fenda"})
        levels = sorted({e.level for e in content.enemies.values()})
        self.assertGreaterEqual(max(levels), 50)
        bosses = [e for e in content.enemies.values() if e.boss]
        self.assertEqual({b.id for b in bosses}, {"guardiao", "devorador_anciao"})
        placed = {ref for m in maps.values() for ref, _, _ in m.enemy_spawns}
        for b in bosses:
            self.assertIn(b.mark, placed)
            for phase in b.phases:
                for enemy_id, _ in phase.summon:
                    self.assertNotIn(content.enemies[enemy_id].mark, placed)  # invocados só aparecem invocados


if __name__ == "__main__":
    unittest.main()
