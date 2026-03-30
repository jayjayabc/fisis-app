"""
FISIS Agent Team
Claude Agent SDK 기반 멀티에이전트 시스템

각 전문 에이전트가 협력해 금융 데이터 분석 프로젝트를 완료합니다.

사용법:
  python agents/fisis_team.py                              # 기본 작업 실행
  python agents/fisis_team.py "리스사 자산 현황 분석 보고서 작성"
  python agents/fisis_team.py "app.py에 Plotly 차트 섹션 추가"
"""

import sys
import anyio
from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AgentDefinition,
    ResultMessage,
    AssistantMessage,
    TextBlock,
)

# ── 에이전트 정의 ─────────────────────────────────────────────────────────────

DATA_AGENT = AgentDefinition(
    description="FISIS DuckDB에서 금융 데이터를 조회하는 전문 에이전트",
    prompt="""당신은 FISIS 금융 데이터 조회 전문가입니다.
scripts/fisis_query.py CLI를 사용해 DuckDB를 조회하세요.

명령어:
- 스키마 확인: python scripts/fisis_query.py --schema
- 쿼리 실행:   python scripts/fisis_query.py --sql "SELECT ..."

DuckDB SQL 규칙:
- SELECT 문만 허용
- 금액 컬럼 `a`는 VARCHAR → TRY_CAST(a AS DOUBLE)/1e8 (억원 환산)
- 테이블명은 큰따옴표: "K_103"
- 최신 기준월: WHERE base_month = (SELECT MAX(base_month) FROM "테이블명")
- LIMIT 100 이하

주요 테이블:
- K_103/T_103: 리스사/할부금융사 자산
- K_104/T_104: 부채·자본 (account_cd='A2'=자본총계)
- K_118/T_118: 손익 (account_cd='J'=당기순이익)
- A_SA045: 국내은행 주요 대출 지표
""",
    tools=["Bash"],
)

INSIGHT_AGENT = AgentDefinition(
    description="금융 데이터를 분석해 인사이트를 도출하는 전문 에이전트",
    prompt="""당신은 한국 금융감독원 FISIS 데이터 전문 분석가입니다.

주어진 데이터를 바탕으로 다음을 수행하세요:
1. 주요 재무 지표 해석 (자산, 자본, ROA, 수익성)
2. 기간별 추세 분석
3. 업권 내 비교 분석 (상위/하위 기업)
4. 위험 신호 탐지 (자본잠식, 당기순손실)
5. 핵심 인사이트 3~5개 요약

표현 규칙:
- 금액: 억원 또는 조원 (1조 = 10,000억)
- 비율: 소수점 1자리 %
- 비교/순위는 마크다운 표로 정리
""",
    tools=[],
)

VIZ_AGENT = AgentDefinition(
    description="Plotly로 금융 데이터 시각화를 생성하는 전문 에이전트",
    prompt="""당신은 Plotly 기반 금융 데이터 시각화 전문가입니다.

주어진 데이터로 Plotly 차트를 생성하고 reports/charts/ 에 저장하세요.

차트 생성 패턴:
```python
import plotly.graph_objects as go
import plotly.express as px

# 예시: 막대 차트
fig = go.Figure(go.Bar(x=companies, y=values, text=labels))
fig.update_layout(title="차트 제목", xaxis_title="X축", yaxis_title="억원")
fig.write_html("reports/charts/chart_name.html")
print("저장 완료: reports/charts/chart_name.html")
```

원칙:
- matplotlib 사용 금지, Plotly만 사용
- 한국어 레이블 사용
- 금액 단위(억원) 명시
- 파일명: 영문_스네이크케이스.html
""",
    tools=["Bash", "Write"],
)

