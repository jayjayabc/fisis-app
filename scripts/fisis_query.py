"""
FISIS 쿼리 CLI 헬퍼
Claude Code 스킬(/fisis)에서 사용하는 독립 실행 스크립트

사용법:
  python scripts/fisis_query.py --schema
  python scripts/fisis_query.py --sql "SELECT ..."
"""

import argparse
import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

DB_PATH = Path(__file__).parent.parent / "data" / "fisis.duckdb"


def get_con() -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        print(f"[ERROR] DB 파일 없음: {DB_PATH}", file=sys.stderr)
        print("scripts/excel_to_duckdb.py 를 먼저 실행하세요.", file=sys.stderr)
        sys.exit(1)
    return duckdb.connect(str(DB_PATH), read_only=True)


def cmd_schema() -> None:
    """sheet_meta + 컬럼 구조 출력 (LLM 프롬프트용)"""
    con = get_con()
    meta = con.execute(
        "SELECT sector_code, sector_name, stat_num, table_name, sheet_name, row_count "
        "FROM sheet_meta ORDER BY sector_code, stat_num"
    ).df()

    lines = ["# FISIS DuckDB 스키마\n"]

    lines.append("## 업권(sector) 목록\n")
    for code, name in meta[["sector_code", "sector_name"]].drop_duplicates().values:
        lines.append(f"- `{code}` = {name}\n")

    lines.append("\n## 테이블 목록 (sheet_meta)\n")
    lines.append("| sector | table_name | sheet_name | row_count |\n")
    lines.append("|--------|------------|------------|----------|\n")
    for _, row in meta.iterrows():
        lines.append(
            f'| {row["sector_code"]} | {row["table_name"]} '
            f'| {row["sheet_name"][:35]} | {row["row_count"]:,} |\n'
        )

    lines.append("\n## 데이터 테이블 공통 컬럼 구조\n")
    lines.append("| 컬럼 | 타입 | 설명 |\n")
    lines.append("|------|------|------|\n")
    lines.append("| `base_month` | INT | 기준월 (예: 202509) |\n")
    lines.append("| `finance_cd` | VARCHAR | 금융회사 코드 |\n")
    lines.append("| `finance_nm` | VARCHAR | 금융회사명 |\n")
    lines.append("| `account_cd` | VARCHAR | 계정과목 코드 |\n")
    lines.append("| `account_nm` | VARCHAR | 계정과목명 |\n")
    lines.append("| `a` | VARCHAR | 금액(원 단위). `TRY_CAST(a AS DOUBLE)/1e8` → 억원 |\n")
    lines.append("| `b` | DOUBLE | 비율(%) 또는 전기 금액 |\n")

    lines.append("\n## 주요 account_cd\n")
    lines.append("재무상태표 (103, 104 테이블):\n")
    lines.append("- `A` = 자산총계  `B` = 부채총계  `A2` = 자본총계\n")
    lines.append("손익계산서 (118 테이블):\n")
    lines.append("- `A` = 수익합계  `J` = 당기순이익\n")

    lines.append("\n## 주요 회사명 LIKE 검색 키워드\n")
    aliases = [
        ("현대캐피탈", "%현대커머셜%"),
        ("KB캐피탈", "%케이비캐피탈%"),
        ("우리금융캐피탈", "%우리금융캐피탈%"),
        ("하나캐피탈", "%하나캐피탈%"),
        ("신한캐피탈", "%신한캐피탈%"),
        ("롯데캐피탈", "%롯데캐피탈%"),
    ]
    for display, pattern in aliases:
        lines.append(f"- {display}: `finance_nm LIKE '{pattern}'`\n")

    print("".join(lines))


def cmd_sql(sql: str, max_rows: int = 100) -> None:
    """SQL 실행 후 결과를 마크다운 테이블로 출력"""
    stripped = sql.strip().upper()
    if not (stripped.startswith("SELECT") or stripped.startswith("WITH")):
        print("[ERROR] SELECT 또는 WITH 쿼리만 허용됩니다.", file=sys.stderr)
        sys.exit(1)

    if "LIMIT" not in stripped:
        sql = sql.rstrip(";") + f" LIMIT {max_rows}"

    try:
        con = get_con()
        df: pd.DataFrame = con.execute(sql).df()
    except Exception as e:
        print(f"[ERROR] 쿼리 실행 실패: {e}", file=sys.stderr)
        sys.exit(1)

    if df.empty:
        print("(결과 없음)")
        return

    print(f"총 {len(df)}행\n")
    print(df.to_markdown(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="FISIS DuckDB CLI 헬퍼")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--schema", action="store_true", help="DB 스키마 출력")
    group.add_argument("--sql", metavar="SQL", help="SQL 쿼리 실행")
    parser.add_argument("--max-rows", type=int, default=100, help="최대 행 수 (기본: 100)")
    args = parser.parse_args()

    if args.schema:
        cmd_schema()
    else:
        cmd_sql(args.sql, max_rows=args.max_rows)


if __name__ == "__main__":
    main()
