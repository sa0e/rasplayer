"""SQLite storage: card mappings and runtime settings.

Connections are short-lived (one per operation) so the reader thread and
Flask request threads can share the database safely. WAL mode keeps
concurrent reads cheap.
"""

import sqlite3
from contextlib import contextmanager

from . import config

MODES = ("single", "random1", "random3", "stop")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    card_id    TEXT PRIMARY KEY,
    label      TEXT NOT NULL DEFAULT '',
    mode       TEXT NOT NULL CHECK (mode IN ('single','random1','random3','stop')),
    target     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@contextmanager
def connect(db_path=None):
    """Short-lived connection: commits on success, always closes."""
    conn = sqlite3.connect(db_path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init(db_path=None):
    """Create tables and seed default settings if missing."""
    with connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        for key, value in config.SETTINGS_DEFAULTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )


# --- cards ---

def list_cards(db_path=None):
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM cards ORDER BY label COLLATE NOCASE, card_id"
        ).fetchall()


def get_card(card_id, db_path=None):
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM cards WHERE card_id = ?", (card_id,)
        ).fetchone()


def upsert_card(card_id, label, mode, target, db_path=None):
    if mode not in MODES:
        raise ValueError(f"invalid mode: {mode!r}")
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO cards (card_id, label, mode, target)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(card_id) DO UPDATE SET
                label = excluded.label,
                mode = excluded.mode,
                target = excluded.target,
                updated_at = datetime('now')
            """,
            (card_id, label, mode, target),
        )


def delete_card(card_id, db_path=None):
    with connect(db_path) as conn:
        conn.execute("DELETE FROM cards WHERE card_id = ?", (card_id,))


# --- settings ---

def get_setting(key, db_path=None):
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else config.SETTINGS_DEFAULTS.get(key, "")


def get_settings(db_path=None):
    values = dict(config.SETTINGS_DEFAULTS)
    with connect(db_path) as conn:
        for row in conn.execute("SELECT key, value FROM settings"):
            values[row["key"]] = row["value"]
    return values


def set_setting(key, value, db_path=None):
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
