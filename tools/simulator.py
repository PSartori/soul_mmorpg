"""Lutas simuladas para balanceamento, rodando dentro do próprio motor do jogo.

Nada de fórmulas copiadas: as lutas acontecem num World de verdade, numa arena
vazia, com os mesmos cálculos de dano, recarga, energia, efeitos, atordoamento e
IA dos inimigos. Para medir taxas de dano sem ninguém morrer, a vida dos dois
lados é restaurada a cada tick e os atributos são congelados (o treino durante a
luta não muda o resultado).

Política de cada jogador (a "rotação"):
  1. usa um buff próprio (skill "self") se estiver pronto e o efeito não estiver ativo;
  2. usa, entre as skills de dano prontas e que caibam na energia, a de maior dano
     esperado por uso (dano × projéteis + dano total dos efeitos periódicos).
Dash não entra na rotação. Os dois lutam parados, virados um para o outro, a 28 px.
"""
from __future__ import annotations

import copy
import dataclasses
import random
from collections import defaultdict
from dataclasses import dataclass, field

from server import config as C
from server.content import Content, SkillDef
from server.db import Database
from server.entities import Enemy, Entity, Player
from server.stats import STAT_NAMES, Stat
from server.tilemap import parse_map
from server.world import World, _face

DT = 1.0 / C.TICK_RATE
GAP = 28.0  # distância entre os lutadores (cabe golpe corpo a corpo)
HUGE_HP = 1e9  # vida "infinita" durante a medição
DUMMY_ID = "__alvo__"  # alvo de treino: inimigo parado que não ataca
DAMAGING_TYPES = ("melee", "projectile", "area")

ARENA = """
##############################
#S...........................#
#............................#
#............................#
#............................#
#............................#
##############################
"""
ARENA_ZONES = {"default_zone": {"type": "pvp", "name": "Arena"}}
ARENA_SPOT = (15.5 * C.TILE_SIZE, 3.5 * C.TILE_SIZE)


@dataclass(frozen=True)
class Build:
    """Um personagem para simular: classe, nível de todos os atributos e equipamento."""

    class_id: str
    level: int
    equipment: tuple[str, ...] = ()


@dataclass
class Side:
    name: str
    max_hp: float = 0.0
    damage: float = 0.0
    by_source: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    uses: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    skipped_equipment: list[str] = field(default_factory=list)


@dataclass
class FightResult:
    seconds: float
    a: Side
    b: Side

    def dps(self, side: Side) -> float:
        return side.damage / self.seconds

    def time_to_kill(self, attacker: Side, defender: Side) -> float:
        """Segundos para o atacante zerar a vida do defensor nesse ritmo (inf = nunca)."""
        rate = self.dps(attacker)
        return defender.max_hp / rate if rate > 0 else float("inf")


