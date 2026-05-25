import pytest
from src.orchestrator.scheduler import TaskScheduler


class TestTaskScheduler:
    def setup_method(self):
        self.scheduler = TaskScheduler()

    def test_enqueue_task(self):
        task_id = self.scheduler.enqueue({"type": "test", "payload": {}})
        assert task_id is not None

    def test_dequeue_task(self):
        self.scheduler.enqueue({"type": "test", "payload": {"data": 1}})
        import asyncio
        task = asyncio.run(self.scheduler.dequeue())
        assert task is not None
        assert task["type"] == "test"

    def test_enqueue_multiple_priorities(self):
        self.scheduler.enqueue({"type": "low"}, priority=1)
        self.scheduler.enqueue({"type": "high"}, priority=10)
        import asyncio
        task = asyncio.run(self.scheduler.dequeue())
        assert task["type"] == "high"

    def test_complete_task(self):
        self.scheduler.enqueue({"type": "test"})
        import asyncio
        task = asyncio.run(self.scheduler.dequeue())
        assert self.scheduler.complete(task["id"])

    def test_fail_task_with_retry(self):
        self.scheduler.enqueue({"type": "test"})
        import asyncio
        task = asyncio.run(self.scheduler.dequeue())
        assert self.scheduler.fail(task["id"])


class TestCatchUpRetention:
    """Tests for the atomic catch-up retention window precondition (#4190)."""

    def test_fresh_scheduled_task_promoted(self):
        """Task within retention window should be promoted on catch-up."""
        import asyncio
        import time
        scheduler = TaskScheduler(retention_window=3600.0)
        # Schedule a task that is already due (delay=0)
        tid = scheduler.schedule({"type": "fresh"}, delay=0)
        # Pretend a tiny amount of time passed
        time.sleep(0.01)
        task = asyncio.run(scheduler.dequeue())
        assert task is not None
        assert task["id"] == tid
        assert task["type"] == "fresh"

    def test_stale_task_beyond_retention_dropped(self):
        """Task older than retention window must be DROPPED, not enqueued."""
        import asyncio
        import time
        scheduler = TaskScheduler(retention_window=60.0)  # 60s window
        # Manually inject a stale scheduled task (scheduled 1 hour ago)
        stale_id = "stale-task-1"
        scheduler._scheduled[stale_id] = time.time() - 3600
        scheduler._task_store[stale_id] = {
            "id": stale_id, "type": "stale", "queue": "default", "priority": 0
        }
        # Trigger catch-up via dequeue
        task = asyncio.run(scheduler.dequeue())
        assert task is None, "Stale task must not be returned"
        assert stale_id not in scheduler._scheduled
        assert stale_id not in scheduler._task_store
        # Queue should be empty (task dropped, not enqueued)
        assert "default" not in scheduler._queues or len(scheduler._queues["default"]) == 0

    def test_catch_up_returns_counts(self):
        """_catch_up should report promoted vs dropped counts atomically."""
        import time
        scheduler = TaskScheduler(retention_window=60.0)
        now = time.time()

        # 2 fresh tasks (within window)
        scheduler._scheduled["fresh-1"] = now - 10
        scheduler._task_store["fresh-1"] = {"id": "fresh-1", "queue": "default", "priority": 0}
        scheduler._scheduled["fresh-2"] = now - 20
        scheduler._task_store["fresh-2"] = {"id": "fresh-2", "queue": "default", "priority": 0}

        # 3 stale tasks (beyond window)
        for i in range(3):
            sid = f"stale-{i}"
            scheduler._scheduled[sid] = now - 3600 - i
            scheduler._task_store[sid] = {"id": sid, "queue": "default", "priority": 0}

        # Not-yet-due task (future)
        scheduler._scheduled["future-1"] = now + 3600
        scheduler._task_store["future-1"] = {"id": "future-1", "queue": "default", "priority": 0}

        result = scheduler._catch_up(now)
        assert result["promoted"] == 2
        assert result["dropped"] == 3
        # Future task untouched
        assert "future-1" in scheduler._scheduled

    def test_atomic_precondition_no_partial_state(self):
        """If a task is dropped, it must NOT appear in queue, scheduled, or task_store.

        Validates the atomic check-and-act: precondition failure → full removal,
        no partial state where the task lingers in one structure.
        """
        import time
        scheduler = TaskScheduler(retention_window=30.0)
        now = time.time()
        sid = "atomic-stale"
        scheduler._scheduled[sid] = now - 9999
        scheduler._task_store[sid] = {"id": sid, "queue": "q1", "priority": 5}

        scheduler._catch_up(now)

        # All three structures must be consistent: task fully gone, never enqueued.
        assert sid not in scheduler._scheduled
        assert sid not in scheduler._task_store
        assert sid not in scheduler._in_flight
        # The queue may not even exist since nothing was enqueued
        q = scheduler._queues.get("q1")
        assert q is None or len(q) == 0

    def test_boundary_exactly_at_retention_window_is_kept(self):
        """A task exactly at the retention boundary (age == window) should be KEPT.

        Drop condition uses strict `<` (scheduled_at < deadline), so equal is kept.
        """
        import time
        scheduler = TaskScheduler(retention_window=100.0)
        now = time.time()
        sid = "boundary"
        # scheduled_at == now - 100 == retention_deadline → NOT strictly less → kept
        scheduler._scheduled[sid] = now - 100.0
        scheduler._task_store[sid] = {"id": sid, "queue": "default", "priority": 0}

        result = scheduler._catch_up(now)
        assert result["promoted"] == 1
        assert result["dropped"] == 0

    def test_default_retention_window(self):
        """Default retention window is 1 hour (3600s)."""
        scheduler = TaskScheduler()
        assert scheduler._retention_window == 3600.0

    def test_custom_retention_window(self):
        """Retention window is configurable via constructor."""
        scheduler = TaskScheduler(retention_window=7200.0)
        assert scheduler._retention_window == 7200.0

    def test_catch_up_preserves_priority_and_queue(self):
        """Promoted tasks must keep their original queue and priority."""
        import asyncio
        import time
        scheduler = TaskScheduler(retention_window=3600.0)
        now = time.time()
        scheduler._scheduled["t1"] = now - 5
        scheduler._task_store["t1"] = {
            "id": "t1", "type": "low", "queue": "work", "priority": 1
        }
        scheduler._scheduled["t2"] = now - 5
        scheduler._task_store["t2"] = {
            "id": "t2", "type": "high", "queue": "work", "priority": 10
        }
        scheduler._catch_up(now)

        # High priority should come out first
        task = asyncio.run(scheduler.dequeue(queue="work"))
        assert task is not None
        assert task["type"] == "high"

    def test_dropped_task_logged(self, caplog):
        """Dropping a stale task should emit a warning log."""
        import logging
        import time
        scheduler = TaskScheduler(retention_window=10.0)
        now = time.time()
        sid = "loggable-stale"
        scheduler._scheduled[sid] = now - 1000
        scheduler._task_store[sid] = {"id": sid, "queue": "default", "priority": 0}

        with caplog.at_level(logging.WARNING, logger="src.orchestrator.scheduler"):
            scheduler._catch_up(now)

        assert any("Dropping stale scheduled task" in r.message for r in caplog.records)
        assert any("catch-up retention precondition failed" in r.message for r in caplog.records)

    def test_mixed_catch_up_atomicity(self):
        """In one catch-up pass, fresh tasks are promoted while stale ones are dropped — no cross-contamination."""
        import asyncio
        import time
        scheduler = TaskScheduler(retention_window=30.0)
        now = time.time()

        # Fresh
        scheduler._scheduled["good-1"] = now - 5
        scheduler._task_store["good-1"] = {"id": "good-1", "type": "good", "queue": "default", "priority": 0}
        # Stale
        scheduler._scheduled["bad-1"] = now - 600
        scheduler._task_store["bad-1"] = {"id": "bad-1", "type": "bad", "queue": "default", "priority": 0}

        result = scheduler._catch_up(now)
        assert result == {"promoted": 1, "dropped": 1}

        task = asyncio.run(scheduler.dequeue())
        assert task is not None
        assert task["id"] == "good-1"
        # No more tasks
        task2 = asyncio.run(scheduler.dequeue())
        assert task2 is None

