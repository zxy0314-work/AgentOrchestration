"""Tests for HeartbeatMonitor — run state guard against late heartbeats."""
import time
import pytest
from src.orchestrator.heartbeat import HeartbeatMonitor, HeartbeatState


class TestHeartbeatMonitor:
    def test_accepts_heartbeat_for_active_run(self):
        monitor = HeartbeatMonitor(stale_threshold=30.0)
        result = monitor.receive_heartbeat("run-1")
        assert result is True
        assert monitor.status("run-1") == HeartbeatState.ACTIVE

    def test_rejects_heartbeat_for_terminal_run(self):
        monitor = HeartbeatMonitor()
        monitor.receive_heartbeat("run-1")
        monitor.mark_terminal("run-1")
        result = monitor.receive_heartbeat("run-1")
        assert result is False
        assert monitor.status("run-1") == HeartbeatState.TERMINAL

    def test_rejects_heartbeat_without_prior_activity(self):
        monitor = HeartbeatMonitor()
        monitor.mark_terminal("run-1")
        result = monitor.receive_heartbeat("run-1")
        assert result is False

    def test_terminal_runs_not_counted_as_active(self):
        monitor = HeartbeatMonitor()
        monitor.receive_heartbeat("run-active")
        monitor.receive_heartbeat("run-done")
        monitor.mark_terminal("run-done")
        assert monitor.active_run_count() == 1

    def test_stalled_run_detected(self):
        monitor = HeartbeatMonitor(stale_threshold=0.01)
        monitor.receive_heartbeat("run-slow")
        time.sleep(0.02)
        assert monitor.is_stalled("run-slow") is True

    def test_mark_terminal_clears_heartbeat_data(self):
        monitor = HeartbeatMonitor()
        monitor.receive_heartbeat("run-1")
        monitor.mark_terminal("run-1")
        # After terminal, it should not be considered stalled either
        assert monitor.is_stalled("run-1") is False  # Terminal != stalled

    def test_reset_clears_all_tracking(self):
        monitor = HeartbeatMonitor()
        monitor.receive_heartbeat("run-1")
        monitor.mark_terminal("run-2")
        monitor.reset("run-1")
        monitor.reset("run-2")
        assert monitor.active_run_count() == 0

    def test_active_run_count_handles_mixed_states(self):
        monitor = HeartbeatMonitor(stale_threshold=60.0)
        monitor.receive_heartbeat("run-a")
        monitor.receive_heartbeat("run-b")
        monitor.receive_heartbeat("run-c")
        monitor.mark_terminal("run-b")
        assert monitor.active_run_count() == 2

    def test_status_returns_stalled_on_no_heartbeat(self):
        monitor = HeartbeatMonitor()
        assert monitor.status("never-seen") == HeartbeatState.STALLED

    def test_stall_and_timeout_detection(self):
        """Verify timeout logic without infinite loop."""
        monitor = HeartbeatMonitor(stale_threshold=0.01)
        monitor.receive_heartbeat("run-1")
        time.sleep(0.02)
        assert monitor.is_stalled("run-1") is True
        assert monitor.status("run-1") == HeartbeatState.STALLED
