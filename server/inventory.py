"""Inventário de um personagem: espaços que empilham, equipamento e moedas.

Só guarda e organiza. Regras de uso (curar, equipar, restrições de raça) ficam
no World, que conhece o conteúdo e o resto do jogo.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config as C
from .content import EQUIP_SLOTS, ItemDef


@dataclass
class ItemStack:
    item: ItemDef
    qty: int


@dataclass(eq=False)
class Inventory:
    size: int = C.INVENTORY_SIZE
    slots: list[ItemStack | None] = field(default_factory=list)
    equipment: dict[str, ItemDef] = field(default_factory=dict)  # slot -> item equipado
    gold: int = 0
    dirty: bool = True  # mudou desde o último envio ao cliente

    def __post_init__(self) -> None:
        self.slots = (self.slots + [None] * self.size)[: self.size]

    def add(self, item: ItemDef, qty: int) -> int:
        """Guarda o que couber (completa pilhas, depois espaços vazios). Devolve o que sobrou."""
        if qty <= 0:
            return 0
        for stack in self.slots:
            if stack and stack.item.id == item.id and stack.qty < item.stack:
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
                self.slots[i] = ItemStack(item, moved)
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
        return ItemStack(stack.item, moved)

    def remove_item(self, item_id: str, qty: int) -> int:
        """Tira até qty unidades do item, de quaisquer espaços (os últimos primeiro). Devolve quantas tirou."""
        removed = 0
        for i in reversed(range(self.size)):
            stack = self.slots[i]
            if removed >= qty:
                break
            if stack and stack.item.id == item_id:
                taken = self.take(i, qty - removed)
                removed += taken.qty if taken else 0
        return removed

    def room_for(self, item: ItemDef) -> int:
        """Quantas unidades do item ainda cabem (completando pilhas e em espaços vazios)."""
        partial = sum(item.stack - s.qty for s in self.slots if s and s.item.id == item.id and s.qty < item.stack)
        return partial + self.free_slots() * item.stack

    def count(self, item_id: str) -> int:
        return sum(s.qty for s in self.slots if s and s.item.id == item_id)

    def free_slots(self) -> int:
        return sum(1 for s in self.slots if s is None)

    def add_gold(self, amount: int) -> None:
        self.gold = max(0, self.gold + amount)
        self.dirty = True

    # ------------------------------------------------------------ persistência e rede

    def to_dict(self) -> dict:
        return {
            "slots": [[s.item.id, s.qty] if s else None for s in self.slots],
            "equipment": {slot: item.id for slot, item in self.equipment.items()},
            "gold": self.gold,
        }

    def clone(self) -> Inventory:
        """Cópia independente (para simular uma troca antes de fazê-la de verdade)."""
        copy = Inventory(size=self.size, equipment=dict(self.equipment), gold=self.gold)
        copy.slots = [ItemStack(s.item, s.qty) if s else None for s in self.slots]
        return copy

    @classmethod
    def from_dict(cls, data: dict, items: dict[str, ItemDef], size: int = C.INVENTORY_SIZE) -> Inventory:
        """Carrega o que for válido. Itens que não existem mais no conteúdo são descartados.

        `size`: quantos espaços (o banco usa C.BANK_SIZE).
        """
        inv = cls(size=size)
        raw_slots = data.get("slots") if isinstance(data.get("slots"), list) else []
        for i, raw in enumerate(raw_slots[: inv.size]):
            if isinstance(raw, list) and len(raw) == 2 and raw[0] in items and isinstance(raw[1], int) and raw[1] > 0:
                item = items[raw[0]]
                inv.slots[i] = ItemStack(item, min(raw[1], item.stack))
        raw_equip = data.get("equipment") if isinstance(data.get("equipment"), dict) else {}
        for slot, item_id in raw_equip.items():
            item = items.get(item_id)
            if slot in EQUIP_SLOTS and item and item.slot == slot:
                inv.equipment[slot] = item
        gold = data.get("gold")
        inv.gold = gold if isinstance(gold, int) and gold > 0 else 0
        return inv

    def client_info(self) -> dict:
        return {
            "slots": [[s.item.id, s.qty] if s else None for s in self.slots],
            "equipment": {slot: item.id for slot, item in self.equipment.items()},
            "gold": self.gold,
        }
