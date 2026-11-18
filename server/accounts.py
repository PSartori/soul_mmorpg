"""Contas: entrar, criar conta, escolher/criar personagem e trocar a senha.

Antes de jogar, cada conexão passa por duas telas:
  1. conta:       {"t": "account", "op": "login" | "register", "name", "password"}
  2. personagem:  {"t": "char", "op": "create", "name", "race"} / {"t": "char", "op": "enter", "name"}
                  {"t": "account", "op": "logout"} volta para a tela 1
Dentro do jogo:
  {"t": "account", "op": "password", "old", "new"}   troca a senha
  {"t": "char", "op": "leave"}                       sai do personagem e volta para a lista
O servidor responde {"t": "account", ...} com a lista de personagens, {"t": "error", "msg"}
ou {"t": "account_result", "ok", "text"} (troca de senha).

Proteções:
  - a senha é conferida (PBKDF2, ~0,1 s) numa thread, sem parar o jogo;
  - LoginGuard trava IP e conta depois de C.LOGIN_MAX_FAILS erros de senha;
  - contas novas por IP são limitadas (C.ACCOUNTS_PER_IP por hora; o próprio
    computador — 127.0.0.1 — não tem limite, para testar à vontade);
  - RateLimiter limita as mensagens de cada conexão (main.py derruba quem inunda);
  - bans por conta e por IP são conferidos ao entrar na conta; por personagem, ao entrar nele.
"""
from __future__ import annotations

import asyncio
import hmac
import os
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import config as C
from .admin import format_until
from .db import AccountRecord, hash_password

if TYPE_CHECKING:
    from .entities import Player
    from .world import World

NAME_RE = re.compile(r"[A-Za-z0-9_]{3,16}")
LOCAL_IPS = ("127.0.0.1", "::1", "")
WRONG = "Conta ou senha incorreta."


# ------------------------------------------------------------------ travas


