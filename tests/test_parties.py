"""Testes de grupos: convite, liderança, chat, abates, missões, saque e fogo amigo."""
import unittest

from server import config as C
from tests.test_world import CONTENT, make_world, run, tile_center

DT = 1.0 / C.TICK_RATE


class Base(unittest.TestCase):
    def setUp(self):
        self.world = make_world()
        self.a, _ = self.world.login("Ana", "1234", "humano")
        self.b, _ = self.world.login("Beto", "1234", "humano")
        self.c, _ = self.world.login("Caio", "1234", "humano")
        self.enemy = next(iter(self.world.enemies.values()))
        self.world.drain_outbox()

    def op(self, p, **msg):
        self.world.handle_message(p, {"t": "party", **msg})

    def texts(self, p) -> list[str]:
        return [m["text"] for target, m in self.world.drain_outbox() if target == p.id and m["t"] == "sys"]

    def group(self, leader, *others):
        for other in others:
            self.op(leader, op="invite", target=other.name)
            self.op(other, op="accept")
        return self.world.parties.of(leader)

    def place(self, p, tx, ty):
        p.x, p.y = tile_center(tx, ty)


class InviteTests(Base):
    def test_invite_and_accept_creates_party(self):
        self.op(self.a, op="invite", target="beto")
        self.assertEqual(self.world.parties.invite_for(self.b)["from"], "Ana")
        self.assertIsNone(self.world.parties.of(self.a))  # só nasce quando alguém aceita
        self.op(self.b, op="accept")
        party = self.world.parties.of(self.a)
        self.assertIs(party, self.world.parties.of(self.b))
        self.assertEqual(party.leader_id, self.a.id)
        self.assertIsNone(self.world.parties.invite_for(self.b))
        self.assertIn("Beto entrou no grupo.", self.texts(self.a))

    def test_party_goes_to_client_only_when_changed(self):
        self.group(self.a, self.b)
        run(self.world, DT)
        snap = [m for t, m in self.world.drain_outbox() if t == self.b.id and m["t"] == "snap"][-1]["you"]
        self.assertEqual([m["n"] for m in snap["party"]["members"]], ["Ana", "Beto"])
        self.assertEqual(snap["party"]["leader"], self.a.id)
        run(self.world, DT)
        snap = [m for t, m in self.world.drain_outbox() if t == self.b.id and m["t"] == "snap"][-1]["you"]
        self.assertNotIn("party", snap)  # não mudou
        self.op(self.b, op="leave")
        run(self.world, DT)
        snap = [m for t, m in self.world.drain_outbox() if t == self.b.id and m["t"] == "snap"][-1]["you"]
        self.assertIn("party", snap)
        self.assertIsNone(snap["party"])  # saiu: o cliente esconde o painel

    def test_only_leader_invites_and_limits(self):
        self.group(self.a, self.b)
        self.op(self.b, op="invite", target="Caio")
        self.assertIn("Só o líder do grupo pode convidar.", self.texts(self.b))
        self.op(self.a, op="invite", target="Ana")
        self.assertIn("Você não pode se convidar.", self.texts(self.a))
        self.op(self.c, op="invite", target="Beto")
        self.assertIn("Beto já está num grupo.", self.texts(self.c))
        self.op(self.a, op="invite", target="Ninguem")
        self.assertIn("Ninguém online chamado 'Ninguem'.", self.texts(self.a))

    def test_party_is_capped(self):
        old = C.PARTY_MAX
        C.PARTY_MAX = 2
        try:
            self.group(self.a, self.b)
            self.op(self.a, op="invite", target="Caio")
            self.assertIn("O grupo já está cheio (2).", self.texts(self.a))
        finally:
            C.PARTY_MAX = old

    def test_decline_and_expiry(self):
        self.op(self.a, op="invite", target="Beto")
        self.op(self.b, op="decline")
        self.assertIn("Beto recusou o convite para o grupo.", self.texts(self.a))
        self.op(self.a, op="invite", target="Beto")
        run(self.world, C.PARTY_INVITE_SECONDS + 0.1)
        self.op(self.b, op="accept")
        self.assertIsNone(self.world.parties.of(self.b))

    def test_stale_invite_from_someone_who_joined_another_party(self):
        self.op(self.a, op="invite", target="Beto")
        self.group(self.c, self.a)  # Ana entra no grupo do Caio (não é líder)
        self.op(self.b, op="accept")
        self.assertIsNone(self.world.parties.of(self.b))


