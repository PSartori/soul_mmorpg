"""Administração: papéis da equipe (data/roles.yaml), sanções e registro de ações.

A equipe age pela janela "Administração" do cliente, que manda mensagens
{"t": "admin", "op": ..., ...}. Cada operação confere a permissão do papel e a
hierarquia (ninguém age sobre alguém da equipe com nível igual ou maior) e fica
registrada na tabela admin_log.

Bans e silêncios ficam no banco (tabela sanctions): valem depois de sair e
voltar, e expiram sozinhos. Um ban pode ser do personagem (kind "ban"), da conta
inteira ("account_ban", todos os personagens dela) ou da conta e do IP ("ip_ban":
ninguém entra nem cria conta daquele endereço). Papéis ficam na tabela staff; o
primeiro papel se dá pelo terminal (./admin.sh role Nome admin).
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from typing import TYPE_CHECKING, Any

from . import config as C
from .content import RoleDef
from .db import hash_password

if TYPE_CHECKING:
    from .entities import Player
    from .world import World

log = logging.getLogger("admin")

MAX_SANCTION_SECONDS = 365 * 24 * 3600
BAN_KINDS = {"ban": "personagem", "account_ban": "conta", "ip_ban": "IP"}
LOCAL_IPS = ("127.0.0.1", "::1", "")


def temporary_password() -> str:
    """Senha temporária fácil de ditar: 10 letras e números, sem os que se confundem (0/O, 1/l)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))
FOREVER = 1e17  # o banco devolve "para sempre" como um número enorme em listagens


def format_until(until: float | None) -> str:
    if until is None or until >= FOREVER:
        return "para sempre"
    return "até " + time.strftime("%d/%m/%Y %H:%M", time.localtime(until))


