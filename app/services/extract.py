"""Извлечение полей оборудования из текста правилами.

Работает отдельно для каждого источника (документ, каждое фото). Сравнение источников
между собой здесь не выполняется. Ошибки OCR (O/0, кириллица/латиница) не исправляются:
значение сохраняется так, как распознано, вместе с исходной строкой.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

# Порядок определяет порядок строк на экране проверки.
FIELDS: dict[str, str] = {
    "equipment_type": "Вид оборудования",
    "manufacturer": "Производитель",
    "model": "Модель",
    "serial_number": "Заводской (серийный) номер",
    "year": "Год выпуска",
    "max_diameter_mm": "Наибольший диаметр обработки, мм",
    "center_distance_mm": "Расстояние между центрами, мм",
    "power_kw": "Мощность, кВт",
    "weight_kg": "Масса, кг",
}

METHOD_LABEL = "label"  # найдено по подписи поля («Модель: …»)
METHOD_HEURISTIC = "heuristic"  # найдено по положению/виду строки (шильдик без подписей)


@dataclass
class ExtractedField:
    field: str
    value: str
    fragment: str  # исходная строка текста, из которой взято значение
    method: str
    source_file_id: int | None = None
    source_name: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- очистка мусора OCR ----------

# Одиночные символы, которые по краям строки почти всегда мусор (заклёпки, блики),
# кроме знаков, которые сами являются подписью поля.
_KEEP_SINGLE = {"№", "N"}
_SYMBOLS_ONLY = re.compile(r"^[^\w№]+$")


def _is_edge_junk(token: str) -> bool:
    if _SYMBOLS_ONLY.match(token):
        return True
    return len(token) == 1 and not token.isdigit() and token not in _KEEP_SINGLE


def clean_lines(text: str) -> list[tuple[str, str]]:
    """Возвращает пары (очищенная строка, исходная строка) без мусорных строк."""
    result = []
    for raw in text.splitlines():
        tokens = raw.split()
        while tokens and _is_edge_junk(tokens[0]):
            tokens.pop(0)
        while tokens and _is_edge_junk(tokens[-1]):
            tokens.pop()
        # Строка без единого слова из двух и более букв/цифр — мусор («® ®», «. ,»).
        if not any(sum(ch.isalnum() for ch in t) >= 2 for t in tokens):
            continue
        result.append((" ".join(tokens), raw.strip()))
    return result


# ---------- правила по подписям ----------

_NUMBER = r"\d+(?:[.,]\d+)?"
_SEP = r"\s*[:.]?\s*"

LABEL_RULES: dict[str, re.Pattern] = {
    "equipment_type": re.compile(r"(?:наименование|вид\s+оборудования)" + _SEP + r"(?P<v>.+)$", re.I),
    "manufacturer": re.compile(
        r"(?:завод[\s-]*изготовитель|изготовитель|производитель|manufacturer)" + _SEP + r"(?P<v>.+)$",
        re.I,
    ),
    "model": re.compile(r"(?:тип\s*/\s*модель|модель|model)" + _SEP + r"(?P<v>.+)$", re.I),
    # «Зав. №», «Заводской номер», «Зав. Ne» (так OCR читает №), «S/N», «Серийный номер».
    "serial_number": re.compile(
        r"(?:зав(?:одской|\.)?\s*(?:№|n[eoº°]?\b|no\b\.?|номер)|серийный\s+номер|\bs/?n\b|\bserial(?:\s+(?:no|number))?)"
        + _SEP + r"(?P<v>.+)$",
        re.I,
    ),
    # «Год выпуска», «Год изготовления», а также искажённое OCR «Год вытуска».
    "year": re.compile(
        r"(?:год\s+(?:выпуска|изготовления|в\w*)|дата\s+(?:выпуска|изготовления)|year)\D{0,5}"
        r"(?P<v>(?:19|20)\d{2})(?!\d)",
        re.I,
    ),
    "max_diameter_mm": re.compile(
        r"наибольш\w*\s+диаметр[^:\d]*:?\s*(?P<v>" + _NUMBER + ")", re.I
    ),
    "center_distance_mm": re.compile(
        r"(?:расстояни\w*\s+между\s+центрами|рмц)[^:\d]*:?\s*(?P<v>" + _NUMBER + ")", re.I
    ),
    "power_kw": re.compile(r"мощност\w*[^:\d]*:?\s*(?P<v>" + _NUMBER + ")", re.I),
    "weight_kg": re.compile(r"масс\w*[^:\d]*:?\s*(?P<v>\d[\d ]*(?:[.,]\d+)?)", re.I),
}

_SERIAL_VALUE = re.compile(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё\-/.]{2,24}")


def _normalize_value(field: str, value: str) -> str | None:
    value = value.strip().strip(".,;:")
    if not value:
        return None
    if field == "serial_number":
        value = re.sub(r"\s+", "", value)
        if not _SERIAL_VALUE.fullmatch(value) or not any(ch.isdigit() for ch in value):
            return None
    elif field in ("max_diameter_mm", "center_distance_mm", "power_kw", "weight_kg"):
        value = value.replace(" ", "").replace(",", ".")
    else:
        value = re.sub(r"\s+", " ", value)
    return value


# ---------- эвристики для шильдика без подписей ----------

_SERVICE_WORDS = re.compile(r"паспорт|синтетическ|образец|технические|характеристик", re.I)
_EQUIPMENT_WORDS = re.compile(r"^(?:станок|центр\s+обрабатывающий|пресс)\b", re.I)


def _looks_like_manufacturer(line: str) -> bool:
    letters = [ch for ch in line if ch.isalpha()]
    if ":" in line or len(letters) < 4 or _SERVICE_WORDS.search(line):
        return False
    if any(rule.search(line) for rule in LABEL_RULES.values()):
        return False
    return sum(ch.isupper() for ch in letters) / len(letters) >= 0.8


def extract_fields(
    text: str,
    source_file_id: int | None = None,
    source_name: str = "",
    allow_heuristics: bool = False,
) -> dict[str, ExtractedField]:
    """Извлекает поля из текста одного источника.

    allow_heuristics — разрешить поиск без подписей (для фото шильдика, где производитель
    и вид оборудования обычно напечатаны отдельными строками без «Изготовитель:»).
    """
    lines = clean_lines(text)
    found: dict[str, ExtractedField] = {}

    def put(field: str, value: str | None, fragment: str, method: str) -> None:
        if value and field not in found:
            found[field] = ExtractedField(field, value, fragment, method, source_file_id, source_name)

    for clean, raw in lines:
        for field, rule in LABEL_RULES.items():
            if field in found:
                continue
            match = rule.search(clean)
            if match:
                put(field, _normalize_value(field, match.group("v")), raw, METHOD_LABEL)

    if allow_heuristics:
        for clean, raw in lines[:3]:
            if _looks_like_manufacturer(clean):
                put("manufacturer", _normalize_value("manufacturer", clean), raw, METHOD_HEURISTIC)
                break
        for clean, raw in lines:
            if ":" not in clean and _EQUIPMENT_WORDS.search(clean):
                put("equipment_type", _normalize_value("equipment_type", clean), raw, METHOD_HEURISTIC)
                break

    return {f: found[f] for f in FIELDS if f in found}
