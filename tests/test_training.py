"""Testes de skills aprendidas, barra de atalhos e treinadores."""
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
from server.saves import migrate
from server.stats import Stat
from server.tilemap import parse_map
from server.world import World

DT = 1.0 / C.TICK_RATE
CONTENT = dataclasses.replace(Content.load(C.DATA_DIR), rules=())

MAP = """
##########
#S:......#
#........#
#........#
##########
"""
NPCS_YAML = """
mestre:
  name: Mestre
  dialogue:
    inicio:
      text: "Treinar?"
      options:
        - text: Treinar
          trainer: treino
        - text: Tchau
"""
TRAINERS_YAML = """
treino:
  name: Treino de teste
  teaches:
    - { skill: onda, price: 30, stats: { spirit: 3 } }
    - { skill: foco, skills: [onda] }
    - { skill: leque }
    - { skill: chute, if: { total_level: 10, quest_done: q1 } }
"""
# skill sem restrição de raça/classe, só para os testes
EXTRA_SKILL = """
chute:
  name: Chute
  type: melee
  cooldown: 1
  reach: 20
  size: 14
  damage: { base: 3 }
"""
QUESTS_YAML = """
q1:
  name: Missão um
  objectives: [ { flag: feito, label: Fazer } ]
"""


def make_content(trainers: str = TRAINERS_YAML, npcs: str = NPCS_YAML, extra: dict | None = None) -> Content:
    tmp = Path(tempfile.mkdtemp())
    try:
        for path in C.DATA_DIR.glob("*.yaml"):
            shutil.copy(path, tmp / path.name)
        with open(tmp / "skills.yaml", "a", encoding="utf-8") as f:
            f.write(EXTRA_SKILL)
        (tmp / "npcs.yaml").write_text(npcs, encoding="utf-8")
        (tmp / "trainers.yaml").write_text(trainers, encoding="utf-8")
        (tmp / "quests.yaml").write_text(QUESTS_YAML, encoding="utf-8")  # as missões reais citam outros NPCs
        (tmp / "crafting.yaml").write_text("", encoding="utf-8")  # as receitas reais citam missões reais
        for name, text in (extra or {}).items():
            with open(tmp / name, "a", encoding="utf-8") as f:
                f.write(text)
        return dataclasses.replace(Content.load(tmp), rules=())
    finally:
        shutil.rmtree(tmp)


def make_world(db: Database | None = None) -> World:
    tilemap = parse_map(MAP, "teste", CONTENT.enemy_by_mark, zones={"npcs": [{"id": "mestre", "at": [4, 2]}]})
    return World(tilemap, db or Database(":memory:"), make_content(), rng=random.Random(1))


def run(world: World, seconds: float) -> None:
    for _ in range(int(seconds / DT)):
        world.step(DT)


class Base(unittest.TestCase):
    def setUp(self):
        self.world = make_world()
        self.p, _ = self.world.login("Teste", "1234", "humano")
        self.npc = next(iter(self.world.npcs.entities.values()))
        self.p.x, self.p.y = self.npc.x - 40, self.npc.y
        self.world.drain_outbox()

    def out(self) -> list[dict]:
        return [m for target, m in self.world.drain_outbox() if target in (self.p.id, None)]

    def texts(self, msgs: list[dict]) -> list[str]:
        return [m["text"] for m in msgs if m["t"] == "sys"]

    def ask(self, **msg) -> list[dict]:
        self.world.handle_message(self.p, {"t": "npc", **msg})
        return self.out()

    def open_trainer(self) -> dict:
        self.ask(op="talk", id=self.npc.id)
        msgs = self.ask(op="choose", i=0)
        return next(m for m in msgs if m["t"] == "trainer")

    def bar(self, slot, skill_id) -> list[dict]:
        self.world.handle_message(self.p, {"t": "skills", "op": "bar", "slot": slot, "id": skill_id})
        return self.out()

    @staticmethod
    def row(trainer: dict, skill_id: str) -> dict:
        return next(r for r in trainer["teaches"] if r["id"] == skill_id)


