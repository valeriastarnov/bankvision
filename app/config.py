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
