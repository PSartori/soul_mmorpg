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

from .rules import (
    ACTIONS, DUEL_END_REASONS, DUEL_RESULTS, EVENT_FIELDS, KILLER_KINDS, RANK_CAUSES, VICTIM_KINDS, ZONES, Rule,
)
from .stats import STAT_NAMES
from .tilemap import RESERVED_MARKS

SKILL_TYPES = ("melee", "projectile", "area", "dash", "self")
AI_SKILL_TYPES = ("melee", "projectile", "area", "self")
MODIFIERS = ("speed", "damage", "damage_taken", "energy_regen")
FLAGS = ("stunned", "silenced")
MAX_HOTBAR = 8
RACE_BONUSES = ("max_hp", "max_energy", "speed")
ITEM_TYPES = ("equipment", "consumable", "material")
EQUIP_SLOTS = ("weapon", "armor", "accessory")
RARITIES = ("common", "uncommon", "rare", "epic")
PROMOTIONS = ("default", "appointment", "challenge")
PERMISSIONS = ("kick", "mute", "ban", "announce", "teleport", "give", "set_role", "log", "reload")
ID_RE = re.compile(r"[a-z0-9_]+")

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
        return {"name": self.name, "type": self.type, "cd": self.cooldown, "cost": self.cost_energy, "color": self.color}


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
        return {"name": self.name, "color": self.color,
                "ranks": {r.id: {"name": r.name, "numbered": r.numbered} for r in self.ranks}}


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
    skills: tuple[str, ...]
    train_rate: dict[str, float]


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

    @property
    def enemy_by_mark(self) -> dict[str, EnemyDef]:
        return {e.mark: e for e in self.enemies.values()}

    @cached_property
    def rules_by_event(self) -> dict[str, tuple[Rule, ...]]:
        return {event: tuple(r for r in self.rules if r.when == event) for event in EVENT_FIELDS}

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
            "enemies": {e.id: {"color": e.color} for e in self.enemies.values()},
            "races": {r.id: {"name": r.name} for r in self.races.values()},
            "classes": {c.id: {"name": c.name, "race": c.race} for c in self.classes.values()},
            "items": {i.id: i.client_info() for i in self.items.values()},
            "orgs": {o.id: o.client_info() for o in self.orgs.values()},
            "roles": {r.id: r.client_info() for r in self.roles.values()},
        }

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

        # Regras por último: as condições e ações são conferidas contra todo o resto
        refs = _RuleRefs(effects=effects, skills=skills, enemies=enemies, races=races, classes=classes, items=items,
                         orgs=orgs)
        rules = tuple(_parse_rule(i, raw, refs) for i, raw in _load_yaml(data_dir / "rules.yaml").items())

        content = cls(effects=effects, skills=skills, enemies=enemies, races=races, classes=classes,
                      rules=rules, default_race=default_race, items=items, orgs=orgs, roles=roles)
        content.check_references()
        return content

    def check_references(self) -> None:
        for s in self.skills.values():
            for app in s.effects + s.self_effects:
                if app.effect not in self.effects:
                    raise ContentError(f"skills.yaml > {s.id}: efeito '{app.effect}' não existe em effects.yaml")
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
            for sid in c.skills:
                if sid not in self.skills:
                    raise ContentError(f"{where}: skill '{sid}' não existe em skills.yaml")
            if len(c.skills) > MAX_HOTBAR:
                raise ContentError(f"{where}: no máximo {MAX_HOTBAR} skills (barra de atalhos)")
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


# ============================================================== leitura


def _load_yaml(path: Path) -> dict:
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
        **extra,
    )
    f.done()
    return skill


def _parse_enemy(eid: str, raw: Any) -> EnemyDef:
    f = _Fields(f"enemies.yaml > {eid}", raw)
    mark = f.text("mark")
    if len(mark) != 1 or mark in RESERVED_MARKS or not mark.isprintable() or mark.isspace():
        raise ContentError(f"{f.where}: mark deve ser 1 caractere livre (não pode ser {''.join(sorted(RESERVED_MARKS))})")
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
    )
    f.done()
    return enemy


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
        skills=tuple(f.str_list("skills")),
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
            "duel": bool, "level": float,
            "org": self.orgs, "rank": self.rank_ids(), "old_rank": self.rank_ids(), "cause": RANK_CAUSES,
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


def _parse_rule(rid: str, raw: Any, refs: _RuleRefs) -> Rule:
    where = f"rules.yaml > {rid}"
    f = _Fields(where, raw)
    when = f.text("when")
    if when not in EVENT_FIELDS:
        raise ContentError(f"{where}: evento '{when}' desconhecido (válidos: {', '.join(EVENT_FIELDS)})")

    cond = f.sub("if")
    conditions: list[tuple[str, Any]] = []
    for key in list(cond.raw):
        value = cond.raw[key]
        cond.used.add(key)
        if key == "chance":
            value = cond.num("chance")
            if not 0 < value <= 1:
                raise ContentError(f"{cond.where}: 'chance' deve estar entre 0 e 1 (ex.: 0.25 = 25%)")
        elif key in ("flags", "not_flags"):
            value = [value] if isinstance(value, str) else value
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ContentError(f"{cond.where}: '{key}' deve ser uma lista de marcas")
        elif key in EVENT_FIELDS[when]:
            value = _check_condition(cond.where, key, value, refs)
        else:
            raise ContentError(
                f"{cond.where}: campo '{key}' não existe no evento '{when}' "
                f"(disponíveis: {', '.join(EVENT_FIELDS[when] + ('chance', 'flags', 'not_flags'))})"
            )
        conditions.append((key, value))
    conditions.sort(key=lambda kv: kv[0] == "chance")  # sorteia só se o resto bater

    actions = []
    for i, item in enumerate(f.list("do")):
        item_where = f"{where}.do[{i}]"
        if not isinstance(item, dict) or len(item) != 1:
            raise ContentError(f"{item_where}: cada ação é um item com uma única chave (ex.: - message: texto)")
        ((name, args),) = item.items()
        actions.append((name, _check_action(item_where, when, name, args, refs)))

    rule = Rule(id=rid, when=when, conditions=tuple(conditions), actions=tuple(actions), stop=f.flag("stop", False))
    f.done()
    return rule


def parse_rules(raw: dict, content: Content) -> tuple[Rule, ...]:
    """Valida regras avulsas contra um conteúdo já carregado (usado nos testes)."""
    refs = _RuleRefs(effects=content.effects, skills=content.skills, enemies=content.enemies,
                     races=content.races, classes=content.classes, items=content.items, orgs=content.orgs)
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
