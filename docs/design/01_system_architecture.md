# 01. 시스템 아키텍처 설계

> 증권사 리포트 RAG Slack Bot의 전체 시스템 구조, 컴포넌트 명세, 외부 API 연동, 에러 처리 전략을 정의한다.

---

## 1. 전체 시스템 아키텍처

시스템은 크게 두 가지 흐름으로 구분된다: **배치 파이프라인**(데이터 수집 및 적재)과 **실시간 서빙**(사용자 질의 응답).

### 1.1 배치 파이프라인 흐름

PDF 원본에서 벡터 DB 적재까지의 데이터 변환 파이프라인이다.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        배치 파이프라인 (Batch Pipeline)                     │
│                                                                          │
│  ┌─────────────┐    ┌──────────────────┐    ┌─────────────────────┐      │
│  │  PDF 수집    │    │  Upstage         │    │  Chunker            │      │
│  │             │───▶│  Document Parse   │───▶│                     │      │
│  │ data/       │    │  API              │    │ - 테이블 보존        │      │
│  │ raw_pdfs/   │    │                  │    │ - 섹션 기반 분할     │      │
│  │             │    │ PDF → Markdown    │    │ - 컨텍스트 묶기      │      │
│  └─────────────┘    └──────────────────┘    └──────────┬──────────┘      │
│        │                     │                          │                 │
│        │                     ▼                          ▼                 │
│        │            ┌──────────────────┐    ┌─────────────────────┐      │
│        │            │  Metadata        │    │  Embedder           │      │
│        │            │  Extractor       │    │                     │      │
│        └───────────▶│                  │───▶│ OpenAI Embedding    │      │
│                     │ 종목, 날짜,       │    │ text-embedding-     │      │
│                     │ 증권사, 애널리스트 │    │ 3-small             │      │
│                     └──────────────────┘    └──────────┬──────────┘      │
│                                                        │                 │
│                                                        ▼                 │
│                                              ┌─────────────────────┐    │
│                                              │  ChromaDB            │    │
│                                              │  (Vector Store)      │    │
│                                              │                     │    │
│                                              │  청크 + 임베딩       │    │
│                                              │  + 메타데이터 저장    │    │
│                                              └─────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
```

**파이프라인 단계별 데이터 흐름:**

```
PDF 파일 (.pdf)
    │
    ▼  [parser.py] Upstage Document Parse API 호출
Markdown 텍스트 + 메타데이터 JSON
    │
    ▼  [metadata.py] 종목명, 날짜, 증권사 등 추출
보강된 메타데이터
    │
    ▼  [chunker.py] 테이블 보존 + 섹션 기반 분할
Document 객체 리스트 (content + metadata)
    │
    ▼  [embedder.py] OpenAI Embedding API → ChromaDB 적재
벡터 임베딩 + 메타데이터 (ChromaDB에 영구 저장)
```

### 1.2 실시간 서빙 흐름

Slack 메시지 수신부터 RAG 기반 응답 반환까지의 실시간 처리 흐름이다.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        실시간 서빙 (Real-time Serving)                     │
│                                                                          │
│  ┌─────────────┐    ┌──────────────────┐    ┌─────────────────────┐      │
│  │  Slack       │    │  Slack Bolt      │    │  LangChain          │      │
│  │  사용자      │───▶│  App             │───▶│  RAG Chain          │      │
│  │             │    │                  │    │                     │      │
│  │ @봇 멘션    │    │ Socket Mode      │    │ SelfQuery           │      │
│  │ 또는 DM     │    │ 이벤트 수신       │    │ Retriever           │      │
│  └─────────────┘    └──────────────────┘    └──────────┬──────────┘      │
│                                                        │                 │
│                              ┌──────────────────────────┘                │
│                              │                                           │
│                              ▼                                           │
│                    ┌──────────────────┐    ┌─────────────────────┐      │
│                    │  ChromaDB        │    │  OpenAI Chat API    │      │
│                    │  (Vector Store)  │───▶│  (gpt-4o-mini /     │      │
│                    │                  │    │   gpt-4o)           │      │
│                    │  유사도 검색      │    │                     │      │
│                    │  + 메타데이터     │    │  컨텍스트 기반       │      │
│                    │    필터링         │    │  답변 생성           │      │
│                    └──────────────────┘    └──────────┬──────────┘      │
│                                                       │                 │
│                              ┌─────────────────────────┘                │
│                              ▼                                           │
│                    ┌──────────────────┐    ┌─────────────────────┐      │
│                    │  응답 포매팅      │    │  Slack 응답 전송     │      │
│                    │  (Block Kit)     │───▶│                     │      │
│                    │                  │    │  사용자에게 답변      │      │
│                    │  출처 정보 포함   │    │  + 출처 표시          │      │
│                    └──────────────────┘    └─────────────────────┘      │
└──────────────────────────────────────────────────────────────────────────┘
```

