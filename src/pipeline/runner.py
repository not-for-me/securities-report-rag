from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from src.config import Settings, get_settings
from src.models import ParseResult, PipelineResult
from src.pipeline.chunker import ReportChunker
from src.pipeline.embedder import ReportEmbedder
from src.pipeline.metadata import MetadataExtractor
from src.pipeline.parser import DocumentParser
from src.pipeline.registry import DocumentProcessingPlan, MetadataRegistry, compute_file_hash

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ProcessContext:
    document_id: str
    file_hash: str
    reprocess_reason: str
    registry_snapshot: dict | None


class PipelineRunner:
    """배치 파이프라인 오케스트레이터."""

    def __init__(
        self,
        *,
        parser: DocumentParser,
        metadata_extractor: MetadataExtractor,
        chunker: ReportChunker,
        embedder: ReportEmbedder,
        registry: MetadataRegistry,
        parsed_dir: str | Path = "data/parsed",
    ):
        self.parser = parser
        self.metadata_extractor = metadata_extractor
        self.chunker = chunker
        self.embedder = embedder
        self.registry = registry
        self.parsed_dir = Path(parsed_dir)
        self.parsed_dir.mkdir(parents=True, exist_ok=True)

    def run(self, pdf_paths: Iterable[str | Path] | None = None) -> PipelineResult:
        plans = self._build_plans(pdf_paths=pdf_paths)
        success_count = 0
        failures: list[dict[str, str]] = []

        for plan in plans:
            pdf_path = plan.pdf_path
            if not pdf_path.exists():
                failures.append({"file": str(pdf_path), "error": "File does not exist"})
                continue

            try:
                self._process_one(plan)
                success_count += 1
            except Exception as error:  # noqa: BLE001 - 배치 파이프라인은 개별 실패를 수집한다.
                logger.exception("Pipeline failed for %s (%s)", pdf_path.name, plan.reason)
                failures.append({"file": pdf_path.name, "error": str(error)})

        return PipelineResult(
            total=len(plans),
            success_count=success_count,
            failed_count=len(failures),
            failed_files=failures,
        )

    def _process_one(self, plan: DocumentProcessingPlan) -> None:
        pdf_path = plan.pdf_path
        process = self._prepare_process_context(plan)
        document_id = self.registry.register_source_file(
            pdf_path,
            file_hash=process.file_hash,
            reprocess_reason=process.reprocess_reason,
        )
        current_stage = "parsing"
        vector_snapshot = None

        try:
            self.registry.update_status(document_id, "parsing")
            parse_result = self._load_or_parse(pdf_path=pdf_path, document_id=document_id)
            self.registry.append_history(document_id, stage="parsed", success=True)
            self.registry.update_status(document_id, "parsed")

            metadata = self.metadata_extractor.extract(parse_result.content, pdf_path.name)
            self.registry.set_report_metadata(document_id, metadata)

            current_stage = "chunking"
            self.registry.update_status(document_id, "chunking")
            documents = self.chunker.chunk(parse_result.content, metadata)
            self.registry.append_history(
                document_id,
                stage="chunked",
                success=True,
                chunk_count=len(documents),
            )
            self.registry.update_status(document_id, "chunked")

            current_stage = "indexing"
            self.registry.update_status(document_id, "indexing")
            vector_snapshot = self.embedder.snapshot_document(document_id)
            vector_count = self.embedder.replace_document(document_id=document_id, documents=documents)
            self.registry.append_history(
                document_id,
                stage="indexed",
                success=True,
                vector_count=vector_count,
            )
            self.registry.mark_indexed(document_id, file_hash=process.file_hash, vector_count=vector_count)
        except Exception as error:  # noqa: BLE001
            if current_stage == "indexing" and vector_snapshot is not None:
                self.embedder.restore_snapshot(document_id=document_id, snapshot=vector_snapshot)
            if current_stage == "parsing":
                self._cleanup_parsing_cache(document_id)

            if process.registry_snapshot and process.registry_snapshot.get("status") == "indexed":
                self.registry.rollback_document(
                    document_id,
                    snapshot=process.registry_snapshot,
                    stage=current_stage,
                    error_message=str(error),
                )
            else:
                self.registry.mark_failed(
                    document_id,
                    stage=current_stage,
                    error_message=str(error),
                    rolled_back=False,
                )

            raise

    def _build_plans(self, pdf_paths: Iterable[str | Path] | None) -> list[DocumentProcessingPlan]:
        if pdf_paths is None:
            return self.registry.plan_documents_to_process()
        return [self.registry.plan_for_pdf(Path(path), reason_override="manual") for path in pdf_paths]

    def _prepare_process_context(self, plan: DocumentProcessingPlan) -> _ProcessContext:
        snapshot = self.registry.get_document_snapshot(plan.document_id)
        reprocess_reason = plan.reason
        if reprocess_reason == "up_to_date":
            reprocess_reason = "manual"
        return _ProcessContext(
            document_id=plan.document_id,
            file_hash=plan.file_hash,
            reprocess_reason=reprocess_reason,
            registry_snapshot=snapshot,
        )

    def _load_or_parse(self, *, pdf_path: Path, document_id: str) -> ParseResult:
        markdown_path = self.parsed_dir / f"{document_id}.md"
        meta_path = self.parsed_dir / f"{document_id}.meta.json"
        file_hash = compute_file_hash(pdf_path)

        if markdown_path.exists() and meta_path.exists():
            cached_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if cached_meta.get("file_hash") == file_hash:
                return ParseResult(
                    content=markdown_path.read_text(encoding="utf-8"),
                    metadata=cached_meta.get("parse_metadata", {}),
                    usage=cached_meta.get("usage", {}),
                    source_file=pdf_path.name,
                )

        parse_result = self.parser.parse(pdf_path)
        self._save_parse_cache(
            document_id=document_id,
            file_hash=file_hash,
            parse_result=parse_result,
        )
        return parse_result

    def _save_parse_cache(self, *, document_id: str, file_hash: str, parse_result: ParseResult) -> None:
        markdown_path = self.parsed_dir / f"{document_id}.md"
        meta_path = self.parsed_dir / f"{document_id}.meta.json"

        markdown_path.write_text(parse_result.content, encoding="utf-8")
        meta_path.write_text(
            json.dumps(
                {
                    "document_id": document_id,
                    "source_file": parse_result.source_file,
                    "file_hash": file_hash,
                    "parse_format": "markdown",
                    "parse_metadata": parse_result.metadata,
                    "usage": parse_result.usage,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _cleanup_parsing_cache(self, document_id: str) -> None:
        (self.parsed_dir / f"{document_id}.md").unlink(missing_ok=True)
        (self.parsed_dir / f"{document_id}.meta.json").unlink(missing_ok=True)


def build_default_pipeline_runner(settings: Settings | None = None) -> PipelineRunner:
    app_settings = settings or get_settings()
    app_settings.validate_pipeline_settings()

    parser = DocumentParser(
        api_key=app_settings.upstage_api_key or "",
        endpoint=app_settings.upstage_parse_endpoint,
        parse_mode=app_settings.upstage_parse_mode,
        timeout_seconds=app_settings.upstage_timeout_seconds,
        max_retries=app_settings.upstage_max_retries,
        base_retry_delay_seconds=app_settings.upstage_retry_base_delay_seconds,
    )
    metadata_extractor = MetadataExtractor()
    chunker = ReportChunker(chunk_size=1000, chunk_overlap=200)
    embedder = ReportEmbedder(
        api_key=app_settings.upstage_api_key or "",
        persist_directory=app_settings.chroma_persist_dir,
        collection_name=app_settings.chroma_collection_name,
        embedding_model=app_settings.embedding_model,
    )
    registry = MetadataRegistry(path="data/metadata.json")

    return PipelineRunner(
        parser=parser,
        metadata_extractor=metadata_extractor,
        chunker=chunker,
        embedder=embedder,
        registry=registry,
        parsed_dir="data/parsed",
    )
