from __future__ import annotations

from langchain_core.documents import Document

from src.rag.retriever import ReportRetriever


class _FakeVectorStore:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def similarity_search_with_relevance_scores(self, query: str, k: int, **kwargs) -> list[tuple[Document, float]]:
        self.calls.append({"query": query, "k": k, **kwargs})
        return [
            (Document(page_content=f"{query}-relevant", metadata={}), 0.9),
            (Document(page_content=f"{query}-low-score", metadata={}), 0.1),
        ]

    def similarity_search(self, query: str, k: int, **kwargs) -> list[Document]:
        self.calls.append({"query": query, "k": k, **kwargs})
        return [Document(page_content=f"{query}-fallback", metadata={})]


def test_retriever_returns_only_docs_above_score_threshold() -> None:
    vectorstore = _FakeVectorStore()
    retriever = ReportRetriever(
        vectorstore=vectorstore,  # type: ignore[arg-type]
        openai_api_key="test-key",
        score_threshold=0.3,
    )

    docs = retriever.retrieve("일반 질의")
    assert len(docs) == 1
    assert docs[0].page_content.endswith("relevant")


def test_retriever_applies_heuristic_metadata_filter() -> None:
    vectorstore = _FakeVectorStore()
    retriever = ReportRetriever(
        vectorstore=vectorstore,  # type: ignore[arg-type]
        openai_api_key="test-key",
        score_threshold=0.3,
    )

    retriever.retrieve("미래에셋증권 005930 2026.02.10 매수 리포트")
    last_call = vectorstore.calls[-1]
    metadata_filter = last_call.get("filter", {})

    assert metadata_filter.get("broker") == "미래에셋증권"
    assert metadata_filter.get("ticker") == "005930"
    assert metadata_filter.get("date") == "2026-02-10"
    assert metadata_filter.get("rating") == "매수"
