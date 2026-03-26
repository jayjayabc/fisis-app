# FISIS 금융 데이터 조회

금융감독원 FISIS 데이터를 자연어로 조회합니다.

**질문:** $ARGUMENTS

---

다음 순서로 작업하세요.

## 1단계: 스키마 확인

```bash
python scripts/fisis_query.py --schema
```

## 2단계: SQL 작성

스키마를 바탕으로 질문에 답하는 DuckDB SQL을 작성하세요.

**반드시 지킬 규칙:**
- SELECT 문만 작성 (INSERT·UPDATE·DELETE·DROP 금지)
- 금액 컬럼 `a`는 VARCHAR → `TRY_CAST(a AS DOUBLE)` 변환 필수
- 억원 환산: `TRY_CAST(a AS DOUBLE)/1e8`
- 테이블명은 큰따옴표: `"K_103"`
- 최신 기준월: `WHERE base_month = (SELECT MAX(base_month) FROM "테이블명")`
- LIMIT 100 이하
- 회사명은 LIKE 검색 (정확한 이름 대신 핵심 키워드)

**자주 쓰는 패턴:**
- 자산 순위: `K_103` (리스사) 또는 `T_103` (할부금융사), `account_cd='A'`
- 자본총계: `K_104` / `T_104`, `account_cd='A2'`
- 당기순이익: `K_118` / `T_118`, `account_cd='J'`
- 국내은행: `A_SA045` 등
- 신용카드사: `C_103`, `C_118`
- ROA = 당기순이익 / 자산총계 × 100

## 3단계: 쿼리 실행

```bash
python scripts/fisis_query.py --sql "여기에 SQL 작성"
```

## 4단계: 결과 정리

쿼리 결과를 바탕으로 다음 형식으로 답변하세요:
- 금액은 억원 또는 조원 단위로 명시 (1조 = 10,000억)
- 순위·비교는 마크다운 표로 정리
- 핵심 인사이트 1~2줄 추가
- 데이터에 없는 내용은 "확인되지 않습니다"로 명시
- 모든 답변은 한국어로
