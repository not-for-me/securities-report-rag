from __future__ import annotations

import re
from typing import Any

from langchain_core.documents import Document

from src.models import ReportMetadata

try:
    from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
except ModuleNotFoundError:
    try:
        from langchain.text_splitter import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
    except ModuleNotFoundError:
        # LangChain splitter 의존성이 없는 환경에서도 스켈레톤이 동작하도록 최소 fallback 구현을 둔다.
        class MarkdownHeaderTextSplitter:  # type: ignore[no-redef]
            def __init__(self, headers_to_split_on: list[tuple[str, str]], strip_headers: bool = False):
                self.header_prefixes = tuple(prefix for prefix, _ in headers_to_split_on)
                self.strip_headers = strip_headers

            def split_text(self, text: str) -> list[Document]:
                lines = text.splitlines()
                if not lines:
                    return []

                chunks: list[Document] = []
                current_lines: list[str] = []
                for line in lines:
                    is_header_line = any(line.startswith(f"{prefix} ") for prefix in self.header_prefixes)
                    if is_header_line and current_lines:
                        chunks.append(
                            Document(
                                page_content="\n".join(current_lines).strip(),
                                metadata={},
                            )
                        )
                        current_lines = []

                    if self.strip_headers and is_header_line:
                        continue
                    current_lines.append(line)

                if current_lines:
                    chunks.append(
                        Document(
                            page_content="\n".join(current_lines).strip(),
                            metadata={},
                        )
                    )
                return chunks

        class RecursiveCharacterTextSplitter:  # type: ignore[no-redef]
            def __init__(
                self,
                *,
                chunk_size: int,
                chunk_overlap: int,
                separators: list[str] | None = None,
                length_function: Any = len,
            ):
                self.chunk_size = chunk_size
                self.chunk_overlap = chunk_overlap
                self.length_function = length_function

            def split_documents(self, documents: list[Document]) -> list[Document]:
                output: list[Document] = []
                step = max(self.chunk_size - self.chunk_overlap, 1)
                for document in documents:
                    text = document.page_content
                    if self.length_function(text) <= self.chunk_size:
                        output.append(document)
                        continue

                    for start in range(0, len(text), step):
                        chunk = text[start : start + self.chunk_size]
                        if not chunk.strip():
                            continue
                        output.append(
                            Document(
                                page_content=chunk,
                                metadata={**(document.metadata or {})},
                            )
                        )
                        if start + self.chunk_size >= len(text):
                            break
                return output

DISCLAIMER_PATTERNS = [
    re.compile(r"본\s*조사자료는\s*고객의\s*투자에\s*참고"),
    re.compile(r"투자판단의\s*최종\s*책임은"),
    re.compile(r"당사는\s*본\s*자료의\s*내용에\s*의거하여"),
    re.compile(r"Compliance\s*Notice", flags=re.IGNORECASE),
    re.compile(r"과거의\s*수익률.*미래의\s*수익률을\s*보장"),
]

TABLE_PATTERN = re.compile(r"^\|.+\|$", flags=re.MULTILINE)
TABLE_SEPARATOR_PATTERN = re.compile(r"^\|[-:| ]+\|$", flags=re.MULTILINE)


