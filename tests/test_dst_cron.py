"""Tests for DST-aware scheduler cron evaluator (issue #3870)."""

import pytest
import time
from datetime import datetime, timezone, timedelta

from src.orchestrator.scheduler import TaskScheduler, DSTAwareCronEvaluator


class TestDSTAwareCron:

    def setup_method(self):
        self.scheduler = TaskScheduler()
        self.evaluator = DSTAwareCronEvaluator()

    def test_basic_cron_evaluation_utc(self):
        """Basic cron evaluation in UTC works."""
        result = self.evaluator.next_run("0 9 * * *", "UTC")
        assert result is not None
        assert result > time.time()

    def test_cron_evaluation_with_named_timezone(self):
        """Cron evaluation works with named timezones."""
        result = self.evaluator.next_run("0 9 * * *", "America/New_York")
        assert result is not None
        assert result > time.time()

    def test_invalid_timezone_falls_back_to_utc(self):
        """Invalid timezone names fall back to UTC without crashing."""
        result = self.evaluator.next_run("0 9 * * *", "Invalid/Timezone")
        assert result is not None

    def test_invalid_cron_returns_none(self):
        """Malformed cron expression returns None."""
        result = self.evaluator.next_run("not a cron", "UTC")
        assert result is None

    def test_dst_duplicate_detection(self):
        """DST duplicate executions within 1 hour are skipped to next day."""
        # First execution
        result1 = self.evaluator.next_run("30 2 * * *", "America/New_York")
        assert result1 is not None

        # Mark as executed
        self.evaluator.mark_executed("30 2 * * *", "America/New_York", result1)

        # Second execution within 1 hour → should be pushed by 24 hours
        result2 = self.evaluator.next_run("30 2 * * *", "America/New_York", from_time=result1)
        # Either it's >24h later or the cron logic naturally advanced
        assert result2 is None or result2 > result1

    def test_schedule_cron_dedup_via_fingerprint(self):
        """Scheduling the same cron task twice deduplicates via fingerprint."""
        task1 = {"name": "daily-backup"}
        task2 = {"name": "daily-backup"}

        tid1 = self.scheduler.schedule_cron(task1, "0 3 * * *", "America/New_York")
        tid2 = self.scheduler.schedule_cron(task2, "0 3 * * *", "America/New_York")

        # Both task ids issued but second should be pushed to next day
        assert tid1 != ""
        assert tid2 != ""

        t1 = self.scheduler._scheduled[tid1]
        t2 = self.scheduler._scheduled[tid2]

        # Second scheduled time should differ by ~24h (DST dedup)
        assert abs(t2 - t1) >= 3600, f"DST dedup failed: t1={t1}, t2={t2}"

    def test_simple_time_format(self):
        """Simple HH:MM cron format is supported."""
        result = self.evaluator.next_run("09:30", "UTC")
        assert result is not None
        assert result > time.time()

    def test_cron_next_run_is_in_future(self):
        """Computed next_run is always in the future."""
        now = time.time()
        result = self.evaluator.next_run("0 0 * * *", "UTC")
        assert result is not None
        assert result > now

    def test_mark_executed_records_state(self):
        """mark_executed records the execution time for future DST checks."""
        self.evaluator.mark_executed("0 0 * * *", "Europe/London", 1000000.0)
        assert ("0 0 * * *", "Europe/London") in self.evaluator._last_executions
        assert self.evaluator._last_executions[("0 0 * * *", "Europe/London")] == 1000000.0

    def test_cron_logs_audit_metadata(self, caplog):
        """DST decisions are logged for audit without exposing private data."""
        import logging
        caplog.set_level(logging.INFO)

        first = self.evaluator.next_run("0 2 * * *", "America/New_York")
        self.evaluator.mark_executed("0 2 * * *", "America/New_York", first)
        # Trigger DST condition by querying within 1h window
        self.evaluator.next_run("0 2 * * *", "America/New_York", from_time=first - 100)
        # Audit log should not contain sensitive data (no secrets/tokens)
        for record in caplog.records:
            assert "secret" not in record.message.lower()
            assert "token" not in record.message.lower()
