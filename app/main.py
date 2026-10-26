"""FastAPI-приложение BankVision."""
from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db
from app.config import Settings
from app.services import crosscheck, review
from app.services.analysis import analyze_application
from app.services.ingest import IncomingFile, UploadError, create_application_with_files

APP_DIR = Path(__file__).resolve().parent


def _to_incoming(files: list[UploadFile] | None) -> list[IncomingFile]:
    # Браузер присылает пустую часть без имени, если поле файла не заполнено.
    return [IncomingFile(f.filename, f.file) for f in (files or []) if f.filename]


def _error_status(err: UploadError) -> int:
    return 413 if err.too_large else 400


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    db.init_db(settings.db_path)

    app = FastAPI(title="BankVision", description="MVP оценки залогового оборудования")
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=APP_DIR / "templates")

    def _create(photos: list[UploadFile] | None, document: list[UploadFile] | None) -> int:
        docs = _to_incoming(document)
        if len(docs) > 1:
            raise UploadError(["Загрузите один документ."])
        application_id = create_application_with_files(
            _to_incoming(photos), docs[0] if docs else None, settings
        )
        # Обработка синхронная: для MVP с 1–5 фото это несколько секунд.
        analyze_application(application_id, settings)
        return application_id

    def _load(application_id: int):
        with db.connect(settings.db_path) as conn:
            application = db.get_application(conn, application_id)
            journal = db.get_journal(conn, application_id) if application else []
            analysis = db.get_analysis(conn, application_id) if application else {}
        if application is None:
            raise HTTPException(status_code=404, detail="Заявка не найдена.")
        return application, journal, analysis

    def _fields(application_id: int):
        """(извлечённые поля, итоговые значения, сверка, общий список флагов)."""
        application, _, analysis = _load(application_id)
        with db.connect(settings.db_path) as conn:
            extracted = db.get_extracted(conn, application_id)
            finals = db.get_final(conn, application_id)
            checks = review.compute_checks(conn, application_id, settings)
        photo_flags = {
            f.original_name: (analysis.get(f.id, {}).get("quality") or {}).get("flags", [])
            for f in application.photos
        }
        return extracted, finals, checks, crosscheck.flags(checks, photo_flags)

    def _edit(application_id: int, edits: dict[str, str | None],
              sources: dict[str, str] | None = None) -> list[dict]:
        _load(application_id)  # 404, если заявки нет
        try:
            return review.apply_edits(settings, application_id, edits, sources)
        except (review.UnknownFieldError, review.InvalidSourceError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ---------- HTML ----------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(
            request, "index.html", {"settings": settings, "errors": []}
        )

    @app.post("/applications", response_class=HTMLResponse)
    def create_from_form(
        request: Request,
        photos: list[UploadFile] | None = File(None),
        document: list[UploadFile] | None = File(None),
    ):
        try:
            application_id = _create(photos, document)
        except UploadError as err:
            return templates.TemplateResponse(
                request,
                "index.html",
                {"settings": settings, "errors": err.messages},
                status_code=_error_status(err),
            )
        return RedirectResponse(f"/applications/{application_id}", status_code=303)

    @app.get("/applications/{application_id}", response_class=HTMLResponse)
    def view_application(request: Request, application_id: int):
        application, journal, analysis = _load(application_id)
        return templates.TemplateResponse(
            request,
            "application.html",
            {"application": application, "journal": journal, "analysis": analysis},
        )

    @app.get("/applications/{application_id}/review", response_class=HTMLResponse)
    def review_fields(request: Request, application_id: int, saved: int | None = None):
        application, journal, _ = _load(application_id)
        _, finals, checks, flag_list = _fields(application_id)
        edits = [j for j in journal if j["event"] == review.EVENT_FIELD_EDITED]
        return templates.TemplateResponse(
            request,
            "review.html",
            {
                "application": application,
                "rows": review.review_rows(checks, finals),
                "flags": flag_list,
                "edits": edits,
                "saved": saved,
            },
        )

    @app.post("/applications/{application_id}/review")
    async def save_review(request: Request, application_id: int):
        form = await request.form()
        def by_prefix(prefix: str) -> dict[str, str]:
            return {k[len(prefix):]: str(v) for k, v in form.items() if k.startswith(prefix)}

        changes = _edit(application_id, by_prefix("field__"), by_prefix("source__"))
        return RedirectResponse(
            f"/applications/{application_id}/review?saved={len(changes)}", status_code=303
        )

    # ---------- API ----------

    @app.post("/api/applications", status_code=201)
    def api_create(
        photos: list[UploadFile] | None = File(None),
        document: list[UploadFile] | None = File(None),
    ):
        try:
            application_id = _create(photos, document)
        except UploadError as err:
            return JSONResponse(status_code=_error_status(err), content={"errors": err.messages})
        application, _, analysis = _load(application_id)
        return {"application": application, "analysis": analysis}

    @app.get("/api/applications/{application_id}")
    def api_get(application_id: int):
        application, journal, analysis = _load(application_id)
        return {"application": application, "journal": journal, "analysis": analysis}

    @app.get("/api/applications/{application_id}/fields")
    def api_fields(application_id: int):
        extracted, finals, checks, flag_list = _fields(application_id)
        return {
            "extracted": extracted,
            "final": finals,
            "crosscheck": {name: c.to_dict() for name, c in checks.items()},
            "flags": flag_list,
        }

    @app.put("/api/applications/{application_id}/fields")
    def api_edit_fields(application_id: int, edits: dict[str, str | None] = Body(...)):
        changes = _edit(application_id, edits)
        _, finals, _, _ = _fields(application_id)
        return {"changes": changes, "final": finals}

    return app


app = create_app()