class StartAndSaveTests(Base):
    def test_new_character_knows_start_skills(self):
        start = list(CONTENT.classes["aprendiz"].start_skills)
        self.assertEqual(self.p.known, start)
        self.assertEqual(self.p.bar, start + [None] * (C.BAR_SIZE - len(start)))

    def test_known_and_bar_survive_relogin(self):
        self.world.training.learn(self.p, "onda")
        self.p.bar = [None, "onda", "golpe"] + [None] * (C.BAR_SIZE - 3)
        self.world.logout(self.p)
        p, err = self.world.login("Teste", "1234")
        self.assertIsNone(err)
        self.assertIn("onda", p.known)
        self.assertEqual(p.bar, [None, "onda", "golpe"] + [None] * (C.BAR_SIZE - 3))
        # o que o jogador tirou da barra continua conhecido e não volta sozinho
        self.assertIn("rajada", p.known)

    def test_empty_bar_gets_start_skills(self):
        self.p.bar = [None] * C.BAR_SIZE
        self.world._set_class(self.p, self.p.class_def)
        self.assertEqual(self.p.bar_skills, list(CONTENT.classes["aprendiz"].start_skills))

    def test_v5_save_keeps_old_class_skills(self):
        data = migrate({"v": 5, "race": "humano", "class": "aprendiz"})
        self.assertGreaterEqual(data["v"], 6)  # migra até a versão atual
        self.assertEqual(data["skills"]["known"], ["golpe", "rajada", "passo", "onda", "foco"])
        self.assertEqual(data["skills"]["bar"], ["golpe", "rajada", "passo", "onda", "foco"])
        self.assertEqual(migrate({"v": 5, "class": "sumiu"})["skills"], {"known": [], "bar": []})

    def test_corrupt_save_is_cleaned(self):
        p = self.p
        self.world.training.load(p, {"known": ["golpe", "golpe", 3, "nao_existe", "onda"],
                                     "bar": ["onda", "nao_existe", "garra", 7] + ["golpe"] * 10})
        self.world._set_class(p, p.class_def)
        self.assertEqual(p.known[:2], ["golpe", "onda"])
        self.assertNotIn("nao_existe", p.known)
        self.assertEqual(len(p.bar), C.BAR_SIZE)
        self.assertEqual(p.bar[0], "onda")
        self.assertEqual(p.bar.count("golpe"), 1)
        self.assertNotIn("garra", p.bar)  # skill de inimigo que não conhece

    def test_known_goes_to_client_only_when_changed(self):
        run(self.world, DT)
        snaps = [m for m in self.out() if m["t"] == "snap"]
        self.assertEqual([k["id"] for k in snaps[-1]["you"]["known"]], self.p.known)
        run(self.world, DT)
        self.assertNotIn("known", [m for m in self.out() if m["t"] == "snap"][-1]["you"])
        self.world.training.learn(self.p, "onda")
        run(self.world, DT)
        self.assertIn("known", [m for m in self.out() if m["t"] == "snap"][-1]["you"])


class BarTests(Base):
    def test_place_swap_and_clear(self):
        self.assertEqual(self.p.bar[:3], ["golpe", "rajada", "passo"])
        self.bar(5, "golpe")
        self.assertEqual(self.p.bar[0], None)
        self.assertEqual(self.p.bar[5], "golpe")
        self.bar(1, "golpe")  # já estava no 5: troca com o que estava no 1
        self.assertEqual(self.p.bar[1], "golpe")
        self.assertEqual(self.p.bar[5], "rajada")
        self.bar(1, None)
        self.assertNotIn("golpe", self.p.bar)

    def test_only_known_usable_skills_and_valid_slots(self):
        before = list(self.p.bar)
        for slot, skill_id in ((0, "onda"), (0, "garra"), (0, "nao_existe"), (C.BAR_SIZE, "golpe"), (-1, "golpe"),
                               (True, "golpe"), ("0", "golpe"), (0, 5)):
            self.bar(slot, skill_id)
        self.assertEqual(self.p.bar, before)
        self.p.known.append("leque")  # conhece, mas Humano não pode usar
        self.bar(4, "leque")
        self.assertEqual(self.p.bar, before)

    def test_skill_not_on_bar_cannot_be_used(self):
        self.bar(0, None)
        self.world.handle_message(self.p, {"t": "skill", "id": "golpe"})
        self.assertEqual(self.p.actions, [])

    def test_combat_locks_bar(self):
        enemy = next(iter(self.world.enemies.values()), None)
        self.world._damage(self.p, 1, enemy)
        msgs = self.bar(5, "golpe")
        self.assertEqual(self.p.bar[5], None)
        self.assertTrue(any("Em combate" in t for t in self.texts(msgs)))
        self.world.time += C.BAR_COMBAT_LOCK + 0.1
        self.bar(5, "golpe")
        self.assertEqual(self.p.bar[5], "golpe")

    def test_attacking_locks_bar(self):
        self.world.handle_message(self.p, {"t": "skill", "id": "golpe"})
        run(self.world, DT)
        self.bar(5, "rajada")
        self.assertEqual(self.p.bar[5], None)

    def test_duel_locks_bar(self):
        other, _ = self.world.login("Outro", "1234", "humano")
        other.x, other.y = self.p.x + 30, self.p.y
        self.world.duels.challenge(self.p, "Outro")
        self.world.duels.accept(other)
        self.bar(5, "golpe")
        self.assertEqual(self.p.bar[5], None)

    def test_safe_zone_only(self):
        old = C.BAR_SAFE_ZONE_ONLY
        C.BAR_SAFE_ZONE_ONLY = True
        try:
            self.p.x, self.p.y = 6 * C.TILE_SIZE + 16, 2 * C.TILE_SIZE + 16  # chão comum
            self.bar(5, "golpe")
            self.assertEqual(self.p.bar[5], None)
            self.p.x, self.p.y = 2 * C.TILE_SIZE + 16, 1 * C.TILE_SIZE + 16  # tile ':'
            self.bar(5, "golpe")
            self.assertEqual(self.p.bar[5], "golpe")
        finally:
            C.BAR_SAFE_ZONE_ONLY = old


