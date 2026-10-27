"""Предварительный диапазон стоимости по синтетической базе аналогов.

Методика и ограничения — docs/methodology_valuation.md. Результат — демонстрационный:
база аналогов синтетическая, поправка на возраст — допущение, а не рыночные данные.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from app.config import Settings
from app.services.crosscheck import normalize

DISCLAIMER = "Демонстрационная оценка на синтетических данных. Не является рыночной стоимостью."

LEVEL_MODEL = "model"
LEVEL_CLASS = "class"
LEVEL_NONE = "none"
LEVEL_LABELS = {
    LEVEL_MODEL: "точная модель",
    LEVEL_CLASS: "тот же класс (тип и основные характеристики)",
    LEVEL_NONE: "аналоги не найдены",
}

FLAG_CONDITION = "Техническое состояние и наработка не оценены: по фото и паспорту их определить нельзя."
FLAG_INSUFFICIENT = "Недостаточно аналогов: найдено {found} при минимуме {minimum}, диапазон не построен."
FLAG_NO_ANALOGS = "Нет аналогов ни по модели, ни по классу: диапазон не построен."
FLAG_YEAR_UNKNOWN = "Год выпуска объекта неизвестен: поправка на возраст не применялась."
FLAG_CLASS_LEVEL = "Аналоги подобраны по классу, а не по модели: разброс может быть выше."
FLAG_NO_CLASS_DATA = ("Для подбора по классу не хватает данных объекта (тип, диаметр обработки "
                      "или расстояние между центрами).")


@dataclass
class ObjectData:
    """Данные объекта оценки — итоговые значения полей заявки."""
    equipment_type: str | None = None
    model: str | None = None
    year: int | None = None
    max_diameter_mm: float | None = None
    center_distance_mm: float | None = None

    @classmethod
    def from_final(cls, finals: dict[str, dict]) -> "ObjectData":
        def value(name: str) -> str | None:
            return (finals.get(name) or {}).get("value")

        def number(name: str) -> float | None:
            try:
                return float(value(name))
            except (TypeError, ValueError):
                return None

        year = number("year")
        return cls(value("equipment_type"), value("model"),
                   int(year) if year is not None else None,
                   number("max_diameter_mm"), number("center_distance_mm"))


@dataclass
class UsedAnalog:
    id: str
    equipment_type: str
    manufacturer: str
    model: str
    year: int
    listing_date: str
    source: str
    price_rub: float
    years_diff: int | None  # год аналога − год объекта
    age_factor: float
    adjusted_price_rub: float


@dataclass
class ValuationResult:
    level: str
    analogs: list[UsedAnalog]
    median_rub: float | None
    p25_rub: float | None
    p75_rub: float | None
    flags: list[str]
    params: dict
    exact_count: int = 0
    class_count: int = 0
    disclaimer: str = DISCLAIMER
    level_label: str = field(init=False)

    def __post_init__(self) -> None:
        self.level_label = LEVEL_LABELS[self.level]

    @property
    def has_range(self) -> bool:
        return self.median_rub is not None

    def to_dict(self) -> dict:
        return asdict(self) | {"has_range": self.has_range}


# ---------- формулы ----------

def age_factor(analog_year: int, object_year: int | None, depreciation: float) -> float:
    """(1 − d)^(год аналога − год объекта). Аналог новее объекта → коэффициент < 1."""
    if object_year is None:
        return 1.0
    return (1 - depreciation) ** (analog_year - object_year)


def summarize(prices: list[float]) -> tuple[float, float, float]:
    """Медиана, P25, P75 (линейная интерполяция между точками, как numpy по умолчанию)."""
    arr = np.asarray(prices, dtype=float)
    p25, median, p75 = np.percentile(arr, [25, 50, 75])
    return float(median), float(p25), float(p75)


# ---------- подбор аналогов ----------

def load_analogs(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"id": str, "model": str, "listing_date": str})


def _within(value: float, target: float, tolerance: float) -> bool:
    return abs(value - target) <= tolerance * target


def find_exact(analogs: pd.DataFrame, model: str | None) -> pd.DataFrame:
    if not model:
        return analogs.iloc[0:0]
    target = normalize(model, "model")
    mask = analogs["model"].map(lambda m: normalize(str(m), "model") == target)
    return analogs[mask]


def find_class(analogs: pd.DataFrame, obj: ObjectData, settings: Settings) -> pd.DataFrame | None:
    """Аналоги того же класса; None — данных объекта для подбора по классу не хватает."""
    if not (obj.equipment_type and obj.max_diameter_mm and obj.center_distance_mm):
        return None
    target_type = normalize(obj.equipment_type, "equipment_type")
    same_type = analogs["equipment_type"].map(lambda t: normalize(str(t), "equipment_type") == target_type)
    candidates = analogs[same_type & analogs["max_diameter_mm"].notna() & analogs["center_distance_mm"].notna()]
    mask = candidates.apply(
        lambda r: _within(r["max_diameter_mm"], obj.max_diameter_mm, settings.class_diameter_tolerance)
        and _within(r["center_distance_mm"], obj.center_distance_mm, settings.class_center_distance_tolerance),
        axis=1,
    ) if len(candidates) else pd.Series(dtype=bool)
    return candidates[mask] if len(candidates) else candidates


def _used(rows: pd.DataFrame, obj: ObjectData, settings: Settings) -> list[UsedAnalog]:
    used = []
    for _, r in rows.sort_values(["year", "id"]).iterrows():
        factor = age_factor(int(r["year"]), obj.year, settings.valuation_annual_depreciation)
        used.append(UsedAnalog(
            id=r["id"], equipment_type=r["equipment_type"], manufacturer=r["manufacturer"],
            model=r["model"], year=int(r["year"]), listing_date=r["listing_date"], source=r["source"],
            price_rub=float(r["price_rub"]),
            years_diff=int(r["year"]) - obj.year if obj.year is not None else None,
            age_factor=round(factor, 4),
            adjusted_price_rub=round(float(r["price_rub"]) * factor, -3),
        ))
    return used


def value_object(obj: ObjectData, analogs: pd.DataFrame, settings: Settings) -> ValuationResult:
    minimum = settings.valuation_min_analogs
    params = {
        "annual_depreciation": settings.valuation_annual_depreciation,
        "min_analogs": minimum,
        "class_diameter_tolerance": settings.class_diameter_tolerance,
        "class_center_distance_tolerance": settings.class_center_distance_tolerance,
    }
    flags = [FLAG_CONDITION]
    if obj.year is None:
        flags.append(FLAG_YEAR_UNKNOWN)

    exact = find_exact(analogs, obj.model)
    by_class = find_class(analogs, obj, settings)
    class_count = len(by_class) if by_class is not None else 0

    if len(exact) >= minimum:
        level, rows = LEVEL_MODEL, exact
    elif class_count >= minimum:
        level, rows = LEVEL_CLASS, by_class
        flags.append(FLAG_CLASS_LEVEL)
    elif len(exact):
        level, rows = LEVEL_MODEL, exact
    elif class_count:
        level, rows = LEVEL_CLASS, by_class
        flags.append(FLAG_CLASS_LEVEL)
    else:
        level, rows = LEVEL_NONE, analogs.iloc[0:0]

    if by_class is None and len(exact) < minimum:
        flags.append(FLAG_NO_CLASS_DATA)

    used = _used(rows, obj, settings)
    median = p25 = p75 = None
    if level == LEVEL_NONE:
        flags.append(FLAG_NO_ANALOGS)
    elif len(used) < minimum:
        flags.append(FLAG_INSUFFICIENT.format(found=len(used), minimum=minimum))
    else:
        median, p25, p75 = (round(v, -3) for v in summarize([a.adjusted_price_rub for a in used]))

    return ValuationResult(level, used, median, p25, p75, flags, params,
                           exact_count=len(exact), class_count=class_count)


def value_application(finals: dict[str, dict], settings: Settings) -> tuple[ObjectData, ValuationResult]:
    obj = ObjectData.from_final(finals)
    return obj, value_object(obj, load_analogs(settings.analogs_path), settings)
