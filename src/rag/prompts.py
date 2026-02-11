from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

SYSTEM_PROMPT = """당신은 증권사 리포트 분석 전문 AI 어시스턴트입니다.
제공된 증권사 리포트 내용을 바탕으로 사용자의 질문에 정확하게 답변합니다.

답변 규칙:
1. 제공된 컨텍스트만 근거로 답변합니다.
2. 컨텍스트에 없는 정보는 추측하지 않습니다.
3. 답변 끝에 출처(증권사/애널리스트/날짜/파일명)를 표시합니다.
4. 항상 한국어로 답변합니다.
"""

USER_PROMPT = """다음은 관련 증권사 리포트 내용입니다:

{context}

---
사용자 질문: {question}

위 리포트 내용을 바탕으로 답변해 주세요."""


def build_qa_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            ("user", USER_PROMPT),
        ]
    )

