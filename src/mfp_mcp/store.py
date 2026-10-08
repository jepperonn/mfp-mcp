"""SQLite storage: OAuth state, encrypted MyFitnessPal cookies, My Foods, metadata."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_pending (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_codes (
    code_hash TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_tokens (
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL,              -- 'access' | 'refresh'
    client_id TEXT NOT NULL,
    data TEXT NOT NULL,
    expires_at REAL,
    family TEXT NOT NULL             -- links an access token to its refresh token
);
CREATE TABLE IF NOT EXISTS secrets (
    name TEXT PRIMARY KEY,
    value BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS my_foods (
    key TEXT PRIMARY KEY,            -- normalised name
    name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    serving TEXT NOT NULL DEFAULT '1 serving',
    kcal REAL NOT NULL,
    protein REAL NOT NULL,
    carbs REAL NOT NULL,
    fat REAL NOT NULL,
    mfp_food_id TEXT,
    mfp_food_version TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def token_hash(token: str) -> str:
    """Tokens and codes are stored hashed, so a leaked database cannot be replayed."""
    return hashlib.sha256(token.encode()).hexdigest()


def norm(name: str) -> str:
    return " ".join(name.casefold().split())


class Store:
    def __init__(self, path: str | Path, secret_key: str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest()))
        with self._lock:
            if self.path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)

    def _q(self, sql: str, args: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # ---- meta -------------------------------------------------------
    def meta_get(self, key: str, default: str | None = None) -> str | None:
        rows = self._q("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def meta_set(self, key: str, value: str | None) -> None:
        self._q("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # ---- encrypted secrets (MyFitnessPal cookies) --------------------
    def secret_set(self, name: str, value: Any) -> None:
        blob = self._fernet.encrypt(json.dumps(value).encode())
        self._q("INSERT INTO secrets(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (name, blob))

    def secret_get(self, name: str) -> Any | None:
        rows = self._q("SELECT value FROM secrets WHERE name=?", (name,))
        if not rows:
            return None
        try:
            return json.loads(self._fernet.decrypt(rows[0]["value"]))
        except InvalidToken:
            return None  # SECRET_KEY changed: treat as "not connected"

    def secret_delete(self, name: str) -> None:
        self._q("DELETE FROM secrets WHERE name=?", (name,))

    # ---- OAuth -------------------------------------------------------
    def client_put(self, client_id: str, data: str) -> None:
        self._q(
            "INSERT INTO oauth_clients(client_id,data) VALUES(?,?) ON CONFLICT(client_id) DO UPDATE SET data=excluded.data",
            (client_id, data),
        )

    def client_get(self, client_id: str) -> str | None:
        rows = self._q("SELECT data FROM oauth_clients WHERE client_id=?", (client_id,))
        return rows[0]["data"] if rows else None

    def pending_put(self, pid: str, data: str, ttl: float) -> None:
        self._q("DELETE FROM oauth_pending WHERE expires_at < ?", (time.time(),))
        self._q("INSERT OR REPLACE INTO oauth_pending(id,data,expires_at) VALUES(?,?,?)", (pid, data, time.time() + ttl))

    def pending_get(self, pid: str) -> str | None:
        rows = self._q("SELECT data FROM oauth_pending WHERE id=? AND expires_at>=?", (pid, time.time()))
        return rows[0]["data"] if rows else None

    def pending_delete(self, pid: str) -> None:
        self._q("DELETE FROM oauth_pending WHERE id=?", (pid,))

    def code_put(self, code: str, data: str, ttl: float) -> None:
        self._q("DELETE FROM oauth_codes WHERE expires_at < ?", (time.time(),))
        self._q("INSERT OR REPLACE INTO oauth_codes(code_hash,data,expires_at) VALUES(?,?,?)", (token_hash(code), data, time.time() + ttl))

    def code_get(self, code: str) -> str | None:
        rows = self._q("SELECT data FROM oauth_codes WHERE code_hash=? AND expires_at>=?", (token_hash(code), time.time()))
        return rows[0]["data"] if rows else None

    def code_delete(self, code: str) -> None:
        self._q("DELETE FROM oauth_codes WHERE code_hash=?", (token_hash(code),))

    def token_put(self, token: str, kind: str, client_id: str, data: str, expires_at: float | None, family: str) -> None:
        self._q(
            "INSERT OR REPLACE INTO oauth_tokens(token_hash,kind,client_id,data,expires_at,family) VALUES(?,?,?,?,?,?)",
            (token_hash(token), kind, client_id, data, expires_at, family),
        )

    def token_get(self, token: str, kind: str) -> sqlite3.Row | None:
        rows = self._q(
            "SELECT * FROM oauth_tokens WHERE token_hash=? AND kind=? AND (expires_at IS NULL OR expires_at>=?)",
            (token_hash(token), kind, time.time()),
        )
        return rows[0] if rows else None

    def token_delete_family(self, family: str) -> None:
        self._q("DELETE FROM oauth_tokens WHERE family=?", (family,))

    def token_purge_expired(self) -> None:
        self._q("DELETE FROM oauth_tokens WHERE expires_at IS NOT NULL AND expires_at < ?", (time.time(),))

    # ---- My Foods ----------------------------------------------------
    def food_put(self, f: dict[str, Any]) -> None:
        self._q(
            """INSERT INTO my_foods(key,name,aliases,serving,kcal,protein,carbs,fat,mfp_food_id,mfp_food_version)
               VALUES(:key,:name,:aliases,:serving,:kcal,:protein,:carbs,:fat,:mfp_food_id,:mfp_food_version)
               ON CONFLICT(key) DO UPDATE SET name=excluded.name, aliases=excluded.aliases, serving=excluded.serving,
                 kcal=excluded.kcal, protein=excluded.protein, carbs=excluded.carbs, fat=excluded.fat,
                 mfp_food_id=excluded.mfp_food_id, mfp_food_version=excluded.mfp_food_version""",
            {**f, "key": norm(f["name"]), "aliases": json.dumps(f.get("aliases", []), ensure_ascii=False)},
        )

    def food_rows(self) -> list[dict[str, Any]]:
        out = []
        for r in self._q("SELECT * FROM my_foods ORDER BY name COLLATE NOCASE"):
            d = dict(r)
            d["aliases"] = json.loads(d["aliases"])
            out.append(d)
        return out

    def food_delete(self, key: str) -> bool:
        with self._lock:
            return self._db.execute("DELETE FROM my_foods WHERE key=?", (key,)).rowcount > 0
