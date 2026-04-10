#!/usr/bin/env python3
"""
FISIS MCP Server
FISIS 금융 데이터를 MCP 프로토콜로 제공하는 서버

데이터 소스:
  1. 로컬 DuckDB (data/fisis.duckdb) — 즉시 사용 가능
  2. FISIS Open API / 공공데이터포털 — API 키 설정 시 실시간 조회

─── API 키 발급 방법 ────────────────────────────────────────────────
  A. FSS 직접 발급 (FISIS 전용)
     신청: https://www.fss.or.kr/fss/kr/openApi/authReq/req.jsp
     → 승인 후 이메일로 인증키 수신
     → .env에 FISIS_API_KEY=발급키 추가

  B. 공공데이터포털 (국내은행 재무정보 등)
     신청: https://www.data.go.kr → '금융통계국내은행정보' 검색 → 활용신청
     → 보통 1~2시간 내 자동승인
     → .env에 DATA_GO_KR_KEY=발급키 추가

─── Claude Code 등록 ────────────────────────────────────────────────
  claude mcp add fisis python /home/user/fisis-app/mcp_server/server.py

─── 에이전트팀 연동 ─────────────────────────────────────────────────
  agents/fisis_team.py mcp_servers에 이미 포함됨
"""

import json
import os
from pathlib import Path
from typing import Any

import anyio
import duckdb
import httpx
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    ListResourcesResult,
    ListToolsResult,
    ReadResourceResult,
    Resource,
    TextContent,
    Tool,
)

load_dotenv()

# ── 설정 ──────────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent.parent / "data" / "fisis.duckdb"

# FSS FISIS Open API (fisis.fss.or.kr)
# 키 신청: https://www.fss.or.kr/fss/kr/openApi/authReq/req.jsp
FISIS_API_KEY  = os.getenv("FISIS_API_KEY", "")
FISIS_API_BASE = os.getenv("FISIS_API_BASE", "https://fisis.fss.or.kr/openapi")

# 공공데이터포털 API (data.go.kr) — 국내은행 재무정보 등
# 키 신청: https://www.data.go.kr → '금융통계국내은행정보' 검색
DATA_GO_KR_KEY  = os.getenv("DATA_GO_KR_KEY", "")
DATA_GO_KR_BASE = "http://apis.data.go.kr/1160100/service/GetDomeBankInfoService"

# 공공데이터포털 국내은행 API 오퍼레이션
BANK_OPERATIONS = {
    "general":    "getDomeBankGeneInfo",        # 일반현황 (설립연도, 본점소재지)
    "financial":  "getDomeBankFinInfo",          # 재무현황 (자산, 부채, 자본)
    "indicators": "getDomeBankKeyMngmtIndicInfo", # 주요경영지표 (ROA, ROE, BIS)
    "business":   "getDomeBankKeyBizActivityInfo",# 영업활동 (대출금, 예수금)
}

app = Server("fisis-mcp")


# ── 유틸리티 ──────────────────────────────────────────────────────────────────

def _con() -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"DuckDB 없음: {DB_PATH}\n"
            "python scripts/excel_to_duckdb.py 를 먼저 실행하세요."
        )
    return duckdb.connect(str(DB_PATH), read_only=True)


def _query(sql: str, max_rows: int = 200) -> list[dict]:
    """SELECT 전용 안전 쿼리. LIMIT 없으면 자동 추가."""
    stripped = sql.strip().upper()
    if not (stripped.startswith("SELECT") or stripped.startswith("WITH")):
        raise ValueError("SELECT 또는 WITH 쿼리만 허용됩니다.")
    if "LIMIT" not in stripped:
        sql = sql.rstrip(";") + f" LIMIT {max_rows}"
    return _con().execute(sql).df().to_dict("records")


def _md_table(rows: list[dict]) -> str:
    """딕셔너리 리스트 → 마크다운 표"""
    if not rows:
        return "(결과 없음)"
    headers = list(rows[0].keys())
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for r in rows:
        lines.append(
            "| " + " | ".join(str(v) if v is not None else "-" for v in r.values()) + " |"
        )
    return "\n".join(lines)


