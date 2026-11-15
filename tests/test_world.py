"""Testes das regras do servidor. Rode com:  uv run python -m unittest -v"""
import random
import unittest

from server import config as C
from server.db import Database
from server.stats import Stat
from server.tilemap import load_map, parse_map
from server.world import World, _player_blocks

DT = 1.0 / C.TICK_RATE

#            0123456789
TEST_MAP = """
##########
#::S:....#
#:::::...#
#........#
#....E...#
##########
"""


def make_world(db: Database | None = None) -> World:
    return World(parse_map(TEST_MAP, "teste"), db or Database(":memory:"), rng=random.Random(1))


def tile_center(tx: int, ty: int) -> tuple[float, float]:
    return (tx + 0.5) * C.TILE_SIZE, (ty + 0.5) * C.TILE_SIZE


def run(world: World, seconds: float) -> None:
    for _ in range(round(seconds / DT)):
        world.step(DT)


class TileMapTests(unittest.TestCase):
    def test_parse(self):
        m = parse_map(TEST_MAP, "teste")
        self.assertEqual((m.width, m.height), (10, 6))
        self.assertEqual(m.spawn, tile_center(3, 1))
        self.assertEqual(m.enemy_spawns, [tile_center(5, 4)])
        self.assertEqual(m.rows[1][3], ":")  # S vira zona segura
        self.assertEqual(m.rows[4][5], ".")  # E vira chão

    def test_default_map_loads(self):
        m = load_map(C.DEFAULT_MAP)
        self.assertEqual((m.width, m.height), (40, 30))
        self.assertTrue(m.enemy_spawns)

    def test_wall_stops_and_box_touches_it(self):
        m = parse_map(TEST_MAP, "teste")
        x, y = 48.0, 48.0
        for _ in range(10):
            x, y = m.move_box(x, y, 10, -7, 0, _player_blocks)
        self.assertAlmostEqual(x, 32 + 10, places=2)  # encostado na parede da esquerda
        self.assertFalse(m.box_blocked(x, y, 10, _player_blocks))

    def test_slides_along_wall(self):
        m = parse_map(TEST_MAP, "teste")
        x, y = m.move_box(48.0, 48.0, 10, -7, 7, _player_blocks)
        self.assertGreater(y, 48.0)  # o eixo livre continua andando


class StatTests(unittest.TestCase):
    def test_level_up(self):
        s = Stat()
        self.assertEqual(s.add_xp(s.xp_to_next()), 1)
        self.assertEqual(s.level, 2)

    def test_multiple_levels(self):
        s = Stat()
        self.assertGreaterEqual(s.add_xp(10_000), 2)

    def test_max_level(self):
        s = Stat(level=C.MAX_STAT_LEVEL)
        self.assertEqual(s.add_xp(1e9), 0)


class LoginTests(unittest.TestCase):
    def test_create_and_relogin(self):
        world = make_world()
        p, err = world.login("Teste", "1234")
        self.assertIsNone(err)
        world.logout(p)
        _, err = world.login("teste", "errada")
        self.assertEqual(err, "Senha incorreta.")
        p, err = world.login("teste", "1234")
        self.assertIsNone(err)
        self.assertEqual(p.name, "Teste")  # mantém a grafia original

    def test_duplicate_online(self):
        world = make_world()
        world.login("Teste", "1234")
        _, err = world.login("TESTE", "1234")
        self.assertIsNotNone(err)

    def test_invalid_name(self):
        world = make_world()
        for name in ("ab", "nome com espaço", "x" * 17, "<b>"):
            _, err = world.login(name, "1234")
            self.assertIsNotNone(err, name)

    def test_progress_is_saved(self):
        db = Database(":memory:")
        world = make_world(db)
        p, _ = world.login("Teste", "1234")
        p.stats["spirit"] = Stat(level=7, xp=12.5)
        p.x, p.y = tile_center(6, 3)
        world.logout(p)
        p, _ = make_world(db).login("Teste", "1234")
        self.assertEqual(p.stats["spirit"].level, 7)
        self.assertEqual((p.x, p.y), tile_center(6, 3))


