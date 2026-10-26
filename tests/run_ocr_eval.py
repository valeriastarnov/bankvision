"""Прогон OCR по всем синтетическим фикстурам и сравнение с эталоном.

Сравнение строгое, без исправления типичных ошибок OCR (O/0, кириллица/латиница) —
это задача итерации 3. Поле считается найденным «точно», если эталонное значение
встречается в тексте после удаления пробелов (с учётом регистра). Иначе выводится
лучшее нечёткое совпадение rapidfuzz.partial_ratio и найденный фрагмент. Поля, которых
нет в эталонном тексте фикстуры, помечаются «нет в эталоне» и в подсчёт не входят.

Запуск (PowerShell):  .\\.venv\\Scripts\\python.exe -m tests.run_ocr_eval > ocr_eval.md
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from rapidfuzz import fuzz

from app.config import Settings
from app.services import image_quality, ocr, pdf
from tests.generate_fixtures import FIXTURES_DIR


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def reference_fields(ref: dict) -> dict[str, str]:
    f = ref["fields"]
    fields = {k: f[k] for k in ("manufacturer", "model", "serial_number", "year")}
    fields.update({f"characteristics.{k}": v for k, v in f["characteristics"].items()})
    return fields


def compare_field(value: str, text: str) -> tuple[str, float, str]:
    """(статус, балл 0–100, найденный фрагмент)."""
    if _squash(value) in _squash(text):
        return "точно", 100.0, value
    alignment = fuzz.partial_ratio_alignment(value, text)
    snippet = text[alignment.dest_start:alignment.dest_end] if alignment else ""
    score = alignment.score if alignment else 0.0
    return "не точно", round(score, 1), snippet


def evaluate(fixtures: Path, settings: Settings) -> list[dict]:
    results = []
    for ref_path in sorted(fixtures.glob("*.json")):
        ref = json.loads(ref_path.read_text(encoding="utf-8"))
        data_path = next(p for p in fixtures.glob(f"{ref_path.stem}.*") if p.suffix != ".json")
        started = time.perf_counter()
        entry = {"name": data_path.name, "variant": ref["variant"], "distortions": ref["distortions"]}
        if ref["kind"] == "plate_photo":
            entry["quality"] = image_quality.assess(data_path, settings).to_dict()
            r = ocr.ocr_image(data_path, settings)
            entry |= {"text": r.text, "source": "ocr", "confidence": r.confidence,
                      "ocr_variant": r.variant, "deskew_angle": r.deskew_angle}
            ref_text = "\n".join(ref["plate_text"])
        else:
            d = pdf.extract_pdf(data_path, settings)
            entry |= {"text": d.text, "source": ",".join(d.sources),
                      "confidence": d.pages[0].ocr_confidence}
            ref_text = "\n".join(line for line in ref["document_text"] if line)
        entry["seconds"] = round(time.perf_counter() - started, 1)
        entry["text_similarity"] = round(fuzz.ratio(_collapse(ref_text), _collapse(entry["text"])), 1)
        # Поле проверяется, только если оно реально присутствует в эталонном тексте фикстуры
        # (например, на шильдике нет диаметра обработки).
        entry["fields"] = {
            name: compare_field(value, entry["text"]) if _squash(value) in _squash(ref_text)
            else ("нет в эталоне", None, "")
            for name, value in reference_fields(ref).items()
        }
        entry["reference"] = reference_fields(ref)
        results.append(entry)
    return results


def to_markdown(results: list[dict]) -> str:
    out: list[str] = []
    summary = ["| Фикстура | Источник | Флаги качества | Уверенность Tesseract | "
               "Сходство всего текста | Поля точно | Время, с |",
               "|---|---|---|---|---|---|---|"]
    for r in results:
        exact = sum(1 for s, _, _ in r["fields"].values() if s == "точно")
        applicable = sum(1 for s, _, _ in r["fields"].values() if s != "нет в эталоне")
        flags = ", ".join(r["quality"]["flags"]) if "quality" in r else "—"
        flags = flags or "нет"
        conf = r["confidence"] if r["confidence"] is not None else "—"
        summary.append(f"| {r['name']} | {r['source']} | {flags} | {conf} | "
                       f"{r['text_similarity']} | {exact}/{applicable} | {r['seconds']} |")
    out += summary + [""]

    for r in results:
        out.append(f"#### {r['name']}")
        details = [f"искажения: `{json.dumps(r['distortions'], ensure_ascii=False)}`"]
        if "quality" in r:
            q = r["quality"]
            details.append(f"резкость {q['blur_score']}, яркость {q['brightness']}, "
                           f"{q['width']}×{q['height']}")
            details.append(f"OCR: вариант `{r['ocr_variant']}`, угол выравнивания {r['deskew_angle']}°")
        out.append("; ".join(details))
        out.append("")
        out.append("| Поле | Эталон | Результат | Балл | Найденный фрагмент |")
        out.append("|---|---|---|---|---|")
        for name, (status, score, snippet) in r["fields"].items():
            snippet = snippet.replace("\n", "⏎").replace("|", "\\|")
            score = "—" if score is None else score
            snippet = f"`{snippet}`" if snippet else "—"
            out.append(f"| {name} | {r['reference'][name]} | {status} | {score} | {snippet} |")
        out.append("")
        out.append("Распознанный текст:")
        out.append("```text")
        out.append(r["text"])
        out.append("```")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(to_markdown(evaluate(FIXTURES_DIR, Settings.from_env())))