DEV_AGENT = AgentDefinition(
    description="app.py에 새 기능을 구현하는 Streamlit 개발 전문 에이전트",
    prompt="""당신은 FISIS Streamlit 앱 개발 전문가입니다.

코드 수정 전 반드시 기존 파일을 읽으세요.

개발 원칙:
- 차트: Plotly만 사용 (matplotlib 금지)
- DB: db/loader.py를 통한 읽기 전용 접근
- 캐시: @st.cache_data 적극 활용
- API 키: .env 관리 (하드코딩 금지)
- 단일 파일 구조 유지 (app.py)

주요 파일:
- app.py: 메인 Streamlit 앱
- db/loader.py: DuckDB 연결 및 쿼리
- llm/sql_generator.py: Text-to-SQL 파이프라인
- llm/prompts.py: 시스템 프롬프트
""",
    tools=["Read", "Edit", "Glob", "Grep", "Bash"],
)

REPORT_AGENT = AgentDefinition(
    description="분석 결과를 마크다운 보고서로 작성하는 전문 에이전트",
    prompt="""당신은 금융 분석 보고서 작성 전문가입니다.

주어진 분석 결과를 바탕으로 마크다운 보고서를 작성하고 저장하세요.
저장 경로: reports/YYYYMMDD_보고서명.md

보고서 구조:
1. 요약 (Executive Summary)
2. 주요 지표 현황 (표 포함)
3. 트렌드 분석
4. 위험 요인
5. 결론 및 시사점

오늘 날짜: 2026-03-30
""",
    tools=["Write", "Bash"],
)

# ── 오케스트레이터 ────────────────────────────────────────────────────────────

ORCHESTRATOR_SYSTEM = """당신은 FISIS 금융 분석 프로젝트의 오케스트레이터입니다.
전문 에이전트팀을 조율해 작업을 효율적으로 완료하세요.

에이전트팀:
- data-agent    : DuckDB 금융 데이터 조회
- insight-agent : 금융 패턴 분석 및 인사이트 도출
- viz-agent     : Plotly 시각화 생성 (reports/charts/*.html)
- dev-agent     : app.py 기능 개발 및 수정
- report-agent  : 분석 보고서 작성 (reports/*.md)

작업 흐름:
1. 데이터 조회가 필요하면 → data-agent
2. 패턴/인사이트 분석이 필요하면 → insight-agent
3. 시각화가 필요하면 → viz-agent
4. 앱 기능 개발이 필요하면 → dev-agent
5. 보고서 작성이 필요하면 → report-agent

복잡한 작업은 에이전트를 순서대로 호출해 파이프라인으로 처리하세요.
예: data-agent → insight-agent → viz-agent → report-agent
"""


async def run_team(task: str) -> None:
    print(f"\n{'='*60}")
    print(f" FISIS 에이전트팀")
    print(f"{'='*60}")
    print(f" 작업: {task}")
    print(f"{'='*60}\n")

    async for message in query(
        prompt=task,
        options=ClaudeAgentOptions(
            cwd="/home/user/fisis-app",
            allowed_tools=["Bash", "Read", "Edit", "Write", "Glob", "Grep", "Agent"],
            permission_mode="acceptEdits",
            system_prompt=ORCHESTRATOR_SYSTEM,
            agents={
                "data-agent": DATA_AGENT,
                "insight-agent": INSIGHT_AGENT,
                "viz-agent": VIZ_AGENT,
                "dev-agent": DEV_AGENT,
                "report-agent": REPORT_AGENT,
            },
            max_turns=50,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text, end="", flush=True)
        elif isinstance(message, ResultMessage):
            print(f"\n\n{'='*60}")
            print(f" 완료")
            print(f"{'='*60}")
            print(message.result)


def main() -> None:
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        task = (
            "프로젝트 현황을 파악하세요:\n"
            "1. data-agent로 리스사(K) 자산 상위 5개사 최신 데이터를 조회하세요\n"
            "2. insight-agent로 데이터를 분석하세요\n"
            "3. viz-agent로 자산 순위 막대 차트를 생성하세요\n"
            "4. report-agent로 간단한 현황 보고서를 작성하세요"
        )
    anyio.run(run_team, task)


if __name__ == "__main__":
    main()
