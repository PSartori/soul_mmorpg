"""Conteúdo do jogo (efeitos, skills, inimigos) lido dos arquivos YAML em data/.

Tudo é validado ao carregar: campo desconhecido, tipo errado ou referência
inexistente gera ContentError dizendo arquivo, item e campo. Assim um erro de
digitação no YAML aparece na hora de subir o servidor, não no meio do jogo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from . import config as C
from .rules import (
    ACTIONS, DUEL_END_REASONS, DUEL_RESULTS, EVENT_FIELDS, KILLER_KINDS, RANK_CAUSES, VICTIM_KINDS, ZONES, Rule,
)
from .stats import STAT_LABELS, STAT_NAMES
from .tilemap import RESERVED_MARKS

SKILL_TYPES = ("melee", "projectile", "area", "dash", "self")
AI_SKILL_TYPES = ("melee", "projectile", "area", "self")
MODIFIERS = ("speed", "damage", "damage_taken", "energy_regen")
FLAGS = ("stunned", "silenced")
MAX_HOTBAR = C.BAR_SIZE  # barra de atalhos
RACE_BONUSES = ("max_hp", "max_energy", "speed")
ITEM_TYPES = ("equipment", "consumable", "material")
EQUIP_SLOTS = ("weapon", "armor", "accessory")
RARITIES = ("common", "uncommon", "rare", "epic")
PROMOTIONS = ("default", "appointment", "challenge")
PERMISSIONS = ("kick", "mute", "ban", "announce", "teleport", "give", "set_role", "log", "reload")
ID_RE = re.compile(r"[a-z0-9_]+")
# Campos que as condições ("if") das opções de diálogo e das missões podem usar
DIALOG_FIELDS = ("race", "class", "org", "rank", "gold", "total_level", "quest_done", "quest_active")
OBJECTIVE_KINDS = ("kill", "collect", "talk", "reach", "flag")
DEFAULT_BUY_RATE = C.SHOP_BUY_RATE

_REQUIRED = object()


class ContentError(ValueError):
    pass


# ============================================================== definições


@dataclass(frozen=True)
class EffectApplication:
    effect: str
    duration: float


@dataclass(frozen=True)
class EffectDef:
    id: str
    name: str
    color: str
    modifiers: dict[str, float]
    flags: frozenset[str]
    tick_every: float
    tick_damage: float
    tick_heal: float


@dataclass(frozen=True)
class SkillDef:
    id: str
    name: str
    type: str
    color: str
    cooldown: float
    cost_energy: float
    damage_base: float
    damage_scale: dict[str, float]
    train: dict[str, float]
    effects: tuple[EffectApplication, ...]
    self_effects: tuple[EffectApplication, ...]
    # específicos por tipo (0 quando não se aplica)
    reach: float = 0.0
    size: float = 0.0
    speed: float = 0.0
    ttl: float = 0.0
    radius: float = 0.0
    count: int = 1
    spread: float = 0.0
    distance: float = 0.0
    # só para jogadores: texto da janela de skills e quem pode usar (vazio = todos)
    description: str = ""
    races: tuple[str, ...] = ()
    classes: tuple[str, ...] = ()

    def usable_by(self, race: str | None, class_id: str | None) -> bool:
        """A raça/classe pode usar esta skill? (restrições races/classes de skills.yaml)"""
        return (not self.races or race in self.races) and (not self.classes or class_id in self.classes)

    @property
    def ai_range(self) -> float:
        """Distância (centro a centro) a partir da qual a IA usa esta skill."""
        if self.type == "melee":
            return self.reach + self.size * 0.5
        if self.type == "projectile":
            return self.speed * self.ttl * 0.8
        if self.type == "area":
            return self.radius
        return float("inf")  # self: pode usar a qualquer distância

    def client_info(self) -> dict:
        return {"name": self.name, "type": self.type, "cd": self.cooldown, "cost": self.cost_energy, "color": self.color,
                "desc": self.description, "races": list(self.races), "classes": list(self.classes)}


@dataclass(frozen=True)
class BossPhase:
    """Fase de um chefe: começa quando a vida cai até hp_below (fração da vida máxima)."""

    hp_below: float
    skills: tuple[str, ...] | None = None  # troca as skills (None = mantém as anteriores)
    damage: float = 1.0  # multiplicador de dano a partir desta fase (sobre o damage do inimigo)
    speed: float = 1.0  # multiplicador de velocidade a partir desta fase
    message: str = ""  # aviso para quem está perto
    summon: tuple[tuple[str, int], ...] = ()  # (inimigo, quantos) que aparecem em volta
    effects: tuple[EffectApplication, ...] = ()  # efeitos aplicados no chefe ao entrar na fase


@dataclass(frozen=True)
class EnemyDef:
    id: str
    name: str
    mark: str
    color: str
    hp: float
    speed: float
    aggro_radius: float
    leash: float
    wander_radius: float
    respawn: float
    kill_xp: float
    skills: tuple[str, ...]
    gold: tuple[int, int] = (0, 0)  # moedas que caem: [mínimo, máximo]
    loot: tuple[LootEntry, ...] = ()
    level: int = 1  # nível mostrado ao jogador (comparável ao nível dos atributos)
    damage: float = 1.0  # multiplica o dano das skills (inimigos não têm atributos)
    size: float = 1.0  # tamanho do corpo (1 = igual a um jogador)
    elite_chance: float = 0.0  # chance de nascer elite (mais forte e com mais recompensa)
    boss: bool = False
    phases: tuple[BossPhase, ...] = ()  # do primeiro limite de vida para o último


@dataclass(frozen=True)
class LootEntry:
    item: str
    chance: float  # 0–1
    qty_min: int
    qty_max: int


@dataclass(frozen=True)
class ItemDef:
    id: str
    name: str
    type: str  # equipment | consumable | material
    rarity: str
    description: str
    stack: int  # quantos cabem num espaço do inventário
    value: int  # valor em moedas (para lojas, no futuro)
    # equipamento
    slot: str | None = None
    bonus: dict[str, float] = field(default_factory=dict)  # max_hp, max_energy, speed (somados)
    modifiers: dict[str, float] = field(default_factory=dict)  # multiplicadores, como nos efeitos
    races: tuple[str, ...] = ()  # vazio = qualquer raça
    classes: tuple[str, ...] = ()  # vazio = qualquer classe
    # consumível
    heal: float = 0.0
    energy: float = 0.0
    effects: tuple[EffectApplication, ...] = ()
    cooldown: float = 0.0

    def client_info(self) -> dict:
        return {
            "name": self.name, "type": self.type, "rarity": self.rarity, "desc": self.description,
            "stack": self.stack, "value": self.value, "slot": self.slot, "bonus": self.bonus,
            "mods": self.modifiers, "races": list(self.races), "classes": list(self.classes),
            "heal": self.heal, "energy": self.energy, "effects": [a.effect for a in self.effects],
            "cd": self.cooldown,
        }


@dataclass(frozen=True)
class RankDef:
    id: str
    name: str
    seats: int | None  # None = ilimitado
    numbered: bool
    leader: bool
    promotion: str  # default | appointment | challenge
    min_total_level: int


@dataclass(frozen=True)
class OrgDef:
    id: str
    name: str
    description: str
    color: str
    races: tuple[str, ...]
    min_total_level: int
    default_rank: str
    ranks: tuple[RankDef, ...]  # do mais alto para o mais baixo

    def rank(self, rank_id: str) -> RankDef | None:
        return next((r for r in self.ranks if r.id == rank_id), None)

    def rank_index(self, rank_id: str) -> int:
        """Posição na hierarquia: 0 = mais alto."""
        return next(i for i, r in enumerate(self.ranks) if r.id == rank_id)

    def client_info(self) -> dict:
        return {"name": self.name, "color": self.color, "description": self.description,
                "races": list(self.races), "min": self.min_total_level, "default_rank": self.default_rank,
                # na ordem da hierarquia (do mais alto para o mais baixo)
                "ranks": {r.id: {"name": r.name, "numbered": r.numbered, "promotion": r.promotion,
                                 "leader": r.leader, "seats": r.seats, "min": r.min_total_level}
                          for r in self.ranks}}


@dataclass(frozen=True)
class RoleDef:
    id: str
    name: str
    tag: str
    color: str
    level: int
    permissions: frozenset[str]

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def client_info(self) -> dict:
        return {"name": self.name, "tag": self.tag, "color": self.color, "level": self.level,
                "permissions": sorted(self.permissions)}


@dataclass(frozen=True)
class RaceDef:
    id: str
    name: str
    description: str
    selectable: bool
    start_class: str
    bonus: dict[str, float]  # max_hp, max_energy, speed


@dataclass(frozen=True)
class ClassDef:
    id: str
    name: str
    race: str
    description: str
    start_skills: tuple[str, ...]  # aprendidas ao entrar na classe; montam a barra inicial
    train_rate: dict[str, float]


@dataclass(frozen=True)
class ShopEntry:
    item: str
    price: int  # quanto o jogador paga


@dataclass(frozen=True)
class ShopDef:
    id: str
    name: str
    sells: tuple[ShopEntry, ...]  # o que o jogador pode comprar (estoque infinito)
    buys: tuple[str, ...]  # o que a loja compra do jogador: "all", tipos de item e/ou ids
    buy_rate: float  # fração do valor (value) do item que a loja paga

    def buy_price(self, item: ItemDef) -> int:
        """Quanto a loja paga por uma unidade do item (0 = não compra)."""
        if item.value <= 0 or not ("all" in self.buys or item.type in self.buys or item.id in self.buys):
            return 0
        return max(1, int(item.value * self.buy_rate))

    def sell_price(self, item_id: str) -> int | None:
        return next((e.price for e in self.sells if e.item == item_id), None)


@dataclass(frozen=True)
class TrainerEntry:
    skill: str
    price: int  # moedas para aprender (0 = de graça)
    stats: dict[str, int]  # nível mínimo de cada atributo
    skills: tuple[str, ...]  # skills que precisa já conhecer
    rule: Rule  # condições extras (as mesmas dos diálogos: total_level, quest_done, flags...)


@dataclass(frozen=True)
class TrainerDef:
    id: str
    name: str
    teaches: tuple[TrainerEntry, ...]

    def entry(self, skill_id: str) -> TrainerEntry | None:
        return next((e for e in self.teaches if e.skill == skill_id), None)


@dataclass(frozen=True)
class DialogOption:
    text: str
    rule: Rule  # condições (quando a opção aparece) e ações (o que acontece ao escolher)
    goto: str | None = None  # nó seguinte; sem goto, shop nem trainer, a conversa termina
    shop: str | None = None  # abre esta loja
    trainer: str | None = None  # abre este treinador (data/trainers.yaml)


@dataclass(frozen=True)
class DialogNode:
    id: str
    text: str
    options: tuple[DialogOption, ...]


@dataclass(frozen=True)
class NpcDef:
    id: str
    name: str
    title: str  # aparece abaixo do nome (ex.: Ferreiro)
    color: str
    start: str  # nó onde a conversa começa (o primeiro do arquivo)
    nodes: dict[str, DialogNode]

    def client_info(self) -> dict:
        return {"name": self.name, "title": self.title, "color": self.color}


@dataclass(frozen=True)
class QuestObjective:
    kind: str  # kill | collect | talk | reach | flag
    target: str  # inimigo, item, NPC, mapa ou marca
    count: int = 1  # quantos (kill e collect)
    area: tuple[int, int, int, int] | None = None  # reach: área em tiles (None = o mapa todo)
    label: str = ""  # texto do objetivo; vazio = gerado (ex.: "Derrotar Espectro")

    @property
    def live(self) -> bool:
        """Progresso medido na hora (itens na bolsa, marca) em vez de contado."""
        return self.kind in ("collect", "flag")


@dataclass(frozen=True)
class QuestDef:
    id: str
    name: str
    description: str
    objectives: tuple[QuestObjective, ...]
    rule: Rule  # condições para a missão aparecer e recompensas (ações) ao concluir
    giver: str | None  # NPC que oferece (None = só por ação start_quest)
    turn_in: str | None  # NPC a quem se entrega (None = conclui sozinha quando os objetivos terminam)
    complete_text: str
    repeatable: bool
    cooldown: float  # segundos (horário real) até poder repetir


@dataclass(frozen=True)
class Content:
    effects: dict[str, EffectDef]
    skills: dict[str, SkillDef]
    enemies: dict[str, EnemyDef]
    races: dict[str, RaceDef]
    classes: dict[str, ClassDef]
    rules: tuple[Rule, ...]
    default_race: str
    items: dict[str, ItemDef] = field(default_factory=dict)
    orgs: dict[str, OrgDef] = field(default_factory=dict)
    roles: dict[str, RoleDef] = field(default_factory=dict)
    shops: dict[str, ShopDef] = field(default_factory=dict)
    npcs: dict[str, NpcDef] = field(default_factory=dict)
    quests: dict[str, QuestDef] = field(default_factory=dict)
    trainers: dict[str, TrainerDef] = field(default_factory=dict)

    @property
    def enemy_by_mark(self) -> dict[str, EnemyDef]:
        return {e.mark: e for e in self.enemies.values()}

    @cached_property
    def rules_by_event(self) -> dict[str, tuple[Rule, ...]]:
        return {event: tuple(r for r in self.rules if r.when == event) for event in EVENT_FIELDS}

    def class_skill_pool(self, class_id: str) -> list[str]:
        """Tudo que a classe pode chegar a usar: as iniciais e as de treinadores que ela pode usar.

        Serve às ferramentas (balanceamento); não confere requisitos de nível nem preço.
        """
        cls = self.classes[class_id]
        taught = [e.skill for t in self.trainers.values() for e in t.teaches]
        return [sid for sid in dict.fromkeys([*cls.start_skills, *taught])
                if self.skills[sid].usable_by(cls.race, cls.id)]

    def selectable_races(self) -> list[dict]:
        return [
            {"id": r.id, "name": r.name, "description": r.description}
            for r in self.races.values()
            if r.selectable
        ]

    def client_payload(self) -> dict:
        return {
            "skills": {sid: s.client_info() for sid, s in self.skills.items()},
            "effects": {eid: {"name": e.name, "color": e.color} for eid, e in self.effects.items()},
            "enemies": {e.id: {"color": e.color, "phases": len(e.phases)} for e in self.enemies.values()},
            "races": {r.id: {"name": r.name} for r in self.races.values()},
            "classes": {c.id: {"name": c.name, "race": c.race} for c in self.classes.values()},
            "stat_names": {s: STAT_LABELS[s] for s in STAT_NAMES},
            "items": {i.id: i.client_info() for i in self.items.values()},
            "orgs": {o.id: o.client_info() for o in self.orgs.values()},
            "roles": {r.id: r.client_info() for r in self.roles.values()},
            "npcs": {n.id: n.client_info() for n in self.npcs.values()},
            "quests": {q.id: self.quest_client_info(q) for q in self.quests.values()},
        }

    def objective_label(self, obj: QuestObjective) -> str:
        if obj.label:
            return obj.label
        # .get: um conteúdo montado à mão (testes) pode não ter passado pela validação
        if obj.kind == "kill":
            return f"Derrotar {getattr(self.enemies.get(obj.target), 'name', obj.target)}"
        if obj.kind == "collect":
            return f"Conseguir {getattr(self.items.get(obj.target), 'name', obj.target)}"
        return f"Falar com {getattr(self.npcs.get(obj.target), 'name', obj.target)}"  # reach/flag têm label

    def reward_labels(self, quest: QuestDef) -> list[str]:
        """Recompensas que dá para mostrar ao jogador (itens, moedas, XP)."""
        labels = []
        for name, args in quest.rule.actions:
            if name == "give_item":
                item_name = getattr(self.items.get(args["id"]), "name", args["id"])
                labels.append(f"{args['qty']}× {item_name}" if args["qty"] > 1 else item_name)
            elif name == "give_gold":
                labels.append(f"{args} moedas")
            elif name == "gain_xp":
                labels.append(f"{args['amount']:g} XP de {STAT_LABELS[args['stat']]}")
            elif name == "learn_skill":
                labels.append(f"Skill: {getattr(self.skills.get(args), 'name', args)}")
        return labels

    def quest_client_info(self, q: QuestDef) -> dict:
        npc_name = lambda npc_id: getattr(self.npcs.get(npc_id), "name", npc_id) if npc_id else None  # noqa: E731
        return {
            "name": q.name, "description": q.description, "giver": npc_name(q.giver),
            "turn_in": npc_name(q.turn_in), "repeatable": q.repeatable,
            "objectives": [{"label": self.objective_label(o), "count": o.count, "kind": o.kind}
                           for o in q.objectives],
            "rewards": self.reward_labels(q),
        }

    def check_maps(self, maps: dict) -> None:
        """Confere o que o conteúdo diz sobre mapas (objetivos reach) contra os mapas carregados."""
        for q in self.quests.values():
            for i, obj in enumerate(q.objectives):
                if obj.kind != "reach":
                    continue
                where = f"quests.yaml > {q.id}.objectives[{i}]"
                tilemap = maps.get(obj.target)
                if tilemap is None:
                    raise ContentError(f"{where}: mapa {obj.target!r} não existe (mapas: {', '.join(maps)})")
                if obj.area:
                    x0, y0, x1, y1 = obj.area
                    if not (0 <= x0 <= x1 < tilemap.width and 0 <= y0 <= y1 < tilemap.height):
                        raise ContentError(f"{where}: area {list(obj.area)} fora do mapa {obj.target} "
                                           f"({tilemap.width}×{tilemap.height}) ou invertida")

    @classmethod
    def load(cls, data_dir: str | Path) -> Content:
        data_dir = Path(data_dir)
        effects = {i: _parse_effect(i, raw) for i, raw in _load_yaml(data_dir / "effects.yaml").items()}
        skills = {i: _parse_skill(i, raw) for i, raw in _load_yaml(data_dir / "skills.yaml").items()}
        enemies = {i: _parse_enemy(i, raw) for i, raw in _load_yaml(data_dir / "enemies.yaml").items()}
        races = {i: _parse_race(i, raw) for i, raw in _load_yaml(data_dir / "races.yaml").items()}
        classes = {i: _parse_class(i, raw) for i, raw in _load_yaml(data_dir / "classes.yaml").items()}
        items = {i: _parse_item(i, raw) for i, raw in _load_yaml(data_dir / "items.yaml").items()}
        orgs = {i: _parse_org(i, raw) for i, raw in _load_yaml(data_dir / "organizations.yaml").items()}
        roles = {i: _parse_role(i, raw) for i, raw in _load_yaml(data_dir / "roles.yaml").items()}

        player = _Fields("player.yaml", _load_yaml(data_dir / "player.yaml"))
        default_race = player.text("default_race")
        player.done()

        # Regras, missões e NPCs por último: condições e ações são conferidas contra todo o resto.
        # Missões, NPCs e regras se referem umas às outras, então os ids das missões vêm antes.
        quests_raw = _load_yaml(data_dir / "quests.yaml", True)
        refs = _RuleRefs(effects=effects, skills=skills, enemies=enemies, races=races, classes=classes, items=items,
                         orgs=orgs, quests=tuple(quests_raw))
        rules = tuple(_parse_rule(i, raw, refs) for i, raw in _load_yaml(data_dir / "rules.yaml").items())
        # lojas, NPCs e missões são opcionais (o arquivo pode não existir)
        shops = {i: _parse_shop(i, raw, items) for i, raw in _load_yaml(data_dir / "shops.yaml", True).items()}
        trainers = {i: _parse_trainer(i, raw, refs) for i, raw in _load_yaml(data_dir / "trainers.yaml", True).items()}
        npcs = {i: _parse_npc(i, raw, refs) for i, raw in _load_yaml(data_dir / "npcs.yaml", True).items()}
        quests = {i: _parse_quest(i, raw, refs) for i, raw in quests_raw.items()}

        content = cls(effects=effects, skills=skills, enemies=enemies, races=races, classes=classes,
                      rules=rules, default_race=default_race, items=items, orgs=orgs, roles=roles,
                      shops=shops, npcs=npcs, quests=quests, trainers=trainers)
        content.check_references()
        return content

    def check_references(self) -> None:
        for s in self.skills.values():
            for app in s.effects + s.self_effects:
                if app.effect not in self.effects:
                    raise ContentError(f"skills.yaml > {s.id}: efeito '{app.effect}' não existe em effects.yaml")
            for race in s.races:
                if race not in self.races:
                    raise ContentError(f"skills.yaml > {s.id}: raça '{race}' não existe em races.yaml")
            for cls_id in s.classes:
                if cls_id not in self.classes:
                    raise ContentError(f"skills.yaml > {s.id}: classe '{cls_id}' não existe em classes.yaml")
        marks: dict[str, str] = {}
        for e in self.enemies.values():
            if e.mark in marks:
                raise ContentError(f"enemies.yaml > {e.id}: mark '{e.mark}' já usado por '{marks[e.mark]}'")
            marks[e.mark] = e.id
            for sid in e.skills:
                if sid not in self.skills:
                    raise ContentError(f"enemies.yaml > {e.id}: skill '{sid}' não existe em skills.yaml")
                if self.skills[sid].type not in AI_SKILL_TYPES:
                    raise ContentError(f"enemies.yaml > {e.id}: a IA não sabe usar skills do tipo '{self.skills[sid].type}'")
            for entry in e.loot:
                if entry.item not in self.items:
                    raise ContentError(f"enemies.yaml > {e.id}: item de saque '{entry.item}' não existe em items.yaml")
            for i, phase in enumerate(e.phases):
                where = f"enemies.yaml > {e.id}.phases[{i}]"
                for sid in phase.skills or ():
                    if sid not in self.skills:
                        raise ContentError(f"{where}: skill '{sid}' não existe em skills.yaml")
                    if self.skills[sid].type not in AI_SKILL_TYPES:
                        raise ContentError(f"{where}: a IA não sabe usar skills do tipo '{self.skills[sid].type}'")
                for enemy_id, _ in phase.summon:
                    other = self.enemies.get(enemy_id)
                    if other is None:
                        raise ContentError(f"{where}: summon '{enemy_id}' não existe em enemies.yaml")
                    if other.boss:
                        raise ContentError(f"{where}: um chefe não pode invocar outro chefe ({enemy_id})")
                for app in phase.effects:
                    if app.effect not in self.effects:
                        raise ContentError(f"{where}: efeito '{app.effect}' não existe em effects.yaml")
        for item in self.items.values():
            where = f"items.yaml > {item.id}"
            for race in item.races:
                if race not in self.races:
                    raise ContentError(f"{where}: raça '{race}' não existe em races.yaml")
            for cls_id in item.classes:
                if cls_id not in self.classes:
                    raise ContentError(f"{where}: classe '{cls_id}' não existe em classes.yaml")
            for app in item.effects:
                if app.effect not in self.effects:
                    raise ContentError(f"{where}: efeito '{app.effect}' não existe em effects.yaml")
        for c in self.classes.values():
            where = f"classes.yaml > {c.id}"
            if c.race not in self.races:
                raise ContentError(f"{where}: raça '{c.race}' não existe em races.yaml")
            for sid in c.start_skills:
                if sid not in self.skills:
                    raise ContentError(f"{where}: skill '{sid}' não existe em skills.yaml")
                if not self.skills[sid].usable_by(c.race, c.id):
                    raise ContentError(f"{where}: a skill inicial '{sid}' não pode ser usada por esta classe "
                                       "(veja races/classes dela em skills.yaml)")
            if len(c.start_skills) > MAX_HOTBAR:
                raise ContentError(f"{where}: no máximo {MAX_HOTBAR} skills iniciais (barra de atalhos)")
        for r in self.races.values():
            start = self.classes.get(r.start_class)
            if start is None:
                raise ContentError(f"races.yaml > {r.id}: start_class '{r.start_class}' não existe em classes.yaml")
            if start.race != r.id:
                raise ContentError(f"races.yaml > {r.id}: start_class '{r.start_class}' é da raça '{start.race}'")
        for org in self.orgs.values():
            for race in org.races:
                if race not in self.races:
                    raise ContentError(f"organizations.yaml > {org.id}: raça '{race}' não existe em races.yaml")
        if self.default_race not in self.races:
            raise ContentError(f"player.yaml: default_race '{self.default_race}' não existe em races.yaml")
        if not any(r.selectable for r in self.races.values()):
            raise ContentError("races.yaml: nenhuma raça com selectable: true (ninguém conseguiria criar personagem)")
        for npc in self.npcs.values():
            for node in npc.nodes.values():
                for i, opt in enumerate(node.options):
                    if opt.shop and opt.shop not in self.shops:
                        raise ContentError(f"npcs.yaml > {npc.id}.dialogue > {node.id}.options[{i}]: "
                                           f"loja {opt.shop!r} não existe em shops.yaml")
                    if opt.trainer and opt.trainer not in self.trainers:
                        raise ContentError(f"npcs.yaml > {npc.id}.dialogue > {node.id}.options[{i}]: "
                                           f"treinador {opt.trainer!r} não existe em trainers.yaml")
        self._check_shop_prices()
        for q in self.quests.values():
            where = f"quests.yaml > {q.id}"
            for field_name in ("giver", "turn_in"):
                npc_id = getattr(q, field_name)
                if npc_id and npc_id not in self.npcs:
                    raise ContentError(f"{where}: {field_name} {npc_id!r} não existe em npcs.yaml")
            for i, obj in enumerate(q.objectives):
                table = {"kill": (self.enemies, "enemies.yaml"), "collect": (self.items, "items.yaml"),
                         "talk": (self.npcs, "npcs.yaml")}.get(obj.kind)
                if table and obj.target not in table[0]:
                    raise ContentError(f"{where}.objectives[{i}]: {obj.target!r} não existe em {table[1]}")

    def _check_shop_prices(self) -> None:
        """Nenhuma loja pode comprar um item por mais do que outra o vende (seria dinheiro infinito)."""
        for item in self.items.values():
            selling = [(s.sell_price(item.id), s) for s in self.shops.values() if s.sell_price(item.id)]
            if not selling:
                continue
            cheapest, seller = min(selling, key=lambda pair: pair[0])
            for shop in self.shops.values():
                paid = shop.buy_price(item)
                if paid > cheapest:
                    raise ContentError(
                        f"shops.yaml > {shop.id}: compra {item.id} por {paid}, mas {seller.id} vende por {cheapest} "
                        "(dá para comprar numa e vender na outra sem parar); baixe o buy_rate ou suba o preço"
                    )


# ============================================================== leitura


def _load_yaml(path: Path, optional: bool = False) -> dict:
    if optional and not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ContentError(f"{path.name}: YAML inválido: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ContentError(f"{path.name}: o arquivo deve ser um mapa de itens")
    for key in data:
        if not isinstance(key, str) or not ID_RE.fullmatch(key):
            raise ContentError(f"{path.name}: id inválido {key!r} (use letras minúsculas, números e _)")
    return data


class _Fields:
    """Lê campos de um mapa do YAML, validando tipos e lembrando quais foram usados."""

    def __init__(self, where: str, raw: Any):
        if not isinstance(raw, dict):
            raise ContentError(f"{where}: esperado um mapa de campos, veio {type(raw).__name__}")
        self.where = where
        self.raw = raw
        self.used: set[str] = set()

    def _get(self, key: str, default: Any) -> Any:
        self.used.add(key)
        value = self.raw.get(key)
        if value is not None:
            return value
        if default is _REQUIRED:
            raise ContentError(f"{self.where}: campo obrigatório '{key}' faltando")
        return default

    def num(self, key: str, default: Any = _REQUIRED, minimum: float = 0.0) -> float:
        value = self._get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ContentError(f"{self.where}: '{key}' deve ser um número, veio {value!r}")
        if value < minimum:
            raise ContentError(f"{self.where}: '{key}' deve ser no mínimo {minimum}, veio {value}")
        return float(value)

    def int(self, key: str, default: Any = _REQUIRED, minimum: int = 0) -> int:
        value = self._get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContentError(f"{self.where}: '{key}' deve ser um número inteiro, veio {value!r}")
        if value < minimum:
            raise ContentError(f"{self.where}: '{key}' deve ser no mínimo {minimum}, veio {value}")
        return value

    def flag(self, key: str, default: Any = _REQUIRED) -> bool:
        value = self._get(key, default)
        if not isinstance(value, bool):
            raise ContentError(f"{self.where}: '{key}' deve ser true ou false, veio {value!r}")
        return value

    def text(self, key: str, default: Any = _REQUIRED) -> str:
        if default is not _REQUIRED and self.raw.get(key) is None:
            self.used.add(key)
            return default  # campo opcional ausente: vale o padrão (pode ser "")
        value = self._get(key, default)
        if not isinstance(value, str) or not value:
            raise ContentError(f"{self.where}: '{key}' deve ser um texto, veio {value!r}")
        return value

    def sub(self, key: str) -> _Fields:
        return _Fields(f"{self.where}.{key}", self._get(key, {}))

    def list(self, key: str) -> list:
        value = self._get(key, [])
        if not isinstance(value, list):
            raise ContentError(f"{self.where}: '{key}' deve ser uma lista, veio {value!r}")
        return value

    def str_list(self, key: str) -> list[str]:
        items = self.list(key)
        for item in items:
            if not isinstance(item, str):
                raise ContentError(f"{self.where}: '{key}' deve conter textos, veio {item!r}")
        return items

    def stat_numbers(self, key: str) -> dict[str, float]:
        sub = self.sub(key)
        result = {}
        for stat in list(sub.raw):
            if stat not in STAT_NAMES:
                raise ContentError(f"{sub.where}: atributo desconhecido '{stat}' (válidos: {', '.join(STAT_NAMES)})")
            result[stat] = sub.num(stat)
        return result

    def done(self) -> None:
        extra = set(self.raw) - self.used
        if extra:
            names = ", ".join(sorted(map(str, extra)))
            raise ContentError(f"{self.where}: campo(s) desconhecido(s) ou que não se aplicam a este tipo: {names}")


def _effect_apps(f: _Fields, key: str) -> tuple[EffectApplication, ...]:
    apps = []
    for i, raw in enumerate(f.list(key)):
        item = _Fields(f"{f.where}.{key}[{i}]", raw)
        apps.append(EffectApplication(effect=item.text("id"), duration=item.num("duration", minimum=0.05)))
        item.done()
    return tuple(apps)


def _parse_effect(eid: str, raw: Any) -> EffectDef:
    f = _Fields(f"effects.yaml > {eid}", raw)
    mods = f.sub("modifiers")
    modifiers = {}
    for key in list(mods.raw):
        if key not in MODIFIERS:
            raise ContentError(f"{mods.where}: modificador desconhecido '{key}' (válidos: {', '.join(MODIFIERS)})")
        modifiers[key] = mods.num(key)
    flags = f.str_list("flags")
    for flag in flags:
        if flag not in FLAGS:
            raise ContentError(f"{f.where}: flag desconhecida '{flag}' (válidas: {', '.join(FLAGS)})")
    tick = f.sub("tick")
    every, damage, heal = tick.num("every", 0.0), tick.num("damage", 0.0), tick.num("heal", 0.0)
    tick.done()
    if (damage or heal) and every <= 0:
        raise ContentError(f"{f.where}.tick: 'every' precisa ser maior que 0")
    effect = EffectDef(
        id=eid,
        name=f.text("name"),
        color=f.text("color", "#ffffff"),
        modifiers=modifiers,
        flags=frozenset(flags),
        tick_every=every,
        tick_damage=damage,
        tick_heal=heal,
    )
    f.done()
    return effect


def _parse_skill(sid: str, raw: Any) -> SkillDef:
    f = _Fields(f"skills.yaml > {sid}", raw)
    stype = f.text("type")
    if stype not in SKILL_TYPES:
        raise ContentError(f"{f.where}: type '{stype}' inválido (válidos: {', '.join(SKILL_TYPES)})")
    damage = f.sub("damage")
    damage_base = damage.num("base", 0.0)
    damage_scale = damage.stat_numbers("scale")
    damage.done()
    cost = f.sub("cost")
    cost_energy = cost.num("energy", 0.0)
    cost.done()

    extra: dict[str, float | int] = {}
    if stype == "melee":
        extra = {"reach": f.num("reach"), "size": f.num("size", minimum=1)}
    elif stype == "projectile":
        extra = {
            "speed": f.num("speed", minimum=1),
            "ttl": f.num("ttl", minimum=0.05),
            "radius": f.num("radius", minimum=1),
            "count": f.int("count", 1, minimum=1),
            "spread": f.num("spread", 0.0),
        }
    elif stype == "area":
        extra = {"radius": f.num("radius", minimum=1)}
    elif stype == "dash":
        extra = {"distance": f.num("distance", minimum=1)}

    skill = SkillDef(
        id=sid,
        name=f.text("name"),
        type=stype,
        color=f.text("color", "#ffffff"),
        cooldown=f.num("cooldown"),
        cost_energy=cost_energy,
        damage_base=damage_base,
        damage_scale=damage_scale,
        train=f.stat_numbers("train"),
        effects=_effect_apps(f, "effects"),
        self_effects=_effect_apps(f, "self_effects"),
        description=f.text("description", ""),
        races=tuple(f.str_list("races")),
        classes=tuple(f.str_list("classes")),
        **extra,
    )
    f.done()
    return skill


def _parse_enemy(eid: str, raw: Any) -> EnemyDef:
    f = _Fields(f"enemies.yaml > {eid}", raw)
    mark = f.text("mark")
    if len(mark) != 1 or mark in RESERVED_MARKS or not mark.isprintable() or mark.isspace():
        raise ContentError(f"{f.where}: mark deve ser 1 caractere livre (não pode ser {''.join(sorted(RESERVED_MARKS))})")
    boss = f.flag("boss", False)
    elite_chance = f.num("elite_chance", 0.0)
    if not 0 <= elite_chance <= 1:
        raise ContentError(f"{f.where}: 'elite_chance' deve estar entre 0 e 1 (ex.: 0.1 = 10%)")
    if boss and elite_chance:
        raise ContentError(f"{f.where}: chefes não nascem elite (tire elite_chance)")
    phases = tuple(_parse_phase(f"{f.where}.phases[{i}]", raw) for i, raw in enumerate(f.list("phases")))
    if phases and not boss:
        raise ContentError(f"{f.where}: 'phases' só vale para chefes (boss: true)")
    limits = [p.hp_below for p in phases]
    if limits != sorted(limits, reverse=True) or len(set(limits)) != len(limits):
        raise ContentError(f"{f.where}: as fases devem vir em ordem, de hp_below maior para menor (ex.: 0.6, 0.3)")
    enemy = EnemyDef(
        id=eid,
        name=f.text("name"),
        mark=mark,
        color=f.text("color", "#6d2f7d"),
        hp=f.num("hp", minimum=1),
        speed=f.num("speed"),
        aggro_radius=f.num("aggro_radius"),
        leash=f.num("leash"),
        wander_radius=f.num("wander_radius"),
        respawn=f.num("respawn"),
        kill_xp=f.num("kill_xp", 0.0),
        skills=tuple(f.str_list("skills")),
        gold=_int_range(f, "gold", (0, 0)),
        loot=tuple(_parse_loot(f)),
        level=f.int("level", 1, minimum=1),
        damage=f.num("damage", 1.0),
        size=f.num("size", 1.0, minimum=0.5),
        elite_chance=elite_chance,
        boss=boss,
        phases=phases,
    )
    f.done()
    return enemy


def _parse_phase(where: str, raw: Any) -> BossPhase:
    f = _Fields(where, raw)
    hp_below = f.num("hp_below")
    if not 0 < hp_below < 1:
        raise ContentError(f"{where}: 'hp_below' deve estar entre 0 e 1 (ex.: 0.5 = metade da vida)")
    summon = []
    for i, entry in enumerate(f.list("summon")):
        s = _Fields(f"{where}.summon[{i}]", entry if isinstance(entry, dict) else {"enemy": entry})
        summon.append((s.text("enemy"), s.int("count", 1, minimum=1)))
        s.done()
    phase = BossPhase(
        hp_below=hp_below,
        skills=tuple(f.str_list("skills")) if "skills" in f.raw else None,
        damage=f.num("damage", 1.0),
        speed=f.num("speed", 1.0),
        message=f.text("message", ""),
        summon=tuple(summon),
        effects=_effect_apps(f, "effects"),
    )
    f.done()
    return phase


def _int_range(f: _Fields, key: str, default: tuple[int, int]) -> tuple[int, int]:
    """Aceita N ou [mínimo, máximo] (inteiros, mínimo ≤ máximo)."""
    value = f._get(key, list(default))
    if isinstance(value, int) and not isinstance(value, bool):
        value = [value, value]
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in value)
        or value[0] > value[1]
    ):
        raise ContentError(f"{f.where}: '{key}' deve ser um número inteiro ou [mínimo, máximo], veio {value!r}")
    return value[0], value[1]


def _parse_loot(f: _Fields) -> list[LootEntry]:
    entries = []
    for i, raw in enumerate(f.list("loot")):
        e = _Fields(f"{f.where}.loot[{i}]", raw)
        chance = e.num("chance", 1.0)
        if not 0 < chance <= 1:
            raise ContentError(f"{e.where}: 'chance' deve estar entre 0 e 1 (ex.: 0.25 = 25%)")
        qty_min, qty_max = _int_range(e, "qty", (1, 1))
        if qty_min < 1:
            raise ContentError(f"{e.where}: 'qty' deve ser pelo menos 1")
        entries.append(LootEntry(item=e.text("item"), chance=chance, qty_min=qty_min, qty_max=qty_max))
        e.done()
    return entries


def _parse_item(iid: str, raw: Any) -> ItemDef:
    f = _Fields(f"items.yaml > {iid}", raw)
    itype = f.text("type")
    if itype not in ITEM_TYPES:
        raise ContentError(f"{f.where}: type '{itype}' inválido (válidos: {', '.join(ITEM_TYPES)})")
    rarity = f.text("rarity", "common")
    if rarity not in RARITIES:
        raise ContentError(f"{f.where}: rarity '{rarity}' inválida (válidas: {', '.join(RARITIES)})")

    extra: dict[str, Any] = {}
    if itype == "equipment":
        slot = f.text("slot")
        if slot not in EQUIP_SLOTS:
            raise ContentError(f"{f.where}: slot '{slot}' inválido (válidos: {', '.join(EQUIP_SLOTS)})")
        bonus_f = f.sub("bonus")
        bonus = {}
        for key in list(bonus_f.raw):
            if key not in RACE_BONUSES:
                raise ContentError(f"{bonus_f.where}: bônus desconhecido '{key}' (válidos: {', '.join(RACE_BONUSES)})")
            bonus[key] = bonus_f.num(key, minimum=-1e9)
        mods_f = f.sub("modifiers")
        modifiers = {}
        for key in list(mods_f.raw):
            if key not in MODIFIERS:
                raise ContentError(f"{mods_f.where}: modificador desconhecido '{key}' (válidos: {', '.join(MODIFIERS)})")
            modifiers[key] = mods_f.num(key)
        extra = {"slot": slot, "bonus": bonus, "modifiers": modifiers,
                 "races": tuple(f.str_list("races")), "classes": tuple(f.str_list("classes"))}
        stack = 1  # equipamento não empilha
    else:
        if itype == "consumable":
            use = f.sub("use")
            heal, energy = use.num("heal", 0.0), use.num("energy", 0.0)
            effects = _effect_apps(use, "effects")
            use.done()
            if not (heal or energy or effects):
                raise ContentError(f"{f.where}.use: precisa de heal, energy e/ou effects")
            extra = {"heal": heal, "energy": energy, "effects": effects, "cooldown": f.num("cooldown", 1.0)}
        stack = f.int("stack", 10 if itype == "consumable" else 50, minimum=1)

    item = ItemDef(
        id=iid,
        name=f.text("name"),
        type=itype,
        rarity=rarity,
        description=f.text("description", ""),
        stack=stack,
        value=f.int("value", 0),
        **extra,
    )
    f.done()
    return item


def _parse_race(rid: str, raw: Any) -> RaceDef:
    f = _Fields(f"races.yaml > {rid}", raw)
    bonus_f = f.sub("bonus")
    bonus = {}
    for key in list(bonus_f.raw):
        if key not in RACE_BONUSES:
            raise ContentError(f"{bonus_f.where}: bônus desconhecido '{key}' (válidos: {', '.join(RACE_BONUSES)})")
        bonus[key] = bonus_f.num(key, minimum=-1e9)
    race = RaceDef(
        id=rid,
        name=f.text("name"),
        description=f.text("description", ""),
        selectable=f.flag("selectable", False),
        start_class=f.text("start_class"),
        bonus=bonus,
    )
    f.done()
    return race


def _parse_class(cid: str, raw: Any) -> ClassDef:
    f = _Fields(f"classes.yaml > {cid}", raw)
    cls = ClassDef(
        id=cid,
        name=f.text("name"),
        race=f.text("race"),
        description=f.text("description", ""),
        start_skills=tuple(f.str_list("start_skills")),
        train_rate=f.stat_numbers("train_rate"),
    )
    f.done()
    return cls


# ============================================================== regras


@dataclass(frozen=True)
class _RuleRefs:
    """O que já foi carregado, para conferir os valores usados nas regras."""

    effects: dict[str, EffectDef]
    skills: dict[str, SkillDef]
    enemies: dict[str, EnemyDef]
    races: dict[str, RaceDef]
    classes: dict[str, ClassDef]
    items: dict[str, ItemDef] = field(default_factory=dict)
    orgs: dict[str, OrgDef] = field(default_factory=dict)
    quests: tuple[str, ...] = ()  # só os ids (missões e regras se referem umas às outras)

    def rank_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r.id for o in self.orgs.values() for r in o.ranks))

    def allowed(self, field: str) -> tuple[str, ...] | type | None:
        """Valores válidos para um campo de condição (ou o tipo esperado)."""
        table: dict[str, Any] = {
            "race": self.races, "killer_race": self.races, "victim_race": self.races, "opponent_race": self.races,
            "class": self.classes, "killer_class": self.classes, "victim_class": self.classes,
            "opponent_class": self.classes, "result": DUEL_RESULTS, "reason": DUEL_END_REASONS,
            "killer_enemy": self.enemies, "victim_enemy": self.enemies,
            "skill": self.skills, "effect": self.effects, "effects": self.effects,
            "zone": ZONES, "killer": KILLER_KINDS, "victim": VICTIM_KINDS, "stat": STAT_NAMES,
            "duel": bool, "level": float, "gold": float, "total_level": float,
            "org": self.orgs, "rank": self.rank_ids(), "old_rank": self.rank_ids(), "cause": RANK_CAUSES,
            "quest": self.quests, "quest_done": self.quests, "quest_active": self.quests,
        }
        value = table.get(field)
        return tuple(value) if isinstance(value, (dict, tuple)) else value


def _check_condition(where: str, field: str, value: Any, refs: _RuleRefs) -> Any:
    allowed = refs.allowed(field)
    if allowed is bool:
        if not isinstance(value, bool):
            raise ContentError(f"{where}: '{field}' deve ser true ou false, veio {value!r}")
        return value
    if allowed is float:
        if isinstance(value, dict):
            sub = _Fields(f"{where}.{field}", value)
            result = {k: sub.num(k, minimum=-1e9) for k in ("min", "max") if k in value}
            sub.done()
            if not result:
                raise ContentError(f"{where}: '{field}' precisa de min e/ou max")
            return result
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ContentError(f"{where}: '{field}' deve ser um número ou {{min, max}}, veio {value!r}")
        return value
    options = value if isinstance(value, list) else [value]
    for option in options:
        if option not in allowed:  # type: ignore[operator]
            raise ContentError(
                f"{where}: valor {option!r} inválido para '{field}' (válidos: {', '.join(allowed)})"  # type: ignore[arg-type]
            )
    return value


def _check_action(where: str, when: str, name: str, args: Any, refs: _RuleRefs) -> Any:
    if name not in ACTIONS:
        raise ContentError(f"{where}: ação desconhecida '{name}' (válidas: {', '.join(ACTIONS)})")

    def flag_name(value: Any) -> str:
        if not isinstance(value, str) or not ID_RE.fullmatch(value):
            raise ContentError(f"{where}: {name} precisa de um nome de marca (letras minúsculas, números e _)")
        return value

    if name in ("message", "broadcast"):
        if not isinstance(args, str) or not args:
            raise ContentError(f"{where}: {name} precisa de um texto")
        return args
    if name == "give_gold":
        if isinstance(args, bool) or not isinstance(args, int) or args < 1:
            raise ContentError(f"{where}: give_gold precisa de um número inteiro positivo")
        return args
    if name == "lose_gold":
        f = _Fields(f"{where}.lose_gold", args)
        if ("percent" in args) == ("amount" in args):
            raise ContentError(f"{f.where}: use percent OU amount")
        result = {"percent": f.num("percent")} if "percent" in args else {"amount": f.int("amount", minimum=1)}
        if "percent" in result and not 0 < result["percent"] <= 100:
            raise ContentError(f"{f.where}: 'percent' deve estar entre 0 e 100")
        f.done()
        return result
    if name == "give_item":
        f = _Fields(f"{where}.give_item", args if isinstance(args, dict) else {"id": args})
        item = f.text("id")
        if item not in refs.items:
            raise ContentError(f"{f.where}: item {item!r} não existe em items.yaml")
        result = {"id": item, "qty": f.int("qty", 1, minimum=1)}
        f.done()
        return result
    if name in ("set_flag", "clear_flag"):
        return flag_name(args)
    if name == "learn_skill":
        if args not in refs.skills:
            raise ContentError(f"{where}: skill {args!r} não existe em skills.yaml")
        return args
    if name == "start_quest":
        if args not in refs.quests:
            raise ContentError(f"{where}: missão {args!r} não existe em quests.yaml")
        return args
    if name == "set_class":
        if args not in refs.classes:
            raise ContentError(f"{where}: classe {args!r} não existe em classes.yaml")
        return args
    if name == "set_race":
        race, cls = (args, None) if isinstance(args, str) else (None, None)
        if isinstance(args, dict):
            f = _Fields(f"{where}.set_race", args)
            race, cls = f.text("race"), f.text("class", None) if "class" in args else None
            f.done()
        if race not in refs.races:
            raise ContentError(f"{where}: raça {race!r} não existe em races.yaml")
        if cls is not None and (cls not in refs.classes or refs.classes[cls].race != race):
            raise ContentError(f"{where}: classe {cls!r} não existe ou não é da raça '{race}'")
        return {"race": race, "class": cls or refs.races[race].start_class}
    f = _Fields(f"{where}.{name}", args if args is not None else {})
    if name == "lose_xp":
        percent = f.num("percent")
        if not 0 < percent <= 100:
            raise ContentError(f"{f.where}: 'percent' deve estar entre 0 e 100")
        stat = f.text("stat", "highest")
        if stat not in ("highest", "all", *STAT_NAMES):
            raise ContentError(f"{f.where}: stat {stat!r} inválido (válidos: highest, all, {', '.join(STAT_NAMES)})")
        result: Any = {"percent": percent, "stat": stat}
    elif name == "gain_xp":
        stat = f.text("stat")
        if stat not in STAT_NAMES:
            raise ContentError(f"{f.where}: stat {stat!r} inválido (válidos: {', '.join(STAT_NAMES)})")
        result = {"stat": stat, "amount": f.num("amount")}
    else:  # add_effect
        if when == "death":
            raise ContentError(f"{where}: add_effect não funciona em 'death' (o personagem está morto)")
        effect = f.text("id")
        if effect not in refs.effects:
            raise ContentError(f"{f.where}: efeito {effect!r} não existe em effects.yaml")
        result = EffectApplication(effect=effect, duration=f.num("duration", minimum=0.05))
    f.done()
    return result


def _parse_conditions(cond: _Fields, fields: tuple[str, ...], refs: _RuleRefs, context: str,
                      chance: bool = True) -> tuple[tuple[str, Any], ...]:
    """Lê um bloco "if". `fields` são os campos de contexto disponíveis; `context` aparece no erro."""
    special = (("chance",) if chance else ()) + ("flags", "not_flags")
    conditions: list[tuple[str, Any]] = []
    for key in list(cond.raw):
        value = cond.raw[key]
        cond.used.add(key)
        if key == "chance" and chance:
            value = cond.num("chance")
            if not 0 < value <= 1:
                raise ContentError(f"{cond.where}: 'chance' deve estar entre 0 e 1 (ex.: 0.25 = 25%)")
        elif key in ("flags", "not_flags"):
            value = [value] if isinstance(value, str) else value
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ContentError(f"{cond.where}: '{key}' deve ser uma lista de marcas")
        elif key in fields:
            value = _check_condition(cond.where, key, value, refs)
        else:
            raise ContentError(
                f"{cond.where}: campo '{key}' não existe {context} (disponíveis: {', '.join(fields + special)})"
            )
        conditions.append((key, value))
    conditions.sort(key=lambda kv: kv[0] == "chance")  # sorteia só se o resto bater
    return tuple(conditions)


def _parse_actions(f: _Fields, when: str, refs: _RuleRefs, key: str = "do") -> tuple[tuple[str, Any], ...]:
    """Lê uma lista de ações ("do" nas regras e diálogos, "rewards" nas missões)."""
    actions = []
    for i, item in enumerate(f.list(key)):
        item_where = f"{f.where}.{key}[{i}]"
        if not isinstance(item, dict) or len(item) != 1:
            raise ContentError(f"{item_where}: cada ação é um item com uma única chave (ex.: - message: texto)")
        ((name, args),) = item.items()
        actions.append((name, _check_action(item_where, when, name, args, refs)))
    return tuple(actions)


def _parse_rule(rid: str, raw: Any, refs: _RuleRefs) -> Rule:
    where = f"rules.yaml > {rid}"
    f = _Fields(where, raw)
    when = f.text("when")
    if when not in EVENT_FIELDS:
        raise ContentError(f"{where}: evento '{when}' desconhecido (válidos: {', '.join(EVENT_FIELDS)})")
    conditions = _parse_conditions(f.sub("if"), EVENT_FIELDS[when], refs, f"no evento '{when}'")
    rule = Rule(id=rid, when=when, conditions=conditions, actions=_parse_actions(f, when, refs),
                stop=f.flag("stop", False))
    f.done()
    return rule


def parse_rules(raw: dict, content: Content) -> tuple[Rule, ...]:
    """Valida regras avulsas contra um conteúdo já carregado (usado nos testes)."""
    refs = _RuleRefs(effects=content.effects, skills=content.skills, enemies=content.enemies,
                     races=content.races, classes=content.classes, items=content.items, orgs=content.orgs,
                     quests=tuple(content.quests))
    return tuple(_parse_rule(i, r, refs) for i, r in raw.items())


def _parse_org(oid: str, raw: Any) -> OrgDef:
    f = _Fields(f"organizations.yaml > {oid}", raw)
    ranks_raw = f._get("ranks", None)
    if not isinstance(ranks_raw, dict) or not ranks_raw:
        raise ContentError(f"{f.where}: 'ranks' deve listar os cargos, do mais alto para o mais baixo")
    ranks = []
    for rid, rraw in ranks_raw.items():
        r = _Fields(f"{f.where}.ranks > {rid}", rraw)
        if not isinstance(rid, str) or not ID_RE.fullmatch(rid):
            raise ContentError(f"{r.where}: id de cargo inválido")
        promotion = r.text("promotion")
        if promotion not in PROMOTIONS:
            raise ContentError(f"{r.where}: promotion '{promotion}' inválida (válidas: {', '.join(PROMOTIONS)})")
        seats = r.int("seats", None, minimum=1) if "seats" in rraw else None
        rank = RankDef(
            id=rid,
            name=r.text("name"),
            seats=seats,
            numbered=r.flag("numbered", False),
            leader=r.flag("leader", False),
            promotion=promotion,
            min_total_level=r.int("min_total_level", 0),
        )
        r.done()
        if rank.numbered and rank.seats is None:
            raise ContentError(f"{r.where}: cargo numerado precisa de 'seats'")
        if promotion == "default" and seats is not None:
            raise ContentError(f"{r.where}: o cargo inicial (promotion: default) não pode ter vagas limitadas")
        ranks.append(rank)
    org = OrgDef(
        id=oid,
        name=f.text("name"),
        description=f.text("description", ""),
        color=f.text("color", "#d8dbe2"),
        races=tuple(f.str_list("races")),
        min_total_level=f.int("min_total_level", 0),
        default_rank=f.text("default_rank"),
        ranks=tuple(ranks),
    )
    f.done()
    default = org.rank(org.default_rank)
    if default is None or default.promotion != "default":
        raise ContentError(f"{f.where}: default_rank '{org.default_rank}' precisa ser um cargo com promotion: default")
    if sum(1 for r in ranks if r.promotion == "default") != 1:
        raise ContentError(f"{f.where}: precisa de exatamente um cargo com promotion: default")
    return org


def _parse_role(rid: str, raw: Any) -> RoleDef:
    f = _Fields(f"roles.yaml > {rid}", raw)
    permissions = f.str_list("permissions")
    for perm in permissions:
        if perm not in PERMISSIONS:
            raise ContentError(f"{f.where}: permissão desconhecida '{perm}' (válidas: {', '.join(PERMISSIONS)})")
    role = RoleDef(
        id=rid,
        name=f.text("name"),
        tag=f.text("tag"),
        color=f.text("color", "#d8dbe2"),
        level=f.int("level", minimum=1),
        permissions=frozenset(permissions),
    )
    f.done()
    return role


# ============================================================== lojas e NPCs


def _parse_shop(sid: str, raw: Any, items: dict[str, ItemDef]) -> ShopDef:
    f = _Fields(f"shops.yaml > {sid}", raw)
    sells = []
    for i, entry in enumerate(f.list("sells")):
        e = _Fields(f"{f.where}.sells[{i}]", entry if isinstance(entry, dict) else {"item": entry})
        item_id = e.text("item")
        item = items.get(item_id)
        if item is None:
            raise ContentError(f"{e.where}: item {item_id!r} não existe em items.yaml")
        price = e.int("price", item.value, minimum=1) if "price" in e.raw else item.value
        if price < 1:
            raise ContentError(f"{e.where}: {item_id} tem value 0 em items.yaml; dê um 'price' para vendê-lo")
        e.done()
        if any(s.item == item_id for s in sells):
            raise ContentError(f"{e.where}: {item_id} aparece duas vezes na lista")
        sells.append(ShopEntry(item=item_id, price=price))
    buys = tuple(f.str_list("buys"))
    for what in buys:
        if what != "all" and what not in ITEM_TYPES and what not in items:
            raise ContentError(f"{f.where}.buys: {what!r} não é 'all', um tipo de item "
                               f"({', '.join(ITEM_TYPES)}) nem um item de items.yaml")
    rate = f.num("buy_rate", DEFAULT_BUY_RATE)
    if not 0 < rate <= 1:
        raise ContentError(f"{f.where}: 'buy_rate' deve estar entre 0 e 1 (ex.: 0.4 = paga 40% do valor)")
    shop = ShopDef(id=sid, name=f.text("name"), sells=tuple(sells), buys=buys, buy_rate=rate)
    f.done()
    if not sells and not buys:
        raise ContentError(f"{f.where}: a loja não vende (sells) nem compra (buys) nada")
    return shop


def _parse_trainer(tid: str, raw: Any, refs: _RuleRefs) -> TrainerDef:
    f = _Fields(f"trainers.yaml > {tid}", raw)
    teaches: list[TrainerEntry] = []
    for i, entry in enumerate(f.list("teaches")):
        e = _Fields(f"{f.where}.teaches[{i}]", entry if isinstance(entry, dict) else {"skill": entry})
        skill_id = e.text("skill")
        if skill_id not in refs.skills:
            raise ContentError(f"{e.where}: skill {skill_id!r} não existe em skills.yaml")
        if any(t.skill == skill_id for t in teaches):
            raise ContentError(f"{e.where}: {skill_id} aparece duas vezes na lista")
        stats_f = e.sub("stats")
        stats = {}
        for stat in list(stats_f.raw):
            if stat not in STAT_NAMES:
                raise ContentError(f"{stats_f.where}: atributo {stat!r} inválido (válidos: {', '.join(STAT_NAMES)})")
            stats[stat] = stats_f.int(stat, minimum=1)
        stats_f.done()
        needs = tuple(e.str_list("skills"))
        for other in needs:
            if other not in refs.skills:
                raise ContentError(f"{e.where}.skills: skill {other!r} não existe em skills.yaml")
            if other == skill_id:
                raise ContentError(f"{e.where}.skills: a skill não pode ser requisito dela mesma")
        conditions = _dialog_conditions(e, refs, "nos requisitos de treinador")
        teaches.append(TrainerEntry(skill=skill_id, price=e.int("price", 0), stats=stats, skills=needs,
                                    rule=Rule(id=f"{tid}.{skill_id}", when="train", conditions=conditions, actions=())))
        e.done()
    if not teaches:
        raise ContentError(f"{f.where}: 'teaches' precisa de pelo menos uma skill")
    trainer = TrainerDef(id=tid, name=f.text("name"), teaches=tuple(teaches))
    f.done()
    return trainer


def _parse_npc(nid: str, raw: Any, refs: _RuleRefs) -> NpcDef:
    f = _Fields(f"npcs.yaml > {nid}", raw)
    nodes_raw = f._get("dialogue", None)
    if not isinstance(nodes_raw, dict) or not nodes_raw:
        raise ContentError(f"{f.where}: 'dialogue' deve ter pelo menos um nó (o primeiro é onde a conversa começa)")
    nodes: dict[str, DialogNode] = {}
    for node_id, node_raw in nodes_raw.items():
        if not isinstance(node_id, str) or not ID_RE.fullmatch(node_id):
            raise ContentError(f"{f.where}.dialogue: id de nó inválido {node_id!r} (letras minúsculas, números e _)")
        n = _Fields(f"{f.where}.dialogue > {node_id}", node_raw)
        options = []
        for i, opt_raw in enumerate(n.list("options")):
            o = _Fields(f"{n.where}.options[{i}]", opt_raw)
            conditions = _dialog_conditions(o, refs, "nas condições de diálogo")
            goto, shop, trainer = o.text("goto", None), o.text("shop", None), o.text("trainer", None)
            if sum(x is not None for x in (goto, shop, trainer)) > 1:
                raise ContentError(f"{o.where}: use só um de goto, shop ou trainer")
            options.append(DialogOption(
                text=o.text("text"),
                rule=Rule(id=f"{nid}.{node_id}[{i}]", when="talk", conditions=conditions,
                          actions=_parse_actions(o, "talk", refs)),
                goto=goto, shop=shop, trainer=trainer,
            ))
            o.done()
        nodes[node_id] = DialogNode(id=node_id, text=n.text("text"), options=tuple(options))
        n.done()
    for node in nodes.values():
        for i, opt in enumerate(node.options):
            if opt.goto and opt.goto not in nodes:
                raise ContentError(f"{f.where}.dialogue > {node.id}.options[{i}]: goto {opt.goto!r} "
                                   f"não é um nó deste NPC (nós: {', '.join(nodes)})")
    npc = NpcDef(
        id=nid,
        name=f.text("name"),
        title=f.text("title", ""),
        color=f.text("color", "#e0c070"),
        start=next(iter(nodes)),
        nodes=nodes,
    )
    f.done()
    return npc


def _dialog_conditions(f: _Fields, refs: _RuleRefs, context: str) -> tuple[tuple[str, Any], ...]:
    """Condições de diálogo/missão: sem chance; número sozinho em gold/total_level quer dizer "pelo menos"."""
    return tuple(
        (key, {"min": value} if key in ("gold", "total_level") and not isinstance(value, dict) else value)
        for key, value in _parse_conditions(f.sub("if"), DIALOG_FIELDS, refs, context, chance=False)
    )


def _parse_objective(where: str, raw: Any) -> QuestObjective:
    f = _Fields(where, raw)
    kinds = [k for k in OBJECTIVE_KINDS if k in f.raw]
    if len(kinds) != 1:
        raise ContentError(f"{where}: cada objetivo tem exatamente um tipo: {', '.join(OBJECTIVE_KINDS)}")
    kind = kinds[0]
    target = f.text(kind)
    count, area = 1, None
    if kind in ("kill", "collect"):
        count = f.int("count", 1, minimum=1)
    if kind == "reach" and "area" in f.raw:
        f.used.add("area")
        value = f.raw["area"]
        if not (isinstance(value, list) and len(value) == 4
                and all(isinstance(v, int) and not isinstance(v, bool) for v in value)):
            raise ContentError(f"{where}: area deve ser [x0, y0, x1, y1] em tiles")
        area = (value[0], value[1], value[2], value[3])
    if kind == "flag" and not ID_RE.fullmatch(target):
        raise ContentError(f"{where}: flag precisa de um nome de marca (letras minúsculas, números e _)")
    label = f.text("label", "")
    if kind in ("reach", "flag") and not label:
        raise ContentError(f"{where}: objetivo {kind} precisa de 'label' (o texto que o jogador vê)")
    f.done()
    return QuestObjective(kind=kind, target=target, count=count, area=area, label=label)


def _parse_quest(qid: str, raw: Any, refs: _RuleRefs) -> QuestDef:
    f = _Fields(f"quests.yaml > {qid}", raw)
    objectives_raw = f.list("objectives")
    if not objectives_raw:
        raise ContentError(f"{f.where}: 'objectives' precisa de pelo menos um objetivo")
    objectives = tuple(_parse_objective(f"{f.where}.objectives[{i}]", o) for i, o in enumerate(objectives_raw))
    giver = f.text("giver", None)
    turn_in = f.text("turn_in", None) if "turn_in" in f.raw else giver
    repeatable = f.flag("repeatable", False)
    cooldown = f.num("cooldown", 0.0)
    if cooldown and not repeatable:
        raise ContentError(f"{f.where}: 'cooldown' só vale para missões com repeatable: true")
    quest = QuestDef(
        id=qid,
        name=f.text("name"),
        description=f.text("description", ""),
        objectives=objectives,
        rule=Rule(id=qid, when="quest_complete", conditions=_dialog_conditions(f, refs, "nas condições de missão"),
                  actions=_parse_actions(f, "quest_complete", refs, key="rewards")),
        giver=giver,
        turn_in=turn_in,
        complete_text=f.text("complete_text", "Muito bem. Aqui está sua recompensa."),
        repeatable=repeatable,
        cooldown=cooldown,
    )
    f.done()
    return quest
