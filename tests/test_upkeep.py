"""Testes das formas de gastar moedas: desgaste e conserto, ampliação, taxas e viagem rápida."""
import math
import random
import unittest

from server import config as C
from server.content import ContentError
from server.db import Database
from server.inventory import Inventory, condition
from server.saves import migrate
from server.tilemap import check_portals, parse_map
from server.world import World
from tests.test_bank_trade import BankBase
from tests.test_npcs import MAP, SHOPS_YAML, make_content
from tests.test_world import CONTENT, MAP_A, MAP_B, make_world

DT = 1.0 / C.TICK_RATE

SERVICE_NPCS = """
ferreiro:
  name: Otto
  title: Ferreiro
  dialogue:
    inicio:
      text: "Oi."
      options:
        - text: Consertar
          repair: true
        - text: Loja
          shop: loja
guia:
  name: Tomé
  title: Guia
  dialogue:
    inicio:
      text: "Para onde?"
      options:
        - text: Viajar
          travel: true
guia2:
  name: Rui
  title: Guia
  dialogue:
    inicio:
      text: "Para onde?"
      options:
        - text: Viajar
          travel: true
"""
SERVICE_CONTENT = make_content(SERVICE_NPCS, SHOPS_YAML)


def sys_texts(world, p):
    return [m["text"] for t, m in world.drain_outbox() if t == p.id and m["t"] == "sys"]


