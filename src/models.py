from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict


class ReportMetadata(TypedDict):
    ticker: str
    company_name: str
    date: str
    broker: str
    analyst: str | None
    report_type: str
    target_price: int | None
    rating: str | None
    source_file: str


@dataclass(slots=True)
class ParseResult:
    content: str
    metadata: dict[str, Any]
    usage: dict[str, Any]
    source_file: str


@dataclass(slots=True)
class QAResult:
    answer: str
    sources: list[dict[str, str]] = field(default_factory=list)
    retrieved_documents: list[Any] = field(default_factory=list)


@dataclass(slots=True)
class PipelineResult:
    total: int
    success_count: int
    failed_count: int
    failed_files: list[dict[str, str]] = field(default_factory=list)

