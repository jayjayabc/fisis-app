# financial-data MCP

**DART(전자공시시스템) · FISIS(금융통계정보시스템) 금융 데이터 MCP 서버**

Claude Code / Claude Desktop에서 한국 기업의 공시·재무제표·금융통계를 자연어로 조회·분석합니다.

> 💡 이 레포는 **MCP 서버**(`financial_data_mcp/`)와 **Streamlit 대시보드**(`app.py`) 두 가지 앱을 담고 있습니다. 아래는 MCP 서버 사용 가이드입니다. Streamlit 앱은 [Streamlit 대시보드](#-streamlit-대시보드-부가-기능) 섹션을 참고하세요.

---

## ✨ 특징

- **11개 MCP 도구**로 한국 기업 데이터 풀스택 조회 (DART 6 + FISIS 3 + 운영/참조 2)
- **토큰 효율**: 컴팩트 JSON + `sj_div` 필터로 재무제표 응답 토큰 **75% 절감** 실측
- **반복 사용 최적화**: 3단계 캐싱 (메모리 → 디스크 30일 → TTL 응답 캐시 1시간)
- **동시성 안전**: `asyncio.Lock`으로 기업코드 8MB 중복 다운로드 방지
- **자동 복구**: 503/429/transport 오류 지수 백오프 재시도 + CFS→OFS 자동 폴백
- **입력 검증**: API 호출 전 사전 차단으로 quota·토큰 낭비 방지
- **DART quota 트래킹**: 일일 20,000건 한도 실시간 추적
- **148개 단위 테스트**로 모든 기능 커버

---

## 🚀 빠른 시작

### 1. 클론 & 의존성 설치

```bash
git clone https://github.com/jayjayabc/fisis-app.git
cd fisis-app
git checkout claude/financial-data-mcp-ZT9Yd  # MCP 브랜치

# uv 권장
uv pip install -e .

# 또는 pip
pip install -e .
```

### 2. API 키 설정

`.env` 파일 생성 (`.env.example` 참고):

```bash
DART_API_KEY=your-dart-api-key    # https://opendart.fss.or.kr 에서 발급
FISIS_API_KEY=your-fisis-api-key  # https://fisis.fss.or.kr 에서 발급
```

### 3. 환경 검증 (⭐ 권장)

```bash
python scripts/preflight.py
```

Python 버전, 패키지, API 키, 실제 DART/FISIS 접근 가능 여부까지 한 방에 진단합니다.

### 4. Claude Code 연결

#### 방법 A — 프로젝트 전용 (해당 폴더에서만 사용)

프로젝트 루트에 `.mcp.json`이 포함되어 있으므로, 프로젝트 디렉토리에서 `claude`를 실행하면 자동 인식됩니다.

```bash
cd fisis-app
claude
```

#### 방법 B — 전역 등록 (⭐ 권장, 어디서든 사용)

어느 디렉토리에서 `claude`를 열어도 DART/FISIS 도구를 쓸 수 있게 전역 등록합니다.

**Windows (PowerShell)**:

```powershell
# 래퍼 스크립트 생성 + 전역 등록 (최초 1회)
Set-Content -Path "$HOME\financial-data-mcp.cmd" -Value '@python -m financial_data_mcp %*'
claude mcp add financial-data "$HOME\financial-data-mcp.cmd"
```

**macOS / Linux**:

```bash
# 래퍼 스크립트 생성 + 전역 등록 (최초 1회)
echo '#!/bin/sh' > ~/financial-data-mcp.sh && echo 'python -m financial_data_mcp "$@"' >> ~/financial-data-mcp.sh
chmod +x ~/financial-data-mcp.sh
claude mcp add financial-data ~/financial-data-mcp.sh
```

등록 확인: `claude mcp list`에서 `financial-data`가 보이면 성공.

#### 방법 C — Claude Desktop 앱

`claude_desktop_config.json`에 추가:

| OS | 경로 |
|----|------|
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "financial-data": {
      "command": "python",
      "args": ["-m", "financial_data_mcp"]
    }
  }
}
```

> `.env` 파일이 프로젝트 루트에 있으면 API 키가 자동 로드됩니다.

### 5. 테스트 질문

```
"삼성전자 2024년 손익계산서 보여줘"
"삼성전자, SK하이닉스, LG전자 2024년 영업이익 비교"
"오늘 DART API 얼마나 썼어?"
```

더 많은 시나리오는 [`docs/USAGE.md`](docs/USAGE.md) 참고.

---

## 🛠️ 제공 MCP 도구 (11개)

### DART 전자공시시스템

| 도구 | 용도 |
|------|------|
| `dart_search_company` | 회사명 → `corp_code` 검색 (다른 DART 도구 선행 조건) |
| `dart_company_overview` | 기업개황 (대표자·업종·주소·상장일 등) |
| `dart_search_disclosures` | 공시 목록 검색 (기간·유형·법인구분 필터) |
| `dart_financial_statements` | 주요 재무계정 (자산·부채·매출·이익) — **가벼움** |
| `dart_full_financial_statements` | 전체 재무제표 (`sj_div` 필터로 BS/IS/CF 선택) — **상세** |
| `dart_multi_company_financials` | 다중 회사 비교 (최대 20개, 1회 호출) |

### FISIS 금융통계정보시스템

| 도구 | 용도 |
|------|------|
| `fisis_list_statistics` | 통계 목록 검색 (대분류: 01=은행, 02=비은행, 03=보험, 04=금융투자) |
| `fisis_get_statistics` | 통계 데이터 조회 (기간·회사·항목별) |
| `fisis_list_companies` | 금융회사 목록 (권역별) |

### 운영 / 참조

| 도구 | 용도 |
|------|------|
| `dart_quota_status` | DART 일일 API quota 사용 현황 (20,000건/일) |
| `get_api_reference` | 보고서코드·법인구분·재무제표 구분 등 코드 참조표 |

---

## 📖 문서

| 문서 | 내용 |
|------|------|
| [`docs/USAGE.md`](docs/USAGE.md) | 실전 시나리오 10가지, 토큰 절약 팁, 로깅 설정 |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | 30+ 에러별 진단과 해결법 |
| [`.env.example`](.env.example) | 환경변수 템플릿 |

---

## 🧪 개발

### 테스트

```bash
pip install -e ".[dev]"
pytest tests/
```

148개 테스트 통과 기대 (`test_cache` · `test_http` · `test_validators` · `test_quota` · `test_dart_client` · `test_fisis_client` · `test_server`).

### 로컬 디버깅

```bash
# 세부 로그로 MCP 서버 기동
LOG_LEVEL=DEBUG python -m financial_data_mcp

