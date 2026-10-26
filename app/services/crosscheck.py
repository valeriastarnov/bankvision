"""Сверка полей между документом и фото шильдика.

Нормализация применяется только для сравнения; на экране и в отчёте остаются исходные
значения, а рядом — список того, что пришлось нормализовать.

- Заводской номер, модель и остальные поля сравниваются строго после нормализации.
- Название производителя дополнительно сравнивается нечётко (rapidfuzz), порог в config.
- Фото с любым флагом качества считается источником низкого качества: расхождение
  только с таким фото даёт «не подтверждено», а не «расходится».
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from rapidfuzz import fuzz

from app.config import Settings
from app.services.extract import FIELDS

STATUS_MATCH = "match"
STATUS_NORMALIZED = "normalized_match"
STATUS_MISMATCH = "mismatch"
STATUS_NOT_CONFIRMED = "not_confirmed"
STATUS_SINGLE = "single_source"
STATUS_MISSING = "missing"

STATUS_LABELS = {
    STATUS_MATCH: "совпадает",
    STATUS_NORMALIZED: "совпадает после нормализации",
    STATUS_MISMATCH: "расходится",
    STATUS_NOT_CONFIRMED: "не подтверждено — источник низкого качества",
    STATUS_SINGLE: "есть только в одном источнике",
    STATUS_MISSING: "нет ни в одном источнике",
}
# Статусы, при которых итоговое значение не выбирается автоматически.
NEEDS_DECISION = {STATUS_MISMATCH, STATUS_NOT_CONFIRMED}
# Поля, по которым идентифицируется оборудование: для них отсутствие подтверждения
# вторым источником попадает в общий список флагов.
KEY_FIELDS = ("manufacturer", "model", "serial_number", "year")

# Кириллические буквы, совпадающие по начертанию с латинскими.
HOMOGLYPHS = {"А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
              "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X"}
_HOMOGLYPH_TABLE = str.maketrans(HOMOGLYPHS)
_DASHES = re.compile(r"[-‐‑‒–—]")


# ---------- нормализация ----------

def _step_case(s: str) -> str:
    return s.upper()


def _step_homoglyphs(s: str) -> str:
    return s.translate(_HOMOGLYPH_TABLE)


def _step_spaces(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _step_dashes(s: str) -> str:
    return _DASHES.sub("", s)


def _step_o_zero(s: str) -> str:
    return s.replace("O", "0").replace("o", "0")


def _step_i_l_one(s: str) -> str:
    return re.sub(r"[IiLl]", "1", s)


COMMON_STEPS = [
    ("case", "регистр", _step_case),
    ("homoglyphs", "кириллица/латиница", _step_homoglyphs),
    ("spaces", "пробелы", _step_spaces),
    ("dashes", "дефисы", _step_dashes),
]
SERIAL_STEPS = [
    ("o_zero", "O → 0", _step_o_zero),
    ("i_l_one", "I/l → 1", _step_i_l_one),
]


def _steps_for(field_name: str):
    return COMMON_STEPS + (SERIAL_STEPS if field_name == "serial_number" else [])


def normalize(value: str, field_name: str, skip: str | None = None) -> str:
    for key, _, func in _steps_for(field_name):
        if key != skip:
            value = func(value)
    return value


def _homoglyph_detail(*values: str) -> str:
    letters = sorted({ch for v in values for ch in v.upper() if ch in HOMOGLYPHS})
    return ("кириллические " + ", ".join(letters) + " → латинские "
            + ", ".join(HOMOGLYPHS[ch] for ch in letters))


def necessary_steps(a: str, b: str, field_name: str) -> list[str]:
    """Шаги нормализации, без которых значения перестали бы совпадать."""
    result = []
    for key, label, _ in _steps_for(field_name):
        if normalize(a, field_name, skip=key) != normalize(b, field_name, skip=key):
            result.append(_homoglyph_detail(a, b) if key == "homoglyphs" else label)
    return result


@dataclass
class Comparison:
    equal: bool
    kind: str  # "exact" | "normalized" | "fuzzy" | "different"
    steps: list[str] = field(default_factory=list)
    score: float | None = None


def compare_values(a: str, b: str, field_name: str, settings: Settings) -> Comparison:
    if a == b:
        return Comparison(True, "exact")
    na, nb = normalize(a, field_name), normalize(b, field_name)
    if na == nb:
        return Comparison(True, "normalized", necessary_steps(a, b, field_name))
    if field_name == "manufacturer":
        score = round(fuzz.ratio(na, nb), 1)
        if score >= settings.manufacturer_fuzzy_min:
            return Comparison(True, "fuzzy", [
                f"нечёткое сравнение: {score:g}/100 при пороге {settings.manufacturer_fuzzy_min:g}"
            ], score)
        return Comparison(False, "different", score=score)
    return Comparison(False, "different")


# ---------- сверка поля ----------

@dataclass
class FieldCheck:
    field: str
    label: str
    status: str
    document: dict | None
    photos: list[dict]  # строки extracted_fields + low_quality, quality_flags, comparison
    normalization: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def needs_decision(self) -> bool:
        return self.status in NEEDS_DECISION

    def to_dict(self) -> dict:
        return asdict(self) | {"status_label": self.status_label,
                               "needs_decision": self.needs_decision}


def _photo_entries(rows: list[dict], quality_flags: dict[int, list[str]]) -> list[dict]:
    entries = []
    for row in rows:
        flags = quality_flags.get(row["file_id"], [])
        entries.append(dict(row) | {"quality_flags": flags, "low_quality": bool(flags)})
    return entries


def _check_against_document(doc: dict, photos: list[dict], name: str,
                            settings: Settings) -> tuple[str, list[str], str]:
    good_equal, good_diff, low_equal, low_diff = [], [], [], []
    for p in photos:
        cmp = compare_values(doc["value"], p["value"], name, settings)
        p["comparison"] = asdict(cmp)
        bucket = (low_equal if cmp.equal else low_diff) if p["low_quality"] else \
                 (good_equal if cmp.equal else good_diff)
        bucket.append((p, cmp))

    def matched(pairs: list, note: str = "") -> tuple[str, list[str], str]:
        if all(c.kind == "exact" for _, c in pairs):
            return STATUS_MATCH, [], note
        steps = list(dict.fromkeys(s for _, c in pairs for s in c.steps))
        return STATUS_NORMALIZED, steps, note

    if good_diff:
        names = ", ".join(p["source_name"] for p, _ in good_diff)
        return STATUS_MISMATCH, [], f"значение на фото ({names}) отличается от документа"
    if good_equal:
        return matched(good_equal)
    if low_diff:
        names = ", ".join(p["source_name"] for p, _ in low_diff)
        return STATUS_NOT_CONFIRMED, [], (
            f"значение отличается только на фото с флагом качества ({names}); "
            "фото нужно переснять или проверить вручную")
    return matched(low_equal, "совпадение по фото с флагом качества")


def _check_photos_only(photos: list[dict], name: str, settings: Settings) -> tuple[str, list[str], str]:
    first = photos[0]
    differ = [p for p in photos[1:]
              if not compare_values(first["value"], p["value"], name, settings).equal]
    if differ:
        involved = [first] + differ
        status = STATUS_MISMATCH if not any(p["low_quality"] for p in involved) else STATUS_NOT_CONFIRMED
        return status, [], "в документе поля нет, а фото противоречат друг другу"
    note = "есть только на фото"
    if all(p["low_quality"] for p in photos):
        note += " с флагом качества"
    return STATUS_SINGLE, [], note


def check_field(name: str, extracted: list[dict], quality_flags: dict[int, list[str]],
                settings: Settings) -> FieldCheck:
    rows = [r for r in extracted if r["field"] == name]
    doc = next((r for r in rows if r["kind"] == "document"), None)
    photos = _photo_entries([r for r in rows if r["kind"] == "photo"], quality_flags)

    if doc is None and not photos:
        status, steps, note = STATUS_MISSING, [], ""
    elif doc is None:
        status, steps, note = _check_photos_only(photos, name, settings)
    elif not photos:
        status, steps, note = STATUS_SINGLE, [], "есть только в документе"
    else:
        status, steps, note = _check_against_document(doc, photos, name, settings)
    return FieldCheck(name, FIELDS[name], status, doc, photos, steps, note)


def crosscheck(extracted: list[dict], quality_flags: dict[int, list[str]],
               settings: Settings) -> dict[str, FieldCheck]:
    """Сверка всех полей. extracted — строки db.get_extracted, quality_flags — {file_id: флаги}."""
    return {name: check_field(name, extracted, quality_flags, settings) for name in FIELDS}


def flags(checks: dict[str, FieldCheck], photo_flags: dict[str, list[str]] | None = None) -> list[str]:
    """Общий список флагов заявки: расхождения, неподтверждённые и отсутствующие поля,
    поля идентификации без второго источника, флаги качества фото."""
    result = []
    for c in checks.values():
        if c.status in NEEDS_DECISION or c.status == STATUS_MISSING or (
                c.status == STATUS_SINGLE and c.field in KEY_FIELDS):
            text = f"{c.label}: {c.status_label}"
            result.append(f"{text} ({c.note})" if c.note else text)
    for name, fl in (photo_flags or {}).items():
        if fl:
            result.append(f"Фото «{name}»: флаги качества — {', '.join(fl)}")
    return result
