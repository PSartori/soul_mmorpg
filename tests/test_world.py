"""Testes das regras do servidor. Rode com:  uv run python -m unittest -v"""
import dataclasses
import random
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from server import config as C
from server.content import Content, ContentError, EffectApplication, parse_rules
from server.db import Database
from server.saves import SAVE_VERSION, SaveVersionError, migrate
from server.stats import Stat
from server.tilemap import load_map, parse_map
from server.world import World, _player_blocks

DT = 1.0 / C.TICK_RATE
# Os testes de jogabilidade rodam SEM as regras de data/rules.yaml (que são exemplos
# e vão mudar); os testes de regras montam as próprias com rules_from().
CONTENT = dataclasses.replace(Content.load(C.DATA_DIR), rules=())


def rules_from(text: str):
    return parse_rules(yaml.safe_load(text), CONTENT)

#            0123456789
TEST_MAP = """
##########
#::S:....#
#:::::...#
#........#
#....E...#
##########
"""


def make_world(db: Database | None = None, text: str = TEST_MAP, rules: tuple = ()) -> World:
    tilemap = parse_map(text, "teste", CONTENT.enemy_by_mark)
    content = dataclasses.replace(CONTENT, rules=rules)
    return World(tilemap, db or Database(":memory:"), content, rng=random.Random(1))


def tile_center(tx: int, ty: int) -> tuple[float, float]:
    return (tx + 0.5) * C.TILE_SIZE, (ty + 0.5) * C.TILE_SIZE


def run(world: World, seconds: float) -> None:
    for _ in range(round(seconds / DT)):
        world.step(DT)


def move(world: World, player, ticks: int, **keys) -> None:
    """Simula o cliente: um comando de movimento por tick."""
    for _ in range(ticks):
        world.handle_message(player, {"t": "move", "seq": player.last_seq + 1, **keys})
        world.step(DT)


def use(world: World, player, skill_id: str, facing: str | None = None) -> None:
    msg = {"t": "skill", "id": skill_id}
    if facing:
        msg["f"] = facing
    world.handle_message(player, msg)
    world.step(DT)


