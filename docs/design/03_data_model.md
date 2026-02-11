# 03. 데이터 모델 설계

> 증권사 리포트 RAG 시스템의 데이터 구조, 저장소, 정합성 관리 방안을 정의한다.

---

## 1. 데이터 라이프사이클

증권사 리포트는 PDF 원본에서 최종 벡터 임베딩까지 4단계를 거쳐 변환된다.

```
[Stage 1]          [Stage 2]              [Stage 3]          [Stage 4]
PDF 원본    →    파싱 결과(Markdown)   →     청크           →   벡터 임베딩
                + 메타데이터(JSON)       + 메타데이터          + 메타데이터

저장 위치:        저장 위치:               저장 위치:          저장 위치:
data/raw_pdfs/   data/parsed/            (메모리 내 중간      data/chromadb/
                  ├── {id}.md             데이터, 별도        (ChromaDB
                  └── {id}.meta.json      파일 저장 없음)      persistent store)
```

### 1.1 Stage 1: PDF 원본 수집

- 증권사 리포트 PDF를 `data/raw_pdfs/`에 저장
- 파일명 컨벤션에 따라 네이밍
- SHA-256 해시를 계산하여 중복 감지에 활용

### 1.2 Stage 2: 파싱 (PDF → Markdown + 메타데이터)

- Upstage Document Parse API를 호출하여 PDF를 Markdown으로 변환
- 파싱 결과물: Markdown 파일 + 메타데이터 JSON
- 파싱 성공/실패 상태를 `data/metadata.json`에 기록

### 1.3 Stage 3: 청킹 (Markdown → Chunks)

- Markdown을 의미 단위로 분할 (테이블 보존, 섹션 기반 분할)
- 각 청크에 리포트 레벨 메타데이터 부착
- 메모리 내 중간 데이터로 처리, 별도 파일로 저장하지 않음
- 파이프라인이 중단되더라도 Stage 2 결과물이 캐시되어 있으므로 재처리 비용 최소화

### 1.4 Stage 4: 벡터 임베딩 → ChromaDB 적재

- OpenAI `text-embedding-3-small`로 임베딩 생성 (1536차원)
- ChromaDB에 document + embedding + metadata 저장
- 적재 완료 후 `data/metadata.json`에 상태 업데이트

---

## 2. 파일 저장소 설계

### 2.1 디렉토리 구조

```
data/
├── raw_pdfs/                    # 원본 PDF 파일
│   └── {broker}_{company}_{date}.pdf
├── parsed/                      # 파싱 결과물
│   ├── {document_id}.md         # Markdown 변환 결과
│   └── {document_id}.meta.json  # 문서별 메타데이터
├── metadata.json                # 전체 문서 관리 레지스트리
└── chromadb/                    # ChromaDB persistent storage
    └── (ChromaDB 내부 파일)
```

### 2.2 PDF 파일 네이밍 컨벤션

**형식**: `{broker}_{company}_{date}.pdf`

| 필드 | 규칙 | 예시 |
|------|------|------|
| `broker` | 증권사 영문 약칭 (소문자, 공백 없음) | `mirae`, `koreainvest`, `samsung` |
| `company` | 종목 영문 약칭 (소문자, 공백 없음) | `samsung_elec`, `sk_hynix` |
| `date` | 발행일 (YYYYMMDD) | `20260210` |

**예시**:
- `mirae_samsung_elec_20260210.pdf`
- `koreainvest_sk_hynix_20260205.pdf`
- `samsung_naver_20260201.pdf`

**동일 조합 중복 시**: 파일명 끝에 `_v2`, `_v3` 등 접미사 부여
- `mirae_samsung_elec_20260210_v2.pdf`

### 2.3 파싱 결과물 구조

파싱 결과는 `document_id`를 기준으로 관리한다. `document_id`는 PDF 파일명에서 `.pdf` 확장자를 제거한 값이다.

**Markdown 파일** (`{document_id}.md`):
- Upstage Document Parse API의 Markdown 출력 결과 그대로 저장
- 테이블은 Markdown table 형식으로 보존

