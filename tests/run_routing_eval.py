"""Сценарий → маршрут → причины → индекс. Полная цепочка приложения во временной папке,
без вмешательства сотрудника (кроме отдельно помеченной строки с ручным выбором).

Запуск (PowerShell):  .\\.venv\\Scripts\\python.exe -m tests.run_routing_eval > routing.md
"""
from __future__ import annotations

import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from tests.generate_fixtures import FIXTURES_DIR  # noqa: E402
from tests.test_routing import SCENARIOS  # noqa: E402

BROKEN_PDF = b"%PDF-1.4\n" + b"\x00garbage" * 50


def main() -> str:
    tmp = Path(tempfile.mkdtemp(prefix="bankvision_routing_"))
    client = TestClient(create_app(Settings(data_dir=tmp, upload_dir=tmp / "uploads", db_path=tmp / "r.db")))

    def run(photo: str, doc: str, doc_bytes: bytes | None = None, edit: dict | None = None):
        files = [("photos", (photo, (FIXTURES_DIR / photo).read_bytes(), "image/jpeg")),
                 ("document", (doc, doc_bytes or (FIXTURES_DIR / doc).read_bytes(), "application/pdf"))]
        resp = client.post("/api/applications", files=files)
        if resp.status_code != 201:
            return None, resp
        app_id = resp.json()["application"]["id"]
        if edit:
            client.post(f"/applications/{app_id}/review", data=edit)
        return client.get(f"/api/applications/{app_id}/assessment").json(), resp

    cases = [(t, p, d, None, None, e) for t, p, d, e in SCENARIOS]
    cases.insert(6, ("4. расхождение номера + сотрудник выбрал значение из документа",
                     "plate_serial_mismatch.jpg", "passport_text.pdf", None,
                     {"source__serial_number": "document"}, "expert_review"))
    cases.append(("6. битый PDF (проходит загрузку по сигнатуре)", "plate_clear.jpg", "broken.pdf",
                  BROKEN_PDF, None, "request_materials"))

    rows = ["| Сценарий | Маршрут | Ожидался | Индекс | Фото / Полнота / Согласованность / Аналоги | Причины | Что запросить у клиента |",
            "|---|---|---|---|---|---|---|"]
    for title, photo, doc, doc_bytes, edit, expected in cases:
        data, _ = run(photo, doc, doc_bytes, edit)
        r, s = data["route"], data["score"]
        parts = " / ".join(f"{c['contribution']:g}" for c in s["components"])
        ok = "✓" if r["route"] == expected else "✗ " + expected
        rows.append(f"| {title} | **{r['label']}** | {ok} | {s['total']:g} | {parts} | "
                    f"{'<br>'.join(r['reasons'])} | {'<br>'.join(r['requests']) or '—'} |")

    # Неверный формат — отказ на загрузке, заявка не создаётся.
    files = [("photos", ("plate.gif", b"GIF89a....", "image/gif")),
             ("document", ("passport_text.pdf", (FIXTURES_DIR / "passport_text.pdf").read_bytes(), "application/pdf"))]
    resp = client.post("/api/applications", files=files)
    rows.append(f"| 6. неверный формат фото (GIF) | **отказ на загрузке** (HTTP {resp.status_code}) | ✓ | — | — | "
                f"{'<br>'.join(resp.json()['errors'])} | — |")
    return "\n".join(rows)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(main())
