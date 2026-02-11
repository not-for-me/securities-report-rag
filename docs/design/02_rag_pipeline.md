# 02. RAG Pipeline 설계

> 증권사 리포트 PDF를 파싱하고, 청킹 및 임베딩을 거쳐 Vector DB에 적재한 뒤, 자연어 질의에 대해 정확한 답변을 생성하는 RAG 파이프라인의 상세 설계

---

## 1. PDF 파싱 전략

### 1.1 Upstage Document Parse API 활용

증권사 리포트는 텍스트, 테이블, 차트, 이미지가 복합적으로 구성된 문서이다. Upstage Document Parse API는 레이아웃 인식 기반으로 이러한 복합 요소를 구조적으로 추출하는 데 강점이 있다.

**API 호출 기본 구성:**

```python
import httpx

async def parse_pdf(file_path: str) -> dict:
    url = "https://api.upstage.ai/v1/document-ai/document-parse"
    headers = {"Authorization": f"Bearer {UPSTAGE_API_KEY}"}

    with open(file_path, "rb") as f:
        files = {"document": f}
        data = {
            "output_format": "markdown",
            "coordinates": False,
            "ocr": "auto",
            "model": "document-parse",
        }
        response = httpx.post(url, headers=headers, files=files, data=data)
    return response.json()
```

### 1.2 output_format 선택: Markdown

| 기준 | Markdown | HTML |
|------|----------|------|
| 청킹 호환성 | LangChain `MarkdownHeaderTextSplitter`와 직접 호환 | 별도 파서 필요 |
| 테이블 표현 | Markdown 테이블 (파이프 구분) | `<table>` 태그 |
| 후처리 용이성 | 정규식으로 헤더/테이블 패턴 추출 용이 | DOM 파싱 필요 |
| LLM 입력 적합성 | LLM이 Markdown 테이블을 잘 이해 | HTML도 가능하나 토큰 효율 낮음 |
| 가독성 | 사람이 직접 읽기 편함 | 태그로 인해 가독성 저하 |

**선택: Markdown**

- LangChain의 Markdown 기반 splitter와 자연스럽게 연계
- LLM 컨텍스트 윈도우 내 토큰 효율이 더 높음
- 증권사 리포트의 핵심인 테이블이 Markdown 테이블로 깔끔하게 표현됨

### 1.3 증권사 리포트 특성에 맞는 파싱 옵션

```python
PARSE_OPTIONS = {
    "output_format": "markdown",
    "coordinates": False,       # 좌표 정보 불필요 (텍스트 검색 목적)
    "ocr": "auto",             # 스캔 PDF가 섞여 있을 수 있으므로 auto
    "model": "document-parse",  # 최신 모델 사용
}
```

**주의사항:**
- 증권사 리포트는 대부분 디지털 PDF이나, 간혹 스캔 이미지가 포함되므로 `ocr: auto` 설정
- 차트/그래프 이미지는 텍스트 추출이 불가하므로, 차트 아래 캡션 텍스트를 활용
- 파싱 결과는 `data/parsed/{파일명}.md`로 캐싱하여 재파싱 방지

---

## 2. 청킹 전략 상세

### 2.1 청킹 전략 개요

증권사 리포트는 테이블과 텍스트가 혼재하며, 각각 다른 청킹 전략이 필요하다.

```
[리포트 파싱 결과]
    │
    ├─ 디스클레이머 필터링 (제거)
    │
    ├─ 테이블 청크 추출 (테이블 + 전후 설명)
    │
    └─ 텍스트 청크 분할 (섹션/소제목 기준)
         │
         └─ 최종 청크 리스트 + 메타데이터
```

### 2.2 테이블 청크

테이블은 증권사 리포트의 핵심 정보(실적 추정, 밸류에이션, 투자의견 등)를 담고 있으므로 절대 분할하지 않는다.

**규칙:**