def _api_key_guide(key_type: str) -> str:
    if key_type == "fisis":
        return (
            "⚠️ FISIS_API_KEY 미설정\n\n"
            "FSS FISIS Open API 키 발급:\n"
            "1. https://www.fss.or.kr/fss/kr/openApi/authReq/req.jsp 접속\n"
            "2. 이용형태(개인/법인) 선택 후 신청\n"
            "3. 승인 후 이메일로 인증키 수신\n"
            "4. .env 파일에 FISIS_API_KEY=발급키 추가"
        )
    return (
        "⚠️ DATA_GO_KR_KEY 미설정\n\n"
        "공공데이터포털 API 키 발급:\n"
        "1. https://www.data.go.kr 접속 후 로그인\n"
        "2. '금융통계국내은행정보' 검색 → 활용신청 (1~2시간 내 자동승인)\n"
        "3. 마이페이지 → 데이터 활용 → 개발계정에서 serviceKey 확인\n"
        "4. .env 파일에 DATA_GO_KR_KEY=발급키 추가"
    )


# ── 리소스 ────────────────────────────────────────────────────────────────────

@app.list_resources()
async def list_resources() -> ListResourcesResult:
    return ListResourcesResult(resources=[
        Resource(
            uri="fisis://schema",
            name="FISIS DB 스키마",
            description="업권·테이블 목록과 공통 컬럼 구조",
            mimeType="text/markdown",
        ),
        Resource(
            uri="fisis://sectors",
            name="업권 목록",
            description="국내은행(A), 신용카드사(C), 리스사(K), 할부금융사(T)",
            mimeType="application/json",
        ),
        Resource(
            uri="fisis://api-guide",
            name="API 키 발급 가이드",
            description="FISIS Open API 및 공공데이터포털 API 키 발급 방법",
            mimeType="text/markdown",
        ),
    ])


@app.read_resource()
async def read_resource(uri: str) -> ReadResourceResult:
    if uri == "fisis://schema":
        rows = _query(
            "SELECT sector_code, sector_name, stat_num, table_name, sheet_name, row_count "
            "FROM sheet_meta ORDER BY sector_code, stat_num"
        )
        lines = [
            "# FISIS DuckDB 스키마\n\n",
            "## 공통 컬럼 구조\n",
            "| 컬럼 | 타입 | 설명 |\n|------|------|------|\n",
            "| base_month | INT | 기준월 (예: 202509) |\n",
            "| finance_cd | VARCHAR | 금융회사 코드 |\n",
            "| finance_nm | VARCHAR | 금융회사명 |\n",
            "| account_cd | VARCHAR | 계정과목 코드 |\n",
            "| account_nm | VARCHAR | 계정과목명 |\n",
            "| a | VARCHAR | 금액(원 단위). TRY_CAST(a AS DOUBLE)/1e8 → 억원 |\n",
            "| b | DOUBLE | 비율(%) 또는 전기 금액 |\n\n",
            "## 주요 account_cd\n",
            "- 재무상태표(103/104): `A`=자산총계, `B`=부채총계, `A2`=자본총계\n",
            "- 손익계산서(118): `A`=수익합계, `J`=당기순이익\n\n",
            "## 테이블 목록\n",
            "| 업권 | 테이블 | 시트명 | 행수 |\n|------|--------|--------|------|\n",
        ]
        for r in rows:
            lines.append(
                f"| {r['sector_code']} {r['sector_name']} "
                f"| {r['table_name']} | {r['sheet_name'][:35]} | {r['row_count']:,} |\n"
            )
        return ReadResourceResult(
            contents=[TextContent(type="text", text="".join(lines))]
        )

    if uri == "fisis://sectors":
        rows = _query(
            "SELECT DISTINCT sector_code, sector_name FROM sheet_meta ORDER BY sector_code"
        )
        return ReadResourceResult(
            contents=[TextContent(
                type="text",
                text=json.dumps(rows, ensure_ascii=False, indent=2),
            )]
        )

    if uri == "fisis://api-guide":
        guide = """# FISIS API 키 발급 가이드

## A. FSS FISIS Open API (권장)
- 신청: https://www.fss.or.kr/fss/kr/openApi/authReq/req.jsp
- API 문서: https://fisis.fss.or.kr/fss/fsi/id/fssOpenAPIView30.jsp
- 설정: `.env` → `FISIS_API_KEY=발급받은키`

## B. 공공데이터포털 — 국내은행 재무정보
- 신청: https://www.data.go.kr → '금융통계국내은행정보' 검색 (데이터셋 ID: 15061304)
- 자동승인 (1~2시간)
- 설정: `.env` → `DATA_GO_KR_KEY=발급받은키`

### 공공데이터포털 제공 오퍼레이션 (국내은행)
| 오퍼레이션 | 설명 |
|-----------|------|
| getDomeBankGeneInfo | 일반현황 (설립연도, 본점소재지) |
| getDomeBankFinInfo | 재무현황 (자산, 부채, 자본) |
| getDomeBankKeyMngmtIndicInfo | 주요경영지표 (ROA, ROE, BIS) |
| getDomeBankKeyBizActivityInfo | 영업활동 (대출금, 예수금) |

### 공통 파라미터
| 파라미터 | 설명 | 예시 |
|---------|------|------|
| serviceKey | API 인증키 (자동 추가) | — |
| startBaseMon | 조회 시작 기준월 | 202501 |
| endBaseMon | 조회 종료 기준월 | 202509 |
| numOfRows | 한 번에 조회할 행 수 | 100 |
| pageNo | 페이지 번호 | 1 |
| type | 응답 형식 | json 또는 xml |
"""
        return ReadResourceResult(
            contents=[TextContent(type="text", text=guide)]
        )

    raise ValueError(f"알 수 없는 리소스: {uri}")


