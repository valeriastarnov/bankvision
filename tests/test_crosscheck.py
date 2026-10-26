"""Итерация 3: сверка документа и фото шильдика."""
from __future__ import annotations

from app.config import Settings
from app.services import crosscheck as cc
from tests.generate_fixtures import FIXTURES_DIR
from tests.test_quality_ocr import needs_tesseract

S = Settings()


def _row(kind: str, field: str, value: str, file_id: int, name: str | None = None) -> dict:
    return {"file_id": file_id, "kind": kind, "source_name": name or f"{kind}{file_id}",
            "field": field, "value": value, "fragment": value, "method": "label"}


def _check(field: str, doc: str | None, photo: str | None, low_quality: bool = False) -> cc.FieldCheck:
    rows = []
    if doc is not None:
        rows.append(_row("document", field, doc, 1, "passport.pdf"))
    if photo is not None:
        rows.append(_row("photo", field, photo, 2, "plate.jpg"))
    flags = {2: ["blurry"]} if low_quality else {}
    return cc.check_field(field, rows, flags, S)


# ---------- обязательные сценарии ----------

def test_exact_match():
    c = _check("serial_number", "24-07815", "24-07815")
    assert c.status == cc.STATUS_MATCH
    assert c.normalization == []


def test_model_matches_only_after_homoglyph_normalization():
    c = _check("model", "ТС-1640М", "TC-1640M")  # кириллица в документе, латиница с фото
    assert c.status == cc.STATUS_NORMALIZED
    assert c.normalization == ["кириллические М, С, Т → латинские M, C, T"]
    # Исходные значения не меняются — их и показывает интерфейс.
    assert c.document["value"] == "ТС-1640М"
    assert c.photos[0]["value"] == "TC-1640M"


def test_serial_mismatch_on_clear_photo():
    c = _check("serial_number", "24-07815", "24-09156")
    assert c.status == cc.STATUS_MISMATCH
    assert c.needs_decision


def test_serial_mismatch_on_blurred_photo_is_not_confirmed():
    c = _check("serial_number", "24-07815", "24-07615", low_quality=True)
    assert c.status == cc.STATUS_NOT_CONFIRMED
    assert c.needs_decision
    assert "plate.jpg" in c.note


def test_serial_missing_in_passport():
    c = _check("serial_number", None, "24-07815")
    assert c.status == cc.STATUS_SINGLE
    assert not c.needs_decision
    assert "только на фото" in c.note


def test_serial_differing_by_one_digit_is_not_a_match():
    c = _check("serial_number", "24-07815", "24-07816")
    assert c.status == cc.STATUS_MISMATCH


# ---------- нормализация ----------

def test_serial_o_zero_and_i_l_one_normalized():
    c = _check("serial_number", "24-07815", "24-O78I5")
    assert c.status == cc.STATUS_NORMALIZED
    assert "O → 0" in c.normalization and "I/l → 1" in c.normalization


def test_o_zero_not_applied_to_model():
    assert _check("model", "TC-1640M", "TC-164OM").status == cc.STATUS_MISMATCH


def test_spaces_dashes_and_case_reported():
    c = _check("serial_number", "24-07815", "24 07815")
    assert c.status == cc.STATUS_NORMALIZED
    assert set(c.normalization) == {"пробелы", "дефисы"}
    c = _check("equipment_type", "Станок токарный", "СТАНОК ТОКАРНЫЙ")
    assert c.normalization == ["регистр"]


def test_cyrillic_o_in_serial_matches_zero():
    # Кириллическая «О» → латинская O → 0.
    c = _check("serial_number", "24-07815", "24-О7815")
    assert c.status == cc.STATUS_NORMALIZED


def test_no_fuzzy_matching_for_serial_and_model():
    # Одна лишняя буква — для производителя прошло бы нечёткое сравнение, здесь нет.
    assert _check("model", "TC-1640M", "TC-1640MA").status == cc.STATUS_MISMATCH
    assert _check("serial_number", "24-078150", "24-07815").status == cc.STATUS_MISMATCH


def test_manufacturer_fuzzy_match_above_threshold():
    c = _check("manufacturer", "ТОКАРЬПРОМ-СИНТЕЗ", "ТОКАРЬПРОМ.СИНТЕЗ")
    assert c.status == cc.STATUS_NORMALIZED
    assert any("нечёткое сравнение" in s for s in c.normalization)


def test_manufacturer_fuzzy_respects_threshold():
    c = cc.check_field("manufacturer",
                       [_row("document", "manufacturer", "ТОКАРЬПРОМ-СИНТЕЗ", 1),
                        _row("photo", "manufacturer", "ТОКАРЬПРОМ.СИНТЕЗ", 2)],
                       {}, Settings(manufacturer_fuzzy_min=99.9))
    assert c.status == cc.STATUS_MISMATCH


def test_different_manufacturer_is_mismatch():
    assert _check("manufacturer", "ТОКАРЬПРОМ-СИНТЕЗ", "ВЕКТОРСТАН-ДЕМО").status == cc.STATUS_MISMATCH


def test_missing_everywhere_and_document_only():
    assert _check("serial_number", None, None).status == cc.STATUS_MISSING
    c = _check("max_diameter_mm", "400", None)
    assert c.status == cc.STATUS_SINGLE and "только в документе" in c.note


