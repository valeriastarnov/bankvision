"""Итерация 5: индекс полноты и согласованности, маршрутизация, отчёт.

Ожидаемые маршруты по сценариям зафиксированы до прогона (см. план итерации 5).
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.services import crosscheck as cc
from app.services import routing, scoring
from app.services import valuation as v
from tests.generate_fixtures import FIXTURES_DIR
from tests.test_quality_ocr import needs_tesseract

S = Settings()
EXPERT, REQUEST, READY = routing.ROUTE_EXPERT, routing.ROUTE_REQUEST, routing.ROUTE_READY


def _upload(client, photo: str, document: str, document_bytes: bytes | None = None):
    files = [("photos", (photo, (FIXTURES_DIR / photo).read_bytes(), "image/jpeg")),
             ("document", (document, document_bytes or (FIXTURES_DIR / document).read_bytes(),
                           "application/pdf"))]
    return client.post("/api/applications", files=files)


def _assessment(client, photo: str, document: str, document_bytes: bytes | None = None) -> tuple[int, dict]:
    resp = _upload(client, photo, document, document_bytes)
    assert resp.status_code == 201, resp.text
    app_id = resp.json()["application"]["id"]
    return app_id, client.get(f"/api/applications/{app_id}/assessment").json()


# ---------- 7 обязательных сценариев ----------

SCENARIOS = [
    ("1. корректные фото и документ", "plate_clear.jpg", "passport_text.pdf", READY),
    ("2. размытое фото", "plate_blurred.jpg", "passport_text.pdf", REQUEST),
    ("2. тёмное фото", "plate_dark.jpg", "passport_text.pdf", READY),
    ("2. тёмное и размытое фото", "plate_dark_blurred.jpg", "passport_text.pdf", REQUEST),
    ("3. нет серийного номера", "plate_clear.jpg", "passport_no_serial.pdf", REQUEST),
    ("4. расхождение номера", "plate_serial_mismatch.jpg", "passport_text.pdf", EXPERT),
    ("5. нет аналогов", "plate_unknown_model.jpg", "passport_unknown_model.pdf", EXPERT),
    ("7. недостаточная полнота", "plate_clear.jpg", "passport_minimal.pdf", REQUEST),
]


@needs_tesseract
@pytest.mark.parametrize("title, photo, document, expected", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_scenario_route(client, title, photo, document, expected):
    _, data = _assessment(client, photo, document)
    route = data["route"]
    assert route["route"] == expected, (route["reasons"], data["score"]["total"])
    assert route["reasons"]
    assert "одобр" not in route["label"].lower()


@needs_tesseract
def test_scenario_2_blurred_requests_reshoot_of_that_photo(client):
    _, data = _assessment(client, "plate_blurred.jpg", "passport_text.pdf")
    assert any("Переснять фото шильдика «plate_blurred.jpg»" in r for r in data["route"]["requests"])


@needs_tesseract
def test_scenario_3_requests_full_passport(client):
    _, data = _assessment(client, "plate_clear.jpg", "passport_no_serial.pdf")
    assert any("паспорт" in r and "Заводской (серийный) номер" in r for r in data["route"]["requests"])


def test_scenario_6_wrong_format_rejected_at_upload(client):
    files = [("photos", ("plate.gif", b"GIF89a....", "image/gif")),
             ("document", ("passport.pdf", (FIXTURES_DIR / "passport_text.pdf").read_bytes(), "application/pdf"))]
    resp = client.post("/api/applications", files=files)
    assert resp.status_code == 400
    assert client.get("/api/applications/1/assessment").status_code == 404


@needs_tesseract
def test_scenario_6_broken_pdf_requests_document_again(client):
    _, data = _assessment(client, "plate_clear.jpg", "broken.pdf", b"%PDF-1.4\n" + b"\x00garbage" * 50)
    assert data["document_error"]
    assert data["route"]["route"] == REQUEST
    assert any("Загрузить документ" in r for r in data["route"]["requests"])


# ---------- жёсткие правила раньше индекса ----------

@needs_tesseract
def test_serial_mismatch_goes_to_expert_even_with_high_index(client):
    app_id, data = _assessment(client, "plate_serial_mismatch.jpg", "passport_text.pdf")
    # Сотрудник выбрал значение — индекс растёт (поле заполнено), но маршрут остаётся экспертным.
    client.post(f"/applications/{app_id}/review", data={"source__serial_number": "document"})
    data = client.get(f"/api/applications/{app_id}/assessment").json()
    assert data["score"]["total"] >= S.routing_score_threshold + 20
    assert data["route"]["route"] == EXPERT
    assert any("расходится" in r for r in data["route"]["reasons"])


@needs_tesseract
def test_manual_choice_does_not_clear_expert_route_and_is_reported(client):
    app_id, _ = _assessment(client, "plate_serial_mismatch.jpg", "passport_text.pdf")
    client.post(f"/applications/{app_id}/review",
                data={"source__serial_number": "manual", "field__serial_number": "24-07815"})
    route = client.get(f"/api/applications/{app_id}/assessment").json()["route"]
    assert route["route"] == EXPERT
    assert any("сотрудник ввёл значение вручную «24-07815»" in r for r in route["resolutions"])
    page = client.get(f"/applications/{app_id}/report").text
    assert "Расхождения и как они разрешены" in page
    assert "сотрудник ввёл значение вручную" in page


def test_condition_flag_is_informational_only():
    checks = {n: cc.FieldCheck(n, n, cc.STATUS_MATCH, {"value": "x"}, [
        {"value": "x", "low_quality": False, "quality_flags": [], "source_name": "p.jpg",
         "comparison": {"equal": True}}]) for n in cc.KEY_FIELDS}
    finals = {n: {"value": "x"} for n in cc.KEY_FIELDS}
    val = v.ValuationResult(v.LEVEL_MODEL, [object()] * 3, 1.0, 1.0, 1.0, [v.FLAG_CONDITION], {})
    score = scoring.compute(checks, finals, {"p.jpg": []}, val, S)
    r = routing.decide(checks, finals, val, score, S)
    assert r.route == READY
    assert r.info == [v.FLAG_CONDITION]


def test_low_index_alone_sends_to_expert():
    checks = {n: cc.FieldCheck(n, n, cc.STATUS_MATCH, {"value": "x"}, [
        {"value": "x", "low_quality": True, "quality_flags": ["blurry"], "source_name": "p.jpg",
         "comparison": {"equal": True}}]) for n in cc.KEY_FIELDS}
    finals = {n: {"value": "x"} for n in cc.KEY_FIELDS}
    val = v.ValuationResult(v.LEVEL_MODEL, [object()] * 3, 1.0, 1.0, 1.0, [v.FLAG_CONDITION], {})
    score = scoring.compute(checks, finals, {"p.jpg": ["blurry"]}, val, Settings(routing_score_threshold=90))
    r = routing.decide(checks, finals, val, score, Settings(routing_score_threshold=90))
    assert r.route == EXPERT
    assert any("ниже порога 90" in x for x in r.reasons)


# ---------- индекс ----------

def _check(status, low=False):
    photos = [] if status in (cc.STATUS_MISSING,) else [
        {"value": "x", "low_quality": low, "quality_flags": ["blurry"] if low else [],
         "source_name": "p.jpg", "comparison": {"equal": status in (cc.STATUS_MATCH, cc.STATUS_NORMALIZED)}}]
    return cc.FieldCheck("f", "f", status, {"value": "x"}, photos)


def test_consistency_low_quality_match_weighs_less():
    good, _ = scoring.field_consistency(_check(cc.STATUS_MATCH), S)
    low, kind = scoring.field_consistency(_check(cc.STATUS_MATCH, low=True), S)
    assert good == 1.0 and low == S.score_consistency["match_low_quality_photo"] < good
    assert "флагом качества" in kind
    assert scoring.field_consistency(_check(cc.STATUS_MISMATCH), S)[0] == 0.0


def test_score_components_and_weights_from_config():
    checks = {n: _check(cc.STATUS_MATCH) for n in cc.KEY_FIELDS}
    finals = {n: {"value": "x"} for n in cc.KEY_FIELDS[:2]}  # 2 из 4 заполнены
    val = v.ValuationResult(v.LEVEL_NONE, [], None, None, None, [], {})
    score = scoring.compute(checks, finals, {"a.jpg": [], "b.jpg": ["too_dark"]}, val, S)
    contrib = {c.key: c.contribution for c in score.components}
    assert contrib == {"photo_quality": 10.0, "completeness": 15.0, "consistency": 30.0, "analogs": 0.0}
    assert score.total == 55.0 and score.max_total == 100
    weights = {"photo_quality": 10, "completeness": 10, "consistency": 10, "analogs": 70}
    alt = scoring.compute(checks, finals, {"a.jpg": []}, val, Settings(score_weights=weights))
    assert alt.total == 10 + 5 + 10 + 0


# ---------- отчёт ----------

REPORT_SECTIONS = ["1. Объект", "2. Источники", "3. Поля и сверка источников", "4. Правки сотрудника",
                   "5. Флаги", "6. Предварительная оценка", "7. Индекс и маршрут", "8. Ограничения"]


@needs_tesseract
def test_report_contains_all_sections(client):
    app_id, _ = _assessment(client, "plate_serial_mismatch.jpg", "passport_text.pdf")
    client.post(f"/applications/{app_id}/review", data={"source__serial_number": "document"})
    page = client.get(f"/applications/{app_id}/report").text
    for section in REPORT_SECTIONS:
        assert section in page, section
    for text in ["Система не принимает кредитное решение",
                 "Демонстрационная оценка на синтетических данных. Не является рыночной стоимостью.",
                 "Индекс полноты и согласованности данных", "Маршрут: Требуется экспертная проверка",
                 "plate_serial_mismatch.jpg", "Зав. №: 24-09156",  # источник и исходный фрагмент
                 "выбрано: из документа",  # правка из журнала
                 "Вклад", "синтетические"]:
        assert text in page, text
    assert "вероятност" not in page.lower().replace("не вероятность", "")
    assert "одобрено" not in page.lower()


def test_print_css_present():
    css = (S.analogs_path.parents[1] / "app" / "static" / "style.css").read_text(encoding="utf-8")
    assert "@media print" in css and "@page" in css
