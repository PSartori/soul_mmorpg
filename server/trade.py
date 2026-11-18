"""Troca entre jogadores.

Fluxo, tudo por botões ({"t": "trade", "op": ...}); /troca Nome é atalho:

  invite {target}   pede para trocar com alguém perto (C.TRADE_RANGE)
  accept / decline  responde ao pedido (aviso na tela com Aceitar e Recusar)
  set {item, qty}   põe (ou ajusta; 0 tira) um item da bolsa na sua oferta
  gold {amount}     moedas na sua oferta
  confirm {v}       aceita a troca como ela está (v = versão que o jogador viu)
  cancel            desiste

Qualquer mudança numa das ofertas desfaz o aceite dos dois e muda a versão: ninguém
troca algo diferente do que viu na tela. Quando os dois aceitam, o servidor confere
tudo de novo (itens, moedas e espaço na bolsa de cada um), faz a troca de uma vez e
salva os dois personagens e o registro da troca juntos, na hora.

A troca é cancelada se um dos dois sair, for derrotado, mudar de mapa, se afastar
(C.TRADE_LEAVE_RANGE) ou entrar num duelo. Itens equipados não entram (tire antes).
Quem ignora alguém não recebe pedidos de troca dele.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import config as C

if TYPE_CHECKING:
    from .entities import Player
    from .world import World

log = logging.getLogger("trade")


@dataclass
class Offer:
    items: dict[str, int] = field(default_factory=dict)  # item -> quantidade, na ordem em que entrou
    gold: int = 0

    def empty(self) -> bool:
        return not self.items and self.gold == 0

    def to_dict(self) -> dict:
        return {"items": dict(self.items), "gold": self.gold}


@dataclass
class Trade:
    a: int  # quem pediu
    b: int  # quem aceitou
    offers: dict[int, Offer]
    accepted: set[int] = field(default_factory=set)
    version: int = 1

    def other(self, player_id: int) -> int:
        return self.b if player_id == self.a else self.a


@dataclass
class TradeInvite:
    from_id: int
    expires_at: float


def _int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class TradeSystem:
    def __init__(self, world: World):
        self.world = world
        self.trades: dict[int, Trade] = {}  # id do jogador -> troca (os dois apontam para a mesma)
        self.invites: dict[int, TradeInvite] = {}  # id de quem recebeu o pedido -> pedido

    # ------------------------------------------------------------ pedidos do cliente

    def handle_op(self, p: Player, data: dict) -> None:
        op = data.get("op")
        if op == "invite":
            self.invite(p, data.get("target"))
        elif op == "accept":
            self.accept(p)
        elif op == "decline":
            self.decline(p)
        elif op == "set":
            self.set_item(p, data.get("item"), data.get("qty"))
        elif op == "gold":
            self.set_gold(p, data.get("amount"))
        elif op == "confirm":
            self.confirm(p, data.get("v"))
        elif op == "cancel":
            self.cancel(p.id, f"{p.name} cancelou a troca.")

    def _why_not(self, p: Player, other: Player) -> str | None:
        """Por que p e other não podem começar uma troca agora (None = podem)."""
        duels = self.world.duels
        if not p.alive or not other.alive:
            return "Os dois precisam estar de pé para trocar."
        if p.map_id != other.map_id or math.hypot(p.x - other.x, p.y - other.y) > C.TRADE_RANGE:
            return f"Chegue mais perto de {other.name} para trocar."
        if p.id in self.trades:
            return "Você já está numa troca."
        if other.id in self.trades:
            return f"{other.name} já está numa troca."
        if duels.opponent_id(p) is not None or duels.opponent_id(other) is not None:
            return "Não dá para trocar durante um duelo."
        return None

    def invite(self, p: Player, name: object) -> None:
        w = self.world
        name = name.strip() if isinstance(name, str) else ""
        if not name:
            return
        target = next((o for o in w.players.values() if o.name.lower() == name.lower()), None)
        if target is None:
            w._private(p, f"Ninguém online chamado {name!r}.")
            return
        if target is p:
            w._private(p, "Você não pode trocar consigo mesmo.")
            return
        if reason := self._why_not(p, target):
            w._private(p, reason)
            return
        if w.social.ignores(target, p):
            w._private(p, f"{target.name} não está aceitando trocas suas.")
            return
        pending = self.invites.get(target.id)
        if pending and pending.from_id != p.id and pending.from_id in w.players and pending.expires_at > w.time:
            w._private(p, f"{target.name} já tem um pedido de troca esperando resposta.")
            return
        self.invites[target.id] = TradeInvite(p.id, w.time + C.TRADE_INVITE_SECONDS)
        w._private(p, f"Pedido de troca enviado para {target.name}.")
        w._private(target, f"{p.name} quer trocar com você.")

    def accept(self, p: Player) -> None:
        w = self.world
        invite = self.invites.pop(p.id, None)
        inviter = w.players.get(invite.from_id) if invite else None
        if invite is None:
            w._private(p, "Você não tem nenhum pedido de troca.")
            return
        if inviter is None:
            w._private(p, "Quem pediu a troca não está mais online.")
            return
        if reason := self._why_not(p, inviter):
            w._private(p, reason)
            return
        trade = Trade(inviter.id, p.id, {inviter.id: Offer(), p.id: Offer()})
        self.trades[inviter.id] = self.trades[p.id] = trade
        w._private(inviter, f"{p.name} aceitou: a troca está aberta.")
        w._private(p, f"Troca com {inviter.name} aberta.")

    def decline(self, p: Player) -> None:
        invite = self.invites.pop(p.id, None)
        inviter = self.world.players.get(invite.from_id) if invite else None
        if inviter:
            self.world._private(inviter, f"{p.name} recusou a troca.")

    def set_item(self, p: Player, item_id: object, qty: object) -> None:
        trade = self.trades.get(p.id)
        amount = _int(qty)
        if trade is None or amount is None or not isinstance(item_id, str) or item_id not in self.world.content.items:
            return
        offer = trade.offers[p.id]
        amount = min(amount, p.inventory.count(item_id))  # só o que está na bolsa (equipado não conta)
        if amount == 0:
            if offer.items.pop(item_id, None) is None:
                return
        else:
            if item_id not in offer.items and len(offer.items) >= C.TRADE_MAX_ITEMS:
                self.world._private(p, f"Cada um pode oferecer até {C.TRADE_MAX_ITEMS} itens diferentes.")
                return
            if offer.items.get(item_id) == amount:
                return
            offer.items[item_id] = amount
        self._changed(trade)

    def set_gold(self, p: Player, amount: object) -> None:
        trade = self.trades.get(p.id)
        gold = _int(amount)
        if trade is None or gold is None:
            return
        gold = min(gold, p.inventory.gold)
        offer = trade.offers[p.id]
        if offer.gold != gold:
            offer.gold = gold
            self._changed(trade)

    def confirm(self, p: Player, version: object) -> None:
        trade = self.trades.get(p.id)
        if trade is None:
            return
        if version != trade.version:
            self.world._private(p, "A troca mudou: confira de novo antes de aceitar.")
            return
        if all(o.empty() for o in trade.offers.values()):
            self.world._private(p, "Coloquem algo na troca antes de aceitar.")
            return
        trade.accepted.add(p.id)
        if len(trade.accepted) == 2:
            self._execute(trade)

    # ------------------------------------------------------------ troca

    @staticmethod
    def _changed(trade: Trade) -> None:
        """Uma oferta mudou: os aceites valem para a versão anterior, então caem."""
        trade.version += 1
        trade.accepted.clear()

    def _execute(self, trade: Trade) -> None:
        w = self.world
        a, b = w.players[trade.a], w.players[trade.b]
        offer_a, offer_b = trade.offers[a.id], trade.offers[b.id]
        for p, offer in ((a, offer_a), (b, offer_b)):  # confere de novo: ainda tem o que ofereceu?
            inv = p.inventory
            if inv.gold < offer.gold or any(inv.count(i) < q for i, q in offer.items.items()):
                self._changed(trade)
                self._tell(trade, f"{p.name} não tem mais tudo o que ofereceu. Confiram a troca de novo.")
                return
        # faz tudo em cópias; só troca de verdade se couber tudo nas duas bolsas
        new_a, new_b = a.inventory.clone(), b.inventory.clone()
        for inv, offer in ((new_a, offer_a), (new_b, offer_b)):
            for item_id, qty in offer.items.items():
                inv.remove_item(item_id, qty)
            inv.add_gold(-offer.gold)
        items = w.content.items
        for inv, offer, owner in ((new_a, offer_b, a), (new_b, offer_a, b)):
            for item_id, qty in offer.items.items():
                if inv.add(items[item_id], qty) > 0:
                    self._changed(trade)
                    self._tell(trade, f"Não cabe tudo na bolsa de {owner.name}. Abram espaço e aceitem de novo.")
                    return
            inv.add_gold(offer.gold)
        a.inventory, b.inventory = new_a, new_b
        new_a.dirty = new_b.dirty = True
        w.economy.record("troca", "", offer_a.gold + offer_b.gold)  # só mudam de mão
        self._end(trade)
        w.save_players([a, b], trade=(a.name, b.name, offer_a.to_dict(), offer_b.to_dict()))
        log.info("troca: %s deu %s; %s deu %s", a.name, offer_a.to_dict(), b.name, offer_b.to_dict())
        for me, other, gave, got in ((a, b, offer_a, offer_b), (b, a, offer_b, offer_a)):
            w._private(me, f"Troca feita com {other.name}. Você deu: {self._summary(gave)}. "
                           f"Recebeu: {self._summary(got)}.")

    def _summary(self, offer: Offer) -> str:
        items = self.world.content.items
        parts = [f"{qty}× {items[i].name}" if qty > 1 else items[i].name for i, qty in offer.items.items()]
        if offer.gold:
            parts.append(f"{offer.gold} moedas")
        return ", ".join(parts) or "nada"

    def _tell(self, trade: Trade, text: str) -> None:
        for pid in (trade.a, trade.b):
            if (p := self.world.players.get(pid)) is not None:
                self.world._private(p, text)

    def _end(self, trade: Trade) -> None:
        for pid in (trade.a, trade.b):
            if self.trades.get(pid) is trade:
                del self.trades[pid]

    def cancel(self, player_id: int, text: str) -> None:
        trade = self.trades.get(player_id)
        if trade is None:
            return
        self._end(trade)
        self._tell(trade, text)

    # ------------------------------------------------------------ ciclo

    def update(self) -> None:
        """Pedidos vencidos somem; trocas que não fazem mais sentido são canceladas."""
        w = self.world
        for target_id, invite in list(self.invites.items()):
            if w.time >= invite.expires_at or invite.from_id not in w.players or target_id not in w.players:
                del self.invites[target_id]
        for trade in {id(t): t for t in self.trades.values()}.values():
            a, b = w.players.get(trade.a), w.players.get(trade.b)
            reason = None
            if a is None or b is None:
                reason = "A troca foi cancelada: alguém saiu do jogo."
            elif not a.alive or not b.alive:
                reason = f"A troca foi cancelada: {(a if not a.alive else b).name} foi derrotado."
            elif a.map_id != b.map_id or math.hypot(a.x - b.x, a.y - b.y) > C.TRADE_LEAVE_RANGE:
                reason = "A troca foi cancelada: vocês se afastaram."
            elif w.duels.opponent_id(a) is not None or w.duels.opponent_id(b) is not None:
                reason = "A troca foi cancelada por causa de um duelo."
            if reason:
                self._end(trade)
                self._tell(trade, reason)
                continue
            self._fit_offers(trade, a, b)  # type: ignore[arg-type]

    def _fit_offers(self, trade: Trade, a: Player, b: Player) -> None:
        """Usou, largou ou vendeu algo que estava na oferta: a oferta diminui (e os aceites caem)."""
        changed = False
        for p in (a, b):
            offer = trade.offers[p.id]
            for item_id, qty in list(offer.items.items()):
                have = p.inventory.count(item_id)
                if have < qty:
                    changed = True
                    if have:
                        offer.items[item_id] = have
                    else:
                        del offer.items[item_id]
            if offer.gold > p.inventory.gold:
                offer.gold = p.inventory.gold
                changed = True
        if changed:
            self._changed(trade)

    def on_logout(self, p: Player) -> None:
        self.invites.pop(p.id, None)
        for target_id, invite in list(self.invites.items()):
            if invite.from_id == p.id:
                del self.invites[target_id]
        self.cancel(p.id, f"{p.name} saiu do jogo. A troca foi cancelada.")

    # ------------------------------------------------------------ para o cliente

    def invite_for(self, p: Player) -> dict | None:
        """Pedido que p recebeu e ainda não respondeu (aviso com botões)."""
        invite = self.invites.get(p.id)
        inviter = self.world.players.get(invite.from_id) if invite else None
        if invite is None or inviter is None:
            return None
        return {"from": inviter.name, "left": round(max(0.0, invite.expires_at - self.world.time))}

    def client_info(self, p: Player) -> dict | None:
        trade = self.trades.get(p.id)
        if trade is None:
            return None
        other_id = trade.other(p.id)
        other = self.world.players.get(other_id)
        return {
            "with": other.name if other else "",
            "mine": trade.offers[p.id].to_dict(),
            "theirs": trade.offers[other_id].to_dict(),
            "v": trade.version,
            "ok_me": p.id in trade.accepted,
            "ok_them": other_id in trade.accepted,
        }

    def snapshot_for(self, p: Player) -> tuple[bool, dict | None]:
        """(mudou, dados). A troca só vai ao cliente quando muda (None = acabou)."""
        info = self.client_info(p)
        key = json.dumps(info)
        if key == p.trade_sent:
            return False, info
        p.trade_sent = key
        return True, info
