"""Экран проверки полей: итоговые значения, сверка источников, ручные правки с журналом.

Итоговое значение по умолчанию — из документа, при его отсутствии — с фото. Если сверка
дала «расходится» или «не подтверждено», значение автоматически не выбирается
(origin = pending): сотрудник выбирает источник или вводит значение, выбор пишется
в журнал.
"""
from __future__ import annotations

import sqlite3

from app import db
from app.config import Settings
from app.services import crosscheck
from app.services.extract import FIELDS

ORIGIN_DOCUMENT = "document"
ORIGIN_PHOTO = "photo"
ORIGIN_MANUAL = "manual"
ORIGIN_CHOSEN_DOCUMENT = "chosen_document"
ORIGIN_CHOSEN_PHOTO = "chosen_photo"
ORIGIN_PENDING = "pending"
ORIGIN_NONE = "none"

SOURCE_DOCUMENT = "document"
SOURCE_MANUAL = "manual"
SOURCE_PHOTO_PREFIX = "photo:"

EVENT_FIELD_EDITED = "field_edited"


class UnknownFieldError(ValueError):
    pass


class InvalidSourceError(ValueError):
    pass


def photo_quality_flags(conn: sqlite3.Connection, application_id: int) -> dict[int, list[str]]:
    analysis = db.get_analysis(conn, application_id)
    return {fid: (a["quality"] or {}).get("flags", []) for fid, a in analysis.items() if a["quality"]}


def compute_checks(conn: sqlite3.Connection, application_id: int,
                   settings: Settings) -> dict[str, crosscheck.FieldCheck]:
    return crosscheck.crosscheck(
        db.get_extracted(conn, application_id), photo_quality_flags(conn, application_id), settings
    )


def save_default_finals(conn: sqlite3.Connection, application_id: int, settings: Settings) -> None:
    """Заполняет итоговые значения по умолчанию, не трогая решения сотрудника."""
    current = db.get_final(conn, application_id)
    checks = compute_checks(conn, application_id, settings)
    for name, check in checks.items():
        if current.get(name, {}).get("manually_edited"):
            continue
        if check.needs_decision:
            db.upsert_final(conn, application_id, name, None, ORIGIN_PENDING)
        elif check.document is not None:
            db.upsert_final(conn, application_id, name, check.document["value"],
                            ORIGIN_DOCUMENT, check.document["file_id"])
        elif check.photos:
            p = check.photos[0]
            db.upsert_final(conn, application_id, name, p["value"], ORIGIN_PHOTO, p["file_id"])
        else:
            db.upsert_final(conn, application_id, name, None, ORIGIN_NONE)


def review_rows(checks: dict[str, crosscheck.FieldCheck], finals: dict[str, dict]) -> list[dict]:
    """Строки таблицы «поле — из документа — с фото — сверка — итоговое значение»."""
    return [
        {
            "field": name,
            "label": c.label,
            "document": c.document,
            "photos": c.photos,
            "check": c,
            "final": finals.get(name, {"value": None, "origin": ORIGIN_NONE, "manually_edited": False}),
        }
        for name, c in checks.items()
    ]


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def _resolve_source(check: crosscheck.FieldCheck, source: str, typed: str | None
                    ) -> tuple[str | None, str, int | None, str]:
    """(значение, origin, файл, пояснение для журнала) по выбранному источнику."""
    if source == SOURCE_DOCUMENT:
        if check.document is None:
            raise InvalidSourceError(f"{check.label}: в документе значения нет")
        return check.document["value"], ORIGIN_CHOSEN_DOCUMENT, check.document["file_id"], \
            "выбрано: из документа"
    if source.startswith(SOURCE_PHOTO_PREFIX):
        try:
            file_id = int(source[len(SOURCE_PHOTO_PREFIX):])
        except ValueError as exc:
            raise InvalidSourceError(f"{check.label}: неверный источник «{source}»") from exc
        photo = next((p for p in check.photos if p["file_id"] == file_id), None)
        if photo is None:
            raise InvalidSourceError(f"{check.label}: на этом фото значения нет")
        return photo["value"], ORIGIN_CHOSEN_PHOTO, file_id, \
            f"выбрано: с фото «{photo['source_name']}»"
    if source == SOURCE_MANUAL:
        return _clean(typed), ORIGIN_MANUAL, None, "введено вручную"
    raise InvalidSourceError(f"{check.label}: неверный источник «{source}»")


def apply_edits(
    settings: Settings,
    application_id: int,
    edits: dict[str, str | None],
    sources: dict[str, str] | None = None,
) -> list[dict]:
    """Применяет правки итоговых значений.

    edits — введённые значения; sources — выбранный источник для поля
    («document», «photo:<id>», «manual»). Если источник выбран, значение берётся из него.
    В журнал пишутся только реально изменённые поля: поле, старое и новое значение,
    способ выбора (время — время записи журнала)."""
    sources = {k: v for k, v in (sources or {}).items() if v}
    unknown = [f for f in {*edits, *sources} if f not in FIELDS]
    if unknown:
        raise UnknownFieldError(f"Неизвестные поля: {', '.join(sorted(unknown))}")

    changes = []
    with db.connect(settings.db_path) as conn:
        current = db.get_final(conn, application_id)
        checks = compute_checks(conn, application_id, settings) if sources else {}
        for name in FIELDS:
            if name not in edits and name not in sources:
                continue
            if name in sources:
                new, origin, file_id, how = _resolve_source(checks[name], sources[name], edits.get(name))
            else:
                new, origin, file_id, how = _clean(edits[name]), ORIGIN_MANUAL, None, ""
            old = current.get(name, {}).get("value")
            was_pending = current.get(name, {}).get("origin") == ORIGIN_PENDING
            if new == old and not (was_pending and new is not None):
                continue
            db.upsert_final(conn, application_id, name, new, origin, file_id, manually_edited=True)
            details = f"{FIELDS[name]} ({name}): «{old or '—'}» → «{new or '—'}»"
            db.log_event(conn, application_id, EVENT_FIELD_EDITED,
                         f"{details} ({how})" if how else details)
            changes.append({"field": name, "old": old, "new": new, "origin": origin})
    return changes
