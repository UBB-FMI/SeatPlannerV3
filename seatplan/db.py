from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, created REAL NOT NULL,
    locale TEXT NOT NULL DEFAULT 'en'
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id), csrf TEXT NOT NULL,
    expires REAL NOT NULL, created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires);
CREATE TABLE IF NOT EXISTS login_tokens (
    token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, expires REAL NOT NULL,
    consumed REAL, created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS login_expiry ON login_tokens(expires);
CREATE TABLE IF NOT EXISTS rate_limits (
    key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, pdf_path TEXT NOT NULL,
    sha256 TEXT NOT NULL, pages TEXT NOT NULL DEFAULT '[]',
    zones TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT 'processing'
        CHECK(state IN ('processing', 'draft', 'published', 'failed')),
    revision INTEGER NOT NULL DEFAULT 1, notes TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL, published REAL, parent_id TEXT REFERENCES plans(id)
);
CREATE TABLE IF NOT EXISTS seats (
    id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id),
    section TEXT NOT NULL, row_name TEXT NOT NULL, label TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE(plan_id, section, row_name, label)
);
CREATE INDEX IF NOT EXISTS seats_plan ON seats(plan_id);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, starts_at TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '', plan_id TEXT NOT NULL REFERENCES plans(id),
    status TEXT NOT NULL DEFAULT 'closed' CHECK(status IN ('open','closed')),
    max_per_user INTEGER NOT NULL DEFAULT 4, revision INTEGER NOT NULL DEFAULT 1,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
    user_id TEXT REFERENCES users(id), email TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL, request_key TEXT NOT NULL, request_hash TEXT NOT NULL,
    UNIQUE(user_id, event_id, request_key)
);
CREATE TABLE IF NOT EXISTS allocations (
    event_id TEXT NOT NULL REFERENCES events(id), seat_id TEXT NOT NULL REFERENCES seats(id),
    status TEXT NOT NULL CHECK(status IN ('reserved', 'blocked')),
    booking_id TEXT REFERENCES bookings(id), note TEXT NOT NULL DEFAULT '',
    updated REAL NOT NULL,
    PRIMARY KEY(event_id, seat_id)
);
CREATE INDEX IF NOT EXISTS allocations_booking ON allocations(booking_id);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL,
    object_id TEXT NOT NULL, details TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY, recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','sending','sent','failed')),
    attempts INTEGER NOT NULL DEFAULT 0, available REAL NOT NULL, lease REAL,
    last_error TEXT NOT NULL DEFAULT '', created REAL NOT NULL, sent REAL
);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(status, available);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id),
    kind TEXT NOT NULL CHECK(kind IN ('render','detect')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','done','failed')),
    params TEXT NOT NULL, result TEXT, error TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL, lease REAL
);
CREATE TABLE IF NOT EXISTS heartbeat (
    name TEXT PRIMARY KEY, updated REAL NOT NULL
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 20000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.read() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise RuntimeError("Database is from a newer Seatplan version. Do not downgrade.")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            if version < 2:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
                if "locale" not in columns:
                    connection.execute("ALTER TABLE users ADD COLUMN locale TEXT NOT NULL DEFAULT 'en'")
                connection.execute("PRAGMA user_version = 2")

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialize writers *before* checking availability, including across processes."""
        with self.read() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise


def json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