**서빙 단계별 데이터 흐름:**

```
Slack 메시지 (자연어 질문)
    │
    ▼  [handlers.py] 이벤트 수신 + 전처리
질문 텍스트
    │
    ▼  [retriever.py] SelfQueryRetriever → 메타데이터 필터 자동 추출
    │                 + ChromaDB 유사도 검색
관련 문서 청크 리스트
    │
    ▼  [chain.py] LLM에 컨텍스트 + 질문 전달
생성된 답변 텍스트 + 출처 정보
    │
    ▼  [handlers.py] Block Kit 포매팅 → Slack 전송
Slack 메시지 (답변 + 출처)
```

### 1.3 통합 시스템 다이어그램

```
                     ┌─────────────────────────────┐
                     │         외부 서비스            │
                     │                               │
                     │  ┌─────────┐  ┌────────────┐ │
                     │  │ Upstage │  │  OpenAI    │ │
                     │  │ API     │  │  API       │ │
                     │  └────┬────┘  └─────┬──────┘ │
                     │       │             │        │
                     └───────┼─────────────┼────────┘
                             │             │
              ┌──────────────┼─────────────┼──────────────────┐
              │              ▼             ▼                   │
              │  ┌────────────────────────────────────────┐   │
              │  │        src/pipeline/                    │   │
              │  │  parser → metadata → chunker → embedder│   │
              │  └────────────────────┬───────────────────┘   │
              │                       │                       │
              │                       ▼                       │
              │         ┌──────────────────────┐              │
              │         │    ChromaDB           │              │
              │         │    data/chromadb/     │              │
              │         └──────────┬───────────┘              │
              │                    │                           │
              │                    ▼                           │
              │  ┌────────────────────────────────────────┐   │
              │  │        src/rag/                         │   │
              │  │  retriever → chain → prompts            │   │
              │  └────────────────────┬───────────────────┘   │
              │                       │                       │
              │                       ▼                       │
              │  ┌────────────────────────────────────────┐   │
              │  │        src/slack/                       │   │
              │  │  app → handlers                         │   │
              │  └────────────────────┬───────────────────┘   │
              │                       │                       │
              │          증권사 리포트 RAG 시스템              │
              └───────────────────────┼───────────────────────┘
                                      │
                                      ▼
                           ┌──────────────────┐
                           │   Slack Platform  │
                           │   (Socket Mode)   │
                           └──────────────────┘
                                      │
                                      ▼
                              ┌──────────────┐
                              │   사용자       │
                              └──────────────┘
```

---

## 2. 컴포넌트 명세

### 2.1 `src/pipeline/parser.py` - PDF 파서

Upstage Document Parse API를 호출하여 PDF 파일을 Markdown 텍스트로 변환한다.

| 항목 | 내용 |
|------|------|
| **책임** | PDF 파일을 Upstage API로 전송하여 구조화된 Markdown 텍스트를 추출 |
| **입력** | PDF 파일 경로 (`str` 또는 `Path`) |
| **출력** | 파싱된 Markdown 텍스트 (`str`) + API 응답 메타데이터 (`dict`) |
| **의존성** | `httpx`, Upstage API Key |

**주요 인터페이스:**

```python
class DocumentParser:
    """Upstage Document Parse API를 이용한 PDF 파서."""

    def __init__(self, api_key: str):
        """API 키로 파서를 초기화한다."""

    def parse(self, pdf_path: str | Path) -> ParseResult:
        """
        PDF 파일을 파싱하여 Markdown으로 변환한다.

        Args:
            pdf_path: PDF 파일 경로

        Returns:
            ParseResult(content=str, metadata=dict, usage=dict)

        Raises:
            FileNotFoundError: PDF 파일이 존재하지 않을 때
            APIError: Upstage API 호출 실패 시
        """

    def parse_batch(self, pdf_paths: list[str | Path]) -> list[ParseResult]:
        """여러 PDF를 순차적으로 파싱한다. rate limit을 고려하여 딜레이를 둔다."""
```

**출력 데이터 형식:**

```python
@dataclass
class ParseResult:
    content: str          # Markdown 형식의 파싱 결과
    metadata: dict        # API 응답 메타데이터 (모델 버전, 페이지 수 등)
    usage: dict           # API 사용량 정보 (pages 등)
    source_file: str      # 원본 PDF 파일명
```

