# 스켈레톤 코드 구현 문서

## 1. 문서 목적
이 문서는 아래 설계 문서를 기준으로 현재 코드베이스에 반영된 **초기 스켈레톤 구현**을 정리한다.

- `docs/design/01_system_architecture.md`
- `docs/design/02_rag_pipeline.md`
- `docs/design/03_data_model.md`
- `docs/design/04_security.md`

현재 구현은 "구조 고정 + 인터페이스 정의 + 최소 동작" 단계이며, 실제 운영 수준의 정교한 로직(정확한 파싱 품질 최적화, 고급 오류 복구, 성능 튜닝)은 후속 단계에서 확장한다.

---

## 2. 구현 범위 요약

### 2.1 아키텍처 매핑
설계서의 배치/서빙 분리를 그대로 반영했다.

- 배치 파이프라인: `src/pipeline/*`
  - PDF 파싱 → 메타데이터 추출 → 청킹 → 임베딩/적재 → 상태 레지스트리 관리
- 실시간 서빙(RAG): `src/rag/*`
  - Retriever(SelfQuery 기반) → QA Chain
- Slack 인터페이스: `src/slack/*`
  - 이벤트 수신(app mention/DM) → RAG 질의 → Block Kit 응답
- 공통 기반: `src/config.py`, `src/models.py`, `src/security.py`, `src/logging_utils.py`

### 2.2 실행 스크립트
- 파이프라인 실행: `scripts/run_pipeline.py`
- Slack 봇 실행: `scripts/run_slack_bot.py`

---

## 3. 디렉터리 구조(현재)

```text
src/
├── config.py
├── logging_utils.py
├── models.py
├── security.py
├── pipeline/
│   ├── parser.py
│   ├── metadata.py
│   ├── chunker.py
│   ├── embedder.py
│   ├── registry.py
│   └── runner.py
├── rag/
│   ├── prompts.py
│   ├── retriever.py
│   └── chain.py
└── slack/
    ├── app.py
    └── handlers.py
```

---

## 4. 모듈별 구현 내용

### 4.1 공통 레이어

### `src/config.py`
- `.env` 기반 설정 로딩
- 개발/운영 공통 설정 필드 정의
- 파이프라인/Slack 실행 전 필수 환경변수 검증 함수 제공

### `src/models.py`
- 핵심 데이터 구조 정의
  - `ParseResult`
  - `ReportMetadata`
  - `QAResult`
  - `PipelineResult`

### `src/security.py`
- 보안/입력 검증 기본 유틸
  - `SecretFilter` (로그 시크릿 마스킹)
  - `RateLimiter` (사용자 단위 요청 제한)
  - `validate_query`, `validate_pdf`

### `src/logging_utils.py`
- 로깅 포맷/레벨 설정
- 모든 핸들러에 `SecretFilter` 적용

---

### 4.2 배치 파이프라인 (`src/pipeline`)

### `parser.py` (`DocumentParser`)
- Upstage Document Parse API 호출 골격
- PDF 유효성 검증 후 Markdown 파싱 결과를 `ParseResult`로 반환
- 배치 파싱(`parse_batch`) 인터페이스 제공

### `metadata.py` (`MetadataExtractor`)
- 정규식 + 파일명 fallback 기반 메타데이터 추출
- 추출 대상: 종목코드/종목명/날짜/증권사/애널리스트/목표가/투자의견/리포트 타입

### `chunker.py` (`ReportChunker`)
- Markdown 헤더 기반 1차 분할 + 재귀 분할 2차 분할 구조
- 테이블 청크 감지 및 `chunk_type` 태깅
- 디스클레이머 패턴 필터링
- LangChain splitter 미설치 환경을 위한 fallback splitter 포함

### `embedder.py` (`ReportEmbedder`)
- OpenAI embedding + Chroma 저장소 연결
- 결정론적 청크 ID 생성 규칙 반영
  - `{document_id}::chunk_{chunk_index}`
- `embed_and_store`, `delete_document`, `get_vectorstore` 제공

### `registry.py` (`MetadataRegistry`)
- `data/metadata.json` 레지스트리 로드/저장
- 스키마 버전 검증
- 파일 해시(SHA-256) 기반 중복 감지
- 문서 상태/이력 업데이트 관리

### `runner.py` (`PipelineRunner`)
- 파이프라인 단계 오케스트레이션
  - register → parse(cache) → metadata → chunk → index
- 단계별 상태 전이/이력 기록
- 실패 시 단계별 정리 로직(파싱 캐시 삭제, indexing 실패 시 벡터 삭제)
- 기본 조합 빌더 `build_default_pipeline_runner` 제공

---

### 4.3 RAG 레이어 (`src/rag`)

### `prompts.py`
- 시스템/사용자 프롬프트 템플릿 관리
- 한국어 답변 + 출처 기반 응답 규칙 반영

### `retriever.py` (`ReportRetriever`)
- `SelfQueryRetriever` 기반 검색 골격
- 메타데이터 필드 스키마 정의
- 결과 없음 시 유사도 검색 fallback

### `chain.py` (`ReportQAChain`)
- 검색 결과를 컨텍스트 문자열로 변환
- LLM 호출로 답변 생성
- 출처 메타데이터 추출 및 `QAResult` 반환

---

### 4.4 Slack 레이어 (`src/slack`)

### `handlers.py`
- `app_mention`, DM 이벤트 핸들링
- 멘션 정리, 요청 길이 검증, 사용자 rate limit 적용
- RAG 체인 결과를 Block Kit 형태로 포맷해 응답

### `app.py`
- Slack Bolt 앱 생성
- Chroma + Retriever + QA Chain 조립
- Socket Mode 실행 엔트리포인트 제공 (`main`)

---

## 5. 데이터/보안 설계 반영 포인트

- 데이터 모델 설계 반영
  - `data/metadata.json` 상태 추적 스키마
  - 문서 해시 기반 중복 감지
  - 결정론적 chunk ID 전략
- 보안 설계 반영
  - 환경변수 fail-fast 검증
  - 시크릿 마스킹 로그 필터
  - PDF 매직바이트/크기 검증
  - Slack 입력 검증 및 rate limit

---

## 6. 현재 한계와 다음 구현 항목

현재 코드는 스켈레톤 단계이므로 아래 항목은 후속 구현이 필요하다.

1. Upstage 응답 포맷 다양성/예외 케이스에 대한 파서 강화
2. 테이블 전후 문단 결합 규칙 정교화
3. SelfQuery 필터 품질 검증 및 검색 파라미터 튜닝
4. Slack 응답 포맷(출처/요약/비교) 고도화
5. 배치 파이프라인 병렬 처리, 재시도, 비용 모니터링

---

## 7. 검증 상태

초기 뼈대 구현 후 아래 기본 검증을 통과했다.

- `ruff check` 통과
- `pytest` 통과
