"""Статусы сверки документа и фото по сценариям на синтетических фикстурах.

Цепочка как в приложении: качество фото → текст (PDF/OCR) → extract_fields → crosscheck.

Запуск (PowerShell):  .\\.venv\\Scripts\\python.exe -m tests.run_crosscheck_eval > crosscheck.md
"""
from __future__ import annotations

import sys

from app.config import Settings
from app.services import crosscheck, extract, image_quality
from tests.generate_fixtures import FIXTURES_DIR
from tests.run_extract_eval import fixture_text

# (сценарий CLAUDE.md, документ, фото)
SCENARIOS = [
    ("1. Корректные фото и полный документ", "passport_text.pdf", "plate_clear.jpg"),
    ("1. Скан паспорта без текстового слоя", "passport_scan.pdf", "plate_clear.jpg"),
    ("1. Фото с наклоном", "passport_text.pdf", "plate_skewed.jpg"),
    ("2. Размытое фото", "passport_text.pdf", "plate_blurred.jpg"),
    ("2. Тёмное фото", "passport_text.pdf", "plate_dark.jpg"),
    ("2. Тёмное и размытое фото", "passport_text.pdf", "plate_dark_blurred.jpg"),
    ("3. Нет серийного номера в паспорте", "passport_no_serial.pdf", "plate_clear.jpg"),
    ("4. Номер на шильдике отличается от паспорта", "passport_text.pdf", "plate_serial_mismatch.jpg"),
    ("5. Модель вне базы аналогов", "passport_unknown_model.pdf", "plate_unknown_model.jpg"),
    ("7. Паспорт с минимумом данных", "passport_minimal.pdf", "plate_clear.jpg"),
]

SHORT = {
    crosscheck.STATUS_MATCH: "совпадает",
    crosscheck.STATUS_NORMALIZED: "после нормализации",
    crosscheck.STATUS_MISMATCH: "**расходится**",
    crosscheck.STATUS_NOT_CONFIRMED: "**не подтверждено**",
    crosscheck.STATUS_SINGLE: "один источник",
    crosscheck.STATUS_MISSING: "нет нигде",
}


def _rows(name: str, kind: str, file_id: int, settings: Settings) -> list[dict]:
    found = extract.extract_fields(fixture_text(FIXTURES_DIR / name, settings), file_id, name,
                                   allow_heuristics=kind == "photo")
    return [f.to_dict() | {"file_id": file_id, "kind": kind} for f in found.values()]


def evaluate(settings: Settings) -> list[dict]:
    results = []
    for title, doc, photo in SCENARIOS:
        q = image_quality.assess(FIXTURES_DIR / photo, settings)
        extracted = _rows(doc, "document", 1, settings) + _rows(photo, "photo", 2, settings)
        checks = crosscheck.crosscheck(extracted, {2: q.flags}, settings)
        results.append({"title": title, "doc": doc, "photo": photo, "checks": checks,
                        "flags": crosscheck.flags(checks, {photo: q.flags})})
    return results


def to_markdown(results: list[dict]) -> str:
    fields = list(extract.FIELDS)
    out = ["| Сценарий | Документ | Фото | " + " | ".join(fields) + " |",
           "|---|---|---|" + "---|" * len(fields)]
    for r in results:
        cells = [SHORT[r["checks"][f].status] for f in fields]
        out.append(f"| {r['title']} | {r['doc']} | {r['photo']} | " + " | ".join(cells) + " |")
    out.append("")
    for r in results:
        out.append(f"#### {r['title']}")
        out.append("")
        details = []
        for c in r["checks"].values():
            if c.status in (crosscheck.STATUS_MATCH, crosscheck.STATUS_MISSING):
                continue
            doc_value = c.document["value"] if c.document else "—"
            photo_value = ", ".join(p["value"] for p in c.photos) or "—"
            extra = "; ".join(c.normalization + ([c.note] if c.note else []))
            details.append(f"- {c.label}: {c.status_label} — документ «{doc_value}», "
                           f"фото «{photo_value}»" + (f" ({extra})" if extra else ""))
        out += details or ["- все поля, найденные в обоих источниках, совпадают дословно"]
        out.append("")
        out.append("Флаги заявки: " + ("; ".join(r["flags"]) if r["flags"] else "нет"))
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(to_markdown(evaluate(Settings.from_env())))