def test_match_on_low_quality_photo_is_match_with_note():
    c = _check("serial_number", "24-07815", "24-07815", low_quality=True)
    assert c.status == cc.STATUS_MATCH
    assert "флагом качества" in c.note


def test_good_photo_mismatch_wins_over_blurred_match():
    rows = [_row("document", "serial_number", "24-07815", 1),
            _row("photo", "serial_number", "24-07815", 2),   # размытое, совпадает
            _row("photo", "serial_number", "24-09156", 3)]   # чёткое, расходится
    c = cc.check_field("serial_number", rows, {2: ["blurry"]}, S)
    assert c.status == cc.STATUS_MISMATCH


def test_flags_list():
    checks = {
        "serial_number": _check("serial_number", "24-07815", "24-09156"),
        "max_diameter_mm": _check("max_diameter_mm", "400", None),  # только в документе — норма
        "year": _check("year", "2018", None),  # ключевое поле только в одном источнике
    }
    flags = cc.flags(checks, {"plate.jpg": ["blurry"], "general.jpg": []})
    assert any(f.startswith("Заводской (серийный) номер: расходится") for f in flags)
    assert any(f.startswith("Год выпуска: есть только в одном источнике") for f in flags)
    assert not any("диаметр" in f for f in flags)
    assert "Фото «plate.jpg»: флаги качества — blurry" in flags
    assert len(flags) == 3


# ---------- сквозные сценарии на фикстурах ----------

def _upload(client, photo: str, document: str) -> int:
    files = [
        ("photos", (photo, (FIXTURES_DIR / photo).read_bytes(), "image/jpeg")),
        ("document", (document, (FIXTURES_DIR / document).read_bytes(), "application/pdf")),
    ]
    resp = client.post("/api/applications", files=files)
    assert resp.status_code == 201, resp.text
    return resp.json()["application"]["id"]


def _fields(client, app_id: int) -> dict:
    return client.get(f"/api/applications/{app_id}/fields").json()


@needs_tesseract
def test_fixture_match_and_normalized_model(client):
    data = _fields(client, _upload(client, "plate_clear.jpg", "passport_text.pdf"))
    checks = data["crosscheck"]
    assert checks["serial_number"]["status"] == cc.STATUS_MATCH
    assert checks["model"]["status"] == cc.STATUS_NORMALIZED
    assert data["final"]["model"]["value"] == "ТС-1640М"  # из документа, в исходном виде
    assert data["flags"] == []


@needs_tesseract
def test_fixture_serial_mismatch_requires_decision(client):
    app_id = _upload(client, "plate_serial_mismatch.jpg", "passport_text.pdf")
    data = _fields(client, app_id)
    assert data["crosscheck"]["serial_number"]["status"] == cc.STATUS_MISMATCH
    assert data["final"]["serial_number"]["value"] is None
    assert data["final"]["serial_number"]["origin"] == "pending"

    page = client.get(f"/applications/{app_id}/review").text
    assert "расходится" in page  # статус текстом, не только цветом
    assert "Заводской (серийный) номер: расходится" in page  # в общем списке флагов
    assert 'name="source__serial_number"' in page


@needs_tesseract
def test_fixture_blurred_photo_not_confirmed(client):
    data = _fields(client, _upload(client, "plate_blurred.jpg", "passport_text.pdf"))
    assert data["crosscheck"]["serial_number"]["status"] == cc.STATUS_NOT_CONFIRMED
    assert data["final"]["serial_number"]["origin"] == "pending"


@needs_tesseract
def test_fixture_no_serial_in_passport(client):
    data = _fields(client, _upload(client, "plate_clear.jpg", "passport_no_serial.pdf"))
    assert data["crosscheck"]["serial_number"]["status"] == cc.STATUS_SINGLE
    assert data["final"]["serial_number"]["value"] == "24-07815"
    assert any(f.startswith("Заводской (серийный) номер: есть только в одном источнике")
               for f in data["flags"])


@needs_tesseract
def test_choice_for_pending_field_is_saved_and_journaled(client):
    app_id = _upload(client, "plate_serial_mismatch.jpg", "passport_text.pdf")
    resp = client.post(f"/applications/{app_id}/review",
                       data={"source__serial_number": "document", "field__serial_number": ""})
    assert resp.status_code == 200
    assert "Сохранено изменений: 1" in resp.text
    final = _fields(client, app_id)["final"]["serial_number"]
    assert final["value"] == "24-07815"
    assert final["origin"] == "chosen_document"
    assert final["manually_edited"] is True

    journal = client.get(f"/api/applications/{app_id}").json()["journal"]
    edit = [j for j in journal if j["event"] == "field_edited"][-1]
    assert "«—» → «24-07815»" in edit["details"]
    assert "выбрано: из документа" in edit["details"]


@needs_tesseract
def test_choice_from_photo_and_invalid_source(client):
    app_id = _upload(client, "plate_serial_mismatch.jpg", "passport_text.pdf")
    photo_id = next(e["file_id"] for e in _fields(client, app_id)["extracted"] if e["kind"] == "photo")
    client.post(f"/applications/{app_id}/review", data={"source__serial_number": f"photo:{photo_id}"})
    final = _fields(client, app_id)["final"]["serial_number"]
    assert (final["value"], final["origin"]) == ("24-09156", "chosen_photo")

    bad = client.post(f"/applications/{app_id}/review", data={"source__serial_number": "photo:9999"})
    assert bad.status_code == 400
