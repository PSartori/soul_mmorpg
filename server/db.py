"""Persistência de personagens em SQLite."""
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
    pw_hash: bytes
    salt: bytes
    data: dict


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

    def load_character(self, name: str) -> CharacterRecord | None:
        """Busca sem diferenciar maiúsculas/minúsculas."""
        row = self.conn.execute(
            "SELECT name, pw_hash, salt, data FROM characters WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row[3])
        except ValueError:
            data = {}
        return CharacterRecord(name=row[0], pw_hash=row[1], salt=row[2], data=data)

    def create_character(self, name: str, password: str) -> CharacterRecord:
        salt = os.urandom(16)
        pw_hash = hash_password(password, salt)
        now = time.time()
        self.conn.execute(
            "INSERT INTO characters (name, pw_hash, salt, data, created_at, updated_at) VALUES (?, ?, ?, '{}', ?, ?)",
            (name, pw_hash, salt, now, now),
        )
        self.conn.commit()
        return CharacterRecord(name=name, pw_hash=pw_hash, salt=salt, data={})

    @staticmethod
    def verify_password(record: CharacterRecord, password: str) -> bool:
        return hmac.compare_digest(hash_password(password, record.salt), record.pw_hash)

    def save_character(self, name: str, data: dict) -> None:
        self.conn.execute(
            "UPDATE characters SET data = ?, updated_at = ? WHERE name = ?",
            (json.dumps(data), time.time(), name),
        )
        self.conn.commit()

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