**메타데이터 JSON** (`{document_id}.meta.json`):
```json
{
  "document_id": "mirae_samsung_elec_20260210",
  "source_file": "mirae_samsung_elec_20260210.pdf",
  "file_hash": "sha256:a1b2c3d4e5f6...",
  "ticker": "005930",
  "company_name": "삼성전자",
  "date": "2026-02-10",
  "broker": "미래에셋증권",
  "analyst": "홍길동",
  "report_type": "실적분석",
  "target_price": 85000,
  "rating": "매수",
  "parsed_at": "2026-02-11T10:30:00+09:00",
  "parse_format": "markdown",
  "page_count": 12
}
```

### 2.4 파일 관리 정책

| 항목 | 정책 | 사유 |
|------|------|------|
| PDF 원본 보관 | 무기한 보관 | 재파싱 필요 시 원본 필요, 저장 용량 부담 낮음 |
| 파싱 결과 보관 | 무기한 보관 (캐시 역할) | 동일 PDF 재파싱 방지, API 비용 절감 |
| ChromaDB 데이터 | 영구 보관 (persist mode) | 벡터 검색 서비스에 직접 사용 |
| 정리 주기 | 수동 관리 | 학습 프로젝트 특성상 자동 정리 불필요 |

**gitignore 정책**: `data/` 디렉토리 전체를 `.gitignore`에 포함하여 저장소에 커밋하지 않는다.

---

## 3. 메타데이터 스키마

### 3.1 전체 문서 관리 레지스트리 (`data/metadata.json`)

모든 PDF 문서의 처리 상태를 추적하는 중앙 레지스트리 파일이다.

```json
{
  "schema_version": "1.0.0",
  "last_updated": "2026-02-11T10:30:00+09:00",
  "documents": {
    "mirae_samsung_elec_20260210": {
      "source_file": "mirae_samsung_elec_20260210.pdf",
      "file_hash": "sha256:a1b2c3d4e5f6...",
      "file_size_bytes": 2048576,
      "added_at": "2026-02-11T09:00:00+09:00",
      "status": "indexed",
      "pipeline_history": [
        {
          "stage": "parsed",
          "timestamp": "2026-02-11T09:05:00+09:00",
          "success": true
        },
        {
          "stage": "chunked",
          "timestamp": "2026-02-11T09:05:30+09:00",
          "success": true,
          "chunk_count": 15
        },
        {
          "stage": "indexed",
          "timestamp": "2026-02-11T09:06:00+09:00",
          "success": true,
          "vector_count": 15
        }
      ],
      "metadata": {
        "ticker": "005930",
        "company_name": "삼성전자",
        "date": "2026-02-10",
        "broker": "미래에셋증권",
        "analyst": "홍길동",
        "report_type": "실적분석",
        "target_price": 85000,
        "rating": "매수"
      }
    }
  }
}
```

### 3.2 필드 상세 정의

#### 3.2.1 레지스트리 최상위 필드

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `schema_version` | `string` | Y | 스키마 버전 (SemVer) |
| `last_updated` | `string` (ISO 8601) | Y | 마지막 업데이트 시간 |
| `documents` | `object` | Y | document_id → 문서 정보 매핑 |

#### 3.2.2 문서 항목 필드

| 필드 | 타입 | 필수 | 설명 | 유효값 |
|------|------|------|------|--------|
| `source_file` | `string` | Y | 원본 PDF 파일명 | - |
| `file_hash` | `string` | Y | `sha256:{hex}` 형식의 파일 해시 | - |
| `file_size_bytes` | `integer` | Y | 파일 크기 (bytes) | > 0 |
| `added_at` | `string` (ISO 8601) | Y | 레지스트리에 추가된 시간 | - |
| `status` | `string` | Y | 현재 처리 상태 | `pending`, `parsing`, `parsed`, `chunking`, `chunked`, `indexing`, `indexed`, `failed` |
| `pipeline_history` | `array` | Y | 파이프라인 각 단계 실행 이력 | - |
| `metadata` | `object` | Y | 리포트 메타데이터 | 아래 참조 |

#### 3.2.3 리포트 메타데이터 필드

| 필드 | 타입 | 필수 | 설명 | 유효값 / 예시 |
|------|------|------|------|---------------|
| `ticker` | `string` | Y | 종목코드 (6자리) | `"005930"` |
| `company_name` | `string` | Y | 종목명 | `"삼성전자"` |
| `date` | `string` | Y | 리포트 발행일 | `"2026-02-10"` (YYYY-MM-DD) |
| `broker` | `string` | Y | 증권사명 | `"미래에셋증권"` |
| `analyst` | `string` | N | 애널리스트명 | `"홍길동"` |
| `report_type` | `string` | Y | 리포트 유형 | `"실적분석"`, `"기업분석"`, `"업종분석"`, `"기타"` |
| `target_price` | `integer \| null` | N | 목표가 (원) | `85000`, `null` |
| `rating` | `string \| null` | N | 투자의견 | `"매수"`, `"중립"`, `"매도"`, `null` |