1. **테이블 탐지**: Markdown 테이블 패턴(`|---|` 포함 블록)을 정규식으로 탐지
2. **테이블 보존**: 테이블 전체를 하나의 청크로 유지
3. **컨텍스트 포함**: 테이블 직전 1~2 문단(설명)과 직후 1 문단(해석)을 같은 청크에 포함
4. **청크 타입 태깅**: `chunk_type: "table"` 메타데이터 부착

```python
import re

TABLE_PATTERN = re.compile(
    r"((?:^.*\n)?)"           # 테이블 직전 문단 (선택)
    r"((?:^\|.+\|$\n?)+"     # 테이블 본체 (파이프로 시작/끝)
    r"(?:^\|[-:| ]+\|$\n?)"  # 구분선
    r"(?:^\|.+\|$\n?)*)"     # 데이터 행
    r"((?:^.*\n)?)",          # 테이블 직후 문단 (선택)
    re.MULTILINE
)
```

**테이블 전후 문단 포함 기준:**
- 직전 문단: 테이블 바로 위의 비어 있지 않은 텍스트 줄 (보통 테이블 제목/설명)
- 직후 문단: 테이블 바로 아래의 비어 있지 않은 텍스트 줄 (보통 출처 또는 주석)
- 직전/직후 텍스트가 다른 테이블이면 포함하지 않음

### 2.3 텍스트 청크

**분할 기준:**

1. **1차 분할**: Markdown 헤더(`#`, `##`, `###`) 기준으로 섹션 분할
2. **2차 분할**: 섹션이 너무 길 경우 `RecursiveCharacterTextSplitter`로 추가 분할

**청크 크기 설정:**

| 파라미터 | 값 | 근거 |
|----------|-----|------|
| `chunk_size` | 1,000 tokens (약 2,000~2,500 한글 문자) | 증권사 리포트 한 섹션의 평균 길이에 근접. `text-embedding-3-small`의 최대 입력 8,191 tokens 이내에서 충분한 의미 단위 확보 |
| `chunk_overlap` | 200 tokens (약 400~500 한글 문자) | 섹션 경계에서 문맥 손실 방지. 전체 청크 대비 20% 수준으로 적절 |
| `separators` | `["\n## ", "\n### ", "\n\n", "\n", " "]` | Markdown 헤더 우선, 문단 경계 차선 |

### 2.4 Splitter 비교 및 선택

#### MarkdownHeaderTextSplitter

- **장점**: Markdown 헤더 계층을 인식하여 의미 단위로 정확하게 분할. 헤더를 메타데이터로 자동 추출
- **단점**: 헤더가 없는 긴 본문은 분할하지 못함. 테이블 내부 분할 위험

#### RecursiveCharacterTextSplitter

- **장점**: 범용적. chunk_size/overlap 세밀 제어 가능. 긴 텍스트도 안정적으로 분할
- **단점**: 의미 단위 보장이 어려움. 문장 중간 절단 가능성

#### 선택: 2단계 하이브리드 접근

```python
from langchain.text_splitter import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

# 1단계: Markdown 헤더 기준 분할
headers_to_split_on = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
]
markdown_splitter = MarkdownHeaderTextSplitter(
    headers_to_split_on=headers_to_split_on,
    strip_headers=False,  # 헤더를 청크 내용에 유지 (검색 시 컨텍스트)
)

# 2단계: 큰 섹션을 추가 분할
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    separators=["\n\n", "\n", " "],
    length_function=len,  # 실제 구현 시 tiktoken 기반 토큰 카운터 사용
)

def split_text_chunks(markdown_text: str) -> list:
    # 1단계: 헤더 기준 분할
    header_splits = markdown_splitter.split_text(markdown_text)

    # 2단계: 큰 청크 추가 분할
    final_chunks = text_splitter.split_documents(header_splits)
    return final_chunks
```

