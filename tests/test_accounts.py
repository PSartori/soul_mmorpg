"""Testes de contas (vários personagens), migração do banco, travas de senha, limites e bans."""
import contextlib
import hashlib
import io
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server import config as C
from server.accounts import AccountService, LoginGuard, RateLimiter, Session
from server.db import Database, hash_password
from tests.test_world import make_world, run

DT = 1.0 / C.TICK_RATE


async def fast_hash(password: str, salt: bytes) -> bytes:
    """Troca o PBKDF2 (lento de propósito) por um hash rápido nos testes."""
    return hashlib.sha256(salt + password.encode()).digest()


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class MigrationTests(unittest.TestCase):
    def test_old_database_gets_one_account_per_character(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            path = tmp / "old.db"
            conn = sqlite3.connect(path)  # banco no formato de antes das contas
            conn.execute("CREATE TABLE characters (name TEXT PRIMARY KEY COLLATE NOCASE, pw_hash BLOB NOT NULL, "
                         "salt BLOB NOT NULL, data TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, "
                         "updated_at REAL NOT NULL)")
            for name, pw in (("Velho", "senha1"), ("Antigo", "abcd")):
                salt = b"s" * 16
                conn.execute("INSERT INTO characters VALUES (?, ?, ?, '{\"v\": 5}', 1, 1)",
                             (name, hash_password(pw, salt), salt))
            conn.commit()
            conn.close()

            db = Database(path)
            velho = db.load_account("velho")
            self.assertEqual(velho.name, "Velho")
            self.assertTrue(db.verify_password(velho, "senha1"))  # a senha de antes continua valendo
            self.assertEqual([c.name for c in db.list_characters(velho.id)], ["Velho"])
            self.assertEqual(db.account_of("Antigo").name, "Antigo")
            db.close()
            Database(path).close()  # abrir de novo não duplica nada
            check = sqlite3.connect(path)
            self.assertEqual(check.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 2)
            check.close()
        finally:
            shutil.rmtree(tmp)


class ServiceBase(unittest.IsolatedAsyncioTestCase):
    ip = "203.0.113.7"

    def setUp(self):
        self.world = make_world()
        self.clock = FakeClock()
        self.service = AccountService(self.world, hasher=fast_hash, guard=LoginGuard(clock=self.clock))

    def session(self, ip=None) -> Session:
        return Session(ip=self.ip if ip is None else ip)

    async def send(self, s, **msg) -> dict:
        replies = await self.service.handle(s, msg)
        return replies[0] if replies else {}

    async def registered(self, name="Conta1", password="segredo1", ip=None) -> Session:
        s = self.session(ip)
        reply = await self.send(s, t="account", op="register", name=name, password=password)
        self.assertEqual(reply["t"], "account", reply)
        return s


class RegisterAndLoginTests(ServiceBase):
    async def test_register_validates(self):
        s = self.session()
        for name, pw, error in (("ab", "segredo1", "3 a 16"), ("Conta1", "curta", "pelo menos 6"),
                                ("Conta1", "conta1", "igual ao nome")):
            reply = await self.send(s, t="account", op="register", name=name, password=pw)
            self.assertEqual(reply["t"], "error")
            self.assertIn(error, reply["msg"])
        await self.registered()
        reply = await self.send(self.session(), t="account", op="register", name="CONTA1", password="segredo1")
        self.assertIn("Já existe", reply["msg"])

    async def test_accounts_per_ip(self):
        for i in range(C.ACCOUNTS_PER_IP):
            await self.registered(name=f"Conta{i}")
        reply = await self.send(self.session(), t="account", op="register", name="Mais1", password="segredo1")
        self.assertIn("Contas demais", reply["msg"])
        # o próprio computador não tem limite (para testar à vontade)
        for i in range(C.ACCOUNTS_PER_IP + 1):
            await self.registered(name=f"Local{i}", ip="127.0.0.1")

    async def test_login_and_generic_error(self):
        await self.registered()
        s = self.session()
        wrong = await self.send(s, t="account", op="login", name="Conta1", password="errada")
        unknown = await self.send(s, t="account", op="login", name="Ninguem", password="errada")
        self.assertEqual(wrong["msg"], unknown["msg"])  # não revela se a conta existe
        reply = await self.send(s, t="account", op="login", name="conta1", password="segredo1")
        self.assertEqual((reply["t"], reply["name"], reply["chars"]), ("account", "Conta1", []))
        self.assertEqual(self.world.db.load_account("Conta1").last_ip, self.ip)

    async def test_too_many_wrong_passwords_lock_ip_and_account(self):
        await self.registered()
        s = self.session()
        for _ in range(C.LOGIN_MAX_FAILS):
            await self.send(s, t="account", op="login", name="Conta1", password="errada")
        reply = await self.send(s, t="account", op="login", name="Conta1", password="segredo1")
        self.assertIn("Muitas tentativas", reply["msg"])  # nem a certa entra
        other_ip = Session(ip="198.51.100.1")
        reply = await self.send(other_ip, t="account", op="login", name="Conta1", password="segredo1")
        self.assertIn("Muitas tentativas", reply["msg"])  # a conta também está travada
        self.clock.now += C.LOGIN_LOCK_SECONDS + 1
        reply = await self.send(s, t="account", op="login", name="Conta1", password="segredo1")
        self.assertEqual(reply["t"], "account")

    async def test_logout_back_to_login(self):
        s = await self.registered()
        reply = await self.send(s, t="account", op="logout")
        self.assertEqual(reply, {"t": "account", "name": None})
        self.assertIsNone(s.account)
        self.assertEqual(await self.send(s, t="char", op="create", name="Heroi", race="humano"), {})


class CharacterTests(ServiceBase):
    async def test_create_list_and_enter(self):
        s = await self.registered()
        reply = await self.send(s, t="char", op="create", name="Heroi", race="humano")
        self.assertEqual([c["name"] for c in reply["chars"]], ["Heroi"])
        self.assertEqual(reply["chars"][0]["race"], "Humano")
        reply = await self.send(s, t="char", op="create", name="Heroina", race="desperto")
        self.assertEqual(len(reply["chars"]), 2)
        welcome = await self.send(s, t="char", op="enter", name="heroina")
        self.assertEqual(welcome["t"], "welcome")
        p = s.player
        self.assertEqual((p.name, p.race_id, p.account_id, p.ip), ("Heroina", "desperto", s.account.id, self.ip))
        self.assertEqual(p.hp, p.max_hp)

    async def test_create_rules(self):
        s = await self.registered()
        await self.send(s, t="char", op="create", name="Heroi", race="humano")
        other = await self.registered(name="Conta2")
        for name, race, error in (("Heroi", "humano", "Já existe"), ("x", "humano", "3 a 16"),
                                  ("Novo1", "espectral", "Escolha uma raça"), ("Novo1", None, "Escolha uma raça")):
            reply = await self.send(other, t="char", op="create", name=name, race=race)
            self.assertIn(error, reply["msg"])
        for i in range(C.MAX_CHARACTERS - 1):
            await self.send(s, t="char", op="create", name=f"Extra{i}", race="humano")
        reply = await self.send(s, t="char", op="create", name="Demais", race="humano")
        self.assertIn(f"até {C.MAX_CHARACTERS}", reply["msg"])

    async def test_only_own_characters_and_one_at_a_time(self):
        a = await self.registered()
        await self.send(a, t="char", op="create", name="Heroi", race="humano")
        await self.send(a, t="char", op="create", name="Outro", race="humano")
        b = await self.registered(name="Conta2")
        reply = await self.send(b, t="char", op="enter", name="Heroi")
        self.assertIn("não encontrado", reply["msg"])
        await self.send(a, t="char", op="enter", name="Heroi")
        second = Session(ip=self.ip, account=a.account)  # a mesma conta noutra aba
        reply = await self.send(second, t="char", op="enter", name="Outro")
        self.assertIn("Outro personagem desta conta", reply["msg"])

    async def test_leave_and_change_password(self):
        s = await self.registered()
        await self.send(s, t="char", op="create", name="Heroi", race="humano")
        await self.send(s, t="char", op="enter", name="Heroi")
        reply = await self.send(s, t="account", op="password", old="errada", new="novasenha")
        self.assertFalse(reply["ok"])
        reply = await self.send(s, t="account", op="password", old="segredo1", new="curta")
        self.assertIn("pelo menos", reply["text"])
        reply = await self.send(s, t="account", op="password", old="segredo1", new="novasenha")
        self.assertTrue(reply["ok"])
        reply = await self.send(s, t="char", op="leave")
        self.assertEqual(reply["t"], "account")
        self.assertIsNone(s.player)
        self.assertEqual(self.world.players, {})
        fresh = self.session()
        reply = await self.send(fresh, t="account", op="login", name="Conta1", password="novasenha")
        self.assertEqual(reply["t"], "account")


class BanTests(ServiceBase):
    async def test_character_account_and_ip_bans(self):
        s = await self.registered()
        await self.send(s, t="char", op="create", name="Heroi", race="humano")
        db = self.world.db
        db.add_sanction("Heroi", "ban", None, "trapaça", "equipe")
        reply = await self.send(s, t="char", op="enter", name="Heroi")
        self.assertIn("banido", reply["msg"])
        db.lift_sanctions("Heroi", "ban")

        db.add_sanction("Conta1", "account_ban", None, "", "equipe")
        reply = await self.send(self.session(), t="account", op="login", name="Conta1", password="segredo1")
        self.assertIn("Esta conta está banida", reply["msg"].replace("banido", "banida"))
        db.lift_sanctions("Conta1", "account_ban")

        db.add_sanction(self.ip, "ip_ban", None, "", "equipe")
        reply = await self.send(self.session(), t="account", op="login", name="Conta1", password="segredo1")
        self.assertIn("Este endereço", reply["msg"])
        reply = await self.send(self.session(), t="account", op="register", name="Nova1", password="segredo1")
        self.assertIn("Este endereço", reply["msg"])
        reply = await self.send(self.session("198.51.100.9"), t="account", op="login", name="Conta1",
                                password="segredo1")
        self.assertEqual(reply["t"], "account")  # de outro endereço, entra


class LimitTests(unittest.TestCase):
    def test_rate_limiter(self):
        clock = FakeClock()
        limiter = RateLimiter(rate=10, burst=5, clock=clock)
        self.assertEqual(sum(limiter.allow() for _ in range(8)), 5)
        self.assertEqual(limiter.dropped, 3)
        clock.now += 0.35  # 3,5 fichas de volta (dá para 3 mensagens)
        self.assertEqual(sum(limiter.allow() for _ in range(5)), 3)
        clock.now += 10  # balde cheio de novo: zera a contagem de descartes
        self.assertTrue(limiter.allow())
        self.assertEqual(limiter.dropped, 0)

    def test_flooding(self):
        clock = FakeClock()
        limiter = RateLimiter(rate=1, burst=1, clock=clock)
        for _ in range(C.MSG_FLOOD_KICK + 1):
            limiter.allow()
        self.assertTrue(limiter.flooding)

    def test_chat_limit(self):
        world = make_world()
        p, _ = world.login("Falador", "1234", "humano")
        world.drain_outbox()
        for i in range(C.CHAT_BURST + 2):
            world.handle_message(p, {"t": "chat", "text": f"oi {i}"})
        out = world.drain_outbox()
        said = [m["text"] for _, m in out if m["t"] == "chat"]
        self.assertEqual(len(said), C.CHAT_BURST)
        self.assertTrue(any("Calma" in m["text"] for _, m in out if m["t"] == "sys"))
        run(world, C.CHAT_WINDOW + 0.1)
        world.handle_message(p, {"t": "chat", "text": "voltei"})
        self.assertTrue(any(m.get("text") == "voltei" for _, m in world.drain_outbox()))


class AdminAccountTests(unittest.TestCase):
    def setUp(self):
        self.world = make_world()
        self.adm, _ = self.world.login("Dono", "1234", "humano")
        self.world.db.set_role("Dono", "admin")
        self.world.admin.load_player(self.adm)
        self.alvo, _ = self.world.login("Alvo", "1234", "humano")
        self.alvo.ip = "203.0.113.50"
        self.world.drain_outbox()

    def admin(self, **data) -> dict:
        self.world.handle_message(self.adm, {"t": "admin", **data})
        return next(m for t, m in self.world.drain_outbox() if t == self.adm.id and m["t"] == "admin_result")

    def test_ban_scopes(self):
        db = self.world.db
        reply = self.admin(op="ban", target="Alvo", scope="account", reason="teste")
        self.assertTrue(reply["ok"], reply)
        self.assertIsNotNone(db.active_sanction("Alvo", "account_ban", self.world.clock()))
        reply = self.admin(op="ban", target="Alvo", scope="ip")
        self.assertTrue(reply["ok"], reply)
        self.assertIsNotNone(db.active_sanction("203.0.113.50", "ip_ban", self.world.clock()))
        self.alvo.ip = "127.0.0.1"
        reply = self.admin(op="ban", target="Alvo", scope="ip")
        self.assertIn("IP local", reply["text"])
        self.assertFalse(self.admin(op="ban", target="Alvo", scope="planeta")["ok"])
        reply = self.admin(op="unban", target="203.0.113.50", kind="ip_ban")
        self.assertTrue(reply["ok"], reply)
        self.assertIsNone(db.active_sanction("203.0.113.50", "ip_ban", self.world.clock()))

    def test_reset_password(self):
        reply = self.admin(op="reset_password", target="Alvo")
        self.assertTrue(reply["ok"], reply)
        temp = reply["text"].split("Alvo: ")[1].split(" ")[0]
        self.assertEqual(len(temp), 10)
        self.assertTrue(self.world.db.verify_password(self.world.db.load_account("Alvo"), temp))
        self.world.db.set_role("Dono", "gm")  # gm não tem a permissão "account"
        self.world.admin.load_player(self.adm)
        self.assertFalse(self.admin(op="reset_password", target="Alvo")["ok"])

    def test_cli_account_commands(self):
        from tools.admin import main as admin_main
        tmp = Path(tempfile.mkdtemp())
        try:
            db_path = tmp / "game.db"
            db = Database(db_path)
            db.create_character("Dono", "1234")
            db.add_sanction("203.0.113.9", "ip_ban", None, "", "alguém")
            db.close()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(admin_main(["conta", "dono"], db_path=db_path), 0)
                self.assertEqual(admin_main(["senha", "Dono"], db_path=db_path), 0)
                self.assertEqual(admin_main(["unban-ip", "203.0.113.9"], db_path=db_path), 0)
                self.assertEqual(admin_main(["bans"], db_path=db_path), 0)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(admin_main(["senha", "Ninguem"], db_path=db_path), 1)
            text = out.getvalue()
            self.assertIn("Conta Dono: Dono", text)
            temp = text.split("Senha temporária da conta Dono: ")[1].split()[0]
            check = Database(db_path)
            self.assertTrue(check.verify_password(check.load_account("Dono"), temp))
            check.close()
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
