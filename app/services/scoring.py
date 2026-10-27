"""Индекс полноты и согласованности данных (0–100).

Правило-ориентированный балл: сумма вкладов компонентов, каждый = вес × оценка 0–1.
Это НЕ вероятность и НЕ откалиброванная уверенность. Веса выбраны экспертно и не
откалиброваны; методика — docs/methodology_score.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from app.config import Settings
from app.services import crosscheck as cc
from app.services import valuation as v
from app.services.extract import FIELDS

INDEX_NAME = "Индекс полноты и согласованности данных"

COMPONENT_LABELS = {
    "photo_quality": "Качество фото",
    "completeness": "Полнота ключевых полей",
    "consistency": "Согласованность источников",
    "analogs": "Достаточность аналогов",
}


@dataclass
class Component:
    key: str
    label: str
    weight: float
    subscore: float  # 0–1
    contribution: float  # weight × subscore
    explanation: str


@dataclass
class ScoreResult:
    total: float
    components: list[Component]
    name: str = INDEX_NAME

    @property
    def max_total(self) -> float:
        return sum(c.weight for c in self.components)

    def to_dict(self) -> dict:
        return asdict(self) | {"max_total": self.max_total}


def _photo_quality(photo_flags: dict[str, list[str]]) -> tuple[float, str]:
    if not photo_flags:
        return 0.0, "фото нет"
    clean = [n for n, f in photo_flags.items() if not f]
    flagged = [f"{n} ({', '.join(f)})" for n, f in photo_flags.items() if f]
    text = f"без флагов качества: {len(clean)} из {len(photo_flags)}"
    if flagged:
        text += "; с флагами: " + "; ".join(flagged)
    return len(clean) / len(photo_flags), text


def _completeness(finals: dict[str, dict], checks: dict[str, cc.FieldCheck]) -> tuple[float, str]:
    """Доля ключевых полей с итоговым значением. Поле, источники которого расходятся
    или не подтверждены, не засчитывается даже после выбора значения сотрудником:
    выбор не устраняет противоречие в исходных данных и не должен повышать индекс."""
    conflicted = [f for f in cc.KEY_FIELDS if checks[f].needs_decision]
    filled = [f for f in cc.KEY_FIELDS
              if (finals.get(f) or {}).get("value") and f not in conflicted]
    empty = [FIELDS[f] for f in cc.KEY_FIELDS if f not in filled and f not in conflicted]
    text = f"заполнено {len(filled)} из {len(cc.KEY_FIELDS)} ключевых полей"
    if empty:
        text += "; нет итогового значения: " + ", ".join(empty)
    if conflicted:
        text += ("; не засчитаны из-за расхождения или неподтверждённости источников "
                 "(независимо от выбора сотрудника): " + ", ".join(FIELDS[f] for f in conflicted))
    return len(filled) / len(cc.KEY_FIELDS), text


def field_consistency(check: cc.FieldCheck, settings: Settings) -> tuple[float, str]:
    """Балл согласованности одного поля и вид совпадения."""
    s = settings.score_consistency
    if check.status in (cc.STATUS_MATCH, cc.STATUS_NORMALIZED):
        equal = [p for p in check.photos if (p.get("comparison") or {}).get("equal")]
        if any(not p["low_quality"] for p in equal):
            return s["match_good_photo"], "совпадает"
        return s["match_low_quality_photo"], "совпадает только на фото с флагом качества"
    return s[check.status], check.status_label


def _consistency(checks: dict[str, cc.FieldCheck], settings: Settings) -> tuple[float, str]:
    parts, total = [], 0.0
    for name in cc.KEY_FIELDS:
        value, kind = field_consistency(checks[name], settings)
        total += value
        parts.append(f"{checks[name].label}: {kind} ({value:g})")
    return total / len(cc.KEY_FIELDS), "; ".join(parts)


def _analogs(result: v.ValuationResult, settings: Settings) -> tuple[float, str]:
    s = settings.score_analogs
    n = len(result.analogs)
    if result.has_range and result.level == v.LEVEL_MODEL:
        return s["model_range"], f"диапазон построен по {n} аналогам той же модели"
    if result.has_range:
        return s["class_range"], f"диапазон построен по {n} аналогам того же класса"
    if n:
        return s["below_minimum"], f"найдено {n} аналогов при минимуме {settings.valuation_min_analogs}"
    return s["none"], "аналогов нет"


def compute(checks: dict[str, cc.FieldCheck], finals: dict[str, dict],
            photo_flags: dict[str, list[str]], valuation_result: v.ValuationResult,
            settings: Settings) -> ScoreResult:
    """photo_flags — {имя фото: флаги качества}."""
    parts = {
        "photo_quality": _photo_quality(photo_flags),
        "completeness": _completeness(finals, checks),
        "consistency": _consistency(checks, settings),
        "analogs": _analogs(valuation_result, settings),
    }
    components = []
    for key, (subscore, text) in parts.items():
        weight = float(settings.score_weights[key])
        components.append(Component(key, COMPONENT_LABELS[key], weight, round(subscore, 4),
                                    round(weight * subscore, 2), text))
    return ScoreResult(round(sum(c.contribution for c in components), 1), components)
