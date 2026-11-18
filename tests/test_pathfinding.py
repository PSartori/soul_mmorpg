"""Caminho dos inimigos (A*): contornar paredes, sair de salas, desistir sem rota."""
import dataclasses
import math
import random
import unittest

from server import config as C
from server.db import Database
from server.pathfinding import clear_line, find_path, tile_of
from server.tilemap import parse_map
from server.world import World, _enemy_blocks
from tests.test_world import CONTENT, DT, tile_center

# Muro em U entre o inimigo (E, dentro do U) e o jogador (fora): indo reto, ele fica preso.
#          0123456789012345
U_WALL = """
################
#S:............#
#..............#
#....#####.....#
#....#.E.#.....#
#....#...#.....#
#..............#
#..............#
################
"""
# Labirinto: o único caminho do E até o canto direito dá voltas.
MAZE = """
################
#S:#...#.......#
#..#.#.#.#####.#
#..#.#.#.#...#.#
#.E..#...#.#...#
################
"""
# Água no meio, sem ponte: não dá para chegar do outro lado.
RIVER = """
################
#S:....~~......#
#......~~......#
#..E...~~......#
#......~~......#
################
"""


def world_for(text: str, enemy: str = "espectro", **changes) -> World:
    content = CONTENT
    if changes:
        defn = dataclasses.replace(CONTENT.enemies[enemy], **changes)
        content = dataclasses.replace(CONTENT, enemies={**CONTENT.enemies, enemy: defn})
    tilemap = parse_map(text, "teste", content.enemy_by_mark)
    return World(tilemap, Database(":memory:"), content, rng=random.Random(1))


def setup(world: World, player_tile):
    p, _ = world.login("Alvo", "1234", "humano")
    p.x, p.y = tile_center(*player_tile)
    p.hp = 1e6
    enemy = next(iter(world.enemies.values()))
    return p, enemy


def chase(world: World, p, seconds: float):
    for _ in range(round(seconds / DT)):
        p.hp = 1e6
        world.step(DT)


class FindPathTests(unittest.TestCase):
    def test_goes_around_walls(self):
        tilemap = parse_map(U_WALL, "teste", CONTENT.enemy_by_mark)
        path = find_path(tilemap, (7, 4), (7, 1), 10, _enemy_blocks, 500)
        self.assertIsNotNone(path)
        self.assertEqual(path[-1], (7, 1))
        self.assertTrue(all(tilemap.tile_at(*t) != "#" for t in path))
        self.assertGreater(len(path), 4)  # deu a volta (reto seriam 3)

    def test_no_corner_cutting(self):
        tilemap = parse_map(MAZE, "teste", CONTENT.enemy_by_mark)
        path = find_path(tilemap, (2, 4), (14, 1), 10, _enemy_blocks, 500)
        self.assertIsNotNone(path)
        prev = (2, 4)
        for t in path:
            dx, dy = t[0] - prev[0], t[1] - prev[1]
            if dx and dy:  # diagonal: os dois lados livres
                self.assertNotEqual(tilemap.tile_at(prev[0] + dx, prev[1]), "#")
                self.assertNotEqual(tilemap.tile_at(prev[0], prev[1] + dy), "#")
            prev = t

    def test_unreachable_and_limit(self):
        tilemap = parse_map(RIVER, "teste", CONTENT.enemy_by_mark)
        self.assertIsNone(find_path(tilemap, (3, 3), (12, 3), 10, _enemy_blocks, 500))
        tilemap = parse_map(MAZE, "teste", CONTENT.enemy_by_mark)
        self.assertIsNone(find_path(tilemap, (2, 4), (14, 1), 10, _enemy_blocks, 5))  # limite baixo demais

    def test_same_tile(self):
        tilemap = parse_map(U_WALL, "teste", CONTENT.enemy_by_mark)
        self.assertEqual(find_path(tilemap, (7, 4), (7, 4), 10, _enemy_blocks, 10), [])

    def test_clear_line(self):
        tilemap = parse_map(U_WALL, "teste", CONTENT.enemy_by_mark)
        self.assertTrue(clear_line(tilemap, *tile_center(1, 6), *tile_center(14, 6), 10, _enemy_blocks))
        self.assertFalse(clear_line(tilemap, *tile_center(7, 4), *tile_center(7, 1), 10, _enemy_blocks))


class EnemyPathTests(unittest.TestCase):
    def test_gets_out_of_the_u(self):
        world = world_for(U_WALL, aggro_radius=400, leash=1000)
        p, e = setup(world, (7, 1))
        chase(world, p, 4)
        self.assertLess(math.hypot(e.x - p.x, e.y - p.y), 40)

    def test_walks_the_maze(self):
        world = world_for(MAZE, aggro_radius=600, leash=2000)
        p, e = setup(world, (14, 1))
        chase(world, p, 8)
        self.assertLess(math.hypot(e.x - p.x, e.y - p.y), 40)

    def test_gives_up_without_path_and_tries_again_later(self):
        world = world_for(RIVER, aggro_radius=600, leash=2000)
        p, e = setup(world, (12, 3))
        world.step(DT)
        world.step(DT)
        self.assertIsNone(e.target_id)
        self.assertEqual(e.ignore_id, p.id)
        start = (e.x, e.y)
        chase(world, p, 1)
        self.assertLess(math.hypot(e.x - start[0], e.y - start[1]), 40)  # não fica empurrando a água
        chase(world, p, C.PATH_GIVE_UP_SECONDS)
        self.assertEqual(e.ignore_id, p.id)  # tentou de novo, continua sem caminho

    def test_returns_home_by_path(self):
        world = world_for(U_WALL, aggro_radius=0)
        p, e = setup(world, (14, 7))
        e.x, e.y = tile_center(7, 1)  # foi parar fora do U (ex.: perseguição)
        chase(world, p, 8)
        self.assertLess(math.hypot(e.x - e.home_x, e.y - e.home_y), e.defn.wander_radius + C.TILE_SIZE + 1)

    def test_ranged_enemy_seeks_line_of_fire(self):
        # Vulto atrás de um pilar: com alcance de sobra, mas sem linha de tiro, ele se move até ter
        pillar = """
################
#S:............#
#..............#
#......#.......#
#..V...#.......#
#......#.......#
#..............#
################
"""
        world = world_for(pillar, enemy="vulto", aggro_radius=400, leash=2000)
        p, e = setup(world, (9, 4))
        world.step(DT)
        self.assertFalse(clear_line(world.map, e.x, e.y, p.x, p.y, 2.0, lambda ch: ch in "#T"))
        start_hp = p.hp = 1e6
        for _ in range(round(4 / DT)):
            world.step(DT)
        self.assertLess(p.hp, start_hp)  # achou um ângulo e acertou

    def test_boss_leaves_room_through_two_tile_door(self):
        room = """
######################
#S:..................#
#....................#
#...#######..........#
#...#.....#..........#
#...#..G..#..........#
#...#.....#..........#
#...###..##..........#
#....................#
#....................#
######################
"""
        world = world_for(room, enemy="guardiao", aggro_radius=600, leash=2000)
        p, boss = setup(world, (16, 9))
        chase(world, p, 8)
        self.assertGreater(boss.y, tile_center(0, 7)[1])  # saiu pela porta de baixo
        self.assertLess(math.hypot(boss.x - p.x, boss.y - p.y), 60)


if __name__ == "__main__":
    unittest.main()