# ── 도구 정의 ─────────────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> ListToolsResult:
    return ListToolsResult(tools=[
        Tool(
            name="query",
            description=(
                "FISIS DuckDB에 SELECT 쿼리를 실행합니다 (로컬, 즉시 사용 가능).\n"
                "- 금액 컬럼 'a'는 VARCHAR → TRY_CAST(a AS DOUBLE)/1e8 으로 억원 환산\n"
                "- 테이블명은 큰따옴표: \"K_103\"\n"
                "- 최신 기준월: WHERE base_month = (SELECT MAX(base_month) FROM \"테이블\")"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "실행할 SELECT SQL"},
                    "max_rows": {"type": "integer", "default": 100},
                },
                "required": ["sql"],
            },
        ),
        Tool(
            name="asset_ranking",
            description="특정 업권의 자산 규모 순위를 조회합니다 (로컬 DuckDB).",
            inputSchema={
                "type": "object",
                "properties": {
                    "sector": {
                        "type": "string",
                        "enum": ["K", "T", "A", "C"],
                        "description": "K=리스사, T=할부금융사, A=국내은행, C=신용카드사",
                    },
                    "top_n": {"type": "integer", "default": 10},
                },
                "required": ["sector"],
            },
        ),
        Tool(
            name="risk_companies",
            description=(
                "자본잠식(자본총계<0) 또는 당기순손실 기업을 3단계 등급으로 조회합니다 (로컬 DuckDB).\n"
                "- 🔴 적색: 자본잠식 + 당기순손실 동시\n"
                "- 🟡 황색: 둘 중 하나\n"
                "- 🟢 녹색: 정상"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sector": {
                        "type": "string",
                        "enum": ["K", "T", "both"],
                        "default": "both",
                        "description": "K=리스사, T=할부금융사, both=캐피탈 전체",
                    },
                },
            },
        ),
        Tool(
            name="company_financials",
            description="특정 금융회사의 자산·자본·손익·ROA 시계열을 조회합니다 (로컬 DuckDB).",
            inputSchema={
                "type": "object",
                "properties": {
                    "company_keyword": {
                        "type": "string",
                        "description": "회사명 키워드 (LIKE 검색, 예: '현대', '케이비')",
                    },
                    "sector": {
                        "type": "string",
                        "enum": ["K", "T", "A", "C"],
                    },
                    "periods": {"type": "integer", "default": 8, "description": "최근 N개 기준월"},
                },
                "required": ["company_keyword", "sector"],
            },
        ),
        Tool(
            name="sector_summary",
            description="업권 전체 집계 지표를 조회합니다 (총 자산, 평균 ROA, 자본잠식 수 등). 로컬 DuckDB.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sector": {
                        "type": "string",
                        "enum": ["K", "T", "both"],
                        "default": "both",
                    },
                },
            },
        ),
        Tool(
            name="fetch_fisis_api",
            description=(
                "FSS FISIS Open API에서 실시간 데이터를 조회합니다.\n"
                "FISIS_API_KEY 필요 (신청: https://www.fss.or.kr/fss/kr/openApi/authReq/req.jsp)"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "endpoint": {
                        "type": "string",
                        "description": "API 엔드포인트 경로 (FISIS_API_BASE 이후)",
                    },
                    "params": {
                        "type": "object",
                        "description": "쿼리 파라미터 (startBaseMon, endBaseMon 등)",
                        "default": {},
                    },
                    "response_format": {
                        "type": "string",
                        "enum": ["json", "xml"],
                        "default": "json",
                    },
                },
                "required": ["endpoint"],
            },
        ),
        Tool(
            name="fetch_bank_api",
            description=(
                "공공데이터포털 국내은행 재무정보 API에서 실시간 데이터를 조회합니다.\n"
                "DATA_GO_KR_KEY 필요 (신청: https://www.data.go.kr → '금융통계국내은행정보')\n\n"
                "오퍼레이션:\n"
                "- financial:  재무현황 (자산, 부채, 자본)\n"
                "- indicators: 주요경영지표 (ROA, ROE, BIS)\n"
                "- business:   영업활동 (대출금, 예수금)\n"
                "- general:    일반현황 (설립연도, 본점소재지)"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["financial", "indicators", "business", "general"],
                        "default": "financial",
                    },
                    "start_month": {
                        "type": "string",
                        "description": "조회 시작 기준월 (YYYYMM, 예: 202501)",
                    },
                    "end_month": {
                        "type": "string",
                        "description": "조회 종료 기준월 (YYYYMM, 예: 202509)",
                    },
                    "num_of_rows": {"type": "integer", "default": 100},
                    "page_no": {"type": "integer", "default": 1},
                },
            },
        ),
    ])


