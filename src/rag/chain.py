from __future__ import annotations

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI

from src.models import QAResult
from src.rag.prompts import build_qa_prompt
from src.rag.retriever import ReportRetriever


def format_documents_for_prompt(documents: list[Document]) -> str:
    formatted: list[str] = []
    for index, document in enumerate(documents, start=1):
        metadata = document.metadata or {}
        header = (
            f"[출처 {index}] {metadata.get('broker', '알 수 없음')} | "
            f"{metadata.get('analyst', '알 수 없음')} | "
            f"{metadata.get('company_name', '알 수 없음')} | "
            f"{metadata.get('date', '날짜 미상')} | "
            f"{metadata.get('source_file', 'unknown')}"
        )
        formatted.append(f"{header}\n{document.page_content}")
    return "\n\n---\n\n".join(formatted)


def extract_sources(documents: list[Document]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    for document in documents:
        metadata = document.metadata or {}
        sources.append(
            {
                "broker": str(metadata.get("broker", "")),
                "analyst": str(metadata.get("analyst", "")),
                "date": str(metadata.get("date", "")),
                "file": str(metadata.get("source_file", "")),
            }
        )
    return sources


class ReportQAChain:
    """검색 결과를 컨텍스트로 LLM 답변을 생성하는 QA 체인."""

    def __init__(
        self,
        retriever: ReportRetriever,
        *,
        openai_api_key: str,
        llm_model: str = "gpt-4o-mini",
    ):
        self.retriever = retriever
        self.prompt = build_qa_prompt()
        self.llm = ChatOpenAI(
            api_key=openai_api_key,
            model=llm_model,
            temperature=0,
            max_retries=3,
            request_timeout=30,
        )
        self.chain = self.prompt | self.llm | StrOutputParser()

    def ask(self, question: str) -> QAResult:
        documents = self.retriever.retrieve(question)
        if not documents:
            return QAResult(
                answer=(
                    "관련 증권사 리포트를 찾을 수 없습니다. "
                    "종목명이나 키워드를 바꿔 다시 질문해 주세요."
                ),
                sources=[],
                retrieved_documents=[],
            )

        context = format_documents_for_prompt(documents)
        answer = self.chain.invoke({"context": context, "question": question})
        return QAResult(
            answer=answer,
            sources=extract_sources(documents),
            retrieved_documents=documents,
        )

