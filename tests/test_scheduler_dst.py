"""Tests for DST-safe scheduling behavior in TaskScheduler."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src.orchestrator.scheduler import TaskScheduler


class TestDSTTransitions:
    """Verify the scheduler handles daylight saving time correctly.

    Spring-forward: clocks jump forward 1 hour (e.g. 02:00 → 03:00)
    Fall-back: clocks fall back 1 hour (e.g. 02:00 → 01:00)
    """

    @pytest.fixture
    def scheduler(self):
        return TaskScheduler()

    # ── schedule_at with timezone-aware datetimes ──────────────────────

    @pytest.mark.skipif(not ZoneInfo("America/New_York"), reason="ZoneInfo needed")
    def test_schedule_at_spring_forward(self, scheduler):
        """Schedule at a time that would fall into the spring-forward gap."""
        # 2026-03-08 02:30:00 in US/Eastern DOES NOT EXIST
        # (clocks spring forward at 02:00 → 03:00)
        # So 03:30 ET == 08:30 UTC
        tz = ZoneInfo("America/New_York")
        spring_forward = datetime(2026, 3, 8, 3, 30, tzinfo=tz)  # Valid after spring-forward
        task = {"name": "post_dst_task"}
        tid = scheduler.schedule_at(task, spring_forward)
        # Should store as UTC
        stored_time, stored_task = scheduler._scheduled[tid]
        assert stored_time.tzinfo is not None
        assert stored_time.tzinfo.utcoffset(stored_time) == timedelta(0)
        assert stored_time == datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)

    @pytest.mark.skipif(not ZoneInfo("America/New_York"), reason="ZoneInfo needed")
    def test_schedule_at_fall_back(self, scheduler):
        """Schedule at a time that falls back (repeated hour)."""
        # 2026-11-01 01:30:00 in US/Eastern occurs TWICE
        # (clocks fall back at 02:00 → 01:00)
        tz = ZoneInfo("America/New_York")
        fall_back = datetime(2026, 11, 1, 1, 30, tzinfo=tz)
        task = {"name": "dst_fallback"}
        tid = scheduler.schedule_at(task, fall_back)
        stored_time, _ = scheduler._scheduled[tid]
        assert stored_time.tzinfo is not None
        # First occurrence of 01:30 EDT == 05:30 UTC, second == 06:30 UTC
        # ZoneInfo uses the earlier (EDT) offset by default for ambiguous times
        assert stored_time == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)

    # ── schedule() uses relative offset from UTC ─────────────────────

    def test_schedule_relative_delay_utc(self, scheduler):
        """schedule() applies delay from UTC, unaffected by local DST."""
        before = scheduler._utcnow()
        task = {"name": "relative"}
        tid = scheduler.schedule(task, delay=3600)
        stored_time, _ = scheduler._scheduled[tid]
        after = scheduler._utcnow()
        expected_delay = timedelta(seconds=3600)
        assert before + expected_delay <= stored_time <= after + expected_delay + timedelta(seconds=1)

    # ── Naive datetime handling ─────────────────────────────────────

    def test_schedule_at_naive_datetime(self, scheduler):
        """Naive datetimes are treated as local time and converted to UTC."""
        from datetime import datetime
        naive = datetime(2026, 6, 15, 12, 0, 0)  # No tzinfo
        task = {"name": "naive_local"}
        tid = scheduler.schedule_at(task, naive)
        stored_time, _ = scheduler._scheduled[tid]
        assert stored_time.tzinfo is not None
        assert stored_time.tzinfo.utcoffset(stored_time) == timedelta(0)

    # ── dequeue promotes expired scheduled tasks ────────────────────

    def test_scheduled_task_promoted_to_queue(self, scheduler):
        """Scheduled tasks whose UTC time has passed are dequeued."""
        past_time = scheduler._utcnow() - timedelta(seconds=10)
        task = {"name": "past_task"}
        scheduler._scheduled["test_id"] = (past_time, task)
        result = asyncio_run(scheduler.dequeue("default"))
        assert result is not None
        assert result["name"] == "past_task"

    def test_future_scheduled_task_not_promoted(self, scheduler):
        """Scheduled tasks in the future should not be dequeued yet."""
        future_time = scheduler._utcnow() + timedelta(hours=1)
        task = {"name": "future_task"}
        scheduler._scheduled["test_id"] = (future_time, task)
        result = asyncio_run(scheduler.dequeue("default"))
        assert result is None


def asyncio_run(coro):
    """Helper: run a coroutine synchronously."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already in an event loop — create a new one in a thread
    import threading
    result = []
    error = []
    def _run():
        try:
            r = asyncio.run(coro)
            result.append(r)
        except Exception as e:
            error.append(e)
    t = threading.Thread(target=_run)
    t.start()
    t.join()
    if error:
        raise error[0]
    return result[0]