class ClassChangeTests(Base):
    def test_transformation_keeps_known_and_locks_invalid(self):
        p = self.p
        self.world.training.learn(p, "onda")  # só Humano/Desperto
        self.world._do_action(p, "set_race", {"race": "espectral", "class": "assombracao"}, {}, 0)
        self.assertIn("onda", p.known)
        self.assertIn("dardo", p.known)  # inicial da classe nova
        self.assertNotIn("onda", p.bar)
        self.assertIn("dardo", p.bar)
        info = {k["id"]: k["ok"] for k in self.world.training.client_info(p)}
        self.assertFalse(info["onda"])
        self.assertTrue(info["dardo"])
        # voltar a Humano: onda volta a funcionar (fica fora da barra até o jogador pôr)
        self.world._do_action(p, "set_race", {"race": "humano", "class": "aprendiz"}, {}, 0)
        self.assertTrue(self.world.training.usable(p, "onda"))
        self.assertFalse(self.world.training.usable(p, "dardo"))

    def test_reload_forgets_removed_skills(self):
        self.world.training.learn(self.p, "onda")
        content = self.world.content
        skills = {k: v for k, v in content.skills.items() if k != "onda"}
        trainers = {}  # o treinador de teste cita onda
        new = dataclasses.replace(content, skills=skills, trainers=trainers)
        self.world.reload(new, self.world.maps)
        self.assertNotIn("onda", self.p.known)
        self.assertNotIn("onda", self.p.bar)


class LearnActionTests(Base):
    def test_learn_skill_action_and_message(self):
        self.world._do_action(self.p, "learn_skill", "onda", {}, 0)
        self.assertIn("onda", self.p.known)
        self.assertIn("onda", self.p.bar)
        self.assertTrue(any("Você aprendeu Onda de choque" in t for t in self.texts(self.out())))
        self.world._do_action(self.p, "learn_skill", "onda", {}, 0)  # de novo: nada acontece
        self.assertEqual(self.p.known.count("onda"), 1)

    def test_learned_skill_waits_when_bar_is_full(self):
        self.p.known += ["chute"]
        self.p.bar = ["golpe"] * C.BAR_SIZE  # cheia (o conteúdo não importa aqui)
        self.world.training.learn(self.p, "onda")
        self.assertIn("onda", self.p.known)
        self.assertNotIn("onda", self.p.bar)

    def test_reward_label(self):
        quest = dataclasses.replace(next(iter(self.world.content.quests.values())),
                                    rule=Rule(id="x", when="quest_complete", conditions=(),
                                              actions=(("learn_skill", "onda"),)))
        self.assertEqual(self.world.content.reward_labels(quest), ["Skill: Onda de choque"])