# ── 도구 실행 ─────────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    try:
        result = await _dispatch(name, arguments)
        return CallToolResult(content=[TextContent(type="text", text=result)])
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"❌ 오류: {e}")],
            isError=True,
        )


async def _dispatch(name: str, args: dict) -> str:

    # ── 로컬 DuckDB 도구 ──────────────────────────────────────────────────────

    if name == "query":
        rows = _query(args["sql"], args.get("max_rows", 100))
        return f"총 {len(rows)}행\n\n" + _md_table(rows)

    elif name == "asset_ranking":
        s, n = args["sector"], args.get("top_n", 10)
        rows = _query(f"""
            SELECT finance_nm AS 회사명,
                   ROUND(TRY_CAST(a AS DOUBLE)/1e8, 1) AS 자산_억원
            FROM "{s}_103"
            WHERE account_cd = 'A'
              AND base_month = (SELECT MAX(base_month) FROM "{s}_103")
            ORDER BY TRY_CAST(a AS DOUBLE) DESC NULLS LAST
            LIMIT {n}
        """)
        lines = [
            f"## 자산 상위 {n}개사 (업권: {s})\n",
            "| 순위 | 회사명 | 자산(억원) |\n|------|--------|----------|\n",
        ]
        for i, r in enumerate(rows, 1):
            lines.append(f"| {i} | {r['회사명']} | {r['자산_억원']:,.1f} |\n")
        return "".join(lines)

    elif name == "risk_companies":
        sector = args.get("sector", "both")
        targets = ["K", "T"] if sector == "both" else [sector]
        all_rows: list[dict] = []
        for s in targets:
            rows = _query(f"""
                SELECT f.finance_nm AS 회사명,
                       '{s}' AS 업권,
                       ROUND(TRY_CAST(f.a  AS DOUBLE)/1e8, 1) AS 자산_억원,
                       ROUND(TRY_CAST(e.a  AS DOUBLE)/1e8, 1) AS 자본_억원,
                       ROUND(TRY_CAST(ni.a AS DOUBLE)/1e8, 1) AS 순이익_억원,
                       CASE
                         WHEN TRY_CAST(e.a  AS DOUBLE) < 0
                          AND TRY_CAST(ni.a AS DOUBLE) < 0 THEN '🔴 적색'
                         WHEN TRY_CAST(e.a  AS DOUBLE) < 0
                           OR TRY_CAST(ni.a AS DOUBLE) < 0 THEN '🟡 황색'
                         ELSE '🟢 녹색'
                       END AS 위험등급
                FROM "{s}_103" f
                JOIN "{s}_104" e  ON f.finance_nm=e.finance_nm AND f.base_month=e.base_month
                JOIN "{s}_118" ni ON f.finance_nm=ni.finance_nm AND f.base_month=ni.base_month
                WHERE f.account_cd='A' AND e.account_cd='A2' AND ni.account_cd='J'
                  AND f.base_month=(SELECT MAX(base_month) FROM "{s}_103")
                  AND (TRY_CAST(e.a AS DOUBLE) < 0 OR TRY_CAST(ni.a AS DOUBLE) < 0)
                ORDER BY TRY_CAST(e.a AS DOUBLE) ASC
            """)
            all_rows.extend(rows)
        return f"## 위험 기업 ({len(all_rows)}개사)\n\n" + _md_table(all_rows)

    elif name == "company_financials":
        kw, s = args["company_keyword"], args["sector"]
        periods = args.get("periods", 8)
        rows = _query(f"""
            SELECT f.base_month AS 기준월,
                   f.finance_nm AS 회사명,
                   ROUND(TRY_CAST(f.a  AS DOUBLE)/1e8, 1) AS 자산_억원,
                   ROUND(TRY_CAST(e.a  AS DOUBLE)/1e8, 1) AS 자본_억원,
                   ROUND(TRY_CAST(ni.a AS DOUBLE)/1e8, 1) AS 순이익_억원,
                   ROUND(
                     TRY_CAST(ni.a AS DOUBLE)
                     / NULLIF(TRY_CAST(f.a AS DOUBLE), 0) * 100, 2
                   ) AS ROA_pct
            FROM "{s}_103" f
            JOIN "{s}_104" e  ON f.finance_nm=e.finance_nm AND f.base_month=e.base_month
            JOIN "{s}_118" ni ON f.finance_nm=ni.finance_nm AND f.base_month=ni.base_month
            WHERE f.account_cd='A' AND e.account_cd='A2' AND ni.account_cd='J'
              AND f.finance_nm LIKE '%{kw}%'
            ORDER BY f.base_month DESC
            LIMIT {periods}
        """)
        header = f"## '{kw}' 재무 현황 (업권: {s}, 최근 {periods}개월)\n\n"
        return header + (_md_table(rows) if rows else f"'{kw}' 데이터 없음")

    elif name == "sector_summary":
        sector = args.get("sector", "both")
        targets = ["K", "T"] if sector == "both" else [sector]
        summaries = []
        for s in targets:
            rows = _query(f"""
                WITH latest AS (SELECT MAX(base_month) AS m FROM "{s}_103"),
                asset AS (
                    SELECT finance_nm, TRY_CAST(a AS DOUBLE) AS v
                    FROM "{s}_103", latest WHERE account_cd='A' AND base_month=m
                ),
                equity AS (
                    SELECT finance_nm, TRY_CAST(a AS DOUBLE) AS v
                    FROM "{s}_104", latest WHERE account_cd='A2' AND base_month=m
                ),
                income AS (
                    SELECT finance_nm, TRY_CAST(a AS DOUBLE) AS v
                    FROM "{s}_118", latest WHERE account_cd='J' AND base_month=m
                )
                SELECT '{s}' AS 업권,
                       COUNT(*) AS 회사수,
                       ROUND(SUM(a.v)/1e8, 0) AS 총자산_억원,
                       ROUND(AVG(i.v / NULLIF(a.v, 0) * 100), 2) AS 평균ROA_pct,
                       SUM(CASE WHEN e.v < 0 THEN 1 ELSE 0 END) AS 자본잠식수,
                       SUM(CASE WHEN i.v < 0 THEN 1 ELSE 0 END) AS 순손실수
                FROM asset a
                LEFT JOIN equity e ON a.finance_nm=e.finance_nm
                LEFT JOIN income i ON a.finance_nm=i.finance_nm
            """)
            summaries.extend(rows)
        return "## 업권 집계 요약\n\n" + _md_table(summaries)

    # ── 실시간 API 도구 ───────────────────────────────────────────────────────

    elif name == "fetch_fisis_api":
        if not FISIS_API_KEY:
            return _api_key_guide("fisis")
        endpoint = args["endpoint"].lstrip("/")
        params = {
            **args.get("params", {}),
            "authKey": FISIS_API_KEY,
            "lang": "ko",
            "type": args.get("response_format", "json"),
        }
        url = f"{FISIS_API_BASE}/{endpoint}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
        return f"## FISIS API 응답 ({endpoint})\n\n```\n{resp.text[:5000]}\n```"

    elif name == "fetch_bank_api":
        if not DATA_GO_KR_KEY:
            return _api_key_guide("data_go_kr")
        operation = args.get("operation", "financial")
        op_name = BANK_OPERATIONS.get(operation, operation)
        params = {
            "serviceKey": DATA_GO_KR_KEY,
            "type": "json",
            "numOfRows": args.get("num_of_rows", 100),
            "pageNo": args.get("page_no", 1),
        }
        if args.get("start_month"):
            params["startBaseMon"] = args["start_month"]
        if args.get("end_month"):
            params["endBaseMon"] = args["end_month"]
        url = f"{DATA_GO_KR_BASE}/{op_name}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
        try:
            data = resp.json()
            items = (
                data.get("response", {})
                    .get("body", {})
                    .get("items", {})
                    .get("item", [])
            )
            if isinstance(items, dict):
                items = [items]
            return f"## 국내은행 {operation} API ({len(items)}건)\n\n" + _md_table(items)
        except Exception:
            return f"## 국내은행 {operation} API\n\n```json\n{resp.text[:5000]}\n```"

    else:
        raise ValueError(f"알 수 없는 도구: {name}")


# ── 진입점 ────────────────────────────────────────────────────────────────────

async def main() -> None:
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
