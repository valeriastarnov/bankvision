"""Извлечение полей на всех синтетических фикстурах и сравнение с эталоном.

Цепочка та же, что в приложении: текст (текстовый слой PDF или OCR) → extract_fields.
Сравнение строгое (точное равенство строк), ошибки OCR не исправляются — это итерация 3.

Запуск (PowerShell):  .\\.venv\\Scripts\\python.exe -m tests.run_extract_eval > extract_eval.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.config import Settings
from app.services import extract, ocr, pdf
from tests.generate_fixtures import FIXTURES_DIR

STATUS_OK = "совпадает"
STATUS_WRONG = "отличается"
STATUS_MISSING = "не найдено"
STATUS_EXTRA = "лишнее (нет в эталоне)"
STATUS_ABSENT = "нет и не найдено"


def fixture_text(path: Path, settings: Settings) -> str:
    if path.suffix == ".pdf":
        return pdf.extract_pdf(path, settings).text
    return ocr.ocr_image(path, settings).text


def compare(expected: str | None, got: extract.ExtractedField | None) -> str:
    if expected is None:
        return STATUS_ABSENT if got is None else STATUS_EXTRA
    if got is None:
        return STATUS_MISSING
    return STATUS_OK if got.value == expected else STATUS_WRONG


def evaluate(fixtures: Path, settings: Settings) -> list[dict]:
    results = []
    for ref_path in sorted(fixtures.glob("*.json")):
        ref = json.loads(ref_path.read_text(encoding="utf-8"))
        data_path = next(p for p in fixtures.glob(f"{ref_path.stem}.*") if p.suffix != ".json")
        is_photo = ref["kind"] == "plate_photo"
        found = extract.extract_fields(
            fixture_text(data_path, settings), source_name=data_path.name, allow_heuristics=is_photo
        )
        rows = []
        for field in extract.FIELDS:
            expected = ref["fields"].get(field)
            got = found.get(field)
            rows.append({"field": field, "expected": expected, "got": got,
                         "status": compare(expected, got)})
        results.append({"name": data_path.name, "scenario": ref["scenario"], "rows": rows})
    return results


def to_markdown(results: list[dict]) -> str:
    out = ["| Фикстура | Сценарий | Совпадает / есть в эталоне | Отличается | Не найдено | Лишнее |",
           "|---|---|---|---|---|---|"]
    for r in results:
        count = {s: sum(1 for row in r["rows"] if row["status"] == s)
                 for s in (STATUS_OK, STATUS_WRONG, STATUS_MISSING, STATUS_EXTRA)}
        present = sum(1 for row in r["rows"] if row["expected"] is not None)
        out.append(f"| {r['name']} | {r['scenario']} | {count[STATUS_OK]}/{present} | "
                   f"{count[STATUS_WRONG]} | {count[STATUS_MISSING]} | {count[STATUS_EXTRA]} |")
    out.append("")
    for r in results:
        out += [f"#### {r['name']} ({r['scenario']})", "",
                "| Поле | Эталон | Извлечено | Статус | Исходная строка | Метод |",
                "|---|---|---|---|---|---|"]
        for row in r["rows"]:
            got = row["got"]
            fragment = f"`{got.fragment.replace('|', '/')}`" if got else "—"
            out.append(f"| {row['field']} | {row['expected'] or '—'} | {got.value if got else '—'} | "
                       f"{row['status']} | {fragment} | {got.method if got else '—'} |")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(to_markdown(evaluate(FIXTURES_DIR, Settings.from_env())))
