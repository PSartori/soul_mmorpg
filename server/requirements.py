"""Requisitos de treinadores e receitas: o que falta para o personagem, em texto pronto.

Um requisito tem três partes, todas opcionais:
  stats   nível mínimo de atributos  {strength: 5}
  skills  skills que precisa conhecer [rajada]
  rule    condições dos diálogos (race, class, org, rank, total_level, quest_done,
          quest_active, flags, not_flags)

missing() devolve a lista do que falta, uma linha por requisito, para mostrar ao jogador.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .orgs import total_level
from .rules import Rule, rule_matches
from .stats import STAT_LABELS

if TYPE_CHECKING:
    from .entities import Player
    from .world import World


def conditions_hold(world: World, p: Player, rule: Rule) -> bool:
    """As condições (if) valem para o personagem agora?"""
    return rule_matches(rule, world._player_context(p), p.flags, world.rng)


def missing(world: World, p: Player, stats: dict[str, int], skills: tuple[str, ...], rule: Rule) -> list[str]:
    """O que falta (vazio = cumpre tudo)."""
    content = world.content
    lines = []
    for stat, level in stats.items():
        if p.stat_level(stat) < level:
            lines.append(f"{STAT_LABELS[stat]} nível {level} (você: {p.stat_level(stat)})")
    for sid in skills:
        if sid not in p.known:
            lines.append(f"Conhecer {getattr(content.skills.get(sid), 'name', sid)}")
    ctx = world._player_context(p)
    for key, value in rule.conditions:
        single = Rule(id=rule.id, when=rule.when, conditions=((key, value),), actions=())
        if not rule_matches(single, ctx, p.flags, world.rng):
            lines.append(condition_label(world, p, key, value))
    return lines


def condition_label(world: World, p: Player, key: str, value: Any) -> str:
    content = world.content
    values = value if isinstance(value, list) else [value]

    def names(table: dict) -> str:
        return ", ".join(getattr(table.get(v), "name", str(v)) for v in values)

    if key == "total_level" and isinstance(value, dict):
        if "min" in value:
            return f"Nível total {value['min']:g} (você: {total_level(p)})"
        return f"Nível total até {value['max']:g}"
    if key == "gold" and isinstance(value, dict):
        return f"Ter {value.get('min', 0):g} moedas"
    if key == "race":
        return f"Raça: {names(content.races)}"
    if key == "class":
        return f"Classe: {names(content.classes)}"
    if key == "org":
        return f"Ser de: {names(content.orgs)}"
    if key == "rank":
        return f"Cargo: {', '.join(map(str, values))}"
    if key == "quest_done":
        return f"Concluir a missão {names(content.quests)}"
    if key == "quest_active":
        return f"Estar na missão {names(content.quests)}"
    return "Um requisito especial"  # marcas (flags) não são mostradas ao jogador
