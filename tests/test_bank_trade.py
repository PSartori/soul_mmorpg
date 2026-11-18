"""Testes do banco (por conta, aberto por NPC) e da troca entre jogadores."""
import random
import unittest
from unittest import mock

from server import config as C
from server.db import Database
from server.tilemap import parse_map
from server.world import World
from tests.test_npcs import MAP, SHOPS_YAML, make_content
from tests.test_world import make_world, run, tile_center

DT = 1.0 / C.TICK_RATE

BANK_NPCS = """
banqueiro:
  name: Matias
  title: Banqueiro
  dialogue:
    inicio:
      text: "Seu baú."
      options:
        - text: Abrir o baú
          bank: true
        - text: Tchau
"""
CONTENT = make_content(BANK_NPCS, SHOPS_YAML)


def bank_world(db: Database) -> World:
    tilemap = parse_map(MAP, "teste", CONTENT.enemy_by_mark, zones={"npcs": [{"id": "banqueiro", "at": [4, 2]}]})
    return World(tilemap, db, CONTENT, rng=random.Random(1))


class BankBase(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.world = bank_world(self.db)
        self.p = self.enter("Teste")
        self.items = self.world.content.items

    def enter(self, name, world=None):
        world = world or self.world
        p, err = world.login(name, "1234", "humano")
        self.assertIsNone(err, err)
        npc = next(iter(world.npcs.entities.values()))
        p.x, p.y = npc.x - 40, npc.y
        world.drain_outbox()
        return p

    def npc_op(self, p=None, world=None, **msg):
        world = world or self.world
        world.handle_message(p or self.p, {"t": "npc", **msg})
        return [m for target, m in world.drain_outbox() if target == (p or self.p).id]

    def open_bank(self, p=None, world=None):
        world = world or self.world
        npc = next(iter(world.npcs.entities.values()))
        self.npc_op(p, world, op="talk", id=npc.id)
        msgs = self.npc_op(p, world, op="choose", i=0)
        return [m for m in msgs if m["t"] == "bank"][-1]

    def texts(self, msgs):
        return [m["text"] for m in msgs if m["t"] == "sys"]


class BankTests(BankBase):
    def test_open_bank_shows_empty_chest(self):
        bank = self.open_bank()
        self.assertEqual((bank["size"], bank["gold"], bank["name"]), (C.BANK_SIZE, 0, "Matias"))
        self.assertEqual(len(bank["slots"]), C.BANK_SIZE)
        self.assertTrue(all(s is None for s in bank["slots"]))

    def test_deposit_and_withdraw_items(self):
        self.p.inventory.add(self.items["pocao_vida"], 5)
        self.open_bank()
        msgs = self.npc_op(op="deposit", slot=0, qty=5)
        bank = [m for m in msgs if m["t"] == "bank"][-1]
        self.assertEqual(bank["slots"][0], ["pocao_vida", 5])
        self.assertEqual(self.p.inventory.count("pocao_vida"), 0)
        self.npc_op(op="withdraw", slot=0, qty=1)  # Shift + clique: só 1
        self.assertEqual(self.p.inventory.count("pocao_vida"), 1)
        self.assertEqual(self.p.bank.count("pocao_vida"), 4)

    def test_saved_immediately_together(self):
        self.p.inventory.add(self.items["espada_curta"], 1)
        self.open_bank()
        self.npc_op(op="deposit", slot=0, qty=1)
        # sem esperar o salvamento automático: banco e personagem já estão no banco de dados
        self.assertEqual(self.db.load_bank(self.p.account_id)["slots"][0], ["espada_curta", 1])
        self.assertIsNone(self.db.load_character("Teste").data["inventory"]["slots"][0])

    def test_gold(self):
        self.p.inventory.add_gold(100)
        self.open_bank()
        self.npc_op(op="deposit_gold", amount=500)  # mais do que tem: guarda tudo
        self.assertEqual((self.p.inventory.gold, self.p.bank.gold), (0, 100))
        self.npc_op(op="withdraw_gold", amount=30)
        self.assertEqual((self.p.inventory.gold, self.p.bank.gold), (30, 70))
        self.p.inventory.add_gold(-30)
        self.assertIn("Você não tem moedas na bolsa.", self.texts(self.npc_op(op="deposit_gold", amount=1)))
        for bad in (0, -5, True, "10"):
            self.npc_op(op="withdraw_gold", amount=bad)
        self.assertEqual(self.p.bank.gold, 70)

    def test_moves_only_what_fits(self):
        bank = self.p.bank
        for _ in range(C.BANK_SIZE - 1):
            bank.add(self.items["espada_curta"], 1)
        bank.add(self.items["pocao_vida"], 8)  # último espaço: cabem mais 2
        self.p.inventory.add(self.items["pocao_vida"], 5)
        self.open_bank()
        self.npc_op(op="deposit", slot=0, qty=5)
        self.assertEqual((self.p.inventory.count("pocao_vida"), bank.count("pocao_vida")), (3, 10))
        msgs = self.npc_op(op="deposit", slot=0, qty=3)
        self.assertIn("O banco está cheio.", self.texts(msgs))

    def test_withdraw_with_full_bag(self):
        self.p.bank.add(self.items["couro_grosso"], 3)
        for _ in range(C.INVENTORY_SIZE):
            self.p.inventory.add(self.items["espada_curta"], 1)
        self.open_bank()
        self.assertIn("Inventário cheio.", self.texts(self.npc_op(op="withdraw", slot=0, qty=3)))
        self.assertEqual(self.p.bank.count("couro_grosso"), 3)

    def test_needs_bank_open_and_near(self):
        self.p.inventory.add(self.items["pocao_vida"], 2)
        self.npc_op(op="deposit", slot=0, qty=2)  # banco fechado: nada
        self.assertEqual(self.p.bank.count("pocao_vida"), 0)
        self.open_bank()
        self.p.x += C.NPC_LEAVE_RANGE + 50  # afastou
        self.npc_op(op="deposit", slot=0, qty=2)
        self.assertEqual(self.p.bank.count("pocao_vida"), 0)
        for bad in (None, -1, 99, True, "0"):
            self.npc_op(op="deposit", slot=bad, qty=1)

    def test_bank_is_shared_by_the_account(self):
        self.p.inventory.add(self.items["presa_de_devorador"], 4)
        self.p.inventory.add_gold(50)
        self.open_bank()
        self.npc_op(op="deposit", slot=0, qty=4)
        self.npc_op(op="deposit_gold", amount=50)
        account_id = self.p.account_id
        self.world.logout(self.p)
        self.assertIsNone(self.world.create_character(account_id, "Segundo", "humano"))
        other, err = self.world.enter(account_id, "Segundo")
        self.assertIsNone(err, err)
        self.assertEqual((other.bank.count("presa_de_devorador"), other.bank.gold), (4, 50))

    def test_bank_survives_restart(self):
        self.p.inventory.add(self.items["pocao_energia"], 3)
        self.open_bank()
        self.npc_op(op="deposit", slot=0, qty=3)
        self.world.logout(self.p)
        world2 = bank_world(self.db)
        p = self.enter("Teste", world2)
        self.assertEqual(p.bank.count("pocao_energia"), 3)


class TradeBase(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.world = make_world(self.db)
        self.world.enemies.clear()
        self.a = self.login("Ana")
        self.b = self.login("Beto")
        self.c = self.login("Caio")
        self.items = self.world.content.items
        self.world.drain_outbox()

    def login(self, name):
        p, err = self.world.login(name, "1234", "humano")
        self.assertIsNone(err, err)
        p.x, p.y = tile_center(2, 2)
        return p

    def op(self, p, **msg):
        self.world.handle_message(p, {"t": "trade", **msg})

    def texts(self, p):
        return [m["text"] for t, m in self.world.drain_outbox() if t == p.id and m["t"] == "sys"]

    def open_trade(self, a=None, b=None):
        a, b = a or self.a, b or self.b
        self.op(a, op="invite", target=b.name)
        self.op(b, op="accept")
        return self.world.trades.trades[a.id]

    def info(self, p):
        return self.world.trades.client_info(p)

    def confirm_both(self):
        self.op(self.a, op="confirm", v=self.info(self.a)["v"])
        self.op(self.b, op="confirm", v=self.info(self.b)["v"])


class TradeFlowTests(TradeBase):
    def test_invite_accept_opens_trade(self):
        self.op(self.a, op="invite", target="beto")
        self.assertEqual(self.world.trades.invite_for(self.b)["from"], "Ana")
        self.op(self.b, op="accept")
        self.assertIsNone(self.world.trades.invite_for(self.b))
        info = self.info(self.b)
        self.assertEqual(info["with"], "Ana")
        self.assertEqual(info["mine"], {"items": {}, "gold": 0})
        self.assertFalse(info["ok_me"] or info["ok_them"])

    def test_full_trade_swaps_and_saves(self):
        self.a.inventory.add(self.items["pocao_vida"], 6)
        self.a.inventory.add_gold(40)
        self.b.inventory.add(self.items["espada_curta"], 1)
        self.open_trade()
        self.op(self.a, op="set", item="pocao_vida", qty=4)
        self.op(self.a, op="gold", amount=25)
        self.op(self.b, op="set", item="espada_curta", qty=1)
        self.assertEqual(self.info(self.b)["theirs"], {"items": {"pocao_vida": 4}, "gold": 25})
        self.world.drain_outbox()
        self.confirm_both()
        self.assertEqual((self.a.inventory.count("pocao_vida"), self.a.inventory.gold), (2, 15))
        self.assertEqual(self.a.inventory.count("espada_curta"), 1)
        self.assertEqual((self.b.inventory.count("pocao_vida"), self.b.inventory.gold), (4, 25))
        self.assertEqual(self.b.inventory.count("espada_curta"), 0)
        self.assertNotIn(self.a.id, self.world.trades.trades)
        self.assertTrue(any(t.startswith("Troca feita com Beto. Você deu: 4× Poção") for t in self.texts(self.a)))
        # salvo na hora, os dois juntos, e registrado
        saved_b = self.db.load_character("Beto").data["inventory"]
        self.assertEqual(saved_b["gold"], 25)
        at, a, b, a_gave, b_gave = self.db.recent_trades(1)[0]
        self.assertEqual((a, b, a_gave, b_gave), ("Ana", "Beto", {"items": {"pocao_vida": 4}, "gold": 25},
                                                  {"items": {"espada_curta": 1}, "gold": 0}))
        self.assertEqual(len(self.db.recent_trades(10, "beto")), 1)
        self.assertEqual(self.db.recent_trades(10, "Caio"), [])

    def test_any_change_drops_both_accepts(self):
        self.a.inventory.add(self.items["pocao_vida"], 3)
        self.b.inventory.add_gold(10)
        self.open_trade()
        self.op(self.a, op="set", item="pocao_vida", qty=3)
        self.op(self.b, op="gold", amount=10)
        v = self.info(self.a)["v"]
        self.op(self.b, op="confirm", v=v)
        self.assertTrue(self.info(self.a)["ok_them"])
        self.op(self.a, op="set", item="pocao_vida", qty=1)  # Ana muda depois do aceite de Beto
        self.assertFalse(self.info(self.a)["ok_them"])
        self.op(self.a, op="confirm", v=self.info(self.a)["v"])
        self.op(self.b, op="confirm", v=v)  # versão velha: não vale
        self.assertIn("A troca mudou: confira de novo antes de aceitar.", self.texts(self.b))
        self.assertEqual(self.a.inventory.count("pocao_vida"), 3)  # nada foi trocado

    def test_empty_trade_cannot_be_accepted(self):
        self.open_trade()
        self.op(self.a, op="confirm", v=self.info(self.a)["v"])
        self.assertIn("Coloquem algo na troca antes de aceitar.", self.texts(self.a))

    def test_offer_limited_to_what_is_in_the_bag(self):
        self.a.inventory.add(self.items["pocao_vida"], 2)
        self.a.inventory.equipment["weapon"] = self.items["espada_curta"]  # equipada não entra
        self.a.inventory.add_gold(5)
        self.open_trade()
        self.op(self.a, op="set", item="pocao_vida", qty=50)
        self.op(self.a, op="set", item="espada_curta", qty=1)
        self.op(self.a, op="gold", amount=999)
        self.op(self.a, op="set", item="nao_existe", qty=1)
        self.assertEqual(self.info(self.a)["mine"], {"items": {"pocao_vida": 2}, "gold": 5})
        self.op(self.a, op="set", item="pocao_vida", qty=0)
        self.assertEqual(self.info(self.a)["mine"]["items"], {})

    def test_item_limit(self):
        ids = ["pocao_vida", "pocao_energia", "fragmento_espectral", "couro_grosso", "po_de_pedra"]
        for item_id in ids:
            self.a.inventory.add(self.items[item_id], 1)
        self.open_trade()
        with mock.patch.object(C, "TRADE_MAX_ITEMS", 4):
            for item_id in ids:
                self.op(self.a, op="set", item=item_id, qty=1)
        self.assertEqual(len(self.info(self.a)["mine"]["items"]), 4)
        self.assertIn("Cada um pode oferecer até 4 itens diferentes.", self.texts(self.a))

    def test_no_room_means_no_trade(self):
        self.a.inventory.add(self.items["pocao_vida"], 1)
        for _ in range(C.INVENTORY_SIZE):
            self.b.inventory.add(self.items["espada_curta"], 1)
        self.open_trade()
        self.op(self.a, op="set", item="pocao_vida", qty=1)
        self.world.drain_outbox()
        self.confirm_both()
        self.assertIn("Não cabe tudo na bolsa de Beto. Abram espaço e aceitem de novo.", self.texts(self.a))
        self.assertEqual(self.a.inventory.count("pocao_vida"), 1)
        self.assertIn(self.a.id, self.world.trades.trades)  # a troca continua aberta
        self.assertFalse(self.info(self.a)["ok_me"])

    def test_swap_of_full_bags_works(self):
        # bolsas cheias, mas cada um entrega um espaço inteiro: cabe
        for _ in range(C.INVENTORY_SIZE):
            self.a.inventory.add(self.items["espada_curta"], 1)
            self.b.inventory.add(self.items["gibao_de_couro"], 1)
        self.open_trade()
        self.op(self.a, op="set", item="espada_curta", qty=1)
        self.op(self.b, op="set", item="gibao_de_couro", qty=1)
        self.confirm_both()
        self.assertEqual(self.a.inventory.count("gibao_de_couro"), 1)
        self.assertEqual(self.b.inventory.count("espada_curta"), 1)

    def test_offer_shrinks_when_item_leaves_the_bag(self):
        self.a.inventory.add(self.items["pocao_vida"], 3)
        self.b.inventory.add_gold(5)
        self.open_trade()
        self.op(self.a, op="set", item="pocao_vida", qty=3)
        self.op(self.b, op="gold", amount=5)
        self.op(self.b, op="confirm", v=self.info(self.b)["v"])
        self.a.inventory.remove_item("pocao_vida", 2)  # usou ou largou
        run(self.world, DT)
        info = self.info(self.a)
        self.assertEqual(info["mine"]["items"], {"pocao_vida": 1})
        self.assertFalse(info["ok_them"])

    def test_trade_goes_to_client_only_when_changed(self):
        def you(p):
            run(self.world, DT)
            return [m for t, m in self.world.drain_outbox() if t == p.id and m["t"] == "snap"][-1]["you"]

        self.assertIsNone(you(self.a)["trade"])  # primeira vez: null
        self.assertNotIn("trade", you(self.a))
        self.open_trade()
        self.assertEqual(you(self.a)["trade"]["with"], "Beto")
        self.assertNotIn("trade", you(self.a))
        self.op(self.a, op="cancel")
        self.assertIsNone(you(self.a)["trade"])


class TradeLimitTests(TradeBase):
    def test_invite_errors(self):
        self.op(self.a, op="invite", target="Ana")
        self.assertIn("Você não pode trocar consigo mesmo.", self.texts(self.a))
        self.op(self.a, op="invite", target="Ninguem")
        self.assertIn("Ninguém online chamado 'Ninguem'.", self.texts(self.a))
        self.b.x, self.b.y = tile_center(8, 4)
        with mock.patch.object(C, "TRADE_RANGE", 64.0):
            self.op(self.a, op="invite", target="Beto")
        self.assertIn("Chegue mais perto de Beto para trocar.", self.texts(self.a))

    def test_ignored_cannot_ask(self):
        self.world.handle_message(self.b, {"t": "social", "op": "ignore", "name": "Ana"})
        self.world.drain_outbox()
        self.op(self.a, op="invite", target="Beto")
        self.assertIn("Beto não está aceitando trocas suas.", self.texts(self.a))
        self.assertIsNone(self.world.trades.invite_for(self.b))

    def test_one_trade_at_a_time(self):
        self.open_trade()
        self.op(self.c, op="invite", target="Ana")
        self.assertIn("Ana já está numa troca.", self.texts(self.c))
        self.op(self.a, op="invite", target="Caio")
        self.assertIn("Você já está numa troca.", self.texts(self.a))

    def test_command_shortcut(self):
        self.world.handle_message(self.a, {"t": "chat", "text": "/troca Beto"})
        self.assertEqual(self.world.trades.invite_for(self.b)["from"], "Ana")

    def test_decline_and_expire(self):
        self.op(self.a, op="invite", target="Beto")
        self.op(self.b, op="decline")
        self.assertIn("Beto recusou a troca.", self.texts(self.a))
        self.op(self.a, op="invite", target="Beto")
        run(self.world, C.TRADE_INVITE_SECONDS + DT)
        self.assertIsNone(self.world.trades.invite_for(self.b))

    def test_cancel_when_far_dead_logout_or_duel(self):
        self.open_trade()
        self.b.x += C.TRADE_LEAVE_RANGE + 50
        run(self.world, DT)
        self.assertNotIn(self.a.id, self.world.trades.trades)
        self.assertIn("A troca foi cancelada: vocês se afastaram.", self.texts(self.a))

        self.b.x, self.b.y = self.a.x, self.a.y
        self.open_trade()
        self.world._kill(self.b, None, None)
        run(self.world, DT)
        self.assertNotIn(self.a.id, self.world.trades.trades)

        run(self.world, C.RESPAWN_SECONDS + 1)
        self.b.x, self.b.y = self.a.x + 20, self.a.y
        self.open_trade()
        self.world.logout(self.b)
        self.assertNotIn(self.a.id, self.world.trades.trades)
        self.assertIn("Beto saiu do jogo. A troca foi cancelada.", self.texts(self.a))

        self.c.x, self.c.y = self.a.x + 20, self.a.y
        self.open_trade(self.a, self.c)
        self.world.handle_message(self.a, {"t": "duel", "op": "challenge", "target": "Caio"})
        self.world.handle_message(self.c, {"t": "duel", "op": "accept"})
        run(self.world, DT)
        self.assertNotIn(self.a.id, self.world.trades.trades)


class TradeToolTests(TradeBase):
    def test_admin_lists_trades(self):
        import io
        import tempfile
        from contextlib import redirect_stdout
        from pathlib import Path

        from tools.admin import main as admin_main

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "game.db"
            db = Database(db_path)
            db.save_together([], trade=("Ana", "Beto", {"items": {"pocao_vida": 2}, "gold": 0},
                                        {"items": {}, "gold": 30}))
            db.close()
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(admin_main(["trocas"], db_path=db_path), 0)
                self.assertEqual(admin_main(["trocas", "Caio"], db_path=db_path), 0)
            text = out.getvalue()
            self.assertIn("Ana deu 2× Poção de vida  ⇄  Beto deu 30 moedas", text)
            self.assertIn("Nenhuma troca de Caio.", text)


if __name__ == "__main__":
    unittest.main()
