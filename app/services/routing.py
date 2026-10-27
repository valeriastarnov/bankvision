"""Маршрутизация заявки. Жёсткие правила проверяются раньше индекса, по приоритету:

1. «Требуется экспертная проверка»
2. «Запросить материалы у клиента» (с конкретным списком, что дозагрузить или переснять)
3. «Готово к проверке сотрудником»

Ни один маршрут не означает одобрения: система не принимает кредитного решения и не
решает, принимать ли залог. Флаг «состояние и наработка не оценены» — информационный.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.config import Settings
from app.services import crosscheck as cc
from app.services import review
from app.services import valuation as v
from app.services.scoring import INDEX_NAME, ScoreResult

ROUTE_EXPERT = "expert_review"
ROUTE_REQUEST = "request_materials"
ROUTE_READY = "ready_for_officer"
ROUTE_LABELS = {
    ROUTE_EXPERT: "Требуется экспертная проверка",
    ROUTE_REQUEST: "Запросить материалы у клиента",
    ROUTE_READY: "Готово к проверке сотрудником",
}
# Расхождение этих полей всегда ведёт к эксперту, даже если сотрудник выбрал значение.
EXPERT_MISMATCH_FIELDS = ("serial_number", "model")


@dataclass
class RouteResult:
    route: str
    reasons: list[str]  # причины выбранного маршрута
    requests: list[str] = field(default_factory=list)  # что запросить у клиента
    other_findings: list[str] = field(default_factory=list)  # причины маршрутов ниже по приоритету
    resolutions: list[str] = field(default_factory=list)  # как разрешены расхождения
    info: list[str] = field(default_factory=list)  # информационные флаги, на маршрут не влияют

    @property
    def label(self) -> str:
        return ROUTE_LABELS[self.route]

    def to_dict(self) -> dict:
        return asdict(self) | {"label": self.label}


def _resolution(check: cc.FieldCheck, final: dict | None) -> str:
    final = final or {}
    origin, value = final.get("origin"), final.get("value")
    doc = check.document["value"] if check.document else "—"
    photos = ", ".join(f"«{p['value']}» ({p['source_name']})" for p in check.photos) or "—"
    head = f"{check.label}: документ «{doc}», фото {photos}"
    how = {
        review.ORIGIN_CHOSEN_DOCUMENT: f"сотрудник выбрал значение из документа «{value}»",
        review.ORIGIN_CHOSEN_PHOTO: f"сотрудник выбрал значение с фото «{value}»",
        review.ORIGIN_MANUAL: f"сотрудник ввёл значение вручную «{value or '—'}»",
    }.get(origin, "не разрешено: итоговое значение не выбрано")
    return f"{head} — {how}"


def decide(
    checks: dict[str, cc.FieldCheck],
    finals: dict[str, dict],
    valuation_result: v.ValuationResult,
    score: ScoreResult,
    settings: Settings,
    document_error: str | None = None,
) -> RouteResult:
    expert: list[str] = []
    request_reasons: list[str] = []
    requests: list[str] = []
    resolutions: list[str] = []

    # --- 1. Экспертная проверка ---
    for name in EXPERT_MISMATCH_FIELDS:
        c = checks[name]
        if c.status == cc.STATUS_MISMATCH:
            expert.append(f"{c.label}: расходится между документом и фото шильдика")
            resolutions.append(_resolution(c, finals.get(name)))
    if valuation_result.level == v.LEVEL_NONE:
        expert.append("Аналогов нет: диапазон стоимости не построен")
    elif not valuation_result.has_range:
        expert.append(f"Аналогов меньше {settings.valuation_min_analogs} "
                      f"(найдено {len(valuation_result.analogs)}): диапазон не построен")
    if score.total < settings.routing_score_threshold:
        expert.append(f"{INDEX_NAME} {score.total:g} ниже порога {settings.routing_score_threshold:g}")

    # --- 2. Запрос материалов у клиента ---
    if document_error:
        request_reasons.append(f"Документ не обработан: {document_error}")
        requests.append("Загрузить документ на оборудование заново (файл не читается)")
    for name, c in checks.items():
        if c.status == cc.STATUS_NOT_CONFIRMED:
            files = ", ".join(f"«{p['source_name']}» ({', '.join(p['quality_flags'])})"
                              for p in c.photos if p["low_quality"])
            request_reasons.append(f"{c.label}: не подтверждено — фото низкого качества")
            requests.append(f"Переснять фото шильдика {files}: не подтверждено поле «{c.label}»")
        elif c.status == cc.STATUS_MISMATCH and name not in EXPERT_MISMATCH_FIELDS and name in cc.KEY_FIELDS:
            request_reasons.append(f"{c.label}: расходится между документом и фото")
            resolutions.append(_resolution(c, finals.get(name)))
            requests.append(f"Уточнить у клиента поле «{c.label}» и приложить подтверждающий документ")

    missing_in_passport = [checks[n].label for n in cc.KEY_FIELDS if checks[n].document is None]
    if missing_in_passport and not document_error:
        request_reasons.append("Паспорт неполный: нет полей " + ", ".join(missing_in_passport))
        requests.append("Дозагрузить полный паспорт оборудования, где указаны: "
                        + ", ".join(missing_in_passport))
    for name in cc.KEY_FIELDS:
        c = checks[name]
        if c.status == cc.STATUS_SINGLE and c.document is not None and not c.photos:
            request_reasons.append(f"{c.label}: есть только в документе, на фото шильдика не найдено")
            requests.append(f"Переснять шильдик крупно и при хорошем освещении: должно читаться поле «{c.label}»")
        elif c.status == cc.STATUS_SINGLE and c.document is None:
            request_reasons.append(f"{c.label}: есть только на фото шильдика, в документе нет")

    info = [f for f in valuation_result.flags if f == v.FLAG_CONDITION]

    if expert:
        route, reasons, other = ROUTE_EXPERT, expert, request_reasons
    elif request_reasons:
        route, reasons, other = ROUTE_REQUEST, request_reasons, []
    else:
        route, reasons, other = ROUTE_READY, [
            "Жёсткие правила не сработали; "
            f"{INDEX_NAME} {score.total:g} не ниже порога {settings.routing_score_threshold:g}"
        ], []
    return RouteResult(route, reasons, list(dict.fromkeys(requests)), other, resolutions, info)