class WearTests(unittest.TestCase):
    def setUp(self):
        self.world = make_world()
        self.p, _ = self.world.login("Ana", "1234", "humano")
        self.other, _ = self.world.login("Beto", "1234", "humano")
        self.items = self.world.content.items
        self.golpe = self.world.content.skills["golpe"]
        for who in (self.p, self.other):
            who.inventory.equip("weapon", self.items["espada_curta"], 0)
            who.inventory.equip("armor", self.items["gibao_de_couro"], 0)

    def test_default_and_custom_durability(self):
        self.assertEqual(self.items["espada_curta"].durability, C.ITEM_DURABILITY)
        self.assertEqual(self.items["presa_ancia"].durability, 400)
        self.assertEqual(self.items["pocao_vida"].durability, 0)  # só equipamento se desgasta

    def test_hit_wears_weapon_of_attacker_and_armor_of_target(self):
        self.world._damage(self.p, 3, self.other, skill=self.golpe)
        self.assertEqual(self.other.inventory.equip_wear, {"weapon": C.WEAR_PER_HIT})
        self.assertEqual(self.p.inventory.equip_wear, {"armor": C.WEAR_PER_HIT})
        # dano contínuo (efeito) não desgasta
        self.world._damage(self.p, 3, self.other, effect_id="veneno")
        self.assertEqual(self.p.inventory.equip_wear, {"armor": C.WEAR_PER_HIT})

    def test_client_sees_condition_only_when_percent_changes(self):
        inv = self.p.inventory
        inv.dirty = False
        self.world.upkeep.wear(self.p, "armor", 1)  # 250 -> 249: 99%
        self.assertTrue(inv.dirty)
        inv.dirty = False
        self.world.upkeep.wear(self.p, "armor", 1)  # 248: ainda 99%
        self.assertFalse(inv.dirty)
        self.assertEqual(inv.client_info()["cond"], {"armor": 99})

    def test_broken_item_gives_no_bonus(self):
        p = self.p
        with_armor = p.max_hp
        armor = self.items["gibao_de_couro"]
        self.world.upkeep.wear(p, "armor", armor.durability - 1)
        self.assertEqual(p.max_hp, with_armor)
        self.world.drain_outbox()
        self.world.upkeep.wear(p, "armor", 5)
        self.assertTrue(p.inventory.broken("armor"))
        self.assertEqual(p.max_hp, with_armor - armor.bonus["max_hp"])
        self.assertEqual(p.modifier("damage_taken"), 1.0)
        self.assertIn("Gibão de couro quebrou! Não dá bônus até ser consertado por um ferreiro.",
                      sys_texts(self.world, p))
        self.assertEqual(p.inventory.client_info()["cond"]["armor"], 0)

    def test_warning_before_breaking(self):
        armor = self.items["gibao_de_couro"]
        self.world.upkeep.wear(self.p, "armor", math.ceil(armor.durability * (1 - C.WEAR_WARN)))
        self.assertTrue(any("está se desgastando" in t for t in sys_texts(self.world, self.p)))

    def test_death_wears_everything_equipped(self):
        self.world._kill(self.p, None, None)
        expected = math.ceil(C.ITEM_DURABILITY * C.DEATH_WEAR)
        self.assertEqual(self.p.inventory.equip_wear, {"weapon": expected, "armor": expected})

    def item(self, op, slot):
        self.world.handle_message(self.p, {"t": "item", "op": op, "slot": slot})
        self.world.step(DT)

    def test_wear_goes_with_the_item(self):
        p, inv = self.p, self.p.inventory
        inv.equip_wear["weapon"] = 40
        self.item("unequip", "weapon")
        self.assertEqual((inv.slots[0].item.id, inv.slots[0].wear), ("espada_curta", 40))
        self.assertEqual(inv.client_info()["slots"][0], ["espada_curta", 1, condition(inv.slots[0].item, 40)])
        # largou no chão e pegou de volta
        self.item("drop", 0)
        drop = next(iter(self.world.drops.values()))
        self.assertEqual(drop.wear, 40)
        self.world.time += C.DROP_PICKUP_DELAY + 1
        p.x, p.y = drop.x, drop.y
        self.world._pickup(p)
        self.assertEqual(inv.slots[0].wear, 40)
        # equipou de novo (e a arma nova volta para a bolsa com o desgaste dela)
        inv.add(self.items["lamina_antiga"], 1, 7)
        self.item("use", 0)
        self.assertEqual(inv.equip_wear["weapon"], 40)
        self.item("use", 1)
        self.assertEqual(inv.equip_wear["weapon"], 7)
        self.assertEqual((inv.slots[0].item.id, inv.slots[0].wear), ("espada_curta", 40))

    def test_save_and_load_keep_wear_and_size(self):
        inv = self.p.inventory
        inv.equip_wear["armor"] = 12
        inv.grow(5)
        inv.add(self.items["espada_curta"], 1, 30)
        inv.add(self.items["espada_curta"], 1)  # novo: não junta (equipamento não empilha mesmo)
        loaded = Inventory.from_dict(inv.to_dict(), self.items, max_size=C.BAG_MAX_SIZE)
        self.assertEqual(loaded.size, C.INVENTORY_SIZE + 5)
        self.assertEqual(loaded.equip_wear, {"armor": 12})
        self.assertEqual([(s.item.id, s.wear) for s in loaded.slots if s], [("espada_curta", 30), ("espada_curta", 0)])
        # tamanho salvo maior que o permitido (ou estragado) volta ao padrão
        self.assertEqual(Inventory.from_dict({"size": 999}, self.items, max_size=C.BAG_MAX_SIZE).size,
                         C.INVENTORY_SIZE)
        self.assertEqual(Inventory.from_dict({"slots": [["espada_curta", 1, "x"]]}, self.items).slots[0], None)


class ServiceBase(unittest.TestCase):
    def setUp(self):
        tilemap = parse_map(MAP, "teste", SERVICE_CONTENT.enemy_by_mark,
                            zones={"npcs": [{"id": "ferreiro", "at": [4, 2]}]})
        self.world = World(tilemap, Database(":memory:"), SERVICE_CONTENT, rng=random.Random(1))
        self.p, _ = self.world.login("Teste", "1234", "humano")
        self.smith = next(iter(self.world.npcs.entities.values()))
        self.p.x, self.p.y = self.smith.x - 40, self.smith.y
        self.items = self.world.content.items
        self.world.drain_outbox()

    def npc(self, **msg):
        self.world.handle_message(self.p, {"t": "npc", **msg})
        return [m for t, m in self.world.drain_outbox() if t == self.p.id]

    def open(self, i):
        self.npc(op="talk", id=self.smith.id)
        return self.npc(op="choose", i=i)[-1]

    def recorded(self):
        return {(k, d): tuple(v) for (_, k, d), v in self.world.economy.pending.items()}


