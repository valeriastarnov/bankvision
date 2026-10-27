"""Итерация 6: скрипт демо-данных создаёт заявки с ожидаемыми маршрутами."""
from __future__ import annotations

from app.services import report, routing
from scripts.seed_demo import DEMO_CASES, seed
from tests.test_quality_ocr import needs_tesseract


@needs_tesseract
def test_seed_demo_creates_expected_applications(settings):
    created = seed(settings)
    assert len(created) == len(DEMO_CASES) == 4
    routes = [report.assess(settings, app_id).route.route for _, app_id in created]
    assert routes == [routing.ROUTE_READY, routing.ROUTE_REQUEST,
                      routing.ROUTE_EXPERT, routing.ROUTE_EXPERT]
    journal = report.assess(settings, created[0][1]).journal
    assert any(j["event"] == "demo_seed" and "Синтетическая" in j["details"] for j in journal)
