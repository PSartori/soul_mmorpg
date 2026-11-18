"""Inventário de um personagem: espaços que empilham, equipamento e moedas.

Só guarda e organiza. Regras de uso (curar, equipar, restrições de raça) ficam
no World, que conhece o conteúdo e o resto do jogo.

Desgaste: equipamento (ItemDef.durability > 0) guarda quanto já se desgastou (`wear`),
tanto na bolsa (ItemStack.wear) quanto equipado (Inventory.equip_wear). O desgaste vai
junto com o item para onde ele for (banco, troca, chão). Desgaste igual à durabilidade =
quebrado: equipado, não dá bônus até ser consertado (server/upkeep.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import config as C
from .content import EQUIP_SLOTS, ItemDef


@dataclass
class ItemStack:
    item: ItemDef
    qty: int
    wear: int = 0  # desgaste (só equipamento, que não empilha)


def condition(item: ItemDef, wear: int) -> int:
    """Estado do item em % (100 = novo, 0 = quebrado). Desgastado mas não quebrado é no mínimo 1."""
    if item.durability <= 0 or wear <= 0:
        return 100
    if wear >= item.durability:
        return 0
    return max(1, math.floor(100 * (item.durability - wear) / item.durability))


@dataclass(eq=False)
class Inventory:
    size: int = C.INVENTORY_SIZE
    slots: list[ItemStack | None] = field(default_factory=list)
    equipment: dict[str, ItemDef] = field(default_factory=dict)  # slot -> item equipado
    equip_wear: dict[str, int] = field(default_factory=dict)  # slot -> desgaste do item equipado
    gold: int = 0
    dirty: bool = True  # mudou desde o último envio ao cliente

    def __post_init__(self) -> None:
        self.slots = (self.slots + [None] * self.size)[: self.size]

    def add(self, item: ItemDef, qty: int, wear: int = 0) -> int:
        """Guarda o que couber (completa pilhas, depois espaços vazios). Devolve o que sobrou."""
        if qty <= 0:
            return 0
        for stack in self.slots:
            if stack and stack.item.id == item.id and stack.qty < item.stack and stack.wear == wear:
                moved = min(qty, item.stack - stack.qty)
                stack.qty += moved
                qty -= moved
                if qty == 0:
                    break
        for i, stack in enumerate(self.slots):
            if qty == 0:
                break
            if stack is None:
                moved = min(qty, item.stack)
                self.slots[i] = ItemStack(item, moved, wear)
                qty -= moved
        self.dirty = True
        return qty

    def take(self, index: int, qty: int = 1) -> ItemStack | None:
        """Tira até qty do espaço index. Devolve o que saiu (ou None se vazio/inválido)."""
        if not 0 <= index < self.size or self.slots[index] is None:
            return None
        stack = self.slots[index]
        assert stack is not None
        moved = min(qty, stack.qty)
        stack.qty -= moved
        if stack.qty == 0:
            self.slots[index] = None
        self.dirty = True
        return ItemStack(stack.item, moved, stack.wear)

    def remove_item(self, item_id: str, qty: int) -> int:
        """Tira até qty unidades do item, de quaisquer espaços (os últimos primeiro). Devolve quantas tirou."""
        return sum(s.qty for s in self.take_item(item_id, qty, worn_first=False))

    def take_item(self, item_id: str, qty: int, worn_first: bool = True) -> list[ItemStack]:
        """Tira até qty unidades do item e devolve o que saiu (com o desgaste de cada um).

        worn_first: os mais desgastados primeiro (troca); senão, dos últimos espaços para os primeiros.
        """
        order = [i for i in reversed(range(self.size)) if (s := self.slots[i]) and s.item.id == item_id]
        if worn_first:
            order.sort(key=lambda i: -self.slots[i].wear)  # type: ignore[union-attr]
        taken: list[ItemStack] = []
        for i in order:
            left = qty - sum(s.qty for s in taken)
            if left <= 0:
                break
            stack = self.take(i, left)
            if stack:
                taken.append(stack)
        return taken

    def wear_of(self, item_id: str, qty: int) -> list[int]:
        """Estado (%) das unidades que take_item(item_id, qty) tiraria agora (para a tela de troca)."""
        stacks = sorted((s for s in self.slots if s and s.item.id == item_id), key=lambda s: -s.wear)
        out: list[int] = []
        for s in stacks:
            out += [condition(s.item, s.wear)] * min(s.qty, qty - len(out))
            if len(out) >= qty:
                break
        return out

    def room_for(self, item: ItemDef) -> int:
        """Quantas unidades do item ainda cabem (completando pilhas e em espaços vazios)."""
        partial = sum(item.stack - s.qty for s in self.slots
                      if s and s.item.id == item.id and s.qty < item.stack and s.wear == 0)
        return partial + self.free_slots() * item.stack

    def count(self, item_id: str) -> int:
        return sum(s.qty for s in self.slots if s and s.item.id == item_id)

    def free_slots(self) -> int:
        return sum(1 for s in self.slots if s is None)

    def add_gold(self, amount: int) -> None:
        self.gold = max(0, self.gold + amount)
        self.dirty = True

    def grow(self, extra: int) -> None:
        """Mais espaços (ampliação comprada)."""
        self.size += extra
        self.slots += [None] * extra
        self.dirty = True

    # ------------------------------------------------------------ equipamento e desgaste

    def broken(self, slot: str) -> bool:
        item = self.equipment.get(slot)
        return item is not None and 0 < item.durability <= self.equip_wear.get(slot, 0)

    def working_equipment(self) -> list[ItemDef]:
        """O equipamento que dá bônus (quebrado não dá)."""
        return [item for slot, item in self.equipment.items() if not self.broken(slot)]

    def equip(self, slot: str, item: ItemDef, wear: int) -> None:
        self.equipment[slot] = item
        if wear > 0:
            self.equip_wear[slot] = wear
        else:
            self.equip_wear.pop(slot, None)
        self.dirty = True

    def unequip(self, slot: str) -> tuple[ItemDef, int] | None:
        """Tira do corpo (não põe na bolsa): devolve (item, desgaste)."""
        item = self.equipment.pop(slot, None)
        if item is None:
            return None
        self.dirty = True
        return item, self.equip_wear.pop(slot, 0)

    # ------------------------------------------------------------ persistência e rede

    def to_dict(self) -> dict:
        data = {
            "size": self.size,
            "slots": [([s.item.id, s.qty, s.wear] if s.wear else [s.item.id, s.qty]) if s else None
                      for s in self.slots],
            "equipment": {slot: item.id for slot, item in self.equipment.items()},
            "gold": self.gold,
        }
        if self.equip_wear:
            data["wear"] = dict(self.equip_wear)
        return data

    def clone(self) -> Inventory:
        """Cópia independente (para simular uma troca antes de fazê-la de verdade)."""
        copy = Inventory(size=self.size, equipment=dict(self.equipment), equip_wear=dict(self.equip_wear),
                         gold=self.gold)
        copy.slots = [ItemStack(s.item, s.qty, s.wear) if s else None for s in self.slots]
        return copy

    @classmethod
    def from_dict(cls, data: dict, items: dict[str, ItemDef], size: int = C.INVENTORY_SIZE,
                  max_size: int | None = None) -> Inventory:
        """Carrega o que for válido. Itens que não existem mais no conteúdo são descartados.

        `size`: espaços de quem nunca ampliou (o banco usa C.BANK_SIZE); `max_size`: o máximo
        com todas as ampliações (o tamanho salvo vale se estiver entre os dois).
        """
        saved = data.get("size")
        if isinstance(saved, int) and not isinstance(saved, bool) and size < saved <= (max_size or size):
            size = saved
        inv = cls(size=size)
        raw_slots = data.get("slots") if isinstance(data.get("slots"), list) else []
        for i, raw in enumerate(raw_slots[: inv.size]):
            if not (isinstance(raw, list) and len(raw) in (2, 3) and raw[0] in items):
                continue
            qty = raw[1]
            wear = raw[2] if len(raw) == 3 else 0
            if not (_int(qty) and qty > 0 and _int(wear) and wear >= 0):
                continue
            item = items[raw[0]]
            inv.slots[i] = ItemStack(item, min(qty, item.stack), min(wear, item.durability))
        raw_equip = data.get("equipment") if isinstance(data.get("equipment"), dict) else {}
        raw_wear = data.get("wear") if isinstance(data.get("wear"), dict) else {}
        for slot, item_id in raw_equip.items():
            item = items.get(item_id)
            if slot in EQUIP_SLOTS and item and item.slot == slot:
                inv.equipment[slot] = item
                wear = raw_wear.get(slot, 0)
                if _int(wear) and wear > 0:
                    inv.equip_wear[slot] = min(wear, item.durability)
        gold = data.get("gold")
        inv.gold = gold if isinstance(gold, int) and gold > 0 else 0
        return inv

    def client_info(self) -> dict:
        """Espaços: [id, qtd] ou [id, qtd, estado %] para equipamento desgastado."""
        slots = []
        for s in self.slots:
            if s is None:
                slots.append(None)
            else:
                cond = condition(s.item, s.wear)
                slots.append([s.item.id, s.qty, cond] if cond < 100 else [s.item.id, s.qty])
        cond = {slot: c for slot, item in self.equipment.items()
                if (c := condition(item, self.equip_wear.get(slot, 0))) < 100}
        return {
            "size": self.size,
            "slots": slots,
            "equipment": {slot: item.id for slot, item in self.equipment.items()},
            "cond": cond,  # estado (%) do que está equipado e desgastado
            "gold": self.gold,
        }


def _int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