class GameplayTests(unittest.TestCase):
    def setUp(self):
        self.world = make_world()
        self.player, _ = self.world.login("Teste", "1234")
        self.enemy = next(iter(self.world.enemies.values()))

    def place_next_to_enemy(self):
        self.player.x, self.player.y = self.enemy.x - 20, self.enemy.y
        self.player.facing = "right"

    def test_walking_moves_and_trains_agility(self):
        p = self.player
        start_x = p.x
        p.input["right"] = True
        run(self.world, 1.0)
        self.assertGreater(p.x, start_x)
        self.assertGreater(p.stats["agility"].xp + p.stats["agility"].level, 1)

    def test_melee_kills_enemy_and_it_respawns(self):
        self.place_next_to_enemy()
        start_xp = self.player.stats["strength"].xp
        for _ in range(200):
            self.player.actions.append("attack")
            self.world.step(DT)
            if not self.enemy.alive:
                break
        self.assertFalse(self.enemy.alive)
        s = self.player.stats["strength"]
        self.assertTrue(s.level > 1 or s.xp > start_xp)
        run(self.world, C.ENEMY_RESPAWN_SECONDS + 0.1)
        self.assertTrue(self.enemy.alive)
        self.assertEqual(self.enemy.hp, self.enemy.max_hp)

    def test_blast_costs_energy_and_hits(self):
        self.place_next_to_enemy()
        self.player.x -= 40  # um pouco mais longe
        energy = self.player.energy
        self.player.actions.append("blast")
        run(self.world, 0.5)
        self.assertLess(self.player.energy, energy)
        self.assertLess(self.enemy.hp, self.enemy.max_hp)

    def test_meditation_trains_until_exhausted(self):
        p = self.player  # está na zona segura: nenhum inimigo atrapalha
        p.actions.append("meditate")
        run(self.world, 25)
        self.assertEqual(p.state, "idle")  # parou sozinho ao ficar exausto
        self.assertGreater(p.fatigue, C.MEDITATE_FATIGUE_LIMIT)
        self.assertGreater(p.stats["spirit"].level, 1)
        p.actions.append("meditate")  # cansado demais: não deixa
        self.world.step(DT)
        self.assertNotEqual(p.state, "meditate")
        p.actions.append("rest")
        run(self.world, 5)
        self.assertLess(p.fatigue, C.MEDITATE_FATIGUE_LIMIT)
        p.actions.append("meditate")  # descansou: pode meditar de novo
        self.world.step(DT)
        self.assertEqual(p.state, "meditate")

    def test_no_pvp_in_safe_zone(self):
        a, b = self.player, self.world.login("Outro", "1234")[0]
        a.x, a.y = tile_center(2, 2)
        b.x, b.y = a.x + 20, a.y
        a.facing = "right"
        a.actions.append("attack")
        self.world.step(DT)
        self.assertEqual(b.hp, b.max_hp)

        a.x, a.y = tile_center(7, 1)  # fora da zona segura, longe do inimigo
        b.x, b.y = a.x + 20, a.y
        a.attack_cd = 0
        a.actions.append("attack")
        self.world.step(DT)
        self.assertLess(b.hp, b.max_hp)

    def test_player_death_and_respawn(self):
        p = self.player
        self.place_next_to_enemy()
        p.hp = 1
        run(self.world, 1.5)
        self.assertFalse(p.alive)
        run(self.world, C.RESPAWN_SECONDS + 0.1)
        self.assertTrue(p.alive)
        self.assertEqual((p.x, p.y), self.world.map.spawn)

    def test_chat_and_snapshot_messages(self):
        self.world.handle_message(self.player, {"t": "chat", "text": "olá"})
        self.world.step(DT)
        out = self.world.drain_outbox()
        kinds = [msg["t"] for _, msg in out]
        self.assertIn("chat", kinds)
        self.assertIn("snap", kinds)


if __name__ == "__main__":
    unittest.main()
