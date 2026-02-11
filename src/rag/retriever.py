from __future__ import annotations

from langchain.chains.query_constructor.schema import AttributeInfo
from langchain.retrievers.self_query.base import SelfQueryRetriever
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI

DOCUMENT_CONTENT_DESCRIPTION = (
    "증권사 애널리스트 리포트 텍스트. 실적 분석, 목표주가, 투자의견, 밸류에이션, 업종 전망을 포함한다."
)


def _metadata_field_info() -> list[AttributeInfo]:
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
    """SelfQueryRetriever 기반 증권 리포트 검색기."""

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

        llm = ChatOpenAI(
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
            search_kwargs={"k": self.k, "score_threshold": self.score_threshold},
        )

    def retrieve(self, query: str, k: int | None = None) -> list[Document]:
        limit = k or self.k
        docs = self.retriever.invoke(query)
        if docs:
            return docs[:limit]

        return self._fallback_similarity_search(query=query, k=limit)

    def _fallback_similarity_search(self, *, query: str, k: int) -> list[Document]:
        try:
            with_scores = self.vectorstore.similarity_search_with_relevance_scores(query, k=k)
            return [doc for doc, score in with_scores if score >= self.score_threshold]
        except Exception:  # noqa: BLE001 - vectorstore 구현별 fallback 허용
            return self.vectorstore.similarity_search(query, k=k)

