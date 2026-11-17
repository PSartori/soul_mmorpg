"""Colisão entre corpos: jogadores, inimigos e NPCs não entram uns nos outros."""
import random
import unittest
from unittest import mock

from server import config as C
from server.content import EffectApplication
from server.db import Database
from server.tilemap import clamp_by_solids, parse_map
from server.world import World
from tests.test_world import CONTENT, DT, move, tile_center

#           0123456789012
MAP = """
#############
#S::........#
#...........#
#...........#
#...........#
#....E.E....#
#############
"""
CONTACT = 2 * C.ENTITY_HALF  # distância entre centros de duas caixas encostadas


def gap(a, b) -> float:
    """Folga entre duas caixas (negativa = sobrepostas)."""
    return max(abs(a.x - b.x), abs(a.y - b.y)) - a.half - b.half


class Base(unittest.TestCase):
    def setUp(self):
        tilemap = parse_map(MAP, "teste", CONTENT.enemy_by_mark,
                            zones={"npcs": [{"id": "ferreiro", "at": [10, 3]}]})
        self.world = World(tilemap, Database(":memory:"), CONTENT, rng=random.Random(1))
        self.enemies = sorted(self.world.enemies.values(), key=lambda e: e.x)
        self.world.enemies.clear()  # cada teste põe os que precisar
        self.a, _ = self.world.login("Aaa", "1234", "humano")
        self.b, _ = self.world.login("Bbb", "1234", "humano")
        self.a.x, self.a.y = tile_center(3, 3)
        self.b.x, self.b.y = tile_center(5, 3)

    def add_enemy(self, enemy, x, y, stunned=False):
        enemy.x, enemy.y = enemy.home_x, enemy.home_y = x, y
        self.world.enemies[enemy.id] = enemy
        if stunned:
            self.world._apply_effect(enemy, EffectApplication("atordoado", 60), None)


class PlayerCollisionTests(Base):
    def test_player_stops_at_player(self):
        move(self.world, self.a, 30, right=True)
        self.assertAlmostEqual(self.b.x - self.a.x, CONTACT, delta=0.01)
        self.assertGreaterEqual(gap(self.a, self.b), 0)

    def test_slides_along_body(self):
        self.b.x, self.b.y = self.a.x + CONTACT + 0.01, self.a.y
        y0 = self.a.y
        move(self.world, self.a, 5, right=True, down=True)
        self.assertGreater(self.a.y, y0)  # o eixo livre continua andando
        self.assertGreaterEqual(gap(self.a, self.b), 0)

    def test_overlapping_bodies_can_separate(self):
        self.b.x, self.b.y = self.a.x + 5, self.a.y  # ex.: nasceram no mesmo ponto
        x0 = self.a.x
        move(self.world, self.a, 10, left=True)
        self.assertLess(self.a.x, x0)
        self.assertGreaterEqual(gap(self.a, self.b), 0)

    def test_dead_body_does_not_block(self):
        self.b.state, self.b.dead_timer = "dead", 99
        move(self.world, self.a, 30, right=True)
        self.assertGreater(self.a.x, self.b.x)

    def test_npc_blocks(self):
        npc = next(iter(self.world.npcs.entities.values()))
        self.b.x, self.b.y = tile_center(1, 5)  # fora do caminho
        self.a.x, self.a.y = npc.x - 60, npc.y
        move(self.world, self.a, 30, right=True)
        self.assertAlmostEqual(npc.x - self.a.x, CONTACT, delta=0.01)

    def test_enemy_blocks_player(self):
        self.add_enemy(self.enemies[0], self.a.x + 50, self.a.y, stunned=True)
        self.b.x, self.b.y = tile_center(1, 5)
        move(self.world, self.a, 30, right=True)
        self.assertAlmostEqual(self.enemies[0].x - self.a.x, CONTACT, delta=0.01)

    def test_dash_stops_at_body(self):
        self.world._set_class(self.a, CONTENT.classes["aprendiz"])  # tem o Passo veloz
        self.a.energy = self.a.max_energy
        self.b.x = self.a.x + 50
        self.a.facing = "right"
        self.world.handle_message(self.a, {"t": "skill", "id": "passo", "f": "right"})
        self.world.step(DT)
        self.assertGreaterEqual(gap(self.a, self.b), 0)
        self.assertLess(self.b.x - self.a.x, CONTACT + C.DASH_STEP)

    def test_switches_turn_collision_off(self):
        with mock.patch.object(C, "PLAYERS_BLOCK_PLAYERS", False):
            move(self.world, self.a, 30, right=True)
        self.assertGreater(self.a.x, self.b.x)

    def test_welcome_tells_client_what_blocks(self):
        self.assertEqual(self.world.welcome_payload(self.a)["solid"], {"player": True, "enemy": True, "npc": True})


class EnemyCollisionTests(Base):
    def test_enemies_never_overlap_while_chasing(self):
        self.b.x, self.b.y = tile_center(1, 5)
        self.a.x, self.a.y = tile_center(6, 3)  # os dois inimigos vêm atrás dele
        self.a.hp = 10_000  # não morre durante o teste
        e1, e2 = self.enemies
        self.add_enemy(e1, *tile_center(5, 5))
        self.add_enemy(e2, *tile_center(7, 5))
        for _ in range(int(4 / DT)):
            self.a.hp = 10_000
            self.world.step(DT)
            self.assertGreaterEqual(gap(e1, e2), -1e-6)
            self.assertGreaterEqual(gap(e1, self.a), -1e-6)
            self.assertGreaterEqual(gap(e2, self.a), -1e-6)
        self.assertIsNotNone(e1.target_id)  # perseguiram de verdade

    def test_enemy_walks_around_player(self):
        # andando sempre para cima, encosta no jogador, contorna (desvio) e passa, sem nunca entrar nele
        e = self.enemies[0]
        self.add_enemy(e, self.a.x, self.a.y + 60)
        for _ in range(100):
            self.world._enemy_chase_step(e, 0.0, -1.0, DT)
            self.assertGreaterEqual(gap(e, self.a), -1e-6)
        self.assertLess(e.y, self.a.y)


class ClampTests(unittest.TestCase):
    def test_order_does_not_matter(self):
        solids = [(50.0, 0.0, 10.0), (35.0, 5.0, 10.0), (80.0, -3.0, 10.0)]
        results = {clamp_by_solids(0.0, 0.0, 10.0, 30.0, list(order), True)
                   for order in (solids, solids[::-1], solids[1:] + solids[:1])}
        self.assertEqual(results, {35.0 - 20.0 - 1e-3})

    def test_moving_away_is_free(self):
        self.assertEqual(clamp_by_solids(0.0, 0.0, 10.0, -5.0, [(25.0, 0.0, 10.0)], True), -5.0)

    def test_not_crossing_on_other_axis(self):
        self.assertEqual(clamp_by_solids(0.0, 0.0, 10.0, 30.0, [(25.0, 20.0, 10.0)], True), 30.0)


if __name__ == "__main__":
    unittest.main()
