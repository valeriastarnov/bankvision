"""Генератор синтетической базы аналогов data/analogs.csv.

ВНИМАНИЕ: все записи вымышленные. Производители, модели, цены и даты объявлений
не взяты с рынка и не отражают его. Цены строятся произвольной формулой ниже —
только для демонстрации работы алгоритма оценки.

Запуск (PowerShell):  .\\.venv\\Scripts\\python.exe data\\generate_analogs.py
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent / "analogs.csv"
SEED = 20261027
COLUMNS = ["id", "equipment_type", "manufacturer", "model", "year", "max_diameter_mm",
           "center_distance_mm", "price_rub", "listing_date", "source"]

LATHE = "Станок токарно-винторезный"

# Условная «цена нового» по модели — произвольное число, не рыночная цена.
# (тип, производитель, модель, диаметр, РМЦ, условная цена нового, написание модели в записях)
GROUPS = [
    # Точные аналоги модели объекта; модель записана по-разному, как в реальных объявлениях.
    (LATHE, "ТОКАРЬПРОМ-СИНТЕЗ", "ТС-1640М", 400, 1000, 4_200_000,
     ["ТС-1640М", "ТС-1640М", "ТС-1640М", "TC-1640M", "TC-1640M", "ТС 1640М"]),
    # Тот же класс (токарно-винторезный, диаметр ~400, РМЦ ~1000), другие модели.
    (LATHE, "РЕЗЕЦМАШ-ДЕМО", "ТВ-400Д", 400, 1000, 3_800_000, ["ТВ-400Д"] * 4),
    (LATHE, "ОБРАБОТКА-СИНТЕЗ", "ОС-16К", 380, 900, 3_500_000, ["ОС-16К"] * 4),
    (LATHE, "ПРИМЕР-СТАНОК", "ПТ-42-1100", 420, 1100, 4_600_000, ["ПТ-42-1100"] * 4),
    # Токарно-винторезные вне класса объекта.
    (LATHE, "РЕЗЕЦМАШ-ДЕМО", "ТВ-500Д", 500, 1500, 5_900_000, ["ТВ-500Д"] * 3),
    (LATHE, "ОБРАБОТКА-СИНТЕЗ", "ОС-63К", 630, 2000, 8_700_000, ["ОС-63К"] * 3),
    (LATHE, "ПРИМЕР-СТАНОК", "ПТ-25-500", 250, 500, 1_900_000, ["ПТ-25-500"] * 2),
    # Другие типы оборудования (в том числе токарный с ЧПУ с тем же диаметром — должен
    # отсекаться фильтром по типу).
    ("Станок токарный с ЧПУ", "ЦИФРОСТАН-ДЕМО", "ЦТ-400", 400, 1000, 9_500_000, ["ЦТ-400"] * 2),
    ("Станок вертикально-фрезерный", "ФРЕЗА-СИНТЕЗ", "ФВ-32", None, None, 3_100_000, ["ФВ-32"] * 3),
    ("Станок радиально-сверлильный", "СВЕРЛО-ДЕМО", "РС-50", None, None, 1_600_000, ["РС-50"] * 3),
]

DEPRECIATION_IN_GENERATOR = 0.07  # произвольно; не связано с рынком
PRICE_NOISE_SIGMA = 0.15  # разброс цен объявлений, произвольно


def generate(out: Path = OUT) -> list[dict]:
    rng = np.random.default_rng(SEED)
    rows = []
    for eq_type, manufacturer, _, diameter, distance, new_price, spellings in GROUPS:
        for spelling in spellings:
            year = int(rng.integers(2008, 2025))
            listing = date(2025, 1, 1) + timedelta(days=int(rng.integers(0, 640)))
            age = max(listing.year - year, 0)
            price = new_price * (1 - DEPRECIATION_IN_GENERATOR) ** age * rng.lognormal(0, PRICE_NOISE_SIGMA)
            rows.append({
                "id": f"A{len(rows) + 1:03d}",
                "equipment_type": eq_type,
                "manufacturer": manufacturer,
                "model": spelling,
                "year": year,
                "max_diameter_mm": diameter if diameter is not None else "",
                "center_distance_mm": distance if distance is not None else "",
                "price_rub": int(round(price, -4)),
                "listing_date": listing.isoformat(),
                "source": "synthetic",
            })
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


if __name__ == "__main__":
    print(f"{len(generate())} записей -> {OUT}")
