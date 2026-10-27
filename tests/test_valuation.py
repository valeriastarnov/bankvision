"""Итерация 4: предварительная оценка по синтетическим аналогам."""
from __future__ import annotations

import pandas as pd
import pytest

from app.config import Settings
from app.services import valuation as v
from tests.generate_fixtures import FIXTURES_DIR
from tests.test_quality_ocr import needs_tesseract

S = Settings()
LATHE = "Станок токарно-винторезный"


def _analogs(rows: list[tuple]) -> pd.DataFrame:
    """rows: (модель, год, цена, тип, диаметр, РМЦ)."""
    return pd.DataFrame([
        {"id": f"T{i}", "equipment_type": t, "manufacturer": "ТЕСТ-ДЕМО", "model": m, "year": y,
         "max_diameter_mm": d, "center_distance_mm": c, "price_rub": p,
         "listing_date": "2026-01-01", "source": "synthetic"}
        for i, (m, y, p, t, d, c) in enumerate(rows)
    ])


def _obj(**kw) -> v.ObjectData:
    base = {"equipment_type": LATHE, "model": "ТС-1640М", "year": 2018,
            "max_diameter_mm": 400.0, "center_distance_mm": 1000.0}
    return v.ObjectData(**(base | kw))


# ---------- формулы ----------

def test_age_factor_formula():
    d = 0.07
    assert v.age_factor(2018, 2018, d) == pytest.approx(1.0)
    assert v.age_factor(2020, 2018, d) == pytest.approx(0.93 ** 2)  # аналог новее → дешевле
    assert v.age_factor(2015, 2018, d) == pytest.approx(0.93 ** -3)  # аналог старше → дороже
    assert v.age_factor(2020, None, d) == 1.0  # год объекта неизвестен — без поправки


def test_median_and_percentiles_on_known_set():
    median, p25, p75 = v.summarize([100, 200, 300, 400])
    assert (median, p25, p75) == (250.0, 175.0, 325.0)
    median, p25, p75 = v.summarize([1, 2, 3, 4, 5])
    assert (median, p25, p75) == (3.0, 2.0, 4.0)


def test_adjusted_prices_feed_the_statistics():
    analogs = _analogs([("ТС-1640М", 2018, 1_000_000, LATHE, 400, 1000),
                        ("ТС-1640М", 2019, 1_000_000, LATHE, 400, 1000),
                        ("ТС-1640М", 2017, 1_000_000, LATHE, 400, 1000)])
    r = v.value_object(_obj(), analogs, S)
    adjusted = sorted(a.adjusted_price_rub for a in r.analogs)
    assert adjusted == [930_000, 1_000_000, 1_075_000]  # 1e6×0.93, 1e6, 1e6/0.93 (округл. до 1000)
    assert r.median_rub == 1_000_000
    for a in r.analogs:
        assert a.adjusted_price_rub == pytest.approx(a.price_rub * a.age_factor, abs=500)


# ---------- уровни подбора на реальной синтетической базе ----------

@pytest.fixture(scope="module")
def base() -> pd.DataFrame:
    return v.load_analogs(S.analogs_path)


def test_analogs_base_is_synthetic_and_sized(base):
    assert 30 <= len(base) <= 40
    assert set(base["source"]) == {"synthetic"}
    assert base["id"].is_unique


def test_exact_model_analogs(base):
    r = v.value_object(_obj(), base, S)
    assert r.level == v.LEVEL_MODEL
    assert r.exact_count == len(r.analogs) >= S.valuation_min_analogs
    assert r.has_range and r.p25_rub <= r.median_rub <= r.p75_rub
    assert v.FLAG_CONDITION in r.flags
    assert r.disclaimer == v.DISCLAIMER


def test_model_found_from_ocr_latin_spelling(base):
    latin = v.value_object(_obj(model="TC-1640M"), base, S)
    cyrillic = v.value_object(_obj(model="ТС-1640М"), base, S)
    assert latin.level == v.LEVEL_MODEL
    assert [a.id for a in latin.analogs] == [a.id for a in cyrillic.analogs]
    # В базе модель записана тремя способами — все находятся.
    assert {a.model for a in latin.analogs} == {"ТС-1640М", "TC-1640M", "ТС 1640М"}


def test_only_class_analogs(base):
    r = v.value_object(_obj(model="НЕТ-ТАКОЙ-1"), base, S)
    assert r.level == v.LEVEL_CLASS
    assert r.exact_count == 0
    assert v.FLAG_CLASS_LEVEL in r.flags
    for a in r.analogs:
        assert a.equipment_type == LATHE  # «токарный с ЧПУ» с тем же диаметром отсечён
    assert r.has_range


