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
        max_retries: int = 3,
        base_retry_delay_seconds: float = 2.0,
    ):
        self.api_key = api_key
        self.endpoint = endpoint
        self.parse_mode = parse_mode
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.base_retry_delay_seconds = base_retry_delay_seconds

    def parse(self, pdf_path: str | Path) -> ParseResult:
        path = Path(pdf_path)
        if not validate_pdf(path):
            raise FileNotFoundError(f"Invalid PDF file: {path}")
        if not self.api_key:
            raise DocumentParseError("UPSTAGE_API_KEY is required")

        try:
            payload = self._request_document_parse_with_retry(path)
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

    def _request_document_parse_with_retry(self, pdf_path: Path) -> dict[str, Any]:
        transient_statuses = {429, 500, 502, 503, 504}
        attempts = self.max_retries + 1

        for attempt in range(1, attempts + 1):
            try:
                return self._request_document_parse(pdf_path)
            except httpx.HTTPStatusError as error:
                status_code = error.response.status_code
                if status_code not in transient_statuses or attempt >= attempts:
                    raise

                delay_seconds = self._retry_delay(attempt=attempt, response=error.response)
                logger.warning(
                    "Upstage API retrying after HTTP %s (attempt=%s/%s, sleep=%.1fs)",
                    status_code,
                    attempt,
                    attempts,
                    delay_seconds,
                )
                time.sleep(delay_seconds)
            except (httpx.TimeoutException, httpx.RequestError):
                if attempt >= attempts:
                    raise

                delay_seconds = self.base_retry_delay_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "Upstage API request error, retrying (attempt=%s/%s, sleep=%.1fs)",
                    attempt,
                    attempts,
                    delay_seconds,
                )
                time.sleep(delay_seconds)

        raise DocumentParseError("Unexpected retry loop termination")

    def _request_document_parse(self, pdf_path: Path) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client, pdf_path.open("rb") as file:
            response = client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"document": (pdf_path.name, file, "application/pdf")},
                data={
                    "model": "document-parse",
                    "ocr": self.parse_mode,
                    "output_formats": '["markdown"]',
                    "coordinates": "false",
                },
            )

        response.raise_for_status()
        return response.json()

    def _retry_delay(self, *, attempt: int, response: httpx.Response) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), 0.0)
            except ValueError:
                pass
        return self.base_retry_delay_seconds * (2 ** (attempt - 1))

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