class TileMapTests(unittest.TestCase):
    def test_parse(self):
        m = parse_map(TEST_MAP, "teste", {"E"})
        self.assertEqual((m.width, m.height), (10, 6))
        self.assertEqual(m.spawn, tile_center(3, 1))
        self.assertEqual(m.enemy_spawns, [("E", *tile_center(5, 4))])
        self.assertEqual(m.rows[1][3], ":")  # S vira zona segura
        self.assertEqual(m.rows[4][5], ".")  # marca de inimigo vira chão

    def test_unknown_mark_is_error(self):
        with self.assertRaises(ValueError):
            parse_map(TEST_MAP, "teste", set())

    def test_default_map_loads(self):
        m = load_map(C.DEFAULT_MAP, CONTENT.enemy_by_mark)
        self.assertEqual((m.width, m.height), (40, 30))
        self.assertEqual({mark for mark, _, _ in m.enemy_spawns}, {"E", "B"})

    def test_wall_stops_and_box_touches_it(self):
        m = parse_map(TEST_MAP, "teste", {"E"})
        x, y = 48.0, 48.0
        for _ in range(10):
            x, y = m.move_box(x, y, 10, -7, 0, _player_blocks)
        self.assertAlmostEqual(x, 32 + 10, places=2)  # encostado na parede da esquerda
        self.assertFalse(m.box_blocked(x, y, 10, _player_blocks))

    def test_slides_along_wall(self):
        m = parse_map(TEST_MAP, "teste", {"E"})
        _, y = m.move_box(48.0, 48.0, 10, -7, 7, _player_blocks)
        self.assertGreater(y, 48.0)  # o eixo livre continua andando


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        for path in C.DATA_DIR.glob("*.yaml"):
            shutil.copy(path, self.dir / path.name)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def append(self, name: str, text: str) -> None:
        with open(self.dir / name, "a", encoding="utf-8") as f:
            f.write("\n" + text)

    def assert_error(self, *fragments: str) -> None:
        with self.assertRaises(ContentError) as ctx:
            Content.load(self.dir)
        for fragment in fragments:
            self.assertIn(fragment, str(ctx.exception))

    def test_real_content_loads(self):
        content = Content.load(self.dir)  # com as regras de exemplo
        self.assertIn("golpe", content.skills)
        self.assertTrue(content.rules)
        self.assertTrue(content.selectable_races())

    def test_class_of_unknown_race(self):
        self.append("classes.yaml", "teste:\n  name: T\n  race: anao\n  skills: [golpe]\n")
        self.assert_error("teste", "anao")

    def test_start_class_of_other_race(self):
        self.append("races.yaml", "teste:\n  name: T\n  start_class: aprendiz\n")
        self.assert_error("teste", "aprendiz")

    def test_rule_unknown_event(self):
        self.append("rules.yaml", "teste:\n  when: morreu\n")
        self.assert_error("teste", "morreu")

    def test_rule_field_not_in_event(self):
        self.append("rules.yaml", "teste:\n  when: level_up\n  if: { killer: enemy }\n")
        self.assert_error("teste", "killer", "level_up")

    def test_rule_invalid_value(self):
        self.append("rules.yaml", "teste:\n  when: death\n  if: { killer_enemy: dragao }\n")
        self.assert_error("teste", "dragao")

    def test_rule_unknown_action(self):
        self.append("rules.yaml", "teste:\n  when: death\n  do: [ { teleportar: x } ]\n")
        self.assert_error("teste", "teleportar")

    def test_rule_add_effect_on_death(self):
        self.append("rules.yaml", "teste:\n  when: death\n  do: [ { add_effect: { id: lento, duration: 2 } } ]\n")
        self.assert_error("teste", "add_effect")

    def test_rule_set_race_with_class_of_other_race(self):
        self.append("rules.yaml", "teste:\n  when: death\n  do: [ { set_race: { race: humano, class: iniciado } } ]\n")
        self.assert_error("teste", "iniciado")

    def test_unknown_field(self):
        self.append("skills.yaml", "teste:\n  name: T\n  type: melee\n  cooldown: 1\n  reach: 10\n  size: 5\n  velocidade: 3\n")
        self.assert_error("teste", "velocidade")

    def test_field_of_other_type(self):
        self.append("skills.yaml", "teste:\n  name: T\n  type: melee\n  cooldown: 1\n  reach: 10\n  size: 5\n  speed: 3\n")
        self.assert_error("teste", "speed")

    def test_missing_effect_reference(self):
        self.append("skills.yaml", "teste:\n  name: T\n  type: self\n  cooldown: 1\n  self_effects: [{id: nao_existe, duration: 1}]\n")
        self.assert_error("teste", "nao_existe")

    def test_unknown_stat(self):
        self.append("skills.yaml", "teste:\n  name: T\n  type: self\n  cooldown: 1\n  train: {sorte: 1}\n")
        self.assert_error("teste", "sorte")

    def test_wrong_type(self):
        self.append("effects.yaml", "teste:\n  name: T\n  modifiers: {speed: rapido}\n")
        self.assert_error("teste", "speed")


class StatTests(unittest.TestCase):
    def test_level_up(self):
        s = Stat()
        self.assertEqual(s.add_xp(s.xp_to_next()), 1)
        self.assertEqual(s.level, 2)

    def test_multiple_levels(self):
        self.assertGreaterEqual(Stat().add_xp(10_000), 2)

    def test_max_level(self):
        self.assertEqual(Stat(level=C.MAX_STAT_LEVEL).add_xp(1e9), 0)


