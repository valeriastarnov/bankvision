"""Предварительная оценка по сценариям — полная цепочка приложения во временной папке.

Загрузка паспорта и фото → качество, OCR, извлечение, сверка, итоговые значения
по умолчанию (без вмешательства сотрудника) → оценка по data/analogs.csv.

Запуск (PowerShell):  .\\.venv\\Scripts\\python.exe -m tests.run_valuation_eval > valuation.md
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services import valuation
from tests.generate_fixtures import FIXTURES_DIR
from tests.run_crosscheck_eval import SCENARIOS


def _rub(x: float | None) -> str:
    return "—" if x is None else f"{x:,.0f}".replace(",", " ")


def main() -> str:
    tmp = Path(tempfile.mkdtemp(prefix="bankvision_valuation_"))
    settings = Settings(data_dir=tmp, upload_dir=tmp / "uploads", db_path=tmp / "eval.db")
    client = TestClient(create_app(settings))

    out = ["| Сценарий | Модель (итог) | Год (итог) | Уровень | По модели / по классу | "
           "Медиана, ₽ | P25–P75, ₽ | Флаги (кроме постоянного «состояние не оценено») |",
           "|---|---|---|---|---|---|---|---|"]
    details = []
    for title, doc, photo in SCENARIOS:
        files = [("photos", (photo, (FIXTURES_DIR / photo).read_bytes(), "image/jpeg")),
                 ("document", (doc, (FIXTURES_DIR / doc).read_bytes(), "application/pdf"))]
        app_id = client.post("/api/applications", files=files).json()["application"]["id"]
        data = client.get(f"/api/applications/{app_id}/valuation").json()
        obj, r = data["object"], data["valuation"]
        assert valuation.FLAG_CONDITION in r["flags"]
        other_flags = [f for f in r["flags"] if f != valuation.FLAG_CONDITION]
        rng = f"{_rub(r['p25_rub'])} – {_rub(r['p75_rub'])}" if r["has_range"] else "не построен"
        out.append(f"| {title} | {obj['model'] or '—'} | {obj['year'] or '—'} | {r['level_label']} | "
                   f"{r['exact_count']} / {r['class_count']} | {_rub(r['median_rub'])} | {rng} | "
                   f"{'; '.join(other_flags) or '—'} |")
        if title.startswith("1. Корректные"):
            details += [f"#### Использованные аналоги: {title}", "",
                        "| ID | Модель | Год | Цена, ₽ | Разница лет | Коэффициент | Скорректированная, ₽ |",
                        "|---|---|---|---|---|---|---|"]
            for a in r["analogs"]:
                diff = "—" if a["years_diff"] is None else f"{a['years_diff']:+d}"
                details.append(f"| {a['id']} | {a['model']} | {a['year']} | {_rub(a['price_rub'])} | "
                               f"{diff} | {a['age_factor']:.4f} | {_rub(a['adjusted_price_rub'])} |")
    return "\n".join(out + [""] + details)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(main())