# 또는 uv로
LOG_LEVEL=DEBUG uv run financial-data-mcp
```

### 프로젝트 구조

```
fisis-app/
├── financial_data_mcp/       ← MCP 서버 패키지
│   ├── __init__.py
│   ├── __main__.py           # python -m financial_data_mcp
│   ├── server.py             # FastMCP 서버 + 11개 도구
│   ├── dart_client.py        # DART OpenAPI 클라이언트
│   ├── fisis_client.py       # FISIS OpenAPI 클라이언트
│   ├── _cache.py             # TTL 메모리 캐시 + 디스크 캐시
│   ├── _http.py              # 재시도 + HTTP 에러 변환
│   ├── _quota.py             # DART quota 트래킹
│   └── _validators.py        # 입력 검증
├── tests/                    # 148개 단위 테스트
├── scripts/
│   └── preflight.py          # 환경 검증 스크립트
├── docs/
│   ├── USAGE.md
│   └── TROUBLESHOOTING.md
├── .mcp.json                 # Claude Code 자동 인식용
├── .env.example
├── pyproject.toml
│
└── [Streamlit 앱 파일들]
    ├── app.py                ← Streamlit 대시보드
    ├── db/                   ← DuckDB 로더
    ├── llm/                  ← Text-to-SQL 파이프라인
    └── data/                 ← FISIS Excel (git 미추적)
```

---

## 📊 Streamlit 대시보드 (부가 기능)

레포에는 별도로 FISIS Excel 기반의 Streamlit 분석 대시보드가 포함되어 있습니다. MCP 서버와 독립적이며, 로컬에서 탐색적 분석용으로 사용합니다.

```bash
source venv/Scripts/activate  # Windows
streamlit run app.py
```

자세한 내용은 이전 버전 README의 [Streamlit 섹션](https://github.com/jayjayabc/fisis-app/blob/master/README.md)을 참고하세요.

---

## 📜 라이선스

MIT

---

## 🙏 감사

- [금융감독원 DART OpenAPI](https://opendart.fss.or.kr) — 전자공시 데이터
- [금융감독원 FISIS OpenAPI](https://fisis.fss.or.kr) — 금융통계 데이터
- [Model Context Protocol](https://modelcontextprotocol.io) — MCP 프로토콜