### 2.2 `src/pipeline/metadata.py` - 메타데이터 추출기

파싱된 Markdown과 PDF 파일명에서 리포트 수준의 메타데이터를 추출한다.

| 항목 | 내용 |
|------|------|
| **책임** | 종목명, 종목코드, 발행일, 증권사, 애널리스트, 목표가, 투자의견 등을 추출 |
| **입력** | Markdown 텍스트 (`str`) + 파일명 (`str`) |
| **출력** | `ReportMetadata` 딕셔너리 |
| **의존성** | 정규식, 파일명 파싱 규칙 |

**주요 인터페이스:**

```python
class MetadataExtractor:
    """리포트 메타데이터 추출기."""

    def extract(self, content: str, filename: str) -> ReportMetadata:
        """
        Markdown 텍스트와 파일명에서 메타데이터를 추출한다.

        Returns:
            ReportMetadata(
                ticker="005930",
                company_name="삼성전자",
                date="2026-02-10",
                broker="미래에셋증권",
                analyst="홍길동",
                report_type="실적분석",
                target_price=85000,
                rating="매수",
                source_file="mirae_samsung_20260210.pdf"
            )
        """
```

**출력 데이터 형식:**

```python
class ReportMetadata(TypedDict):
    ticker: str            # 종목코드 (예: "005930")
    company_name: str      # 종목명 (예: "삼성전자")
    date: str              # 발행일 (YYYY-MM-DD)
    broker: str            # 증권사명
    analyst: str           # 애널리스트명
    report_type: str       # 리포트 유형 (실적분석, 기업분석, 업종분석)
    target_price: int | None  # 목표가 (원)
    rating: str | None     # 투자의견 (매수, 중립, 매도 등)
    source_file: str       # 원본 파일명
```

### 2.3 `src/pipeline/chunker.py` - 청킹 모듈

Markdown 텍스트를 의미 단위로 분할하되, 테이블 구조를 보존한다.

| 항목 | 내용 |
|------|------|
| **책임** | Markdown 텍스트를 검색에 적합한 크기의 청크로 분할. 테이블은 반드시 통째로 유지 |
| **입력** | Markdown 텍스트 (`str`) + `ReportMetadata` |
| **출력** | LangChain `Document` 객체 리스트 |
| **의존성** | `langchain.text_splitter` |

**주요 인터페이스:**

```python
class ReportChunker:
    """증권사 리포트 전용 청킹 모듈."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        """청크 크기와 오버랩을 설정한다."""

    def chunk(self, content: str, metadata: ReportMetadata) -> list[Document]:
        """
        Markdown 텍스트를 청크로 분할한다.

        전략:
        1. Markdown 헤더 기준으로 1차 분할
        2. 테이블 블록 감지 → 통째로 하나의 청크로 유지
        3. 테이블 직전/직후 설명 문단을 같은 청크에 포함
        4. 긴 텍스트 섹션은 chunk_size 기준으로 추가 분할
        5. 각 청크에 리포트 레벨 메타데이터 부착

        Returns:
            list[Document]: page_content와 metadata를 포함한 Document 리스트
        """
```

**출력 데이터 형식:**

```python
# LangChain Document 객체
Document(
    page_content="## 실적 요약\n\n삼성전자는 2025년 4분기...\n\n| 항목 | 금액 |\n|---|---|\n| 매출 | 79조 |",
    metadata={
        "ticker": "005930",
        "company_name": "삼성전자",
        "date": "2026-02-10",
        "broker": "미래에셋증권",
        "analyst": "홍길동",
        "report_type": "실적분석",
        "source_file": "mirae_samsung_20260210.pdf",
        "chunk_type": "table_with_context",  # text, table, table_with_context
        "chunk_index": 2
    }
)
```

### 2.4 `src/pipeline/embedder.py` - 임베딩 및 적재 모듈

Document 객체를 OpenAI Embedding API로 벡터화하여 ChromaDB에 적재한다.

| 항목 | 내용 |
|------|------|
| **책임** | 청크를 임베딩 벡터로 변환하고 ChromaDB에 메타데이터와 함께 저장 |
| **입력** | `list[Document]` (LangChain Document 객체) |
| **출력** | ChromaDB에 영구 저장 (반환값: 적재된 문서 수) |
| **의존성** | `langchain-openai`, `langchain-chroma`, `chromadb` |

**주요 인터페이스:**

