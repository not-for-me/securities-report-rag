# 증권사 리포트 RAG Slack Bot - Project Handoff

> Claude Code에서 이어서 개발을 시작하기 위한 프로젝트 컨텍스트 문서
> 작성일: 2026.02.11

---

## 1. 프로젝트 개요

### 목적
증권사 애널리스트 리포트(PDF)를 수집 → 파싱 → 벡터DB에 적재하고, Slack에서 자연어로 질문하면 RAG 기반으로 답변하는 QA 봇을 구축한다.

### 배경
- 개발자: 조우진 (Backend 11년 + Data Engineering 경험, AI Engineering 전환 중)
- 학습 목적: LangChain RAG 파이프라인 end-to-end 경험, Document AI 역량 확장
- 커리어 맥락: Upstage AI Data Manager 포지션 타겟 → Upstage Document Parse API를 직접 활용하여 제품 이해도 확보

---

## 2. 아키텍처

```
┌─────────────────────────────────────────────────┐
│                 PIPELINE (배치/비동기)              │
│                                                   │
│  PDF 수집 (로컬 디렉토리 or 크롤러)                  │
│       │                                           │
│       ▼                                           │
│  Upstage Document Parse API                       │
│  (PDF → Markdown/HTML)                            │
│       │                                           │
│       ▼                                           │
│  Chunking (테이블/텍스트 분리 전략)                   │
│       │                                           │
│       ▼                                           │
│  Embedding → Vector DB 적재                        │
│  (메타데이터: 종목, 날짜, 증권사, 애널리스트)           │
│                                                   │
└──────────────────────┬──────────────────────────┘
                       │ Vector DB (ChromaDB)
┌──────────────────────┴──────────────────────────┐
│                 SERVING (실시간)                    │
│                                                   │
│  Slack 메시지 수신 (Bolt for Python)                │
│       │                                           │
│       ▼                                           │
│  LangChain Agent                                  │
│  (SelfQueryRetriever + ConversationalRetrievalQA) │
│       │                                           │
│       ▼                                           │
│  Slack 응답 반환                                    │
│                                                   │
└─────────────────────────────────────────────────┘
```

---

## 3. 기술 스택

| 영역 | 선택 | 사유 |
|------|------|------|
| PDF 파싱 | Upstage Document Parse API | 테이블 구조 인식 품질 우수, 타겟 회사 제품 경험 |
| RAG 프레임워크 | LangChain | RAG 파이프라인 학습 목적, 생태계 풍부 |
| Vector DB | ChromaDB | 로컬 개발 편의, 설치 간편, LangChain 통합 우수 |
| Embedding | OpenAI `text-embedding-3-small` | 비용 효율적, 한국어 성능 양호 |
| LLM | OpenAI `gpt-4o-mini` (개발) → `gpt-4o` (운영) | 개발 시 비용 절약, 운영 시 품질 확보 |
| Slack 연동 | Slack Bolt for Python | 공식 SDK, 비동기 지원 |
| 패키지 관리 | uv | 빠른 의존성 관리 |
| 코드 품질 | ruff | 린터/포매터 |

---

## 4. 디렉토리 구조 (제안)

```
securities-report-rag/
├── README.md
├── pyproject.toml
├── .env.example              # API 키 템플릿
├── .gitignore
│
├── data/
│   ├── raw_pdfs/             # 원본 PDF 저장
│   ├── parsed/               # Upstage API 파싱 결과 (markdown/html)
│   └── metadata.json         # PDF별 메타데이터 (종목, 날짜, 증권사 등)
│
├── src/
│   ├── __init__.py
│   │
│   ├── pipeline/             # 배치 파이프라인
│   │   ├── __init__.py
│   │   ├── parser.py         # Upstage API 호출, PDF → Markdown 변환
│   │   ├── chunker.py        # 청킹 전략 (테이블/텍스트 분리)
│   │   ├── embedder.py       # Embedding + ChromaDB 적재
│   │   └── metadata.py       # 메타데이터 추출/관리
│   │
│   ├── rag/                  # RAG 서빙
│   │   ├── __init__.py
│   │   ├── retriever.py      # SelfQueryRetriever 구성
│   │   ├── chain.py          # LangChain QA Chain 구성
│   │   └── prompts.py        # 시스템 프롬프트, 템플릿
│   │
│   └── slack/                # Slack 연동
│       ├── __init__.py
│       ├── app.py            # Bolt app 초기화, 이벤트 핸들러
│       └── handlers.py       # 메시지/커맨드 핸들러
│
├── scripts/
│   ├── run_pipeline.py       # 파이프라인 실행 스크립트
│   └── run_slack_bot.py      # Slack 봇 실행 스크립트
│
└── tests/
    ├── test_parser.py
    ├── test_chunker.py
    └── test_retriever.py
```