class LoginGuard:
    """Conta erros de senha por chave (IP e conta) e trava quem erra demais."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.fails: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float]:
        times = self.fails.setdefault(key, deque())
        limit = self.clock() - C.LOGIN_LOCK_SECONDS
        while times and times[0] < limit:
            times.popleft()
        return times

    def locked_for(self, *keys: str) -> float:
        """Segundos até destravar (0 = livre)."""
        wait = 0.0
        for key in keys:
            times = self._recent(key)
            if len(times) >= C.LOGIN_MAX_FAILS:
                wait = max(wait, times[0] + C.LOGIN_LOCK_SECONDS - self.clock())
        return wait

    def fail(self, *keys: str) -> None:
        for key in keys:
            self._recent(key).append(self.clock())

    def clear(self, *keys: str) -> None:
        for key in keys:
            self.fails.pop(key, None)


class RateLimiter:
    """Balde de fichas: cada mensagem gasta uma; elas voltam a C.MSG_RATE por segundo."""

    def __init__(self, rate: float = C.MSG_RATE, burst: float = C.MSG_BURST,
                 clock: Callable[[], float] = time.monotonic):
        self.rate, self.burst, self.clock = rate, burst, clock
        self.tokens = burst
        self.last = clock()
        self.dropped = 0  # mensagens descartadas desde a última vez que o balde encheu

    def allow(self) -> bool:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= self.burst:
            self.dropped = 0
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        self.dropped += 1
        return False

    @property
    def flooding(self) -> bool:
        return self.dropped >= C.MSG_FLOOD_KICK


# ------------------------------------------------------------------ sessão


@dataclass
class Session:
    """Uma conexão: de onde vem, em que conta entrou e com que personagem está jogando."""

    ip: str = ""
    account: AccountRecord | None = None
    player: Player | None = None
    limiter: RateLimiter = field(default_factory=RateLimiter)


async def _hash_in_thread(password: str, salt: bytes) -> bytes:
    return await asyncio.to_thread(hash_password, password, salt)


class AccountService:
    def __init__(self, world: World, hasher: Callable[[str, bytes], Awaitable[bytes]] = _hash_in_thread,
                 guard: LoginGuard | None = None):
        self.world = world
        self.db = world.db
        self.hasher = hasher
        self.guard = guard or LoginGuard()

    # ------------------------------------------------------------ entrada

    async def handle(self, s: Session, data: dict) -> list[dict]:
        """Mensagens de conta/personagem. Devolve as respostas para esta conexão."""
        kind, op = data.get("t"), data.get("op")
        if s.player is not None:  # dentro do jogo
            if kind == "account" and op == "password":
                return [await self.change_password(s, data.get("old"), data.get("new"))]
            if kind == "char" and op == "leave":
                self.world.logout(s.player)
                s.player = None
                return [self.account_payload(s)]
            return []
        if s.account is None:
            if kind == "account" and op == "login":
                return [await self.login(s, data.get("name"), data.get("password"))]
            if kind == "account" and op == "register":
                return [await self.register(s, data.get("name"), data.get("password"))]
            return []
        if kind == "account" and op == "logout":
            s.account = None
            return [{"t": "account", "name": None}]
        if kind == "char" and op == "create":
            error = self.world.create_character(s.account.id, str(data.get("name", "")),
                                                data.get("race") if isinstance(data.get("race"), str) else None)
            return [_error(error) if error else self.account_payload(s)]
        if kind == "char" and op == "enter":
            player, error = self.world.enter(s.account.id, str(data.get("name", "")), s.ip)
            if error:
                return [_error(error)]
            s.player = player
            return [self.world.welcome_payload(player)]  # type: ignore[arg-type]
        return []

    # ------------------------------------------------------------ conta

    def _ban_message(self, account: AccountRecord | None, ip: str) -> str | None:
        now = self.world.clock()
        for kind, target, what in (("ip_ban", ip, "Este endereço"), ("account_ban", account.name if account else "",
                                                                       "Esta conta")):
            if not target:
                continue
            ban = self.db.active_sanction(target, kind, now)
            if ban:
                until, reason, _ = ban
                return f"{what} está banido {format_until(until)}." + (f" Motivo: {reason}" if reason else "")
        return None

    async def login(self, s: Session, name: object, password: object) -> dict:
        name = str(name or "").strip()
        password = str(password or "")
        keys = (f"ip:{s.ip}", f"conta:{name.lower()}")
        if wait := self.guard.locked_for(*keys):
            return _error(f"Muitas tentativas erradas. Espere {wait / 60:.0f} min e tente de novo."
                          if wait >= 60 else f"Muitas tentativas erradas. Espere {wait:.0f} s.")
        account = self.db.load_account(name) if NAME_RE.fullmatch(name) else None
        # confere mesmo sem conta (com um sal qualquer), para a resposta demorar o mesmo
        digest = await self.hasher(password, account.salt if account else os.urandom(16))
        if account is None or not hmac.compare_digest(digest, account.pw_hash):
            self.guard.fail(*keys)
            return _error(WRONG)
        self.guard.clear(*keys)
        if ban := self._ban_message(account, s.ip):
            return _error(ban)
        s.account = account
        self.db.touch_account(account.id, s.ip)
        return self.account_payload(s)

    async def register(self, s: Session, name: object, password: object) -> dict:
        name = str(name or "").strip()
        password = str(password or "")
        if not NAME_RE.fullmatch(name):
            return _error("O nome da conta deve ter de 3 a 16 caracteres (letras, números ou _).")
        if len(password) < C.PASSWORD_MIN:
            return _error(f"A senha deve ter pelo menos {C.PASSWORD_MIN} caracteres.")
        if password.lower() == name.lower():
            return _error("A senha não pode ser igual ao nome da conta.")
        if ban := self._ban_message(None, s.ip):
            return _error(ban)
        if s.ip not in LOCAL_IPS and self.db.accounts_created_from(
                s.ip, time.time() - C.ACCOUNTS_PER_IP_WINDOW) >= C.ACCOUNTS_PER_IP:
            return _error("Contas demais criadas deste endereço. Tente mais tarde.")
        if self.db.load_account(name) is not None:
            return _error("Já existe uma conta com esse nome.")
        salt = os.urandom(16)
        digest = await self.hasher(password, salt)
        if self.db.load_account(name) is not None:  # alguém criou enquanto a senha era calculada
            return _error("Já existe uma conta com esse nome.")
        s.account = self.db.create_account(name, digest, salt, s.ip)
        self.db.touch_account(s.account.id, s.ip)
        return self.account_payload(s)

    async def change_password(self, s: Session, old: object, new: object) -> dict:
        account = self.db.account_by_id(s.account.id) if s.account else None
        old, new = str(old or ""), str(new or "")
        if account is None:
            return {"t": "account_result", "ok": False, "text": "Conta não encontrada."}
        keys = (f"ip:{s.ip}", f"conta:{account.name.lower()}")
        if self.guard.locked_for(*keys):
            return {"t": "account_result", "ok": False, "text": "Muitas tentativas erradas. Espere e tente de novo."}
        if not hmac.compare_digest(await self.hasher(old, account.salt), account.pw_hash):
            self.guard.fail(*keys)
            return {"t": "account_result", "ok": False, "text": "A senha atual está errada."}
        if len(new) < C.PASSWORD_MIN:
            return {"t": "account_result", "ok": False,
                    "text": f"A senha nova deve ter pelo menos {C.PASSWORD_MIN} caracteres."}
        if new.lower() == account.name.lower():
            return {"t": "account_result", "ok": False, "text": "A senha não pode ser igual ao nome da conta."}
        salt = os.urandom(16)
        self.db.set_account_password(account.id, await self.hasher(new, salt), salt)
        s.account = self.db.account_by_id(account.id)
        return {"t": "account_result", "ok": True, "text": "Senha trocada."}

    # ------------------------------------------------------------ para o cliente

    def account_payload(self, s: Session) -> dict:
        """A conta e seus personagens (tela de escolher personagem)."""
        assert s.account is not None
        content = self.world.content
        online = {p.name.lower() for p in self.world.players.values()}
        chars = []
        for record in self.db.list_characters(s.account.id):
            data = record.data
            race = content.races.get(data.get("race") or "")
            cls = content.classes.get(data.get("class") or "")
            stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
            levels = [v.get("level", 1) for v in stats.values() if isinstance(v, dict)
                      and isinstance(v.get("level", 1), int)]
            tilemap = self.world.maps.get(data.get("map") or "") or self.world.map
            chars.append({
                "name": record.name,
                "race": race.name if race else "",
                "class": cls.name if cls else "",
                "lv": sum(levels) // len(levels) if levels else 1,
                "map": tilemap.title or tilemap.name,
                "online": record.name.lower() in online,
            })
        return {"t": "account", "name": s.account.name, "chars": chars, "max": C.MAX_CHARACTERS,
                "races": content.selectable_races()}


def _error(text: str) -> dict:
    return {"t": "error", "msg": text}
