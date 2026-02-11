import time
from pathlib import Path

from src.security import RateLimiter, validate_pdf, validate_query


def test_validate_query() -> None:
    assert validate_query("삼성전자 목표가 알려줘") is None
    assert validate_query("   ") is not None
    assert validate_query("a" * 501) is not None


def test_rate_limiter() -> None:
    limiter = RateLimiter(max_requests=2, window_seconds=1)
    assert limiter.is_allowed("U123")
    assert limiter.is_allowed("U123")
    assert not limiter.is_allowed("U123")

    time.sleep(1.1)
    assert limiter.is_allowed("U123")


def test_validate_pdf_magic_bytes(tmp_path: Path) -> None:
    good_pdf = tmp_path / "sample.pdf"
    good_pdf.write_bytes(b"%PDF-1.7 content")
    assert validate_pdf(good_pdf)

    bad_pdf = tmp_path / "bad.pdf"
    bad_pdf.write_bytes(b"not-pdf")
    assert not validate_pdf(bad_pdf)

