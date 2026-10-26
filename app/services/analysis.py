"""Первичная обработка заявки: качество фото, OCR фото, извлечение текста документа.

Ошибка обработки одного файла не прерывает остальные: она сохраняется в анализе файла
и в журнале, а заявка получает статус «обработана с ошибками».
"""
from __future__ import annotations

from app import db
from app.config import Settings
from app.services import image_quality, ocr, pdf

STATUS_PROCESSED = "processed"
STATUS_PROCESSED_WITH_ERRORS = "processed_with_errors"


def _analyze_photo(path, settings: Settings) -> dict:
    quality = image_quality.assess(path, settings)
    result = {"quality": quality.to_dict()}
    try:
        ocr_result = ocr.ocr_image(path, settings)
        result["text"] = ocr_result.text
        result["text_meta"] = {"sources": [pdf.SOURCE_OCR]} | ocr_result.to_dict()
    except ocr.OcrUnavailableError as exc:
        result["error"] = str(exc)
    return result


def _analyze_document(path, settings: Settings) -> dict:
    doc_text = pdf.extract_document_text(path, settings)
    return {"text": doc_text.text, "text_meta": doc_text.to_dict()}


def analyze_application(application_id: int, settings: Settings) -> str:
    """Обрабатывает все файлы заявки; возвращает итоговый статус."""
    with db.connect(settings.db_path) as conn:
        application = db.get_application(conn, application_id)
    if application is None:
        raise ValueError(f"Заявка {application_id} не найдена")

    has_errors = False
    for f in application.files:
        path = settings.upload_dir / f.stored_path
        try:
            if f.kind == "photo":
                result = _analyze_photo(path, settings)
            else:
                result = _analyze_document(path, settings)
        except (image_quality.ImageReadError, pdf.DocumentError, ocr.OcrUnavailableError) as exc:
            result = {"error": str(exc)}

        with db.connect(settings.db_path) as conn:
            db.save_analysis(conn, f.id, **result)
            if result.get("error"):
                has_errors = True
                db.log_event(conn, application_id, "processing_error",
                             f"{f.original_name}: {result['error']}")
            quality = result.get("quality")
            if quality is not None:
                flags = ", ".join(quality["flags"]) or "замечаний нет"
                db.log_event(conn, application_id, "quality_checked", f"{f.original_name}: {flags}")
            if result.get("text") is not None:
                sources = ", ".join(result["text_meta"]["sources"])
                db.log_event(conn, application_id, "text_extracted",
                             f"{f.original_name}: {len(result['text'])} симв., источник: {sources}")

    status = STATUS_PROCESSED_WITH_ERRORS if has_errors else STATUS_PROCESSED
    with db.connect(settings.db_path) as conn:
        db.set_status(conn, application_id, status)
    return status