class LoginTests(unittest.TestCase):
    def test_create_and_relogin(self):
        world = make_world()
        p, err = world.login("Teste", "1234", "humano")
        self.assertIsNone(err)
        self.assertEqual(p.class_id, CONTENT.races["humano"].start_class)
        self.assertEqual(p.skills, list(CONTENT.classes[p.class_id].skills))
        world.logout(p)
        _, err = world.login("teste", "errada")
        self.assertEqual(err, "Senha incorreta.")
        p, err = world.login("teste", "1234", "humano")
        self.assertIsNone(err)
        self.assertEqual(p.name, "Teste")  # mantém a grafia original

    def test_duplicate_online(self):
        world = make_world()
        world.login("Teste", "1234", "humano")
        _, err = world.login("TESTE", "1234", "humano")
        self.assertIsNotNone(err)

    def test_invalid_name(self):
        world = make_world()
        for name in ("ab", "nome com espaço", "x" * 17, "<b>"):
            _, err = world.login(name, "1234")
            self.assertIsNotNone(err, name)

    def test_progress_is_saved(self):
        db = Database(":memory:")
        world = make_world(db)
        p, _ = world.login("Teste", "1234", "humano")
        p.stats["spirit"] = Stat(level=7, xp=12.5)
        p.x, p.y = tile_center(6, 3)
        world.logout(p)
        p, _ = make_world(db).login("Teste", "1234", "humano")
        self.assertEqual(p.stats["spirit"].level, 7)
        self.assertEqual((p.x, p.y), tile_center(6, 3))

    def test_new_character_needs_selectable_race(self):
        world = make_world()
        self.assertIsNotNone(world.login("Teste", "1234")[1])  # sem raça
        self.assertIsNotNone(world.login("Teste", "1234", "espectral")[1])  # não selecionável
        self.assertIsNotNone(world.login("Teste", "1234", "anao")[1])  # não existe
        self.assertIsNone(world.db.load_character("Teste"))  # nada foi criado

    def test_race_is_ignored_for_existing_character(self):
        world = make_world()
        p, _ = world.login("Teste", "1234", "desperto")
        world.logout(p)
        p, err = world.login("Teste", "1234", "humano")
        self.assertIsNone(err)
        self.assertEqual(p.race_id, "desperto")

    def test_race_bonus(self):
        world = make_world()
        human, _ = world.login("Humano1", "1234", "humano")
        awake, _ = world.login("Desperto1", "1234", "desperto")
        self.assertEqual(human.max_hp - awake.max_hp, 30)  # +20 contra -10
        self.assertEqual(awake.max_energy - human.max_energy, 25)
        self.assertEqual(human.hp, human.max_hp)  # personagem novo nasce cheio

    def test_class_and_flags_are_saved(self):
        db = Database(":memory:")
        world = make_world(db)
        p, _ = world.login("Teste", "1234", "humano")
        world._set_class(p, CONTENT.classes["assombracao"])
        p.flags.add("marcado")
        world.logout(p)
        p, _ = make_world(db).login("Teste", "1234")
        self.assertEqual((p.race_id, p.class_id), ("espectral", "assombracao"))
        self.assertEqual(p.flags, {"marcado"})
        self.assertEqual(db.load_character("Teste").data["v"], SAVE_VERSION)

    def test_old_save_gets_default_race(self):
        db = Database(":memory:")
        db.create_character("Antigo", "1234")
        db.save_character("Antigo", {"x": 100.0, "y": 80.0, "hp": 50, "stats": {"spirit": {"level": 4, "xp": 1}}})
        p, err = make_world(db).login("Antigo", "1234")
        self.assertIsNone(err)
        self.assertEqual(p.race_id, CONTENT.default_race)
        self.assertEqual(p.class_id, CONTENT.races[CONTENT.default_race].start_class)
        self.assertEqual(p.stats["spirit"].level, 4)

    def test_removed_class_falls_back_to_race_start(self):
        db = Database(":memory:")
        db.create_character("Teste", "1234")
        db.save_character("Teste", {"v": 2, "race": "desperto", "class": "classe_removida", "flags": []})
        p, _ = make_world(db).login("Teste", "1234")
        self.assertEqual(p.class_id, "iniciado")

    def test_save_from_newer_server_is_refused(self):
        db = Database(":memory:")
        db.create_character("Futuro", "1234")
        db.save_character("Futuro", {"v": SAVE_VERSION + 1})
        _, err = make_world(db).login("Futuro", "1234")
        self.assertIsNotNone(err)


