from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx

from src.models import ParseResult
from src.security import validate_pdf

logger = logging.getLogger(__name__)


class DocumentParseError(RuntimeError):
    """Upstage Document Parse API 호출 실패."""


class DocumentParser:
    """Upstage Document Parse API 기반 PDF 파서."""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = "https://api.upstage.ai/v1/document-digitization",
        parse_mode: str = "auto",
        timeout_seconds: int = 300,
    ):
        self.api_key = api_key
        self.endpoint = endpoint
        self.parse_mode = parse_mode
        self.timeout_seconds = timeout_seconds

    def parse(self, pdf_path: str | Path) -> ParseResult:
        path = Path(pdf_path)
        if not validate_pdf(path):
            raise FileNotFoundError(f"Invalid PDF file: {path}")

        try:
            payload = self._request_document_parse(path)
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            raise DocumentParseError(f"Upstage API failed with status {status_code}") from error
        except httpx.HTTPError as error:
            raise DocumentParseError(f"Failed to call Upstage API: {error}") from error

        markdown = self._extract_markdown(payload)
        metadata = {
            "api": payload.get("api"),
            "model": payload.get("model"),
            "element_count": len(payload.get("elements", [])),
        }

        return ParseResult(
            content=markdown,
            metadata=metadata,
            usage=payload.get("usage", {}),
            source_file=path.name,
        )

    def parse_batch(
        self,
        pdf_paths: list[str | Path],
        *,
        delay_seconds: float = 0.5,
    ) -> list[ParseResult]:
        results: list[ParseResult] = []

        for index, pdf_path in enumerate(pdf_paths):
            result = self.parse(pdf_path)
            results.append(result)

            if index < len(pdf_paths) - 1 and delay_seconds > 0:
                time.sleep(delay_seconds)

        return results

    def _request_document_parse(self, pdf_path: Path) -> dict[str, Any]:
        with pdf_path.open("rb") as file:
            response = httpx.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"document": (pdf_path.name, file, "application/pdf")},
                data={
                    "model": "document-parse",
                    "ocr": self.parse_mode,
                    "output_formats": '["markdown"]',
                    "coordinates": "false",
                },
                timeout=self.timeout_seconds,
            )

        response.raise_for_status()
        return response.json()

    @staticmethod
    def _extract_markdown(payload: dict[str, Any]) -> str:
        content = payload.get("content", {})
        if isinstance(content, dict) and content.get("markdown"):
            return str(content["markdown"])

        # 일부 응답 포맷은 최상위 markdown 키를 사용한다.
        if payload.get("markdown"):
            return str(payload["markdown"])

        logger.warning("Upstage 응답에서 markdown 본문을 찾지 못했습니다.")
        return ""