class ReportChunker:
    """증권사 리포트 전용 청킹 모듈."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        headers_to_split_on = [("#", "h1"), ("##", "h2"), ("###", "h3")]
        self.markdown_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=headers_to_split_on,
            strip_headers=False,
        )
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n## ", "\n### ", "\n\n", "\n", " "],
            length_function=len,
        )

    def chunk(self, content: str, metadata: ReportMetadata) -> list[Document]:
        if not content.strip():
            return []

        header_chunks = self.markdown_splitter.split_text(content)
        final_chunks: list[Document] = []
        chunk_index = 0

        for header_chunk in header_chunks:
            section_text = header_chunk.page_content.strip()
            if not section_text or self._is_disclaimer(section_text):
                continue

            header_metadata = self._build_base_metadata(metadata, header_chunk.metadata)
            section_segments = self._split_section_segments(section_text)

            for segment_type, segment_text in section_segments:
                if self._is_disclaimer(segment_text):
                    continue

                if segment_type == "table":
                    final_chunks.append(
                        Document(
                            page_content=segment_text,
                            metadata={**header_metadata, "chunk_type": "table", "chunk_index": chunk_index},
                        )
                    )
                    chunk_index += 1
                    continue

                text_chunk_source = Document(page_content=segment_text, metadata=header_chunk.metadata)
                for split_chunk in self.text_splitter.split_documents([text_chunk_source]):
                    if self._is_disclaimer(split_chunk.page_content):
                        continue

                    split_metadata = self._build_base_metadata(metadata, split_chunk.metadata)
                    final_chunks.append(
                        Document(
                            page_content=split_chunk.page_content,
                            metadata={**split_metadata, "chunk_type": "text", "chunk_index": chunk_index},
                        )
                    )
                    chunk_index += 1

        return final_chunks

    def _build_base_metadata(
        self,
        report_metadata: ReportMetadata,
        section_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        source_file = report_metadata.get("source_file", "")
        document_id = source_file.rsplit(".", maxsplit=1)[0]
        return {
            **report_metadata,
            **section_metadata,
            "document_id": document_id,
        }

    def _split_section_segments(self, section_text: str) -> list[tuple[str, str]]:
        lines = section_text.splitlines()
        if not lines:
            return []

        table_ranges = self._find_table_ranges(lines)
        if not table_ranges:
            return [("text", section_text)]

        segments: list[tuple[str, str]] = []
        cursor = 0
        for start, end in table_ranges:
            if cursor < start:
                text_block = "\n".join(lines[cursor:start]).strip()
                if text_block:
                    segments.append(("text", text_block))

            table_block = self._build_table_chunk_with_context(lines, start, end)
            if table_block:
                segments.append(("table", table_block))
            cursor = end + 1

        if cursor < len(lines):
            tail_text = "\n".join(lines[cursor:]).strip()
            if tail_text:
                segments.append(("text", tail_text))

        return segments

    def _find_table_ranges(self, lines: list[str]) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        idx = 0
        while idx < len(lines) - 1:
            current = lines[idx].strip()
            next_line = lines[idx + 1].strip()
            if self._is_table_row(current) and self._is_table_separator(next_line):
                end = idx + 1
                while end + 1 < len(lines) and self._is_table_row(lines[end + 1].strip()):
                    end += 1
                ranges.append((idx, end))
                idx = end + 1
                continue
            idx += 1
        return ranges

    def _build_table_chunk_with_context(self, lines: list[str], start: int, end: int) -> str:
        before_context = self._extract_previous_paragraph(lines, start - 1)
        after_context = self._extract_next_paragraph(lines, end + 1)
        table_lines = lines[start : end + 1]

        chunk_lines: list[str] = []
        if before_context:
            chunk_lines.extend(before_context)
            chunk_lines.append("")

        chunk_lines.extend(table_lines)

        if after_context:
            chunk_lines.append("")
            chunk_lines.extend(after_context)

        return "\n".join(chunk_lines).strip()

    @staticmethod
    def _extract_previous_paragraph(lines: list[str], start_idx: int) -> list[str]:
        idx = start_idx
        while idx >= 0 and not lines[idx].strip():
            idx -= 1
        if idx < 0:
            return []

        end = idx
        while idx >= 0 and lines[idx].strip():
            idx -= 1
        return lines[idx + 1 : end + 1]

    @staticmethod
    def _extract_next_paragraph(lines: list[str], start_idx: int) -> list[str]:
        idx = start_idx
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
        if idx >= len(lines):
            return []

        start = idx
        while idx < len(lines) and lines[idx].strip():
            idx += 1
        return lines[start:idx]

    @staticmethod
    def _is_table_row(line: str) -> bool:
        return bool(TABLE_PATTERN.fullmatch(line))

    @staticmethod
    def _is_table_separator(line: str) -> bool:
        return bool(TABLE_SEPARATOR_PATTERN.fullmatch(line))

    @staticmethod
    def _is_disclaimer(text: str) -> bool:
        return any(pattern.search(text) for pattern in DISCLAIMER_PATTERNS)
