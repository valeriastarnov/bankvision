"""Сборка данных заявки для отчёта: поля и сверка, правки, оценка, индекс, маршрут."""
from __future__ import annotations

from dataclasses import dataclass

from app import db
from app.config import Settings
from app.models import Application
from app.services import crosscheck, review, routing, scoring, valuation

NO_CREDIT_DECISION = "Система не принимает кредитное решение и не решает, принимать ли залог."
SYNTHETIC_NOTE = ("Все тестовые фото, документы и база аналогов синтетические; "
                  "производители и модели вымышленные.")

LIMITATIONS = [
    "Оценка демонстрационная: база аналогов синтетическая, цены сгенерированы произвольной "
    "формулой. Не является рыночной стоимостью.",
    "Не оцениваются техническое состояние, наработка, ремонты и комплектация; нет поправок "
    "на регион, торг и дату объявления; цены предложения не равны ценам сделок.",
    "Поправка на возраст (коэффициент в год) — демонстрационное допущение, не рыночные данные.",
    f"{scoring.INDEX_NAME} — правило-ориентированный балл с экспертными, не откалиброванными "
    "весами. Это не вероятность и не уверенность.",
    "Проверка качества фото: средняя яркость кадра не обнаруживает пересвет таблички на тёмном "
    "фоне; флаги размытости и темноты могут срабатывать раньше, чем текст становится нечитаемым.",
    "OCR может читать кириллические обозначения латиницей; сверка сравнивает источники между "
    "собой и не обнаружит одинаковую ошибку в обоих.",
    "Пороги и параметры не калибровались на реальных фото и документах.",
]


@dataclass
class ApplicationAssessment:
    application: Application
    journal: list[dict]
    analysis: dict[int, dict]
    finals: dict[str, dict]
    checks: dict[str, crosscheck.FieldCheck]
    photo_flags: dict[str, list[str]]
    flags: list[str]
    valuation_object: valuation.ObjectData
    valuation: valuation.ValuationResult
    score: scoring.ScoreResult
    route: routing.RouteResult
    document_error: str | None

    @property
    def edits(self) -> list[dict]:
        return [j for j in self.journal if j["event"] == review.EVENT_FIELD_EDITED]

    @property
    def rows(self) -> list[dict]:
        return review.review_rows(self.checks, self.finals)


def assess(settings: Settings, application_id: int) -> ApplicationAssessment | None:
    with db.connect(settings.db_path) as conn:
        application = db.get_application(conn, application_id)
        if application is None:
            return None
        journal = db.get_journal(conn, application_id)
        analysis = db.get_analysis(conn, application_id)
        finals = db.get_final(conn, application_id)
        checks = review.compute_checks(conn, application_id, settings)

    photo_flags = {f.original_name: ((analysis.get(f.id) or {}).get("quality") or {}).get("flags", [])
                   for f in application.photos}
    document_error = next(((analysis.get(f.id) or {}).get("error") for f in application.documents
                           if (analysis.get(f.id) or {}).get("error")), None)
    obj, val = valuation.value_application(finals, settings)
    score = scoring.compute(checks, finals, photo_flags, val, settings)
    route = routing.decide(checks, finals, val, score, settings, document_error)
    return ApplicationAssessment(
        application, journal, analysis, finals, checks, photo_flags,
        crosscheck.flags(checks, photo_flags), obj, val, score, route, document_error,
    )