#### 3.2.4 파이프라인 이력 항목 필드

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `stage` | `string` | Y | 파이프라인 단계 (`parsed`, `chunked`, `indexed`) |
| `timestamp` | `string` (ISO 8601) | Y | 실행 시간 |
| `success` | `boolean` | Y | 성공 여부 |
| `error_message` | `string` | N | 실패 시 에러 메시지 |
| `chunk_count` | `integer` | N | `chunked` 단계에서 생성된 청크 수 |
| `vector_count` | `integer` | N | `indexed` 단계에서 적재된 벡터 수 |

### 3.3 스키마 버전 관리

| 버전 | 변경 유형 | 대응 방안 |
|------|-----------|-----------|
| `1.0.x` (patch) | 선택 필드 추가, 설명 변경 | 하위 호환, 마이그레이션 불필요 |
| `1.x.0` (minor) | 필수 필드 추가 (기본값 존재) | 로드 시 기본값 자동 채움 |
| `x.0.0` (major) | 구조 변경, 필드 삭제/타입 변경 | 마이그레이션 스크립트 필요 |

파이프라인 코드에서 `schema_version`을 읽어 호환성을 체크한다:
```python
SUPPORTED_SCHEMA_VERSIONS = ["1.0"]  # major.minor 단위로 호환성 판단

def load_metadata(path: str) -> dict:
    data = json.loads(Path(path).read_text())
    version = data["schema_version"]
    major_minor = ".".join(version.split(".")[:2])
    if major_minor not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"Unsupported schema version: {version}")
    return data
```

---

## 4. ChromaDB 벡터 저장소 설계

### 4.1 컬렉션 구조: 단일 컬렉션

모든 리포트의 청크를 단일 컬렉션 `securities_reports`에 저장한다.

**단일 컬렉션 선택 이유**:
- 리포트 유형이 동일하여 임베딩 공간의 의미 분포가 유사함
- SelfQueryRetriever의 메타데이터 필터로 종목/증권사/날짜별 검색이 충분히 가능
- 컬렉션 간 cross-search 필요 없음
- 관리 복잡도 최소화

```python
import chromadb

client = chromadb.PersistentClient(path="data/chromadb")
collection = client.get_or_create_collection(
    name="securities_reports",
    metadata={
        "description": "증권사 애널리스트 리포트 청크 벡터 저장소",
        "hnsw:space": "cosine"  # cosine similarity 사용
    }
)
```

### 4.2 Document 스키마

ChromaDB에 저장되는 각 document(청크)의 구조:

| 필드 | ChromaDB 매핑 | 타입 | 설명 |
|------|---------------|------|------|
| ID | `ids` | `string` | 청크 고유 식별자 |
| 텍스트 | `documents` | `string` | 청크 원문 (Markdown) |
| 임베딩 | `embeddings` | `float[]` (1536차원) | OpenAI embedding 벡터 |
| 메타데이터 | `metadatas` | `dict` | 아래 참조 |

#### 메타데이터 필드 (ChromaDB metadatas)

| 필드 | 타입 | 필수 | 설명 | SelfQuery 필터 대상 |
|------|------|------|------|---------------------|
| `document_id` | `string` | Y | 소스 문서 ID | N |
| `ticker` | `string` | Y | 종목코드 | Y |
| `company_name` | `string` | Y | 종목명 | Y |
| `date` | `string` | Y | 발행일 (YYYY-MM-DD) | Y |
| `broker` | `string` | Y | 증권사명 | Y |
| `report_type` | `string` | Y | 리포트 유형 | Y |
| `analyst` | `string` | N | 애널리스트명 | N |
| `chunk_type` | `string` | Y | 청크 유형 (`text`, `table`) | N |
| `chunk_index` | `integer` | Y | 문서 내 청크 순서 (0-based) | N |
| `page_number` | `integer` | N | 원본 PDF 페이지 번호 | N |

> **참고**: ChromaDB는 메타데이터 값으로 `string`, `integer`, `float`, `boolean`만 지원한다. 리스트나 중첩 객체는 사용할 수 없다.