**하이브리드 접근의 이점:**
- `MarkdownHeaderTextSplitter`로 의미 단위를 먼저 확보
- 의미 단위가 너무 긴 경우에만 `RecursiveCharacterTextSplitter`가 보조적으로 분할
- 헤더 정보가 메타데이터로 자동 보존되어 retrieval 시 섹션 컨텍스트 제공

### 2.5 디스클레이머 필터링

증권사 리포트 하단에는 법적 고지사항(디스클레이머)이 포함되며, 이는 검색/답변에 불필요하다.

**필터링 전략:**

```python
DISCLAIMER_PATTERNS = [
    r"본\s*조사자료는\s*고객의\s*투자에\s*참고",
    r"투자판단의\s*최종\s*책임은",
    r"당사는\s*본\s*자료의\s*내용에\s*의거하여",
    r"이\s*자료에\s*게재된\s*내용들은\s*작성자의\s*의견",
    r"Compliance\s*Notice",
    r"본\s*자료는\s*투자\s*참고용으로\s*작성",
    r"과거의\s*수익률.*미래의\s*수익률을\s*보장",
]

def filter_disclaimers(chunks: list) -> list:
    """디스클레이머 패턴이 포함된 청크를 제거"""
    filtered = []
    for chunk in chunks:
        content = chunk.page_content
        is_disclaimer = any(
            re.search(pattern, content) for pattern in DISCLAIMER_PATTERNS
        )
        if not is_disclaimer:
            filtered.append(chunk)
    return filtered
```

**필터링 시점**: 청킹 완료 후, 임베딩 전에 수행

---

## 3. 메타데이터 추출

### 3.1 추출 대상

| 필드 | 타입 | 설명 | 추출 방법 |
|------|------|------|-----------|
| `ticker` | string | 종목코드 (예: 005930) | 정규식 + 종목 코드 매핑 테이블 |
| `company_name` | string | 종목명 (예: 삼성전자) | 파싱 결과 첫 페이지에서 추출 |
| `date` | string | 리포트 발행일 (YYYY-MM-DD) | 정규식 패턴 매칭 |
| `broker` | string | 증권사명 | 파일명 규칙 또는 본문 패턴 |
| `analyst` | string | 애널리스트명 | 정규식 (이름 패턴) |
| `target_price` | integer | 목표가 (원) | 정규식 (숫자 + "원" 패턴) |
| `rating` | string | 투자의견 (매수/중립/매도 등) | 키워드 매칭 |
| `report_type` | string | 리포트 유형 | 키워드 분류 |
| `source_file` | string | 원본 PDF 파일명 | 파일 경로에서 추출 |

### 3.2 정규식 기반 추출

리포트 첫 1~2 페이지에 핵심 메타데이터가 집중되어 있으므로, 파싱 결과의 상위 부분을 우선 분석한다.

```python
import re
from datetime import datetime

def extract_date(text: str) -> str | None:
    """리포트 발행일 추출"""
    patterns = [
        r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})",       # 2026.02.10, 2026-02-10
        r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일",         # 2026년 2월 10일
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            y, m, d = match.groups()
            return f"{y}-{int(m):02d}-{int(d):02d}"
    return None

def extract_target_price(text: str) -> int | None:
    """목표가 추출"""
    patterns = [
        r"목표주?가\s*[:\s]*([0-9,]+)\s*원",
        r"Target\s*Price\s*[:\s]*([0-9,]+)",
        r"TP\s*[:\s]*([0-9,]+)\s*원",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            price_str = match.group(1).replace(",", "")
            return int(price_str)
    return None

RATING_KEYWORDS = {
    "매수": ["매수", "Buy", "BUY", "Overweight", "비중확대"],
    "중립": ["중립", "Hold", "HOLD", "Neutral", "시장수익률"],
    "매도": ["매도", "Sell", "SELL", "Underweight", "비중축소"],
}

def extract_rating(text: str) -> str | None:
    """투자의견 추출"""
    # 투자의견 관련 문맥 근처에서 탐색
    context_match = re.search(
        r"(?:투자의견|투자등급|Rating|Recommendation)[:\s]*(\S+)",
        text
    )
    if context_match:
        value = context_match.group(1)
        for rating, keywords in RATING_KEYWORDS.items():
            if any(kw in value for kw in keywords):
                return rating
    return None
```

