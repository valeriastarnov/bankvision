"""Работа с SQLite через стандартный sqlite3."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.models import Application, StoredFile

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    kind TEXT NOT NULL CHECK (kind IN ('photo', 'document')),
    original_name TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    created_at TEXT NOT NULL,
    event TEXT NOT NULL,
    details TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def create_application(conn: sqlite3.Connection, status: str = "uploaded") -> int:
    cur = conn.execute(
        "INSERT INTO applications (created_at, status) VALUES (?, ?)", (_now(), status)
    )
    return int(cur.lastrowid)


def add_file(
    conn: sqlite3.Connection,
    application_id: int,
    kind: str,
    original_name: str,
    stored_path: str,
    content_type: str,
    size: int,
) -> int:
    cur = conn.execute(
        "INSERT INTO files (application_id, kind, original_name, stored_path, content_type, size)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (application_id, kind, original_name, stored_path, content_type, size),
    )
    return int(cur.lastrowid)


def log_event(
    conn: sqlite3.Connection, application_id: int, event: str, details: str | None = None
) -> None:
    conn.execute(
        "INSERT INTO journal (application_id, created_at, event, details) VALUES (?, ?, ?, ?)",
        (application_id, _now(), event, details),
    )


def get_application(conn: sqlite3.Connection, application_id: int) -> Application | None:
    row = conn.execute(
        "SELECT id, created_at, status FROM applications WHERE id = ?", (application_id,)
    ).fetchone()
    if row is None:
        return None
    files = [
        StoredFile(**dict(r))
        for r in conn.execute(
            "SELECT id, application_id, kind, original_name, stored_path, content_type, size"
            " FROM files WHERE application_id = ? ORDER BY id",
            (application_id,),
        )
    ]
    return Application(id=row["id"], created_at=row["created_at"], status=row["status"], files=files)


def get_journal(conn: sqlite3.Connection, application_id: int) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT created_at, event, details FROM journal WHERE application_id = ? ORDER BY id",
            (application_id,),
        )
    ]
