"""Criação de itens: oficinas (data/crafting.yaml) com receitas que transformam
materiais (e moedas) em itens.

Uma oficina abre por uma opção de diálogo de NPC (craft: id). O pedido de criar passa
pelo NpcSystem ({"t": "npc", "op": "craft", "i": receita, "times": n}), que confere se
a oficina está aberta e o jogador ainda está perto.

Criar n vezes gasta n × os materiais e n × o preço, de uma vez: ou cabe tudo e cria
tudo, ou não cria nada. Cada criação dispara o evento "craft" das regras.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import config as C
from . import requirements

if TYPE_CHECKING:
    from .content import CraftStation, Recipe
    from .entities import Player
    from .world import World


class CraftingSystem:
    def __init__(self, world: World):
        self.world = world

    def visible(self, p: Player, recipe: Recipe) -> bool:
        """Receitas hidden só aparecem quando as condições delas valem."""
        return not recipe.hidden or requirements.conditions_hold(self.world, p, recipe.rule)

    def missing(self, p: Player, recipe: Recipe) -> list[str]:
        """Requisitos que faltam (atributos, skills, condições). Materiais e moedas são conferidos à parte."""
        return requirements.missing(self.world, p, recipe.stats, recipe.skills, recipe.rule)

    def payload(self, p: Player, station: CraftStation) -> list[dict]:
        """Receitas para a janela da oficina. O cliente cruza os materiais com o inventário."""
        return [
            {"i": i, "make": r.make, "qty": r.qty, "needs": [[item_id, qty] for item_id, qty in r.needs],
             "price": r.price, "missing": self.missing(p, r)}
            for i, r in enumerate(station.recipes)
            if self.visible(p, r)
        ]

    def craft(self, p: Player, station: CraftStation, index: object, times: object) -> bool:
        """Cria `times` vezes a receita `index`. Devolve se criou."""
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(station.recipes):
            return False
        if isinstance(times, bool) or not isinstance(times, int) or times < 1:
            return False
        times = min(times, C.CRAFT_MAX_TIMES)
        recipe = station.recipes[index]
        if not self.visible(p, recipe):
            return False
        content = self.world.content
        item = content.items[recipe.make]
        inv = p.inventory
        if lines := self.missing(p, recipe):
            self.world._private(p, f"Ainda não dá para criar {item.name}: {'; '.join(lines)}.")
            return False
        short = [f"{qty * times - inv.count(item_id)}× {content.items[item_id].name}"
                 for item_id, qty in recipe.needs if inv.count(item_id) < qty * times]
        if short:
            self.world._private(p, f"Faltam materiais: {', '.join(short)}.")
            return False
        cost = recipe.price * times
        if inv.gold < cost:
            self.world._private(p, f"Faltam {cost - inv.gold} moedas para criar {item.name}.")
            return False

        # Tira os materiais e só então confere o espaço (os materiais podem liberar espaços).
        for item_id, qty in recipe.needs:
            inv.remove_item(item_id, qty * times)
        made = recipe.qty * times
        if inv.room_for(item) < made:
            for item_id, qty in recipe.needs:  # devolve: cabem, porque acabaram de sair
                inv.add(content.items[item_id], qty * times)
            self.world._private(p, "Inventário cheio: libere espaço para o que vai criar.")
            return False
        inv.add(item, made)
        inv.add_gold(-cost)
        self.world._private(p, f"Você criou {made}× {item.name}" + (f" por {cost} moedas." if cost else "."))
        self.world._fx_at(p, {"k": "pick", "id": p.id, "x": round(p.x), "y": round(p.y),
                              "t": f"+{made} {item.name}", "r": item.rarity})
        for _ in range(times):
            self.world._fire("craft", p, {"item": item.id, "station": station.id})
        return True