### 3.3 LLM 기반 추출 (보조)

정규식으로 추출이 어려운 경우(비정형 레이아웃, 특이한 표기법), LLM을 보조적으로 활용한다.

```python
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

METADATA_EXTRACTION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """증권사 리포트의 첫 페이지 텍스트에서 메타데이터를 추출하세요.
다음 JSON 형식으로 응답하세요:
{{
    "company_name": "종목명",
    "ticker": "종목코드(6자리)",
    "date": "YYYY-MM-DD",
    "broker": "증권사명",
    "analyst": "애널리스트명",
    "target_price": 숫자(원),
    "rating": "매수/중립/매도 중 하나",
    "report_type": "실적분석/기업분석/업종분석 중 하나"
}}
추출할 수 없는 필드는 null로 표시하세요."""),
    ("user", "{first_page_text}")
])

async def extract_metadata_with_llm(first_page_text: str) -> dict:
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    chain = METADATA_EXTRACTION_PROMPT | llm
    result = await chain.ainvoke({"first_page_text": first_page_text})
    return parse_json_response(result.content)
```

**추출 전략 우선순위:**
1. 정규식 기반 추출 시도 (빠르고 비용 없음)
2. 추출 실패 필드가 있으면 LLM 기반 추출로 보완 (gpt-4o-mini 사용으로 비용 최소화)
3. 최종 결과 검증 (필수 필드 누락 시 경고 로그)

---

## 4. Embedding 설계

### 4.1 모델 선택: text-embedding-3-small

| 기준 | text-embedding-3-small | text-embedding-3-large |
|------|------------------------|------------------------|
| 차원 수 | 1,536 (기본) | 3,072 (기본) |
| 비용 | $0.02 / 1M tokens | $0.13 / 1M tokens |
| MTEB 벤치마크 | 62.3% | 64.6% |
| 한국어 성능 | 양호 (다국어 학습) | 약간 우수 |
| 저장 공간 | 상대적 작음 | 약 2배 |

**선택 근거:**
- 비용이 6.5배 차이나는 반면 성능 차이는 약 2%p로 미미
- 증권사 리포트는 도메인이 한정적이므로 small 모델로도 충분한 구분력 확보
- ChromaDB 로컬 환경에서 저장 공간 및 검색 속도 측면에서 유리
- 학습/프로토타이핑 단계에서 비용 효율이 중요

### 4.2 차원 설정

```python
from langchain_openai import OpenAIEmbeddings

embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    dimensions=1536,  # 기본값 유지. 차원 축소(예: 512) 시 한국어 성능 저하 우려
)
```

- `text-embedding-3-small`은 Matryoshka Representation Learning을 지원하여 차원 축소가 가능하나, 한국어 텍스트의 미세한 의미 차이(종목명, 증권 용어 등)를 보존하기 위해 기본 1,536 차원을 유지한다.

### 4.3 한국어 성능 특성

- OpenAI embedding 모델은 다국어 코퍼스로 학습되어 한국어 지원
- 한국어 증권 용어(PER, PBR, EPS 등)는 영문 약어가 그대로 사용되어 embedding 품질에 유리
- 종목명은 한글 고유명사이므로 메타데이터 필터링으로 보완 (embedding 유사도만으로 종목 구분 한계)

---

## 5. ChromaDB 적재

### 5.1 컬렉션 구성

단일 컬렉션으로 구성하고, 메타데이터 필터링으로 검색 범위를 제어한다.

