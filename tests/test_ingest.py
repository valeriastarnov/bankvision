"""Итерация 0: создание заявки, загрузка файлов, отклонение неверных форматов."""
from __future__ import annotations

import sqlite3


def _upload(client, photos, document, url="/api/applications"):
    files = [("photos", p) for p in photos]
    if document is not None:
        files.append(("document", document))
    return client.post(url, files=files)


def _count(settings, table):
    with sqlite3.connect(settings.db_path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _stored_files(settings):
    return [p for p in settings.upload_dir.rglob("*") if p.is_file()]


def test_valid_upload_saves_files(client, settings, jpg_bytes, png_bytes, pdf_bytes):
    resp = _upload(
        client,
        [("plate.jpg", jpg_bytes, "image/jpeg"), ("general.png", png_bytes, "image/png")],
        ("passport.pdf", pdf_bytes, "application/pdf"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()["application"]
    assert body["status"] == "processed"
    kinds = sorted(f["kind"] for f in body["files"])
    assert kinds == ["document", "photo", "photo"]

    saved = _stored_files(settings)
    assert len(saved) == 3
    contents = {p.read_bytes() for p in saved}
    assert contents == {jpg_bytes, png_bytes, pdf_bytes}
    for f in body["files"]:
        assert (settings.upload_dir / f["stored_path"]).is_file()

    detail = client.get(f"/api/applications/{body['id']}").json()
    assert detail["journal"][0]["event"] == "created"


def test_document_as_image_is_accepted(client, jpg_bytes, png_bytes):
    resp = _upload(
        client, [("plate.jpg", jpg_bytes, "image/jpeg")], ("scan.png", png_bytes, "image/png")
    )
    assert resp.status_code == 201, resp.text


def test_wrong_photo_format_rejected(client, settings, pdf_bytes):
    resp = _upload(
        client,
        [("photo.gif", b"GIF89a....", "image/gif")],
        ("passport.pdf", pdf_bytes, "application/pdf"),
    )
    assert resp.status_code == 400
    errors = resp.json()["errors"]
    assert any("photo.gif" in e and "неподдерживаемый формат" in e for e in errors)
    assert _count(settings, "applications") == 0
    assert _stored_files(settings) == []


def test_pdf_not_allowed_as_photo(client, pdf_bytes):
    resp = _upload(
        client,
        [("photo.pdf", pdf_bytes, "application/pdf")],
        ("passport.pdf", pdf_bytes, "application/pdf"),
    )
    assert resp.status_code == 400
    assert "Допустимы: JPG, PNG" in resp.json()["errors"][0]


def test_renamed_file_rejected_by_signature(client, settings, jpg_bytes):
    resp = _upload(
        client,
        [("plate.jpg", jpg_bytes, "image/jpeg")],
        ("passport.pdf", b"this is not a pdf", "application/pdf"),
    )
    assert resp.status_code == 400
    assert "не соответствует формату PDF" in resp.json()["errors"][0]
    assert _stored_files(settings) == []


def test_oversized_file_rejected(client, settings, pdf_bytes):
    big = b"\xff\xd8\xff" + b"\x00" * (settings.max_file_size + 1)
    resp = _upload(
        client, [("big.jpg", big, "image/jpeg")], ("passport.pdf", pdf_bytes, "application/pdf")
    )
    assert resp.status_code == 413
    assert "больше" in resp.json()["errors"][0]
    assert _stored_files(settings) == []


def test_empty_file_rejected(client, pdf_bytes):
    resp = _upload(
        client, [("empty.jpg", b"", "image/jpeg")], ("passport.pdf", pdf_bytes, "application/pdf")
    )
    assert resp.status_code == 400
    assert "пустой" in resp.json()["errors"][0]


def test_no_photos_rejected(client, pdf_bytes):
    resp = _upload(client, [], ("passport.pdf", pdf_bytes, "application/pdf"))
    assert resp.status_code == 400
    assert "хотя бы 1 фото" in resp.json()["errors"][0]


def test_too_many_photos_rejected(client, settings, jpg_bytes, pdf_bytes):
    photos = [(f"p{i}.jpg", jpg_bytes, "image/jpeg") for i in range(6)]
    resp = _upload(client, photos, ("passport.pdf", pdf_bytes, "application/pdf"))
    assert resp.status_code == 400
    assert "не более 5 фото" in resp.json()["errors"][0]
    assert _count(settings, "applications") == 0


def test_five_photos_accepted(client, jpg_bytes, pdf_bytes):
    photos = [(f"p{i}.jpg", jpg_bytes, "image/jpeg") for i in range(5)]
    resp = _upload(client, photos, ("passport.pdf", pdf_bytes, "application/pdf"))
    assert resp.status_code == 201, resp.text


def test_missing_document_rejected(client, jpg_bytes):
    resp = _upload(client, [("plate.jpg", jpg_bytes, "image/jpeg")], None)
    assert resp.status_code == 400
    assert "документ" in resp.json()["errors"][0]


def test_one_bad_file_rejects_whole_upload(client, settings, jpg_bytes, pdf_bytes):
    resp = _upload(
        client,
        [("ok.jpg", jpg_bytes, "image/jpeg"), ("bad.bmp", b"BM..", "image/bmp")],
        ("passport.pdf", pdf_bytes, "application/pdf"),
    )
    assert resp.status_code == 400
    assert _count(settings, "applications") == 0
    assert _count(settings, "files") == 0
    assert _stored_files(settings) == []


def test_html_form_flow(client, jpg_bytes, pdf_bytes):
    assert client.get("/").status_code == 200

    resp = _upload(
        client,
        [("plate.jpg", jpg_bytes, "image/jpeg")],
        ("passport.pdf", pdf_bytes, "application/pdf"),
        url="/applications",
    )
    assert resp.status_code == 200  # после редиректа 303
    assert "Заявка №1" in resp.text
    assert "plate.jpg" in resp.text

    bad = _upload(
        client,
        [("plate.txt", b"hello", "text/plain")],
        ("passport.pdf", pdf_bytes, "application/pdf"),
        url="/applications",
    )
    assert bad.status_code == 400
    assert "Файлы не приняты" in bad.text
    assert "неподдерживаемый формат" in bad.text


def test_unknown_application_404(client):
    assert client.get("/api/applications/999").status_code == 404
