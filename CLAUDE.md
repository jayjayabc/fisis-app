# financial-data MCP

## 프로젝트 개요
DART(전자공시시스템)과 FISIS(금융통계정보시스템) 데이터를 조회·분석하는 MCP 서버.
Claude Code / Claude Desktop에서 자연어로 한국 기업 공시·재무제표·금융통계를 조회합니다.

## 기술 스택
- MCP (Model Context Protocol) - FastMCP 서버
- httpx - 비동기 HTTP 클라이언트
- python-dotenv - 환경변수 관리

## 구조
- `financial_data_mcp/` - MCP 서버 패키지
  - `server.py` - FastMCP 서버 + 11개 도구 정의
  - `dart_client.py` - DART OpenAPI 클라이언트
  - `fisis_client.py` - FISIS OpenAPI 클라이언트
  - `_cache.py` - TTL 메모리 캐시 + 디스크 캐시
  - `_http.py` - 재시도 + HTTP 에러 변환
  - `_quota.py` - DART quota 트래킹
  - `_validators.py` - 입력 검증
- `tests/` - 148개 단위 테스트
- `scripts/preflight.py` - 로컬 환경 진단 스크립트
- `docs/` - 사용 가이드 + 트러블슈팅
- `.env` - API 키 (git 미추적)

## 규칙
- API 키는 .env로 관리 (하드코딩 금지)
- 차트/시각화는 이 패키지의 범위 아님 (데이터 조회 전용)
- 테스트는 모두 mock 기반 (실 API 호출 안 함)

## 실행
```bash
# MCP 서버
python -m financial_data_mcp

# 테스트
pip install -e ".[dev]"
pytest tests/

# 환경 진단
python scripts/preflight.py
```

## MCP 서버
- DART_API_KEY, FISIS_API_KEY 환경변수 필요
- pyproject.toml에 패키지 설정 포함
- Claude Code: .mcp.json 자동 인식 또는 claude mcp add로 전역 등록
- Claude Desktop: claude_desktop_config.json에서 설정
