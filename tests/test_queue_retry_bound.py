"""Tests for bounded retry metadata growth in the task queue."""

import asyncio
import time

import pytest
from src.orchestrator.scheduler import TaskScheduler


class TestQueueRetryBound:
    def setup_method(self):
        self.scheduler = TaskScheduler()

    def test_retry_metadata_initialized_as_empty_list(self):
        """Ensure each task starts with an empty retry_metadata list."""
        task_id = self.scheduler.enqueue({"type": "test"})
        task = asyncio.run(self.scheduler.dequeue())
        assert task is not None
        assert "retry_metadata" in task
        assert task["retry_metadata"] == []

    def test_retry_metadata_appended_on_failure(self):
        """Verify metadata is appended each time a task fails."""
        self.scheduler.enqueue({"type": "test"})
        task = asyncio.run(self.scheduler.dequeue())
        task_id = task["id"]

        # Fail the task
        self.scheduler.fail(task_id, error="Connection timeout")
        task = asyncio.run(self.scheduler.dequeue())
        assert len(task["retry_metadata"]) == 1
        assert task["retry_metadata"][0]["attempt"] == 1
        assert task["retry_metadata"][0]["error"] == "Connection timeout"
        assert "timestamp" in task["retry_metadata"][0]

        # Fail again
        self.scheduler.fail(task["id"], error="Rate limited")
        task = asyncio.run(self.scheduler.dequeue())
        assert len(task["retry_metadata"]) == 2
        assert task["retry_metadata"][1]["attempt"] == 2
        assert task["retry_metadata"][1]["error"] == "Rate limited"

    def test_retry_metadata_capped_at_max(self):
        """Verify retry_metadata is capped and oldest entries are pruned."""
        self.scheduler._max_retry_metadata = 5
        self.scheduler._max_retries = 10

        self.scheduler.enqueue({"type": "test"})
        task = asyncio.run(self.scheduler.dequeue())

        # Fail the task 8 times (exceeding cap of 5)
        for i in range(8):
            task_id = task["id"]
            self.scheduler.fail(task_id, error=f"Error {i + 1}")
            task = asyncio.run(self.scheduler.dequeue())

        # Should only have last 5 entries
        assert len(task["retry_metadata"]) == 5
        # Oldest entry should be from attempt 4 (index 0 of last 5)
        assert task["retry_metadata"][0]["attempt"] == 4
        assert task["retry_metadata"][0]["error"] == "Error 4"
        # Newest entry should be attempt 8
        assert task["retry_metadata"][-1]["attempt"] == 8
        assert task["retry_metadata"][-1]["error"] == "Error 8"

    def test_retry_metadata_includes_timestamps(self):
        """Verify each metadata entry includes a valid timestamp."""
        self.scheduler.enqueue({"type": "test"})
        task = asyncio.run(self.scheduler.dequeue())
        before = time.time()
        self.scheduler.fail(task["id"], error="Timeout")
        task = asyncio.run(self.scheduler.dequeue())
        after = time.time()

        ts = task["retry_metadata"][0]["timestamp"]
        assert before <= ts <= after

    def test_retry_metadata_with_default_error(self):
        """Verify error can be None when not provided."""
        self.scheduler.enqueue({"type": "test"})
        task = asyncio.run(self.scheduler.dequeue())
        self.scheduler.fail(task["id"])
        task = asyncio.run(self.scheduler.dequeue())
        assert task["retry_metadata"][0]["error"] is None

    def test_retry_metadata_preserved_after_exhaustion(self):
        """Verify metadata survives until retries are exhausted."""
        self.scheduler._max_retries = 3
        self.scheduler.enqueue({"type": "test"})
        task = asyncio.run(self.scheduler.dequeue())

        # Fail 3 times (exhaust retries)
        for i in range(3):
            task_id = task["id"]
            self.scheduler.fail(task_id, error=f"Attempt {i + 1} failed")
            task = asyncio.run(self.scheduler.dequeue())

        # After 3rd fail, task should not be re-enqueued (max_retries exhausted)
        # But retry_metadata should have all 3 attempts
        if task is not None:
            # task was dequeued after 3rd fail? Actually on 3rd fail retries=3 >= max_retries=3,
            # so it returns False and the task is NOT enqueued -> dequeue returns None
            pass

        # Actually, let's re-test more carefully
        self.scheduler = TaskScheduler()
        self.scheduler._max_retries = 3
        self.scheduler.enqueue({"type": "test"})
        task = asyncio.run(self.scheduler.dequeue())

        # Fail once — retries=1, gets re-enqueued
        self.scheduler.fail(task["id"], error="Error 1")
        task = asyncio.run(self.scheduler.dequeue())
        assert task["retries"] == 1
        assert len(task["retry_metadata"]) == 1

        # Fail twice — retries=2, gets re-enqueued
        self.scheduler.fail(task["id"], error="Error 2")
        task = asyncio.run(self.scheduler.dequeue())
        assert task["retries"] == 2
        assert len(task["retry_metadata"]) == 2

        # Fail third time — retries=3, NOT re-enqueued
        result = self.scheduler.fail(task["id"], error="Error 3")
        assert result is False
        task = asyncio.run(self.scheduler.dequeue())
        assert task is None  # No more tasks in queue

    def test_default_max_retry_metadata(self):
        """Verify default cap is 20 entries."""
        assert self.scheduler._max_retry_metadata == 20

    def test_prune_removes_oldest_entries(self):
        """Verify pruning removes the oldest, not newest entries."""
        self.scheduler._max_retry_metadata = 3
        self.scheduler._max_retries = 100

        self.scheduler.enqueue({"type": "test"})
        task = asyncio.run(self.scheduler.dequeue())

        # Fail 5 times — cap is 3, so we keep attempts 3, 4, 5
        for i in range(5):
            task_id = task["id"]
            self.scheduler.fail(task_id, error=f"E{i + 1}")
            task = asyncio.run(self.scheduler.dequeue())

        assert len(task["retry_metadata"]) == 3
        assert task["retry_metadata"][0]["attempt"] == 3  # E3
        assert task["retry_metadata"][1]["attempt"] == 4  # E4
        assert task["retry_metadata"][2]["attempt"] == 5  # E5