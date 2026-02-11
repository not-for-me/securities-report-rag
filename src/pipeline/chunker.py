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
            if self._is_disclaimer(header_chunk.page_content):
                continue

            header_metadata = self._build_base_metadata(metadata, header_chunk.metadata)

            if self._contains_table(header_chunk.page_content):
                final_chunks.append(
                    Document(
                        page_content=header_chunk.page_content,
                        metadata={**header_metadata, "chunk_type": "table", "chunk_index": chunk_index},
                    )
                )
                chunk_index += 1
                continue

            for split_chunk in self.text_splitter.split_documents([header_chunk]):
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

    @staticmethod
    def _contains_table(text: str) -> bool:
        has_rows = bool(TABLE_PATTERN.search(text))
        has_separator = bool(TABLE_SEPARATOR_PATTERN.search(text))
        return has_rows and has_separator

    @staticmethod
    def _is_disclaimer(text: str) -> bool:
        return any(pattern.search(text) for pattern in DISCLAIMER_PATTERNS)
