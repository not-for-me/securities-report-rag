# API 연동 강화 + 증분 재처리/롤백 고도화 구현 문서

## 1. 문서 목적
본 문서는 2차 구현 작업의 범위를 정리한다.

1. Upstage/OpenAI/Slack 실사용 호출 경로 강화
2. `metadata.json` 기반 증분 재처리 로직 고도화(rollback 포함)

---

## 2. 변경 파일

- `/Users/woojinjoe/Dev/securities-report-rag/src/pipeline/parser.py`
- `/Users/woojinjoe/Dev/securities-report-rag/src/config.py`
- `/Users/woojinjoe/Dev/securities-report-rag/.env.example`
- `/Users/woojinjoe/Dev/securities-report-rag/src/pipeline/registry.py`
- `/Users/woojinjoe/Dev/securities-report-rag/src/pipeline/embedder.py`
- `/Users/woojinjoe/Dev/securities-report-rag/src/pipeline/runner.py`
- `/Users/woojinjoe/Dev/securities-report-rag/src/rag/retriever.py`
- `/Users/woojinjoe/Dev/securities-report-rag/src/rag/chain.py`
- `/Users/woojinjoe/Dev/securities-report-rag/tests/test_registry_runner.py`

---

## 3. Upstage/OpenAI/Slack 호출 강화 내용

### 3.1 Upstage 파서 재시도/백오프
파일: `/Users/woojinjoe/Dev/securities-report-rag/src/pipeline/parser.py`

- `DocumentParser`에 재시도 설정 추가
  - `max_retries`
  - `base_retry_delay_seconds`
- 일시적 장애 상태코드(`429`, `500`, `502`, `503`, `504`)에 대해 지수 백오프 재시도
- `429` 응답 시 `Retry-After` 헤더 우선 반영
- 네트워크 요청 오류/타임아웃에 대해 재시도
- `UPSTAGE_API_KEY` 누락 시 조기 실패(`DocumentParseError`) 처리

### 3.2 RAG 검색/응답 안정화
파일: `/Users/woojinjoe/Dev/securities-report-rag/src/rag/retriever.py`, `/Users/woojinjoe/Dev/securities-report-rag/src/rag/chain.py`

- `SelfQueryRetriever`에 `search_type="similarity_score_threshold"` 명시
- QA 체인 LLM 호출 실패 시 사용자 친화적 fallback 응답 추가
- 예외를 로깅하고 시스템 오류 메시지를 안전하게 반환

### 3.3 환경 설정 확장
파일: `/Users/woojinjoe/Dev/securities-report-rag/src/config.py`, `/Users/woojinjoe/Dev/securities-report-rag/.env.example`

추가된 설정:
- `UPSTAGE_TIMEOUT_SECONDS`
- `UPSTAGE_MAX_RETRIES`
- `UPSTAGE_RETRY_BASE_DELAY_SECONDS`

`runner` 빌더에서 위 설정을 `DocumentParser`에 주입하도록 연결했다.

---

## 4. metadata.json 기반 증분 처리 고도화

### 4.1 증분 대상 판별 계획(Plan) 도입
파일: `/Users/woojinjoe/Dev/securities-report-rag/src/pipeline/registry.py`

- `DocumentProcessingPlan` 추가
  - `pdf_path`, `document_id`, `file_hash`, `reason`, `previous_status`
- 처리 사유(`reason`) 자동 판별
  - `new`
  - `hash_changed`
  - `status_<current_status>`
  - `up_to_date`
- `plan_documents_to_process()`로 실제 처리 대상만 반환

### 4.2 해시 변경 기반 재처리 플래그
파일: `/Users/woojinjoe/Dev/securities-report-rag/src/pipeline/registry.py`

- `register_source_file()`가 해시 변경 감지 시 상태를 `pending`으로 전환
- `reprocess_reason` 필드 기록
- indexed 완료 시 `mark_indexed()`로 상태 정리
  - `status=indexed`
  - `indexed_file_hash` 기록
  - 오류/재처리 이유 필드 정리

---

## 5. rollback 고도화

### 5.1 Registry rollback
파일: `/Users/woojinjoe/Dev/securities-report-rag/src/pipeline/registry.py`

- 문서 처리 전 snapshot 보관: `get_document_snapshot()`
- 실패 시 snapshot 복원: `rollback_document()`
- 복원 이력에 `rolled_back` 플래그 남김
- 복원이 불가능한 경우 `mark_failed()`로 실패 상태 명시

### 5.2 Vector rollback
파일: `/Users/woojinjoe/Dev/securities-report-rag/src/pipeline/embedder.py`

- 벡터 스냅샷 모델 `VectorSnapshot` 추가
- 기존 벡터 snapshot: `snapshot_document(document_id)`
- 인덱싱 교체 방식: `replace_document(document_id, documents)`
- 실패 시 벡터 복구: `restore_snapshot(document_id, snapshot)`

### 5.3 Runner 오케스트레이션 반영
파일: `/Users/woojinjoe/Dev/securities-report-rag/src/pipeline/runner.py`

- 실행 단위를 `Path` 목록이 아닌 `DocumentProcessingPlan` 기반으로 처리
- 인덱싱 전 벡터 snapshot 생성
- 인덱싱 실패 시:
  - 벡터 snapshot 복원
  - registry snapshot 복원(기존 indexed 상태 우선)
  - history에 rollback 여부 기록

---

## 6. 테스트 추가
파일: `/Users/woojinjoe/Dev/securities-report-rag/tests/test_registry_runner.py`

추가 시나리오:
1. 해시 변경 감지 시 재처리 계획이 `hash_changed`로 잡히는지 검증
2. registry rollback 시 이전 indexed 상태가 복원되는지 검증
3. runner 인덱싱 실패 시 vector/registry rollback이 모두 실행되는지 검증

---

## 7. 검증 결과

- `ruff check` 통과
- `pytest` 통과 (`8 passed`)

---

## 8. 운영 전 체크포인트

1. 실제 API 키 주입 후 `scripts/run_pipeline.py` 스모크 테스트
2. Slack 앱 이벤트 권한/토큰 설정 확인
3. 장애 상황(429/5xx/timeout)에서 재시도 로그 및 rollback 기록 확인
4. `data/metadata.json`의 history/last_error 필드 모니터링 정책 확정