```python
class ReportEmbedder:
    """리포트 청크를 임베딩하여 ChromaDB에 적재한다."""

    def __init__(
        self,
        openai_api_key: str,
        persist_directory: str = "./data/chromadb",
        collection_name: str = "securities_reports"
    ):
        """임베딩 모델과 ChromaDB 클라이언트를 초기화한다."""

    def embed_and_store(self, documents: list[Document]) -> int:
        """
        Document 리스트를 임베딩하여 ChromaDB에 적재한다.

        Returns:
            적재된 문서(청크) 수
        """

    def get_vectorstore(self) -> Chroma:
        """LangChain Chroma 벡터스토어 인스턴스를 반환한다."""
```

### 2.5 `src/rag/retriever.py` - Retriever 모듈

사용자 질문에서 메타데이터 필터를 자동 추출하고, ChromaDB에서 관련 문서를 검색한다.

| 항목 | 내용 |
|------|------|
| **책임** | 자연어 질문을 메타데이터 필터 + 의미 검색으로 분해하여 관련 청크를 검색 |
| **입력** | 질문 텍스트 (`str`) |
| **출력** | 관련 `Document` 리스트 (유사도 순 정렬) |
| **의존성** | `langchain`, `langchain-openai`, `langchain-chroma` |

**주요 인터페이스:**

```python
class ReportRetriever:
    """SelfQueryRetriever 기반 증권 리포트 검색 모듈."""

    def __init__(self, vectorstore: Chroma, llm: ChatOpenAI):
        """SelfQueryRetriever를 구성한다."""

    def retrieve(self, query: str, k: int = 5) -> list[Document]:
        """
        자연어 질문으로 관련 문서를 검색한다.

        예시:
          "삼성전자 최신 리포트 목표가"
          → 내부적으로 company_name="삼성전자" 필터 + "목표가" 의미 검색

        Returns:
            유사도 순으로 정렬된 Document 리스트 (최대 k개)
        """
```

### 2.6 `src/rag/chain.py` - QA Chain 모듈

검색된 문서를 컨텍스트로 활용하여 LLM 기반 답변을 생성한다.

| 항목 | 내용 |
|------|------|
| **책임** | Retriever가 반환한 문서를 LLM에 전달하여 질문에 대한 답변 생성 |
| **입력** | 질문 텍스트 (`str`) |
| **출력** | 답변 텍스트 (`str`) + 출처 정보 (`list[dict]`) |
| **의존성** | `langchain`, `langchain-openai` |

**주요 인터페이스:**

```python
class ReportQAChain:
    """증권 리포트 RAG QA Chain."""

    def __init__(self, retriever: ReportRetriever, llm: ChatOpenAI):
        """RetrievalQA 또는 ConversationalRetrievalQA Chain을 구성한다."""

    def ask(self, question: str) -> QAResult:
        """
        질문에 대해 RAG 기반 답변을 생성한다.

        Returns:
            QAResult(
                answer="삼성전자의 목표가는 미래에셋증권 기준 85,000원입니다.",
                sources=[
                    {"broker": "미래에셋증권", "date": "2026-02-10", "file": "mirae_samsung_20260210.pdf"}
                ]
            )
        """
```

**출력 데이터 형식:**

```python
@dataclass
class QAResult:
    answer: str                # 생성된 답변 텍스트
    sources: list[dict]        # 출처 정보 리스트
    retrieved_documents: list[Document]  # 검색에 사용된 원본 Document
```

### 2.7 `src/rag/prompts.py` - 프롬프트 템플릿

RAG Chain에서 사용하는 시스템 프롬프트와 질의 템플릿을 관리한다.

| 항목 | 내용 |
|------|------|
| **책임** | LLM에 전달할 시스템 프롬프트, QA 프롬프트 템플릿 정의 |
| **입력** | 컨텍스트 문서 + 질문 텍스트 |
| **출력** | 포매팅된 프롬프트 문자열 |
| **의존성** | `langchain.prompts` |

### 2.8 `src/slack/app.py` - Slack App 초기화

Slack Bolt 앱을 초기화하고 Socket Mode로 연결한다.

| 항목 | 내용 |
|------|------|
| **책임** | Slack Bolt App 인스턴스 생성, Socket Mode 연결, 이벤트 핸들러 등록 |
| **입력** | 환경변수 (SLACK_BOT_TOKEN, SLACK_APP_TOKEN) |
| **출력** | 실행 중인 Slack Bot 프로세스 |
| **의존성** | `slack-bolt`, `slack-sdk` |

**주요 인터페이스:**

```python
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

app = App(token=os.environ["SLACK_BOT_TOKEN"])

# 이벤트 핸들러 등록
register_handlers(app)

# Socket Mode로 실행
handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
handler.start()
```

### 2.9 `src/slack/handlers.py` - Slack 이벤트 핸들러

