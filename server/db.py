"""Persistência em SQLite: contas, personagens, equipe, sanções e organizações.

Uma conta (nome + senha) tem vários personagens. Bancos de antes das contas, em que
cada personagem tinha a própria senha, são migrados ao abrir: cada personagem vira
uma conta com o mesmo nome e a mesma senha.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

PBKDF2_ITERATIONS = 200_000


@dataclass
class CharacterRecord:
    name: str
    data: dict
    account_id: int | None = None


@dataclass
class AccountRecord:
    id: int
    name: str
    pw_hash: bytes
    salt: bytes
    last_ip: str = ""


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)


class Database:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS characters (
                name       TEXT PRIMARY KEY COLLATE NOCASE,
                pw_hash    BLOB NOT NULL,
                salt       BLOB NOT NULL,
                data       TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        # Contas: os personagens apontam para a conta dona (account_id)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
                pw_hash    BLOB NOT NULL,
                salt       BLOB NOT NULL,
                created_at REAL NOT NULL,
                last_login REAL,
                last_ip    TEXT NOT NULL DEFAULT '',
                created_ip TEXT NOT NULL DEFAULT ''
            )
            """
        )
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(characters)")}
        if "account_id" not in columns:
            self.conn.execute("ALTER TABLE characters ADD COLUMN account_id INTEGER")
        self._migrate_to_accounts()
        # Equipe (papéis), sanções (ban/silêncio) e registro de ações da equipe
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS staff (
                character TEXT PRIMARY KEY COLLATE NOCASE,
                role      TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sanctions (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                character TEXT NOT NULL COLLATE NOCASE,
                kind      TEXT NOT NULL,          -- ban | mute
                until     REAL,                   -- NULL = para sempre
                reason    TEXT NOT NULL DEFAULT '',
                by        TEXT NOT NULL,
                at        REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bug_reports (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                at        REAL NOT NULL,
                character TEXT NOT NULL,
                account   TEXT NOT NULL DEFAULT '',
                map       TEXT NOT NULL DEFAULT '',
                x         REAL NOT NULL DEFAULT 0,
                y         REAL NOT NULL DEFAULT 0,
                zone      TEXT NOT NULL DEFAULT '',
                text      TEXT NOT NULL,
                client    TEXT NOT NULL DEFAULT '{}',  -- visão, navegador, erros recentes do cliente
                status    TEXT NOT NULL DEFAULT 'open',  -- open | closed
                closed_by TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS admin_log (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                at      REAL NOT NULL,
                actor   TEXT NOT NULL,
                action  TEXT NOT NULL,
                target  TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT ''
            );
            """
        )
        # Banco de cada conta (itens e moedas guardados) e registro das trocas entre jogadores
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS banks (
                account_id INTEGER PRIMARY KEY,
                data       TEXT NOT NULL DEFAULT '{}',
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS economy (   -- movimentos de moedas somados por dia (server/economy.py)
                day    TEXT NOT NULL,               -- AAAA-MM-DD (horário local)
                kind   TEXT NOT NULL,               -- venda, loja, oficina, treinador, regra, equipe, troca, banco
                detail TEXT NOT NULL DEFAULT '',    -- item, loja, oficina, treinador...
                amount INTEGER NOT NULL DEFAULT 0,  -- moedas
                count  INTEGER NOT NULL DEFAULT 0,  -- quantas vezes
                PRIMARY KEY (day, kind, detail)
            );
            CREATE TABLE IF NOT EXISTS trades (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                at     REAL NOT NULL,
                a      TEXT NOT NULL,
                b      TEXT NOT NULL,
                a_gave TEXT NOT NULL,  -- {"items": {id: qtd}, "gold": n}
                b_gave TEXT NOT NULL
            );
            """
        )
        # Vínculo com organizações fica fora do save: cargos são globais e valem com o ocupante offline
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memberships (
                character TEXT PRIMARY KEY COLLATE NOCASE,
                org       TEXT NOT NULL,
                rank      TEXT NOT NULL,
                seat      INTEGER,
                since     REAL NOT NULL
            )
            """
        )
        self.conn.commit()

    def _migrate_to_accounts(self) -> None:
        """Personagens sem conta (de antes das contas) ganham uma, com o nome e a senha deles."""
        rows = self.conn.execute(
            "SELECT name, pw_hash, salt, created_at FROM characters WHERE account_id IS NULL"
        ).fetchall()
        for name, pw_hash, salt, created_at in rows:
            account_name = name
            n = 2
            while self.conn.execute("SELECT 1 FROM accounts WHERE name = ?", (account_name,)).fetchone():
                account_name = f"{name}{n}"  # não deveria acontecer, mas não pode travar a migração
                n += 1
            cursor = self.conn.execute(
                "INSERT INTO accounts (name, pw_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                (account_name, pw_hash, salt, created_at),
            )
            self.conn.execute("UPDATE characters SET account_id = ? WHERE name = ?", (cursor.lastrowid, name))
        if rows:
            self.conn.commit()

    # ------------------------------------------------------------ contas

    def load_account(self, name: str) -> AccountRecord | None:
        """Busca sem diferenciar maiúsculas/minúsculas."""
        row = self.conn.execute(
            "SELECT id, name, pw_hash, salt, last_ip FROM accounts WHERE name = ?", (name,)
        ).fetchone()
        return AccountRecord(*row) if row else None

    def account_by_id(self, account_id: int) -> AccountRecord | None:
        row = self.conn.execute(
            "SELECT id, name, pw_hash, salt, last_ip FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        return AccountRecord(*row) if row else None

    def create_account(self, name: str, pw_hash: bytes, salt: bytes, ip: str = "") -> AccountRecord:
        """Cria a conta com a senha já transformada (hash_password roda fora do loop do jogo)."""
        cursor = self.conn.execute(
            "INSERT INTO accounts (name, pw_hash, salt, created_at, created_ip) VALUES (?, ?, ?, ?, ?)",
            (name, pw_hash, salt, time.time(), ip),
        )
        self.conn.commit()
        return AccountRecord(id=cursor.lastrowid, name=name, pw_hash=pw_hash, salt=salt)  # type: ignore[arg-type]

    def set_account_password(self, account_id: int, pw_hash: bytes, salt: bytes) -> None:
        self.conn.execute("UPDATE accounts SET pw_hash = ?, salt = ? WHERE id = ?", (pw_hash, salt, account_id))
        self.conn.commit()

    def touch_account(self, account_id: int, ip: str) -> None:
        self.conn.execute("UPDATE accounts SET last_login = ?, last_ip = ? WHERE id = ?",
                          (time.time(), ip, account_id))
        self.conn.commit()

    def accounts_created_from(self, ip: str, since: float) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM accounts WHERE created_ip = ? AND created_at >= ?", (ip, since)
        ).fetchone()
        return row[0]

    def account_of(self, character: str) -> AccountRecord | None:
        row = self.conn.execute("SELECT account_id FROM characters WHERE name = ?", (character,)).fetchone()
        return self.account_by_id(row[0]) if row and row[0] is not None else None

    def list_characters(self, account_id: int) -> list[CharacterRecord]:
        rows = self.conn.execute(
            "SELECT name, data FROM characters WHERE account_id = ? ORDER BY created_at", (account_id,)
        ).fetchall()
        return [CharacterRecord(name=name, data=_json(data), account_id=account_id) for name, data in rows]

    @staticmethod
    def verify_password(account: AccountRecord, password: str) -> bool:
        """Confere a senha aqui mesmo (bloqueia ~0,1 s). O servidor usa hash_password numa thread."""
        return hmac.compare_digest(hash_password(password, account.salt), account.pw_hash)

    # ------------------------------------------------------------ personagens

    def load_character(self, name: str) -> CharacterRecord | None:
        """Busca sem diferenciar maiúsculas/minúsculas."""
        row = self.conn.execute(
            "SELECT name, data, account_id FROM characters WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return CharacterRecord(name=row[0], data=_json(row[1]), account_id=row[2])

    def create_character(self, name: str, password: str | None = None, account_id: int | None = None,
                         data: dict | None = None) -> CharacterRecord:
        """Cria um personagem numa conta.

        Sem account_id, cria também uma conta com o mesmo nome e a senha dada (atalho
        usado pelos testes e ferramentas; o jogo cria a conta antes).
        """
        if account_id is None:
            salt = os.urandom(16)
            account_id = self.create_account(name, hash_password(password or "", salt), salt).id
        now = time.time()
        self.conn.execute(
            "INSERT INTO characters (name, pw_hash, salt, data, created_at, updated_at, account_id) "
            "VALUES (?, x'', x'', ?, ?, ?, ?)",  # pw_hash/salt: colunas antigas, sem uso
            (name, json.dumps(data or {}), now, now, account_id),
        )
        self.conn.commit()
        return CharacterRecord(name=name, data=data or {}, account_id=account_id)

    def save_character(self, name: str, data: dict) -> None:
        self.save_together([(name, data)])

    def save_together(
        self,
        characters: list[tuple[str, dict]],
        banks: list[tuple[int, dict]] = (),  # type: ignore[assignment]
        trade: tuple[str, str, dict, dict] | None = None,
    ) -> None:
        """Salva personagens, bancos e o registro de uma troca de uma vez só (tudo ou nada).

        Assim, se o servidor cair no meio, nunca fica um item nos dois lugares (duplicado)
        nem em nenhum (perdido): depositar no banco e trocar salvam tudo junto.
        """
        now = time.time()
        with self.conn:  # uma transação: commit no fim, rollback se der erro
            for name, data in characters:
                self.conn.execute("UPDATE characters SET data = ?, updated_at = ? WHERE name = ?",
                                  (json.dumps(data), now, name))
            for account_id, data in banks:
                self.conn.execute(
                    "INSERT INTO banks (account_id, data, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(account_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                    (account_id, json.dumps(data), now),
                )
            if trade:
                a, b, a_gave, b_gave = trade
                self.conn.execute("INSERT INTO trades (at, a, b, a_gave, b_gave) VALUES (?, ?, ?, ?, ?)",
                                  (now, a, b, json.dumps(a_gave), json.dumps(b_gave)))

    # ------------------------------------------------------------ banco e trocas

    def load_bank(self, account_id: int) -> dict:
        row = self.conn.execute("SELECT data FROM banks WHERE account_id = ?", (account_id,)).fetchone()
        return _json(row[0]) if row else {}

    def add_economy(self, rows: list[tuple[str, str, str, int, int]]) -> None:
        """Soma (dia, categoria, detalhe, moedas, vezes) ao que já está gravado."""
        with self.conn:
            self.conn.executemany(
                "INSERT INTO economy (day, kind, detail, amount, count) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(day, kind, detail) DO UPDATE SET amount = amount + excluded.amount, "
                "count = count + excluded.count", rows)

    def economy_rows(self, since_day: str) -> list[tuple[str, str, str, int, int]]:
        return self.conn.execute(
            "SELECT day, kind, detail, amount, count FROM economy WHERE day >= ? ORDER BY day", (since_day,)
        ).fetchall()

    def gold_holdings(self) -> tuple[list[tuple[str, int]], list[tuple[str, int, int]]]:
        """Moedas salvas: ([(personagem, moedas na bolsa)], [(conta, id da conta, moedas no banco)])."""
        def gold_of(data: dict) -> int:
            inv = data.get("inventory") if isinstance(data.get("inventory"), dict) else {}
            gold = inv.get("gold") if "inventory" in data else data.get("gold")
            return gold if isinstance(gold, int) and not isinstance(gold, bool) and gold > 0 else 0

        bags = [(name, gold_of(_json(data))) for name, data in self.conn.execute("SELECT name, data FROM characters")]
        banks = [(account or f"#{account_id}", account_id, gold_of(_json(data))) for account_id, account, data in
                 self.conn.execute("SELECT b.account_id, a.name, b.data FROM banks b "
                                   "LEFT JOIN accounts a ON a.id = b.account_id")]
        return bags, banks

    def recent_trades(self, limit: int = 20, name: str | None = None) -> list[tuple]:
        """(at, a, b, a_gave, b_gave), as mais recentes primeiro; com name, só as dele."""
        if name:
            rows = self.conn.execute(
                "SELECT at, a, b, a_gave, b_gave FROM trades WHERE a = ? COLLATE NOCASE OR b = ? COLLATE NOCASE "
                "ORDER BY id DESC LIMIT ?", (name, name, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT at, a, b, a_gave, b_gave FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [(at, a, b, _json(ag), _json(bg)) for at, a, b, ag, bg in rows]

    # ------------------------------------------------------------ organizações

    def load_memberships(self) -> list[tuple[str, str, str, int | None]]:
        return self.conn.execute("SELECT character, org, rank, seat FROM memberships").fetchall()

    def save_membership(self, name: str, org: str, rank: str, seat: int | None) -> None:
        self.conn.execute(
            "INSERT INTO memberships (character, org, rank, seat, since) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(character) DO UPDATE SET org = excluded.org, rank = excluded.rank, seat = excluded.seat",
            (name, org, rank, seat, time.time()),
        )
        self.conn.commit()

    def delete_membership(self, name: str) -> None:
        self.conn.execute("DELETE FROM memberships WHERE character = ?", (name,))
        self.conn.commit()

    # ------------------------------------------------------------ equipe e moderação

    def get_role(self, name: str) -> str | None:
        row = self.conn.execute("SELECT role FROM staff WHERE character = ?", (name,)).fetchone()
        return row[0] if row else None

    def set_role(self, name: str, role: str | None) -> None:
        if role is None:
            self.conn.execute("DELETE FROM staff WHERE character = ?", (name,))
        else:
            self.conn.execute(
                "INSERT INTO staff (character, role) VALUES (?, ?) "
                "ON CONFLICT(character) DO UPDATE SET role = excluded.role",
                (name, role),
            )
        self.conn.commit()

    def list_staff(self) -> list[tuple[str, str]]:
        return self.conn.execute("SELECT character, role FROM staff ORDER BY character").fetchall()

    def add_sanction(self, name: str, kind: str, until: float | None, reason: str, by: str) -> None:
        self.conn.execute(
            "INSERT INTO sanctions (character, kind, until, reason, by, at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, kind, until, reason, by, time.time()),
        )
        self.conn.commit()

    def active_sanction(self, name: str, kind: str, now: float) -> tuple[float | None, str, str] | None:
        """A sanção mais longa ainda valendo: (até quando ou None = para sempre, motivo, por quem)."""
        row = self.conn.execute(
            "SELECT until, reason, by FROM sanctions WHERE character = ? AND kind = ? "
            "AND (until IS NULL OR until > ?) ORDER BY until IS NULL DESC, until DESC LIMIT 1",
            (name, kind, now),
        ).fetchone()
        return (row[0], row[1], row[2]) if row else None

    def lift_sanctions(self, name: str, kind: str) -> int:
        cursor = self.conn.execute("DELETE FROM sanctions WHERE character = ? AND kind = ?", (name, kind))
        self.conn.commit()
        return cursor.rowcount

    def list_active(self, kind: str, now: float) -> list[tuple[str, float | None, str, str]]:
        return self.conn.execute(
            "SELECT character, MAX(COALESCE(until, 1e18)), reason, by FROM sanctions "
            "WHERE kind = ? AND (until IS NULL OR until > ?) GROUP BY character ORDER BY character",
            (kind, now),
        ).fetchall()

    def log_admin(self, actor: str, action: str, target: str = "", details: str = "") -> None:
        self.conn.execute(
            "INSERT INTO admin_log (at, actor, action, target, details) VALUES (?, ?, ?, ?, ?)",
            (time.time(), actor, action, target, details),
        )
        self.conn.commit()

    def recent_log(self, limit: int = 50) -> list[tuple[float, str, str, str, str]]:
        return self.conn.execute(
            "SELECT at, actor, action, target, details FROM admin_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    # ------------------------------------------------------------ relatos de bug

    def add_bug_report(self, character: str, account: str, map_id: str, x: float, y: float, zone: str,
                       text: str, client: dict) -> int:
        cursor = self.conn.execute(
            "INSERT INTO bug_reports (at, character, account, map, x, y, zone, text, client) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), character, account, map_id, x, y, zone, text, json.dumps(client, ensure_ascii=False)),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def list_bug_reports(self, include_closed: bool = False, limit: int = 50) -> list[dict]:
        """Os mais recentes primeiro."""
        rows = self.conn.execute(
            "SELECT id, at, character, account, map, x, y, zone, text, client, status, closed_by FROM bug_reports "
            + ("" if include_closed else "WHERE status = 'open' ") + "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        keys = ("id", "at", "character", "account", "map", "x", "y", "zone", "text", "client", "status", "closed_by")
        reports = [dict(zip(keys, row)) for row in rows]
        for r in reports:
            r["client"] = _json(r["client"])
        return reports

    def get_bug_report(self, report_id: int) -> dict | None:
        row = self.conn.execute("SELECT map, x, y FROM bug_reports WHERE id = ?", (report_id,)).fetchone()
        return {"map": row[0], "x": row[1], "y": row[2]} if row else None

    def close_bug_report(self, report_id: int, by: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE bug_reports SET status = 'closed', closed_by = ? WHERE id = ? AND status = 'open'", (by, report_id)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def backup(self, dest_dir: str | Path, keep: int) -> Path:
        """Copia o banco (com o servidor rodando) e apaga as cópias mais antigas além de `keep`."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"game-{time.strftime('%Y%m%d-%H%M%S')}.db"
        target = sqlite3.connect(str(path))
        try:
            self.conn.backup(target)
        finally:
            target.close()
        for old in sorted(dest_dir.glob("game-*.db"))[:-keep]:
            old.unlink()
        return path

    def close(self) -> None:
        self.conn.close()


def _json(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