class MembershipTests(Base):
    def test_leaving_passes_leadership_and_disbands(self):
        party = self.group(self.a, self.b, self.c)
        self.op(self.a, op="leave")
        self.assertEqual(party.leader_id, self.b.id)
        self.assertIn("Ana saiu do grupo. Beto agora lidera o grupo.", self.texts(self.c))
        self.op(self.c, op="leave")
        self.assertIsNone(self.world.parties.of(self.b))  # sozinho não é grupo
        self.assertEqual(self.world.parties.parties, {})

    def test_logout_leaves(self):
        self.group(self.a, self.b)
        self.world.logout(self.b)
        self.assertIsNone(self.world.parties.of(self.a))
        self.assertEqual(self.world.parties.by_player, {})

    def test_kick_and_lead_are_for_the_leader(self):
        party = self.group(self.a, self.b, self.c)
        self.op(self.b, op="kick", target="Caio")
        self.assertIn(self.c.id, party.members)
        self.op(self.a, op="lead", target="Beto")
        self.assertEqual(party.leader_id, self.b.id)
        self.op(self.b, op="kick", target="Caio")
        self.assertNotIn(self.c.id, party.members)
        self.assertIn("Você foi tirado do grupo.", self.texts(self.c))

    def test_party_chat(self):
        self.group(self.a, self.b)
        self.world.handle_message(self.a, {"t": "chat", "ch": "party", "text": "vamos?"})
        out = self.world.drain_outbox()
        got = {target for target, m in out if m["t"] == "chat" and m["text"] == "vamos?" and m["ch"] == "Grupo"}
        self.assertEqual(got, {self.a.id, self.b.id})  # Caio não recebe

    def test_chat_shortcuts(self):
        self.world.handle_message(self.a, {"t": "chat", "text": "/grupo Beto"})
        self.op(self.b, op="accept")
        self.world.drain_outbox()
        self.world.handle_message(self.b, {"t": "chat", "text": "/g oi grupo"})
        self.assertTrue(any(m["t"] == "chat" and m["text"] == "oi grupo" and t == self.a.id
                            for t, m in self.world.drain_outbox()))


class SharingTests(Base):
    def kill_enemy(self, killer):
        self.world._damage(self.enemy, 10_000, killer, CONTENT.skills["golpe"])

    def test_kill_bonus_shared_with_fighting_members_nearby(self):
        self.group(self.a, self.b, self.c)
        ex, ey = self.enemy.x, self.enemy.y
        for p in (self.a, self.b, self.c):
            p.x, p.y = ex - 30, ey
        self.b.last_stat = "spirit"
        self.b.combat_until = self.world.time + 3  # lutou há pouco
        self.c.combat_until = 0  # só olhando
        before = {p.name: {s: st.xp for s, st in p.stats.items()} for p in (self.a, self.b, self.c)}
        self.kill_enemy(self.a)
        kill_xp = CONTENT.enemies[self.enemy.defn.id].kill_xp * (C.ELITE_REWARD if self.enemy.elite else 1)
        gain = lambda p, s: p.stats[s].xp - before[p.name][s]  # noqa: E731
        rate = lambda p, s: p.class_def.train_rate.get(s, 1.0)  # noqa: E731
        self.assertAlmostEqual(gain(self.a, "strength"), kill_xp * rate(self.a, "strength"), places=3)
        self.assertAlmostEqual(gain(self.b, "spirit"), kill_xp * C.PARTY_XP_SHARE * rate(self.b, "spirit"), places=3)
        self.assertEqual(sum(gain(self.c, s) for s in self.c.stats), 0)

    def test_far_member_gets_nothing(self):
        self.group(self.a, self.b)
        self.b.combat_until = self.world.time + 3
        self.b.x, self.b.y = self.enemy.x + C.PARTY_SHARE_RANGE + 10, self.enemy.y
        before = sum(st.xp for st in self.b.stats.values())
        self.kill_enemy(self.a)
        self.assertEqual(sum(st.xp for st in self.b.stats.values()), before)

    def test_kill_counts_for_nearby_members_quests(self):
        self.group(self.a, self.b)
        for p in (self.a, self.b):
            p.quests = {"primeiros_passos": [0]}
            p.x, p.y = self.enemy.x - 30, self.enemy.y
        self.kill_enemy(self.a)
        self.assertEqual(self.b.quests["primeiros_passos"], [1])
        self.assertEqual(self.a.quests["primeiros_passos"], [1])

    def test_party_loot_and_gold_split(self):
        self.group(self.a, self.b)
        w = self.world
        party = w.parties.of(self.a)
        drop = w._drop(None, 9, self.enemy.map_id, self.enemy.x, self.enemy.y, owner=self.a.id, scatter=False)
        drop.party_id = party.id
        self.c.x, self.c.y = drop.x, drop.y  # quem é de fora não pega durante a exclusividade
        w._pickup(self.c)
        self.assertIn(drop.id, w.drops)
        self.c.x += 400
        self.a.x, self.a.y = drop.x + 40, drop.y  # perto, mas sem passar por cima
        self.b.x, self.b.y = drop.x, drop.y
        w._pickup(self.b)
        self.assertNotIn(drop.id, w.drops)
        self.assertEqual((self.a.inventory.gold, self.b.inventory.gold), (4, 5))  # o resto fica com quem pegou

    def test_spawned_loot_belongs_to_party(self):
        self.group(self.a, self.b)
        self.kill_enemy(self.a)
        drops = list(self.world.drops.values())
        self.assertTrue(drops)
        self.assertTrue(all(d.party_id == self.world.parties.of(self.a).id for d in drops))


class FriendlyFireTests(Base):
    def test_party_members_do_not_hurt_each_other_in_pvp(self):
        self.place(self.a, 7, 2)
        self.place(self.b, 7, 3)
        self.assertTrue(self.world._can_hit(self.a, self.b))
        self.group(self.a, self.b)
        self.assertFalse(self.world._can_hit(self.a, self.b))
        self.assertTrue(self.world._can_hit(self.a, self.enemy))

    def test_duel_still_works_inside_party(self):
        self.group(self.a, self.b)
        self.place(self.a, 4, 2)
        self.place(self.b, 4, 3)
        self.world.duels.challenge(self.a, "Beto")
        self.world.duels.accept(self.b)
        run(self.world, C.DUEL_COUNTDOWN + 0.1)
        self.assertTrue(self.world._can_hit(self.a, self.b))


if __name__ == "__main__":
    unittest.main()
