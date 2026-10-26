"""Генератор синтетических фикстур BankVision.

Все данные вымышленные: производитель, модель, номера и характеристики не относятся
к реальным компаниям. Каждая фикстура сопровождается JSON с эталонными значениями.

Запуск (PowerShell):  .\\.venv\\Scripts\\python.exe tests\\generate_fixtures.py
"""
from __future__ import annotations

import io
import json
import random
import sys
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SEED = 20261026
PHOTO_JPEG_QUALITY = 90  # типичное качество JPEG с камеры телефона

# Вымышленная запись об оборудовании — эталон для шильдиков и паспортов.
EQUIPMENT = {
    "equipment_type": "Станок токарно-винторезный",
    "manufacturer": "ТОКАРЬПРОМ-СИНТЕЗ",
    "model": "ТС-1640М",
    "serial_number": "24-07815",
    "year": "2018",
    "characteristics": {
        "max_diameter_mm": "400",
        "center_distance_mm": "1000",
        "power_kw": "7.5",
        "weight_kg": "2800",
    },
}

FONT_CANDIDATES = {
    "regular": [
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path(r"C:\Windows\Fonts\DejaVuSans.ttf"),
    ],
    "bold": [
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path(r"C:\Windows\Fonts\DejaVuSans-Bold.ttf"),
    ],
}


def find_font(style: str = "regular") -> Path:
    for path in FONT_CANDIDATES[style]:
        if path.exists():
            return path
    raise FileNotFoundError(
        f"Не найден шрифт с кириллицей ({style}). Проверены: "
        + ", ".join(str(p) for p in FONT_CANDIDATES[style])
    )


def plate_lines(eq: dict) -> list[tuple[str, bool]]:
    """Строки шильдика: (текст, жирный)."""
    ch = eq["characteristics"]
    return [
        (eq["manufacturer"], True),
        (eq["equipment_type"], False),
        (f"Модель: {eq['model']}", True),
        (f"Зав. №: {eq['serial_number']}", True),
        (f"Год выпуска: {eq['year']}", False),
        (f"Мощность: {ch['power_kw']} кВт", False),
        (f"Масса: {ch['weight_kg']} кг", False),
        ("СИНТЕТИЧЕСКИЙ ОБРАЗЕЦ", False),
    ]


def _add_noise(img: Image.Image, rng: np.random.Generator, sigma: float) -> Image.Image:
    arr = np.asarray(img).astype(np.float32)
    arr += rng.normal(0.0, sigma, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def render_plate(eq: dict) -> Image.Image:
    """Чёткая «фотография» шильдика: металлическая табличка на тёмном фоне станка."""
    width, height = 1600, 1100
    photo = Image.new("RGB", (width, height), (62, 84, 78))  # окраска станины
    draw = ImageDraw.Draw(photo)

    px0, py0, px1, py1 = 180, 150, 1420, 950
    # Металлическая табличка с лёгким вертикальным градиентом.
    for y in range(py0, py1):
        shade = int(205 - 25 * (y - py0) / (py1 - py0))
        draw.line([(px0, y), (px1, y)], fill=(shade, shade, shade - 6))
    draw.rectangle([px0, py0, px1, py1], outline=(90, 90, 90), width=6)
    draw.rectangle([px0 + 20, py0 + 20, px1 - 20, py1 - 20], outline=(40, 40, 40), width=3)
    for cx, cy in [(px0 + 45, py0 + 45), (px1 - 45, py0 + 45), (px0 + 45, py1 - 45), (px1 - 45, py1 - 45)]:
        draw.ellipse([cx - 14, cy - 14, cx + 14, cy + 14], fill=(120, 120, 120), outline=(60, 60, 60))

    regular = ImageFont.truetype(str(find_font("regular")), 52)
    bold = ImageFont.truetype(str(find_font("bold")), 58)
    y = py0 + 75
    for text, is_bold in plate_lines(eq):
        font = bold if is_bold else regular
        draw.text((px0 + 100, y), text, font=font, fill=(25, 25, 25))
        y += 85
    return photo


def make_plate_variants(eq: dict, rng: np.random.Generator) -> dict[str, tuple[Image.Image, dict]]:
    base = render_plate(eq)
    noise_sigma = 4.0
    variants: dict[str, tuple[Image.Image, dict]] = {}

    variants["plate_clear"] = (
        _add_noise(base, rng, noise_sigma),
        {"noise_sigma": noise_sigma},
    )
    blur_radius = 4.0
    variants["plate_blurred"] = (
        _add_noise(base.filter(ImageFilter.GaussianBlur(blur_radius)), rng, noise_sigma),
        {"gaussian_blur_radius": blur_radius, "noise_sigma": noise_sigma},
    )
    brightness = 0.22
    variants["plate_dark"] = (
        _add_noise(ImageEnhance.Brightness(base).enhance(brightness), rng, noise_sigma),
        {"brightness_factor": brightness, "noise_sigma": noise_sigma},
    )
    angle = 7.0
    rotated = base.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=(62, 84, 78))
    variants["plate_skewed"] = (
        _add_noise(rotated, rng, noise_sigma),
        {"rotation_deg": angle, "noise_sigma": noise_sigma},
    )
    return variants


