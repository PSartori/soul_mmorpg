"""Entidades do mundo: jogadores, inimigos e projéteis."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from . import config as C
from .stats import STAT_NAMES, Stat

DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
MOVE_KEYS = ("up", "down", "left", "right")


@dataclass(eq=False)
class Entity:
    id: int
    name: str
    x: float
    y: float
    hp: float = 1.0
    facing: str = "down"
    state: str = "idle"  # idle | walk | meditate | rest | dead
    attack_cd: float = 0.0
    dead_timer: float = 0.0
    half: float = C.ENTITY_HALF

    kind: ClassVar[str] = "?"

    @property
    def alive(self) -> bool:
        return self.state != "dead"

    @property
    def max_hp(self) -> float:
        raise NotImplementedError

    def snapshot(self) -> dict:
        """O que todo mundo vê desta entidade."""
        return {
            "id": self.id,
            "k": self.kind,
            "n": self.name,
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "f": self.facing,
            "s": self.state,
            "hp": round(self.hp),
            "mhp": round(self.max_hp),
        }


@dataclass(eq=False)
class Player(Entity):
    energy: float = 0.0
    fatigue: float = 0.0
    blast_cd: float = 0.0
    stats: dict[str, Stat] = field(default_factory=lambda: {n: Stat() for n in STAT_NAMES})
    input: dict[str, bool] = field(default_factory=lambda: dict.fromkeys(MOVE_KEYS, False))
    actions: list[str] = field(default_factory=list)

    kind: ClassVar[str] = "player"

    def level(self, stat: str) -> int:
        return self.stats[stat].level

    # --- Valores derivados dos atributos ---
    @property
    def max_hp(self) -> float:
        return 80 + 20 * self.level("vitality")

    @property
    def max_energy(self) -> float:
        return 40 + 15 * self.level("spirit")

    @property
    def speed(self) -> float:
        return min(C.MAX_SPEED, C.BASE_SPEED + C.SPEED_PER_AGILITY * self.level("agility"))

    @property
    def melee_damage(self) -> float:
        return 6 + 2 * self.level("strength")

    @property
    def blast_damage(self) -> float:
        return 8 + 3 * self.level("spirit")

    def private_snapshot(self) -> dict:
        """O que só o próprio jogador vê (HUD)."""
        return {
            "hp": round(self.hp),
            "mhp": round(self.max_hp),
            "en": round(self.energy),
            "men": round(self.max_energy),
            "fat": round(self.fatigue),
            "mfat": round(C.MAX_FATIGUE),
            "s": self.state,
            "stats": {
                n: {"lv": s.level, "xp": int(s.xp), "next": s.xp_to_next()} for n, s in self.stats.items()
            },
        }

    # --- Persistência ---
    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "hp": self.hp,
            "energy": self.energy,
            "fatigue": self.fatigue,
            "facing": self.facing,
            "stats": {n: {"level": s.level, "xp": s.xp} for n, s in self.stats.items()},
        }

    def load_dict(self, data: dict) -> None:
        """Carrega o que for válido e ignora o resto (dados antigos ou corrompidos)."""
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
    home_x: float = 0.0
    home_y: float = 0.0
    base_hp: float = C.ENEMY_HP
    damage: float = C.ENEMY_DAMAGE
    target_id: int | None = None
    wander_dir: tuple[float, float] = (0.0, 0.0)
    wander_timer: float = 0.0

    kind: ClassVar[str] = "enemy"

    @property
    def max_hp(self) -> float:
        return self.base_hp


@dataclass(eq=False)
class Projectile:
    id: int
    owner_id: int
    x: float
    y: float
    vx: float
    vy: float
    damage: float
    ttl: float

    def snapshot(self) -> dict:
        return {"id": self.id, "x": round(self.x, 1), "y": round(self.y, 1), "vx": self.vx, "vy": self.vy}