### 4.3 ID 생성 전략

청크 ID는 소스 문서와 청크 위치를 조합하여 **결정론적(deterministic)**으로 생성한다. 이를 통해 동일 문서를 재처리해도 동일한 ID가 생성되어 중복이 자연스럽게 방지된다.

**ID 형식**: `{document_id}::chunk_{chunk_index}`

**예시**:
- `mirae_samsung_elec_20260210::chunk_0`
- `mirae_samsung_elec_20260210::chunk_1`
- `mirae_samsung_elec_20260210::chunk_14`

**구현**:
```python
def generate_chunk_id(document_id: str, chunk_index: int) -> str:
    return f"{document_id}::chunk_{chunk_index}"
```

**중복 방지 효과**:
- 동일 문서 재적재 시: 같은 ID가 생성되어 ChromaDB의 `upsert`로 덮어쓰기
- 다른 문서의 동일 인덱스: `document_id`가 다르므로 충돌 없음

---

## 5. 데이터 정합성

### 5.1 중복 PDF 감지 (해시 기반)

PDF 파일 등록 시 SHA-256 해시를 계산하여 기존 문서와 비교한다.

```python
import hashlib

def compute_file_hash(file_path: str) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return f"sha256:{sha256.hexdigest()}"
```

**중복 감지 플로우**:
```
새 PDF 파일 등록 요청
    │
    ▼
SHA-256 해시 계산
    │
    ▼
metadata.json의 기존 문서 해시와 비교
    │
    ├── 동일 해시 존재 → 중복 PDF로 판단, 스킵 (로그 출력)
    │
    └── 해시 없음 → 신규 문서로 등록, 파이프라인 진행
```

### 5.2 증분 업데이트 전략

파이프라인 실행 시 전체 PDF를 재처리하지 않고, 신규 및 미완료 문서만 처리한다.

```python
def get_documents_to_process(metadata: dict) -> list[str]:
    """처리가 필요한 문서 ID 목록을 반환한다."""
    to_process = []

    # 1. data/raw_pdfs/에 존재하지만 metadata.json에 없는 PDF → 신규
    for pdf_file in Path("data/raw_pdfs").glob("*.pdf"):
        doc_id = pdf_file.stem
        if doc_id not in metadata["documents"]:
            to_process.append(doc_id)
            continue

        # 2. status가 'indexed'가 아닌 문서 → 미완료, 재처리
        doc = metadata["documents"][doc_id]
        if doc["status"] != "indexed":
            to_process.append(doc_id)

    return to_process
```

### 5.3 파싱 결과 캐싱

Upstage Document Parse API 호출은 비용이 발생하므로, 이미 파싱된 결과가 있으면 API 호출을 건너뛴다.

**캐시 판단 로직**:
```
파싱 요청
    │
    ▼
data/parsed/{document_id}.md 파일 존재 여부 확인
    │
    ├── 존재함 → metadata.json에서 해시 비교
    │     │
    │     ├── 해시 일치 → 캐시 히트, 파싱 스킵
    │     │
    │     └── 해시 불일치 → PDF가 변경됨, 재파싱
    │
    └── 존재하지 않음 → API 호출하여 파싱 실행
```

### 5.4 파이프라인 실패 시 롤백/재처리 전략

각 단계는 독립적으로 실패할 수 있으며, `metadata.json`의 `status` 필드로 상태를 추적한다.

#### 상태 전이 다이어그램

```
pending → parsing → parsed → chunking → chunked → indexing → indexed
            │                   │                     │
            ▼                   ▼                     ▼
          failed              failed                failed
```

#### 단계별 실패 대응

| 실패 단계 | 상태값 | 대응 전략 | 정리(cleanup) 필요 여부 |
|-----------|--------|-----------|------------------------|
| **parsing** 실패 | `failed` | 재실행 시 API 재호출 | 불완전한 `.md` 파일 삭제 |
| **chunking** 실패 | `failed` | 캐시된 파싱 결과로 재청킹 | 없음 (메모리 내 처리) |
| **indexing** 실패 | `failed` | 해당 document_id의 기존 벡터 삭제 후 재적재 | ChromaDB에서 해당 문서 청크 삭제 |

#### 재처리 구현

