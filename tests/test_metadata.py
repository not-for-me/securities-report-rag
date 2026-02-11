from src.pipeline.metadata import MetadataExtractor


def test_extract_metadata_from_text_and_filename() -> None:
    extractor = MetadataExtractor()
    content = """
# 삼성전자
미래에셋증권
애널리스트: 홍길동
투자의견: 매수
목표주가: 85,000원
발행일: 2026.02.10
"""
    metadata = extractor.extract(content=content, filename="mirae_samsung_elec_20260210.pdf")

    assert metadata["company_name"] == "삼성전자"
    assert metadata["broker"] == "미래에셋증권"
    assert metadata["date"] == "2026-02-10"
    assert metadata["rating"] == "매수"
    assert metadata["target_price"] == 85000


def test_extract_falls_back_to_filename() -> None:
    extractor = MetadataExtractor()
    metadata = extractor.extract(content="내용 없음", filename="koreainvest_sk_hynix_20260205.pdf")

    assert metadata["broker"] == "한국투자증권"
    assert metadata["date"] == "2026-02-05"

