"""Общие фикстуры: изолированные БД и папка загрузок, синтетические мини-файлы."""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

# Модуль app.main создаёт приложение при импорте — направляем его во временную папку,
# чтобы тесты не трогали рабочие data/uploads и базу.
os.environ.setdefault("BANKVISION_DATA_DIR", tempfile.mkdtemp(prefix="bankvision_import_"))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        upload_dir=tmp_path / "uploads",
        db_path=tmp_path / "test.db",
        max_file_size=200 * 1024,
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def _image_bytes(fmt: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (180, 180, 180)).save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def jpg_bytes() -> bytes:
    return _image_bytes("JPEG")


@pytest.fixture
def png_bytes() -> bytes:
    return _image_bytes("PNG")


@pytest.fixture
def pdf_bytes() -> bytes:
    # Минимальный синтетический PDF через PyMuPDF.
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "SYNTHETIC equipment passport")
    data = doc.tobytes()
    doc.close()
    return data