class RepairTests(ServiceBase):
    def test_price(self):
        sword = self.items["espada_curta"]
        from server.upkeep import repair_price, worn_value
        self.assertEqual(repair_price(sword, 0), 0)
        self.assertEqual(repair_price(sword, sword.durability), math.ceil(sword.value * C.REPAIR_RATE))
        self.assertEqual(repair_price(sword, 1), 1)  # no mínimo 1 moeda
        self.assertEqual(worn_value(12, sword, sword.durability // 2), 6)
        self.assertEqual(worn_value(12, sword, sword.durability), 1)

    def test_repair_window_one_and_all(self):
        inv = self.p.inventory
        sword, armor = self.items["espada_curta"], self.items["gibao_de_couro"]
        inv.equip("armor", armor, armor.durability)  # quebrada
        inv.add(sword, 1, 100)
        window = self.open(0)
        self.assertEqual(window["t"], "repair")
        self.assertEqual([(e["where"], e["slot"], e["cond"]) for e in window["items"]],
                         [("eq", "armor", 0), ("bag", 0, 60)])
        armor_price = window["items"][0]["price"]
        sword_price = window["items"][1]["price"]
        msgs = self.npc(op="repair", where="eq", slot="armor")
        self.assertIn(f"Faltam {armor_price} moedas para o conserto ({armor_price}).",
                      [m["text"] for m in msgs if m["t"] == "sys"])
        inv.add_gold(armor_price + sword_price)
        self.npc(op="repair", where="eq", slot="armor")
        self.assertFalse(inv.broken("armor"))
        self.assertEqual(inv.gold, sword_price)
        msgs = self.npc(op="repair_all")
        self.assertEqual(inv.slots[0].wear, 0)
        self.assertEqual(inv.gold, 0)
        self.assertEqual([m for m in msgs if m["t"] == "repair"][-1]["items"], [])
        self.assertEqual(self.recorded(), {("conserto", "gibao_de_couro"): (armor_price, 1),
                                           ("conserto", "espada_curta"): (sword_price, 1)})

    def test_repair_needs_the_window_open(self):
        self.p.inventory.equip("armor", self.items["gibao_de_couro"], 50)
        self.p.inventory.add_gold(100)
        self.open(1)  # abriu a loja, não o conserto
        self.npc(op="repair_all")
        self.assertEqual(self.p.inventory.equip_wear, {"armor": 50})

    def test_worn_item_sells_for_less(self):
        sword = self.items["espada_curta"]
        self.p.inventory.add(sword, 1, sword.durability // 2)
        self.open(1)
        full = self.world.content.shops["loja"].buy_price(sword)
        self.npc(op="sell", slot=0, qty=1)
        self.assertEqual(self.p.inventory.gold, full // 2)


class UpgradeAndFeeTests(BankBase):
    def test_bank_window_shows_and_buys_upgrades(self):
        bank = self.open_bank()
        self.assertEqual(bank["fee"], C.BANK_DEPOSIT_FEE)
        self.assertEqual(bank["upgrades"]["bag"], {"size": C.INVENTORY_SIZE, "price": C.BAG_UPGRADE_PRICES[0],
                                                   "add": C.BAG_UPGRADE_SLOTS})
        msgs = self.npc_op(op="expand", what="bag")
        self.assertIn(f"Faltam {C.BAG_UPGRADE_PRICES[0]} moedas para ampliar ({C.BAG_UPGRADE_PRICES[0]}).",
                      self.texts(msgs))
        self.p.inventory.add_gold(sum(C.BAG_UPGRADE_PRICES) + C.BANK_UPGRADE_PRICES[0])
        for _ in C.BAG_UPGRADE_PRICES:
            msgs = self.npc_op(op="expand", what="bag")
        self.assertEqual(self.p.inventory.size, C.BAG_MAX_SIZE)
        self.assertEqual([m for m in msgs if m["t"] == "bank"][-1]["upgrades"]["bag"]["price"], None)
        self.assertIn("Sua bolsa já tem o máximo de espaços.", self.texts(self.npc_op(op="expand", what="bag")))
        self.npc_op(op="expand", what="bank")
        self.assertEqual(self.p.bank.size, C.BANK_SIZE + C.BANK_UPGRADE_SLOTS)
        self.assertEqual(self.p.inventory.gold, 0)
        pending = {(k, d): v[0] for (_, k, d), v in self.world.economy.pending.items()}
        self.assertEqual(pending, {("espaço", "bolsa"): sum(C.BAG_UPGRADE_PRICES),
                                   ("espaço", "banco"): C.BANK_UPGRADE_PRICES[0]})
        # salvo na hora: volta com os espaços comprados
        self.world.logout(self.p)
        again = self.enter("Teste")
        self.assertEqual((again.inventory.size, again.bank.size),
                         (C.BAG_MAX_SIZE, C.BANK_SIZE + C.BANK_UPGRADE_SLOTS))
        self.assertEqual(len(again.inventory.client_info()["slots"]), C.BAG_MAX_SIZE)

    def test_expand_needs_the_bank_open(self):
        self.p.inventory.add_gold(1000)
        self.npc_op(op="expand", what="bag")
        self.assertEqual(self.p.inventory.size, C.INVENTORY_SIZE)

    def test_bank_fee_is_recorded(self):
        self.p.inventory.add_gold(200)
        self.open_bank()
        self.npc_op(op="deposit_gold", amount=200)
        pending = {(k, d): v[0] for (_, k, d), v in self.world.economy.pending.items()}
        self.assertEqual(pending, {("banco", "guardar"): 196, ("taxa", "banco"): 4})

    def test_bank_keeps_wear(self):
        sword = self.items["espada_curta"]
        self.p.inventory.add(sword, 1, 33)
        self.open_bank()
        msgs = self.npc_op(op="deposit", slot=0, qty=1)
        self.assertEqual([m for m in msgs if m["t"] == "bank"][-1]["slots"][0], ["espada_curta", 1, condition(sword, 33)])
        self.npc_op(op="withdraw", slot=0, qty=1)
        self.assertEqual(self.p.inventory.slots[0].wear, 33)


class TradeWearAndFeeTests(unittest.TestCase):
    def test_trade_fee_and_worn_items(self):
        world = make_world()
        a, _ = world.login("Ana", "1234", "humano")
        b, _ = world.login("Beto", "1234", "humano")
        b.x, b.y = a.x, a.y
        sword = world.content.items["espada_curta"]
        a.inventory.add(sword, 1, 0)
        a.inventory.add(sword, 1, 125)  # a desgastada vai primeiro
        b.inventory.add_gold(100)
        op = lambda who, **m: world.handle_message(who, {"t": "trade", **m})
        op(a, op="invite", target="Beto")
        op(b, op="accept")
        op(a, op="set", item="espada_curta", qty=1)
        op(b, op="gold", amount=100)
        info = world.trades.client_info(b)
        self.assertEqual(info["theirs"]["cond"], {"espada_curta": [50]})
        for who in (a, b):
            op(who, op="confirm", v=world.trades.client_info(who)["v"])
        self.assertEqual(a.inventory.gold, 95)  # 5% de taxa
        self.assertEqual([s.wear for s in b.inventory.slots if s], [125])
        self.assertEqual([s.wear for s in a.inventory.slots if s], [0])
        pending = {(k, d): v[0] for (_, k, d), v in world.economy.pending.items()}
        self.assertEqual(pending, {("troca", ""): 95, ("taxa", "troca"): 5})


class TravelTests(unittest.TestCase):
    def setUp(self):
        maps = {
            "a": parse_map(MAP_A, "a", (), zones={
                "name": "Mapa A", "portals": [{"area": [8, 3, 8, 3], "to": "b", "at": [2, 2]}],
                "npcs": [{"id": "guia", "at": [4, 1]}, {"id": "guia2", "at": [7, 1]}]}),
            "b": parse_map(MAP_B, "b", (), zones={
                "name": "Mapa B", "portals": [{"area": [1, 3, 1, 3], "to": "a", "at": [3, 2]}],
                "npcs": [{"id": "guia", "at": [6, 2]}]}),
        }
        check_portals(maps)
        self.world = World(maps, Database(":memory:"), SERVICE_CONTENT, rng=random.Random(1), start_map="a")
        self.p, _ = self.world.login("Teste", "1234", "humano")
        self.npcs = {(n.map_id, n.defn.id): n for n in self.world.npcs.entities.values()}
        self.here = self.npcs[("a", "guia")]
        self.p.x, self.p.y = self.here.x, self.here.y + 32
        self.world.drain_outbox()

    def npc(self, **msg):
        self.world.handle_message(self.p, {"t": "npc", **msg})
        return [m for t, m in self.world.drain_outbox() if t == self.p.id]

    def open(self):
        self.npc(op="talk", id=self.here.id)
        return self.npc(op="choose", i=0)[-1]

    def test_destinations_and_prices(self):
        window = self.open()
        self.assertEqual(window["t"], "travel")
        stops = {(s["map"], s["npc"]): (s["price"], s["known"]) for s in window["stops"]}
        self.assertEqual(stops, {
            ("Mapa A", "Rui"): (C.TRAVEL_BASE_PRICE, True),  # mesmo mapa
            ("Mapa B", "Tomé"): (C.TRAVEL_BASE_PRICE + C.TRAVEL_PRICE_PER_MAP, False),  # ainda não visitado
        })
        self.assertEqual(self.p.maps_seen, {"a"})

    def test_travel_rules(self):
        dest = self.npcs[("b", "guia")]
        price = C.TRAVEL_BASE_PRICE + C.TRAVEL_PRICE_PER_MAP
        self.open()
        msgs = self.npc(op="travel", to=dest.id)
        self.assertIn("Você ainda não conhece esse lugar: vá até lá a pé uma vez.",
                      [m["text"] for m in msgs if m["t"] == "sys"])
        self.p.maps_seen.add("b")
        msgs = self.npc(op="travel", to=dest.id)
        self.assertIn(f"Faltam {price} moedas para a viagem ({price}).", [m["text"] for m in msgs if m["t"] == "sys"])
        self.p.inventory.add_gold(price)
        self.p.combat_until = self.world.time + 3
        msgs = self.npc(op="travel", to=dest.id)
        self.assertIn("Não dá para viajar no meio de um combate.", [m["text"] for m in msgs if m["t"] == "sys"])
        self.p.combat_until = 0
        msgs = self.npc(op="travel", to=dest.id)
        self.assertEqual(self.p.map_id, "b")
        self.assertEqual(self.p.inventory.gold, 0)
        self.assertLessEqual(math.hypot(self.p.x - dest.x, self.p.y - dest.y), 1.5 * C.TILE_SIZE)
        self.assertNotIn(self.p.id, self.world.npcs.talks)
        self.assertIn("npc_close", [m["t"] for m in msgs])
        pending = {(k, d): v[0] for (_, k, d), v in self.world.economy.pending.items()}
        self.assertEqual(pending, {("viagem", "Mapa B"): price})

    def test_walking_to_a_map_marks_it_and_is_saved(self):
        self.world.teleport(self.p, "b", *self.world.maps["b"].tile_center(3, 2))
        self.assertEqual(self.p.maps_seen, {"a", "b"})
        self.assertEqual(self.p.to_dict()["travel"], {"seen": ["a", "b"]})

    def test_travel_needs_the_window(self):
        self.p.inventory.add_gold(100)
        self.npc(op="travel", to=self.npcs[("a", "guia2")].id)
        self.assertEqual(self.p.inventory.gold, 100)


class SaveAndContentTests(unittest.TestCase):
    def test_v7_save_migrates(self):
        data = migrate({"v": 7, "inventory": {"slots": [], "equipment": {}, "gold": 3}})
        self.assertEqual((data["v"], data["travel"]), (8, {"seen": []}))

    def test_only_one_option_kind(self):
        bad = SERVICE_NPCS.replace("          repair: true", "          repair: true\n          bank: true")
        with self.assertRaises(ContentError) as ctx:
            make_content(bad, SHOPS_YAML)
        self.assertIn("só um de goto, shop, trainer, craft, bank, repair ou travel", str(ctx.exception))

    def test_real_content_has_repair_and_travel(self):
        options = [o for n in CONTENT.npcs.values() for node in n.nodes.values() for o in node.options]
        self.assertTrue(any(o.repair for o in options))
        self.assertGreaterEqual(sum(o.travel for o in options), 2)


if __name__ == "__main__":
    unittest.main()
