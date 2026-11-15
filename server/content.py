"""Conteúdo do jogo (efeitos, skills, inimigos) lido dos arquivos YAML em data/.

Tudo é validado ao carregar: campo desconhecido, tipo errado ou referência
inexistente gera ContentError dizendo arquivo, item e campo. Assim um erro de
digitação no YAML aparece na hora de subir o servidor, não no meio do jogo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .stats import STAT_NAMES
from .tilemap import RESERVED_MARKS

SKILL_TYPES = ("melee", "projectile", "area", "dash", "self")
AI_SKILL_TYPES = ("melee", "projectile", "area", "self")
MODIFIERS = ("speed", "damage", "damage_taken", "energy_regen")
FLAGS = ("stunned", "silenced")
MAX_HOTBAR = 8
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


@dataclass(frozen=True)
class Content:
    effects: dict[str, EffectDef]
    skills: dict[str, SkillDef]
    enemies: dict[str, EnemyDef]
    starting_skills: tuple[str, ...]

    @property
    def enemy_by_mark(self) -> dict[str, EnemyDef]:
        return {e.mark: e for e in self.enemies.values()}

    def client_payload(self) -> dict:
        return {
            "skills": {sid: s.client_info() for sid, s in self.skills.items()},
            "effects": {eid: {"name": e.name, "color": e.color} for eid, e in self.effects.items()},
            "enemies": {e.id: {"color": e.color} for e in self.enemies.values()},
        }

    @classmethod
    def load(cls, data_dir: str | Path) -> Content:
        data_dir = Path(data_dir)
        effects = {i: _parse_effect(i, raw) for i, raw in _load_yaml(data_dir / "effects.yaml").items()}
        skills = {i: _parse_skill(i, raw) for i, raw in _load_yaml(data_dir / "skills.yaml").items()}
        enemies = {i: _parse_enemy(i, raw) for i, raw in _load_yaml(data_dir / "enemies.yaml").items()}

        player = _Fields("player.yaml", _load_yaml(data_dir / "player.yaml"))
        starting = tuple(player.str_list("starting_skills"))
        player.done()

        content = cls(effects=effects, skills=skills, enemies=enemies, starting_skills=starting)
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
        for sid in self.starting_skills:
            if sid not in self.skills:
                raise ContentError(f"player.yaml: skill inicial '{sid}' não existe em skills.yaml")
        if len(self.starting_skills) > MAX_HOTBAR:
            raise ContentError(f"player.yaml: no máximo {MAX_HOTBAR} skills iniciais (barra de atalhos)")


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

    def text(self, key: str, default: Any = _REQUIRED) -> str:
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
    )
    f.done()
    return enemy
