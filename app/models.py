"""Модели предметной области."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StoredFile:
    id: int
    application_id: int
    kind: str  # "photo" | "document"
    original_name: str
    stored_path: str
    content_type: str
    size: int


@dataclass
class Application:
    id: int
    created_at: str
    status: str
    files: list[StoredFile] = field(default_factory=list)

    @property
    def photos(self) -> list[StoredFile]:
        return [f for f in self.files if f.kind == "photo"]

    @property
    def documents(self) -> list[StoredFile]:
        return [f for f in self.files if f.kind == "document"]