---

## 5. 개발 단계 (Phase)

### Phase 1: 파싱 + 청킹 실험 (1~2일)
- [ ] 프로젝트 초기화 (pyproject.toml, .env, 기본 구조)
- [ ] Upstage Document Parse API 연동 (`src/pipeline/parser.py`)
  - API docs: https://console.upstage.ai/docs/capabilities/digitize/document-parsing
  - PDF 5~10개로 파싱 결과 확인
  - Markdown vs HTML 출력 비교 → 청킹에 유리한 포맷 선택
- [ ] 청킹 전략 수립 및 구현 (`src/pipeline/chunker.py`)
  - 핵심 과제: 테이블은 통째로 보존, 앞뒤 설명 문단과 묶기
  - LangChain MarkdownHeaderTextSplitter 또는 커스텀 splitter 검토
- [ ] 메타데이터 추출 로직 (`src/pipeline/metadata.py`)
  - PDF 파일명 또는 파싱 결과에서 종목명, 날짜, 증권사, 애널리스트, 목표가 추출

### Phase 2: Vector DB + 로컬 RAG QA (2~3일)
- [ ] ChromaDB 적재 (`src/pipeline/embedder.py`)
  - 청크 + 메타데이터 함께 저장
  - 메타데이터 스키마: `{ticker, company_name, date, broker, analyst, report_type}`
- [ ] LangChain Retriever 구성 (`src/rag/retriever.py`)
  - SelfQueryRetriever: 자연어 질문에서 메타데이터 필터 자동 추출
  - 예: "삼성전자 최신 리포트 목표가" → ticker=삼성전자, sort=date desc
- [ ] QA Chain 구성 (`src/rag/chain.py`)
  - ConversationalRetrievalQA 또는 RetrievalQA
  - 답변에 출처(증권사, 날짜, 페이지) 포함하도록 프롬프트 설계
- [ ] 로컬에서 CLI로 질문-답변 테스트
  - 품질 확인 질문 예시:
    - "삼성전자 목표가 컨센서스 알려줘"
    - "SK하이닉스 최근 실적 요약해줘"
    - "반도체 업종 전망 리포트 중 가장 긍정적인 의견은?"

### Phase 3: Slack 연동 (반나절)
- [ ] Slack App 생성 및 Bot Token 발급
  - Slack API: https://api.slack.com/apps
  - 필요 권한: `chat:write`, `app_mentions:read`, `im:history`, `im:read`, `im:write`
- [ ] Bolt for Python 앱 구성 (`src/slack/app.py`)
  - DM 또는 멘션으로 질문 수신
  - RAG chain 호출 → 답변 반환
- [ ] UX 개선
  - 답변 생성 중 "typing..." 표시
  - 출처 정보를 Slack Block Kit으로 포매팅
  - 에러 핸들링 (API 실패, 관련 문서 없음 등)

### Phase 4: 파이프라인 자동화 + 고도화 (선택)
- [ ] 증분 업데이트: 신규 PDF만 파싱/적재
- [ ] 파싱 결과 캐싱 (동일 PDF 재파싱 방지)
- [ ] 평가 프레임워크: 질문-정답 쌍 기반 retrieval 정확도 측정
- [ ] Airflow DAG로 파이프라인 스케줄링 (기존 역량 활용)

---

## 6. 핵심 설계 결정 사항

### 6.1 청킹 전략
증권사 리포트의 구조적 특성을 고려한 청킹이 필요하다:

