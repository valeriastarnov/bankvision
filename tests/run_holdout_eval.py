"""Однократная проверка метрики качества на отложенной выборке tests/fixtures/holdout.

Параметры метрики берутся из app/config.py как есть. Для пограничных искажений
«правильного» флага нет, поэтому рядом выводится внешний ориентир — прочитал ли OCR
заводской номер и год точно (после удаления пробелов, без нормализации OCR-ошибок).

Запуск (PowerShell):  .\\.venv\\Scripts\\python.exe -m tests.run_holdout_eval > holdout.md
"""
from __future__ import annotations

import json
import re
import sys

from app.config import Settings
from app.services import image_quality, ocr
from tests.generate_fixtures import HOLDOUT_DIR


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def main() -> str:
    settings = Settings.from_env()
    rows = ["| Фото | Искажение | Размер | Резкость | Яркость | Флаги | OCR: номер точно | OCR: год точно |",
            "|---|---|---|---|---|---|---|---|"]
    for path in sorted(HOLDOUT_DIR.glob("*.jpg")):
        ref = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        q = image_quality.assess(path, settings)
        text = ocr.ocr_image(path, settings).text
        fields = ref["fields"]
        distortion = {k: v for k, v in ref["distortions"].items() if k != "noise_sigma"} or "—"
        rows.append(
            f"| {path.name} | `{json.dumps(distortion, ensure_ascii=False)}` | {q.width}×{q.height} | "
            f"{q.blur_score} | {q.brightness} | {', '.join(q.flags) or '—'} | "
            f"{'да' if fields['serial_number'] in _squash(text) else 'нет'} | "
            f"{'да' if fields['year'] in text else 'нет'} |"
        )
    return "\n".join(rows)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(main())
