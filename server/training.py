"""Skills aprendidas, barra de atalhos e treinadores.

Cada personagem tem:
  - skills conhecidas (p.known): as iniciais da classe, as compradas de treinadores e
    as dadas por regras, diálogos ou missões (ação learn_skill). Nunca se perdem;
  - a barra (p.bar): C.BAR_SIZE espaços com as skills que o jogador escolheu usar.

Uma skill pode ser restrita a raças/classes (races/classes em skills.yaml). Se a raça ou
a classe mudar e a skill não servir mais, ela continua conhecida, mas fica bloqueada:
sai da barra e só volta a funcionar se o personagem voltar a poder usá-la.

A barra só muda fora de combate (C.BAR_COMBAT_LOCK segundos depois de atacar ou levar
dano, e nunca durante um duelo) e, se C.BAR_SAFE_ZONE_ONLY, só na zona segura.

Pedidos do cliente ({"t": "skills", "op": ...}):
  bar {slot, id}        põe a skill `id` no espaço `slot` (id null = esvazia). Se a
                        skill já estava em outro espaço, os dois trocam de lugar.

Treinadores (data/trainers.yaml) abrem por uma opção de diálogo (trainer: id); o
pedido de aprender passa pelo NpcSystem ({"t": "npc", "op": "learn", "skill": id}).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from . import config as C
from . import requirements

if TYPE_CHECKING:
    from .content import SkillDef, TrainerDef, TrainerEntry
    from .entities import Player
    from .world import World


def _ids(raw: Any) -> list[str]:
    """Lista de ids de um save, sem repetidos e sem lixo."""
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(v for v in raw if isinstance(v, str)))


class TrainingSystem:
    def __init__(self, world: World):
        self.world = world

    @property
    def skills(self) -> dict[str, SkillDef]:
        return self.world.content.skills

    # ------------------------------------------------------------ estado do personagem

    def usable(self, p: Player, skill_id: str) -> bool:
        """O personagem sabe a skill e a raça/classe atual pode usá-la."""
        skill = self.skills.get(skill_id)
        return skill is not None and skill_id in p.known and skill.usable_by(p.race_id, p.class_id)

    @staticmethod
    def load(p: Player, raw: Any) -> None:
        """Lê skills e barra do save, sem conferir com o conteúdo (quem confere é on_class_change)."""
        raw = raw if isinstance(raw, dict) else {}
        p.known = _ids(raw.get("known"))
        bar = raw.get("bar") if isinstance(raw.get("bar"), list) else []
        p.bar = [sid if isinstance(sid, str) else None for sid in bar[: C.BAR_SIZE]]

    def on_class_change(self, p: Player) -> None:
        """Depois de entrar numa classe (ou de recarregar o conteúdo): ajusta skills e barra.

        - esquece skills que deixaram de existir no conteúdo;
        - aprende as skills iniciais da classe;
        - tira da barra o que não pode mais usar e repetidos;
        - põe nos espaços vazios as iniciais que acabou de aprender (o que o jogador
          tirou da barra de propósito não volta sozinho), ou todas, se a barra ficou vazia.
        """
        p.known = [sid for sid in dict.fromkeys(p.known) if sid in self.skills]
        start = p.class_def.start_skills if p.class_def else ()
        new = [sid for sid in start if sid not in p.known]
        p.known += new
        bar: list[str | None] = []
        for sid in p.bar[: C.BAR_SIZE]:
            bar.append(sid if sid and sid not in bar and self.usable(p, sid) else None)
        bar += [None] * (C.BAR_SIZE - len(bar))
        for sid in (start if not any(bar) else new):
            if sid not in bar and None in bar:
                bar[bar.index(None)] = sid
        p.bar = bar
        p.actions = [a for a in p.actions if a[0] != "skill" or a[1] in p.bar]

    def learn(self, p: Player, skill_id: str) -> bool:
        """Aprende uma skill (sem custo nem requisito: quem chama já conferiu). Devolve se era nova."""
        skill = self.skills.get(skill_id)
        if skill is None or skill_id in p.known:
            return False
        p.known.append(skill_id)
        placed = ""
        if self.usable(p, skill_id) and None in p.bar:
            slot = p.bar.index(None)
            p.bar[slot] = skill_id
            placed = f" Ela foi para a barra (tecla {slot + 1})."
        elif not self.usable(p, skill_id):
            placed = " Sua raça/classe atual não consegue usá-la."
        self.world._private(p, f"Você aprendeu {skill.name}!{placed} Veja suas skills com K.")
        self.world._fx_at(p, {"k": "learn", "id": p.id, "x": round(p.x), "y": round(p.y), "t": skill.name})
        return True

    def mark_combat(self, p: Player) -> None:
        if C.BAR_COMBAT_LOCK > 0:
            p.combat_until = max(p.combat_until, self.world.time + C.BAR_COMBAT_LOCK)

    def bar_lock_reason(self, p: Player) -> str | None:
        """Por que a barra não pode mudar agora (None = pode)."""
        if not p.alive:
            return "Não dá para mudar a barra morto."
        if self.world.duels.opponent_id(p) is not None:
            return "Não dá para mudar a barra durante um duelo."
        if self.world.time < p.combat_until:
            return f"Em combate: espere {p.combat_until - self.world.time:.0f} s para mudar a barra."
        if C.BAR_SAFE_ZONE_ONLY and self.world._map(p).zone_at(p.x, p.y).type != "safe":
            return "A barra só pode ser mudada na zona segura."
        return None

    def set_bar(self, p: Player, slot: object, skill_id: object) -> bool:
        if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < C.BAR_SIZE:
            return False
        if skill_id is not None and not (isinstance(skill_id, str) and self.usable(p, skill_id)):
            return False
        if (reason := self.bar_lock_reason(p)) is not None:
            self.world._private(p, reason)
            return False
        if skill_id is not None and skill_id in p.bar:  # já estava em outro espaço: trocam de lugar
            p.bar[p.bar.index(skill_id)] = p.bar[slot]
        p.bar[slot] = skill_id
        p.actions = [a for a in p.actions if a[0] != "skill" or a[1] in p.bar]
        return True

    def handle_op(self, p: Player, data: dict) -> None:
        if data.get("op") == "bar":
            self.set_bar(p, data.get("slot"), data.get("id"))

    def client_info(self, p: Player) -> list[dict]:
        """Skills conhecidas para a janela de skills (ok = pode usar com a raça/classe atual)."""
        return [{"id": sid, "ok": self.usable(p, sid)} for sid in p.known]

    def snapshot_for(self, p: Player) -> list[dict] | None:
        """As skills conhecidas só vão ao cliente quando mudam."""
        info = self.client_info(p)
        key = json.dumps(info)
        if key == p.skills_sent:
            return None
        p.skills_sent = key
        return info

    # ------------------------------------------------------------ treinadores

    def missing(self, p: Player, entry: TrainerEntry) -> list[str]:
        """Requisitos que faltam para aprender (vazio = pode). O preço é conferido à parte."""
        skill = self.skills[entry.skill]
        content = self.world.content
        lines = []
        if not skill.usable_by(p.race_id, p.class_id):
            table, ids = (content.classes, skill.classes) if skill.classes else (content.races, skill.races)
            lines.append("Só para: " + ", ".join(table[i].name for i in ids if i in table))
        return lines + requirements.missing(self.world, p, entry.stats, entry.skills, entry.rule)

    def trainer_payload(self, p: Player, trainer: TrainerDef) -> list[dict]:
        rows = []
        for entry in trainer.teaches:
            known = entry.skill in p.known
            rows.append({
                "id": entry.skill,
                "price": entry.price,
                "known": known,
                "missing": [] if known else self.missing(p, entry),
            })
        return rows

    def teach(self, p: Player, trainer: TrainerDef, skill_id: object) -> bool:
        entry = trainer.entry(skill_id) if isinstance(skill_id, str) else None
        if entry is None:
            return False
        skill = self.skills[entry.skill]
        if entry.skill in p.known:
            self.world._private(p, f"Você já conhece {skill.name}.")
            return False
        if missing := self.missing(p, entry):
            self.world._private(p, f"Ainda não dá para aprender {skill.name}: {'; '.join(missing)}.")
            return False
        if p.inventory.gold < entry.price:
            self.world._private(p, f"Faltam {entry.price - p.inventory.gold} moedas para aprender {skill.name}.")
            return False
        if entry.price:
            p.inventory.add_gold(-entry.price)
            self.world.economy.record("treinador", trainer.id, entry.price)
        return self.learn(p, entry.skill)
