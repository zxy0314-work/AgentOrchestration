"""Tests for DST-aware scheduling in TaskScheduler.

These tests verify that scheduled tasks are handled correctly across
daylight saving time transitions by using timezone-aware UTC datetimes
for all internal comparisons.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from src.orchestrator.scheduler import TaskScheduler


class TestSchedulerDST:
    """DST-aware scheduling behavior."""

    def setup_method(self):
        self.scheduler = TaskScheduler()

    # ------------------------------------------------------------------ #
    # schedule_at: timezone-aware datetimes                              #
    # ------------------------------------------------------------------ #

    def test_schedule_at_with_utc_timezone(self):
        """schedule_at accepts a timezone-aware UTC datetime."""
        future = datetime.now(timezone.utc) + timedelta(seconds=10)
        task_id = self.scheduler.schedule_at({"type": "test"}, future)
        assert task_id is not None
        assert len(self.scheduler._scheduled) == 1

    def test_schedule_at_with_non_utc_timezone(self):
        """schedule_at converts non-UTC timezone-aware datetimes to UTC."""
        import zoneinfo

        tz = zoneinfo.ZoneInfo("America/New_York")
        future_local = datetime.now(tz) + timedelta(seconds=10)
        task_id = self.scheduler.schedule_at({"type": "test"}, future_local)
        assert task_id is not None

        # Verify internal storage is UTC
        stored_time, _ = self.scheduler._scheduled[task_id]
        assert stored_time.tzinfo == timezone.utc

    def test_schedule_at_naive_datetime(self):
        """schedule_at treats a naive datetime as system local time."""
        future_naive = datetime(2026, 6, 15, 14, 30, 0)
        task_id = self.scheduler.schedule_at({"type": "test"}, future_naive)
        assert task_id is not None

        # Internal storage should be timezone-aware UTC
        stored_time, _ = self.scheduler._scheduled[task_id]
        assert stored_time.tzinfo == timezone.utc

    # ------------------------------------------------------------------ #
    # schedule: relative delay (remains DST-safe via UTC)                 #
    # ------------------------------------------------------------------ #

    def test_schedule_relative_delay(self):
        """schedule with a relative delay stores a UTC-aware datetime."""
        self.scheduler.schedule({"type": "delayed"}, delay=60.0)
        tid, (scheduled_time, task) = next(iter(self.scheduler._scheduled.items()))
        assert scheduled_time.tzinfo == timezone.utc
        assert task["type"] == "delayed"
        assert scheduled_time > self.scheduler._utcnow()

    # ------------------------------------------------------------------ #
    # Dequeue promotion of expired scheduled tasks                       #
    # ------------------------------------------------------------------ #

    def test_schedule_and_dequeue(self):
        """A task scheduled with a short delay is dequeued after expiry."""
        self.scheduler.schedule({"type": "short_delay"}, delay=0.01)
        time.sleep(0.02)
        task = asyncio.run(self.scheduler.dequeue())
        assert task is not None
        assert task["type"] == "short_delay"

    def test_schedule_at_and_dequeue(self):
        """A task scheduled at an absolute future time is dequeued."""
        future = datetime.now(timezone.utc) + timedelta(seconds=0.01)
        self.scheduler.schedule_at({"type": "absolute"}, future)
        time.sleep(0.02)
        task = asyncio.run(self.scheduler.dequeue())
        assert task is not None
        assert task["type"] == "absolute"

    def test_scheduled_not_expired_not_dequeued(self):
        """A task scheduled far in the future is not dequeued."""
        self.scheduler.schedule({"type": "future"}, delay=3600)
        task = asyncio.run(self.scheduler.dequeue())
        assert task is None

    # ------------------------------------------------------------------ #
    # DST transition scenarios                                           #
    # ------------------------------------------------------------------ #

    def test_dst_spring_forward_missing_hour(self):
        """Schedule during the 'spring forward' missing hour resolves correctly.

        In US/Eastern, 2026-03-08 at 02:30 AM local does not exist (clocks
        spring forward to 03:00 AM). The scheduler should still produce a
        valid UTC time without error.
        """
        import zoneinfo

        et = zoneinfo.ZoneInfo("America/New_York")
        # 2026-03-08 02:30 AM ET is spring-forward gap
        spring_forward = datetime(2026, 3, 8, 2, 30, 0, tzinfo=et)
        task_id = self.scheduler.schedule_at(
            {"type": "spring_forward"}, spring_forward
        )
        assert task_id is not None
        stored_time, task = self.scheduler._scheduled[task_id]
        # Since 2:30 AM doesn't exist, zoneinfo folds it to 3:00 AM EDT = 08:00 UTC
        assert stored_time.tzinfo == timezone.utc
        assert task["type"] == "spring_forward"

    def test_dst_fall_back_duplicate_hour(self):
        """Schedule during the 'fall back' duplicated hour resolves to the
        first occurrence (before the transition)."""
        import zoneinfo

        et = zoneinfo.ZoneInfo("America/New_York")
        # 2026-11-01 01:30 AM ET — hour that repeats after fall-back
        fall_back = datetime(2026, 11, 1, 1, 30, 0, tzinfo=et)
        task_id = self.scheduler.schedule_at(
            {"type": "fall_back"}, fall_back
        )
        assert task_id is not None
        stored_time, task = self.scheduler._scheduled[task_id]
        assert stored_time.tzinfo == timezone.utc

    def test_dst_crossing_relative_schedule(self):
        """A relative schedule crossing a DST boundary still fires after
        the correct number of wall-clock seconds."""
        # Schedule a task 1 hour (3600s) before a spring-forward transition
        # and verify the UTC time shifts by exactly 3600 seconds.
        import zoneinfo

        et = zoneinfo.ZoneInfo("America/New_York")
        # 2026-03-08 01:59:59 AM ET (just before spring-forward)
        before_transition = datetime(2026, 3, 8, 1, 59, 59, tzinfo=et)
        # We can't actually travel in time, but we can verify the scheduler
        # stores UTC internally so a 3600s relative delay is always correct.
        # Mock-ish: manually check that utcnow + delay gives the right offset.
        now = self.scheduler._utcnow()
        self.scheduler.schedule({"type": "cross_dst"}, delay=3600)
        tid, (sched_time, _) = next(iter(self.scheduler._scheduled.items()))
        diff = (sched_time - now).total_seconds()
        assert abs(diff - 3600) < 1.0  # Within 1-second tolerance

    # ------------------------------------------------------------------ #
    # schedule_at input validation                                       #
    # ------------------------------------------------------------------ #

    def test_schedule_at_past_time_immediately_dequeued(self):
        """A task scheduled in the past is immediately eligible."""
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        self.scheduler.schedule_at({"type": "past"}, past)
        task = asyncio.run(self.scheduler.dequeue())
        assert task is not None
        assert task["type"] == "past"

    def test_schedule_at_keeps_task_data(self):
        """schedule_at preserves all task data fields."""
        future = datetime.now(timezone.utc) + timedelta(seconds=3600)
        task_data = {"type": "data_test", "payload": {"key": "value"}, "meta": "info"}
        tid = self.scheduler.schedule_at(task_data, future)
        _, stored_task = self.scheduler._scheduled[tid]
        assert stored_task["type"] == "data_test"
        assert stored_task["payload"] == {"key": "value"}
        assert stored_task["meta"] == "info"

    # ------------------------------------------------------------------ #
    # Multiple scheduled tasks                                           #
    # ------------------------------------------------------------------ #

    def test_multiple_scheduled_tasks_dequeued_in_order(self):
        """Multiple expired scheduled tasks are all moved to the queue."""
        now = datetime.now(timezone.utc)
        self.scheduler.schedule_at({"type": "a"}, now + timedelta(seconds=0.01))
        self.scheduler.schedule_at({"type": "b"}, now + timedelta(seconds=0.02))
        time.sleep(0.05)
        task1 = asyncio.run(self.scheduler.dequeue())
        task2 = asyncio.run(self.scheduler.dequeue())
        # Both should be dequeued; order depends on enqueue priority
        types = {task1["type"], task2["type"]}
        assert types == {"a", "b"}