```python
import chromadb
from langchain_chroma import Chroma

COLLECTION_NAME = "securities_reports"
PERSIST_DIR = "./data/chromadb"

vectorstore = Chroma(
    collection_name=COLLECTION_NAME,
    embedding_function=embeddings,
    persist_directory=PERSIST_DIR,
    collection_metadata={
        "hnsw:space": "cosine",  # distance metric
        "hnsw:M": 16,            # HNSW 그래프 연결 수 (기본값)
        "hnsw:construction_ef": 100,  # 인덱스 구축 시 탐색 범위
    },
)
```

**단일 컬렉션 선택 근거:**
- 증권사 리포트는 동일 도메인이므로 컬렉션 분리 불필요
- 메타데이터 필터(`company_name`, `broker`, `date`)로 세밀한 범위 지정 가능
- SelfQueryRetriever가 단일 컬렉션 기준으로 동작

### 5.2 Distance Metric: Cosine Similarity

| Metric | 특성 | 적합성 |
|--------|------|--------|
| Cosine | 방향 기반 유사도, 벡터 크기 무관 | 텍스트 의미 유사도에 적합 |
| L2 (Euclidean) | 거리 기반, 벡터 크기에 민감 | 클러스터링에 적합 |
| Inner Product | 내적 기반, 정규화 필요 | 추천 시스템에 적합 |

**Cosine 선택 이유:**
- 텍스트 임베딩의 의미적 유사도 비교에 가장 널리 사용
- OpenAI embedding은 이미 정규화되어 있어 cosine과 inner product 결과가 동일하지만, 의미적 명확성을 위해 cosine 사용
- 문서 길이에 따른 벡터 크기 차이에 영향받지 않음

### 5.3 메타데이터 인덱싱

ChromaDB는 메타데이터 필터링을 자동으로 지원하며, 별도 인덱스 설정 없이 `where` 절로 필터링 가능하다.

```python
# 적재 시 메타데이터 포함
vectorstore.add_documents(
    documents=chunks,
    # 각 Document에 metadata dict가 포함됨:
    # {
    #     "ticker": "005930",
    #     "company_name": "삼성전자",
    #     "date": "2026-02-10",
    #     "broker": "미래에셋증권",
    #     "analyst": "홍길동",
    #     "target_price": 85000,
    #     "rating": "매수",
    #     "report_type": "실적분석",
    #     "source_file": "mirae_samsung_20260210.pdf",
    #     "chunk_type": "text",  # "text" | "table"
    #     "section_header": "실적 분석",  # MarkdownHeaderTextSplitter에서 추출
    # }
)
```

**메타데이터 필드별 타입:**
- `string` 타입: `ticker`, `company_name`, `date`, `broker`, `analyst`, `rating`, `report_type`, `source_file`, `chunk_type`, `section_header`
- `integer` 타입: `target_price`

---

## 6. Retriever 설계

### 6.1 SelfQueryRetriever 구성

SelfQueryRetriever는 사용자의 자연어 질문에서 의미적 쿼리와 메타데이터 필터를 자동으로 분리한다.

**예시:**
- 질문: "삼성전자 최근 목표가 알려줘"
- 분리 결과:
  - Semantic query: "목표가"
  - Metadata filter: `company_name == "삼성전자"`, sort by `date` desc