```
[리포트 구조 예시]
─ 투자의견/목표가 요약 (테이블)
─ 본문: 실적 분석, 업황 전망 (텍스트)
─ 실적 추정 테이블 (테이블)
─ 밸류에이션 테이블 (테이블)
─ 디스클레이머 (무시 가능)
```

**원칙:**
- 테이블은 절대 분할하지 않고 하나의 청크로 유지
- 테이블 직전/직후 설명 문단을 같은 청크에 포함하여 컨텍스트 보존
- 본문 텍스트는 의미 단위(섹션/소제목 기준)로 분할
- 각 청크에 리포트 레벨 메타데이터 부착

### 6.2 메타데이터 스키마
```python
{
    "ticker": "005930",           # 종목코드
    "company_name": "삼성전자",     # 종목명
    "date": "2026-02-10",         # 리포트 발행일
    "broker": "미래에셋증권",       # 증권사
    "analyst": "홍길동",           # 애널리스트
    "report_type": "실적분석",     # 리포트 유형
    "target_price": 85000,        # 목표가 (있으면)
    "rating": "매수",             # 투자의견 (있으면)
    "source_file": "mirae_samsung_20260210.pdf"
}
```

### 6.3 SelfQueryRetriever 메타데이터 필터
```python
metadata_field_info = [
    AttributeInfo(name="company_name", description="종목명 (예: 삼성전자, SK하이닉스)", type="string"),
    AttributeInfo(name="date", description="리포트 발행일 (YYYY-MM-DD)", type="string"),
    AttributeInfo(name="broker", description="증권사명 (예: 미래에셋증권, 한국투자증권)", type="string"),
    AttributeInfo(name="report_type", description="리포트 유형 (실적분석, 기업분석, 업종분석)", type="string"),
]
```

---

## 7. API 키 및 환경변수

```bash
# .env.example
UPSTAGE_API_KEY=your_upstage_api_key
OPENAI_API_KEY=your_openai_api_key
SLACK_BOT_TOKEN=xoxb-your-slack-bot-token
SLACK_APP_TOKEN=xapp-your-slack-app-token
SLACK_SIGNING_SECRET=your_signing_secret

# 선택
CHROMA_PERSIST_DIR=./data/chromadb
```

---

## 8. 참고 리소스

- **Upstage Document Parse API**: https://console.upstage.ai/docs/capabilities/digitize/document-parsing
- **LangChain RAG Tutorial**: https://python.langchain.com/docs/tutorials/rag/
- **LangChain SelfQueryRetriever**: https://python.langchain.com/docs/how_to/self_query/
- **Slack Bolt for Python**: https://slack.dev/bolt-python/tutorial/getting-started
- **ChromaDB**: https://docs.trychroma.com/

---

## 9. Claude Code에게 전달할 첫 번째 작업

> Phase 1부터 시작합니다. 아래 순서로 진행해주세요:
>
> 1. `pyproject.toml` 생성 (의존성: langchain, langchain-openai, langchain-chroma, chromadb, slack-bolt, httpx, python-dotenv, ruff)
> 2. 디렉토리 구조 생성
> 3. `.env.example` 생성
> 4. `src/pipeline/parser.py` 구현 — Upstage Document Parse API를 호출하여 PDF를 Markdown으로 변환하는 함수
> 5. 테스트용 스크립트 작성 — PDF 1개를 파싱하고 결과를 `data/parsed/`에 저장

---

## 10. 품질 확인 체크리스트

프로젝트 완료 시 아래 시나리오가 동작해야 한다:

- [ ] Slack DM으로 "삼성전자 목표가 알려줘" → 최신 리포트 기반 답변 + 출처
- [ ] "SK하이닉스 실적 테이블 보여줘" → 테이블 데이터 포함 답변
- [ ] "반도체 업종 전망 요약" → 여러 리포트 종합 답변
- [ ] 새 PDF 추가 후 파이프라인 실행 → 즉시 검색 가능
- [ ] 관련 없는 질문 → "관련 리포트를 찾을 수 없습니다" 응답
