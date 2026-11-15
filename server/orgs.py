"""Organizações (facções) e cargos, definidos em data/organizations.yaml.

Comandos (chat):
  /org [id]             mostra sua organização (cargos e ocupantes) ou uma organização
  /entrar id            entra numa organização (raça e nível mínimo)
  /sair                 sai da organização
  /o texto              fala só com os membros online da sua organização
  /assumir cargo        assume um cargo de disputa que esteja vago
  /disputar Nome        desafia o ocupante de um cargo de disputa acima do seu para um duelo
                        valendo o cargo (quem vence fica com o cargo; o outro fica com o seu)
  /promover Nome cargo  líder: promove alguém abaixo dele a um cargo de nomeação
  /rebaixar Nome        líder: devolve alguém abaixo dele ao cargo inicial
  /expulsar Nome        líder: tira alguém abaixo dele da organização

Os vínculos ficam no banco (tabela memberships), fora do save do personagem:
cargos são vagas globais e continuam ocupados com o dono offline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import config as C
from .content import OrgDef, RankDef

if TYPE_CHECKING:
    from .entities import Player
    from .world import World

log = logging.getLogger("orgs")


@dataclass
class Membership:
    name: str  # nome do personagem (grafia original)
    org: str
    rank: str
    seat: int | None  # número da vaga em cargos com vagas limitadas


def total_level(p: Player) -> int:
    return sum(s.level for s in p.stats.values())


class OrgSystem:
    def __init__(self, world: World):
        self.world = world
        self.content = world.content
        self.members: dict[str, Membership] = {}  # nome em minúsculas -> vínculo
        self.dispute_cooldowns: dict[str, float] = {}  # nome em minúsculas -> até quando não pode disputar
        for name, org_id, rank_id, seat in world.db.load_memberships():
            org = self.content.orgs.get(org_id)
            if org is None or org.rank(rank_id) is None:
                log.warning("vínculo de %s com %s/%s removido: não existe mais no conteúdo", name, org_id, rank_id)
                world.db.delete_membership(name)
                continue
            self.members[name.lower()] = Membership(name, org_id, rank_id, seat)

    # ------------------------------------------------------------ consultas

    def of(self, who: Player | str) -> Membership | None:
        name = who if isinstance(who, str) else who.name
        return self.members.get(name.lower())

    def org_rank(self, m: Membership) -> tuple[OrgDef, RankDef]:
        org = self.content.orgs[m.org]
        return org, org.rank(m.rank)  # type: ignore[return-value]

    def title(self, m: Membership) -> str:
        _, rank = self.org_rank(m)
        return f"{rank.name} {m.seat}" if rank.numbered and m.seat else rank.name

    def holders(self, org_id: str, rank_id: str) -> list[Membership]:
        return [m for m in self.members.values() if m.org == org_id and m.rank == rank_id]

    def next_seat(self, org_id: str, rank: RankDef) -> int | None:
        """Menor vaga livre do cargo (None se lotado). Cargo ilimitado não usa vaga: devolve 0."""
        if rank.seats is None:
            return 0
        taken = {m.seat for m in self.holders(org_id, rank.id)}
        return next((n for n in range(1, rank.seats + 1) if n not in taken), None)

    def snapshot(self, p: Player) -> list | None:
        m = self.of(p)
        return [m.org, m.rank, m.seat] if m else None

    def _online(self, name: str) -> Player | None:
        return next((p for p in self.world.players.values() if p.name.lower() == name.lower()), None)

    # ------------------------------------------------------------ mudanças (sempre por aqui)

    def _set(self, name: str, org_id: str, rank_id: str, seat: int | None, cause: str) -> None:
        old = self.members.get(name.lower())
        old_rank = old.rank if old and old.org == org_id else None
        rank = self.content.orgs[org_id].rank(rank_id)
        seat = seat if rank and rank.seats is not None else None
        self.members[name.lower()] = Membership(name, org_id, rank_id, seat)
        self.world.db.save_membership(name, org_id, rank_id, seat)
        player = self._online(name)
        if player:
            self.world._fire("rank_change", player, {"org": org_id, "rank": rank_id, "old_rank": old_rank,
                                                    "cause": cause})

    def _remove(self, name: str, cause: str) -> None:
        old = self.members.pop(name.lower(), None)
        if old is None:
            return
        self.world.db.delete_membership(name)
        player = self._online(name)
        if player:
            self.world._fire("rank_change", player, {"org": old.org, "rank": None, "old_rank": old.rank,
                                                    "cause": cause})

    def _tell_org(self, org_id: str, text: str) -> None:
        for p in self.world.players.values():
            m = self.of(p)
            if m and m.org == org_id:
                self.world._private(p, text)

    # ------------------------------------------------------------ comandos

    def handle(self, p: Player, command: str, args: list[str], raw_text: str) -> bool:
        """Trata um comando de chat. Devolve False se não for comando de organização."""
        handlers = {
            "/org": lambda: self.info(p, args[0] if args else None),
            "/entrar": lambda: self.join(p, args[0]) if args else self._usage(p, "/entrar id"),
            "/sair": lambda: self.leave(p),
            "/o": lambda: self.chat(p, raw_text.split(" ", 1)[1] if " " in raw_text else ""),
            "/assumir": lambda: self.claim(p, args[0]) if args else self._usage(p, "/assumir cargo"),
            "/disputar": lambda: self.dispute(p, args[0]) if args else self._usage(p, "/disputar Nome"),
            "/promover": lambda: self.promote(p, args[0], args[1]) if len(args) >= 2 else self._usage(p, "/promover Nome cargo"),
            "/rebaixar": lambda: self.demote(p, args[0]) if args else self._usage(p, "/rebaixar Nome"),
            "/expulsar": lambda: self.expel(p, args[0]) if args else self._usage(p, "/expulsar Nome"),
        }
        handler = handlers.get(command)
        if handler is None:
            return False
        handler()
        return True

    def handle_op(self, p: Player, data: dict) -> None:
        """Pedidos da janela da organização / do menu do jogador: {"t": "org", "op": ...}."""
        op = data.get("op")
        target = data.get("target") if isinstance(data.get("target"), str) else ""
        org_id = data.get("org") if isinstance(data.get("org"), str) else ""
        rank_id = data.get("rank") if isinstance(data.get("rank"), str) else ""
        actions = {
            "info": lambda: None,
            "join": lambda: self.join(p, org_id),
            "leave": lambda: self.leave(p),
            "claim": lambda: self.claim(p, rank_id),
            "dispute": lambda: self.dispute(p, target),
            "promote": lambda: self.promote(p, target, rank_id),
            "demote": lambda: self.demote(p, target),
            "expel": lambda: self.expel(p, target),
        }
        if op not in actions:
            return
        actions[op]()
        self.world._outbox.append((p.id, self.window_data(p)))

    def window_data(self, p: Player) -> dict:
        """Tudo o que a janela da organização mostra, já com o que p pode fazer."""
        w = self.world
        m = self.of(p)
        level = total_level(p)
        orgs = []
        for o in self.content.orgs.values():
            reason = None
            if o.races and p.race_id not in o.races:
                reason = f"não aceita {p.race_def.name if p.race_def else 'sua raça'}"
            elif level < o.min_total_level:
                reason = f"exige nível total {o.min_total_level}"
            orgs.append({"id": o.id, "can_join": m is None and reason is None, "reason": reason})
        data: dict[str, Any] = {"t": "org_data", "total_level": level, "orgs": orgs, "mine": None}
        if m is None:
            return data
        org, rank = self.org_rank(m)
        my_index = org.rank_index(rank.id)
        wait = max(0.0, self.dispute_cooldowns.get(p.name.lower(), 0.0) - w.time)
        ranks = []
        for i, r in enumerate(org.ranks):
            holders = sorted(self.holders(org.id, r.id), key=lambda h: (h.seat or 0, h.name.lower()))
            ranks.append({
                "id": r.id,
                "holders": [{"name": h.name, "seat": h.seat, "online": self._online(h.name) is not None}
                            for h in holders],
                "can_claim": (r.promotion == "challenge" and i < my_index and level >= r.min_total_level
                              and self.next_seat(org.id, r) is not None),
            })
        members = []
        for other in sorted((x for x in self.members.values() if x.org == org.id), key=lambda x: x.name.lower()):
            index = org.rank_index(other.rank)
            other_rank = org.rank(other.rank)
            online = self._online(other.name)
            below = index > my_index
            leader = rank.leader and below
            members.append({
                "name": other.name,
                "title": self.title(other),
                "online": online is not None,
                "me": other.name.lower() == p.name.lower(),
                "promote_to": [x.id for x in org.ranks if leader and x.promotion == "appointment"
                               and org.rank_index(x.id) > my_index and x.id != other.rank] if online else [],
                "can_demote": leader and other.rank != org.default_rank,
                "can_expel": leader,
                "can_dispute": (other_rank is not None and other_rank.promotion == "challenge" and index < my_index
                                and online is not None and level >= other_rank.min_total_level and wait <= 0),
            })
        data["mine"] = {"org": org.id, "rank": rank.id, "title": self.title(m), "leader": rank.leader,
                        "ranks": ranks, "members": members, "dispute_wait": round(wait)}
        return data

    def _usage(self, p: Player, usage: str) -> None:
        self.world._private(p, f"Use: {usage}")

    def info(self, p: Player, org_id: str | None) -> None:
        w = self.world
        mine = self.of(p)
        org = self.content.orgs.get(org_id) if org_id else (self.content.orgs[mine.org] if mine else None)
        if org is None:
            lines = ["Organizações:"]
            for o in self.content.orgs.values():
                races = ", ".join(self.content.races[r].name for r in o.races) or "qualquer raça"
                lines.append(f"• {o.name} (/entrar {o.id}) — {races}; nível total mínimo {o.min_total_level}.")
            w._private(p, " ".join(lines))
            return
        parts = [f"{org.name}:"]
        for rank in org.ranks:
            holders = sorted(self.holders(org.id, rank.id), key=lambda m: (m.seat or 0, m.name.lower()))
            names = ", ".join(
                (f"{m.seat}. " if rank.numbered and m.seat else "") + m.name + (" •" if self._online(m.name) else "")
                for m in holders
            ) or "vago"
            seats = f" [{len(holders)}/{rank.seats}]" if rank.seats else ""
            parts.append(f"{rank.name}{seats}: {names}.")
        w._private(p, " ".join(parts) + " (• = online)")

    def join(self, p: Player, org_id: str) -> None:
        w = self.world
        org = self.content.orgs.get(org_id)
        current = self.of(p)
        if org is None:
            w._private(p, f"Organização desconhecida: {org_id}.")
        elif current:
            w._private(p, f"Você já faz parte de {self.content.orgs[current.org].name}. Saia dela antes.")
        elif org.races and p.race_id not in org.races:
            w._private(p, f"{org.name} não aceita {p.race_def.name if p.race_def else 'sua raça'}.")
        elif total_level(p) < org.min_total_level:
            w._private(p, f"{org.name} exige nível total {org.min_total_level} (o seu é {total_level(p)}).")
        else:
            self._set(p.name, org.id, org.default_rank, None, "join")
            self._tell_org(org.id, f"{p.name} entrou para {org.name}.")

    def leave(self, p: Player) -> None:
        m = self.of(p)
        if m is None:
            self.world._private(p, "Você não faz parte de nenhuma organização.")
            return
        org = self.content.orgs[m.org]
        self._remove(p.name, "leave")
        self.world._private(p, f"Você saiu de {org.name}.")
        self._tell_org(org.id, f"{p.name} saiu de {org.name}.")

    def chat(self, p: Player, text: str) -> None:
        m = self.of(p)
        text = text.strip()[: C.CHAT_MAX_LEN]
        if m is None:
            self.world._private(p, "Você não faz parte de nenhuma organização.")
            return
        if muted := self.world.admin.mute_message(p):
            self.world._private(p, muted)
            return
        if not text:
            return
        org = self.content.orgs[m.org]
        for other in self.world.players.values():
            om = self.of(other)
            if om and om.org == m.org:
                self.world._outbox.append(
                    (other.id, {"t": "chat", "from": p.name, "text": text, "ch": org.name, "color": org.color})
                )

    def claim(self, p: Player, rank_id: str) -> None:
        w = self.world
        m = self.of(p)
        if m is None:
            w._private(p, "Você não faz parte de nenhuma organização.")
            return
        org, current = self.org_rank(m)
        rank = org.rank(rank_id)
        if rank is None:
            w._private(p, f"{org.name} não tem o cargo {rank_id}.")
        elif rank.promotion != "challenge":
            w._private(p, f"{rank.name} não se assume: é por nomeação de um líder.")
        elif org.rank_index(rank.id) >= org.rank_index(current.id):
            w._private(p, f"{rank.name} não está acima do seu cargo.")
        elif total_level(p) < rank.min_total_level:
            w._private(p, f"{rank.name} exige nível total {rank.min_total_level} (o seu é {total_level(p)}).")
        else:
            seat = self.next_seat(org.id, rank)
            if seat is None:
                w._private(p, f"Não há vaga de {rank.name}. Para tomar uma, dispute com quem a ocupa.")
                return
            self._set(p.name, org.id, rank.id, seat, "claim")
            self._tell_org(org.id, f"{p.name} assumiu o cargo de {self.title(self.of(p))}.")  # type: ignore[arg-type]

    def dispute(self, p: Player, target_name: str) -> None:
        w = self.world
        m, target = self.of(p), self._online(target_name)
        tm = self.of(target) if target else None
        if m is None:
            w._private(p, "Você não faz parte de nenhuma organização.")
            return
        org, current = self.org_rank(m)
        if target is None:
            w._private(p, f"{target_name} não está online.")
        elif tm is None or tm.org != m.org:
            w._private(p, f"{target.name} não é de {org.name}.")
        else:
            rank = org.rank(tm.rank)
            until = self.dispute_cooldowns.get(p.name.lower(), 0.0)
            if rank is None or rank.promotion != "challenge":
                w._private(p, f"O cargo de {target.name} não é disputado em duelo.")
            elif org.rank_index(rank.id) >= org.rank_index(current.id):
                w._private(p, f"O cargo de {target.name} não está acima do seu.")
            elif total_level(p) < rank.min_total_level:
                w._private(p, f"{rank.name} exige nível total {rank.min_total_level} (o seu é {total_level(p)}).")
            elif w.time < until:
                w._private(p, f"Você perdeu uma disputa há pouco. Espere {until - w.time:.0f}s.")
            else:
                w.duels.challenge(p, target.name, stake=(org.id, rank.id, tm.seat))

    def resolve_dispute(self, challenger: str, defender: str, challenger_won: bool,
                        stake: tuple[str, str, int | None]) -> None:
        """Chamado pelo sistema de duelos ao fim de um duelo valendo cargo."""
        org_id, rank_id, seat = stake
        cm, dm = self.of(challenger), self.of(defender)
        still_valid = (
            cm is not None and dm is not None and cm.org == org_id and dm.org == org_id
            and dm.rank == rank_id and dm.seat == seat
        )
        if not challenger_won:
            self.dispute_cooldowns[challenger.lower()] = self.world.time + C.DISPUTE_COOLDOWN_SECONDS
            return
        if not still_valid:
            return  # alguém saiu ou mudou de cargo durante o duelo: nada muda
        assert cm is not None and dm is not None
        old_rank, old_seat = cm.rank, cm.seat
        self._set(dm.name, org_id, old_rank, old_seat, "dispute_lost")
        self._set(cm.name, org_id, rank_id, seat, "dispute_won")
        org = self.content.orgs[org_id]
        self._tell_org(org_id, f"{cm.name} venceu {dm.name} e agora é {self.title(self.of(cm.name))} de {org.name}.")  # type: ignore[arg-type]

    # ------------------------------------------------------------ comandos de líder

    def _leader_over(self, p: Player, target_name: str) -> tuple[OrgDef, Membership, Membership] | None:
        """Confere se p é líder e se o alvo é da mesma organização e está abaixo dele."""
        w = self.world
        m = self.of(p)
        if m is None:
            w._private(p, "Você não faz parte de nenhuma organização.")
            return None
        org, rank = self.org_rank(m)
        tm = self.of(target_name)
        if not rank.leader:
            w._private(p, f"Só líderes de {org.name} podem fazer isso.")
        elif tm is None or tm.org != m.org:
            w._private(p, f"{target_name} não é de {org.name}.")
        elif org.rank_index(tm.rank) <= org.rank_index(m.rank):
            w._private(p, f"{tm.name} não está abaixo de você.")
        else:
            return org, m, tm
        return None

    def promote(self, p: Player, target_name: str, rank_id: str) -> None:
        w = self.world
        checked = self._leader_over(p, target_name)
        if checked is None:
            return
        org, m, tm = checked
        rank = org.rank(rank_id)
        target = self._online(tm.name)
        if rank is None:
            w._private(p, f"{org.name} não tem o cargo {rank_id}.")
        elif rank.promotion != "appointment":
            w._private(p, f"{rank.name} não é por nomeação.")
        elif org.rank_index(rank.id) <= org.rank_index(m.rank):
            w._private(p, f"Você só nomeia para cargos abaixo do seu.")
        elif rank.id == tm.rank:
            w._private(p, f"{tm.name} já é {rank.name}.")
        elif target is None:
            w._private(p, f"{tm.name} precisa estar online para ser promovido.")
        elif total_level(target) < rank.min_total_level:
            w._private(p, f"{tm.name} ainda não tem nível para {rank.name} ({total_level(target)}/{rank.min_total_level}).")
        else:
            seat = self.next_seat(org.id, rank)
            if seat is None:
                w._private(p, f"Não há vaga de {rank.name}.")
                return
            self._set(tm.name, org.id, rank.id, seat, "promotion")
            self._tell_org(org.id, f"{p.name} nomeou {tm.name} como {self.title(self.of(tm.name))}.")  # type: ignore[arg-type]

    def demote(self, p: Player, target_name: str) -> None:
        checked = self._leader_over(p, target_name)
        if checked is None:
            return
        org, _, tm = checked
        if tm.rank == org.default_rank:
            self.world._private(p, f"{tm.name} já está no cargo inicial.")
            return
        self._set(tm.name, org.id, org.default_rank, None, "demotion")
        self._tell_org(org.id, f"{p.name} rebaixou {tm.name} a {org.rank(org.default_rank).name}.")  # type: ignore[union-attr]

    def expel(self, p: Player, target_name: str) -> None:
        checked = self._leader_over(p, target_name)
        if checked is None:
            return
        org, _, tm = checked
        self._tell_org(org.id, f"{p.name} expulsou {tm.name} de {org.name}.")
        self._remove(tm.name, "expelled")

    # ------------------------------------------------------------ ganchos

    def revalidate(self) -> list[str]:
        """Depois de recarregar o conteúdo: desfaz vínculos com organização ou cargo que sumiram."""
        notes = []
        for m in list(self.members.values()):
            org = self.content.orgs.get(m.org)
            if org is None or org.rank(m.rank) is None:
                self.members.pop(m.name.lower(), None)
                self.world.db.delete_membership(m.name)
                notes.append(f"{m.name} saiu de {m.org}/{m.rank} (não existe mais)")
        return notes

    def on_race_change(self, p: Player) -> None:
        """A raça mudou (regras): se a organização não aceita a nova, o personagem sai."""
        m = self.of(p)
        if m is None:
            return
        org = self.content.orgs[m.org]
        if org.races and p.race_id not in org.races:
            self._remove(p.name, "race")
            self.world._private(p, f"{org.name} não aceita {p.race_def.name if p.race_def else 'sua raça'}: "
                                   "você deixou a organização.")
            self._tell_org(org.id, f"{p.name} deixou {org.name}.")
