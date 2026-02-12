from __future__ import annotations

import json
from pathlib import Path

from langchain_core.documents import Document

from src.models import ParseResult
from src.pipeline.registry import MetadataRegistry
from src.pipeline.runner import PipelineRunner


def _write_pdf(path: Path, *, tail: bytes = b"sample") -> None:
    path.write_bytes(b"%PDF-1.7\n" + tail)


def test_registry_plan_detects_hash_changes(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw_pdfs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = raw_dir / "mirae_samsung_elec_20260210.pdf"
    _write_pdf(pdf_path, tail=b"version1")

    registry = MetadataRegistry(path=tmp_path / "metadata.json")

    plans = registry.plan_documents_to_process(raw_pdf_dir=raw_dir)
    assert len(plans) == 1
    assert plans[0].reason == "new"

    document_id = registry.register_source_file(
        pdf_path,
        file_hash=plans[0].file_hash,
        reprocess_reason=plans[0].reason,
    )
    registry.mark_indexed(document_id, file_hash=plans[0].file_hash, vector_count=2)
    assert registry.plan_documents_to_process(raw_pdf_dir=raw_dir) == []

    _write_pdf(pdf_path, tail=b"version2")
    changed_plan = registry.plan_documents_to_process(raw_pdf_dir=raw_dir)
    assert len(changed_plan) == 1
    assert changed_plan[0].reason == "hash_changed"


def test_registry_rollback_restores_previous_indexed_state(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw_pdfs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = raw_dir / "mirae_samsung_elec_20260210.pdf"
    _write_pdf(pdf_path)

    registry = MetadataRegistry(path=tmp_path / "metadata.json")
    plan = registry.plan_for_pdf(pdf_path)
    document_id = registry.register_source_file(pdf_path, file_hash=plan.file_hash, reprocess_reason=plan.reason)
    registry.mark_indexed(document_id, file_hash=plan.file_hash, vector_count=3)

    snapshot = registry.get_document_snapshot(document_id)
    assert snapshot is not None
    registry.update_status(document_id, "indexing")

    registry.rollback_document(
        document_id,
        snapshot=snapshot,
        stage="indexing",
        error_message="forced failure",
    )

    data = registry.load()
    entry = data["documents"][document_id]
    assert entry["status"] == "indexed"
    assert entry["last_error"]["stage"] == "indexing"
    assert entry["pipeline_history"][-1]["rolled_back"] is True


class _FakeParser:
    def parse(self, pdf_path: str | Path) -> ParseResult:
        return ParseResult(
            content="# 제목\n본문",
            metadata={"api": "stub"},
            usage={},
            source_file=Path(pdf_path).name,
        )


class _FakeMetadataExtractor:
    def extract(self, content: str, filename: str) -> dict:
        return {
            "ticker": "005930",
            "company_name": "삼성전자",
            "date": "2026-02-10",
            "broker": "미래에셋증권",
            "analyst": "홍길동",
            "report_type": "기업분석",
            "target_price": 85000,
            "rating": "매수",
            "source_file": filename,
        }


class _FakeChunker:
    def chunk(self, content: str, metadata: dict) -> list[Document]:
        return [
            Document(
                page_content=content,
                metadata={
                    **metadata,
                    "document_id": Path(metadata["source_file"]).stem,
                    "chunk_index": 0,
                },
            )
        ]


class _FakeEmbedder:
    def __init__(self) -> None:
        self.restore_called = False

    def snapshot_document(self, document_id: str) -> dict:
        return {"document_id": document_id}

    def replace_document(self, *, document_id: str, documents: list[Document]) -> int:
        raise RuntimeError("indexing failed")

    def restore_snapshot(self, *, document_id: str, snapshot: dict) -> None:
        self.restore_called = True


def test_runner_rolls_back_registry_and_vector_snapshot_on_index_failure(tmp_path: Path) -> None:
    pdf_path = tmp_path / "mirae_samsung_elec_20260210.pdf"
    _write_pdf(pdf_path)

    registry = MetadataRegistry(path=tmp_path / "metadata.json")
    initial_plan = registry.plan_for_pdf(pdf_path)
    document_id = registry.register_source_file(
        pdf_path,
        file_hash=initial_plan.file_hash,
        reprocess_reason=initial_plan.reason,
    )
    registry.mark_indexed(document_id, file_hash=initial_plan.file_hash, vector_count=1)

    fake_embedder = _FakeEmbedder()
    runner = PipelineRunner(
        parser=_FakeParser(),  # type: ignore[arg-type]
        metadata_extractor=_FakeMetadataExtractor(),  # type: ignore[arg-type]
        chunker=_FakeChunker(),  # type: ignore[arg-type]
        embedder=fake_embedder,  # type: ignore[arg-type]
        registry=registry,
        parsed_dir=tmp_path / "parsed",
    )

    result = runner.run(pdf_paths=[pdf_path])
    assert result.failed_count == 1
    assert fake_embedder.restore_called is True

    data = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    entry = data["documents"][document_id]
    assert entry["status"] == "indexed"
    assert entry["pipeline_history"][-1]["rolled_back"] is True
