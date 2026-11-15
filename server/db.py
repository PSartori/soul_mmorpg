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
