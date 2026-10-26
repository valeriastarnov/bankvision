"""Приём файлов заявки: проверка формата, размера и количества, сохранение на диск.

Загрузка атомарна: если хотя бы один файл не прошёл проверку, ничего не сохраняется.
"""
from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app import db
from app.config import Settings

# Сигнатуры (magic bytes): проверяем содержимое, а не только расширение.
SIGNATURES: dict[str, tuple[bytes, str]] = {
    "jpeg": (b"\xff\xd8\xff", "image/jpeg"),
    "png": (b"\x89PNG\r\n\x1a\n", "image/png"),
    "pdf": (b"%PDF-", "application/pdf"),
}
EXTENSION_FORMAT = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".pdf": "pdf"}

READ_CHUNK = 64 * 1024


class UploadError(Exception):
    """Ошибка загрузки с сообщениями, понятными пользователю."""

    def __init__(self, messages: list[str], too_large: bool = False):
        super().__init__("; ".join(messages))
        self.messages = messages
        self.too_large = too_large


@dataclass
class IncomingFile:
    filename: str
    stream: BinaryIO


@dataclass
class _Checked:
    kind: str
    original_name: str
    extension: str
    content_type: str
    data: bytes


def _read_limited(stream: BinaryIO, limit: int) -> bytes | None:
    """Читает поток порциями; возвращает None, если размер превышает limit."""
    buf = bytearray()
    while chunk := stream.read(READ_CHUNK):
        buf.extend(chunk)
        if len(buf) > limit:
            return None
    return bytes(buf)


def _human_list(extensions: frozenset[str]) -> str:
    return ", ".join(sorted(e.lstrip(".").upper() for e in extensions if e != ".jpeg"))


def _check_file(
    incoming: IncomingFile, kind: str, allowed: frozenset[str], settings: Settings
) -> tuple[_Checked | None, str | None, bool]:
    """Возвращает (результат, сообщение об ошибке, превышен ли размер)."""
    name = Path(incoming.filename or "").name or "без имени"
    label = "Фото" if kind == "photo" else "Документ"
    ext = Path(name).suffix.lower()

    if ext not in allowed:
        return None, (
            f"{label} «{name}»: неподдерживаемый формат. Допустимы: {_human_list(allowed)}."
        ), False

    data = _read_limited(incoming.stream, settings.max_file_size)
    if data is None:
        limit_mb = settings.max_file_size / (1024 * 1024)
        return None, f"{label} «{name}»: файл больше {limit_mb:g} МБ.", True
    if not data:
        return None, f"{label} «{name}»: файл пустой.", False

    fmt = EXTENSION_FORMAT[ext]
    signature, content_type = SIGNATURES[fmt]
    if not data.startswith(signature):
        return None, (
            f"{label} «{name}»: содержимое не соответствует формату {fmt.upper()} "
            "(файл повреждён или расширение изменено)."
        ), False

    stored_ext = ".jpg" if fmt == "jpeg" else ext
    return _Checked(kind, name, stored_ext, content_type, data), None, False


def validate(
    photos: list[IncomingFile], document: IncomingFile | None, settings: Settings
) -> list[_Checked]:
    errors: list[str] = []

    if len(photos) < settings.min_photos:
        errors.append(f"Загрузите хотя бы {settings.min_photos} фото оборудования.")
    elif len(photos) > settings.max_photos:
        errors.append(
            f"Можно загрузить не более {settings.max_photos} фото, получено {len(photos)}."
        )
    if document is None:
        errors.append("Загрузите документ на оборудование (PDF, JPG или PNG).")
    if errors:
        raise UploadError(errors)

    checked: list[_Checked] = []
    too_large = False
    items = [(p, "photo", settings.photo_extensions) for p in photos]
    items.append((document, "document", settings.document_extensions))
    for incoming, kind, allowed in items:
        result, error, oversize = _check_file(incoming, kind, allowed, settings)
        if error:
            errors.append(error)
            too_large = too_large or oversize
        else:
            checked.append(result)

    if errors:
        raise UploadError(errors, too_large=too_large)
    return checked


def create_application_with_files(
    photos: list[IncomingFile], document: IncomingFile | None, settings: Settings
) -> int:
    """Проверяет файлы, создаёт заявку и сохраняет файлы. Возвращает id заявки."""
    checked = validate(photos, document, settings)

    app_dir: Path | None = None
    try:
        with db.connect(settings.db_path) as conn:
            application_id = db.create_application(conn)
            app_dir = settings.upload_dir / str(application_id)
            app_dir.mkdir(parents=True, exist_ok=True)
            for item in checked:
                stored = app_dir / f"{item.kind}_{uuid.uuid4().hex}{item.extension}"
                stored.write_bytes(item.data)
                db.add_file(
                    conn,
                    application_id,
                    item.kind,
                    item.original_name,
                    stored.relative_to(settings.upload_dir).as_posix(),
                    item.content_type,
                    len(item.data),
                )
            n_photos = sum(1 for c in checked if c.kind == "photo")
            db.log_event(conn, application_id, "created", f"фото: {n_photos}, документ: 1")
    except Exception:
        if app_dir is not None:
            shutil.rmtree(app_dir, ignore_errors=True)
        raise
    return application_id