def test_scenario_5_no_analogs(base):
    obj = v.ObjectData("Станок токарный", "ЛТ-3200Ф", 2021, 320.0, 750.0)
    r = v.value_object(obj, base, S)
    assert r.level == v.LEVEL_NONE
    assert r.analogs == [] and not r.has_range
    assert v.FLAG_NO_ANALOGS in r.flags
    assert v.FLAG_CONDITION in r.flags


def test_base_has_no_class_analog_for_unknown_model_even_by_diameter(base):
    # Независимо от типа: в пределах допуска 320/750 записей нет.
    near = base[(abs(base["max_diameter_mm"] - 320) <= 32) & (abs(base["center_distance_mm"] - 750) <= 150)]
    assert near.empty


def test_fewer_than_n_analogs_no_range():
    analogs = _analogs([("ТС-1640М", 2018, 1_000_000, LATHE, 400, 1000),
                        ("ТС-1640М", 2019, 1_100_000, LATHE, 400, 1000)])
    r = v.value_object(_obj(), analogs, S)
    assert r.level == v.LEVEL_MODEL and len(r.analogs) == 2
    assert not r.has_range
    assert v.FLAG_INSUFFICIENT.format(found=2, minimum=3) in r.flags


def test_min_analogs_from_settings():
    analogs = _analogs([("ТС-1640М", 2018, 1_000_000, LATHE, 400, 1000),
                        ("ТС-1640М", 2019, 1_100_000, LATHE, 400, 1000)])
    assert v.value_object(_obj(), analogs, Settings(valuation_min_analogs=2)).has_range


def test_class_used_when_exact_below_n():
    analogs = _analogs([("ТС-1640М", 2018, 1_000_000, LATHE, 400, 1000),
                        ("ДРУГАЯ-1", 2018, 900_000, LATHE, 390, 950),
                        ("ДРУГАЯ-2", 2018, 950_000, LATHE, 410, 1100),
                        ("ДАЛЕКО-1", 2018, 3_000_000, LATHE, 630, 2000)])
    r = v.value_object(_obj(), analogs, S)
    assert r.level == v.LEVEL_CLASS and r.exact_count == 1 and r.class_count == 3
    assert "ДАЛЕКО-1" not in {a.model for a in r.analogs}


def test_unknown_year_no_adjustment_and_flag(base):
    r = v.value_object(_obj(year=None), base, S)
    assert v.FLAG_YEAR_UNKNOWN in r.flags
    assert all(a.age_factor == 1.0 and a.years_diff is None for a in r.analogs)
    assert all(a.adjusted_price_rub == round(a.price_rub, -3) for a in r.analogs)


def test_class_search_needs_characteristics(base):
    r = v.value_object(_obj(model="НЕТ-ТАКОЙ-1", max_diameter_mm=None), base, S)
    assert r.level == v.LEVEL_NONE
    assert v.FLAG_NO_CLASS_DATA in r.flags


def test_object_from_final_fields():
    finals = {"model": {"value": "TC-1640M"}, "year": {"value": "2018"},
              "max_diameter_mm": {"value": "400"}, "center_distance_mm": {"value": None},
              "equipment_type": {"value": LATHE}}
    obj = v.ObjectData.from_final(finals)
    assert (obj.model, obj.year, obj.max_diameter_mm, obj.center_distance_mm) == ("TC-1640M", 2018, 400.0, None)


# ---------- сквозные сценарии ----------

def _upload(client, photo: str, document: str) -> int:
    files = [("photos", (photo, (FIXTURES_DIR / photo).read_bytes(), "image/jpeg")),
             ("document", (document, (FIXTURES_DIR / document).read_bytes(), "application/pdf"))]
    resp = client.post("/api/applications", files=files)
    assert resp.status_code == 201, resp.text
    return resp.json()["application"]["id"]


@needs_tesseract
def test_api_and_page_exact_scenario(client):
    app_id = _upload(client, "plate_clear.jpg", "passport_text.pdf")
    data = client.get(f"/api/applications/{app_id}/valuation").json()["valuation"]
    assert data["level"] == v.LEVEL_MODEL and data["has_range"]
    page = client.get(f"/applications/{app_id}/valuation").text
    assert v.DISCLAIMER in page
    assert "точная модель" in page
    assert "Скорректированная цена" in page


@needs_tesseract
def test_api_scenario_5_no_analogs(client):
    app_id = _upload(client, "plate_unknown_model.jpg", "passport_unknown_model.pdf")
    data = client.get(f"/api/applications/{app_id}/valuation").json()["valuation"]
    assert data["level"] == v.LEVEL_NONE and not data["has_range"]
    page = client.get(f"/applications/{app_id}/valuation").text
    assert "Диапазон стоимости не построен" in page
    assert v.DISCLAIMER in page
