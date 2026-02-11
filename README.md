# 증권사 리포트 RAG Slack Bot

증권사 애널리스트 리포트(PDF)를 수집/파싱/벡터DB 적재하고, Slack에서 자연어로 질문하면 RAG 기반으로 답변하는 QA 봇입니다.

## 아키텍처

```
PDF 수집 → Upstage Document Parse → Chunking → Embedding → ChromaDB
                                                                │
Slack 메시지 수신 → LangChain Agent (SelfQueryRetriever) ─────────┘
       │
       ▼
  Slack 응답 반환 (출처 포함)
```

## 기술 스택

| 영역 | 기술 |
|------|------|
| PDF 파싱 | Upstage Document Parse API |
| RAG 프레임워크 | LangChain |
| Vector DB | ChromaDB |
| Embedding | OpenAI `text-embedding-3-small` |
| LLM | OpenAI `gpt-4o-mini` / `gpt-4o` |
| Slack 연동 | Slack Bolt for Python |
| 패키지 관리 | uv |
| 코드 품질 | ruff |

## 디렉토리 구조

```
securities-report-rag/
├── pyproject.toml
├── .env.example
├── data/
│   ├── raw_pdfs/          # 원본 PDF
│   ├── parsed/            # 파싱 결과 (Markdown)
│   └── chromadb/          # 벡터DB 저장소
├── src/
│   ├── pipeline/          # 배치 파이프라인
│   │   ├── parser.py      # PDF → Markdown 변환
│   │   ├── chunker.py     # 청킹 (테이블/텍스트 분리)
│   │   ├── embedder.py    # Embedding + ChromaDB 적재
│   │   └── metadata.py    # 메타데이터 추출
│   ├── rag/               # RAG 서빙
│   │   ├── retriever.py   # SelfQueryRetriever
│   │   ├── chain.py       # QA Chain
│   │   └── prompts.py     # 프롬프트 템플릿
│   └── slack/             # Slack 연동
│       ├── app.py         # Bolt app 초기화
│       └── handlers.py    # 메시지 핸들러
├── scripts/               # 실행 스크립트
└── tests/
```

## 시작하기

### 사전 요구사항

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

### 설치

```bash
# 의존성 설치
uv sync

# 환경변수 설정
cp .env.example .env
# .env 파일에 API 키 입력
```

### 필요한 API 키

| 키 | 용도 | 발급처 |
|----|------|--------|
| `UPSTAGE_API_KEY` | PDF 파싱 | [Upstage Console](https://console.upstage.ai/) |
| `OPENAI_API_KEY` | Embedding + LLM | [OpenAI Platform](https://platform.openai.com/) |
| `SLACK_BOT_TOKEN` | Slack Bot | [Slack API](https://api.slack.com/apps) |
| `SLACK_APP_TOKEN` | Slack Socket Mode | [Slack API](https://api.slack.com/apps) |
| `SLACK_SIGNING_SECRET` | 요청 검증 | [Slack API](https://api.slack.com/apps) |

### 실행

```bash
# 파이프라인 실행 (PDF 파싱 → 벡터DB 적재)
uv run python scripts/run_pipeline.py

# Slack 봇 실행
uv run python scripts/run_slack_bot.py
```

### 개발

```bash
# 린트
uv run ruff check src/

# 포맷
uv run ruff format src/

# 테스트
uv run pytest
```

## 개발 단계

- [x] Phase 0: 프로젝트 초기 셋업
- [ ] Phase 1: PDF 파싱 + 청킹 실험
- [ ] Phase 2: Vector DB + 로컬 RAG QA
- [ ] Phase 3: Slack 연동
- [ ] Phase 4: 파이프라인 자동화 + 고도화
