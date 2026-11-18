"""Testes do controle da economia: de onde as moedas entram, para onde vão e o relatório."""
import io
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from server import config as C
from server.db import Database
from server.economy import day_of, economy_report
from tests.test_npcs import make_world as npc_world
from tests.test_world import CONTENT, make_world, rules_from

NOW = time.mktime((2026, 11, 18, 12, 0, 0, 0, 0, -1))
DAY = 86400


def kinds(world) -> dict[tuple[str, str], tuple[int, int]]:
    """O que está anotado (ainda não gravado): (categoria, detalhe) -> (moedas, vezes)."""
    return {(k, d): tuple(v) for (_, k, d), v in world.economy.pending.items()}


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.world = npc_world()  # um vendedor com loja (compra e vende)
        self.world.clock = lambda: NOW
        self.p, _ = self.world.login("Teste", "1234", "humano")
        self.vendor = next(iter(self.world.npcs.entities.values()))
        self.p.x, self.p.y = self.vendor.x - 40, self.vendor.y
        self.items = self.world.content.items

    def npc(self, **msg):
        self.world.handle_message(self.p, {"t": "npc", **msg})

    def test_shop_sale_creates_and_purchase_destroys(self):
        self.p.inventory.add(self.items["pocao_vida"], 3)
        self.npc(op="talk", id=self.vendor.id)
        self.npc(op="choose", i=0)  # abre a loja
        self.npc(op="sell", slot=0, qty=3)
        sold = self.p.inventory.gold
        self.assertGreater(sold, 0)
        self.npc(op="buy", item="pocao_vida", qty=1)
        spent = sold - self.p.inventory.gold
        self.assertEqual(kinds(self.world), {("venda", "pocao_vida"): (sold, 1), ("loja", "pocao_vida"): (spent, 1)})

    def test_flush_sums_into_the_database_on_save(self):
        self.world.economy.record("venda", "couro_grosso", 10)
        self.world.economy.record("venda", "couro_grosso", 5)
        self.world.save_all()
        self.assertEqual(self.world.economy.pending, {})
        self.world.economy.record("venda", "couro_grosso", 1)
        self.world.save_all()
        self.assertEqual(self.world.db.economy_rows("2026-01-01"),
                         [(day_of(NOW), "venda", "couro_grosso", 16, 3)])

    def test_nothing_recorded_for_zero(self):
        self.world.economy.record("loja", "x", 0)
        self.assertEqual(self.world.economy.pending, {})


