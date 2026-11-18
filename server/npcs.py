"""NPCs: personagens do mundo que conversam, abrem lojas, ensinam skills e criam itens.

Conteúdo em data/npcs.yaml (falas e opções), data/shops.yaml (lojas),
data/trainers.yaml (treinadores) e data/crafting.yaml (oficinas); onde cada
NPC fica vem do mapa. Fluxo, tudo por botões no cliente ({"t": "npc", "op": ...}):

  talk {id}             começa a conversa com o NPC (precisa estar perto)
  choose {i}            escolhe a opção i do nó atual: roda as ações dela e vai
                        para o próximo nó, abre a loja/treinador/oficina ou encerra. Opções de
                        missão (server/quests.py) têm i em texto: "missao:id",
                        "aceitar:id", "entregar:id" e "voltar"
  buy {item, qty}       compra da loja aberta
  sell {slot, qty}      vende para a loja aberta um item da bolsa
  learn {skill}         aprende uma skill do treinador aberto (server/training.py)
  craft {i, times}      cria `times` vezes a receita i da oficina aberta (server/crafting.py)
  deposit {slot, qty}   guarda no banco aberto um item da bolsa (o que couber)
  withdraw {slot, qty}  tira do banco aberto para a bolsa (o que couber)
  deposit_gold {amount} / withdraw_gold {amount}   guarda ou saca moedas
  close                 encerra

O servidor responde com "dialog" (fala + opções visíveis), "shop" (o que a loja
vende e quanto paga), "trainer" (o que ensina e o que falta para aprender), "craft"
(receitas da oficina), "bank" (o que está guardado) ou "npc_close".

O banco é da conta: todos os personagens dela veem as mesmas coisas guardadas. Cada
depósito ou saque salva o personagem e o banco juntos, na hora. A conversa também fecha se o jogador se
afastar, morrer, mudar de mapa ou se o conteúdo for recarregado.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import config as C
from .entities import Npc
from .rules import rule_matches

if TYPE_CHECKING:
    from .content import CraftStation, DialogNode, NpcDef, ShopDef, TrainerDef
    from .entities import Player
    from .world import World


@dataclass
class Conversation:
    npc_id: int  # id da entidade do NPC
    node: str | None  # nó atual do diálogo (None com a loja aberta ou vendo uma missão)
    shop: str | None = None  # loja aberta
    offer: str | None = None  # missão sendo oferecida (esperando Aceitar)
    trainer: str | None = None  # treinador aberto
    station: str | None = None  # oficina aberta
    bank: bool = False  # banco aberto


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
        elif op == "learn":
            self.learn(p, data.get("skill"))
        elif op == "craft":
            self.craft(p, data.get("i"), data.get("times", 1))
        elif op in ("deposit", "withdraw"):
            self.bank_move(p, op, data.get("slot"), _qty(data.get("qty", 1)))
        elif op in ("deposit_gold", "withdraw_gold"):
            self.bank_gold(p, op, data.get("amount"))

    def talk(self, p: Player, entity_id: object) -> None:
        npc = self.entities.get(entity_id) if isinstance(entity_id, int) and not isinstance(entity_id, bool) else None
        if npc is None or not p.alive:
            return
        if not self._near(p, npc, C.NPC_TALK_RANGE):
            self.world._private(p, f"Chegue mais perto de {npc.name} para conversar.")
            return
        self.world.quests.on_talk(p, npc.defn.id)  # antes de montar as opções: pode deixar uma missão pronta
        self.talks[p.id] = Conversation(npc_id=npc.id, node=npc.defn.start)
        self._send_node(p, npc, npc.defn.start)

    def choose(self, p: Player, index: object) -> None:
        talk, npc = self._current(p)
        if talk is None:
            return
        if isinstance(index, str):
            self._quest_choice(p, npc, talk, index[:80])
            return
        if talk.node is None or isinstance(index, bool) or not isinstance(index, int):
            return
        node = npc.defn.nodes[talk.node]
        if not node.options:  # nó sem opções: o único botão é encerrar
            self._close(p.id)
            return
        if not 0 <= index < len(node.options):
            return
        option = node.options[index]
        ctx = self.world._player_context(p)
        if not rule_matches(option.rule, ctx, p.flags, self.world.rng):
            return  # a opção não está disponível (o cliente mostrou algo velho)
        for name, args in option.rule.actions:
            self.world._do_action(p, name, args, ctx, 0)
        if p.id not in self.talks:  # uma ação encerrou a conversa (ex.: mudou algo que a fecha)
            return
        if option.shop:
            self._open_shop(p, npc, self.world.content.shops[option.shop])
        elif option.trainer:
            self._open_trainer(p, npc, self.world.content.trainers[option.trainer])
        elif option.craft:
            self._open_station(p, npc, self.world.content.crafting[option.craft])
        elif option.bank:
            self._open_bank(p, npc)
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
        self.world.economy.record("loja", item.id, total)  # moedas saem do jogo
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
        self.world.economy.record("venda", sold.item.id, total)  # a única forma de moedas entrarem no jogo
        self.world._private(p, f"Você vendeu {sold.qty}× {sold.item.name} por {total} moedas.")
        self._coins_fx(p, f"+{total} moedas")

    def learn(self, p: Player, skill_id: object) -> None:
        talk, npc = self._current(p)
        trainer = self.world.content.trainers.get(talk.trainer) if talk and talk.trainer else None
        if trainer is None:
            return
        entry = trainer.entry(skill_id) if isinstance(skill_id, str) else None
        if self.world.training.teach(p, trainer, skill_id) and entry and entry.price:
            self._coins_fx(p, f"-{entry.price} moedas")
        self._open_trainer(p, npc, trainer)  # reenvia: o que falta (e o que já sabe) mudou

    def craft(self, p: Player, index: object, times: object) -> None:
        talk, npc = self._current(p)
        station = self.world.content.crafting.get(talk.station) if talk and talk.station else None
        if station is None:
            return
        if self.world.crafting.craft(p, station, index, times) and p.id in self.talks:
            self._open_station(p, npc, station)  # reenvia: requisitos e receitas escondidas podem ter mudado

    # ------------------------------------------------------------ banco

    def _bank_open(self, p: Player) -> Npc | None:
        """O NPC do banco, se p está com o banco aberto e perto dele."""
        talk, npc = self._current(p)
        return npc if talk and talk.bank and p.bank is not None else None

    def bank_move(self, p: Player, op: str, slot: object, qty: int | None) -> None:
        """deposit: bolsa -> banco; withdraw: banco -> bolsa. Move o que couber."""
        npc = self._bank_open(p)
        if npc is None or qty is None or isinstance(slot, bool) or not isinstance(slot, int):
            return
        assert p.bank is not None
        source, dest = (p.inventory, p.bank) if op == "deposit" else (p.bank, p.inventory)
        stack = source.slots[slot] if 0 <= slot < source.size else None
        if stack is None:
            return
        room = dest.room_for(stack.item)
        if room == 0:
            self.world._private(p, "O banco está cheio." if op == "deposit" else "Inventário cheio.")
            return
        moved = source.take(slot, min(qty, room))
        assert moved is not None
        dest.add(moved.item, moved.qty)
        self._bank_changed(p, npc)

    def bank_gold(self, p: Player, op: str, amount: object) -> None:
        npc = self._bank_open(p)
        if npc is None or isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
            return
        assert p.bank is not None
        source, dest = (p.inventory, p.bank) if op == "deposit_gold" else (p.bank, p.inventory)
        amount = min(amount, source.gold)
        if amount == 0:
            self.world._private(p, "Você não tem moedas na bolsa." if op == "deposit_gold"
                                else "Não há moedas guardadas no banco.")
            return
        source.add_gold(-amount)
        dest.add_gold(amount)
        self.world.economy.record("banco", "guardar" if op == "deposit_gold" else "sacar", amount)
        self._bank_changed(p, npc)

    def _bank_changed(self, p: Player, npc: Npc) -> None:
        self.world._save(p)  # personagem e banco juntos, na hora (nunca duplica nem perde)
        self._open_bank(p, npc)

    def _open_bank(self, p: Player, npc: Npc) -> None:
        talk = self.talks[p.id]
        talk.node, talk.shop, talk.trainer, talk.station, talk.bank = None, None, None, None, True
        bank = p.bank
        if bank is None:  # não deveria acontecer: todo personagem no mundo tem conta
            self._close(p.id)
            return
        self.world._outbox.append((p.id, {
            "t": "bank", **self._header(npc), "size": bank.size,
            "slots": bank.client_info()["slots"], "gold": bank.gold,
        }))

    # ------------------------------------------------------------ auxiliares

    def _current(self, p: Player) -> tuple[Conversation | None, Npc | None]:
        """A conversa do jogador, se ele ainda estiver perto do NPC."""
        talk = self.talks.get(p.id)
        npc = self.entities.get(talk.npc_id) if talk else None
        if talk is None or not self._near(p, npc, C.NPC_LEAVE_RANGE):
            return None, None
        return talk, npc

    def _quest_choice(self, p: Player, npc: Npc, talk: Conversation, choice: str) -> None:
        """Opções de missão que entram sozinhas na conversa (não estão em npcs.yaml)."""
        quests = self.world.quests
        op, _, quest_id = choice.partition(":")
        quest = self.world.content.quests.get(quest_id)
        if op == "voltar":
            self._back_to_start(p, npc, talk)
        elif op == "missao" and quest and quest.giver == npc.defn.id and quests.available(p, quest):
            talk.node, talk.offer = None, quest.id
            self._send(p, npc, quests.offer_text(p, quest), [
                {"i": f"aceitar:{quest.id}", "text": "Aceitar"},
                {"i": "voltar", "text": "Agora não"},
            ], quest=quest.id)
        elif op == "aceitar" and quest and talk.offer == quest.id:
            if quests.start(p, quest.id):
                self._back_to_start(p, npc, talk)
        elif op == "entregar" and quest and quest.turn_in == npc.defn.id and quests.complete(p, quest.id):
            if p.id in self.talks:
                talk.node, talk.offer = None, None
                self._send(p, npc, self.world._format(quest.complete_text, p, {}),
                           [{"i": "voltar", "text": "Continuar"}])

    def _back_to_start(self, p: Player, npc: Npc, talk: Conversation) -> None:
        talk.node, talk.offer, talk.shop, talk.trainer, talk.station = npc.defn.start, None, None, None, None
        talk.bank = False
        self._send_node(p, npc, npc.defn.start)

    def _send_node(self, p: Player, npc: Npc, node_id: str) -> None:
        node: DialogNode = npc.defn.nodes[node_id]
        ctx = self.world._player_context(p)
        options = []
        if node_id == npc.defn.start:  # missões aparecem no começo da conversa
            quests = self.world.quests
            options += [{"i": f"entregar:{q.id}", "text": f"Entregar: {q.name}", "kind": "turn_in"}
                        for q in quests.turn_ins(p, npc.defn.id)]
            options += [{"i": f"missao:{q.id}", "text": f"Missão: {q.name}", "kind": "offer"}
                        for q in quests.offers(p, npc.defn.id)]
        options += [
            {"i": i, "text": self.world._format(opt.text, p, ctx)}
            for i, opt in enumerate(node.options)
            if rule_matches(opt.rule, ctx, p.flags, self.world.rng)
        ]
        self._send(p, npc, self.world._format(node.text, p, ctx), options)

    def _send(self, p: Player, npc: Npc, text: str, options: list[dict], quest: str | None = None) -> None:
        self.world._outbox.append((p.id, {
            "t": "dialog", **self._header(npc), "text": text,
            "options": options,  # vazio: o cliente mostra só "Encerrar"
            "quest": quest,  # missão sendo oferecida: o cliente mostra objetivos e recompensas
        }))

    def _open_shop(self, p: Player, npc: Npc, shop: ShopDef) -> None:
        talk = self.talks[p.id]
        talk.node, talk.shop, talk.trainer, talk.station, talk.bank = None, shop.id, None, None, False
        items = self.world.content.items
        self.world._outbox.append((p.id, {
            "t": "shop", **self._header(npc), "shop": shop.name,
            "sells": [{"id": e.item, "price": e.price} for e in shop.sells],
            # quanto paga por cada item que compra (o cliente cruza com a bolsa)
            "buys": {item.id: price for item in items.values() if (price := shop.buy_price(item)) > 0},
        }))

    def _open_trainer(self, p: Player, npc: Npc, trainer: TrainerDef) -> None:
        talk = self.talks[p.id]
        talk.node, talk.shop, talk.trainer, talk.station, talk.bank = None, None, trainer.id, None, False
        self.world._outbox.append((p.id, {
            "t": "trainer", **self._header(npc), "trainer": trainer.name,
            # cada skill: preço, se já sabe e o que falta (textos prontos para mostrar)
            "teaches": self.world.training.trainer_payload(p, trainer),
        }))

    def _open_station(self, p: Player, npc: Npc, station: CraftStation) -> None:
        talk = self.talks[p.id]
        talk.node, talk.shop, talk.trainer, talk.station, talk.bank = None, None, None, station.id, False
        self.world._outbox.append((p.id, {
            "t": "craft", **self._header(npc), "station": station.name,
            "recipes": self.world.crafting.payload(p, station),
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
