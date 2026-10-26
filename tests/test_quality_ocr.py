"""Итерация 1: качество фото, PDF (текстовый слой и скан), OCR шильдиков.

Используются синтетические фикстуры из tests/fixtures (tests/generate_fixtures.py).
Тесты с OCR пропускаются с явной причиной, если Tesseract не установлен.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest

from app.config import Settings
from app.services import image_quality, ocr, pdf
from tests.generate_fixtures import FIXTURES_DIR, generate


def _tesseract_available() -> bool:
    try:
        ocr.check_available(Settings.from_env())
        return True
    except ocr.OcrUnavailableError:
        return False


needs_tesseract = pytest.mark.skipif(not _tesseract_available(), reason="Tesseract не установлен")


@pytest.fixture(scope="session")
def fixtures() -> Path:
    if not (FIXTURES_DIR / "plate_clear.jpg").exists():
        generate(FIXTURES_DIR)
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def ocr_settings() -> Settings:
    return Settings.from_env()


def _reference(fixtures: Path, stem: str) -> dict:
    return json.loads((fixtures / f"{stem}.json").read_text(encoding="utf-8"))


def _squash(text: str) -> str:
    """Убирает пробелы — OCR иногда разрывает число пробелом."""
    return re.sub(r"\s+", "", text)


# ---------- качество фото ----------

def test_clear_photo_has_no_quality_flags(fixtures):
    q = image_quality.assess(fixtures / "plate_clear.jpg", Settings())
    assert q.flags == [], q


def test_blurred_photo_flagged_with_hint(fixtures):
    q = image_quality.assess(fixtures / "plate_blurred.jpg", Settings())
    assert image_quality.FLAG_BLURRY in q.flags
    assert any("размыто" in h for h in q.hints)


def test_dark_photo_flagged(fixtures):
    q = image_quality.assess(fixtures / "plate_dark.jpg", Settings())
    assert image_quality.FLAG_DARK in q.flags
    assert any("тёмное" in h for h in q.hints)


def test_bright_and_low_resolution_flags():
    gray = np.full((300, 400), 245, dtype=np.uint8)
    gray[::10, :] = 0  # резкие линии, чтобы не сработал флаг размытости
    q = image_quality.assess_array(gray, Settings())
    assert image_quality.FLAG_BRIGHT in q.flags
    assert image_quality.FLAG_LOW_RES in q.flags
    assert image_quality.FLAG_BLURRY not in q.flags


def test_quality_thresholds_come_from_settings(fixtures):
    strict = Settings(blur_laplacian_min=1e9)
    q = image_quality.assess(fixtures / "plate_clear.jpg", strict)
    assert image_quality.FLAG_BLURRY in q.flags


def test_corrupted_image_raises_readable_error(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8\xff" + b"garbage" * 10)
    with pytest.raises(image_quality.ImageReadError, match="повреждён"):
        image_quality.assess(bad, Settings())


# ---------- PDF ----------

def test_pdf_text_layer_extracted_without_ocr(fixtures, ocr_settings):
    ref = _reference(fixtures, "passport_text")
    doc = pdf.extract_pdf(fixtures / "passport_text.pdf", ocr_settings)
    assert doc.sources == [pdf.SOURCE_TEXT_LAYER]
    for line in ref["document_text"]:
        assert line in doc.text


@needs_tesseract
def test_scanned_pdf_falls_back_to_ocr(fixtures, ocr_settings):
    ref = _reference(fixtures, "passport_scan")
    doc = pdf.extract_pdf(fixtures / "passport_scan.pdf", ocr_settings)
    assert doc.sources == [pdf.SOURCE_OCR]
    assert ref["fields"]["serial_number"] in _squash(doc.text)
    assert ref["fields"]["year"] in doc.text


def test_broken_pdf_raises_readable_error(tmp_path, ocr_settings):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\n" + b"\x00garbage" * 50)
    with pytest.raises(pdf.DocumentError, match="повреждён"):
        pdf.extract_pdf(broken, ocr_settings)


def test_text_layer_normalization():
    assert pdf.normalize_text_layer("ТС\xad1640М\xa0x") == "ТС-1640М x"


# ---------- OCR ----------

@needs_tesseract
def test_ocr_clear_plate_finds_serial_and_year(fixtures, ocr_settings):
    ref = _reference(fixtures, "plate_clear")
    result = ocr.ocr_image(fixtures / "plate_clear.jpg", ocr_settings)
    assert ref["fields"]["serial_number"] in _squash(result.text)
    assert ref["fields"]["year"] in result.text
    assert ref["fields"]["manufacturer"] in result.text


@needs_tesseract
def test_deskew_estimates_rotation(fixtures, ocr_settings):
    ref = _reference(fixtures, "plate_skewed")
    gray = image_quality.load_gray(fixtures / "plate_skewed.jpg")
    _, angle = ocr.preprocess(gray, ocr_settings)
    # Поворот на +angle выравнивает изображение, повёрнутое на -angle, и наоборот.
    assert abs(angle + ref["distortions"]["rotation_deg"]) <= 1.0


def test_missing_tesseract_gives_clear_error(fixtures):
    settings = Settings(tesseract_cmd=r"C:\nonexistent\tesseract.exe")
    with pytest.raises(ocr.OcrUnavailableError, match="TESSERACT_CMD"):
        ocr.ocr_image(fixtures / "plate_clear.jpg", settings)


# ---------- сквозной сценарий ----------

@needs_tesseract
def test_upload_blurred_photo_shows_flag_and_hint(client, fixtures):
    files = [
        ("photos", ("plate_blurred.jpg", (fixtures / "plate_blurred.jpg").read_bytes(), "image/jpeg")),
        ("document", ("passport_text.pdf", (fixtures / "passport_text.pdf").read_bytes(), "application/pdf")),
    ]
    resp = client.post("/api/applications", files=files)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    photo = next(f for f in body["application"]["files"] if f["kind"] == "photo")
    quality = body["analysis"][str(photo["id"])]["quality"]
    assert image_quality.FLAG_BLURRY in quality["flags"]

    page = client.get(f"/applications/{body['application']['id']}")
    assert "Фото размыто" in page.text
    assert "текстовый слой PDF" in page.text


@needs_tesseract
def test_upload_broken_pdf_records_processing_error(client, fixtures):
    files = [
        ("photos", ("plate_clear.jpg", (fixtures / "plate_clear.jpg").read_bytes(), "image/jpeg")),
        ("document", ("broken.pdf", b"%PDF-1.4\n" + b"\x00garbage" * 50, "application/pdf")),
    ]
    resp = client.post("/api/applications", files=files)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["application"]["status"] == "processed_with_errors"
    doc = next(f for f in body["application"]["files"] if f["kind"] == "document")
    assert "повреждён" in body["analysis"][str(doc["id"])]["error"]
