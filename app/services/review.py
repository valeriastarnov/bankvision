"""Экран проверки полей: итоговые значения по умолчанию и ручные правки с журналом.

Итоговое значение по умолчанию — из документа, при его отсутствии — с первого фото,
где поле найдено. Это правило выбора, а не сверка источников: расхождения между
документом и фото проверяются отдельно (итерация 3).
"""
from __future__ import annotations

import sqlite3

from app import db
from app.config import Settings
from app.services.extract import FIELDS

ORIGIN_DOCUMENT = "document"
ORIGIN_PHOTO = "photo"
ORIGIN_MANUAL = "manual"
ORIGIN_NONE = "none"

EVENT_FIELD_EDITED = "field_edited"


class UnknownFieldError(ValueError):
    pass


def _default_choice(extracted: list[dict]) -> dict[str, dict]:
    """{поле: строка extracted_fields}, документ в приоритете перед фото."""
    choice: dict[str, dict] = {}
    for kind in ("document", "photo"):
        for row in extracted:
            if row["kind"] == kind and row["field"] not in choice:
                choice[row["field"]] = row
    return choice


def save_default_finals(conn: sqlite3.Connection, application_id: int) -> None:
    """Заполняет итоговые значения по умолчанию, не трогая исправленные вручную."""
    current = db.get_final(conn, application_id)
    choice = _default_choice(db.get_extracted(conn, application_id))
    for field in FIELDS:
        if current.get(field, {}).get("manually_edited"):
            continue
        row = choice.get(field)
        if row is None:
            db.upsert_final(conn, application_id, field, None, ORIGIN_NONE)
        else:
            db.upsert_final(conn, application_id, field, row["value"], row["kind"], row["file_id"])


def review_rows(extracted: list[dict], finals: dict[str, dict]) -> list[dict]:
    """Строки таблицы «поле — из документа — с фото — итоговое значение»."""
    rows = []
    for field, label in FIELDS.items():
        found = [r for r in extracted if r["field"] == field]
        rows.append({
            "field": field,
            "label": label,
            "document": next((r for r in found if r["kind"] == "document"), None),
            "photos": [r for r in found if r["kind"] == "photo"],
            "final": finals.get(field, {"value": None, "origin": ORIGIN_NONE, "manually_edited": False}),
        })
    return rows


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def apply_edits(settings: Settings, application_id: int, edits: dict[str, str | None]) -> list[dict]:
    """Применяет правки итоговых значений. Записывает в журнал только реально изменённые
    поля: поле, старое значение, новое значение (время — время записи журнала)."""
    unknown = [f for f in edits if f not in FIELDS]
    if unknown:
        raise UnknownFieldError(f"Неизвестные поля: {', '.join(unknown)}")

    changes = []
    with db.connect(settings.db_path) as conn:
        current = db.get_final(conn, application_id)
        for field, raw in edits.items():
            new = _clean(raw)
            old = current.get(field, {}).get("value")
            if new == old:
                continue
            db.upsert_final(conn, application_id, field, new, ORIGIN_MANUAL, manually_edited=True)
            db.log_event(
                conn, application_id, EVENT_FIELD_EDITED,
                f"{FIELDS[field]} ({field}): «{old or '—'}» → «{new or '—'}»",
            )
            changes.append({"field": field, "old": old, "new": new})
    return changes
