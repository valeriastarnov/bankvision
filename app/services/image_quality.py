"""Проверка качества фото: размытость, яркость, разрешение.

Это правило-ориентированные проверки с порогами из config, а не оценка вероятности.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from app.config import Settings

FLAG_BLURRY = "blurry"
FLAG_DARK = "too_dark"
FLAG_BRIGHT = "too_bright"
FLAG_LOW_RES = "low_resolution"

HINTS = {
    FLAG_BLURRY: "Фото размыто. Переснимите шильдик при хорошем освещении, удерживая "
    "телефон неподвижно; коснитесь экрана, чтобы сфокусироваться на табличке.",
    FLAG_DARK: "Фото слишком тёмное. Включите освещение или вспышку, избегайте тени на табличке.",
    FLAG_BRIGHT: "Фото пересвечено. Уберите прямой свет или блик, снимите под небольшим углом.",
    FLAG_LOW_RES: "Низкое разрешение. Снимите ближе или используйте основную камеру "
    "без цифрового зума.",
}


class ImageReadError(Exception):
    pass


@dataclass
class QualityResult:
    width: int
    height: int
    blur_score: float  # дисперсия Лапласиана
    brightness: float  # средняя яркость 0–255
    flags: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.flags

    def to_dict(self) -> dict:
        return asdict(self) | {"ok": self.ok}


def load_gray(path: Path) -> np.ndarray:
    # cv2.imread не понимает не-ASCII пути на Windows, поэтому читаем байты сами.
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ImageReadError(f"Не удалось прочитать изображение «{path.name}»: файл повреждён.")
    return img


def _normalize_size(gray: np.ndarray, long_side: int) -> np.ndarray:
    h, w = gray.shape
    scale = long_side / max(h, w)
    if scale >= 1:
        return gray
    return cv2.resize(gray, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)


def assess_array(gray: np.ndarray, settings: Settings) -> QualityResult:
    h, w = gray.shape
    norm = _normalize_size(gray, settings.quality_long_side)
    blur_score = float(cv2.Laplacian(norm, cv2.CV_64F).var())
    brightness = float(norm.mean())

    flags: list[str] = []
    if blur_score < settings.blur_laplacian_min:
        flags.append(FLAG_BLURRY)
    if brightness < settings.brightness_min:
        flags.append(FLAG_DARK)
    elif brightness > settings.brightness_max:
        flags.append(FLAG_BRIGHT)
    if max(h, w) < settings.min_long_side or min(h, w) < settings.min_short_side:
        flags.append(FLAG_LOW_RES)

    return QualityResult(
        width=w,
        height=h,
        blur_score=round(blur_score, 1),
        brightness=round(brightness, 1),
        flags=flags,
        hints=[HINTS[f] for f in flags],
    )


def assess(path: Path, settings: Settings) -> QualityResult:
    return assess_array(load_gray(path), settings)
