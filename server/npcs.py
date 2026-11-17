"""NPCs: personagens do mundo que conversam e abrem lojas.

Conteúdo em data/npcs.yaml (falas e opções) e data/shops.yaml (lojas); onde cada
NPC fica vem do mapa. Fluxo, tudo por botões no cliente ({"t": "npc", "op": ...}):

  talk {id}             começa a conversa com o NPC (precisa estar perto)
  choose {i}            escolhe a opção i do nó atual: roda as ações dela e vai
                        para o próximo nó, abre a loja ou encerra
  buy {item, qty}       compra da loja aberta
  sell {slot, qty}      vende para a loja aberta um item da bolsa
  close                 encerra

O servidor responde com "dialog" (fala + opções visíveis), "shop" (o que a loja
vende e quanto paga) ou "npc_close". A conversa também fecha se o jogador se
afastar, morrer, mudar de mapa ou se o conteúdo for recarregado.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import config as C
from .entities import Npc
from .orgs import total_level
from .rules import rule_matches

if TYPE_CHECKING:
    from .content import DialogNode, NpcDef, ShopDef
    from .entities import Player
    from .world import World


@dataclass
class Conversation:
    npc_id: int  # id da entidade do NPC
    node: str | None  # nó atual do diálogo (None com a loja aberta)
    shop: str | None = None  # loja aberta


def _qty(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return min(value, C.SHOP_MAX_QTY)


class NpcSystem:
    def __init__(self, world: World):
        self.world = world
        self.entities: dict[int, Npc] = {}  # NPCs de todos os mapas
        self.talks: dict[int, Conversation] = {}  # id do jogador -> conversa em andamento

    # ------------------------------------------------------------ mundo

    def spawn(self) -> None:
        """(Re)cria os NPCs de todos os mapas. Conversas em andamento são encerradas."""
        for player_id in list(self.talks):
            self._close(player_id)
        self.entities.clear()
        for map_id, tilemap in self.world.maps.items():
            for npc_id, x, y in tilemap.npc_spawns:
                defn = self.world.content.npcs.get(npc_id)
                if defn is None:
                    raise ValueError(f"Mapa {tilemap.name!r}: nenhum NPC {npc_id!r} em npcs.yaml")
                npc = Npc(id=self.world._new_id(), name=defn.name, x=x, y=y, map_id=map_id, defn=defn)
                self.entities[npc.id] = npc

    def update(self) -> None:
        """Fecha conversas de quem saiu, morreu, mudou de mapa ou se afastou."""
        for player_id, talk in list(self.talks.items()):
            p = self.world.players.get(player_id)
            if p is None:
                del self.talks[player_id]
            elif not self._near(p, self.entities.get(talk.npc_id), C.NPC_LEAVE_RANGE):
                self._close(player_id)

    def on_logout(self, p: Player) -> None:
        self.talks.pop(p.id, None)

    @staticmethod
    def _near(p: Player, npc: Npc | None, distance: float) -> bool:
        return (npc is not None and p.alive and p.map_id == npc.map_id
                and math.hypot(p.x - npc.x, p.y - npc.y) <= distance)

    # ------------------------------------------------------------ pedidos do cliente

    def handle_op(self, p: Player, data: dict) -> None:
        op = data.get("op")
        if op == "talk":
            self.talk(p, data.get("id"))
        elif op == "close":
            if p.id in self.talks:
                self._close(p.id)
        elif op == "choose":
            self.choose(p, data.get("i"))
        elif op == "buy":
            self.buy(p, data.get("item"), _qty(data.get("qty", 1)))
        elif op == "sell":
            self.sell(p, data.get("slot"), _qty(data.get("qty", 1)))

    def talk(self, p: Player, entity_id: object) -> None:
        npc = self.entities.get(entity_id) if isinstance(entity_id, int) and not isinstance(entity_id, bool) else None
        if npc is None or not p.alive:
            return
        if not self._near(p, npc, C.NPC_TALK_RANGE):
            self.world._private(p, f"Chegue mais perto de {npc.name} para conversar.")
            return
        self.talks[p.id] = Conversation(npc_id=npc.id, node=npc.defn.start)
        self._send_node(p, npc, npc.defn.start)

    def choose(self, p: Player, index: object) -> None:
        talk, npc = self._current(p)
        if talk is None or talk.node is None or isinstance(index, bool) or not isinstance(index, int):
            return
        node = npc.defn.nodes[talk.node]
        if not node.options:  # nó sem opções: o único botão é encerrar
            self._close(p.id)
            return
        if not 0 <= index < len(node.options):
            return
        option = node.options[index]
        ctx = self._context(p)
        if not rule_matches(option.rule, ctx, p.flags, self.world.rng):
            return  # a opção não está disponível (o cliente mostrou algo velho)
        for name, args in option.rule.actions:
            self.world._do_action(p, name, args, ctx, 0)
        if p.id not in self.talks:  # uma ação encerrou a conversa (ex.: mudou algo que a fecha)
            return
        if option.shop:
            self._open_shop(p, npc, self.world.content.shops[option.shop])
        elif option.goto:
            talk.node = option.goto
            self._send_node(p, npc, option.goto)
        else:
            self._close(p.id)

    def buy(self, p: Player, item_id: object, qty: int | None) -> None:
        talk, npc = self._current(p)
        shop = self.world.content.shops.get(talk.shop) if talk and talk.shop else None
        price = shop.sell_price(item_id) if shop and isinstance(item_id, str) else None
        if price is None or qty is None:
            return
        item = self.world.content.items[item_id]
        total = price * qty
        inv = p.inventory
        if inv.gold < total:
            self.world._private(p, f"Faltam {total - inv.gold} moedas para comprar {qty}× {item.name}.")
            return
        room = inv.room_for(item)
        if room < qty:
            self.world._private(p, "Inventário cheio." if room == 0
                                else f"Só cabem mais {room}× {item.name} no inventário.")
            return
        inv.add_gold(-total)
        inv.add(item, qty)
        self.world._private(p, f"Você comprou {qty}× {item.name} por {total} moedas.")
        self._coins_fx(p, f"-{total} moedas")

    def sell(self, p: Player, slot: object, qty: int | None) -> None:
        talk, npc = self._current(p)
        shop = self.world.content.shops.get(talk.shop) if talk and talk.shop else None
        if shop is None or qty is None or isinstance(slot, bool) or not isinstance(slot, int):
            return
        inv = p.inventory
        stack = inv.slots[slot] if 0 <= slot < inv.size else None
        if stack is None:
            return
        price = shop.buy_price(stack.item)
        if price <= 0:
            self.world._private(p, f"{npc.name} não compra {stack.item.name}.")
            return
        sold = inv.take(slot, qty)
        assert sold is not None
        total = price * sold.qty
        inv.add_gold(total)
        self.world._private(p, f"Você vendeu {sold.qty}× {sold.item.name} por {total} moedas.")
        self._coins_fx(p, f"+{total} moedas")

    # ------------------------------------------------------------ auxiliares

    def _current(self, p: Player) -> tuple[Conversation | None, Npc | None]:
        """A conversa do jogador, se ele ainda estiver perto do NPC."""
        talk = self.talks.get(p.id)
        npc = self.entities.get(talk.npc_id) if talk else None
        if talk is None or not self._near(p, npc, C.NPC_LEAVE_RANGE):
            return None, None
        return talk, npc

    def _context(self, p: Player) -> dict:
        """O que as condições ("if") das opções de diálogo podem consultar."""
        membership = self.world.orgs.of(p)
        return {
            "race": p.race_id,
            "class": p.class_id,
            "org": membership.org if membership else None,
            "rank": membership.rank if membership else None,
            "gold": p.inventory.gold,
            "total_level": total_level(p),
        }

    def _send_node(self, p: Player, npc: Npc, node_id: str) -> None:
        node: DialogNode = npc.defn.nodes[node_id]
        ctx = self._context(p)
        options = [
            {"i": i, "text": self.world._format(opt.text, p, ctx)}
            for i, opt in enumerate(node.options)
            if rule_matches(opt.rule, ctx, p.flags, self.world.rng)
        ]
        self.world._outbox.append((p.id, {
            "t": "dialog", **self._header(npc),
            "text": self.world._format(node.text, p, ctx),
            "options": options,  # vazio: o cliente mostra só "Encerrar"
        }))

    def _open_shop(self, p: Player, npc: Npc, shop: ShopDef) -> None:
        talk = self.talks[p.id]
        talk.node, talk.shop = None, shop.id
        items = self.world.content.items
        self.world._outbox.append((p.id, {
            "t": "shop", **self._header(npc), "shop": shop.name,
            "sells": [{"id": e.item, "price": e.price} for e in shop.sells],
            # quanto paga por cada item que compra (o cliente cruza com a bolsa)
            "buys": {item.id: price for item in items.values() if (price := shop.buy_price(item)) > 0},
        }))

    @staticmethod
    def _header(npc: Npc) -> dict:
        defn: NpcDef = npc.defn
        return {"npc": npc.id, "name": defn.name, "title": defn.title, "color": defn.color}

    def _coins_fx(self, p: Player, label: str) -> None:
        self.world._fx_at(p, {"k": "pick", "id": p.id, "x": round(p.x), "y": round(p.y), "t": label, "r": "gold"})

    def _close(self, player_id: int) -> None:
        if self.talks.pop(player_id, None) is not None:
            self.world._outbox.append((player_id, {"t": "npc_close"}))
