"""_quota 모듈과 DartClient quota 통합 테스트."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from financial_data_mcp._quota import QuotaTracker
from financial_data_mcp.dart_client import DartClient


# ── QuotaTracker 단위 테스트 ────────────────────────────────────


def test_tracker_starts_at_zero(tmp_path: Path):
    tracker = QuotaTracker(quota_file=tmp_path / "quota.json")
    assert tracker.today_count() == 0
    assert tracker.remaining() == 20_000


def test_tracker_increment(tmp_path: Path):
    tracker = QuotaTracker(quota_file=tmp_path / "quota.json")
    assert tracker.increment() == 1
    assert tracker.increment() == 2
    assert tracker.today_count() == 2
    assert tracker.remaining() == 19_998


def test_tracker_persists_to_disk(tmp_path: Path):
    quota_file = tmp_path / "quota.json"
    t1 = QuotaTracker(quota_file=quota_file)
    t1.increment()
    t1.increment()

    # 새 인스턴스가 동일 파일에서 로드
    t2 = QuotaTracker(quota_file=quota_file)
    assert t2.today_count() == 2


def test_tracker_near_limit(tmp_path: Path):
    tracker = QuotaTracker(
        daily_limit=100, quota_file=tmp_path / "quota.json"
    )
    for _ in range(89):
        tracker.increment()
    assert not tracker.is_near_limit()

    tracker.increment()  # 90
    assert tracker.is_near_limit()


def test_tracker_status_fields(tmp_path: Path):
    tracker = QuotaTracker(
        daily_limit=1000, quota_file=tmp_path / "quota.json"
    )
    for _ in range(250):
        tracker.increment()

    status = tracker.status()
    assert status["today_count"] == 250
    assert status["daily_limit"] == 1000
    assert status["remaining"] == 750
    assert status["usage_pct"] == 25.0
    assert status["near_limit"] is False
    assert len(status["history_last_7_days"]) == 1


def test_tracker_prunes_old_entries(tmp_path: Path):
    quota_file = tmp_path / "quota.json"
    # 35일 전 데이터를 직접 주입
    old_date = (datetime.now() - timedelta(days=35)).strftime("%Y-%m-%d")
    recent_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    quota_file.write_text(json.dumps({old_date: 100, recent_date: 50}))

    tracker = QuotaTracker(quota_file=quota_file)
    # I/O 버퍼링(10회)을 충족시켜 prune+save 발동
    for _ in range(10):
        tracker.increment()

    # 35일 전 데이터는 제거되고 최근 5일 데이터는 유지
    assert old_date not in tracker._data
    assert recent_date in tracker._data


def test_tracker_handles_corrupted_file(tmp_path: Path):
    quota_file = tmp_path / "quota.json"
    quota_file.write_text("not valid json {{{")

    tracker = QuotaTracker(quota_file=quota_file)
    # 깨진 파일은 무시하고 0부터 시작
    assert tracker.today_count() == 0
    assert tracker.increment() == 1


def test_tracker_reset_today(tmp_path: Path):
    tracker = QuotaTracker(quota_file=tmp_path / "quota.json")
    tracker.increment()
    tracker.increment()
    tracker.reset_today()
    assert tracker.today_count() == 0


# ── DartClient 통합 테스트 ──────────────────────────────────────


@pytest.fixture
async def dart_client_with_temp_quota(tmp_path: Path):
    """임시 quota 파일을 쓰는 DartClient."""
    client = DartClient("test-key")
    # 테스트용 quota 파일로 교체
    client.quota = QuotaTracker(quota_file=tmp_path / "quota.json")
    with (
        patch("financial_data_mcp.dart_client.load_disk_cache", return_value=None),
        patch("financial_data_mcp.dart_client.save_disk_cache"),
    ):
        yield client
    await client.aclose()


def _make_resp(payload: dict) -> MagicMock:
    r = MagicMock()
    r.json = MagicMock(return_value=payload)
    r.raise_for_status = MagicMock()
    return r


async def test_successful_call_increments_quota(dart_client_with_temp_quota):
    client = dart_client_with_temp_quota
    resp = _make_resp({"status": "000", "list": []})

    with patch.object(client._client, "get", AsyncMock(return_value=resp)):
        await client.get_financial_statements("00126380", "2024")
        await client.get_financial_statements("00126380", "2023")
        await client.get_financial_statements("00126380", "2022")

    # 3번 네트워크 호출 → quota 3 증가
    assert client.quota.today_count() == 3


async def test_cached_response_does_not_increment_quota(
    dart_client_with_temp_quota,
):
    """동일 파라미터 재호출은 캐시에서 반환되므로 quota 소비 안 함."""
    client = dart_client_with_temp_quota
    resp = _make_resp({"status": "000", "list": []})

    with patch.object(client._client, "get", AsyncMock(return_value=resp)):
        await client.get_financial_statements("00126380", "2024")
        await client.get_financial_statements("00126380", "2024")  # cache hit
        await client.get_financial_statements("00126380", "2024")  # cache hit

    # 첫 호출만 네트워크, 나머지는 캐시 hit
    assert client.quota.today_count() == 1


async def test_failed_call_still_increments_quota(dart_client_with_temp_quota):
    """HTTP 200 이지만 DART status 에러여도 네트워크는 호출됐으므로 카운트."""
    client = dart_client_with_temp_quota
    resp = _make_resp({"status": "013", "message": "조회된 데이터가 없습니다"})

    with patch.object(client._client, "get", AsyncMock(return_value=resp)):
        await client.get_financial_statements("00000000", "2024")

    # 013은 네트워크 호출이 발생했으므로 카운트
    assert client.quota.today_count() == 1


async def test_transport_error_does_not_increment(dart_client_with_temp_quota):
    """네트워크 실패는 quota 카운트 안 함."""
    import httpx

    client = dart_client_with_temp_quota

    with (
        patch.object(
            client._client,
            "get",
            AsyncMock(side_effect=httpx.ConnectError("dns fail")),
        ),
        patch("financial_data_mcp._http.asyncio.sleep", AsyncMock()),
    ):
        with pytest.raises(RuntimeError):
            await client.get_financial_statements("00126380", "2024")

    # 요청 실패 → quota 증가 안 함
    assert client.quota.today_count() == 0


# ── MCP 도구 테스트 ────────────────────────────────────────────


async def test_dart_quota_status_tool_returns_json():
    """dart_quota_status 도구가 JSON 응답을 반환하는지."""
    from financial_data_mcp import server

    mock_client = MagicMock()
    mock_tracker = MagicMock()
    mock_tracker.status.return_value = {
        "today": "2025-04-10",
        "today_count": 150,
        "daily_limit": 20000,
        "remaining": 19850,
        "usage_pct": 0.75,
        "near_limit": False,
        "history_last_7_days": {"2025-04-10": 150},
    }
    mock_client.quota = mock_tracker

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_quota_status()

    data = json.loads(result)
    assert data["today_count"] == 150
    assert data["remaining"] == 19850
    assert "warning" not in data  # near_limit=False이므로


async def test_dart_quota_status_includes_warning_when_near_limit():
    from financial_data_mcp import server

    mock_client = MagicMock()
    mock_tracker = MagicMock()
    mock_tracker.status.return_value = {
        "today": "2025-04-10",
        "today_count": 18500,
        "daily_limit": 20000,
        "remaining": 1500,
        "usage_pct": 92.5,
        "near_limit": True,
        "history_last_7_days": {"2025-04-10": 18500},
    }
    mock_client.quota = mock_tracker

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_quota_status()

    data = json.loads(result)
    assert "warning" in data
    assert "92.5%" in data["warning"]
