"""Testes dos relatos de bug: guardar com o contexto, limites, equipe e terminal."""
import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from server import config as C
from server.db import Database
from tests.test_world import make_world, run, tile_center


class BugReportTests(unittest.TestCase):
    def setUp(self):
        self.world = make_world()
        self.p, _ = self.world.login("Jogador", "1234", "humano")
        self.p.x, self.p.y = tile_center(5, 3)
        self.world.drain_outbox()

    def report(self, text="O inimigo atravessou a parede.", client=None) -> dict:
        self.world.handle_message(self.p, {"t": "bug", "text": text, "client": client or {}})
        return next(m for t, m in self.world.drain_outbox() if t == self.p.id and m["t"] == "bug_result")

    def test_report_is_saved_with_context(self):
        reply = self.report(client={"view": "3d", "agent": "Navegador X", "screen": "1280×800",
                                    "errors": ["erro 1", "erro 2"], "lixo": "ignorado"})
        self.assertTrue(reply["ok"])
        self.assertIn("Relato #1", reply["text"])
        (saved,) = self.world.db.list_bug_reports()
        self.assertEqual((saved["character"], saved["account"], saved["map"]), ("Jogador", "Jogador", "teste"))
        self.assertEqual((saved["x"], saved["y"]), tile_center(5, 3))
        self.assertEqual(saved["zone"], "Campo")
        self.assertEqual(saved["text"], "O inimigo atravessou a parede.")
        self.assertEqual(saved["client"], {"view": "3d", "agent": "Navegador X", "screen": "1280×800",
                                           "errors": ["erro 1", "erro 2"]})

    def test_limits(self):
        self.assertFalse(self.report(text="  oi ")["ok"])  # curto demais
        self.assertTrue(self.report()["ok"])
        reply = self.report()
        self.assertFalse(reply["ok"])
        self.assertIn("Espere", reply["text"])
        run(self.world, C.BUG_REPORT_COOLDOWN + 0.1)
        self.assertTrue(self.report(text="x" * (C.BUG_REPORT_MAX_LEN + 500))["ok"])
        newest = self.world.db.list_bug_reports()[0]
        self.assertEqual(len(newest["text"]), C.BUG_REPORT_MAX_LEN)

    def test_client_data_is_trimmed(self):
        self.report(client={"agent": "a" * 5000, "errors": ["e" * 900] * 20, "view": ["não é texto"]})
        client = self.world.db.list_bug_reports()[0]["client"]
        self.assertEqual(len(client["agent"]), 300)
        self.assertEqual(len(client["errors"]), 5)
        self.assertEqual(len(client["errors"][0]), 300)
        self.assertNotIn("view", client)
        self.world.handle_message(self.p, {"t": "bug", "text": "sem dados do cliente", "client": "lixo"})
        run(self.world, 0.05)


class StaffBugTests(unittest.TestCase):
    def setUp(self):
        self.world = make_world()
        self.adm, _ = self.world.login("Dono", "1234", "humano")
        self.world.db.set_role("Dono", "admin")
        self.world.admin.load_player(self.adm)
        self.p, _ = self.world.login("Jogador", "1234", "humano")
        self.p.x, self.p.y = tile_center(6, 2)
        self.world.drain_outbox()

    def admin(self, **data) -> list[dict]:
        self.world.handle_message(self.adm, {"t": "admin", **data})
        return [m for t, m in self.world.drain_outbox() if t == self.adm.id]

    def test_staff_is_warned_and_can_resolve(self):
        self.world.handle_message(self.p, {"t": "bug", "text": "A loja não abre."})
        warned = [m["text"] for t, m in self.world.drain_outbox() if t == self.adm.id and m["t"] == "sys"]
        self.assertTrue(any("Novo relato de bug #1 de Jogador" in t for t in warned))
        data = next(m for m in self.admin(op="refresh") if m["t"] == "admin_data")
        self.assertEqual([b["id"] for b in data["bugs"]], [1])
        self.assertEqual(data["bugs"][0]["text"], "A loja não abre.")

        out = self.admin(op="bug_goto", id=1)
        self.assertTrue(next(m for m in out if m["t"] == "admin_result")["ok"])
        self.assertEqual((self.adm.x, self.adm.y), tile_center(6, 2))

        out = self.admin(op="bug_close", id=1)
        self.assertTrue(next(m for m in out if m["t"] == "admin_result")["ok"])
        data = next(m for m in out if m["t"] == "admin_data")
        self.assertEqual(data["bugs"], [])
        out = self.admin(op="bug_close", id=1)
        self.assertFalse(next(m for m in out if m["t"] == "admin_result")["ok"])
        self.assertFalse(next(m for m in self.admin(op="bug_close", id="1") if m["t"] == "admin_result")["ok"])

    def test_only_staff_with_log_sees_reports(self):
        self.world.db.set_role("Dono", None)
        self.world.admin.load_player(self.adm)
        self.assertEqual(self.admin(op="refresh"), [])  # sem papel: nada


class BugCliTests(unittest.TestCase):
    def test_cli(self):
        from tools.admin import main as admin_main
        tmp = Path(tempfile.mkdtemp())
        try:
            db_path = tmp / "game.db"
            db = Database(db_path)
            db.add_bug_report("Jogador", "Conta", "inicio", 100, 200, "Campos", "Travou no portal.",
                              {"view": "2d", "errors": ["falhou x"]})
            db.close()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(admin_main(["bugs"], db_path=db_path), 0)
                self.assertEqual(admin_main(["bug-resolvido", "1"], db_path=db_path), 0)
                self.assertEqual(admin_main(["bugs"], db_path=db_path), 0)
                self.assertEqual(admin_main(["bugs", "--todos"], db_path=db_path), 0)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(admin_main(["bug-resolvido", "1"], db_path=db_path), 1)
            text = out.getvalue()
            self.assertIn("#1", text)
            self.assertIn("Travou no portal.", text)
            self.assertIn("erro: falhou x", text)
            self.assertIn("Nenhum relato aberto.", text)
            self.assertIn("resolvido por terminal", text)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
