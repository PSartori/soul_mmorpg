"""Duelos: combate combinado entre dois jogadores, em qualquer zona e sem morte.

Fluxo (comandos no chat):
  /duelo Nome   desafia alguém por perto; o desafio expira em DUEL_CHALLENGE_SECONDS
  /aceitar      aceita; o duelo começa depois de DUEL_COUNTDOWN segundos
  /recusar      recusa
  /render       desiste de um duelo em andamento

Durante o duelo, os dois só podem ferir um ao outro (em qualquer zona, inclusive
na segura) e ninguém de fora pode ferir nenhum dos dois. Quando um golpe deixaria
alguém sem vida, ele fica com 1 e perde. Também termina por desistência, saída do
jogo, distância, tempo ou morte de um deles (para um inimigo, por exemplo).

Ao terminar, os efeitos de status dos dois são limpos e o evento "duel_end" das
regras (data/rules.yaml) dispara para cada um.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import config as C

if TYPE_CHECKING:
    from .entities import Player
    from .world import World

REASON_TEXT = {
    "defeat": "",
    "forfeit": " (desistência)",
    "logout": " (saiu do jogo)",
    "distance": " (se afastaram demais)",
    "timeout": " (tempo esgotado)",
    "death": " (interrompido por uma morte)",
}


Stake = tuple[str, str, "int | None"]  # (organização, cargo, vaga) em disputa


@dataclass
class Challenge:
    challenger_id: int
    target_id: int
    expires_at: float
    stake: Stake | None = None  # duelo valendo cargo (server/orgs.py)


@dataclass(eq=False)
class Duel:
    a_id: int
    b_id: int
    starts_at: float
    ends_at: float
    announced: bool = False  # já avisou "Valendo!"
    stake: Stake | None = None  # a_id (quem desafiou) disputa o cargo de b_id

    def other(self, player_id: int) -> int:
        return self.b_id if player_id == self.a_id else self.a_id


class DuelSystem:
    def __init__(self, world: World):
        self.world = world
        self.challenges: dict[int, Challenge] = {}  # id do desafiado -> desafio (um por vez)
        self.active: dict[int, Duel] = {}  # id de cada participante -> duelo (o mesmo nas duas chaves)

    # ------------------------------------------------------------ consultas

    def duel_of(self, p: Player) -> Duel | None:
        return self.active.get(p.id)

    def opponent_id(self, p: Player) -> int | None:
        duel = self.active.get(p.id)
        return duel.other(p.id) if duel else None

    def fighting(self, a: Player, b: Player) -> bool:
        """a e b estão num duelo entre si que já começou (depois da contagem)."""
        duel = self.active.get(a.id)
        return duel is not None and duel.other(a.id) == b.id and self.world.time >= duel.starts_at

    def client_info(self, p: Player) -> dict | None:
        duel = self.active.get(p.id)
        if duel is None:
            return None
        other = self.world.players.get(duel.other(p.id))
        now = self.world.time
        return {
            "with": other.name if other else "?",
            "starts_in": round(max(0.0, duel.starts_at - now), 1),
            "left": round(max(0.0, duel.ends_at - now)),
        }

    def invite_for(self, p: Player) -> dict | None:
        """Desafio que p recebeu e ainda não respondeu (para o aviso com botões no cliente)."""
        challenge = self.challenges.get(p.id)
        challenger = self.world.players.get(challenge.challenger_id) if challenge else None
        if challenge is None or challenger is None:
            return None
        return {
            "from": challenger.name,
            "prize": self._stake_name(challenge.stake) or None,
            "left": round(max(0.0, challenge.expires_at - self.world.time)),
        }

    def handle_op(self, p: Player, data: dict) -> None:
        """Pedidos da interface: {"t": "duel", "op": "challenge" | "accept" | "decline" | "forfeit"}."""
        op = data.get("op")
        if op == "challenge" and isinstance(data.get("target"), str):
            self.challenge(p, data["target"])
        elif op == "accept":
            self.accept(p)
        elif op == "decline":
            self.decline(p)
        elif op == "forfeit":
            self.forfeit(p)

    # ------------------------------------------------------------ comandos

    def challenge(self, p: Player, target_name: str, stake: Stake | None = None) -> None:
        w = self.world
        target = next((o for o in w.players.values() if o.name.lower() == target_name.lower()), None)
        if target is None:
            w._private(p, f"Ninguém online chamado {target_name!r}.")
        elif target is p:
            w._private(p, "Você não pode duelar consigo mesmo.")
        elif p.id in self.active:
            w._private(p, "Você já está num duelo.")
        elif target.id in self.active:
            w._private(p, f"{target.name} já está num duelo.")
        elif not p.alive or not target.alive:
            w._private(p, "Os dois precisam estar de pé para duelar.")
        elif w.social.ignores(target, p):
            w._private(p, f"{target.name} não está aceitando desafios seus.")
        elif self._distance(p, target) > C.DUEL_CHALLENGE_RANGE:
            w._private(p, f"Chegue mais perto de {target.name} para desafiar.")
        else:
            self.challenges[target.id] = Challenge(p.id, target.id, w.time + C.DUEL_CHALLENGE_SECONDS, stake)
            what = f"pelo seu cargo de {self._stake_name(stake)}" if stake else "para um duelo"
            w._private(p, f"Você desafiou {target.name} {'pelo cargo' if stake else 'para um duelo'}. Aguardando resposta…")
            w._private(target, f"{p.name} te desafiou {what}. Responda no aviso na tela.")

    def accept(self, p: Player) -> None:
        w = self.world
        challenge = self.challenges.pop(p.id, None)
        challenger = w.players.get(challenge.challenger_id) if challenge else None
        if challenge is None:
            w._private(p, "Você não tem nenhum desafio pendente.")
        elif challenger is None:
            w._private(p, "Quem te desafiou não está mais online.")
        elif p.id in self.active or challenger.id in self.active:
            w._private(p, "Um de vocês já está em outro duelo.")
        elif not p.alive or not challenger.alive:
            w._private(p, "Os dois precisam estar de pé para duelar.")
        elif self._distance(p, challenger) > C.DUEL_CHALLENGE_RANGE:
            w._private(p, f"Vocês estão longe demais. Chegue perto de {challenger.name} e peça outro desafio.")
        else:
            duel = Duel(challenger.id, p.id, w.time + C.DUEL_COUNTDOWN,
                        w.time + C.DUEL_COUNTDOWN + C.DUEL_MAX_SECONDS, stake=challenge.stake)
            self.active[challenger.id] = self.active[p.id] = duel
            self.challenges.pop(challenger.id, None)  # desafio que o outro tinha recebido perde o sentido
            prize = f" pelo cargo de {self._stake_name(duel.stake)}" if duel.stake else ""
            w._system(f"{challenger.name} e {p.name} vão duelar{prize}!")
            for who in (challenger, p):
                w._private(who, f"O duelo começa em {C.DUEL_COUNTDOWN:g} segundos. Para desistir, use o botão Desistir.")

    def decline(self, p: Player) -> None:
        w = self.world
        challenge = self.challenges.pop(p.id, None)
        if challenge is None:
            w._private(p, "Você não tem nenhum desafio pendente.")
            return
        w._private(p, "Desafio recusado.")
        challenger = w.players.get(challenge.challenger_id)
        if challenger:
            w._private(challenger, f"{p.name} recusou o duelo.")

    def forfeit(self, p: Player) -> None:
        duel = self.active.get(p.id)
        if duel is None:
            self.world._private(p, "Você não está num duelo.")
        else:
            self.finish(duel, loser=p, reason="forfeit")

    # ------------------------------------------------------------ ciclo

    def update(self) -> None:
        w = self.world
        now = w.time
        for target_id, challenge in list(self.challenges.items()):
            if now >= challenge.expires_at or target_id not in w.players:
                del self.challenges[target_id]
                challenger = w.players.get(challenge.challenger_id)
                if challenger:
                    w._private(challenger, "O desafio de duelo expirou.")
        for duel in {id(d): d for d in self.active.values()}.values():
            a, b = w.players.get(duel.a_id), w.players.get(duel.b_id)
            if a is None or b is None:
                continue  # on_logout já trata
            if not duel.announced and now >= duel.starts_at:
                duel.announced = True
                for who in (a, b):
                    w._private(who, "Valendo!")
            if now >= duel.ends_at:
                self.finish(duel, loser=None, reason="timeout")
            elif self._distance(a, b) > C.DUEL_LEASH:
                self.finish(duel, loser=None, reason="distance")

    def on_logout(self, p: Player) -> None:
        self.challenges.pop(p.id, None)
        duel = self.active.get(p.id)
        if duel:
            self.finish(duel, loser=p, reason="logout")

    def on_death(self, p: Player) -> bool:
        """Um duelista morreu (para um inimigo, veneno...): termina empatado. Devolve se estava em duelo."""
        duel = self.active.get(p.id)
        if duel:
            self.finish(duel, loser=None, reason="death")
        return duel is not None

    def finish(self, duel: Duel, loser: Player | None, reason: str) -> None:
        w = self.world
        if self.active.get(duel.a_id) is not duel:
            return  # já terminou
        self.active.pop(duel.a_id, None)
        self.active.pop(duel.b_id, None)
        a, b = w.players.get(duel.a_id), w.players.get(duel.b_id)
        # quem saiu do jogo já não está em players: busca pelo perdedor
        people = {pid: pl for pid, pl in ((duel.a_id, a), (duel.b_id, b)) if pl is not None}
        if loser is not None:
            people.setdefault(loser.id, loser)
        for who in people.values():
            if who.alive:
                who.effects.clear()  # nada de veneno do duelo matando depois que acabou

        names = {pid: pl.name for pid, pl in people.items()}
        detail = REASON_TEXT[reason]
        if loser is None:
            w._system(f"O duelo entre {names.get(duel.a_id, '?')} e {names.get(duel.b_id, '?')} terminou empatado{detail}.")
        else:
            winner_id = duel.other(loser.id)
            w._system(f"{names.get(winner_id, '?')} venceu o duelo contra {loser.name}{detail}.")

        for pid, who in people.items():
            if pid not in w.players:
                continue  # quem saiu não recebe evento
            other = people.get(duel.other(pid))
            result = "draw" if loser is None else ("lose" if pid == loser.id else "win")
            w._fire("duel_end", who, {
                "result": result,
                "reason": reason,
                "opponent_race": other.race_id if other else None,
                "opponent_class": other.class_id if other else None,
            })

        if duel.stake and duel.a_id in names and duel.b_id in names:
            challenger_won = loser is not None and loser.id == duel.b_id
            w.orgs.resolve_dispute(names[duel.a_id], names[duel.b_id], challenger_won, duel.stake)

    def _stake_name(self, stake: Stake | None) -> str:
        if not stake:
            return ""
        org_id, rank_id, seat = stake
        org = self.world.content.orgs[org_id]
        rank = org.rank(rank_id)
        title = f"{rank.name} {seat}" if rank and rank.numbered and seat else (rank.name if rank else rank_id)
        return f"{title} de {org.name}"

    @staticmethod
    def _distance(a: Player, b: Player) -> float:
        if a.map_id != b.map_id:
            return math.inf  # mapas diferentes: longe demais
        return math.hypot(a.x - b.x, a.y - b.y)