```python
from langchain.retrievers.self_query.base import SelfQueryRetriever
from langchain.chains.query_constructor.schema import AttributeInfo

metadata_field_info = [
    AttributeInfo(
        name="company_name",
        description="종목명. 예: 삼성전자, SK하이닉스, LG에너지솔루션, 네이버, 카카오",
        type="string",
    ),
    AttributeInfo(
        name="ticker",
        description="종목코드(6자리 숫자). 예: 005930(삼성전자), 000660(SK하이닉스)",
        type="string",
    ),
    AttributeInfo(
        name="date",
        description="리포트 발행일. 형식: YYYY-MM-DD. 예: 2026-02-10",
        type="string",
    ),
    AttributeInfo(
        name="broker",
        description="리포트를 발행한 증권사명. 예: 미래에셋증권, 한국투자증권, NH투자증권, 삼성증권, KB증권",
        type="string",
    ),
    AttributeInfo(
        name="analyst",
        description="리포트를 작성한 애널리스트 이름",
        type="string",
    ),
    AttributeInfo(
        name="report_type",
        description="리포트 유형. 가능한 값: 실적분석, 기업분석, 업종분석",
        type="string",
    ),
    AttributeInfo(
        name="rating",
        description="투자의견. 가능한 값: 매수, 중립, 매도",
        type="string",
    ),
    AttributeInfo(
        name="target_price",
        description="목표 주가(원). 정수값",
        type="integer",
    ),
    AttributeInfo(
        name="chunk_type",
        description="청크 유형. 가능한 값: text, table",
        type="string",
    ),
]

DOCUMENT_CONTENT_DESCRIPTION = (
    "증권사 애널리스트가 작성한 기업 분석 리포트. "
    "실적 분석, 투자 의견, 목표 주가, 밸류에이션, 업종 전망 등의 내용을 포함."
)

retriever = SelfQueryRetriever.from_llm(
    llm=ChatOpenAI(model="gpt-4o-mini", temperature=0),
    vectorstore=vectorstore,
    document_contents=DOCUMENT_CONTENT_DESCRIPTION,
    metadata_field_info=metadata_field_info,
    enable_limit=True,
    search_type="similarity_score_threshold",
    search_kwargs={
        "k": 5,
        "score_threshold": 0.3,
    },
)
```

### 6.2 검색 파라미터

| 파라미터 | 값 | 근거 |
|----------|-----|------|
| `k` | 5 | 증권사 리포트 답변에 3~5개 출처가 적정. 너무 많으면 컨텍스트 노이즈 증가 |
| `score_threshold` | 0.3 | cosine similarity 기준. 증권 도메인 특성상 관련 문서 간 유사도가 높으므로 낮은 threshold로 recall 확보 후 LLM이 필터링 |
| `search_type` | `similarity_score_threshold` | 관련 없는 문서가 혼입되는 것을 방지하기 위해 threshold 적용 |

### 6.3 Fallback 전략

SelfQueryRetriever가 메타데이터 필터를 과도하게 적용하여 결과가 없는 경우를 대비한다.

```python
def retrieve_with_fallback(query: str) -> list:
    # 1차: SelfQueryRetriever (메타데이터 필터 + 유사도)
    results = retriever.invoke(query)

    if not results:
        # 2차: 메타데이터 필터 없이 순수 유사도 검색
        results = vectorstore.similarity_search_with_score(
            query, k=5
        )
        results = [doc for doc, score in results if score >= 0.3]

    return results
```

---

## 7. QA Chain 설계

### 7.1 프롬프트 템플릿

#### System Prompt

```python
SYSTEM_PROMPT = """당신은 증권사 리포트 분석 전문 AI 어시스턴트입니다.
제공된 증권사 리포트 내용을 바탕으로 사용자의 질문에 정확하게 답변합니다.

## 답변 규칙

1. **출처 기반 답변**: 반드시 제공된 리포트 내용만을 근거로 답변하세요. 제공되지 않은 정보를 추측하거나 생성하지 마세요.
2. **출처 표시**: 답변 끝에 참고한 리포트의 출처를 표시하세요.
3. **테이블 활용**: 실적 데이터, 밸류에이션 등 숫자 정보는 가능하면 테이블 형태로 정리하세요.
4. **복수 리포트 종합**: 여러 증권사의 리포트가 있을 경우, 각 증권사의 의견을 비교하여 제시하세요.
5. **답변 불가 시**: 관련 리포트가 없거나 답변할 수 없는 경우, "제공된 리포트에서 해당 정보를 찾을 수 없습니다."라고 명확히 밝히세요.
6. **한국어 답변**: 항상 한국어로 답변하세요. 숫자와 기술 용어는 원문 그대로 사용합니다.

## 출처 표시 형식

답변 끝에 다음 형식으로 출처를 표시하세요:
---
📊 출처:
- [증권사명] 애널리스트명, "종목명 리포트" (YYYY.MM.DD)
"""
```

