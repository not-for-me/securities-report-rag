# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

증권사 애널리스트 리포트(PDF)를 Upstage Document Parse API로 파싱 → 청킹 → ChromaDB에 적재하고, Slack에서 자연어 질문에 RAG 기반으로 답변하는 QA 봇. 한국어 금융 도메인 프로젝트.

## Commands

```bash
uv sync                          # 의존성 설치
uv run ruff check src/           # 린트
uv run ruff format src/          # 포맷
uv run pytest                    # 전체 테스트
uv run pytest tests/test_parser.py -k "test_name"  # 단일 테스트
uv run python scripts/run_pipeline.py   # 파이프라인 실행
uv run python scripts/run_slack_bot.py  # Slack 봇 실행
```

## Architecture

두 개의 독립된 흐름으로 나뉨:

1. **Pipeline (배치)**: `src/pipeline/` — PDF 수집 → Upstage API 파싱(parser) → 청킹(chunker) → 메타데이터 추출(metadata) → Embedding + ChromaDB 적재(embedder)
2. **Serving (실시간)**: `src/rag/` + `src/slack/` — Slack 메시지 수신 → LangChain SelfQueryRetriever로 메타데이터 필터링 + 벡터 검색 → QA Chain 답변 생성 → Slack 응답

## Key Design Decisions

- **청킹 전략**: 테이블은 절대 분할하지 않고 하나의 청크로 유지. 테이블 직전/직후 설명 문단을 같은 청크에 포함하여 컨텍스트 보존.
- **메타데이터 스키마**: `ticker`, `company_name`, `date`, `broker`, `analyst`, `report_type`, `target_price`, `rating`, `source_file` — SelfQueryRetriever가 자연어에서 자동으로 메타데이터 필터를 추출함.
- **환경변수**: `.env` 파일로 관리 (`python-dotenv`). `.env.example` 참조.

## Code Style

- Python 3.11+, ruff (line-length=120, double quotes)
- ruff lint rules: E, F, I, N, W, UP
