"""Entidades do mundo: jogadores, inimigos e projéteis."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import ClassVar

from . import config as C
from .content import ClassDef, EffectDef, EnemyDef, ItemDef, RaceDef
from .inventory import Inventory
from .saves import SAVE_VERSION
from .stats import STAT_NAMES, Stat

DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
MOVE_KEYS = ("up", "down", "left", "right")

MoveKeys = tuple[bool, bool, bool, bool]  # up, down, left, right


@dataclass(eq=False)
class ActiveEffect:
    defn: EffectDef
    remaining: float
    tick_timer: float
    source_id: int | None


@dataclass(eq=False)
class Entity:
    id: int
    name: str
    x: float
    y: float
    hp: float = 1.0
    facing: str = "down"
    state: str = "idle"  # idle | walk | meditate | rest | dead
    dead_timer: float = 0.0
    half: float = C.ENTITY_HALF
    map_id: str = ""  # em qual mapa está (nome do arquivo em data/maps/)
    cooldowns: dict[str, float] = field(default_factory=dict)  # skill -> segundos restantes
    effects: dict[str, ActiveEffect] = field(default_factory=dict)  # efeito -> estado

    kind: ClassVar[str] = "?"

    @property
    def alive(self) -> bool:
        return self.state != "dead"

    @property
    def max_hp(self) -> float:
        raise NotImplementedError

    @property
    def base_speed(self) -> float:
        raise NotImplementedError

    def stat_level(self, stat: str) -> int:
        """Inimigos não têm atributos: o dano das skills deles é só o 'base'."""
        return 0

    # --- efeitos de status ---
    def modifier(self, name: str) -> float:
        result = 1.0
        for active in self.effects.values():
            result *= active.defn.modifiers.get(name, 1.0)
        return result

    def has_flag(self, flag: str) -> bool:
        return any(flag in active.defn.flags for active in self.effects.values())

    @property
    def speed(self) -> float:
        if self.has_flag("stunned"):
            return 0.0
        return self.base_speed * self.modifier("speed")

    def snapshot(self) -> dict:
        """O que todo mundo vê desta entidade."""
        return {
            "id": self.id,
            "k": self.kind,
            "n": self.name,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "f": self.facing,
            "s": self.state,
            "hp": round(self.hp),
            "mhp": round(self.max_hp),
            "ef": sorted(self.effects),
        }


@dataclass(eq=False)
class Player(Entity):
    energy: float = 0.0
    fatigue: float = 0.0
    stats: dict[str, Stat] = field(default_factory=lambda: {n: Stat() for n in STAT_NAMES})
    race_def: RaceDef | None = None
    class_def: ClassDef | None = None
    flags: set[str] = field(default_factory=set)  # marcas de progressão (data/rules.yaml)
    inventory: Inventory = field(default_factory=Inventory)
    item_cooldowns: dict[str, float] = field(default_factory=dict)  # item -> segundos restantes
    portal_lock: bool = False  # acabou de atravessar um portal: só usa outro depois de sair dele
    skills: list[str] = field(default_factory=list)  # vem da classe; ordem da barra de atalhos
    actions: list[tuple] = field(default_factory=list)  # ações pedidas, executadas no próximo tick
    # Movimento: o cliente manda um comando numerado por tick; o servidor executa um por tick
    # e devolve o número do último executado ("ack") para o cliente corrigir a previsão.
    move_queue: deque[tuple[int, MoveKeys]] = field(default_factory=deque)
    last_seq: int = 0
    ack: int = 0

    kind: ClassVar[str] = "player"

    def stat_level(self, stat: str) -> int:
        return self.stats[stat].level

    @property
    def race_id(self) -> str | None:
        return self.race_def.id if self.race_def else None

    @property
    def class_id(self) -> str | None:
        return self.class_def.id if self.class_def else None

    def _bonus(self, key: str) -> float:
        """Bônus somado da raça e dos equipamentos."""
        total = self.race_def.bonus.get(key, 0.0) if self.race_def else 0.0
        for item in self.inventory.equipment.values():
            total += item.bonus.get(key, 0.0)
        return total

    def modifier(self, name: str) -> float:
        """Multiplicador dos efeitos ativos e dos equipamentos."""
        result = super().modifier(name)
        for item in self.inventory.equipment.values():
            result *= item.modifiers.get(name, 1.0)
        return result

    # --- Valores derivados dos atributos, da raça e do equipamento ---
    @property
    def max_hp(self) -> float:
        return max(10.0, 80 + 20 * self.stat_level("vitality") + self._bonus("max_hp"))

    @property
    def max_energy(self) -> float:
        return max(10.0, 40 + 15 * self.stat_level("spirit") + self._bonus("max_energy"))

    @property
    def base_speed(self) -> float:
        speed = C.BASE_SPEED + C.SPEED_PER_AGILITY * self.stat_level("agility") + self._bonus("speed")
        return max(30.0, min(C.MAX_SPEED, speed))

    def private_snapshot(self) -> dict:
        """O que só o próprio jogador vê (HUD e previsão de movimento)."""
        return {
            "hp": round(self.hp),
            "mhp": round(self.max_hp),
            "en": round(self.energy),
            "men": round(self.max_energy),
            "fat": round(self.fatigue),
            "mfat": round(C.MAX_FATIGUE),
            "s": self.state,
            "race": self.race_id,
            "cls": self.class_id,
            "x": self.x,
            "y": self.y,
            "spd": self.speed,
            "ack": self.ack,
            "skills": self.skills,
            "cd": {sid: round(t, 2) for sid, t in self.cooldowns.items() if t > 0},
            "icd": {iid: round(t, 1) for iid, t in self.item_cooldowns.items() if t > 0},
            "gold": self.inventory.gold,
            "ef": [{"id": eid, "t": round(a.remaining, 1)} for eid, a in self.effects.items()],
            "stats": {
                n: {"lv": s.level, "xp": int(s.xp), "next": s.xp_to_next()} for n, s in self.stats.items()
            },
        }

    # --- Persistência ---
    def to_dict(self) -> dict:
        return {
            "v": SAVE_VERSION,
            "race": self.race_id,
            "class": self.class_id,
            "flags": sorted(self.flags),
            "inventory": self.inventory.to_dict(),
            "map": self.map_id,
            "x": self.x,
            "y": self.y,
            "hp": self.hp,
            "energy": self.energy,
            "fatigue": self.fatigue,
            "facing": self.facing,
            "stats": {n: {"level": s.level, "xp": s.xp} for n, s in self.stats.items()},
        }

    def load_dict(self, data: dict) -> None:
        """Carrega o que for válido e ignora o resto (dados corrompidos).

        Espera um save já migrado (saves.migrate). Raça e classe são resolvidas
        pelo World antes, porque dependem do conteúdo carregado.
        """
        flags = data.get("flags")
        if isinstance(flags, list):
            self.flags = {f for f in flags if isinstance(f, str)}
        for key in ("x", "y", "hp", "energy", "fatigue"):
            value = data.get(key)
            if isinstance(value, (int, float)):
                setattr(self, key, float(value))
        if data.get("facing") in DIRECTIONS:
            self.facing = data["facing"]
        for name, raw in (data.get("stats") or {}).items():
            if name in self.stats and isinstance(raw, dict):
                level = raw.get("level", 1)
                xp = raw.get("xp", 0.0)
                if isinstance(level, int) and isinstance(xp, (int, float)):
                    self.stats[name] = Stat(max(1, min(level, C.MAX_STAT_LEVEL)), max(0.0, float(xp)))
        self.hp = max(1.0, min(self.hp, self.max_hp))
        self.energy = max(0.0, min(self.energy, self.max_energy))
        self.fatigue = max(0.0, min(self.fatigue, C.MAX_FATIGUE))


@dataclass(eq=False)
class Enemy(Entity):
    defn: EnemyDef | None = None
    home_x: float = 0.0
    home_y: float = 0.0
    target_id: int | None = None
    wander_dir: tuple[float, float] = (0.0, 0.0)
    wander_timer: float = 0.0
    detour_side: int = 1  # lado (+1/-1) para contornar obstáculos ao perseguir
    detour_timer: float = 0.0  # enquanto > 0, continua contornando em vez de ir reto

    kind: ClassVar[str] = "enemy"

    @property
    def max_hp(self) -> float:
        return self.defn.hp

    @property
    def base_speed(self) -> float:
        return self.defn.speed

    def snapshot(self) -> dict:
        snap = super().snapshot()
        snap["t"] = self.defn.id  # tipo, para o cliente escolher a aparência
        return snap


@dataclass(eq=False)
class Projectile:
    id: int
    owner_id: int
    skill_id: str
    x: float
    y: float
    vx: float
    vy: float
    damage: float
    ttl: float
    radius: float
    map_id: str = ""

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "s": self.skill_id,
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "vx": round(self.vx, 1),
            "vy": round(self.vy, 1),
        }


@dataclass(eq=False)
class GroundItem:
    """Item (ou moedas, se item for None) caído no chão."""

    id: int
    item: ItemDef | None
    qty: int
    x: float
    y: float
    owner_id: int | None  # só ele pega até owner_until
    owner_until: float
    expires_at: float
    ignore_id: int | None = None  # quem largou o item não pega de volta até ignore_until
    ignore_until: float = 0.0
    map_id: str = ""

    def snapshot(self) -> dict:
        return {"id": self.id, "i": self.item.id if self.item else "gold", "q": self.qty,
                "x": round(self.x), "y": round(self.y)}
