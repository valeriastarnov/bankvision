"""FastAPI-приложение BankVision."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db
from app.config import Settings
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
        return create_application_with_files(
            _to_incoming(photos), docs[0] if docs else None, settings
        )

    def _load(application_id: int):
        with db.connect(settings.db_path) as conn:
            application = db.get_application(conn, application_id)
            journal = db.get_journal(conn, application_id) if application else []
        if application is None:
            raise HTTPException(status_code=404, detail="Заявка не найдена.")
        return application, journal

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
        application, journal = _load(application_id)
        return templates.TemplateResponse(
            request, "application.html", {"application": application, "journal": journal}
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
        application, _ = _load(application_id)
        return application

    @app.get("/api/applications/{application_id}")
    def api_get(application_id: int):
        application, journal = _load(application_id)
        return {"application": application, "journal": journal}

    return app


app = create_app()
