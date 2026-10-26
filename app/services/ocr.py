"""OCR через Tesseract (rus+eng) с предобработкой фото шильдика.

Предобработка: оттенки серого → выравнивание контраста (CLAHE) → выравнивание наклона
→ бинаризация Otsu. OCR выполняется и по исходному, и по обработанному изображению;
берётся результат с большей средней уверенностью Tesseract.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
import pytesseract

from app.config import Settings
from app.services.image_quality import load_gray

# Tesseract лучше всего работает при высоте строчных букв ~20–30 px;
# слишком маленькие изображения увеличиваем.
MIN_LONG_SIDE_FOR_OCR = 1600


class OcrUnavailableError(Exception):
    """Tesseract не установлен или путь к нему указан неверно."""


@dataclass
class OcrResult:
    text: str
    confidence: float  # средняя уверенность Tesseract по словам, 0–100
    variant: str  # "original" | "preprocessed"
    deskew_angle: float

    def to_dict(self) -> dict:
        return asdict(self)


def configure(settings: Settings) -> None:
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd


def check_available(settings: Settings) -> None:
    configure(settings)
    try:
        pytesseract.get_tesseract_version()
    except (pytesseract.TesseractNotFoundError, FileNotFoundError, OSError) as exc:
        raise OcrUnavailableError(
            f"Tesseract не найден по пути «{settings.tesseract_cmd}». "
            "Установите Tesseract или задайте переменную окружения TESSERACT_CMD."
        ) from exc


def _upscale(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape
    if max(h, w) >= MIN_LONG_SIDE_FOR_OCR:
        return gray
    scale = MIN_LONG_SIDE_FOR_OCR / max(h, w)
    return cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def _rotate(img: np.ndarray, angle: float, border: int = 255) -> np.ndarray:
    h, w = img.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(
        img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=border
    )


def text_ink(gray: np.ndarray) -> np.ndarray:
    """Маска штрихов текста: морфологический black-hat выделяет тёмные тонкие структуры
    на светлом фоне и не реагирует на крупные тёмные области (фон вокруг таблички)."""
    size = max(15, max(gray.shape) // 60)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (size, size))
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)
    _, ink = cv2.threshold(blackhat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return ink


def estimate_skew(ink: np.ndarray, max_angle: float, step: float) -> float:
    """Угол наклона текста методом проекционного профиля.

    Пиксели текста суммируются по строкам; при правильном повороте строки текста
    дают резкие пики, и дисперсия профиля максимальна.
    """
    h, w = ink.shape
    scale = 800 / max(h, w)
    if scale < 1:
        ink = cv2.resize(ink, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-max_angle, max_angle + step / 2, step):
        rotated = _rotate(ink, float(angle), border=0)
        score = float(np.var(rotated.sum(axis=1, dtype=np.float64)))
        if score > best_score:
            best_angle, best_score = float(angle), score
    return round(best_angle, 2)


def preprocess(gray: np.ndarray, settings: Settings) -> tuple[np.ndarray, float]:
    gray = _upscale(gray)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    contrasted = clahe.apply(gray)
    _, binary = cv2.threshold(contrasted, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    angle = estimate_skew(text_ink(contrasted), settings.deskew_max_angle, settings.deskew_step)
    if angle:
        binary = _rotate(binary, angle)
    return binary, angle


def _run(img: np.ndarray, settings: Settings, psm: int) -> tuple[str, float]:
    config = f"--oem 1 --psm {psm}"
    text = pytesseract.image_to_string(img, lang=settings.ocr_lang, config=config)
    data = pytesseract.image_to_data(
        img, lang=settings.ocr_lang, config=config, output_type=pytesseract.Output.DICT
    )
    confs = [float(c) for c, word in zip(data["conf"], data["text"]) if word.strip() and float(c) >= 0]
    return text.strip(), (sum(confs) / len(confs) if confs else 0.0)


def ocr_array(gray: np.ndarray, settings: Settings, psm: int = 6) -> OcrResult:
    """OCR изображения в оттенках серого. psm 6 — единый блок текста (шильдик)."""
    check_available(settings)
    original_text, original_conf = _run(_upscale(gray), settings, psm)
    processed, angle = preprocess(gray, settings)
    processed_text, processed_conf = _run(processed, settings, psm)
    if processed_conf > original_conf:
        return OcrResult(processed_text, round(processed_conf, 1), "preprocessed", angle)
    return OcrResult(original_text, round(original_conf, 1), "original", 0.0)


def ocr_image(path: Path, settings: Settings, psm: int = 6) -> OcrResult:
    return ocr_array(load_gray(path), settings, psm)
