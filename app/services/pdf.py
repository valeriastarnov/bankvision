"""Извлечение текста из документа: текстовый слой PDF, при его отсутствии — OCR."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pymupdf

from app.config import Settings
from app.services import ocr

SOURCE_TEXT_LAYER = "text_layer"
SOURCE_OCR = "ocr"

# psm 4 — одна колонка текста переменного размера (страница документа).
DOCUMENT_PSM = 4


class DocumentError(Exception):
    """Документ не удалось открыть или обработать; сообщение — для пользователя."""


@dataclass
class PageText:
    number: int
    text: str
    source: str  # SOURCE_TEXT_LAYER | SOURCE_OCR
    ocr_confidence: float | None = None


@dataclass
class DocumentText:
    pages: list[PageText] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)

    @property
    def sources(self) -> list[str]:
        return sorted({p.source for p in self.pages})

    def to_dict(self) -> dict:
        return {"pages": [asdict(p) for p in self.pages], "sources": self.sources}


# Типовые артефакты текстового слоя PDF: неразрывный пробел и мягкий перенос, которым
# многие шрифты кодируют видимый дефис (один глиф на оба символа).
_TEXT_LAYER_FIXES = str.maketrans({"\xa0": " ", "\xad": "-", "‑": "-"})


def normalize_text_layer(text: str) -> str:
    return text.translate(_TEXT_LAYER_FIXES)


def _has_text_layer(text: str, settings: Settings) -> bool:
    return sum(1 for ch in text if not ch.isspace()) >= settings.pdf_min_text_chars


def _render_gray(page: pymupdf.Page, dpi: int) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def extract_pdf(path: Path, settings: Settings) -> DocumentText:
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception as exc:
        raise DocumentError(
            f"Не удалось открыть PDF «{path.name}»: файл повреждён или не является PDF."
        ) from exc

    result = DocumentText()
    try:
        if doc.page_count == 0:
            raise DocumentError(f"PDF «{path.name}» не содержит страниц.")
        for page in doc:
            layer_text = normalize_text_layer(page.get_text()).strip()
            if _has_text_layer(layer_text, settings):
                result.pages.append(PageText(page.number + 1, layer_text, SOURCE_TEXT_LAYER))
                continue
            gray = _render_gray(page, settings.pdf_ocr_dpi)
            ocr_result = ocr.ocr_array(gray, settings, psm=DOCUMENT_PSM)
            result.pages.append(
                PageText(page.number + 1, ocr_result.text, SOURCE_OCR, ocr_result.confidence)
            )
    except (DocumentError, ocr.OcrUnavailableError):
        raise
    except Exception as exc:
        raise DocumentError(f"Ошибка обработки PDF «{path.name}»: {exc}") from exc
    finally:
        doc.close()
    return result


def extract_document_text(path: Path, settings: Settings) -> DocumentText:
    """Текст документа: PDF — через текстовый слой или OCR, изображение — через OCR."""
    if path.suffix.lower() == ".pdf":
        return extract_pdf(path, settings)
    ocr_result = ocr.ocr_image(path, settings, psm=DOCUMENT_PSM)
    return DocumentText([PageText(1, ocr_result.text, SOURCE_OCR, ocr_result.confidence)])
