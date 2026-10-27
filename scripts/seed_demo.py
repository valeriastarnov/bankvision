"""Создаёт демо-заявки из синтетических фикстур — чтобы не загружать файлы вручную.

Обработка та же, что при загрузке через интерфейс: проверка файлов, качество фото,
OCR, извлечение полей, сверка. Все файлы синтетические (tests/generate_fixtures.py).

Запуск (из корня проекта):
    PowerShell:  .\\.venv\\Scripts\\python.exe -m scripts.seed_demo
    cmd:         .venv\\Scripts\\python.exe -m scripts.seed_demo
"""
from __future__ import annotations

import sys
from pathlib import Path

from app import db
from app.config import Settings
from app.services.analysis import analyze_application
from app.services.ingest import IncomingFile, create_application_with_files
from tests.generate_fixtures import FIXTURES_DIR, generate

# (название, фото шильдика, документ)
DEMO_CASES = [
    ("Чистый кейс: фото и паспорт совпадают", "plate_clear.jpg", "passport_text.pdf"),
    ("Размытое фото шильдика", "plate_blurred.jpg", "passport_text.pdf"),
    ("Заводской номер на шильдике отличается от паспорта", "plate_serial_mismatch.jpg", "passport_text.pdf"),
    ("Модель без аналогов в базе", "plate_unknown_model.jpg", "passport_unknown_model.pdf"),
]


def seed(settings: Settings) -> list[tuple[str, int]]:
    if not all((FIXTURES_DIR / name).exists() for _, p, d in DEMO_CASES for name in (p, d)):
        generate(FIXTURES_DIR)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    db.init_db(settings.db_path)

    created = []
    for title, photo, document in DEMO_CASES:
        with (FIXTURES_DIR / photo).open("rb") as ph, (FIXTURES_DIR / document).open("rb") as doc:
            app_id = create_application_with_files(
                [IncomingFile(photo, ph)], IncomingFile(document, doc), settings
            )
        with db.connect(settings.db_path) as conn:
            db.log_event(conn, app_id, "demo_seed", f"Синтетическая демо-заявка: {title}")
        analyze_application(app_id, settings)
        created.append((title, app_id))
    return created


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    settings = Settings.from_env()
    print(f"База: {settings.db_path}")
    for title, app_id in seed(settings):
        print(f"  №{app_id}: {title}")
        print(f"      http://127.0.0.1:8000/applications/{app_id}  ·  отчёт: /applications/{app_id}/report")


if __name__ == "__main__":
    main()
