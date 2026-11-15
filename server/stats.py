"""Atributos treináveis: cada um tem nível e XP próprios (treino por uso)."""
from __future__ import annotations

from dataclasses import dataclass

from . import config as C

STAT_NAMES = ("strength", "spirit", "agility", "vitality")
STAT_LABELS = {
    "strength": "Força",
    "spirit": "Espírito",
    "agility": "Agilidade",
    "vitality": "Vitalidade",
}


@dataclass
class Stat:
    level: int = 1
    xp: float = 0.0

    def xp_to_next(self) -> int:
        return round(30 * self.level**1.6)

    def add_xp(self, amount: float) -> int:
        """Soma XP e devolve quantos níveis foram ganhos."""
        if self.level >= C.MAX_STAT_LEVEL:
            return 0
        self.xp += amount
        gained = 0
        while self.level < C.MAX_STAT_LEVEL and self.xp >= self.xp_to_next():
            self.xp -= self.xp_to_next()
            self.level += 1
            gained += 1
        if self.level >= C.MAX_STAT_LEVEL:
            self.xp = 0.0
        return gained
