"""Missões: objetivos, progresso, entrega e recompensas.

Conteúdo em data/quests.yaml. O estado de cada personagem fica no save:
  Player.quests       missões em andamento: id -> progresso de cada objetivo
  Player.quests_done  missões concluídas: id -> [vezes, horário da última]

Como uma missão anda:
  - começa pelo NPC que a oferece (opção "Missão: ..." que o npcs.py coloca na
    conversa) ou pela ação start_quest de um diálogo/regra;
  - kill e talk são contados quando acontecem; reach é conferido a cada tick;
    collect (itens na bolsa) e flag (marca) são medidos na hora;
  - com todos os objetivos cumpridos fica "pronta": entrega-se ao NPC turn_in
    (opção "Entregar: ...") ou, sem turn_in, conclui sozinha;
  - ao concluir: tira os itens de collect, roda as recompensas e dispara o
    evento quest_complete das regras.
"""
from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING

from . import config as C
from .rules import rule_matches

if TYPE_CHECKING:
    from .content import QuestDef, QuestObjective
    from .entities import Player
    from .world import World


class QuestSystem:
    def __init__(self, world: World):
        self.world = world

    @property
    def defs(self) -> dict[str, QuestDef]:
        return self.world.content.quests

    # ------------------------------------------------------------ estado do personagem

    def normalize(self, p: Player) -> None:
        """Ajusta o save ao conteúdo atual: missões que sumiram saem; o progresso acompanha os objetivos."""
        for qid in list(p.quests):
            quest = self.defs.get(qid)
            if quest is None:
                del p.quests[qid]
                continue
            old = p.quests[qid]
            p.quests[qid] = [min(old[i], o.count) if i < len(old) else 0 for i, o in enumerate(quest.objectives)]
        p.quests_done = {qid: v for qid, v in p.quests_done.items() if qid in self.defs}
        p.quests_ready.intersection_update(p.quests)
        p.quests_sent = ""

    def value(self, p: Player, obj: QuestObjective, stored: int) -> int:
        """Progresso atual de um objetivo (contado ou medido na hora), no máximo o pedido."""
        if obj.kind == "collect":
            return min(p.inventory.count(obj.target), obj.count)
        if obj.kind == "flag":
            return int(obj.target in p.flags)
        return min(stored, obj.count)

    def progress(self, p: Player, quest: QuestDef) -> list[int]:
        stored = p.quests.get(quest.id, [])
        return [self.value(p, o, stored[i] if i < len(stored) else 0) for i, o in enumerate(quest.objectives)]

    def is_ready(self, p: Player, quest: QuestDef) -> bool:
        return quest.id in p.quests and all(
            v >= o.count for v, o in zip(self.progress(p, quest), quest.objectives))

    def cooldown_left(self, p: Player, quest: QuestDef) -> float:
        done = p.quests_done.get(quest.id)
        if not done:
            return 0.0
        return max(0.0, done[1] + quest.cooldown - self.world.clock())

    def available(self, p: Player, quest: QuestDef) -> bool:
        """Pode começar agora: não está em andamento, não foi concluída (ou pode repetir) e as condições batem."""
        if quest.id in p.quests:
            return False
        if quest.id in p.quests_done and (not quest.repeatable or self.cooldown_left(p, quest) > 0):
            return False
        return rule_matches(quest.rule, self.world._player_context(p), p.flags, self.world.rng)

    # ------------------------------------------------------------ ações

    def start(self, p: Player, quest_id: str) -> bool:
        quest = self.defs.get(quest_id)
        if quest is None or not self.available(p, quest):
            return False
        if len(p.quests) >= C.MAX_ACTIVE_QUESTS:
            self.world._private(p, f"Você já tem {C.MAX_ACTIVE_QUESTS} missões em andamento. Conclua ou abandone uma.")
            return False
        p.quests[quest.id] = [0] * len(quest.objectives)
        self.world._private(p, f"Nova missão: {quest.name}. Veja o diário (J).")
        return True

    def abandon(self, p: Player, quest_id: object) -> None:
        if isinstance(quest_id, str) and p.quests.pop(quest_id, None) is not None:
            p.quests_ready.discard(quest_id)
            self.world._private(p, f"Missão abandonada: {self.defs[quest_id].name}.")

    def complete(self, p: Player, quest_id: str) -> bool:
        """Conclui uma missão pronta: entrega os itens, dá as recompensas e dispara quest_complete."""
        quest = self.defs.get(quest_id)
        if quest is None or not self.is_ready(p, quest):
            return False
        for obj in quest.objectives:
            if obj.kind == "collect":
                p.inventory.remove_item(obj.target, obj.count)
        del p.quests[quest.id]
        p.quests_ready.discard(quest.id)
        count = p.quests_done.get(quest.id, [0, 0.0])[0]
        p.quests_done[quest.id] = [count + 1, self.world.clock()]
        self.world._private(p, f"Missão concluída: {quest.name}!")
        self.world._fx_at(p, {"k": "quest", "id": p.id, "x": round(p.x), "y": round(p.y)})
        ctx = self.world._player_context(p)
        for name, args in quest.rule.actions:
            self.world._do_action(p, name, args, ctx, 0)
        self.world._fire("quest_complete", p, {"quest": quest.id})
        return True

    def handle_op(self, p: Player, data: dict) -> None:
        if data.get("op") == "abandon":
            self.abandon(p, data.get("id"))

    # ------------------------------------------------------------ eventos do jogo

    def on_kill(self, p: Player, enemy_id: str) -> None:
        self._count(p, "kill", enemy_id)

    def on_talk(self, p: Player, npc_id: str) -> None:
        self._count(p, "talk", npc_id)

    def _count(self, p: Player, kind: str, target: str) -> None:
        for qid, stored in p.quests.items():
            quest = self.defs[qid]
            for i, obj in enumerate(quest.objectives):
                if obj.kind == kind and obj.target == target and stored[i] < obj.count:
                    stored[i] += 1
                    if obj.count > 1:
                        label = self.world.content.objective_label(obj)
                        self.world._private(p, f"{quest.name}: {label} {stored[i]}/{obj.count}")

    def update(self) -> None:
        """A cada tick: objetivos de lugar (reach) e missões que acabaram de ficar prontas."""
        for p in self.world.players.values():
            if not p.quests or not p.alive:
                continue
            tilemap = self.world._map(p)
            ts = tilemap.tile_size
            tx, ty = math.floor(p.x / ts), math.floor(p.y / ts)
            for qid in list(p.quests):
                quest = self.defs[qid]
                stored = p.quests[qid]
                for i, obj in enumerate(quest.objectives):
                    if obj.kind == "reach" and not stored[i] and p.map_id == obj.target and (
                            obj.area is None
                            or (obj.area[0] <= tx <= obj.area[2] and obj.area[1] <= ty <= obj.area[3])):
                        stored[i] = 1
                ready = self.is_ready(p, quest)
                if not ready:
                    p.quests_ready.discard(qid)
                elif qid not in p.quests_ready:
                    p.quests_ready.add(qid)
                    if quest.turn_in is None:
                        self.complete(p, qid)
                    else:
                        who = self.world.content.npcs[quest.turn_in].name
                        self.world._private(p, f"Missão pronta: {quest.name}. Fale com {who} para entregar.")

    # ------------------------------------------------------------ NPCs

    def offers(self, p: Player, npc_id: str) -> list[QuestDef]:
        """Missões que este NPC pode oferecer agora."""
        return [q for q in self.defs.values() if q.giver == npc_id and self.available(p, q)]

    def offer_text(self, p: Player, quest: QuestDef) -> str:
        return self.world._format(quest.description or quest.name, p, {})

    def turn_ins(self, p: Player, npc_id: str) -> list[QuestDef]:
        """Missões prontas para entregar a este NPC."""
        return [self.defs[qid] for qid in p.quests
                if self.defs[qid].turn_in == npc_id and self.is_ready(p, self.defs[qid])]

    # ------------------------------------------------------------ cliente

    def client_info(self, p: Player) -> dict:
        """Diário e marcadores (! tem missão, ? pronta para entregar) dos NPCs por perto."""
        marks = {}
        radius2 = C.VIEW_RADIUS ** 2
        for npc in self.world.npcs.entities.values():
            if npc.map_id != p.map_id or (npc.x - p.x) ** 2 + (npc.y - p.y) ** 2 > radius2:
                continue
            if self.turn_ins(p, npc.defn.id):
                marks[npc.id] = "?"
            elif self.offers(p, npc.defn.id):
                marks[npc.id] = "!"
        return {
            "active": [{"id": qid, "p": self.progress(p, self.defs[qid]), "ready": self.is_ready(p, self.defs[qid])}
                       for qid in p.quests],
            "done": sorted(p.quests_done),
            "marks": marks,
        }

    def snapshot_for(self, p: Player) -> dict | None:
        """O diário só vai ao cliente quando muda."""
        info = self.client_info(p)
        key = json.dumps(info, sort_keys=True)
        if key == p.quests_sent:
            return None
        p.quests_sent = key
        return info
