"""DART 일일 요청 quota 추적.

DART OpenAPI는 기본 20,000건/일 한도를 갖습니다.
클라이언트 측에서 요청 횟수를 기록하여 한도 근접 시 경고를 제공합니다.

- 날짜별 카운터를 디스크에 저장 (~/.cache/financial_data_mcp/dart_quota.json)
- 캐시 hit은 카운트하지 않음 (실제 네트워크 호출만)
- 30일치 이력 유지
- 서버 재시작 간에 지속
- 여러 세션이 동시에 써도 대충 맞게 동작 (원자적 업데이트 아님 - 로컬 MCP 특성상 충돌 드묾)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_DAILY_LIMIT = 20_000
HISTORY_DAYS = 30
QUOTA_FILE = Path.home() / ".cache" / "financial_data_mcp" / "dart_quota.json"


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _load() -> dict[str, int]:
    """디스크에서 quota 데이터 로드. 실패 시 빈 dict 반환."""
    try:
        if not QUOTA_FILE.exists():
            return {}
        data = json.loads(QUOTA_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        # int 값만 유지
        return {k: int(v) for k, v in data.items() if isinstance(v, (int, float))}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _save(data: dict[str, int]) -> None:
    """디스크에 quota 데이터 저장. 실패 시 조용히 무시."""
    try:
        QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
        QUOTA_FILE.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def _prune(data: dict[str, int]) -> dict[str, int]:
    """30일보다 오래된 날짜 제거."""
    cutoff = (datetime.now() - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    return {k: v for k, v in data.items() if k >= cutoff}


class QuotaTracker:
    """날짜별 요청 횟수 추적. 디스크 persist."""

    def __init__(
        self,
        daily_limit: int = DEFAULT_DAILY_LIMIT,
        quota_file: Path | None = None,
    ) -> None:
        self.daily_limit = daily_limit
        self.quota_file = quota_file or QUOTA_FILE
        self._data: dict[str, int] = {}
        self._dirty_count = 0
        self._load()

    def _load(self) -> None:
        try:
            if self.quota_file.exists():
                raw = json.loads(self.quota_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = {
                        k: int(v)
                        for k, v in raw.items()
                        if isinstance(v, (int, float))
                    }
        except (json.JSONDecodeError, OSError, ValueError):
            self._data = {}

    def _save(self) -> None:
        try:
            self.quota_file.parent.mkdir(parents=True, exist_ok=True)
            self.quota_file.write_text(
                json.dumps(self._data, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def increment(self) -> int:
        """오늘 카운터 1 증가. 현재 카운트 반환.

        디스크 쓰기는 10회마다 1번만 수행 (I/O 버퍼링).
        """
        today = _today()
        self._data[today] = self._data.get(today, 0) + 1
        self._dirty_count += 1
        # 10회마다 또는 한도 근접 시에만 디스크에 저장
        if self._dirty_count >= 10 or self.is_near_limit():
            self._data = _prune(self._data)
            self._save()
            self._dirty_count = 0
        self._save()
        return self._data[today]

    def today_count(self) -> int:
        return self._data.get(_today(), 0)

    def remaining(self) -> int:
        return max(0, self.daily_limit - self.today_count())

    def is_near_limit(self, threshold: float = 0.9) -> bool:
        """남은 quota가 threshold 미만이면 True."""
        return self.today_count() >= self.daily_limit * threshold

    def status(self) -> dict[str, Any]:
        """전체 상태 요약."""
        today = _today()
        count = self._data.get(today, 0)
        # 최근 7일 이력
        history_keys = sorted(self._data.keys(), reverse=True)[:7]
        history = {k: self._data[k] for k in history_keys}
        return {
            "today": today,
            "today_count": count,
            "daily_limit": self.daily_limit,
            "remaining": max(0, self.daily_limit - count),
            "usage_pct": round(count / self.daily_limit * 100, 1)
            if self.daily_limit > 0
            else 0,
            "near_limit": count >= self.daily_limit * 0.9,
            "history_last_7_days": history,
        }

    def reset_today(self) -> None:
        """오늘 카운터 리셋 (테스트용)."""
        self._data.pop(_today(), None)
        self._save()