class AdminSystem:
    def __init__(self, world: World):
        self.world = world
        self.db = world.db
        self.roles = world.content.roles

    # ------------------------------------------------------------ consultas

    def role_of_name(self, name: str) -> RoleDef | None:
        online = self._online(name)
        if online is not None:
            return online.staff_role
        return self.roles.get(self.db.get_role(name) or "")

    def load_player(self, p: Player) -> None:
        """Ao entrar: papel da equipe e silêncio em vigor."""
        p.staff_role = self.roles.get(self.db.get_role(p.name) or "")
        mute = self.db.active_sanction(p.name, "mute", self.world.clock())
        p.muted_until = mute[0] if mute else None
        p.muted = mute is not None

    def ban_message(self, name: str) -> str | None:
        ban = self.db.active_sanction(name, "ban", self.world.clock())
        if ban is None:
            return None
        until, reason, _ = ban
        return f"Este personagem está banido {format_until(until)}." + (f" Motivo: {reason}" if reason else "")

    def mute_message(self, p: Player) -> str | None:
        """Texto para quem tenta falar silenciado (e libera quem já cumpriu o tempo)."""
        if not p.muted:
            return None
        if p.muted_until is not None and p.muted_until <= self.world.clock():
            p.muted, p.muted_until = False, None
            return None
        return f"Você está silenciado {format_until(p.muted_until)}."

    def _online(self, name: str) -> Player | None:
        return next((o for o in self.world.players.values() if o.name.lower() == name.lower()), None)

    # ------------------------------------------------------------ entrada

    def handle(self, p: Player, data: dict) -> None:
        role = p.staff_role
        if role is None:
            return  # não é da equipe: ignora em silêncio
        op = data.get("op")
        ops = {
            "refresh": (None, self._refresh),
            "kick": ("kick", self._kick),
            "mute": ("mute", self._mute),
            "unmute": ("mute", self._unmute),
            "ban": ("ban", self._ban),
            "unban": ("ban", self._unban),
            "announce": ("announce", self._announce),
            "goto": ("teleport", self._goto),
            "bring": ("teleport", self._bring),
            "give_item": ("give", self._give_item),
            "give_gold": ("give", self._give_gold),
            "set_role": ("set_role", self._set_role),
            "reload": ("reload", self._reload),
            "reset_password": ("account", self._reset_password),
        }
        if op not in ops:
            return
        permission, action = ops[op]
        if permission and not role.can(permission):
            self._reply(p, False, "Seu papel não permite isso.")
            return
        try:
            action(p, data)
        except (TypeError, ValueError, KeyError):
            self._reply(p, False, "Pedido inválido.")
            return
        if op != "refresh":
            self._refresh(p, data)

    def _reply(self, p: Player, ok: bool, text: str) -> None:
        self.world._outbox.append((p.id, {"t": "admin_result", "ok": ok, "text": text}))

    def _record(self, p: Player, action: str, target: str = "", details: str = "") -> None:
        self.db.log_admin(p.name, action, target, details)
        log.info("[equipe] %s: %s %s %s", p.name, action, target, details)

    # ------------------------------------------------------------ alvo e hierarquia

    def _target(self, p: Player, data: dict, online: bool) -> tuple[str, Player | None] | None:
        """Resolve o alvo e confere hierarquia. Devolve (nome, jogador online ou None)."""
        name = str(data.get("target", "")).strip()
        target = self._online(name)
        if not name:
            self._reply(p, False, "Escolha um jogador.")
            return None
        if online and target is None:
            self._reply(p, False, f"{name} não está online.")
            return None
        if target is None and self.db.load_character(name) is None:
            self._reply(p, False, f"Não existe personagem chamado {name}.")
            return None
        real_name = target.name if target else self.db.load_character(name).name  # type: ignore[union-attr]
        if real_name.lower() == p.name.lower():
            self._reply(p, False, "Você não pode fazer isso consigo mesmo.")
            return None
        other_role = self.role_of_name(real_name)
        if other_role and other_role.level >= p.staff_role.level:  # type: ignore[union-attr]
            self._reply(p, False, f"{real_name} é {other_role.name}: não está abaixo de você.")
            return None
        return real_name, target

    @staticmethod
    def _duration(data: dict, allow_forever: bool) -> float | None:
        seconds = data.get("seconds")
        if seconds is None and allow_forever:
            return None
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= MAX_SANCTION_SECONDS:
            raise ValueError("duração inválida")
        return float(seconds)

    @staticmethod
    def _reason(data: dict) -> str:
        return str(data.get("reason", "")).strip()[:200]

    # ------------------------------------------------------------ ações

    def _refresh(self, p: Player, data: dict) -> None:
        role = p.staff_role
        assert role is not None
        now = self.world.clock()
        players = []
        for o in sorted(self.world.players.values(), key=lambda o: o.name.lower()):
            membership = self.world.orgs.of(o)
            tilemap = self.world._map(o)
            players.append({
                "name": o.name,
                "map": tilemap.title or tilemap.name,
                "race": o.race_def.name if o.race_def else "",
                "class": o.class_def.name if o.class_def else "",
                "org": (f"{self.world.content.orgs[membership.org].name} · {self.world.orgs.title(membership)}"
                        if membership else ""),
                "role": o.staff_role.id if o.staff_role else None,
                "account": (self.db.account_by_id(o.account_id).name  # type: ignore[union-attr]
                            if role.can("ban") and o.account_id is not None and self.db.account_by_id(o.account_id)
                            else None),
                "muted": bool(self.mute_message(o)),
            })
        payload: dict[str, Any] = {"t": "admin_data", "role": role.id, "players": players,
                                   "items": sorted(self.world.content.items),
                                   "roles": [r.id for r in self.roles.values() if r.level < role.level]}
        if role.can("ban"):
            payload["bans"] = [{"name": n, "kind": kind, "what": label, "until": format_until(u), "reason": r, "by": b}
                               for kind, label in BAN_KINDS.items()
                               for n, u, r, b in self.db.list_active(kind, now)]
        if role.can("log"):
            payload["log"] = [{"at": time.strftime("%d/%m %H:%M", time.localtime(at)), "actor": a, "action": act,
                               "target": t, "details": d} for at, a, act, t, d in self.db.recent_log(40)]
        self.world._outbox.append((p.id, payload))

    def _kick(self, p: Player, data: dict) -> None:
        found = self._target(p, data, online=True)
        if not found:
            return
        name, target = found
        reason = self._reason(data)
        self._disconnect(target, f"Você foi expulso por {p.name}." + (f" Motivo: {reason}" if reason else ""))  # type: ignore[arg-type]
        self._record(p, "expulsou", name, reason)
        self._reply(p, True, f"{name} foi expulso.")

    def _disconnect(self, target: Player, text: str) -> None:
        self.world._outbox.append((target.id, {"t": "kick", "text": text}))

    def _mute(self, p: Player, data: dict) -> None:
        found = self._target(p, data, online=False)
        if not found:
            return
        name, target = found
        seconds = self._duration(data, allow_forever=False)
        until = self.world.clock() + seconds  # type: ignore[operator]
        reason = self._reason(data)
        self.db.add_sanction(name, "mute", until, reason, p.name)
        if target:
            target.muted, target.muted_until = True, until
            self.world._private(target, f"Você foi silenciado {format_until(until)}." + (f" Motivo: {reason}" if reason else ""))
        self._record(p, "silenciou", name, format_until(until) + (f"; {reason}" if reason else ""))
        self._reply(p, True, f"{name} silenciado {format_until(until)}.")

    def _unmute(self, p: Player, data: dict) -> None:
        found = self._target(p, data, online=False)
        if not found:
            return
        name, target = found
        self.db.lift_sanctions(name, "mute")
        if target:
            target.muted, target.muted_until = False, None
            self.world._private(target, "Você pode falar de novo.")
        self._record(p, "tirou o silêncio de", name)
        self._reply(p, True, f"{name} pode falar de novo.")

    def _ban(self, p: Player, data: dict) -> None:
        """Bane o personagem; com scope "account", a conta toda; com "ip", a conta e o IP dela."""
        scope = data.get("scope", "character")
        if scope not in ("character", "account", "ip"):
            raise ValueError("alcance inválido")
        found = self._target(p, data, online=False)
        if not found:
            return
        name, target = found
        seconds = self._duration(data, allow_forever=True)
        until = None if seconds is None else self.world.clock() + seconds
        reason = self._reason(data)
        account = self.db.account_of(name)
        ip = target.ip if target and target.ip else (account.last_ip if account else "")
        if scope != "character" and account is None:
            self._reply(p, False, f"{name} não tem conta (personagem antigo?).")
            return
        if scope == "ip" and ip in LOCAL_IPS:
            self._reply(p, False, f"{name} está jogando deste computador (IP local): não dá para banir o IP.")
            return
        if scope == "character":
            self.db.add_sanction(name, "ban", until, reason, p.name)
            what, hit = name, [target] if target else []
        else:
            self.db.add_sanction(account.name, "account_ban", until, reason, p.name)  # type: ignore[union-attr]
            hit = [o for o in self.world.players.values() if o.account_id == account.id]  # type: ignore[union-attr]
            what = f"a conta de {name} ({account.name})"  # type: ignore[union-attr]
            if scope == "ip":
                self.db.add_sanction(ip, "ip_ban", until, reason, p.name)
                hit += [o for o in self.world.players.values() if o.ip == ip and o not in hit]
                what += " e o IP dela"
        for o in hit:
            self._disconnect(o, f"Você foi banido {format_until(until)}." + (f" Motivo: {reason}" if reason else ""))
        self._record(p, "baniu", what, format_until(until) + (f"; {reason}" if reason else ""))
        self._reply(p, True, f"Banido {format_until(until)}: {what}.")

    def _unban(self, p: Player, data: dict) -> None:
        name = str(data.get("target", "")).strip()
        kind = data.get("kind", "ban")
        if kind not in BAN_KINDS:
            raise ValueError("tipo de ban inválido")
        if not name or self.db.lift_sanctions(name, kind) == 0:
            self._reply(p, False, f"{name or 'Ninguém'} não está banido ({BAN_KINDS[kind]}).")
            return
        self._record(p, "desbaniu", name, BAN_KINDS[kind])
        self._reply(p, True, f"{name} foi desbanido ({BAN_KINDS[kind]}).")

    def _reset_password(self, p: Player, data: dict) -> None:
        """Gera uma senha temporária para a conta do personagem (quem esqueceu a senha).

        A pessoa entra com ela e troca na janela Conta. Bloqueia ~0,1 s (é raro).
        """
        found = self._target(p, data, online=False)
        if not found:
            return
        name, _ = found
        account = self.db.account_of(name)
        if account is None:
            self._reply(p, False, f"{name} não tem conta.")
            return
        password = temporary_password()
        salt = os.urandom(16)
        self.db.set_account_password(account.id, hash_password(password, salt), salt)
        self._record(p, "gerou senha temporária para", account.name, f"conta de {name}")
        self._reply(p, True, f"Senha temporária da conta {account.name}: {password} — passe só para a dona "
                             "da conta; ela deve trocar a senha ao entrar (botão Conta).")

    def _announce(self, p: Player, data: dict) -> None:
        text = str(data.get("text", "")).strip()[: C.CHAT_MAX_LEN]
        if not text:
            self._reply(p, False, "Escreva o anúncio.")
            return
        self.world._outbox.append((None, {"t": "announce", "text": text, "by": p.name}))
        self._record(p, "anunciou", "", text)
        self._reply(p, True, "Anúncio enviado.")

    def _goto(self, p: Player, data: dict) -> None:
        name = str(data.get("target", "")).strip()
        target = self._online(name)
        if target is None or target is p:
            self._reply(p, False, f"{name} não está online.")
            return
        self.world.teleport(p, target.map_id, target.x, target.y)
        self._record(p, "foi até", target.name)
        self._reply(p, True, f"Você foi até {target.name}.")

    def _bring(self, p: Player, data: dict) -> None:
        found = self._target(p, data, online=True)
        if not found:
            return
        name, target = found
        self.world.teleport(target, p.map_id, p.x, p.y)  # type: ignore[arg-type]
        self.world._private(target, f"{p.name} (equipe) trouxe você.")  # type: ignore[arg-type]
        self._record(p, "trouxe", name)
        self._reply(p, True, f"{name} foi trazido até você.")

    def _give_item(self, p: Player, data: dict) -> None:
        name = str(data.get("target", "")).strip()
        target = self._online(name)
        item = self.world.content.items.get(str(data.get("item", "")))
        qty = data.get("qty", 1)
        if target is None:
            self._reply(p, False, f"{name} não está online.")
        elif item is None:
            self._reply(p, False, "Item desconhecido.")
        elif isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= 999:
            self._reply(p, False, "Quantidade deve ser de 1 a 999.")
        else:
            self.world._give_item(target, item, qty)
            self.world._private(target, f"Você recebeu {qty}× {item.name} da equipe.")
            self._record(p, "deu item a", target.name, f"{qty}× {item.id}")
            self._reply(p, True, f"{qty}× {item.name} para {target.name}.")

    def _give_gold(self, p: Player, data: dict) -> None:
        name = str(data.get("target", "")).strip()
        target = self._online(name)
        amount = data.get("amount")
        if target is None:
            self._reply(p, False, f"{name} não está online.")
        elif isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 1_000_000:
            self._reply(p, False, "Quantidade de moedas inválida.")
        else:
            target.inventory.add_gold(amount)
            self.world._private(target, f"Você recebeu {amount} moedas da equipe.")
            self._record(p, "deu moedas a", target.name, str(amount))
            self._reply(p, True, f"{amount} moedas para {target.name}.")

    def _reload(self, p: Player, data: dict) -> None:
        ok, text = self.world.reload_from_disk()
        self._record(p, "recarregou o conteúdo" if ok else "tentou recarregar o conteúdo", "", text[:300])
        if ok:
            self.world._system("O conteúdo do jogo foi atualizado pela equipe.")
            self._reply(p, True, "Conteúdo recarregado: " + text)
        else:
            self._reply(p, False, "Nada mudou. Erro no conteúdo: " + text)

    def _set_role(self, p: Player, data: dict) -> None:
        found = self._target(p, data, online=False)
        if not found:
            return
        name, target = found
        role_id = data.get("role")
        new_role = None if role_id in (None, "") else self.roles.get(str(role_id))
        if role_id not in (None, "") and new_role is None:
            self._reply(p, False, "Papel desconhecido.")
            return
        if new_role and new_role.level >= p.staff_role.level:  # type: ignore[union-attr]
            self._reply(p, False, "Você só dá papéis abaixo do seu.")
            return
        self.db.set_role(name, new_role.id if new_role else None)
        if target:
            target.staff_role = new_role
            self.world._private(target, f"Agora você é {new_role.name} da equipe." if new_role
                                else "Você não faz mais parte da equipe.")
        self._record(p, "mudou o papel de", name, new_role.id if new_role else "nenhum")
        self._reply(p, True, f"{name}: {new_role.name if new_role else 'sem papel'}.")