Slack 메시지/멘션 이벤트를 처리하고 RAG 기반 응답을 반환한다.

| 항목 | 내용 |
|------|------|
| **책임** | @멘션 및 DM 이벤트 수신, RAG Chain 호출, Block Kit 포매팅, 응답 전송 |
| **입력** | Slack 이벤트 객체 (메시지 텍스트, 채널 ID, 사용자 ID) |
| **출력** | Slack Block Kit 포맷 메시지 |
| **의존성** | `slack-bolt`, `src/rag/chain.py` |

**주요 인터페이스:**

```python
def register_handlers(app: App):
    """Slack 이벤트 핸들러를 등록한다."""

    @app.event("app_mention")
    def handle_mention(event, say):
        """@봇 멘션 이벤트를 처리한다."""
        question = extract_question(event["text"])
        result = qa_chain.ask(question)
        blocks = format_response(result)
        say(blocks=blocks)

    @app.event("message")
    def handle_dm(event, say):
        """DM 메시지를 처리한다."""
        if event.get("channel_type") == "im":
            question = event["text"]
            result = qa_chain.ask(question)
            blocks = format_response(result)
            say(blocks=blocks)
```

---

## 3. 외부 API 연동 명세

### 3.1 Upstage Document Parse API

PDF 파일을 구조화된 텍스트(Markdown/HTML)로 변환하는 Document AI 서비스이다.

| 항목 | 내용 |
|------|------|
| **엔드포인트** | `POST https://api.upstage.ai/v1/document-digitization` |
| **인증** | `Authorization: Bearer {UPSTAGE_API_KEY}` |
| **Content-Type** | `multipart/form-data` |

**요청 파라미터:**

| 파라미터 | 필수 | 타입 | 설명 |
|---------|------|------|------|
| `document` | O | file | 업로드할 PDF 파일 |
| `model` | O | string | `"document-parse"` (고정) |
| `ocr` | X | string | OCR 모드: `"auto"` (기본), `"force"` |
| `output_formats` | X | list | 출력 형식: `["text", "html", "markdown"]` |
| `base64_encoding` | X | list | Base64 인코딩 대상: `["table"]` 등 |
| `coordinates` | X | bool | 바운딩 박스 좌표 포함 여부 |

**요청 예시:**

```python
import httpx

response = httpx.post(
    "https://api.upstage.ai/v1/document-digitization",
    headers={"Authorization": f"Bearer {api_key}"},
    files={"document": ("report.pdf", open("report.pdf", "rb"), "application/pdf")},
    data={
        "model": "document-parse",
        "ocr": "auto",
        "output_formats": '["markdown"]',
    },
)
```

**응답 형식:**

```json
{
    "api": "1.0",
    "model": "document-parse-250116",
    "content": {
        "markdown": "# 삼성전자 실적 분석\n\n## 투자의견: 매수...",
        "html": "<h1>삼성전자 실적 분석</h1>..."
    },
    "elements": [
        {
            "id": 0,
            "type": "heading",
            "content": { "markdown": "# 삼성전자 실적 분석" },
            "bounding_box": { ... },
            "confidence": 0.98
        },
        {
            "id": 1,
            "type": "table",
            "content": { "markdown": "| 항목 | 금액 |\n|---|---|\n| 매출 | 79조 |" },
            "confidence": 0.95
        }
    ],
    "usage": {
        "pages": 15
    }
}
```

**비용 및 제한:**

| 항목 | 내용 |
|------|------|
| Standard 모드 | $0.01 / 페이지 |
| Enhanced 모드 | $0.03 / 페이지 (복잡한 테이블, 이미지, 차트 포함) |
| Auto 모드 | 페이지별로 Standard/Enhanced 자동 선택 |
| 동기 API 최대 페이지 | 100페이지 |
| 비동기 API 최대 페이지 | 1,000페이지 |
| 서버 타임아웃 | 5분 |
| 처리 시간 (표준 문서) | 약 3초 (200단어 기준) |
| Rate Limit (Tier 1) | 기본 요청 제한 (tier에 따라 상이) |

### 3.2 OpenAI Embedding API

텍스트를 고차원 벡터로 변환하여 의미 기반 검색을 가능하게 한다.

| 항목 | 내용 |
|------|------|
| **엔드포인트** | `POST https://api.openai.com/v1/embeddings` |
| **인증** | `Authorization: Bearer {OPENAI_API_KEY}` |
| **Content-Type** | `application/json` |

**요청 파라미터:**