# 2019-01-09T19:07:03 update

# 2019-02-18T12:30:02 update

# 2019-04-11T16:04:51 update

# 2019-04-17T16:25:46 update

# 2019-05-24T19:32:13 update

# 2019-07-02T12:54:25 update

# 2019-07-03T20:37:00 update

# 2019-08-21T19:37:17 update

# 2019-10-18T10:30:31 update

# 2019-10-25T09:01:38 update

# 2019-10-29T12:59:34 update

# 2019-11-05T10:07:06 update

# 2019-11-11T10:43:52 update

# 2020-01-17T13:40:02 update

# 2020-02-07T14:06:34 update

# 2020-04-03T08:53:40 update

# 2020-04-06T19:36:29 update

# 2020-05-12T11:51:05 update

# 2020-08-17T08:37:15 update

# 2020-09-15T10:39:38 update

# 2020-10-06T11:26:19 update

# 2020-10-21T13:32:43 update

# 2020-12-14T18:18:36 update

# 2020-12-23T17:15:03 update

# 2021-01-25T16:29:00 update

# 2021-02-23T11:23:50 update

# 2021-03-19T12:21:19 update

# 2021-07-29T18:48:25 update

# 2021-08-25T12:46:58 update

# 2021-09-09T16:27:13 update

# 2021-12-16T12:05:30 update

# 2022-05-07T14:05:12 update

# 2022-07-18T20:52:29 update

# 2022-07-31T18:42:26 update

# 2022-09-09T13:10:08 update

# 2023-01-04T15:16:57 update

# 2023-01-17T14:49:04 update

# 2023-02-15T13:51:30 update

# 2023-03-08T09:15:53 update

# 2023-03-23T16:32:20 update

# 2023-03-28T09:32:01 update

# 2023-05-05T17:28:22 update

# 2023-06-01T08:13:52 update

# 2023-06-20T09:58:10 update

# 2023-07-04T16:14:34 update

# 2023-07-17T20:49:40 update

# 2023-12-26T11:49:18 update

# 2024-05-27T11:00:06 update

# 2024-07-04T08:53:03 update

# 2024-07-18T16:19:02 update

# 2024-08-07T09:35:35 update

# 2024-08-22T14:32:14 update

# 2025-05-20T14:19:23 update

# 2025-07-17T17:54:48 update

# 2025-07-28T13:06:30 update

# 2025-12-22T19:05:25 update

# 2026-01-08T18:43:02 update

# 2026-01-12T16:53:28 update

# 2026-04-16T16:58:23 update