def passport_lines(eq: dict) -> list[str]:
    ch = eq["characteristics"]
    return [
        "ПАСПОРТ ОБОРУДОВАНИЯ",
        "Синтетический образец для демонстрации. Не является реальным документом.",
        "",
        f"Наименование: {eq['equipment_type']}",
        f"Изготовитель: {eq['manufacturer']}",
        f"Модель: {eq['model']}",
        f"Заводской номер: {eq['serial_number']}",
        f"Год выпуска: {eq['year']}",
        "",
        "Технические характеристики",
        f"Наибольший диаметр обработки над станиной, мм: {ch['max_diameter_mm']}",
        f"Расстояние между центрами, мм: {ch['center_distance_mm']}",
        f"Мощность главного привода, кВт: {ch['power_kw']}",
        f"Масса станка, кг: {ch['weight_kg']}",
    ]


def make_text_pdf(eq: dict) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # A4, пункты
    font_file = str(find_font("regular"))
    y = 80
    for i, line in enumerate(passport_lines(eq)):
        size = 16 if i == 0 else 11
        if line:
            page.insert_text((60, y), line, fontsize=size, fontname="F0", fontfile=font_file)
        y += 26 if i == 0 else 20
    doc.subset_fonts()
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data


def make_scan_pdf(text_pdf: bytes, rng: np.random.Generator) -> tuple[bytes, dict]:
    """«Скан»: растр страницы с наклоном и шумом, без текстового слоя."""
    dpi, angle, sigma = 200, 1.5, 6.0
    src = pymupdf.open(stream=text_pdf, filetype="pdf")
    pix = src[0].get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    src.close()

    img = ImageEnhance.Brightness(img).enhance(0.93)  # сероватая бумага
    img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=(235, 235, 235))
    img = _add_noise(img, rng, sigma)

    jpg = io.BytesIO()
    img.save(jpg, format="JPEG", quality=85)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=jpg.getvalue())
    data = doc.tobytes()
    doc.close()
    return data, {"render_dpi": dpi, "rotation_deg": angle, "noise_sigma": sigma, "jpeg_quality": 85}


def _reference(kind: str, variant: str, distortions: dict, extra: dict | None = None) -> dict:
    ref = {
        "synthetic": True,
        "note": "Синтетическая фикстура BankVision. Все данные вымышленные.",
        "kind": kind,
        "variant": variant,
        "distortions": distortions,
        "fields": EQUIPMENT,
    }
    if extra:
        ref.update(extra)
    return ref


def _write(out: Path, name: str, data: bytes, reference: dict) -> None:
    (out / name).write_bytes(data)
    stem = Path(name).stem
    (out / f"{stem}.json").write_text(
        json.dumps(reference, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def generate(out: Path = FIXTURES_DIR) -> list[Path]:
    random.seed(SEED)
    rng = np.random.default_rng(SEED)
    out.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for name, (img, distortions) in make_plate_variants(EQUIPMENT, rng).items():
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=PHOTO_JPEG_QUALITY)
        _write(out, f"{name}.jpg", buf.getvalue(),
               _reference("plate_photo", name.removeprefix("plate_"), distortions,
                          {"plate_text": [t for t, _ in plate_lines(EQUIPMENT)]}))
        written.append(out / f"{name}.jpg")

    text_pdf = make_text_pdf(EQUIPMENT)
    _write(out, "passport_text.pdf", text_pdf,
           _reference("passport_pdf", "text_layer", {},
                      {"has_text_layer": True, "document_text": passport_lines(EQUIPMENT)}))
    written.append(out / "passport_text.pdf")

    scan_pdf, distortions = make_scan_pdf(text_pdf, rng)
    _write(out, "passport_scan.pdf", scan_pdf,
           _reference("passport_pdf", "scan", distortions,
                      {"has_text_layer": False, "document_text": passport_lines(EQUIPMENT)}))
    written.append(out / "passport_scan.pdf")
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURES_DIR
    for path in generate(target):
        print(path)
