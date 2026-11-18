"""Manutenção: desgaste e conserto do equipamento, ampliação da bolsa e do banco, taxas.

São formas de GASTAR moedas (elas só entram vendendo itens para lojas de NPC):

- Desgaste: a arma perde C.WEAR_PER_HIT de durabilidade a cada golpe que acerta;
  armadura e acessório, a cada golpe recebido. Morrer tira C.DEATH_WEAR da durabilidade
  de tudo o que está equipado. Quebrado (durabilidade 0), o item continua equipado mas
  não dá bônus. Consertar num NPC com "repair: true" custa até C.REPAIR_RATE do valor
  do item, proporcional ao desgaste. Itens desgastados valem menos na loja.
- Ampliação: na janela do banco, compra mais espaços na bolsa (do personagem) e no banco
  (da conta), cada compra mais cara que a anterior (C.BAG_UPGRADE_PRICES, C.BANK_UPGRADE_PRICES).
- Taxas: C.TRADE_GOLD_FEE sobre as moedas de uma troca (server/trade.py) e
  C.BANK_DEPOSIT_FEE ao guardar moedas no banco (server/npcs.py). Usam fee().
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from . import config as C
from .entities import Player
from .inventory import condition

if TYPE_CHECKING:
    from .content import ItemDef, SkillDef
    from .entities import Entity
    from .inventory import Inventory
    from .world import World


def fee(amount: int, rate: float) -> int:
    """Taxa sobre uma quantia de moedas (arredonda para baixo)."""
    return math.floor(amount * rate) if amount > 0 else 0


def repair_price(item: ItemDef, wear: int) -> int:
    """Quanto custa consertar o item do desgaste atual até novo (no mínimo 1 moeda se desgastado)."""
    if wear <= 0 or item.durability <= 0:
        return 0
    return max(1, math.ceil(item.value * C.REPAIR_RATE * min(wear, item.durability) / item.durability))


def worn_value(price: int, item: ItemDef, wear: int) -> int:
    """Quanto a loja paga por um item desgastado: proporcional ao estado (no mínimo 1)."""
    if price <= 0 or wear <= 0 or item.durability <= 0:
        return price
    return max(1, math.floor(price * (item.durability - min(wear, item.durability)) / item.durability))


# ampliações: (espaços de quem nunca comprou, espaços por compra, preços em ordem)
UPGRADES = {
    "bag": (C.INVENTORY_SIZE, C.BAG_UPGRADE_SLOTS, C.BAG_UPGRADE_PRICES),
    "bank": (C.BANK_SIZE, C.BANK_UPGRADE_SLOTS, C.BANK_UPGRADE_PRICES),
}
UPGRADE_NAMES = {"bag": "bolsa", "bank": "banco"}


class Upkeep:
    def __init__(self, world: World):
        self.world = world

    # ------------------------------------------------------------ desgaste

    def on_hit(self, target: Entity, source: Entity | None, skill: SkillDef | None, effect_id: str | None) -> None:
        """Um golpe acertou (dano de efeito contínuo não desgasta)."""
        if effect_id is not None or source is None or source is target:
            return
        if isinstance(source, Player) and skill is not None:
            self.wear(source, "weapon", C.WEAR_PER_HIT)
        if isinstance(target, Player):
            self.wear(target, "armor", C.WEAR_PER_HIT)
            self.wear(target, "accessory", C.WEAR_PER_HIT)

    def on_death(self, p: Player) -> None:
        for slot, item in list(p.inventory.equipment.items()):
            if item.durability > 0:
                self.wear(p, slot, math.ceil(item.durability * C.DEATH_WEAR))

    def wear(self, p: Player, slot: str, amount: int) -> None:
        inv = p.inventory
        item = inv.equipment.get(slot)
        if item is None or item.durability <= 0 or amount <= 0:
            return
        before = inv.equip_wear.get(slot, 0)
        if before >= item.durability:
            return  # já quebrado
        after = min(item.durability, before + amount)
        inv.equip_wear[slot] = after
        if condition(item, before) != condition(item, after):
            inv.dirty = True  # o cliente só vê o estado em %: não reenvia a cada golpe
        if after >= item.durability:
            self.world._private(p, f"{item.name} quebrou! Não dá bônus até ser consertado por um ferreiro.")
            self.world._clamp_resources(p)
        elif before < item.durability * (1 - C.WEAR_WARN) <= after:
            self.world._private(p, f"{item.name} está se desgastando ({condition(item, after)}%). "
                                   "Conserte com um ferreiro antes que quebre.")

    # ------------------------------------------------------------ conserto

    def repair_list(self, p: Player) -> list[dict]:
        """O que dá para consertar: equipado e na bolsa, com estado e preço."""
        inv = p.inventory
        out = []
        for slot, item in inv.equipment.items():
            wear = inv.equip_wear.get(slot, 0)
            if wear > 0:
                out.append({"where": "eq", "slot": slot, "id": item.id, "cond": condition(item, wear),
                            "price": repair_price(item, wear)})
        for index, stack in enumerate(inv.slots):
            if stack and stack.wear > 0:
                out.append({"where": "bag", "slot": index, "id": stack.item.id,
                            "cond": condition(stack.item, stack.wear), "price": repair_price(stack.item, stack.wear)})
        return out

    def repair(self, p: Player, where: object, slot: object) -> bool:
        entry = next((e for e in self.repair_list(p) if e["where"] == where and e["slot"] == slot), None)
        if entry is None:
            return False
        return self._repair(p, [entry])

    def repair_all(self, p: Player) -> bool:
        entries = self.repair_list(p)
        if not entries:
            self.world._private(p, "Nada para consertar.")
            return False
        return self._repair(p, entries)

    def _repair(self, p: Player, entries: list[dict]) -> bool:
        inv = p.inventory
        total = sum(e["price"] for e in entries)
        if inv.gold < total:
            self.world._private(p, f"Faltam {total - inv.gold} moedas para o conserto ({total}).")
            return False
        inv.add_gold(-total)
        items = self.world.content.items
        for e in entries:
            if e["where"] == "eq":
                inv.equip_wear.pop(e["slot"], None)
            else:
                stack = inv.slots[e["slot"]]
                assert stack is not None
                stack.wear = 0
            self.world.economy.record("conserto", e["id"], e["price"])  # moedas saem do jogo
        inv.dirty = True
        names = ", ".join(items[e["id"]].name for e in entries)
        self.world._private(p, f"Consertado: {names}. Custou {total} moedas.")
        return True

    # ------------------------------------------------------------ ampliação da bolsa e do banco

    def _inventory(self, p: Player, what: str) -> Inventory | None:
        return p.inventory if what == "bag" else p.bank if what == "bank" else None

    def next_upgrade(self, p: Player, what: str) -> tuple[int, int] | None:
        """(preço, espaços) da próxima ampliação, ou None se já comprou todas."""
        inv = self._inventory(p, what)
        if inv is None:
            return None
        base, step, prices = UPGRADES[what]
        bought = max(0, (inv.size - base) // step)
        return (prices[bought], step) if bought < len(prices) else None

    def upgrades_info(self, p: Player) -> dict:
        info = {}
        for what in UPGRADES:
            inv = self._inventory(p, what)
            nxt = self.next_upgrade(p, what)
            info[what] = {"size": inv.size if inv else 0, "price": nxt[0] if nxt else None,
                          "add": nxt[1] if nxt else 0}
        return info

    def expand(self, p: Player, what: object) -> bool:
        """Compra a próxima ampliação (paga com as moedas da bolsa)."""
        if not isinstance(what, str) or what not in UPGRADES:
            return False
        inv = self._inventory(p, what)
        nxt = self.next_upgrade(p, what)
        if inv is None:
            return False
        name = UPGRADE_NAMES[what]
        if nxt is None:
            self.world._private(p, f"Sua {name} já tem o máximo de espaços." if what == "bag"
                                else f"Seu {name} já tem o máximo de espaços.")
            return False
        price, step = nxt
        if p.inventory.gold < price:
            self.world._private(p, f"Faltam {price - p.inventory.gold} moedas para ampliar ({price}).")
            return False
        p.inventory.add_gold(-price)
        inv.grow(step)
        self.world.economy.record("espaço", name, price)  # moedas saem do jogo
        self.world._private(p, f"Mais {step} espaços: agora são {inv.size}. Custou {price} moedas.")
        self.world._save(p)  # personagem e banco juntos, na hora
        return True