| 파라미터 | 필수 | 타입 | 설명 |
|---------|------|------|------|
| `input` | O | string \| list | 임베딩할 텍스트 (배열로 최대 2,048개) |
| `model` | O | string | `"text-embedding-3-small"` |
| `dimensions` | X | int | 출력 벡터 차원 수 (기본: 1536) |
| `encoding_format` | X | string | `"float"` (기본) 또는 `"base64"` |

**요청 예시:**

```python
from openai import OpenAI

client = OpenAI(api_key=api_key)
response = client.embeddings.create(
    input=["삼성전자 실적 분석 리포트"],
    model="text-embedding-3-small"
)
embedding = response.data[0].embedding  # 1536차원 벡터
```

**응답 형식:**

```json
{
    "object": "list",
    "data": [
        {
            "object": "embedding",
            "embedding": [0.0023064255, -0.009327292, ...],
            "index": 0
        }
    ],
    "model": "text-embedding-3-small",
    "usage": {
        "prompt_tokens": 12,
        "total_tokens": 12
    }
}
```

**비용 및 제한:**

| 항목 | 내용 |
|------|------|
| 모델 | `text-embedding-3-small` |
| 비용 | $0.02 / 1M tokens |
| Batch API 비용 | $0.01 / 1M tokens (50% 할인) |
| 출력 차원 | 1,536 (기본), `dimensions` 파라미터로 축소 가능 |
| 최대 입력 토큰 | 8,191 tokens |
| 배치 크기 | 요청당 최대 2,048개 텍스트 |

### 3.3 OpenAI Chat Completion API

RAG Chain에서 컨텍스트 기반 답변을 생성하는 데 사용한다.

| 항목 | 내용 |
|------|------|
| **엔드포인트** | `POST https://api.openai.com/v1/chat/completions` |
| **인증** | `Authorization: Bearer {OPENAI_API_KEY}` |
| **Content-Type** | `application/json` |

**요청 파라미터:**

| 파라미터 | 필수 | 타입 | 설명 |
|---------|------|------|------|
| `model` | O | string | `"gpt-4o-mini"` (개발) / `"gpt-4o"` (운영) |
| `messages` | O | list | 대화 메시지 배열 (`role` + `content`) |
| `temperature` | X | float | 생성 다양성 (0.0~2.0, 기본 1.0) |
| `max_tokens` | X | int | 최대 출력 토큰 수 |

**비용:**

| 모델 | Input | Output | 용도 |
|------|-------|--------|------|
| `gpt-4o-mini` | $0.15 / 1M tokens | $0.60 / 1M tokens | 개발 환경 (비용 절약) |
| `gpt-4o` | $2.50 / 1M tokens | $10.00 / 1M tokens | 운영 환경 (품질 확보) |

**참고:** Cached Input은 50% 할인이 적용된다.

### 3.4 Slack Bolt for Python (Socket Mode)

Slack 워크스페이스에서 봇 이벤트를 수신하고 응답을 전송한다.

| 항목 | 내용 |
|------|------|
| **프로토콜** | WebSocket (Socket Mode) |
| **SDK** | `slack-bolt` (Python) |
| **인증 토큰** | Bot Token (`xoxb-`), App Token (`xapp-`) |

**필요한 OAuth Scopes:**

| Scope | 용도 |
|-------|------|
| `app_mentions:read` | @봇 멘션 이벤트 수신 |
| `chat:write` | 메시지 전송 |
| `im:history` | DM 메시지 히스토리 읽기 |
| `im:read` | DM 채널 정보 읽기 |
| `im:write` | DM 메시지 전송 |

**Event Subscriptions:**

| 이벤트 | 설명 |
|--------|------|
| `app_mention` | 봇이 @멘션될 때 발생 |
| `message.im` | 봇에게 DM이 올 때 발생 |

**Socket Mode 설정:**

```python
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# App 초기화 (Bot Token)
app = App(token=os.environ["SLACK_BOT_TOKEN"])

# Socket Mode 핸들러 (App-Level Token: connections:write scope 필요)
handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
handler.start()
```

**Slack Block Kit 응답 형식 예시:**

```python
blocks = [
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "삼성전자의 목표가는 *85,000원* (미래에셋증권, 매수)입니다."
        }
    },
    {"type": "divider"},
    {
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": ":page_facing_up: 출처: 미래에셋증권 | 2026-02-10 | 홍길동 애널리스트"
            }
        ]
    }
]
```

---

## 4. 에러 처리 및 재시도 전략

### 4.1 에러 분류 체계

시스템에서 발생할 수 있는 에러를 **일시적(transient)**과 **영구적(permanent)**으로 분류한다.

