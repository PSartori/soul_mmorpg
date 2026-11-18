"""Testes das ferramentas de roleplay: fala local, ações, mensagem privada, ignorar e perfil."""
import unittest
from unittest import mock

from server import config as C
from server.db import Database
from server.saves import migrate
from server.social import clean_profile
from tests.test_world import make_world, run, tile_center

DT = 1.0 / C.TICK_RATE


class Base(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.world = make_world(self.db)
        self.world.enemies.clear()
        self.a = self.login("Ana")
        self.b = self.login("Beto")
        self.c = self.login("Caio")
        self.world.drain_outbox()

    def login(self, name, world=None):
        p, err = (world or self.world).login(name, "1234", "humano")
        self.assertIsNone(err, err)
        p.x, p.y = tile_center(2, 2)
        return p

    def chat(self, p, text, **extra):
        self.world.handle_message(p, {"t": "chat", "text": text, **extra})

    def social(self, p, **data):
        self.world.handle_message(p, {"t": "social", **data})

    def chats(self, p) -> list[dict]:
        """Mensagens de chat que chegaram para p (e esvazia a caixa de saída)."""
        return [m for target, m in self.world.drain_outbox() if target in (p.id, None) and m["t"] == "chat"]

    def out_for(self, out, p, kind):
        return [m for target, m in out if target in (p.id, None) and m["t"] == kind]

    def texts(self, p) -> list[str]:
        return [m["text"] for target, m in self.world.drain_outbox() if target == p.id and m["t"] == "sys"]


class LocalChatTests(Base):
    def test_local_reaches_only_who_is_near(self):
        self.b.x, self.b.y = tile_center(3, 2)
        self.c.x, self.c.y = tile_center(8, 4)
        with mock.patch.object(C, "LOCAL_CHAT_RANGE", 64.0):
            self.chat(self.a, "Bom dia.", ch="local")
        out = self.world.drain_outbox()
        heard = {target for target, m in out if m["t"] == "chat"}
        self.assertEqual(heard, {self.a.id, self.b.id})  # Caio está longe
        msg = next(m for _, m in out if m["t"] == "chat")
        self.assertEqual((msg["ch"], msg["from"], msg["id"], msg["text"]), ("local", "Ana", self.a.id, "Bom dia."))

    def test_local_does_not_cross_maps(self):
        self.b.map_id = "outro"
        self.chat(self.a, "alguém aí?", ch="local")
        heard = {target for target, m in self.world.drain_outbox() if m["t"] == "chat"}
        self.assertEqual(heard, {self.a.id, self.c.id})

    def test_emote_channel_and_me_command(self):
        self.chat(self.a, "acena para todos", ch="emote")
        msg = self.chats(self.b)[0]
        self.assertEqual((msg["ch"], msg["text"], msg["id"]), ("emote", "acena para todos", self.a.id))
        self.chat(self.a, "/me senta no chão", ch="org")  # comando vale em qualquer canal
        msg = self.chats(self.b)[0]
        self.assertEqual((msg["ch"], msg["text"]), ("emote", "senta no chão"))
        self.chat(self.a, "/me")
        self.assertIn("Descreva a ação. Ex.: acena para todos.", self.texts(self.a))

    def test_l_command_is_local(self):
        self.chat(self.a, "/l psiu")
        self.assertEqual(self.chats(self.b)[0]["ch"], "local")

    def test_general_chat_still_reaches_everyone(self):
        self.c.map_id = "outro"
        self.chat(self.a, "oi, mundo")
        heard = {target for target, m in self.world.drain_outbox() if m["t"] == "chat"}
        self.assertEqual(heard, {self.a.id, self.b.id, self.c.id})


class PrivateMessageTests(Base):
    def test_private_message_goes_to_both_only(self):
        self.chat(self.a, "segredo", ch="pm", to="beto")
        out = self.world.drain_outbox()
        got = {target: m for target, m in out if m["t"] == "chat"}
        self.assertEqual(set(got), {self.a.id, self.b.id})
        self.assertEqual((got[self.b.id]["ch"], got[self.b.id]["from"]), ("pm", "Ana"))
        self.assertNotIn("to", got[self.b.id])
        self.assertEqual(got[self.a.id]["to"], "Beto")  # eco para quem mandou

    def test_reply_goes_to_last_partner(self):
        self.chat(self.a, "/p Beto tudo bem?")
        self.world.drain_outbox()
        self.chat(self.b, "/r tudo!")
        got = {target: m for target, m in self.world.drain_outbox() if m["t"] == "chat"}
        self.assertEqual(set(got), {self.a.id, self.b.id})
        self.assertEqual(got[self.a.id]["text"], "tudo!")
        self.chat(self.c, "/r ei")
        self.assertIn("Ninguém para responder ainda.", self.texts(self.c))

    def test_private_message_errors(self):
        self.chat(self.a, "oi", ch="pm", to="Ninguem")
        self.assertIn("Ninguem não está online.", self.texts(self.a))
        self.chat(self.a, "oi", ch="pm", to="Ana")
        self.assertIn("Você não pode mandar mensagem para si mesmo.", self.texts(self.a))
        self.chat(self.a, "oi", ch="pm")
        self.assertTrue(any("Para quem?" in t for t in self.texts(self.a)))
        self.chat(self.a, "/p")
        self.assertTrue(any("Use: /p" in t for t in self.texts(self.a)))

    def test_muted_cannot_whisper_nor_speak_locally(self):
        self.a.muted = True
        self.chat(self.a, "psiu", ch="pm", to="Beto")
        self.chat(self.a, "psiu", ch="local")
        self.chat(self.a, "pula", ch="emote")
        out = self.world.drain_outbox()
        self.assertFalse(any(m["t"] == "chat" for _, m in out))
        warnings = [m["text"] for t, m in out if t == self.a.id and m["t"] == "sys"]
        self.assertEqual(len(warnings), 3)
        self.assertTrue(all("silenciado" in text for text in warnings))


class IgnoreTests(Base):
    def test_ignored_messages_do_not_arrive(self):
        self.social(self.b, op="ignore", name="ana")
        self.assertTrue(any("Você está ignorando Ana" in t for t in self.texts(self.b)))
        for ch in (None, "local", "emote"):
            self.chat(self.a, "oi", **({"ch": ch} if ch else {}))
            heard = {target for target, m in self.world.drain_outbox() if m["t"] == "chat"}
            self.assertNotIn(self.b.id, heard, ch)
            self.assertIn(self.c.id, heard, ch)
        self.chat(self.a, "oi", ch="pm", to="Beto")
        out = self.world.drain_outbox()
        self.assertFalse(any(m["t"] == "chat" for _, m in out))
        self.assertTrue(any(m.get("text") == "Beto não está recebendo mensagens suas." for t, m in out))
        # quem ignora continua falando normalmente com quem ignorou
        self.chat(self.b, "oi", ch="local")
        heard = {target for target, m in self.world.drain_outbox() if m["t"] == "chat"}
        self.assertIn(self.a.id, heard)

    def test_ignored_org_and_party_chat(self):
        self.chat(self.a, "/entrar vigilia")
        self.chat(self.b, "/entrar vigilia")
        self.world.handle_message(self.a, {"t": "party", "op": "invite", "target": "Beto"})
        self.world.handle_message(self.b, {"t": "party", "op": "accept"})
        self.social(self.b, op="ignore", name="Ana")
        self.world.drain_outbox()
        self.chat(self.a, "pela org", ch="org")
        self.chat(self.a, "pelo grupo", ch="party")
        heard = [target for target, m in self.world.drain_outbox() if m["t"] == "chat"]
        self.assertNotIn(self.b.id, heard)
        self.assertEqual(heard.count(self.a.id), 2)  # quem falou vê a própria mensagem

    def test_ignored_cannot_challenge_or_invite(self):
        self.social(self.b, op="ignore", name="Ana")
        self.world.drain_outbox()
        self.world.handle_message(self.a, {"t": "duel", "op": "challenge", "target": "Beto"})
        self.assertIn("Beto não está aceitando desafios seus.", self.texts(self.a))
        self.assertIsNone(self.world.duels.invite_for(self.b))
        self.world.handle_message(self.a, {"t": "party", "op": "invite", "target": "Beto"})
        self.assertIn("Beto não está aceitando convites seus.", self.texts(self.a))
        self.assertIsNone(self.world.parties.invite_for(self.b))

    def test_ignoring_drops_pending_challenge_and_invite(self):
        self.world.handle_message(self.a, {"t": "duel", "op": "challenge", "target": "Beto"})
        self.world.handle_message(self.a, {"t": "party", "op": "invite", "target": "Beto"})
        self.assertIsNotNone(self.world.duels.invite_for(self.b))
        self.assertIsNotNone(self.world.parties.invite_for(self.b))
        self.social(self.b, op="ignore", name="Ana")
        self.assertIsNone(self.world.duels.invite_for(self.b))
        self.assertIsNone(self.world.parties.invite_for(self.b))

    def test_ignore_errors(self):
        self.social(self.a, op="ignore", name="Ana")
        self.assertIn("Você não pode ignorar a si mesmo.", self.texts(self.a))
        self.social(self.a, op="ignore", name="Ninguem")
        self.assertIn("Nenhum personagem chamado 'Ninguem'.", self.texts(self.a))
        self.social(self.a, op="ignore", name="Beto")
        self.social(self.a, op="ignore", name="beto")
        self.assertIn("Você já ignora Beto.", self.texts(self.a))
        self.social(self.a, op="unignore", name="Caio")
        self.assertIn("Você não ignora Caio.", self.texts(self.a))

    def test_cannot_ignore_staff(self):
        self.db.set_role("Beto", "moderador")
        self.world.admin.load_player(self.b)
        self.social(self.a, op="ignore", name="Beto")
        self.assertIn("Não dá para ignorar alguém da equipe.", self.texts(self.a))
        self.assertEqual(self.a.ignored, [])

    def test_ignore_offline_character_and_unignore(self):
        self.world.logout(self.c)
        self.social(self.a, op="ignore", name="caio")
        self.assertEqual(self.a.ignored, ["Caio"])  # nome como está no banco
        self.social(self.a, op="unignore", name="CAIO")
        self.assertEqual(self.a.ignored, [])
        self.assertIn("Você deixou de ignorar Caio.", self.texts(self.a))

    def test_ignore_list_is_capped(self):
        with mock.patch.object(C, "IGNORE_MAX", 1):
            self.social(self.a, op="ignore", name="Beto")
            self.social(self.a, op="ignore", name="Caio")
        self.assertEqual(self.a.ignored, ["Beto"])

    def test_ignore_list_goes_to_client_only_when_changed(self):
        def you():
            run(self.world, DT)
            return [m for t, m in self.world.drain_outbox() if t == self.a.id and m["t"] == "snap"][-1]["you"]

        self.assertEqual(you()["ign"], [])  # primeira vez vai, mesmo vazia
        self.assertNotIn("ign", you())
        self.social(self.a, op="ignore", name="Caio")
        self.social(self.a, op="ignore", name="Beto")
        self.assertEqual(you()["ign"], ["Beto", "Caio"])
        self.assertNotIn("ign", you())

    def test_ignore_list_and_profile_are_saved(self):
        self.social(self.a, op="ignore", name="Beto")
        self.social(self.a, op="set_profile", text="Uma viajante calada.")
        self.world.logout(self.a)
        world2 = make_world(self.db)
        a = self.login("Ana", world=world2)
        self.assertEqual(a.ignored, ["Beto"])
        self.assertEqual(a.profile, "Uma viajante calada.")


class ProfileTests(Base):
    def profile_msg(self, p):
        return [m for t, m in self.world.drain_outbox() if t == p.id and m["t"] == "profile"][-1]

    def test_set_and_view_profile(self):
        self.social(self.a, op="set_profile", text="  Alta, de capa verde.\n\n\n\nFala pouco.  ")
        own = self.profile_msg(self.a)
        self.assertTrue(own["me"])
        self.assertEqual(own["text"], "Alta, de capa verde.\n\nFala pouco.")
        self.social(self.b, op="profile", name="ana")
        seen = self.profile_msg(self.b)
        self.assertEqual((seen["name"], seen["me"], seen["ignored"], seen["can_clear"]), ("Ana", False, False, False))
        self.assertEqual((seen["race"], seen["lv"]), ("humano", 1))
        self.assertEqual(seen["text"], "Alta, de capa verde.\n\nFala pouco.")

    def test_view_own_without_name_and_offline(self):
        self.social(self.a, op="profile")
        self.assertEqual(self.profile_msg(self.a)["name"], "Ana")
        self.social(self.a, op="profile", name="Ninguem")
        self.assertIn("Ninguem não está online.", self.texts(self.a))

    def test_profile_text_is_cleaned(self):
        self.assertEqual(clean_profile("a\x00b\x07c\r\nd"), "abc\nd")
        self.assertEqual(len(clean_profile("x" * 5000)), C.PROFILE_MAX_LEN)
        self.social(self.a, op="set_profile", text=123)  # tipo errado: ignora
        self.assertEqual(self.a.profile, "")

    def test_muted_cannot_change_profile(self):
        self.a.muted = True
        self.social(self.a, op="set_profile", text="ofensa")
        self.assertEqual(self.a.profile, "")

    def test_staff_clears_profile(self):
        self.social(self.a, op="set_profile", text="texto ofensivo")
        self.db.set_role("Caio", "moderador")
        self.world.admin.load_player(self.c)
        self.social(self.c, op="profile", name="Ana")
        self.assertTrue(self.profile_msg(self.c)["can_clear"])
        self.world.handle_message(self.c, {"t": "admin", "op": "clear_profile", "target": "Ana"})
        self.assertEqual(self.a.profile, "")
        self.assertEqual(self.db.recent_log(1)[0][1:4], ("Caio", "apagou o perfil de", "Ana"))
        # quem não é da equipe não pode
        self.social(self.a, op="set_profile", text="de novo")
        self.world.handle_message(self.b, {"t": "admin", "op": "clear_profile", "target": "Ana"})
        self.assertEqual(self.a.profile, "de novo")


class SaveTests(unittest.TestCase):
    def test_v6_save_gets_empty_social(self):
        data = migrate({"v": 6, "race": "humano"})
        self.assertEqual(data["social"], {"profile": "", "ignore": []})

    def test_corrupt_social_is_cleaned(self):
        world = make_world()
        p, _ = world.login("Ana", "1234", "humano")
        p.load_dict({"social": {"profile": 5, "ignore": ["Beto", 3, None]}})
        self.assertEqual((p.profile, p.ignored), ("", ["Beto"]))
        p.load_dict({"social": "lixo"})
        self.assertEqual(p.ignored, ["Beto"])


if __name__ == "__main__":
    unittest.main()
