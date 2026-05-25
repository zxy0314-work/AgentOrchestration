"""Tests for queue pause manager."""
import pytest
from src.orchestrator.queue_pause import QueuePauseManager, QueuePauseState


class TestQueuePauseManager:
    def test_initial_state(self):
        mgr = QueuePauseManager()
        assert mgr.state == QueuePauseState.RUNNING

    def test_can_dequeue_when_running(self):
        mgr = QueuePauseManager()
        assert mgr.can_dequeue() is True

    def test_cannot_dequeue_when_paused(self):
        mgr = QueuePauseManager()
        mgr._state = QueuePauseState.PAUSED
        assert mgr.can_dequeue() is False

    def test_cannot_dequeue_when_draining(self):
        mgr = QueuePauseManager()
        mgr._state = QueuePauseState.DRAINING
        assert mgr.can_dequeue() is False

    def test_resume_from_paused(self):
        mgr = QueuePauseManager()
        mgr._state = QueuePauseState.PAUSED
        mgr.resume()
        assert mgr.state == QueuePauseState.RUNNING

    def test_in_flight_tracking(self):
        mgr = QueuePauseManager()
        mgr.register_in_flight("task-1")
        mgr.complete_in_flight("task-1")
        assert len(mgr._in_flight) == 0

    def test_register_handles_duplicates(self):
        mgr = QueuePauseManager()
        mgr.register_in_flight("task-1")
        mgr.register_in_flight("task-1")  # should not error
        assert len(mgr._in_flight) == 1

    def test_complete_non_existent(self):
        mgr = QueuePauseManager()
        mgr.complete_in_flight("ghost-task")  # should not error

    def test_reset(self):
        mgr = QueuePauseManager()
        mgr._state = QueuePauseState.PAUSED
        mgr.register_in_flight("task-1")
        mgr.reset()
        assert mgr.state == QueuePauseState.RUNNING
        assert mgr.can_dequeue() is True

    def test_double_resume_noop(self):
        mgr = QueuePauseManager()
        mgr.resume()  # already running
        assert mgr.state == QueuePauseState.RUNNING

    def test_double_pause_noop(self):
        mgr = QueuePauseManager()
        mgr._state = QueuePauseState.PAUSED
        import asyncio
        result = asyncio.run(mgr.request_pause())
        assert result is True
        assert mgr.state == QueuePauseState.PAUSED
