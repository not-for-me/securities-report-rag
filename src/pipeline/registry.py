from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.models import ReportMetadata

SCHEMA_VERSION = "1.0.0"
SUPPORTED_SCHEMA_PREFIXES = {"1.0"}

PipelineStatus = str


def now_iso8601() -> str:
    return datetime.now(UTC).isoformat()


def compute_file_hash(file_path: Path) -> str:
    sha256 = hashlib.sha256()
    with file_path.open("rb") as file:
        for block in iter(lambda: file.read(8192), b""):
            sha256.update(block)
    return f"sha256:{sha256.hexdigest()}"


class MetadataRegistry:
    """`data/metadata.json` 레지스트리 관리."""

    def __init__(self, path: str | Path = "data/metadata.json"):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_registry()

        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._validate_schema(data)
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data["last_updated"] = now_iso8601()
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def register_source_file(self, file_path: Path) -> str:
        data = self.load()
        document_id = file_path.stem
        file_hash = compute_file_hash(file_path)

        duplicate = self.find_document_by_hash(file_hash=file_hash, data=data)
        if duplicate and duplicate != document_id:
            raise ValueError(f"Duplicate PDF detected: {file_path.name} (same as {duplicate})")

        entry = data["documents"].get(document_id, {})
        entry = {
            **entry,
            "source_file": file_path.name,
            "file_hash": file_hash,
            "file_size_bytes": file_path.stat().st_size,
            "added_at": entry.get("added_at", now_iso8601()),
            "status": entry.get("status", "pending"),
            "pipeline_history": entry.get("pipeline_history", []),
            "metadata": entry.get("metadata", {}),
        }
        data["documents"][document_id] = entry
        self.save(data)
        return document_id

    def update_status(self, document_id: str, status: PipelineStatus) -> None:
        data = self.load()
        document = data["documents"].setdefault(document_id, {})
        document["status"] = status
        self.save(data)

    def set_report_metadata(self, document_id: str, metadata: ReportMetadata) -> None:
        data = self.load()
        document = data["documents"].setdefault(document_id, {})
        document["metadata"] = metadata
        self.save(data)

    def append_history(
        self,
        document_id: str,
        *,
        stage: str,
        success: bool,
        error_message: str | None = None,
        chunk_count: int | None = None,
        vector_count: int | None = None,
    ) -> None:
        data = self.load()
        document = data["documents"].setdefault(document_id, {})
        history = document.setdefault("pipeline_history", [])

        item: dict[str, Any] = {
            "stage": stage,
            "timestamp": now_iso8601(),
            "success": success,
        }
        if error_message:
            item["error_message"] = error_message
        if chunk_count is not None:
            item["chunk_count"] = chunk_count
        if vector_count is not None:
            item["vector_count"] = vector_count

        history.append(item)
        self.save(data)

    def get_documents_to_process(self, raw_pdf_dir: str | Path = "data/raw_pdfs") -> list[Path]:
        raw_dir = Path(raw_pdf_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)
        data = self.load()

        to_process: list[Path] = []
        for pdf_path in sorted(raw_dir.glob("*.pdf")):
            document_id = pdf_path.stem
            document = data["documents"].get(document_id)
            if document is None:
                to_process.append(pdf_path)
                continue
            if document.get("status") != "indexed":
                to_process.append(pdf_path)
        return to_process

    @staticmethod
    def find_document_by_hash(file_hash: str, data: dict[str, Any]) -> str | None:
        for document_id, document in data.get("documents", {}).items():
            if document.get("file_hash") == file_hash:
                return document_id
        return None

    @staticmethod
    def _empty_registry() -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "last_updated": now_iso8601(),
            "documents": {},
        }

    @staticmethod
    def _validate_schema(data: dict[str, Any]) -> None:
        version = str(data.get("schema_version", "0.0.0"))
        major_minor = ".".join(version.split(".")[:2])
        if major_minor not in SUPPORTED_SCHEMA_PREFIXES:
            raise ValueError(f"Unsupported metadata schema version: {version}")

