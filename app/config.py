"""Настройки приложения. Все значения можно переопределить переменными окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

MB = 1024 * 1024


@dataclass(frozen=True)
class Settings:
    data_dir: Path = BASE_DIR / "data"
    upload_dir: Path = BASE_DIR / "data" / "uploads"
    db_path: Path = BASE_DIR / "data" / "bankvision.db"
    tesseract_cmd: str = DEFAULT_TESSERACT_CMD

    max_file_size: int = 10 * MB
    min_photos: int = 1
    max_photos: int = 5
    photo_extensions: frozenset[str] = field(default_factory=lambda: frozenset({".jpg", ".jpeg", ".png"}))
    document_extensions: frozenset[str] = field(
        default_factory=lambda: frozenset({".pdf", ".jpg", ".jpeg", ".png"})
    )

    # --- Качество фото ---
    # Пороги заданы заранее по общепринятым эвристикам, не откалиброваны на реальных
    # фото. Метрики считаются на изображении, приведённом к длинной стороне
    # quality_long_side, чтобы не зависеть от разрешения камеры.
    quality_long_side: int = 1024
    # Дисперсия Лапласиана (Pech-Pacheco et al., 2000); 100 — распространённая эвристика.
    blur_laplacian_min: float = 100.0
    # Перед расчётом резкости контраст растягивается между этими перцентилями яркости,
    # затем изображение сглаживается гауссианой (Лапласиан гауссианы, σ=1 — канонический
    # масштаб Марра–Хилдрет), чтобы шум сенсора не принимался за резкость.
    blur_contrast_percentiles: tuple[float, float] = (1.0, 99.0)
    blur_presmooth_sigma: float = 1.0
    # Средняя яркость в оттенках серого, шкала 0–255.
    brightness_min: float = 60.0
    brightness_max: float = 200.0
    # Минимальное разрешение исходного фото (длинная × короткая сторона).
    min_long_side: int = 800
    min_short_side: int = 600

    # --- PDF и OCR ---
    ocr_lang: str = "rus+eng"
    # Страница без текстового слоя: меньше этого числа непробельных символов.
    pdf_min_text_chars: int = 30
    pdf_ocr_dpi: int = 300
    # Диапазон и шаг поиска угла наклона при выравнивании, градусы.
    deskew_max_angle: float = 10.0
    deskew_step: float = 0.5

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.environ.get("BANKVISION_DATA_DIR", BASE_DIR / "data"))
        return cls(
            data_dir=data_dir,
            upload_dir=Path(os.environ.get("BANKVISION_UPLOAD_DIR", data_dir / "uploads")),
            db_path=Path(os.environ.get("BANKVISION_DB_PATH", data_dir / "bankvision.db")),
            tesseract_cmd=os.environ.get("TESSERACT_CMD", DEFAULT_TESSERACT_CMD),
            max_file_size=int(os.environ.get("BANKVISION_MAX_FILE_SIZE", 10 * MB)),
        )