| 분류 | 에러 유형 | 재시도 여부 |
|------|----------|-----------|
| 일시적 | API Rate Limit (429) | O - 지수 백오프 |
| 일시적 | 네트워크 타임아웃 | O - 최대 3회 |
| 일시적 | API 서버 에러 (500, 502, 503) | O - 지수 백오프 |
| 영구적 | 인증 실패 (401, 403) | X - 즉시 중단, 로그 |
| 영구적 | 잘못된 요청 (400) | X - 즉시 중단, 로그 |
| 영구적 | PDF 파싱 실패 (손상된 파일) | X - 건너뛰기, 로그 |
| 영구적 | 관련 문서 없음 (검색 결과 0건) | X - 사용자에게 안내 |

### 4.2 시나리오별 에러 처리

#### 4.2.1 Upstage Document Parse API 실패

```python
class DocumentParseError(Exception):
    """Upstage Document Parse API 에러."""
    pass

# 재시도 전략
MAX_RETRIES = 3
RETRY_DELAYS = [2, 4, 8]  # 지수 백오프 (초)

async def parse_with_retry(parser, pdf_path):
    for attempt in range(MAX_RETRIES):
        try:
            return parser.parse(pdf_path)
        except httpx.TimeoutException:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            raise DocumentParseError(f"타임아웃: {pdf_path}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                # Rate limit - Retry-After 헤더 참조
                wait = int(e.response.headers.get("Retry-After", RETRY_DELAYS[attempt]))
                await asyncio.sleep(wait)
                continue
            elif e.response.status_code >= 500:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue
            raise DocumentParseError(f"API 에러 {e.response.status_code}: {pdf_path}")
```

**처리 방안:**

| 에러 상황 | 처리 |
|----------|------|
| 타임아웃 (5분 초과) | 3회 재시도 후 해당 PDF 건너뛰기, 실패 목록에 기록 |
| Rate Limit (429) | `Retry-After` 헤더 기반 대기 후 재시도 |
| 서버 에러 (5xx) | 지수 백오프 재시도 (2초 → 4초 → 8초) |
| 손상된 PDF (400) | 즉시 건너뛰기, 에러 로그에 파일명과 에러 메시지 기록 |
| 인증 실패 (401) | 파이프라인 즉시 중단, API 키 확인 요청 |

#### 4.2.2 OpenAI API 실패

```python
# LangChain의 내장 재시도 메커니즘 활용
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

# max_retries 파라미터로 자동 재시도 설정
llm = ChatOpenAI(
    model="gpt-4o-mini",
    max_retries=3,
    request_timeout=30,
)

embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    max_retries=3,
    request_timeout=30,
)
```

**처리 방안:**

| 에러 상황 | 처리 |
|----------|------|
| Rate Limit (429) | LangChain 내장 자동 재시도 (지수 백오프) |
| 컨텍스트 길이 초과 | 청크 수를 줄여서 재시도 (k 값 감소) |
| 타임아웃 | 3회 재시도 후 사용자에게 에러 메시지 반환 |
| 인증 실패 (401) | 즉시 중단, 관리자에게 알림 |

#### 4.2.3 ChromaDB 에러

| 에러 상황 | 처리 |
|----------|------|
| 디스크 공간 부족 | 파이프라인 중단, 관리자 알림 |
| DB 파일 손상 | 백업에서 복원 안내 (data/chromadb/ 디렉토리) |
| 중복 문서 적재 | `source_file` 기준으로 기존 문서 삭제 후 재적재 |

#### 4.2.4 Slack 이벤트 처리 실패

```python
@app.event("app_mention")
def handle_mention(event, say, logger):
    try:
        question = extract_question(event["text"])
        result = qa_chain.ask(question)
        blocks = format_response(result)
        say(blocks=blocks)
    except NoDocumentsFoundError:
        say("관련 리포트를 찾을 수 없습니다. 질문을 다시 확인해 주세요.")
    except Exception as e:
        logger.error(f"질문 처리 실패: {e}", exc_info=True)
        say("죄송합니다. 답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
```

**처리 방안:**

| 에러 상황 | 사용자에게 표시하는 메시지 |
|----------|------------------------|
| 검색 결과 0건 | "관련 리포트를 찾을 수 없습니다." |
| LLM 응답 생성 실패 | "답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요." |
| 타임아웃 | "응답 시간이 초과되었습니다. 질문을 간단히 바꿔서 다시 시도해 주세요." |

### 4.3 배치 파이프라인 에러 처리 정책

배치 파이프라인은 개별 PDF 실패가 전체 파이프라인을 중단시키지 않도록 설계한다.

