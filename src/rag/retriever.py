from __future__ import annotations

import logging
import re
from typing import Any

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI

try:
    from langchain.chains.query_constructor.schema import AttributeInfo
    from langchain.retrievers.self_query.base import SelfQueryRetriever
except Exception:  # noqa: BLE001 - LangChain 버전별 모듈 차이를 허용한다.
    AttributeInfo = None
    SelfQueryRetriever = None

logger = logging.getLogger(__name__)

DOCUMENT_CONTENT_DESCRIPTION = (
    "증권사 애널리스트 리포트 텍스트. 실적 분석, 목표주가, 투자의견, 밸류에이션, 업종 전망을 포함한다."
)

BROKER_KEYWORDS = (
    "미래에셋증권",
    "한국투자증권",
    "삼성증권",
    "NH투자증권",
    "KB증권",
    "신한투자증권",
    "하나증권",
    "키움증권",
)

RATING_KEYWORDS: dict[str, tuple[str, ...]] = {
    "매수": ("매수", "buy", "overweight", "비중확대"),
    "중립": ("중립", "hold", "neutral", "시장수익률"),
    "매도": ("매도", "sell", "underweight", "비중축소"),
}

REPORT_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "실적분석": ("실적", "분기", "컨센서스"),
    "기업분석": ("기업", "밸류에이션", "목표주가"),
    "업종분석": ("업종", "섹터", "산업"),
}


def _metadata_field_info() -> list[Any]:
    if AttributeInfo is None:
        return []
    return [
        AttributeInfo(name="company_name", description="종목명", type="string"),
        AttributeInfo(name="ticker", description="종목코드(6자리)", type="string"),
        AttributeInfo(name="date", description="리포트 발행일(YYYY-MM-DD)", type="string"),
        AttributeInfo(name="broker", description="증권사명", type="string"),
        AttributeInfo(name="analyst", description="애널리스트명", type="string"),
        AttributeInfo(name="report_type", description="리포트 유형", type="string"),
        AttributeInfo(name="rating", description="투자의견", type="string"),
        AttributeInfo(name="target_price", description="목표주가(원)", type="integer"),
        AttributeInfo(name="chunk_type", description="청크 유형(text/table)", type="string"),
    ]


class ReportRetriever:
    """SelfQueryRetriever 기반 검색 + 호환성 fallback."""

    def __init__(
        self,
        vectorstore: Chroma,
        *,
        openai_api_key: str,
        llm_model: str = "gpt-4o-mini",
        k: int = 5,
        score_threshold: float = 0.3,
    ):
        self.vectorstore = vectorstore
        self.k = k
        self.score_threshold = score_threshold
        self.retriever: Any | None = None

        if SelfQueryRetriever is None or AttributeInfo is None:
            logger.info("SelfQueryRetriever is unavailable in this LangChain version. Similarity fallback is used.")
            return

        try:
            llm = ChatOpenAI(
                base_url="https://api.bizrouter.ai/v1",
                api_key=openai_api_key,
                model=llm_model,
                temperature=0,
                max_retries=3,
                request_timeout=30,
            )
            self.retriever = SelfQueryRetriever.from_llm(
                llm=llm,
                vectorstore=self.vectorstore,
                document_contents=DOCUMENT_CONTENT_DESCRIPTION,
                metadata_field_info=_metadata_field_info(),
                enable_limit=True,
                search_type="similarity_score_threshold",
                search_kwargs={"k": self.k, "score_threshold": self.score_threshold},
            )
        except Exception as error:  # noqa: BLE001 - SelfQuery 구성 실패 시 fallback 검색 사용
            logger.warning("SelfQueryRetriever initialization failed. Similarity fallback is used: %s", error)

    def retrieve(self, query: str, k: int | None = None) -> list[Document]:
        limit = k or self.k
        metadata_filter = self._build_metadata_filter(query)

        if self.retriever is not None:
            try:
                docs = self.retriever.invoke(query)
                if docs:
                    return docs[:limit]
            except Exception as error:  # noqa: BLE001
                logger.warning("SelfQuery retrieval failed. Similarity fallback is used: %s", error)

        if metadata_filter:
            filtered_docs = self._fallback_similarity_search(
                query=query,
                k=limit,
                metadata_filter=metadata_filter,
            )
            if filtered_docs:
                return filtered_docs

        return self._fallback_similarity_search(query=query, k=limit)

    def _fallback_similarity_search(
        self,
        *,
        query: str,
        k: int,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[Document]:
        with_scores = self._call_with_optional_filter(
            "similarity_search_with_relevance_scores",
            query=query,
            k=k,
            metadata_filter=metadata_filter,
        )
        if with_scores is not None:
            return [doc for doc, score in with_scores if score >= self.score_threshold]

        docs = self._call_with_optional_filter(
            "similarity_search",
            query=query,
            k=k,
            metadata_filter=metadata_filter,
        )
        if docs is not None:
            return docs
        return []

    def _call_with_optional_filter(
        self,
        method_name: str,
        *,
        query: str,
        k: int,
        metadata_filter: dict[str, Any] | None,
    ) -> Any | None:
        method = getattr(self.vectorstore, method_name, None)
        if method is None:
            return None

        if metadata_filter:
            for arg_name in ("filter", "where"):
                try:
                    return method(query, k=k, **{arg_name: metadata_filter})
                except TypeError:
                    continue
                except Exception as error:  # noqa: BLE001
                    logger.debug("Vector search with metadata filter failed: %s", error)
                    return None
            return None

        try:
            return method(query, k=k)
        except Exception as error:  # noqa: BLE001
            logger.debug("Vector similarity search failed: %s", error)
            return None

    def _build_metadata_filter(self, query: str) -> dict[str, Any]:
        lowered = query.lower()
        where: dict[str, Any] = {}

        ticker_match = re.search(r"\b(\d{6})\b", query)
        if ticker_match:
            where["ticker"] = ticker_match.group(1)

        for broker in BROKER_KEYWORDS:
            if broker in query:
                where["broker"] = broker
                break

        date_match = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", query)
        if date_match:
            year, month, day = date_match.groups()
            where["date"] = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

        for rating, keywords in RATING_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                where["rating"] = rating
                break

        for report_type, keywords in REPORT_TYPE_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                where["report_type"] = report_type
                break

        return where