class TrainerTests(Base):
    def test_trainer_lists_requirements(self):
        t = self.open_trainer()
        self.assertEqual(t["trainer"], "Treino de teste")
        self.assertEqual(t["name"], "Mestre")
        onda = self.row(t, "onda")
        self.assertEqual(onda["price"], 30)
        self.assertFalse(onda["known"])
        self.assertEqual(onda["missing"], ["Espírito nível 3 (você: 1)"])
        self.assertEqual(self.row(t, "foco")["missing"], ["Conhecer Onda de choque"])
        self.assertEqual(self.row(t, "leque")["missing"], ["Só para: Desperto, Espectral"])
        self.assertEqual(self.row(t, "chute")["missing"], ["Nível total 10 (você: 4)", "Concluir a missão Missão um"])

    def test_learn_after_meeting_requirements(self):
        self.open_trainer()
        msgs = self.ask(op="learn", skill="onda")
        self.assertNotIn("onda", self.p.known)
        self.assertTrue(any("Ainda não dá" in t for t in self.texts(msgs)))
        self.p.stats["spirit"] = Stat(level=3)
        msgs = self.ask(op="learn", skill="onda")
        self.assertNotIn("onda", self.p.known)
        self.assertTrue(any("Faltam 30 moedas" in t for t in self.texts(msgs)))
        self.p.inventory.add_gold(50)
        msgs = self.ask(op="learn", skill="onda")
        self.assertIn("onda", self.p.known)
        self.assertEqual(self.p.inventory.gold, 20)
        self.assertEqual(self.p.bar[3], "onda")  # primeiro espaço vazio
        refreshed = next(m for m in msgs if m["t"] == "trainer")
        self.assertTrue(self.row(refreshed, "onda")["known"])
        self.assertEqual(self.row(refreshed, "foco")["missing"], [])  # agora conhece a anterior
        self.ask(op="learn", skill="onda")  # de novo: não cobra
        self.assertEqual(self.p.inventory.gold, 20)

    def test_free_skill_with_prerequisite(self):
        self.p.known.append("onda")
        self.open_trainer()
        self.ask(op="learn", skill="foco")
        self.assertIn("foco", self.p.known)

    def test_learn_needs_open_trainer_and_listed_skill(self):
        self.p.stats["spirit"] = Stat(level=5)
        self.p.inventory.add_gold(100)
        self.ask(op="learn", skill="onda")  # sem conversa
        self.assertNotIn("onda", self.p.known)
        self.open_trainer()
        for bad in ("rajada_do_vazio", None, 3, ["onda"]):
            self.ask(op="learn", skill=bad)
        self.assertEqual(self.p.known, list(CONTENT.classes["aprendiz"].start_skills))
        self.p.x += 200  # longe demais
        self.ask(op="learn", skill="onda")
        self.assertNotIn("onda", self.p.known)


class TrainerContentTests(unittest.TestCase):
    def assert_error(self, *fragments, trainers=TRAINERS_YAML, npcs=NPCS_YAML, extra=None):
        with self.assertRaises(ContentError) as ctx:
            make_content(trainers, npcs, extra)
        for fragment in fragments:
            self.assertIn(fragment, str(ctx.exception))

    def test_real_content(self):
        self.assertIn("treino_campos", CONTENT.trainers)
        for cls in CONTENT.classes.values():
            for sid in cls.start_skills:
                self.assertTrue(CONTENT.skills[sid].usable_by(cls.race, cls.id))
        self.assertEqual(CONTENT.class_skill_pool("aprendiz"), ["golpe", "rajada", "passo", "onda", "foco"])

    def test_trainer_validation(self):
        self.assert_error("trainers.yaml > t.teaches[0]", "nao_existe",
                          trainers="t:\n  name: T\n  teaches: [nao_existe]\n")
        self.assert_error("duas vezes", trainers="t:\n  name: T\n  teaches: [onda, onda]\n")
        self.assert_error("atributo 'sorte'", trainers="t:\n  name: T\n  teaches: [{skill: onda, stats: {sorte: 1}}]\n")
        self.assert_error("requisito dela mesma", trainers="t:\n  name: T\n  teaches: [{skill: onda, skills: [onda]}]\n")
        self.assert_error("pelo menos uma", trainers="t:\n  name: T\n  teaches: []\n")
        self.assert_error("campo 'zone'", trainers="t:\n  name: T\n  teaches: [{skill: onda, if: {zone: pvp}}]\n")
        self.assert_error("preco", trainers="t:\n  name: T\n  teaches: [{skill: onda, preco: 3}]\n")

    def test_npc_trainer_reference(self):
        npcs = NPCS_YAML.replace("trainer: treino", "trainer: sumiu")
        self.assert_error("treinador 'sumiu'", npcs=npcs)
        npcs = NPCS_YAML.replace("trainer: treino", "trainer: treino\n          goto: inicio")
        self.assert_error("só um de goto, shop, trainer, craft ou bank", npcs=npcs)

    def test_skill_and_class_validation(self):
        self.assert_error("raça 'anao'", extra={"skills.yaml": "x:\n  name: X\n  type: self\n  cooldown: 1\n"
                                                                 "  races: [anao]\n"})
        self.assert_error("classe 'mago'", extra={"skills.yaml": "x:\n  name: X\n  type: self\n  cooldown: 1\n"
                                                                  "  classes: [mago]\n"})
        self.assert_error("classes.yaml > x", "skill inicial 'leque'",
                          extra={"classes.yaml": "x:\n  name: X\n  race: humano\n  start_skills: [leque]\n"})

    def test_learn_skill_action_validation(self):
        self.assert_error("skill 'nao_existe'",
                          npcs=NPCS_YAML.replace("        - text: Tchau",
                                                 "        - text: Tchau\n          do: [ { learn_skill: nao_existe } ]"))


if __name__ == "__main__":
    unittest.main()