#### User Prompt

```python
USER_PROMPT = """다음은 관련 증권사 리포트 내용입니다:

{context}

---
사용자 질문: {question}

위 리포트 내용을 바탕으로 답변해주세요."""
```

### 7.2 Chain 구성

```python
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

def format_docs(docs: list) -> str:
    """검색된 문서를 컨텍스트 문자열로 포맷"""
    formatted = []
    for i, doc in enumerate(docs, 1):
        meta = doc.metadata
        header = (
            f"[출처 {i}] {meta.get('broker', '알 수 없음')} | "
            f"{meta.get('analyst', '알 수 없음')} | "
            f"{meta.get('company_name', '알 수 없음')} | "
            f"{meta.get('date', '날짜 미상')}"
        )
        formatted.append(f"{header}\n{doc.page_content}")
    return "\n\n---\n\n".join(formatted)

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("user", USER_PROMPT),
])

llm = ChatOpenAI(
    model="gpt-4o-mini",   # 개발 단계
    # model="gpt-4o",       # 운영 단계
    temperature=0,
)

rag_chain = (
    {
        "context": retriever | format_docs,
        "question": RunnablePassthrough(),
    }
    | prompt
    | llm
    | StrOutputParser()
)
```

### 7.3 답변 불가 처리

답변 불가 상황은 두 가지로 구분한다:

1. **검색 결과 없음** (retriever가 빈 리스트 반환)
   - "관련 증권사 리포트를 찾을 수 없습니다. 종목명이나 키워드를 확인해주세요."

2. **검색 결과는 있지만 질문과 무관** (LLM이 판단)
   - System prompt의 규칙 5에 의해 LLM이 "제공된 리포트에서 해당 정보를 찾을 수 없습니다." 응답

```python
async def answer_question(question: str) -> str:
    docs = retriever.invoke(question)

    if not docs:
        return (
            "관련 증권사 리포트를 찾을 수 없습니다. "
            "종목명이나 키워드를 확인해주세요.\n\n"
            "💡 예시 질문:\n"
            "- 삼성전자 목표가 알려줘\n"
            "- SK하이닉스 최근 실적 요약\n"
            "- 반도체 업종 전망"
        )

    return await rag_chain.ainvoke(question)
```

---

## 8. 품질 평가

### 8.1 Retrieval 정확도 측정

#### Hit Rate (적중률)

k개의 검색 결과 중 정답 문서가 하나라도 포함되면 적중으로 간주한다.

```
Hit Rate@k = (정답이 포함된 질문 수) / (전체 질문 수)
```

#### Mean Reciprocal Rank (MRR)

정답 문서가 검색 결과에서 몇 번째에 나타나는지를 측정한다.

```
MRR = (1/N) * Σ (1 / rank_i)
```

### 8.2 질문-정답 쌍 평가 프레임워크

#### 평가 데이터셋 구성

실제 사용 시나리오를 반영한 질문-정답 쌍을 수동으로 구성한다.

```python
EVAL_DATASET = [
    {
        "question": "삼성전자 목표가 컨센서스 알려줘",
        "expected_answer_contains": ["목표가", "원"],
        "expected_sources": {"company_name": "삼성전자"},
        "category": "factual",
    },
    {
        "question": "SK하이닉스 최근 실적 요약해줘",
        "expected_answer_contains": ["매출", "영업이익"],
        "expected_sources": {"company_name": "SK하이닉스"},
        "category": "summary",
    },
    {
        "question": "반도체 업종 전망 리포트 중 가장 긍정적인 의견은?",
        "expected_answer_contains": ["매수", "긍정"],
        "expected_sources": {"report_type": "업종분석"},
        "category": "comparison",
    },
    {
        "question": "오늘 날씨 어때?",
        "expected_answer_contains": ["찾을 수 없습니다"],
        "expected_sources": None,
        "category": "out_of_scope",
    },
]
```