```python
def run_pipeline(pdf_paths: list[str]) -> PipelineResult:
    """배치 파이프라인을 실행한다. 개별 PDF 실패 시 건너뛰고 계속 진행."""
    success = []
    failed = []

    for pdf_path in pdf_paths:
        try:
            parsed = parser.parse(pdf_path)
            metadata = extractor.extract(parsed.content, pdf_path)
            chunks = chunker.chunk(parsed.content, metadata)
            embedder.embed_and_store(chunks)
            success.append(pdf_path)
        except Exception as e:
            logger.error(f"파이프라인 실패: {pdf_path} - {e}")
            failed.append({"file": pdf_path, "error": str(e)})

    return PipelineResult(
        total=len(pdf_paths),
        success_count=len(success),
        failed_count=len(failed),
        failed_files=failed
    )
```

---

## 5. 환경별 구성

### 5.1 환경 구분

| 항목 | 개발 (Development) | 운영 (Production) |
|------|-------------------|-------------------|
| LLM 모델 | `gpt-4o-mini` | `gpt-4o` |
| Embedding 모델 | `text-embedding-3-small` | `text-embedding-3-small` |
| Vector DB | ChromaDB (로컬 파일 시스템) | ChromaDB (로컬 파일 시스템) |
| ChromaDB 경로 | `./data/chromadb` | `./data/chromadb` |
| 로그 레벨 | `DEBUG` | `INFO` |
| Upstage 파싱 모드 | `standard` (비용 절약) | `auto` (품질 최적화) |
| 테스트 PDF 수 | 5~10개 | 전체 리포트 |

### 5.2 환경변수 구성

```bash
# .env.example - 공통
UPSTAGE_API_KEY=your_upstage_api_key
OPENAI_API_KEY=your_openai_api_key
SLACK_BOT_TOKEN=xoxb-your-slack-bot-token
SLACK_APP_TOKEN=xapp-your-slack-app-token
SLACK_SIGNING_SECRET=your_signing_secret

# 환경별 설정
ENV=development                    # development | production
LLM_MODEL=gpt-4o-mini             # gpt-4o-mini (개발) | gpt-4o (운영)
CHROMA_PERSIST_DIR=./data/chromadb
LOG_LEVEL=DEBUG                    # DEBUG (개발) | INFO (운영)
UPSTAGE_PARSE_MODE=standard        # standard (개발) | auto (운영)
```

### 5.3 설정 관리 모듈

```python
# src/config.py
import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    """환경별 설정을 관리한다."""

    # 환경 구분
    ENV: str = os.getenv("ENV", "development")

    # API Keys
    UPSTAGE_API_KEY: str = os.environ["UPSTAGE_API_KEY"]
    OPENAI_API_KEY: str = os.environ["OPENAI_API_KEY"]
    SLACK_BOT_TOKEN: str = os.environ["SLACK_BOT_TOKEN"]
    SLACK_APP_TOKEN: str = os.environ["SLACK_APP_TOKEN"]

    # 모델 설정
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    EMBEDDING_MODEL: str = "text-embedding-3-small"

    # ChromaDB
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./data/chromadb")
    CHROMA_COLLECTION_NAME: str = "securities_reports"

    # Upstage
    UPSTAGE_PARSE_MODE: str = os.getenv("UPSTAGE_PARSE_MODE", "standard")

    # 로깅
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "DEBUG")

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"

settings = Settings()
```

### 5.4 비용 추정 (개발 환경 기준)

PDF 10개, 평균 15페이지, 청크당 평균 500 tokens 가정:

| 항목 | 수량 | 단가 | 비용 |
|------|------|------|------|
| Upstage Document Parse (Standard) | 150 페이지 | $0.01/page | $1.50 |
| OpenAI Embedding | ~150 청크 x 500 tokens = 75K tokens | $0.02/1M tokens | ~$0.002 |
| OpenAI Chat (gpt-4o-mini) | 질문 50회 x ~2K tokens | $0.15/1M input + $0.60/1M output | ~$0.05 |
| **개발 환경 총 비용** | | | **~$1.56** |

---

## 6. 참고 자료

- [Upstage Document Parse API 문서](https://console.upstage.ai/docs/capabilities/digitize/document-parsing)
- [Upstage API 가격](https://www.upstage.ai/pricing/api)
- [OpenAI Embeddings API](https://platform.openai.com/docs/api-reference/embeddings)
- [OpenAI API 가격](https://openai.com/api/pricing/)
- [Slack Bolt for Python](https://slack.dev/bolt-python/tutorial/getting-started)
- [LangChain SelfQueryRetriever](https://python.langchain.com/docs/how_to/self_query/)
- [ChromaDB 문서](https://docs.trychroma.com/)
