"""Генератор синтетических фикстур BankVision.

Все данные вымышленные: производитель, модель, номера и характеристики не относятся
к реальным компаниям. Каждая фикстура сопровождается JSON с эталонными значениями:
в "fields" — ровно те поля, что напечатаны на этой фикстуре (отсутствующие — null).

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

FIELD_NAMES = (
    "equipment_type", "manufacturer", "model", "serial_number", "year",
    "max_diameter_mm", "center_distance_mm", "power_kw", "weight_kg",
)
# Поля, которые печатаются на шильдике (остальные — только в паспорте).
PLATE_FIELDS = ("equipment_type", "manufacturer", "model", "serial_number", "year",
                "power_kw", "weight_kg")

# Вымышленные записи об оборудовании — эталоны для шильдиков и паспортов.
EQUIPMENT = {
    "equipment_type": "Станок токарно-винторезный",
    "manufacturer": "ТОКАРЬПРОМ-СИНТЕЗ",
    "model": "ТС-1640М",
    "serial_number": "24-07815",
    "year": "2018",
    "max_diameter_mm": "400",
    "center_distance_mm": "1000",
    "power_kw": "7.5",
    "weight_kg": "2800",
}
# Модель, которой не будет в базе аналогов (сценарий «нет подходящих аналогов»).
EQUIPMENT_UNKNOWN_MODEL = {
    "equipment_type": "Станок токарный",
    "manufacturer": "ВЕКТОРСТАН-ДЕМО",
    "model": "ЛТ-3200Ф",
    "serial_number": "77-31004",
    "year": "2021",
    "max_diameter_mm": "320",
    "center_distance_mm": "750",
    "power_kw": "5.5",
    "weight_kg": "1900",
}
SERIAL_MISMATCH = "24-09156"

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


def with_fields(eq: dict, **changes) -> dict:
    """Копия записи с изменёнными полями; None означает «поле не напечатано»."""
    return {**eq, **changes}


def printed_fields(eq: dict, allowed: tuple[str, ...] = FIELD_NAMES) -> dict:
    return {k: (eq.get(k) if k in allowed else None) for k in FIELD_NAMES}


def plate_lines(eq: dict) -> list[tuple[str, bool]]:
    """Строки шильдика: (текст, жирный). Поля со значением None пропускаются."""
    lines = [
        ("manufacturer", "{}", True),
        ("equipment_type", "{}", False),
        ("model", "Модель: {}", True),
        ("serial_number", "Зав. №: {}", True),
        ("year", "Год выпуска: {}", False),
        ("power_kw", "Мощность: {} кВт", False),
        ("weight_kg", "Масса: {} кг", False),
    ]
    out = [(tpl.format(eq[k]), bold) for k, tpl, bold in lines if eq.get(k) is not None]
    return out + [("СИНТЕТИЧЕСКИЙ ОБРАЗЕЦ", False)]


def _add_noise(img: Image.Image, rng: np.random.Generator, sigma: float) -> Image.Image:
    arr = np.asarray(img).astype(np.float32)
    arr += rng.normal(0.0, sigma, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def render_plate(
    eq: dict,
    background: tuple[int, int, int] = (62, 84, 78),
    plate_top_shade: int = 205,
    font_sizes: tuple[int, int] = (52, 58),
    line_step: int = 85,
) -> Image.Image:
    """Чёткая «фотография» шильдика: металлическая табличка на тёмном фоне станка.

    Параметры оформления по умолчанию — основная выборка; отложенная выборка
    использует другие, чтобы проверка не шла на том же внешнем виде."""
    width, height = 1600, 1100
    photo = Image.new("RGB", (width, height), background)  # окраска станины
    draw = ImageDraw.Draw(photo)

    px0, py0, px1, py1 = 180, 150, 1420, 950
    # Металлическая табличка с лёгким вертикальным градиентом.
    for y in range(py0, py1):
        shade = int(plate_top_shade - 25 * (y - py0) / (py1 - py0))
        draw.line([(px0, y), (px1, y)], fill=(shade, shade, shade - 6))
    draw.rectangle([px0, py0, px1, py1], outline=(90, 90, 90), width=6)
    draw.rectangle([px0 + 20, py0 + 20, px1 - 20, py1 - 20], outline=(40, 40, 40), width=3)
    for cx, cy in [(px0 + 45, py0 + 45), (px1 - 45, py0 + 45), (px0 + 45, py1 - 45), (px1 - 45, py1 - 45)]:
        draw.ellipse([cx - 14, cy - 14, cx + 14, cy + 14], fill=(120, 120, 120), outline=(60, 60, 60))

    regular = ImageFont.truetype(str(find_font("regular")), font_sizes[0])
    bold = ImageFont.truetype(str(find_font("bold")), font_sizes[1])
    y = py0 + 75
    for text, is_bold in plate_lines(eq):
        font = bold if is_bold else regular
        draw.text((px0 + 100, y), text, font=font, fill=(25, 25, 25))
        y += line_step
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
    """Строки паспорта. Поля со значением None не печатаются."""
    def line(key: str, tpl: str) -> list[str]:
        return [tpl.format(eq[key])] if eq.get(key) is not None else []

    main = (line("equipment_type", "Наименование: {}") + line("manufacturer", "Изготовитель: {}")
            + line("model", "Модель: {}") + line("serial_number", "Заводской номер: {}")
            + line("year", "Год выпуска: {}"))
    specs = (line("max_diameter_mm", "Наибольший диаметр обработки над станиной, мм: {}")
             + line("center_distance_mm", "Расстояние между центрами, мм: {}")
             + line("power_kw", "Мощность главного привода, кВт: {}")
             + line("weight_kg", "Масса станка, кг: {}"))
    lines = [
        "ПАСПОРТ ОБОРУДОВАНИЯ",
        "Синтетический образец для демонстрации. Не является реальным документом.",
        "",
        *main,
    ]
    if specs:
        lines += ["", "Технические характеристики", *specs]
    return lines


def _pdf_bytes(doc: pymupdf.Document, **options) -> bytes:
    """Детерминированный PDF: без дат создания и со стабильным идентификатором."""
    doc.set_metadata({"producer": "BankVision generate_fixtures.py (synthetic)"})
    return doc.tobytes(no_new_id=True, **options)


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
    data = _pdf_bytes(doc, garbage=3, deflate=True)
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
    data = _pdf_bytes(doc)
    doc.close()
    return data, {"render_dpi": dpi, "rotation_deg": angle, "noise_sigma": sigma, "jpeg_quality": 85}


def _reference(kind: str, variant: str, distortions: dict, fields: dict,
               scenario: str, extra: dict | None = None) -> dict:
    ref = {
        "synthetic": True,
        "note": "Синтетическая фикстура BankVision. Все данные вымышленные.",
        "kind": kind,
        "variant": variant,
        "scenario": scenario,
        "distortions": distortions,
        "fields": fields,
    }
    if extra:
        ref.update(extra)
    return ref


def _plate_ref(variant: str, distortions: dict, eq: dict, scenario: str,
               extra: dict | None = None) -> dict:
    return _reference("plate_photo", variant, distortions, printed_fields(eq, PLATE_FIELDS),
                      scenario, {"plate_text": [t for t, _ in plate_lines(eq)]} | (extra or {}))


def _passport_ref(variant: str, distortions: dict, eq: dict, scenario: str, has_text_layer: bool,
                  extra: dict | None = None) -> dict:
    return _reference("passport_pdf", variant, distortions, printed_fields(eq), scenario,
                      {"has_text_layer": has_text_layer, "document_text": passport_lines(eq)}
                      | (extra or {}))


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=PHOTO_JPEG_QUALITY)
    return buf.getvalue()


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

    def emit(name: str, data: bytes, reference: dict) -> None:
        _write(out, name, data, reference)
        written.append(out / name)

    # Порядок генерации важен: от него зависит последовательность случайных чисел.
    # Новые фикстуры добавляются в конец, чтобы прежние оставались байт-в-байт теми же.
    plate_scenarios = {"plate_clear": "baseline", "plate_blurred": "poor_quality",
                       "plate_dark": "poor_quality", "plate_skewed": "baseline"}
    for name, (img, distortions) in make_plate_variants(EQUIPMENT, rng).items():
        emit(f"{name}.jpg", _jpeg(img),
             _plate_ref(name.removeprefix("plate_"), distortions, EQUIPMENT, plate_scenarios[name],
                        {"pair": "passport_text.pdf"}))

    text_pdf = make_text_pdf(EQUIPMENT)
    emit("passport_text.pdf", text_pdf,
         _passport_ref("text_layer", {}, EQUIPMENT, "baseline", True, {"pair": "plate_clear.jpg"}))

    scan_pdf, distortions = make_scan_pdf(text_pdf, rng)
    emit("passport_scan.pdf", scan_pdf,
         _passport_ref("scan", distortions, EQUIPMENT, "baseline", False, {"pair": "plate_clear.jpg"}))

    # --- итерация 2 ---
    noise_sigma = 4.0

    # Шильдик того же станка, но с другим заводским номером, чем в паспорте.
    mismatch = with_fields(EQUIPMENT, serial_number=SERIAL_MISMATCH)
    emit("plate_serial_mismatch.jpg", _jpeg(_add_noise(render_plate(mismatch), rng, noise_sigma)),
         _plate_ref("clear", {"noise_sigma": noise_sigma}, mismatch, "serial_mismatch",
                    {"pair": "passport_text.pdf",
                     "pair_value": {"serial_number": EQUIPMENT["serial_number"]}}))

    # Паспорт без заводского номера.
    no_serial = with_fields(EQUIPMENT, serial_number=None)
    emit("passport_no_serial.pdf", make_text_pdf(no_serial),
         _passport_ref("text_layer", {}, no_serial, "no_serial", True, {"pair": "plate_clear.jpg"}))

    # Оборудование, модели которого не будет в базе аналогов.
    extra_unknown = {"expected_in_analogs": False}
    emit("plate_unknown_model.jpg",
         _jpeg(_add_noise(render_plate(EQUIPMENT_UNKNOWN_MODEL), rng, noise_sigma)),
         _plate_ref("clear", {"noise_sigma": noise_sigma}, EQUIPMENT_UNKNOWN_MODEL, "no_analogs",
                    {"pair": "passport_unknown_model.pdf"} | extra_unknown))
    emit("passport_unknown_model.pdf", make_text_pdf(EQUIPMENT_UNKNOWN_MODEL),
         _passport_ref("text_layer", {}, EQUIPMENT_UNKNOWN_MODEL, "no_analogs", True,
                       {"pair": "plate_unknown_model.jpg"} | extra_unknown))

    # Паспорт с минимумом данных: только вид оборудования и модель.
    minimal = {k: None for k in FIELD_NAMES} | {
        "equipment_type": EQUIPMENT["equipment_type"], "model": EQUIPMENT["model"]}
    emit("passport_minimal.pdf", make_text_pdf(minimal),
         _passport_ref("text_layer", {}, minimal, "insufficient_data", True))

    # Тёмное и одновременно размытое фото — проверка метрики резкости.
    blur_radius, brightness = 4.0, 0.22
    dark_blurred = ImageEnhance.Brightness(
        render_plate(EQUIPMENT).filter(ImageFilter.GaussianBlur(blur_radius))).enhance(brightness)
    emit("plate_dark_blurred.jpg", _jpeg(_add_noise(dark_blurred, rng, noise_sigma)),
         _plate_ref("dark_blurred", {"gaussian_blur_radius": blur_radius,
                                     "brightness_factor": brightness, "noise_sigma": noise_sigma},
                    EQUIPMENT, "poor_quality"))
    return written


# ---------- отложенная выборка для проверки метрики качества ----------
# Не используется в тестах и при выборе параметров: метрика прогоняется на ней один раз.

HOLDOUT_DIR = FIXTURES_DIR / "holdout"
HOLDOUT_SEED = 777
EQUIPMENT_HOLDOUT = {
    "equipment_type": "Станок токарный",
    "manufacturer": "КРУГОВИК-ДЕМО",
    "model": "КД-250Т",
    "serial_number": "31-55702",
    "year": "2015",
    "max_diameter_mm": None,
    "center_distance_mm": None,
    "power_kw": "4.0",
    "weight_kg": "1450",
}
# Другое оформление, чем у основной выборки.
HOLDOUT_STYLE = {"background": (48, 52, 96), "plate_top_shade": 218,
                 "font_sizes": (46, 52), "line_step": 80}


def _motion_blur(img: Image.Image, length: int) -> Image.Image:
    import cv2

    kernel = np.zeros((length, length), dtype=np.float32)
    kernel[length // 2, :] = 1.0 / length  # горизонтальный смаз (дрожание руки)
    return Image.fromarray(cv2.filter2D(np.asarray(img), -1, kernel))


def generate_holdout(out: Path = HOLDOUT_DIR) -> list[Path]:
    rng = np.random.default_rng(HOLDOUT_SEED)
    out.mkdir(parents=True, exist_ok=True)
    eq = EQUIPMENT_HOLDOUT
    base = render_plate(eq, **HOLDOUT_STYLE)
    noise_sigma = 5.0

    def gauss(r: float):
        return lambda im: im.filter(ImageFilter.GaussianBlur(r))

    def bright(k: float):
        return lambda im: ImageEnhance.Brightness(im).enhance(k)

    # (имя, преобразование до шума, параметры, итоговый размер или None, качество JPEG)
    variants = [("clear", None, {}, None, PHOTO_JPEG_QUALITY)]
    variants += [(f"gauss_{r}", gauss(r), {"gaussian_blur_radius": r}, None, PHOTO_JPEG_QUALITY)
                 for r in (1.0, 1.5, 2.0, 2.5, 3.0, 5.0)]
    variants += [(f"motion_{n}", lambda im, n=n: _motion_blur(im, n), {"motion_blur_px": n}, None,
                  PHOTO_JPEG_QUALITY) for n in (9, 21)]
    variants += [(f"dark_{k}", bright(k), {"brightness_factor": k}, None, PHOTO_JPEG_QUALITY)
                 for k in (0.50, 0.42, 0.36, 0.30, 0.18)]
    variants += [(f"bright_{k}", bright(k), {"brightness_factor": k}, None, PHOTO_JPEG_QUALITY)
                 for k in (1.35, 1.6)]
    variants += [("dark_0.36_gauss_2.0", lambda im: bright(0.36)(gauss(2.0)(im)),
                  {"gaussian_blur_radius": 2.0, "brightness_factor": 0.36}, None, PHOTO_JPEG_QUALITY)]
    variants += [(f"lowres_{w}", None, {"resize_to": [w, round(w * 1100 / 1600)]},
                  (w, round(w * 1100 / 1600)), PHOTO_JPEG_QUALITY) for w in (760, 960)]
    variants += [("jpeg_q40", None, {"jpeg_quality": 40}, None, 40)]

    written = []
    for name, transform, params, size, quality in variants:
        img = transform(base) if transform else base
        img = _add_noise(img, rng, noise_sigma)
        if size:
            img = img.resize(size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        ref = _plate_ref(name, params | {"noise_sigma": noise_sigma}, eq, "holdout",
                         {"seed": HOLDOUT_SEED,
                          "note_holdout": "Отложенная выборка: только для однократной проверки метрики."})
        _write(out, f"holdout_{name}.jpg", buf.getvalue(), ref)
        written.append(out / f"holdout_{name}.jpg")
    return written


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--holdout":
        paths = generate_holdout(Path(args[1]) if len(args) > 1 else HOLDOUT_DIR)
    else:
        paths = generate(Path(args[0]) if args else FIXTURES_DIR)
    for path in paths:
        print(path)