#### 평가 카테고리

| 카테고리 | 설명 | 평가 기준 |
|----------|------|-----------|
| `factual` | 특정 수치/팩트 질의 | 정확한 수치 포함 여부 |
| `summary` | 요약 질의 | 핵심 키워드 포함 여부 |
| `comparison` | 비교 질의 | 복수 출처 참조 여부 |
| `table` | 테이블 데이터 질의 | 테이블 청크 검색 여부 |
| `out_of_scope` | 범위 외 질문 | 적절한 거절 응답 |

#### 평가 실행

```python
async def evaluate_rag(eval_dataset: list) -> dict:
    results = {"total": len(eval_dataset), "passed": 0, "failed": 0, "details": []}

    for item in eval_dataset:
        answer = await answer_question(item["question"])

        # 키워드 포함 여부 확인
        keywords_found = all(
            kw in answer for kw in item["expected_answer_contains"]
        )

        result = {
            "question": item["question"],
            "category": item["category"],
            "passed": keywords_found,
            "answer_preview": answer[:200],
        }

        if keywords_found:
            results["passed"] += 1
        else:
            results["failed"] += 1

        results["details"].append(result)

    results["pass_rate"] = results["passed"] / results["total"]
    return results
```

### 8.3 평가 목표 지표

| 지표 | 목표값 | 설명 |
|------|--------|------|
| Hit Rate@5 | >= 0.8 | 5개 검색 결과 중 정답 포함 |
| MRR | >= 0.6 | 정답이 상위에 위치 |
| 키워드 Pass Rate | >= 0.7 | 답변 내 기대 키워드 포함 |
| Out-of-scope 정확도 | >= 0.9 | 범위 외 질문 정확 거절 |

### 8.4 반복 개선 프로세스

```
평가 실행 → 실패 케이스 분석 → 원인 분류 → 개선 적용 → 재평가
```

**원인 분류 및 대응:**

| 원인 | 대응 |
|------|------|
| 관련 청크가 검색되지 않음 | 청킹 전략 조정, chunk_size/overlap 변경 |
| 관련 청크가 검색되었으나 순위가 낮음 | embedding 모델 변경 또는 query 리포맷 |
| 검색은 잘 되었으나 답변 품질 저하 | QA prompt 개선, LLM 모델 업그레이드 |
| 메타데이터 필터가 잘못 적용됨 | SelfQueryRetriever의 AttributeInfo 설명 보완 |

---

## 부록: 전체 파이프라인 데이터 흐름

```
PDF 파일
  │
  ▼
[1. Upstage Document Parse API]
  │  output: Markdown 텍스트
  │
  ▼
[2. 디스클레이머 필터링]
  │  output: 정제된 Markdown
  │
  ▼
[3. 메타데이터 추출]  ──────────────┐
  │  output: metadata dict         │
  │                                │
  ▼                                │
[4. 테이블/텍스트 분리]              │
  │                                │
  ├── 테이블 청크 (통째 보존)        │
  │                                │
  └── 텍스트 청크                    │
       │  MarkdownHeaderTextSplitter │
       │  + RecursiveCharacterText   │
       │    Splitter                 │
       ▼                            │
[5. Embedding]                      │
  │  text-embedding-3-small         │
  │  1,536 dims                     │
  ▼                                 │
[6. ChromaDB 적재]  ◄──────────────┘
  │  collection: securities_reports
  │  metric: cosine
  │
  ▼
[7. SelfQueryRetriever]
  │  메타데이터 필터 + 유사도 검색
  │  k=5, score_threshold=0.3
  │
  ▼
[8. QA Chain]
  │  System prompt + Context + Question
  │  gpt-4o-mini (개발) / gpt-4o (운영)
  │
  ▼
답변 + 출처 표시
```
