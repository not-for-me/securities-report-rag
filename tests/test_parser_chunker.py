from __future__ import annotations

from pathlib import Path

import pytest

from src.pipeline.chunker import ReportChunker
from src.pipeline.parser import DocumentParseError, DocumentParser


def _sample_metadata(source_file: str = "mirae_samsung_elec_20260210.pdf") -> dict[str, object]:
    return {
        "ticker": "005930",
        "company_name": "삼성전자",
        "date": "2026-02-10",
        "broker": "미래에셋증권",
        "analyst": "홍길동",
        "report_type": "기업분석",
        "target_price": 85000,
        "rating": "매수",
        "source_file": source_file,
    }


def test_extract_markdown_from_elements_fallback() -> None:
    payload = {
        "elements": [
            {"content": {"markdown": "# 삼성전자"}},
            {"content": {"markdown": "실적 요약 본문"}},
        ]
    }
    assert DocumentParser._extract_markdown(payload) == "# 삼성전자\n\n실적 요약 본문"


def test_parse_raises_when_markdown_body_is_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nsample")

    parser = DocumentParser(api_key="up_test_key")
    monkeypatch.setattr(parser, "_request_document_parse_with_retry", lambda _path: {"content": {}})

    with pytest.raises(DocumentParseError):
        parser.parse(pdf_path)


def test_chunker_keeps_table_with_neighbor_context() -> None:
    content = """# 삼성전자
실적 요약 문단입니다.

| 항목 | 값 |
|---|---|
| 매출 | 79조 |

표 해석 문단입니다.

추가 본문 문단입니다.
"""
    chunker = ReportChunker(chunk_size=1000, chunk_overlap=200)
    chunks = chunker.chunk(content, _sample_metadata())

    table_chunks = [chunk for chunk in chunks if chunk.metadata.get("chunk_type") == "table"]
    assert len(table_chunks) == 1
    table_content = table_chunks[0].page_content
    assert "실적 요약 문단입니다." in table_content
    assert "| 항목 | 값 |" in table_content
    assert "표 해석 문단입니다." in table_content

    text_chunks = [chunk for chunk in chunks if chunk.metadata.get("chunk_type") == "text"]
    assert any("추가 본문 문단입니다." in chunk.page_content for chunk in text_chunks)
