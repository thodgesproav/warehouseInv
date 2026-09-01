from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import DEFAULT_MAPPING, settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','standard')),
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL, user_id INTEGER, username TEXT NOT NULL,
  item_id TEXT NOT NULL, item_name TEXT NOT NULL, quantity INTEGER NOT NULL,
  old_soh INTEGER, new_soh INTEGER, transaction_type TEXT NOT NULL,
  success INTEGER NOT NULL, sync_status TEXT NOT NULL, error TEXT,
  evidence_path TEXT, evidence_captured_at TEXT, evidence_error TEXT,
  evidence_width INTEGER, evidence_height INTEGER
);
CREATE TABLE IF NOT EXISTS login_sessions (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  password_tag TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS login_sessions_user ON login_sessions(user_id);
CREATE TABLE IF NOT EXISTS procurement_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK(quantity>0), status TEXT NOT NULL DEFAULT 'ordered',
  created_at TEXT NOT NULL, received_at TEXT, created_by INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS procurement_open_item ON procurement_orders(item_id) WHERE status='ordered';
CREATE TABLE IF NOT EXISTS procurement_batches (
  batch_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS item_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_requested TEXT NOT NULL, manufacturer_model TEXT, quantity INTEGER NOT NULL,
  notes TEXT, requested_by INTEGER NOT NULL, requested_by_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'new', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS image_metadata (
  item_id TEXT PRIMARY KEY, local_path TEXT, source_url TEXT,
  confidence REAL, review_required INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
  status TEXT NOT NULL, message TEXT, product_count INTEGER
);
CREATE TABLE IF NOT EXISTS local_inventory (
  item_id TEXT PRIMARY KEY, raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS excel_snapshot (
  item_id TEXT PRIMARY KEY, raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inventory_outbox (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  operation_id TEXT NOT NULL UNIQUE, item_id TEXT NOT NULL, kind TEXT NOT NULL,
  base_json TEXT, patch_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
  message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS inventory_outbox_pending ON inventory_outbox(state,sequence);
CREATE TABLE IF NOT EXISTS inventory_sync_meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stock_watches (
  user_id INTEGER NOT NULL, item_id TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, PRIMARY KEY(user_id,item_id)
);
CREATE TABLE IF NOT EXISTS notification_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE NOT NULL,
  recipient_kind TEXT NOT NULL, user_id INTEGER, subject TEXT NOT NULL, html_body TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'queued', error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.database_path, timeout=30, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


@contextmanager
def db_session() -> Iterator[sqlite3.Connection]:
    db = connect()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialise(password_hash: str | None = None) -> None:
    with db_session() as db:
        db.executescript(SCHEMA)
        db.execute('BEGIN IMMEDIATE')
        if 'sync_operation_id' not in {row[1] for row in db.execute('PRAGMA table_info(transactions)')}:
            db.execute('ALTER TABLE transactions ADD COLUMN sync_operation_id TEXT')
        for table, column, definition in (
            ('users', 'email', "TEXT NOT NULL DEFAULT ''"),
            ('users', 'email_notifications', 'INTEGER NOT NULL DEFAULT 0'),
            ('item_requests', 'notify_available', 'INTEGER NOT NULL DEFAULT 0'),
            ('item_requests', 'inventory_item_id', 'TEXT'),
            ('item_requests', 'manufacturer', "TEXT NOT NULL DEFAULT ''"),
            ('item_requests', 'notify_user_id', 'INTEGER'),
            ('transactions', 'excel_exported', 'INTEGER NOT NULL DEFAULT 0'),
            ('transactions', 'evidence_path', 'TEXT'),
            ('transactions', 'evidence_captured_at', 'TEXT'),
            ('transactions', 'evidence_error', 'TEXT'),
            ('transactions', 'evidence_width', 'INTEGER'),
            ('transactions', 'evidence_height', 'INTEGER'),
        ):
            if column not in {row[1] for row in db.execute(f'PRAGMA table_info({table})')}:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        if 'access_level' not in {row[1] for row in db.execute('PRAGMA table_info(users)')}:
            # Preserve the existing role constraint and all user IDs/passwords.
            db.execute("ALTER TABLE users ADD COLUMN access_level TEXT NOT NULL DEFAULT 'warehouse_admin'")
            candidate = db.execute("SELECT id FROM users WHERE role='admin' AND disabled=0 ORDER BY (username=?) DESC,id LIMIT 1", (settings.admin_username,)).fetchone()
            if candidate:
                db.execute("UPDATE users SET access_level='superadmin' WHERE id=?", (candidate[0],))
        if password_hash is not None and not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            db.execute(
                "INSERT INTO users(username,display_name,password_hash,role,created_at,access_level) VALUES(?,?,?,?,?,?)",
                (settings.admin_username, "Administrator", password_hash, "admin", utcnow(), 'superadmin'),
            )
        db.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES('column_mapping',?)",
            (json.dumps(DEFAULT_MAPPING),),
        )
        mapping = json.loads(db.execute("SELECT value FROM settings WHERE key='column_mapping'").fetchone()[0])
        if 'discontinued' not in mapping:
            mapping['discontinued'] = 'Discontinued'
            db.execute("UPDATE settings SET value=? WHERE key='column_mapping'", (json.dumps(mapping),))
        for key in ('reorder_trigger', 'max_quantity', 'description'):
            if key not in mapping: mapping[key] = DEFAULT_MAPPING[key]
        db.execute("UPDATE settings SET value=? WHERE key='column_mapping'", (json.dumps(mapping),))


def get_mapping() -> dict[str, str]:
    with db_session() as db:
        row = db.execute("SELECT value FROM settings WHERE key='column_mapping'").fetchone()
    return json.loads(row["value"]) if row else dict(DEFAULT_MAPPING)


def set_mapping(mapping: dict[str, str]) -> None:
    with db_session() as db:
        db.execute(
            "INSERT INTO settings(key,value) VALUES('column_mapping',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(mapping),),
        )


def rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with db_session() as db:
        return [dict(row) for row in db.execute(query, params).fetchall()]


def runtime_setting(key, default=None):
    with db_session() as db:
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default
