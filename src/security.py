from __future__ import annotations

import html
import logging
import re
import time
from collections import defaultdict
from pathlib import Path

MAX_PDF_SIZE_BYTES = 50 * 1024 * 1024
MAX_QUERY_LENGTH = 500


class SecretFilter(logging.Filter):
    """로그 메시지의 시크릿 값을 마스킹한다."""

    PATTERNS: list[tuple[re.Pattern[str], str]] = [
        (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "sk-****"),
        (re.compile(r"xoxb-[a-zA-Z0-9-]+"), "xoxb-****"),
        (re.compile(r"xapp-[a-zA-Z0-9-]+"), "xapp-****"),
        (re.compile(r"up_[a-zA-Z0-9]+"), "up_****"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern, replacement in self.PATTERNS:
            message = pattern.sub(replacement, message)

        record.msg = message
        record.args = ()
        return True


class RateLimiter:
    """사용자별 요청 빈도를 제한한다."""

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, user_id: str) -> bool:
        now = time.time()
        kept = [ts for ts in self.requests[user_id] if now - ts < self.window_seconds]
        if len(kept) >= self.max_requests:
            self.requests[user_id] = kept
            return False

        self.requests[user_id] = [*kept, now]
        return True


def validate_query(query: str, max_length: int = MAX_QUERY_LENGTH) -> str | None:
    if not query.strip():
        return "질문이 비어 있습니다. 내용을 입력해 주세요."
    if len(query) > max_length:
        return f"질문이 너무 깁니다. {max_length}자 이내로 입력해 주세요."
    return None


def validate_pdf(file_path: Path, max_size_bytes: int = MAX_PDF_SIZE_BYTES) -> bool:
    if not file_path.exists() or not file_path.is_file():
        return False
    if file_path.suffix.lower() != ".pdf":
        return False
    if file_path.stat().st_size > max_size_bytes:
        return False
    with file_path.open("rb") as file:
        return file.read(5) == b"%PDF-"


def normalize_slack_text(text: str) -> str:
    return html.unescape(text)

