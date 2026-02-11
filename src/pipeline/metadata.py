from __future__ import annotations

import re
from pathlib import Path

from src.models import ReportMetadata

BROKER_NAME_MAP: dict[str, str] = {
    "mirae": "미래에셋증권",
    "koreainvest": "한국투자증권",
    "samsung": "삼성증권",
    "nh": "NH투자증권",
    "kb": "KB증권",
    "shinhan": "신한투자증권",
    "hana": "하나증권",
    "kiwoom": "키움증권",
}

BROKER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"미래에셋증권"), "미래에셋증권"),
    (re.compile(r"한국투자증권"), "한국투자증권"),
    (re.compile(r"삼성증권"), "삼성증권"),
    (re.compile(r"NH투자증권"), "NH투자증권"),
    (re.compile(r"KB증권"), "KB증권"),
    (re.compile(r"신한투자증권"), "신한투자증권"),
]

RATING_KEYWORDS: dict[str, list[str]] = {
    "매수": ["매수", "buy", "overweight", "비중확대"],
    "중립": ["중립", "hold", "neutral", "시장수익률"],
    "매도": ["매도", "sell", "underweight", "비중축소"],
}

REPORT_TYPE_KEYWORDS: list[tuple[list[str], str]] = [
    (["실적", "분기", "컨센서스"], "실적분석"),
    (["기업", "밸류에이션", "목표주가"], "기업분석"),
    (["업종", "섹터", "산업"], "업종분석"),
]


class MetadataExtractor:
    """증권사 리포트 메타데이터 추출기."""

    def extract(self, content: str, filename: str) -> ReportMetadata:
        preview = "\n".join(content.splitlines()[:200])
        source_file = Path(filename).name

        metadata: ReportMetadata = {
            "ticker": self._extract_ticker(preview) or self._extract_ticker_from_filename(source_file) or "UNKNOWN",
            "company_name": self._extract_company_name(preview)
            or self._extract_company_from_filename(source_file)
            or "UNKNOWN",
            "date": self._extract_date(preview) or self._extract_date_from_filename(source_file) or "1970-01-01",
            "broker": self._extract_broker(preview) or self._extract_broker_from_filename(source_file) or "UNKNOWN",
            "analyst": self._extract_analyst(preview),
            "report_type": self._extract_report_type(preview),
            "target_price": self._extract_target_price(preview),
            "rating": self._extract_rating(preview),
            "source_file": source_file,
        }
        return metadata

    def _extract_ticker(self, text: str) -> str | None:
        patterns = [
            r"\b(\d{6})\b",
            r"\((\d{6})\)",
            r"종목코드[:\s]*(\d{6})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return None

    def _extract_ticker_from_filename(self, filename: str) -> str | None:
        match = re.search(r"\b(\d{6})\b", filename)
        if match:
            return match.group(1)
        return None

    def _extract_company_name(self, text: str) -> str | None:
        patterns = [
            r"기업명[:\s]*([가-힣A-Za-z0-9(). ]+)",
            r"종목명[:\s]*([가-힣A-Za-z0-9(). ]+)",
            r"^#\s*([가-힣A-Za-z0-9(). ]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.MULTILINE)
            if match:
                value = match.group(1).strip()
                if value:
                    return value
        return None

    def _extract_company_from_filename(self, filename: str) -> str | None:
        parts = Path(filename).stem.split("_")
        if len(parts) < 2:
            return None

        # broker + company + date 형태를 기준으로 company 파트를 추정한다.
        if len(parts) >= 3:
            return parts[1].replace("-", " ")
        return None

    def _extract_date(self, text: str) -> str | None:
        patterns = [
            r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})",
            r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue

            year, month, day = match.groups()
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        return None

    def _extract_date_from_filename(self, filename: str) -> str | None:
        match = re.search(r"(20\d{2})(\d{2})(\d{2})", filename)
        if not match:
            return None
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"

    def _extract_broker(self, text: str) -> str | None:
        for pattern, broker in BROKER_PATTERNS:
            if pattern.search(text):
                return broker
        return None

    def _extract_broker_from_filename(self, filename: str) -> str | None:
        broker_key = Path(filename).stem.split("_")[0].lower()
        return BROKER_NAME_MAP.get(broker_key)

    def _extract_analyst(self, text: str) -> str | None:
        patterns = [
            r"애널리스트[:\s]*([가-힣]{2,4})",
            r"Analyst[:\s]*([A-Za-z .-]{2,40})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
        return None

    def _extract_target_price(self, text: str) -> int | None:
        patterns = [
            r"목표주?가[:\s]*([0-9,]+)\s*원",
            r"Target\s*Price[:\s]*([0-9,]+)",
            r"TP[:\s]*([0-9,]+)\s*원",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            return int(match.group(1).replace(",", ""))
        return None

    def _extract_rating(self, text: str) -> str | None:
        context = re.search(
            r"(?:투자의견|투자등급|rating|recommendation)[:\s]*([^\n]+)",
            text,
            flags=re.IGNORECASE,
        )
        if not context:
            return None

        value = context.group(1).strip().lower()
        for rating, keywords in RATING_KEYWORDS.items():
            if any(keyword in value for keyword in keywords):
                return rating
        return None

    def _extract_report_type(self, text: str) -> str:
        lowered = text.lower()
        for keywords, report_type in REPORT_TYPE_KEYWORDS:
            if any(keyword.lower() in lowered for keyword in keywords):
                return report_type
        return "기타"

