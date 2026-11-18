"""Grupos: jogadores que caçam juntos.

Um grupo tem até C.PARTY_MAX membros e um líder. Só existe enquanto os membros estão
online (não vai para o save): quem sai do jogo sai do grupo; se o líder sair, o
próximo da lista assume; com um membro só, o grupo acaba.

O que o grupo muda no jogo (as regras ficam no World, que pergunta aqui quem é de quem):
  - abates: quem deu o golpe final ganha o bônus de abate inteiro, como sempre; os
    outros membros perto (C.PARTY_SHARE_RANGE) que lutaram nos últimos segundos ganham
    C.PARTY_XP_SHARE dele, no atributo da última skill que usaram;
  - missões: o abate conta para todos os membros perto;
  - saque: a exclusividade de quem matou vale para o grupo todo, e moedas pegas do
    saque do grupo são divididas entre os membros perto;
  - PvP: membros do mesmo grupo não se ferem (a não ser em duelo), se
    C.PARTY_FRIENDLY_FIRE for False.

Pedidos do cliente ({"t": "party", "op": ...}):
  invite {target}       convida (nome); quem não tem grupo cria um ao convidar
  accept / decline      responde ao convite recebido
  leave                 sai do grupo
  kick {target}         (líder) tira alguém do grupo
  lead {target}         (líder) passa a liderança
Chat do grupo: {"t": "chat", "ch": "party", "text"}. Atalhos: /grupo Nome e /g texto.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import config as C

if TYPE_CHECKING:
    from .entities import Player
    from .world import World

PARTY_COLOR = "#7ee0c0"  # cor do canal "Grupo" no chat


@dataclass
class Party:
    id: int
    leader_id: int
    members: list[int] = field(default_factory=list)  # ids dos jogadores, na ordem em que entraram


@dataclass
class PartyInvite:
    party_id: int | None  # None: quem convidou ainda não tinha grupo (cria ao aceitar)
    inviter_id: int
    expires_at: float


class PartySystem:
    def __init__(self, world: World):
        self.world = world
        self.parties: dict[int, Party] = {}
        self.by_player: dict[int, int] = {}  # id do jogador -> id do grupo
        self.invites: dict[int, PartyInvite] = {}  # id do convidado -> convite (um por vez)
        self._next_id = 1

    # ------------------------------------------------------------ consultas

    def of(self, p: Player) -> Party | None:
        party_id = self.by_player.get(p.id)
        return self.parties.get(party_id) if party_id is not None else None

    def same_party(self, a: Player, b: Player) -> bool:
        party_id = self.by_player.get(a.id)
        return party_id is not None and party_id == self.by_player.get(b.id)

    def members(self, party: Party) -> list[Player]:
        return [p for pid in party.members if (p := self.world.players.get(pid))]

    def near(self, p: Player, x: float, y: float) -> list[Player]:
        """Membros do grupo de p (sem ele) vivos, no mesmo mapa e perto de (x, y)."""
        party = self.of(p)
        if party is None:
            return []
        return [
            m for m in self.members(party)
            if m is not p and m.alive and m.map_id == p.map_id
            and math.hypot(m.x - x, m.y - y) <= C.PARTY_SHARE_RANGE
        ]

    def _find(self, p: Player, name: object) -> Player | None:
        if not isinstance(name, str) or not name.strip():
            return None
        target = next((o for o in self.world.players.values() if o.name.lower() == name.strip().lower()), None)
        if target is None:
            self.world._private(p, f"Ninguém online chamado {name.strip()!r}.")
        return target

    # ------------------------------------------------------------ pedidos

    def handle_op(self, p: Player, data: dict) -> None:
        op = data.get("op")
        if op == "invite":
            self.invite(p, data.get("target"))
        elif op == "accept":
            self.accept(p)
        elif op == "decline":
            self.decline(p)
        elif op == "leave":
            self.leave(p)
        elif op == "kick":
            self.kick(p, data.get("target"))
        elif op == "lead":
            self.lead(p, data.get("target"))

    def invite(self, p: Player, name: object) -> None:
        w = self.world
        target = self._find(p, name)
        if target is None:
            return
        party = self.of(p)
        if target is p:
            w._private(p, "Você não pode se convidar.")
        elif party and party.leader_id != p.id:
            w._private(p, "Só o líder do grupo pode convidar.")
        elif party and len(party.members) >= C.PARTY_MAX:
            w._private(p, f"O grupo já está cheio ({C.PARTY_MAX}).")
        elif self.of(target):
            w._private(p, f"{target.name} já está num grupo.")
        else:
            self.invites[target.id] = PartyInvite(party.id if party else None, p.id,
                                                  w.time + C.PARTY_INVITE_SECONDS)
            w._private(p, f"Convite enviado para {target.name}.")
            w._private(target, f"{p.name} convidou você para o grupo.")

    def accept(self, p: Player) -> None:
        w = self.world
        invite = self.invites.pop(p.id, None)
        inviter = w.players.get(invite.inviter_id) if invite else None
        if invite is None:
            w._private(p, "Você não tem nenhum convite de grupo.")
            return
        if inviter is None:
            w._private(p, "Quem convidou não está mais online.")
            return
        if self.of(p):
            w._private(p, "Você já está num grupo.")
            return
        party = self.of(inviter)
        if (party and party.leader_id != inviter.id) or (invite.party_id is not None and (
                party is None or party.id != invite.party_id)):
            w._private(p, "Esse grupo não existe mais.")
            return
        if party is None:  # quem convidou não tinha grupo: cria agora, com ele de líder
            party = Party(id=self._next_id, leader_id=inviter.id, members=[inviter.id])
            self._next_id += 1
            self.parties[party.id] = party
            self.by_player[inviter.id] = party.id
        if len(party.members) >= C.PARTY_MAX:
            w._private(p, "O grupo já está cheio.")
            return
        party.members.append(p.id)
        self.by_player[p.id] = party.id
        self._tell(party, f"{p.name} entrou no grupo.")

    def decline(self, p: Player) -> None:
        w = self.world
        invite = self.invites.pop(p.id, None)
        if invite is None:
            w._private(p, "Você não tem nenhum convite de grupo.")
            return
        w._private(p, "Convite recusado.")
        if inviter := w.players.get(invite.inviter_id):
            w._private(inviter, f"{p.name} recusou o convite para o grupo.")

    def leave(self, p: Player, reason: str = "saiu do grupo") -> None:
        party = self.of(p)
        if party is None:
            self.world._private(p, "Você não está em nenhum grupo.")
            return
        self._remove(party, p.id, f"{p.name} {reason}.")
        self.world._private(p, "Você saiu do grupo.")

    def kick(self, p: Player, name: object) -> None:
        w = self.world
        party = self.of(p)
        target = self._find(p, name)
        if party is None or target is None:
            return
        if party.leader_id != p.id:
            w._private(p, "Só o líder pode tirar alguém do grupo.")
        elif target is p or target.id not in party.members:
            w._private(p, f"{target.name} não é do seu grupo.")
        else:
            self._remove(party, target.id, f"{target.name} foi tirado do grupo.")
            w._private(target, "Você foi tirado do grupo.")

    def lead(self, p: Player, name: object) -> None:
        w = self.world
        party = self.of(p)
        target = self._find(p, name)
        if party is None or target is None:
            return
        if party.leader_id != p.id:
            w._private(p, "Só o líder pode passar a liderança.")
        elif target is p or target.id not in party.members:
            w._private(p, f"{target.name} não é do seu grupo.")
        else:
            party.leader_id = target.id
            self._tell(party, f"{target.name} agora lidera o grupo.")

    def chat(self, p: Player, text: str) -> None:
        w = self.world
        party = self.of(p)
        text = text.strip()[: C.CHAT_MAX_LEN]
        if party is None:
            w._private(p, "Você não está em nenhum grupo.")
            return
        if muted := w.admin.mute_message(p):
            w._private(p, muted)
            return
        if text:
            for m in self.members(party):
                w._outbox.append((m.id, {"t": "chat", "from": p.name, "text": text, "ch": "Grupo",
                                         "color": PARTY_COLOR}))

    # ------------------------------------------------------------ ciclo

    def update(self) -> None:
        """Convites vencidos somem."""
        now = self.world.time
        for target_id, invite in list(self.invites.items()):
            if now >= invite.expires_at or invite.inviter_id not in self.world.players:
                del self.invites[target_id]

    def on_logout(self, p: Player) -> None:
        self.invites.pop(p.id, None)
        if party := self.of(p):
            self._remove(party, p.id, f"{p.name} saiu do jogo e deixou o grupo.")

    def _remove(self, party: Party, player_id: int, message: str) -> None:
        party.members.remove(player_id)
        self.by_player.pop(player_id, None)
        if len(party.members) < 2:  # sozinho não é grupo
            for pid in party.members:
                self.by_player.pop(pid, None)
                if p := self.world.players.get(pid):
                    self.world._private(p, f"{message} O grupo acabou.")
            del self.parties[party.id]
            return
        if party.leader_id == player_id:
            party.leader_id = party.members[0]
            leader = self.world.players.get(party.leader_id)
            message += f" {leader.name if leader else '?'} agora lidera o grupo."
        self._tell(party, message)

    def _tell(self, party: Party, text: str) -> None:
        for m in self.members(party):
            self.world._private(m, text)

    # ------------------------------------------------------------ para o cliente

    def invite_for(self, p: Player) -> dict | None:
        """Convite que p recebeu e ainda não respondeu (aviso com botões)."""
        invite = self.invites.get(p.id)
        inviter = self.world.players.get(invite.inviter_id) if invite else None
        if invite is None or inviter is None:
            return None
        return {"from": inviter.name, "left": round(max(0.0, invite.expires_at - self.world.time))}

    def client_info(self, p: Player) -> dict | None:
        party = self.of(p)
        if party is None:
            return None
        return {
            "leader": party.leader_id,
            "members": [
                {"id": m.id, "n": m.name, "hp": round(m.hp), "mhp": round(m.max_hp), "lv": _level(m),
                 "map": m.map_id, "dead": not m.alive}
                for m in self.members(party)
            ],
        }

    def snapshot_for(self, p: Player) -> tuple[bool, dict | None]:
        """(mudou, dados). O grupo só vai ao cliente quando muda (inclusive quando acaba: None)."""
        info = self.client_info(p)
        key = json.dumps(info)
        if key == p.party_sent:
            return False, info
        p.party_sent = key
        return True, info


def _level(p: Player) -> int:
    levels = [s.level for s in p.stats.values()]
    return sum(levels) // len(levels) if levels else 1