class OtherMovementTests(unittest.TestCase):
    def setUp(self):
        self.world = make_world(rules=rules_from("multa:\n  when: death\n  do: [ { lose_gold: { percent: 50 } } ]\n"))
        self.world.clock = lambda: NOW
        self.p, _ = self.world.login("Teste", "1234", "humano")

    def test_rule_loss_is_a_sink(self):
        self.p.inventory.add_gold(40)
        self.world._kill(self.p, None, None)
        self.assertEqual(kinds(self.world), {("regra", "perda"): (20, 1)})

    def test_crafting_and_trainer_prices_are_sinks(self):
        w, p = self.world, self.p
        station = next(s for s in w.content.crafting.values() if any(r.price for r in s.recipes))
        index, recipe = next((i, r) for i, r in enumerate(station.recipes) if r.price and not r.hidden)
        for item_id, qty in recipe.needs:
            p.inventory.add(w.content.items[item_id], qty)
        for stat in p.stats.values():
            stat.level = C.MAX_STAT_LEVEL  # requisitos de atributo cumpridos
        p.inventory.add_gold(recipe.price)
        self.assertTrue(w.crafting.craft(p, station, index, 1), w.crafting.missing(p, recipe))
        trainer = next(t for t in w.content.trainers.values() if any(e.price for e in t.teaches))
        entry = next(e for e in trainer.teaches if e.price and not w.training.missing(p, e) and e.skill not in p.known)
        p.inventory.add_gold(entry.price)
        self.assertTrue(w.training.teach(p, trainer, entry.skill))
        got = kinds(w)
        self.assertEqual(got[("oficina", station.id)], (recipe.price, 1))
        self.assertEqual(got[("treinador", trainer.id)], (entry.price, 1))

    def test_staff_gift_is_recorded(self):
        self.world.db.set_role("Teste", "gm")
        self.world.admin.load_player(self.p)
        other, _ = self.world.login("Outro", "1234", "humano")
        self.world.handle_message(self.p, {"t": "admin", "op": "give_gold", "target": "Outro", "amount": 30})
        self.assertEqual(kinds(self.world), {("equipe", "Outro"): (30, 1)})
        self.assertEqual(other.inventory.gold, 30)

    def test_trade_and_bank_only_move(self):
        other, _ = self.world.login("Outro", "1234", "humano")
        other.x, other.y = self.p.x, self.p.y
        self.p.inventory.add_gold(10)
        self.world.handle_message(self.p, {"t": "trade", "op": "invite", "target": "Outro"})
        self.world.handle_message(other, {"t": "trade", "op": "accept"})
        self.world.handle_message(self.p, {"t": "trade", "op": "gold", "amount": 10})
        for who in (self.p, other):
            v = self.world.trades.client_info(who)["v"]
            self.world.handle_message(who, {"t": "trade", "op": "confirm", "v": v})
        self.assertEqual(other.inventory.gold, 10)
        self.assertEqual(kinds(self.world), {("troca", ""): (10, 1)})


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.world = make_world(self.db)
        self.world.clock = lambda: NOW
        self.a, _ = self.world.login("Ana", "1234", "humano")
        self.b, _ = self.world.login("Beto", "1234", "humano")

    def test_supply_daily_and_top(self):
        self.a.inventory.add_gold(100)
        self.b.inventory.add_gold(30)
        self.a.bank.add_gold(50)
        self.world.save_all()
        self.b.inventory.add_gold(5)  # online: vale o valor de agora, não o salvo
        self.db.add_economy([
            (day_of(NOW - DAY), "venda", "couro_grosso", 80, 4),
            (day_of(NOW), "venda", "pocao_vida", 20, 2),
            (day_of(NOW), "loja", "pocao_vida", 15, 1),
            (day_of(NOW), "troca", "", 40, 1),
            (day_of(NOW - 30 * DAY), "venda", "couro_grosso", 999, 1),  # fora do período
        ])
        rep = self.world.economy.report(7)
        self.assertEqual(rep["supply"], {"bags": 135, "banks": 50, "total": 185, "characters": 2})
        self.assertEqual(rep["today"], {"in": 20, "out": 15, "net": 5})
        self.assertEqual(rep["period"], {"in": 100, "out": 15, "net": 85})
        self.assertEqual(len(rep["daily"]), 7)
        self.assertEqual(rep["daily"][-1]["moved"], 40)
        self.assertEqual(rep["top_sold"][0], {"kind": "Vendas para lojas", "what": "Couro grosso", "amount": 80,
                                              "count": 4})
        self.assertEqual(rep["top_spent"][0]["what"], "Poção de vida")
        self.assertEqual([r["name"] for r in rep["richest"]], ["Ana", "Beto"])
        self.assertEqual(rep["richest_banks"], [{"name": "Ana", "gold": 50}])
        # 85 a mais sobre 100 que existiam antes: cresceu 85% -> alerta
        self.assertTrue(any("cresceram 85%" in a for a in rep["alerts"]))

    def test_report_without_world(self):
        self.db.add_economy([(day_of(NOW), "equipe", "Ana", 500, 1)])
        rep = economy_report(self.db, CONTENT, 1, NOW)
        self.assertEqual(rep["period"]["in"], 500)
        self.assertTrue(any("equipe deu 500" in a for a in rep["alerts"]))

    def test_admin_window_and_terminal(self):
        self.db.set_role("Ana", "moderador")  # tem a permissão "log"
        self.world.admin.load_player(self.a)
        self.world.drain_outbox()
        self.world.handle_message(self.a, {"t": "admin", "op": "economy", "days": 3})
        msgs = [m for t, m in self.world.drain_outbox() if t == self.a.id]
        self.assertEqual([m["t"] for m in msgs], ["admin_economy"])
        self.assertEqual(msgs[0]["days"], 3)
        self.world.handle_message(self.b, {"t": "admin", "op": "economy"})  # não é da equipe
        self.assertEqual([m for t, m in self.world.drain_outbox() if t == self.b.id], [])

        from tools.admin import main as admin_main
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "game.db"
            db = Database(path)
            db.add_economy([(day_of(time.time()), "venda", "couro_grosso", 12, 2)])
            db.close()
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(admin_main(["economia", "3"], db_path=path), 0)
            text = out.getvalue()
            self.assertIn("entraram 12, saíram 0, saldo +12", text)
            self.assertIn("Couro grosso", text)


class NoGoldSourcesTests(unittest.TestCase):
    def test_real_content_has_no_gold_rewards(self):
        from server.gamedata import load_game_data
        content, _ = load_game_data()
        self.assertFalse(any(hasattr(e, "gold") for e in content.enemies.values()))
        actions = [a for q in content.quests.values() for a, _ in q.rule.actions]
        actions += [a for e in content.events.values() for a, _ in e.rule.actions]
        actions += [a for r in content.rules for a, _ in r.actions]
        self.assertNotIn("give_gold", actions)


if __name__ == "__main__":
    unittest.main()