class SaveTests(unittest.TestCase):
    def test_migrate_v1(self):
        data = migrate({"x": 1.0})
        self.assertEqual(data["v"], SAVE_VERSION)
        self.assertIsNone(data["race"])
        self.assertEqual(data["flags"], [])
        self.assertEqual(data["x"], 1.0)

    def test_migrate_does_not_change_input(self):
        original = {"x": 1.0}
        migrate(original)
        self.assertEqual(original, {"x": 1.0})

    def test_invalid_version(self):
        for v in (0, "2", SAVE_VERSION + 1):
            with self.assertRaises(SaveVersionError):
                migrate({"v": v})

    def test_backup_keeps_last_copies(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            db = Database(tmp / "game.db")
            db.create_character("Teste", "1234")
            backups = tmp / "backups"
            for i in range(3):
                (backups / f"game-2000010{i}-000000.db").parent.mkdir(exist_ok=True)
                (backups / f"game-2000010{i}-000000.db").write_bytes(b"")
            path = db.backup(backups, keep=2)
            self.assertEqual(len(list(backups.glob("game-*.db"))), 2)
            self.assertTrue(path.exists())
            self.assertIsNotNone(Database(path).load_character("Teste"))  # a cópia funciona
        finally:
            shutil.rmtree(tmp)


class GameplayBase(unittest.TestCase):
    def setUp(self):
        self.world = make_world()
        self.player, _ = self.world.login("Teste", "1234", "humano")
        # Os testes de skills usam qualquer skill, independente da classe
        self.player.skills = [sid for sid in CONTENT.skills]
        self.enemy = next(iter(self.world.enemies.values()))

    def place_next_to_enemy(self, gap: float = 20):
        self.player.x, self.player.y = self.enemy.x - gap, self.enemy.y
        self.player.facing = "right"


class MovementTests(GameplayBase):
    def test_move_commands_move_and_ack(self):
        p = self.player
        start_x = p.x
        move(self.world, p, 10, right=True)
        self.assertAlmostEqual(p.x - start_x, p.speed * DT * 10, places=3)
        self.assertEqual(p.ack, 10)
        self.assertEqual(p.facing, "right")

    def test_no_command_no_movement(self):
        p = self.player
        start = (p.x, p.y)
        run(self.world, 1.0)
        self.assertEqual((p.x, p.y), start)

    def test_old_sequence_is_ignored(self):
        p = self.player
        move(self.world, p, 3, right=True)
        x = p.x
        self.world.handle_message(p, {"t": "move", "seq": 2, "right": True})
        self.world.step(DT)
        self.assertEqual(p.x, x)

    def test_queue_is_capped(self):
        p = self.player
        for seq in range(1, 30):
            self.world.handle_message(p, {"t": "move", "seq": seq, "right": True})
        self.assertEqual(len(p.move_queue), C.MAX_MOVE_QUEUE)

    def test_walking_trains_agility(self):
        p = self.player
        move(self.world, p, 20, right=True)
        self.assertGreater(p.stats["agility"].xp, 0)


class SkillTests(GameplayBase):
    def test_melee_kills_enemy_and_it_respawns(self):
        self.place_next_to_enemy()
        start_xp = self.player.stats["strength"].xp
        for _ in range(200):
            use(self.world, self.player, "golpe")
            if not self.enemy.alive:
                break
        self.assertFalse(self.enemy.alive)
        s = self.player.stats["strength"]
        self.assertTrue(s.level > 1 or s.xp > start_xp)
        run(self.world, self.enemy.defn.respawn + 0.1)
        self.assertTrue(self.enemy.alive)
        self.assertEqual(self.enemy.hp, self.enemy.max_hp)

    def test_skill_uses_facing_from_client(self):
        self.place_next_to_enemy()
        self.player.facing = "left"  # servidor acha que olha para a esquerda...
        use(self.world, self.player, "golpe", facing="right")  # ...mas o cliente virou
        self.assertLess(self.enemy.hp, self.enemy.max_hp)

    def test_cost_and_cooldown(self):
        p = self.player
        p.x, p.y = tile_center(7, 1)
        energy = p.energy
        use(self.world, p, "rajada")
        self.assertAlmostEqual(p.energy, energy - CONTENT.skills["rajada"].cost_energy, delta=0.2)
        count = len(self.world.projectiles)
        use(self.world, p, "rajada")  # ainda em cooldown
        self.assertLessEqual(len(self.world.projectiles), count)

    def test_not_enough_energy(self):
        p = self.player
        p.energy = 0
        use(self.world, p, "rajada")
        self.assertEqual(len(self.world.projectiles), 0)

    def test_unknown_skill_is_ignored(self):
        self.player.skills = list(CONTENT.classes["aprendiz"].skills)
        self.world.handle_message(self.player, {"t": "skill", "id": "garra"})  # skill de inimigo
        self.assertEqual(self.player.actions, [])

    def test_projectile_hits(self):
        self.place_next_to_enemy(gap=60)
        use(self.world, self.player, "rajada")
        run(self.world, 0.5)
        self.assertLess(self.enemy.hp, self.enemy.max_hp)

    def test_spread_creates_several_projectiles(self):
        p = self.player
        p.x, p.y = tile_center(7, 3)
        use(self.world, p, "leque")
        self.assertEqual(len(self.world.projectiles), CONTENT.skills["leque"].count)

    def test_dash_moves_and_stops_at_wall(self):
        p = self.player
        p.x, p.y = tile_center(2, 3)
        use(self.world, p, "passo", facing="right")
        self.assertGreater(p.x, tile_center(2, 3)[0] + 64)
        p.cooldowns.clear()
        p.energy = p.max_energy
        use(self.world, p, "passo", facing="right")
        self.assertLessEqual(p.x + p.half, 9 * C.TILE_SIZE)  # não atravessou a parede
        self.assertFalse(self.world.map.box_blocked(p.x, p.y, p.half, _player_blocks))

    def test_area_hits_around(self):
        self.place_next_to_enemy(gap=30)
        self.player.facing = "left"  # área não depende da direção
        use(self.world, self.player, "onda")
        self.assertLess(self.enemy.hp, self.enemy.max_hp)
        self.assertIn("atordoado", self.enemy.effects)

    def test_enemy_uses_its_skill(self):
        p = self.player
        self.place_next_to_enemy()
        run(self.world, 1.5)
        self.assertLess(p.hp, p.max_hp)

    def test_enemy_goes_around_a_tree(self):
        world = make_world(text="""
##########
#S:......#
#........#
#....E...#
#....T...#
#........#
##########
""")
        p, _ = world.login("Teste", "1234", "humano")
        enemy = next(iter(world.enemies.values()))
        p.x, p.y = tile_center(5, 5)  # árvore exatamente entre os dois
        run(world, 6)
        self.assertLess(p.hp, p.max_hp)

    def test_meditation_trains_until_exhausted(self):
        p = self.player  # está na zona segura: nenhum inimigo atrapalha
        p.actions.append(("meditate", None, None))
        run(self.world, 25)
        self.assertEqual(p.state, "idle")  # parou sozinho ao ficar exausto
        self.assertGreater(p.fatigue, C.MEDITATE_FATIGUE_LIMIT)
        self.assertGreater(p.stats["spirit"].level, 1)
        self.world.handle_message(p, {"t": "meditate"})  # cansado demais: não deixa
        self.world.step(DT)
        self.assertNotEqual(p.state, "meditate")
        self.world.handle_message(p, {"t": "rest"})
        run(self.world, 5)
        self.assertLess(p.fatigue, C.MEDITATE_FATIGUE_LIMIT)
        self.world.handle_message(p, {"t": "meditate"})
        self.world.step(DT)
        self.assertEqual(p.state, "meditate")

    def test_no_pvp_in_safe_zone(self):
        a, b = self.player, self.world.login("Outro", "1234", "humano")[0]
        a.x, a.y = tile_center(2, 2)
        b.x, b.y = a.x + 20, a.y
        use(self.world, a, "golpe", facing="right")
        self.assertEqual(b.hp, b.max_hp)

        a.x, a.y = tile_center(7, 1)  # fora da zona segura
        b.x, b.y = a.x + 20, a.y
        a.cooldowns.clear()
        use(self.world, a, "golpe", facing="right")
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
        kinds = [msg["t"] for _, msg in self.world.drain_outbox()]
        self.assertIn("chat", kinds)
        self.assertIn("snap", kinds)


class EffectTests(GameplayBase):
    def apply(self, target, effect_id: str, duration: float = 5.0, source=None):
        self.world._apply_effect(target, EffectApplication(effect_id, duration), source)

    def test_slow_halves_speed(self):
        p = self.player
        normal = p.speed
        self.apply(p, "lento")
        self.assertAlmostEqual(p.speed, normal * 0.5)
        start = p.x
        move(self.world, p, 10, right=True)
        self.assertAlmostEqual(p.x - start, normal * 0.5 * DT * 10, places=3)

    def test_stun_blocks_movement_and_skills(self):
        p = self.player
        p.x, p.y = tile_center(7, 3)
        self.apply(p, "atordoado", 1.0)
        start = (p.x, p.y)
        move(self.world, p, 5, right=True)
        use(self.world, p, "rajada")
        self.assertEqual((p.x, p.y), start)
        self.assertEqual(len(self.world.projectiles), 0)
        run(self.world, 1.0)
        self.assertNotIn("atordoado", p.effects)
        move(self.world, p, 5, right=True)
        self.assertGreater(p.x, start[0])

    def test_poison_ticks_and_gives_kill_credit(self):
        p = self.player
        p.x, p.y = tile_center(8, 1)  # fora da zona segura, senão o inimigo desiste e regenera
        self.enemy.hp = 7
        self.apply(self.enemy, "veneno", 5.0, source=p)
        run(self.world, 1.05)
        self.assertEqual(self.enemy.hp, 4)
        self.assertEqual(self.enemy.target_id, p.id)  # o envenenado persegue quem envenenou
        run(self.world, 2.0)
        self.assertFalse(self.enemy.alive)

    def test_poison_does_not_hurt_in_safe_zone(self):
        p = self.player  # nasce na zona segura
        self.apply(p, "veneno", 3.0)
        run(self.world, 3.1)
        self.assertEqual(p.hp, p.max_hp)

    def test_effect_expires(self):
        self.apply(self.player, "lento", 0.5)
        run(self.world, 0.6)
        self.assertNotIn("lento", self.player.effects)

    def test_reapply_refreshes_without_stacking(self):
        p = self.player
        normal = p.speed
        self.apply(p, "lento", 1.0)
        self.apply(p, "lento", 3.0)
        self.assertAlmostEqual(p.speed, normal * 0.5)
        self.assertAlmostEqual(p.effects["lento"].remaining, 3.0)

    def test_damage_buff(self):
        p = self.player
        plain = self.world._skill_damage(p, CONTENT.skills["golpe"])
        use(self.world, p, "foco")
        self.assertIn("foco", p.effects)
        self.assertAlmostEqual(self.world._skill_damage(p, CONTENT.skills["golpe"]), plain * 1.3)

    def test_damage_taken_modifier(self):
        self.place_next_to_enemy()
        self.apply(self.enemy, "couraca")
        hp = self.enemy.hp
        damage = self.world._skill_damage(self.player, CONTENT.skills["golpe"])
        use(self.world, self.player, "golpe")
        self.assertEqual(hp - self.enemy.hp, round(damage * 0.6))

    def test_effects_cleared_on_death(self):
        p = self.player
        self.apply(p, "lento", 30)
        self.world._kill(p, None, None)
        self.assertEqual(p.effects, {})


class RuleTests(unittest.TestCase):
    """Motor de regras e morte com contexto, com regras montadas em cada teste."""

    def world_with(self, text: str, race: str = "humano") -> World:
        self.world = make_world(rules=rules_from(text))
        self.player, _ = self.world.login("Teste", "1234", race)
        self.player.x, self.player.y = tile_center(7, 3)  # fora da zona segura
        self.enemy = next(iter(self.world.enemies.values()))
        return self.world

    def messages(self) -> list[str]:
        return [msg["text"] for _, msg in self.world.drain_outbox() if msg["t"] == "sys"]

    def kill_player(self, killer=None, skill_id: str | None = "garra", effect_id: str | None = None):
        killer = self.enemy if killer is None else killer
        skill = CONTENT.skills[skill_id] if skill_id else None
        self.player.hp = 1  # golpe de 1: não mexe no XP de Vitalidade além do mínimo
        self.world._damage(self.player, 1, killer, skill, effect_id)

    def test_death_by_enemy_type_changes_race(self):
        self.world_with("""
virar:
  when: death
  if: { race: humano, killer_enemy: espectro }
  do:
    - set_race: espectral
    - message: "Você renasce como {class}."
""")
        self.kill_player()
        p = self.player
        self.assertEqual((p.race_id, p.class_id), ("espectral", "assombracao"))
        self.assertEqual(p.skills, list(CONTENT.classes["assombracao"].skills))
        self.assertIn("Você renasce como Assombração.", self.messages())
        run(self.world, C.RESPAWN_SECONDS + 0.1)
        self.assertEqual(p.hp, p.max_hp)  # renasce com a vida da raça nova

    def test_condition_not_met(self):
        self.world_with("virar:\n  when: death\n  if: { killer_enemy: bruto }\n  do: [ { set_race: espectral } ]\n")
        self.kill_player()
        self.assertEqual(self.player.race_id, "humano")

    def test_stop_skips_following_rules(self):
        self.world_with("""
primeira:
  when: death
  do: [ { set_flag: a } ]
  stop: true
segunda:
  when: death
  do: [ { set_flag: b } ]
""")
        self.kill_player()
        self.assertEqual(self.player.flags, {"a"})

    def test_rules_see_race_from_before_actions(self):
        self.world_with("""
virar:
  when: death
  if: { race: humano }
  do: [ { set_race: espectral } ]
de_novo:
  when: death
  if: { race: espectral }
  do: [ { set_flag: nao_deveria } ]
""")
        self.kill_player()
        self.assertNotIn("nao_deveria", self.player.flags)

    def test_lose_xp_highest_never_loses_level(self):
        self.world_with("p:\n  when: death\n  if: { zone: open, killer: enemy }\n  do: [ { lose_xp: { percent: 50, stat: highest } } ]\n")
        p = self.player
        p.stats["strength"] = Stat(level=5, xp=10)
        p.stats["spirit"] = Stat(level=3, xp=20)
        self.kill_player()
        self.assertEqual((p.stats["strength"].level, p.stats["strength"].xp), (5, 0.0))
        self.assertEqual(p.stats["spirit"].xp, 20)  # só o maior

    def test_lose_xp_all(self):
        self.world_with("p:\n  when: death\n  do: [ { lose_xp: { percent: 10, stat: all } } ]\n")
        p = self.player
        for s in p.stats.values():
            s.xp = 25.0
        self.kill_player()
        for s in p.stats.values():  # tolerância: o golpe final de 1 dá um pouco de XP de Vitalidade
            self.assertAlmostEqual(s.xp, 25.0 - s.xp_to_next() * 0.1, delta=0.5)

    def test_safe_zone_context(self):
        self.world_with("p:\n  when: death\n  if: { zone: safe }\n  do: [ { set_flag: seguro } ]\n")
        self.player.x, self.player.y = tile_center(2, 2)
        self.kill_player(killer=None, skill_id=None)
        self.assertIn("seguro", self.player.flags)

    def test_death_by_poison_context(self):
        self.world_with("""
veneno:
  when: death
  if: { effect: veneno, killer: player, killer_race: desperto }
  do: [ { set_flag: envenenado } ]
""")
        self.world.enemies.clear()  # senão o Espectro do mapa mata antes do veneno
        other, _ = self.world.login("Outro", "1234", "desperto")
        self.player.hp = 2
        self.world._apply_effect(self.player, EffectApplication("veneno", 5), other)
        run(self.world, 1.1)
        self.assertFalse(self.player.alive)
        self.assertIn("envenenado", self.player.flags)

    def test_active_effects_in_context(self):
        self.world_with("p:\n  when: death\n  if: { effects: [lento, foco] }\n  do: [ { set_flag: estava_lento } ]\n")
        self.world._apply_effect(self.player, EffectApplication("lento", 5), None)
        self.kill_player()
        self.assertIn("estava_lento", self.player.flags)

    def test_kill_event_for_killer(self):
        self.world_with("""
agressor:
  when: kill
  if: { victim: player, zone: open, not_flags: [agressor] }
  do:
    - set_flag: agressor
    - message: "Você derrotou {target}."
""")
        killer, _ = self.world.login("Outro", "1234", "humano")
        killer.x, killer.y = tile_center(8, 3)
        self.world.drain_outbox()
        self.world._damage(self.player, 10_000, killer, CONTENT.skills["golpe"])
        self.assertIn("agressor", killer.flags)
        self.assertNotIn("agressor", self.player.flags)
        texts = [(target, msg.get("text")) for target, msg in self.world.drain_outbox() if msg["t"] == "sys"]
        self.assertIn((killer.id, "Você derrotou Teste."), texts)

    def test_kill_enemy_event(self):
        self.world_with("c:\n  when: kill\n  if: { victim_enemy: espectro }\n  do: [ { gain_xp: { stat: vitality, amount: 7 } } ]\n")
        self.world._damage(self.enemy, 10_000, self.player, CONTENT.skills["golpe"])
        self.assertEqual(self.player.stats["vitality"].xp, 7)

    def test_level_up_with_chance(self):
        self.world_with("""
despertar:
  when: level_up
  if: { race: humano, stat: spirit, level: { min: 3 }, not_flags: [despertou], chance: 0.5 }
  do: [ { set_flag: despertou }, { set_race: desperto } ]
""")
        p = self.player
        for _ in range(40):  # a chance é sorteada a cada nível a partir do 3
            if p.race_id == "desperto":
                break
            self.world._train(p, "spirit", p.stats["spirit"].xp_to_next() + 1, use_rate=False)
        self.assertEqual(p.race_id, "desperto")
        self.assertIn("despertou", p.flags)

    def test_level_up_below_min_does_nothing(self):
        self.world_with("d:\n  when: level_up\n  if: { stat: spirit, level: { min: 50 } }\n  do: [ { set_flag: x } ]\n")
        self.world._train(self.player, "spirit", 1_000, use_rate=False)
        self.assertNotIn("x", self.player.flags)

    def test_chain_is_limited(self):
        self.world_with("""
loop:
  when: level_up
  do: [ { gain_xp: { stat: spirit, amount: 100000 } } ]
""")
        self.world._train(self.player, "spirit", 100, use_rate=False)  # não pode travar
        self.assertGreater(self.player.stats["spirit"].level, 1)

    def test_add_effect_and_broadcast(self):
        self.world_with("""
bonus:
  when: level_up
  if: { stat: strength }
  do:
    - add_effect: { id: foco, duration: 5 }
    - broadcast: "{name} chegou ao nível {level} de {stat}."
""")
        self.world._train(self.player, "strength", self.player.stats["strength"].xp_to_next(), use_rate=False)
        self.assertIn("foco", self.player.effects)
        self.assertIn("Teste chegou ao nível 2 de Força.", self.messages())

    def test_train_rate_from_class(self):
        world = make_world()
        human, _ = world.login("Humano1", "1234", "humano")  # aprendiz: strength 1.25, spirit 0.8
        world._train(human, "strength", 10)
        world._train(human, "spirit", 10)
        self.assertAlmostEqual(human.stats["strength"].xp, 12.5)
        self.assertAlmostEqual(human.stats["spirit"].xp, 8.0)

    def test_example_rules_file_works_in_game(self):
        """As regras de exemplo de data/rules.yaml carregam e rodam sem erro."""
        world = make_world(rules=Content.load(C.DATA_DIR).rules)
        p, _ = world.login("Teste", "1234", "humano")
        p.x, p.y = tile_center(7, 3)
        enemy = next(iter(world.enemies.values()))
        world._damage(p, 10_000, enemy, CONTENT.skills["garra"])
        self.assertEqual(p.race_id, "espectral")


if __name__ == "__main__":
    unittest.main()