```python
def cleanup_failed_document(document_id: str, failed_stage: str):
    """실패한 문서의 불완전한 결과물을 정리한다."""

    if failed_stage == "parsing":
        # 불완전한 파싱 결과 삭제
        parsed_md = Path(f"data/parsed/{document_id}.md")
        parsed_meta = Path(f"data/parsed/{document_id}.meta.json")
        parsed_md.unlink(missing_ok=True)
        parsed_meta.unlink(missing_ok=True)

    elif failed_stage == "indexing":
        # ChromaDB에서 해당 문서의 청크 모두 삭제
        collection.delete(
            where={"document_id": document_id}
        )
```

#### 전체 재처리 안전장치

`metadata.json`이 손상된 경우를 대비하여 전체 재구축이 가능하도록 설계한다:
- PDF 원본과 파싱 결과가 보존되어 있으므로, `metadata.json`을 삭제하고 파이프라인을 전체 재실행하면 된다
- ChromaDB도 `data/chromadb/` 디렉토리를 삭제하고 재적재할 수 있다

---

## 6. 용량 산정

### 6.1 기본 가정

| 항목 | 가정치 | 비고 |
|------|--------|------|
| 리포트 수 | 100건 (초기), 최대 1,000건 | 학습 프로젝트 규모 |
| PDF 평균 크기 | 2MB | 증권사 리포트 일반적 크기 |
| 리포트당 평균 청크 수 | 15개 | 테이블 + 텍스트 섹션 |
| 청크 평균 토큰 수 | 500 tokens (~750자) | 테이블 청크는 더 클 수 있음 |
| 임베딩 차원 | 1,536 (float32) | `text-embedding-3-small` |
| 벡터 1개 크기 | 6KB (1,536 x 4 bytes) | float32 기준 |

### 6.2 용량 예측

#### 초기 (100건 리포트)

| 저장소 | 계산 | 예상 용량 |
|--------|------|-----------|
| PDF 원본 | 100 x 2MB | **200MB** |
| 파싱 결과 (Markdown) | 100 x 50KB | **5MB** |
| 메타데이터 JSON | 100 x 2KB + 레지스트리 | **< 1MB** |
| ChromaDB 벡터 | 1,500 x 6KB | **9MB** |
| ChromaDB 메타데이터 + 인덱스 | 벡터의 ~2배 | **~20MB** |
| **합계** | | **~235MB** |

#### 최대 (1,000건 리포트)

| 저장소 | 계산 | 예상 용량 |
|--------|------|-----------|
| PDF 원본 | 1,000 x 2MB | **2GB** |
| 파싱 결과 (Markdown) | 1,000 x 50KB | **50MB** |
| 메타데이터 JSON | 1,000 x 2KB + 레지스트리 | **~3MB** |
| ChromaDB 벡터 | 15,000 x 6KB | **90MB** |
| ChromaDB 메타데이터 + 인덱스 | 벡터의 ~2배 | **~200MB** |
| **합계** | | **~2.3GB** |

### 6.3 용량 결론

- 로컬 개발 환경에서 충분히 감당 가능한 규모
- ChromaDB는 단일 머신에서 수백만 건까지 처리 가능하므로 15,000건 벡터는 성능 이슈 없음
- PDF 원본이 전체 용량의 대부분(~85%)을 차지하므로, 필요 시 PDF만 외부 저장소로 이동 가능
- `metadata.json` 파일은 1,000건까지 단일 JSON 파일로 관리 가능 (약 3MB, 로드 시간 무시 가능)

---

## 7. 설계 결정 요약

| 결정 사항 | 선택 | 대안 (검토 후 기각) | 사유 |
|-----------|------|---------------------|------|
| 벡터 컬렉션 | 단일 컬렉션 | 종목별/증권사별 분리 | 메타데이터 필터로 충분, 관리 단순화 |
| 메타데이터 저장 | JSON 파일 | SQLite, PostgreSQL | 학습 프로젝트 규모에 적합, 외부 의존성 최소화 |
| 청크 중간 저장 | 저장하지 않음 | 파일로 저장 | 파싱 결과가 캐시 역할, 불필요한 중복 방지 |
| ID 생성 | 결정론적 조합 | UUID, 해시 기반 | upsert 자연 지원, 디버깅 용이 |
| 중복 감지 | SHA-256 해시 | 파일명 비교 | 파일명 변경에도 정확한 감지 가능 |
| 파일 관리 | 무기한 보관 | TTL 기반 자동 삭제 | 학습 프로젝트, 용량 부담 낮음 |
