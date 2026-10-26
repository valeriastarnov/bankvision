# Фактические результаты прогонов тестов

## 2026-10-26 — итерация 0 (каркас)

Окружение: Windows 11, Python 3.14.5, pytest 9.1.1, FastAPI 0.162.0.

Команда: `.\.venv\Scripts\python.exe -m pytest -v`

Результат: **14 passed, 1 warning** (5.00 s).

| Тест | Результат |
|---|---|
| test_valid_upload_saves_files | PASSED |
| test_document_as_image_is_accepted | PASSED |
| test_wrong_photo_format_rejected | PASSED |
| test_pdf_not_allowed_as_photo | PASSED |
| test_renamed_file_rejected_by_signature | PASSED |
| test_oversized_file_rejected | PASSED |
| test_empty_file_rejected | PASSED |
| test_no_photos_rejected | PASSED |
| test_too_many_photos_rejected | PASSED |
| test_five_photos_accepted | PASSED |
| test_missing_document_rejected | PASSED |
| test_one_bad_file_rejects_whole_upload | PASSED |
| test_html_form_flow | PASSED |
| test_unknown_application_404 | PASSED |

Предупреждение: `StarletteDeprecationWarning` — starlette рекомендует `httpx2` для TestClient
вместо `httpx`. На результаты не влияет.

Ручная проверка: сервер `uvicorn app.main:app` запущен, `GET /` → 200;
загрузка JPG + PDF через API → 201, файлы появились в `data/uploads/1/`;
загрузка GIF → 400, `Фото «p.gif»: неподдерживаемый формат. Допустимы: JPG, PNG.`

Тестовые файлы синтетические: генерируются в памяти (Pillow, PyMuPDF) в `tests/conftest.py`.
