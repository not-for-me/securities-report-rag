from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.models import ReportMetadata

SCHEMA_VERSION = "1.0.0"
SUPPORTED_SCHEMA_PREFIXES = {"1.0"}

PipelineStatus = str


@dataclass(frozen=True, slots=True)
class DocumentProcessingPlan:
    pdf_path: Path
    document_id: str
    file_hash: str
    reason: str
    previous_status: str | None


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

    def register_source_file(
        self,
        file_path: Path,
        *,
        file_hash: str | None = None,
        reprocess_reason: str | None = None,
    ) -> str:
        data = self.load()
        document_id = file_path.stem
        current_hash = file_hash or compute_file_hash(file_path)

        duplicate = self.find_document_by_hash(file_hash=current_hash, data=data)
        if duplicate and duplicate != document_id:
            raise ValueError(f"Duplicate PDF detected: {file_path.name} (same as {duplicate})")

        previous_entry = data["documents"].get(document_id, {})
        previous_hash = previous_entry.get("file_hash")
        changed_hash = previous_hash is not None and previous_hash != current_hash
        status = previous_entry.get("status", "pending")
        if reprocess_reason or changed_hash:
            status = "pending"

        entry = {
            **previous_entry,
            "source_file": file_path.name,
            "file_hash": current_hash,
            "file_size_bytes": file_path.stat().st_size,
            "added_at": previous_entry.get("added_at", now_iso8601()),
            "status": status,
            "pipeline_history": previous_entry.get("pipeline_history", []),
            "metadata": previous_entry.get("metadata", {}),
        }
        if reprocess_reason:
            entry["reprocess_reason"] = reprocess_reason
        elif changed_hash:
            entry["reprocess_reason"] = "hash_changed"

        data["documents"][document_id] = entry
        self.save(data)
        return document_id

    def mark_indexed(self, document_id: str, *, file_hash: str, vector_count: int | None = None) -> None:
        data = self.load()
        document = data["documents"].setdefault(document_id, {})
        document["status"] = "indexed"
        document["indexed_file_hash"] = file_hash
        document.pop("reprocess_reason", None)
        document.pop("last_error", None)
        if vector_count is not None:
            document["vector_count"] = vector_count
        self.save(data)

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

    def get_document_snapshot(self, document_id: str) -> dict[str, Any] | None:
        data = self.load()
        document = data.get("documents", {}).get(document_id)
        if document is None:
            return None
        return copy.deepcopy(document)

    def rollback_document(
        self,
        document_id: str,
        *,
        snapshot: dict[str, Any] | None,
        stage: str,
        error_message: str,
    ) -> None:
        data = self.load()
        timestamp = now_iso8601()
        if snapshot is None:
            document = data["documents"].setdefault(document_id, {})
            history = document.setdefault("pipeline_history", [])
            history.append(
                {
                    "stage": stage,
                    "timestamp": timestamp,
                    "success": False,
                    "error_message": error_message,
                    "rolled_back": False,
                }
            )
            document["status"] = "failed"
            document["last_error"] = {
                "stage": stage,
                "timestamp": timestamp,
                "message": error_message,
            }
            self.save(data)
            return

        restored = copy.deepcopy(snapshot)
        history = restored.setdefault("pipeline_history", [])
        history.append(
            {
                "stage": stage,
                "timestamp": timestamp,
                "success": False,
                "error_message": error_message,
                "rolled_back": True,
            }
        )
        restored["last_error"] = {
            "stage": stage,
            "timestamp": timestamp,
            "message": error_message,
        }
        restored["status"] = snapshot.get("status", "indexed")
        data["documents"][document_id] = restored
        self.save(data)

    def mark_failed(self, document_id: str, *, stage: str, error_message: str, rolled_back: bool = False) -> None:
        data = self.load()
        document = data["documents"].setdefault(document_id, {})
        history = document.setdefault("pipeline_history", [])
        timestamp = now_iso8601()
        history.append(
            {
                "stage": stage,
                "timestamp": timestamp,
                "success": False,
                "error_message": error_message,
                "rolled_back": rolled_back,
            }
        )
        document["status"] = "failed"
        document["last_error"] = {
            "stage": stage,
            "timestamp": timestamp,
            "message": error_message,
        }
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
        rolled_back: bool | None = None,
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
        if rolled_back is not None:
            item["rolled_back"] = rolled_back

        history.append(item)
        self.save(data)

    def plan_for_pdf(self, pdf_path: str | Path, *, reason_override: str | None = None) -> DocumentProcessingPlan:
        path = Path(pdf_path)
        file_hash = compute_file_hash(path)
        document_id = path.stem

        data = self.load()
        document = data.get("documents", {}).get(document_id)
        previous_status = None if document is None else str(document.get("status"))

        if reason_override is not None:
            reason = reason_override
        elif document is None:
            reason = "new"
        elif document.get("file_hash") != file_hash:
            reason = "hash_changed"
        elif document.get("status") != "indexed":
            reason = f"status_{document.get('status', 'unknown')}"
        else:
            reason = "up_to_date"

        return DocumentProcessingPlan(
            pdf_path=path,
            document_id=document_id,
            file_hash=file_hash,
            reason=reason,
            previous_status=previous_status,
        )

    def plan_documents_to_process(self, raw_pdf_dir: str | Path = "data/raw_pdfs") -> list[DocumentProcessingPlan]:
        raw_dir = Path(raw_pdf_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)
        plans: list[DocumentProcessingPlan] = []

        for pdf_path in sorted(raw_dir.glob("*.pdf")):
            plan = self.plan_for_pdf(pdf_path)
            if plan.reason != "up_to_date":
                plans.append(plan)
        return plans

    def get_documents_to_process(self, raw_pdf_dir: str | Path = "data/raw_pdfs") -> list[Path]:
        plans = self.plan_documents_to_process(raw_pdf_dir=raw_pdf_dir)
        return [plan.pdf_path for plan in plans]

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