class Simulator:
    def __init__(self, content: Content, seed: int = 1):
        # As regras (data/rules.yaml) não entram: ninguém morre de verdade aqui.
        self.content = dataclasses.replace(content, rules=())
        self.seed = seed

    # ------------------------------------------------------------ montagem

    def _world(self) -> World:
        tilemap = parse_map(ARENA, "arena", (), zones=ARENA_ZONES)
        return World(tilemap, Database(":memory:"), self.content, rng=random.Random(self.seed))

    def player(self, world: World, build: Build, name: str, x: float, y: float) -> tuple[Player, list[str]]:
        """Cria o personagem direto no mundo (sem login). Devolve também os itens que não serviram."""
        p = Player(id=world._new_id(), name=name, x=x, y=y, map_id=world.start_map_id)
        p.stats = {n: Stat(level=build.level) for n in STAT_NAMES}
        world._set_class(p, self.content.classes[build.class_id])
        # luta com tudo que a classe pode aprender (iniciais + treinadores), não só com as iniciais
        pool = self.content.class_skill_pool(build.class_id)
        p.known = list(pool)
        p.bar = (pool + [None] * C.BAR_SIZE)[: C.BAR_SIZE]
        skipped = []
        for item_id in build.equipment:
            item = self.content.items[item_id]
            if item.type == "equipment" and world._can_equip(p, item):
                p.inventory.equipment[item.slot] = item  # type: ignore[index]
            else:
                skipped.append(item_id)
        p.hp, p.energy = p.max_hp, p.max_energy
        world.players[p.id] = p
        return p, skipped

    def enemy(self, world: World, enemy_id: str, x: float, y: float, elite: bool = False, phase: int = 0) -> Enemy:
        """Inimigo do tipo pedido; `elite` e `phase` (chefes) fixos durante a luta."""
        defn = self.content.enemies[enemy_id]
        e = Enemy(id=world._new_id(), name=defn.name, x=x, y=y, defn=defn, home_x=x, home_y=y,
                  map_id=world.start_map_id, elite=elite, phase=phase)
        e.half = e.body_half()
        e.hp = e.max_hp
        world.enemies[e.id] = e
        return e

    # ------------------------------------------------------------ luta

    def fight(self, a: Build, b: Build | str, seconds: float = 30.0, elite: bool = False,
              phase: int = 0) -> FightResult:
        """a é sempre um jogador; b é outro jogador (Build) ou um tipo de inimigo (id).

        Para inimigos: `elite` e `phase` (fase de chefe, 0 = inicial) ficam fixos na luta
        (a vida é restaurada a cada tick, então a fase não muda sozinha). Invocados não entram.
        """
        world = self._world()
        x, y = ARENA_SPOT
        pa, skipped_a = self.player(world, a, "A", x, y)
        if isinstance(b, Build):
            pb, skipped_b = self.player(world, b, "B", x + GAP, y)
        else:
            pb, skipped_b = self.enemy(world, b, x + GAP, y, elite, phase), []
            pb.x += world._bulk(pb)  # corpo maior: o centro fica mais longe, as bordas a GAP
        side_a = Side("A", max_hp=pa.max_hp, skipped_equipment=skipped_a)
        side_b = Side("B", max_hp=pb.max_hp, skipped_equipment=skipped_b)
        sides = {pa.id: side_a, pb.id: side_b}

        # Mede o dano real (depois de todos os modificadores) a cada chamada de _damage
        original_damage = world._damage

        def tracking(target, amount, source, skill=None, effect_id=None):  # type: ignore[no-untyped-def]
            before = target.hp
            original_damage(target, amount, source, skill, effect_id)
            lost = before - target.hp
            side = sides.get(source.id) if source is not None else None
            if side is not None and lost > 0:
                side.damage += lost
                key = skill.id if skill else (f"efeito:{effect_id}" if effect_id else "?")
                side.by_source[key] += lost

        world._damage = tracking  # type: ignore[method-assign]

        frozen = {p.id: copy.deepcopy(p.stats) for p in (pa, pb) if isinstance(p, Player)}
        for _ in range(round(seconds / DT)):
            for fighter in (pa, pb):
                if not fighter.alive:  # não deveria acontecer; garante que a medição continue
                    fighter.state = "idle"
                fighter.hp = HUGE_HP
                if isinstance(fighter, Enemy):
                    fighter.target_id = pa.id  # não reage a vida "infinita" mudando de fase
            for me, other in ((pa, pb), (pb, pa)):
                if isinstance(me, Player):
                    _face(me, other.x - me.x, other.y - me.y)
                    self._act(world, me, sides[me.id])
            world.step(DT)
            world.drain_outbox()
            for p in (pa, pb):
                if isinstance(p, Player):
                    p.stats = copy.deepcopy(frozen[p.id])
        return FightResult(seconds=seconds, a=side_a, b=side_b)

    def _act(self, world: World, p: Player, side: Side) -> None:
        if not p.alive or p.has_flag("stunned") or p.has_flag("silenced"):
            return
        skills = [self.content.skills[sid] for sid in p.bar_skills]

        def ready(s: SkillDef) -> bool:
            return p.cooldowns.get(s.id, 0) <= 0 and p.energy >= s.cost_energy

        for s in skills:  # 1) buffs
            if s.type == "self" and s.self_effects and ready(s):
                if not any(app.effect in p.effects for app in s.self_effects) and world._use_skill(p, s, p.facing):
                    side.uses[s.id] += 1
        candidates = [s for s in skills if s.type in DAMAGING_TYPES and ready(s)]
        if candidates:  # 2) o golpe de maior dano esperado
            best = max(candidates, key=lambda s: self.expected_damage(world, p, s))
            if world._use_skill(p, best, p.facing):
                side.uses[best.id] += 1

    def expected_damage(self, world: World, p: Player, skill: SkillDef) -> float:
        direct = world._skill_damage(p, skill) * (skill.count if skill.type == "projectile" else 1)
        periodic = 0.0
        for app in skill.effects:
            effect = self.content.effects[app.effect]
            if effect.tick_every > 0:
                periodic += effect.tick_damage * int(app.duration / effect.tick_every)
        return direct + periodic

    # ------------------------------------------------------------ números estáticos

    def profile(self, build: Build) -> dict:
        """Vida, energia, velocidade e vida efetiva (vida ÷ multiplicador de dano recebido)."""
        world = self._world()
        p, skipped = self.player(world, build, "P", *ARENA_SPOT)
        return {
            "max_hp": p.max_hp,
            "max_energy": p.max_energy,
            "speed": p.speed,
            "effective_hp": p.max_hp / p.modifier("damage_taken"),
            "skills": [(self.content.skills[s], self.expected_damage(world, p, self.content.skills[s])) for s in p.bar_skills],
            "skipped": skipped,
        }

    def dummy_output(self, build: Build, seconds: float = 30.0) -> FightResult:
        """Dano contra um alvo parado que não reage (mostra a rotação pura)."""
        if DUMMY_ID not in self.content.enemies:
            base = next(iter(self.content.enemies.values()))
            target = dataclasses.replace(base, id=DUMMY_ID, name="Alvo", hp=1000.0, speed=0.0,
                                         skills=(), aggro_radius=0.0, loot=())
            self.content = dataclasses.replace(self.content, enemies={**self.content.enemies, DUMMY_ID: target})
        return self.fight(build, DUMMY_ID, seconds)
