"""Regras de gatilho: "quando <evento>, se <condições>, faça <ações>".

As regras ficam em data/rules.yaml e são validadas em content.py. Este módulo
define o vocabulário (eventos, campos de contexto, ações) e como uma regra é
comparada com o contexto de um evento. Quem executa as ações é o World.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

# Campos de contexto de cada evento: são os nomes que podem aparecer em "if".
# Toda regra também recebe "race" e "class" de quem disparou o evento.
EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    # o personagem morreu
    "death": ("race", "class", "killer", "killer_enemy", "killer_race", "killer_class",
              "skill", "effect", "effects", "zone", "duel"),
    # o personagem matou alguém (jogador ou inimigo)
    "kill": ("race", "class", "victim", "victim_enemy", "victim_race", "victim_class", "skill", "zone"),
    # um atributo do personagem subiu de nível
    "level_up": ("race", "class", "stat", "level"),
    # um duelo do personagem terminou (dispara para os dois)
    "duel_end": ("race", "class", "result", "reason", "opponent_race", "opponent_class"),
    # o cargo do personagem numa organização mudou (entrou, subiu, caiu, saiu...)
    "rank_change": ("race", "class", "org", "rank", "old_rank", "cause"),
    # o personagem concluiu uma missão (data/quests.yaml)
    "quest_complete": ("race", "class", "quest"),
    # o personagem criou um item numa oficina (data/crafting.yaml); dispara uma vez por criação
    "craft": ("race", "class", "item", "station"),
    # um evento em ondas (data/events.yaml) terminou; dispara para quem participou (causou dano)
    #   result: win (venceram todas as ondas) | lose (acabou o tempo); damage: dano que o personagem causou
    "event_end": ("race", "class", "event", "result", "damage"),
}
# Todo evento também recebe a organização e o cargo atuais de quem disparou
EVENT_FIELDS = {
    event: fields + tuple(f for f in ("org", "rank") if f not in fields) for event, fields in EVENT_FIELDS.items()
}

# Condições que não vêm do contexto do evento
SPECIAL_CONDITIONS = ("chance", "flags", "not_flags")

ACTIONS = ("message", "broadcast", "lose_xp", "gain_xp", "set_class", "set_race",
           "set_flag", "clear_flag", "add_effect", "give_item", "give_gold", "lose_gold", "start_quest",
           "learn_skill")

KILLER_KINDS = ("player", "enemy", "none")  # "none": morreu sem autor (ex.: veneno de quem já saiu)
VICTIM_KINDS = ("player", "enemy")
ZONES = ("safe", "pve", "pvp")
DUEL_RESULTS = ("win", "lose", "draw")
# defeat: o perdedor chegou a 1 de vida | forfeit: alguém se rendeu | logout: alguém saiu
# distance: se afastaram demais | timeout: passou do tempo | death: alguém morreu (ex.: para um inimigo)
DUEL_END_REASONS = ("defeat", "forfeit", "logout", "distance", "timeout", "death")
# join: entrou | leave: saiu | expelled: expulso | promotion: promovido por líder | demotion: rebaixado
# claim: assumiu cargo vago | dispute_won / dispute_lost: ganhou ou perdeu o cargo num duelo de disputa
# race: saiu porque a raça mudou para uma que a organização não aceita
RANK_CAUSES = ("join", "leave", "expelled", "promotion", "demotion", "claim", "dispute_won", "dispute_lost", "race")


@dataclass(frozen=True)
class Rule:
    id: str
    when: str
    conditions: tuple[tuple[str, object], ...]  # (campo, valor esperado); "chance" sempre por último
    actions: tuple[tuple[str, object], ...]  # (ação, argumentos já validados)
    stop: bool = False


def condition_matches(expected: object, actual: object) -> bool:
    """Compara uma condição com o valor do contexto.

    - mapa {min, max}: faixa numérica;
    - lista: qualquer um dos valores serve;
    - se o contexto for uma lista (ex.: efeitos ativos), basta conter um dos valores.
    """
    if isinstance(expected, dict):
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            return False
        return expected.get("min", -math.inf) <= actual <= expected.get("max", math.inf)
    options = expected if isinstance(expected, list) else [expected]
    if isinstance(actual, (list, tuple, set, frozenset)):
        return any(option in actual for option in options)
    if isinstance(actual, bool) or any(isinstance(o, bool) for o in options):
        return any(type(o) is type(actual) and o == actual for o in options)  # evita True == 1
    return actual in options


def rule_matches(rule: Rule, ctx: dict, flags: set[str], rng: random.Random) -> bool:
    for key, expected in rule.conditions:
        if key == "chance":
            if rng.random() >= expected:  # type: ignore[operator]
                return False
        elif key == "flags":
            if not set(expected) <= flags:  # type: ignore[arg-type]
                return False
        elif key == "not_flags":
            if set(expected) & flags:  # type: ignore[arg-type]
                return False
        elif not condition_matches(expected, ctx.get(key)):
            return False
    return True
