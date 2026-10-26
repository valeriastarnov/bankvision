"""Работа с SQLite через стандартный sqlite3."""
from __future__ import annotations

import json
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
CREATE TABLE IF NOT EXISTS file_analysis (
    file_id INTEGER PRIMARY KEY REFERENCES files(id),
    quality_json TEXT,
    text TEXT,
    text_meta_json TEXT,
    error TEXT
);
-- Поля, извлечённые из каждого файла отдельно.
CREATE TABLE IF NOT EXISTS extracted_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    file_id INTEGER NOT NULL REFERENCES files(id),
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    fragment TEXT NOT NULL,
    method TEXT NOT NULL
);
-- Итоговые значения полей заявки (по умолчанию или исправленные вручную).
CREATE TABLE IF NOT EXISTS final_fields (
    application_id INTEGER NOT NULL REFERENCES applications(id),
    field TEXT NOT NULL,
    value TEXT,
    origin TEXT NOT NULL,
    source_file_id INTEGER REFERENCES files(id),
    manually_edited INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (application_id, field)
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


def save_analysis(
    conn: sqlite3.Connection,
    file_id: int,
    quality: dict | None = None,
    text: str | None = None,
    text_meta: dict | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO file_analysis (file_id, quality_json, text, text_meta_json, error)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            file_id,
            json.dumps(quality, ensure_ascii=False) if quality is not None else None,
            text,
            json.dumps(text_meta, ensure_ascii=False) if text_meta is not None else None,
            error,
        ),
    )


def get_analysis(conn: sqlite3.Connection, application_id: int) -> dict[int, dict]:
    """Результаты анализа файлов заявки: {file_id: {...}}."""
    rows = conn.execute(
        "SELECT a.file_id, a.quality_json, a.text, a.text_meta_json, a.error"
        " FROM file_analysis a JOIN files f ON f.id = a.file_id WHERE f.application_id = ?",
        (application_id,),
    )
    return {
        r["file_id"]: {
            "quality": json.loads(r["quality_json"]) if r["quality_json"] else None,
            "text": r["text"],
            "text_meta": json.loads(r["text_meta_json"]) if r["text_meta_json"] else None,
            "error": r["error"],
        }
        for r in rows
    }


def set_status(conn: sqlite3.Connection, application_id: int, status: str) -> None:
    conn.execute("UPDATE applications SET status = ? WHERE id = ?", (status, application_id))


def replace_extracted(
    conn: sqlite3.Connection, application_id: int, file_id: int, fields: list[dict]
) -> None:
    conn.execute("DELETE FROM extracted_fields WHERE file_id = ?", (file_id,))
    conn.executemany(
        "INSERT INTO extracted_fields (application_id, file_id, field, value, fragment, method)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [(application_id, file_id, f["field"], f["value"], f["fragment"], f["method"]) for f in fields],
    )


def get_extracted(conn: sqlite3.Connection, application_id: int) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT e.file_id, f.kind, f.original_name AS source_name, e.field, e.value,"
            " e.fragment, e.method FROM extracted_fields e JOIN files f ON f.id = e.file_id"
            " WHERE e.application_id = ? ORDER BY e.file_id, e.id",
            (application_id,),
        )
    ]


def upsert_final(
    conn: sqlite3.Connection,
    application_id: int,
    field: str,
    value: str | None,
    origin: str,
    source_file_id: int | None = None,
    manually_edited: bool = False,
) -> None:
    conn.execute(
        "INSERT INTO final_fields (application_id, field, value, origin, source_file_id,"
        " manually_edited, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (application_id, field) DO UPDATE SET value = excluded.value,"
        " origin = excluded.origin, source_file_id = excluded.source_file_id,"
        " manually_edited = excluded.manually_edited, updated_at = excluded.updated_at",
        (application_id, field, value, origin, source_file_id, int(manually_edited), _now()),
    )


def get_final(conn: sqlite3.Connection, application_id: int) -> dict[str, dict]:
    return {
        r["field"]: dict(r) | {"manually_edited": bool(r["manually_edited"])}
        for r in conn.execute(
            "SELECT field, value, origin, source_file_id, manually_edited, updated_at"
            " FROM final_fields WHERE application_id = ?",
            (application_id,),
        )
    }
