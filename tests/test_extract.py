"""Итерация 2: извлечение полей правилами и экран ручной проверки с журналом правок."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pytest

from app.config import Settings
from app.services import extract, ocr
from tests.generate_fixtures import FIXTURES_DIR, generate
from tests.run_extract_eval import fixture_text
from tests.test_quality_ocr import needs_tesseract

# Кириллица ↔ латиница, одинаковые по написанию. Только для сравнения в тестах:
# нормализация в приложении — задача итерации 3.
_HOMOGLYPHS = str.maketrans("АВЕКМНОРСТХ", "ABEKMHOPCTX")


def _same_glyphs(a: str, b: str) -> bool:
    return a.upper().translate(_HOMOGLYPHS) == b.upper().translate(_HOMOGLYPHS)


@pytest.fixture(scope="session", autouse=True)
def _fixtures_exist():
    if not (FIXTURES_DIR / "passport_minimal.pdf").exists():
        generate(FIXTURES_DIR)


@lru_cache(maxsize=None)
def _text(name: str) -> str:
    return fixture_text(FIXTURES_DIR / name, Settings.from_env())


def _ref(name: str) -> dict:
    return json.loads((FIXTURES_DIR / Path(name).with_suffix(".json")).read_text(encoding="utf-8"))


def _values(name: str) -> dict[str, str]:
    found = extract.extract_fields(_text(name), allow_heuristics=name.endswith(".jpg"))
    return {k: v.value for k, v in found.items()}


def _expected(name: str) -> dict[str, str]:
    return {k: v for k, v in _ref(name)["fields"].items() if v is not None}


# ---------- документы с текстовым слоем: OCR не участвует, ожидаем точное совпадение ----------

@pytest.mark.parametrize("name", [
    "passport_text.pdf", "passport_no_serial.pdf", "passport_unknown_model.pdf", "passport_minimal.pdf",
])
def test_text_layer_passports_extract_exactly_reference(name):
    assert _values(name) == _expected(name)


def test_no_serial_passport_has_no_serial():
    assert "serial_number" not in _values("passport_no_serial.pdf")


def test_minimal_passport_has_only_type_and_model():
    assert set(_values("passport_minimal.pdf")) == {"equipment_type", "model"}


# ---------- фото шильдиков (OCR) ----------

@needs_tesseract
@pytest.mark.parametrize("name", ["plate_clear.jpg", "plate_skewed.jpg", "plate_serial_mismatch.jpg",
                                  "plate_unknown_model.jpg"])
def test_plate_fields_match_reference_up_to_homoglyphs(name):
    got, expected = _values(name), _expected(name)
    assert set(got) == set(expected)
    for field, value in expected.items():
        assert _same_glyphs(got[field], value), (field, got[field], value)


@needs_tesseract
def test_mismatch_plate_has_different_serial_from_passport():
    ref = _ref("plate_serial_mismatch.jpg")
    serial = _values("plate_serial_mismatch.jpg")["serial_number"]
    assert serial == ref["fields"]["serial_number"]
    assert serial != ref["pair_value"]["serial_number"]


@needs_tesseract
def test_plate_manufacturer_found_by_heuristic_with_fragment():
    found = extract.extract_fields(_text("plate_clear.jpg"), 7, "plate_clear.jpg", allow_heuristics=True)
    m = found["manufacturer"]
    assert m.method == extract.METHOD_HEURISTIC
    assert m.source_file_id == 7 and m.source_name == "plate_clear.jpg"
    assert m.value in m.fragment


# ---------- правила на синтетических строках ----------

def test_ocr_garbage_is_dropped():
    lines = extract.clean_lines("® ®\n. СИНТЕТИЧЕСКИЙ ОБРАЗЕЦ e\n© ©\n| _\nМодель: TC-1640M")
    assert [c for c, _ in lines] == ["СИНТЕТИЧЕСКИЙ ОБРАЗЕЦ", "Модель: TC-1640M"]


def test_rivets_do_not_become_manufacturer():
    found = extract.extract_fields("® ®\nООО ПРИМЕР-ДЕМО\nМодель: X-1", allow_heuristics=True)
    assert found["manufacturer"].value == "ООО ПРИМЕР-ДЕМО"


@pytest.mark.parametrize("line, expected", [
    ("Зав. №: 24-07815", "24-07815"),
    ("Зав. Ne: 24-07615", "24-07615"),  # так OCR читает знак №
    ("Заводской номер 12/345-А", "12/345-А"),
    ("S/N: AB-1234", "AB-1234"),
    ("Серийный номер: 0042 17", "004217"),
])
def test_serial_label_variants(line, expected):
    assert extract.extract_fields(line)["serial_number"].value == expected


def test_serial_without_digits_is_rejected():
    assert "serial_number" not in extract.extract_fields("Зав. №: отсутствует")


@pytest.mark.parametrize("line, field, expected", [
    ("Год изготовления: 2015", "year", "2015"),
    ("Год вытуска 2018", "year", "2018"),
    ("Мощность главного привода, кВт: 7,5", "power_kw", "7.5"),
    ("Масса станка, кг: 2 800", "weight_kg", "2800"),
    ("РМЦ, мм: 1500", "center_distance_mm", "1500"),
    ("Производитель: ДЕМО-ЗАВОД", "manufacturer", "ДЕМО-ЗАВОД"),
])
def test_label_rules(line, field, expected):
    assert extract.extract_fields(line)[field].value == expected


def test_heuristics_disabled_for_documents():
    text = "ПРИМЕР-ДЕМО\nСтанок токарный\nМодель: X-1"
    assert set(extract.extract_fields(text)) == {"model"}
    assert {"manufacturer", "equipment_type", "model"} <= set(
        extract.extract_fields(text, allow_heuristics=True))


# ---------- экран проверки и журнал ----------

def _upload(client, photo: str, document: str):
    files = [
        ("photos", (photo, (FIXTURES_DIR / photo).read_bytes(), "image/jpeg")),
        ("document", (document, (FIXTURES_DIR / document).read_bytes(), "application/pdf")),
    ]
    resp = client.post("/api/applications", files=files)
    assert resp.status_code == 201, resp.text
    return resp.json()["application"]["id"]


@needs_tesseract
def test_review_defaults_document_first_then_photo(client):
    app_id = _upload(client, "plate_clear.jpg", "passport_no_serial.pdf")
    data = client.get(f"/api/applications/{app_id}/fields").json()
    final = data["final"]
    # Модель есть в документе — итог из документа (кириллица из текстового слоя).
    assert final["model"]["value"] == "ТС-1640М" and final["model"]["origin"] == "document"
    # Номера в документе нет — итог с фото.
    assert final["serial_number"]["value"] == "24-07815"
    assert final["serial_number"]["origin"] == "photo"
    # Для каждого извлечённого значения хранятся файл и исходная строка.
    photo_serial = next(e for e in data["extracted"]
                        if e["field"] == "serial_number" and e["kind"] == "photo")
    assert photo_serial["source_name"] == "plate_clear.jpg"
    assert "24-07815" in photo_serial["fragment"]


@needs_tesseract
def test_manual_edit_is_saved_marked_and_journaled(client):
    app_id = _upload(client, "plate_clear.jpg", "passport_minimal.pdf")
    page = client.get(f"/applications/{app_id}/review")
    assert page.status_code == 200
    assert "Из документа" in page.text and "С фото" in page.text

    resp = client.post(f"/applications/{app_id}/review", data={
        "field__year": "2019",
        "field__model": "ТС-1640М",  # без изменений — не должно попасть в журнал
    })
    assert resp.status_code == 200  # после редиректа 303
    assert "Сохранено изменений: 1" in resp.text
    assert "исправлено вручную" in resp.text

    final = client.get(f"/api/applications/{app_id}/fields").json()["final"]
    assert final["year"]["value"] == "2019"
    assert final["year"]["manually_edited"] is True
    assert final["year"]["origin"] == "manual"

    journal = client.get(f"/api/applications/{app_id}").json()["journal"]
    edits = [j for j in journal if j["event"] == "field_edited"]
    assert len(edits) == 1
    assert "year" in edits[0]["details"]
    assert "«2018» → «2019»" in edits[0]["details"]
    assert edits[0]["created_at"]


@needs_tesseract
def test_api_edit_clear_value_and_unknown_field(client):
    app_id = _upload(client, "plate_clear.jpg", "passport_text.pdf")
    resp = client.put(f"/api/applications/{app_id}/fields", json={"weight_kg": ""})
    assert resp.status_code == 200
    assert resp.json()["changes"] == [
        {"field": "weight_kg", "old": "2800", "new": None, "origin": "manual"}]

    bad = client.put(f"/api/applications/{app_id}/fields", json={"price": "1"})
    assert bad.status_code == 400
    assert "price" in bad.json()["detail"]


def test_review_unknown_application_404(client):
    assert client.get("/applications/999/review").status_code == 404
    assert client.put("/api/applications/999/fields", json={"year": "2000"}).status_code == 404


def test_index_has_processing_indicator(client):
    page = client.get("/").text
    assert "Идёт обработка файлов" in page
