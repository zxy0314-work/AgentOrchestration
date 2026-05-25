"""Tests for AgentRuntime.heartbeat — prevents reviving completed runs."""

import pytest
from src.agent.runtime import AgentRuntime, RuntimeState


class TestAgentHeartbeat:
    """Verify that heartbeat() respects terminal states and doesn't revive runs."""

    def setup_method(self):
        self.runtime = AgentRuntime()

    # ------------------------------------------------------------------
    # Fresh / unknown agent
    # ------------------------------------------------------------------

    def test_heartbeat_unknown_agent_is_ignored(self):
        """An agent that was never started has no state → defaults to STOPPED."""
        assert self.runtime.heartbeat("unknown") is False

    # ------------------------------------------------------------------
    # Terminal states are rejected
    # ------------------------------------------------------------------

    def test_heartbeat_rejected_when_stopped(self):
        """STOPPED is a terminal state — heartbeat must be ignored."""
        self.runtime._states["agent-a"] = RuntimeState.STOPPED
        assert self.runtime.heartbeat("agent-a") is False
        # State must remain STOPPED
        assert self.runtime.get_state("agent-a") == RuntimeState.STOPPED

    def test_heartbeat_rejected_when_crashed(self):
        """CRASHED is a terminal state — heartbeat must be ignored."""
        self.runtime._states["agent-b"] = RuntimeState.CRASHED
        assert self.runtime.heartbeat("agent-b") is False
        assert self.runtime.get_state("agent-b") == RuntimeState.CRASHED

    # ------------------------------------------------------------------
    # Active states accept heartbeats
    # ------------------------------------------------------------------

    def test_heartbeat_accepted_when_running(self):
        """RUNNING agents accept and refresh heartbeats."""
        self.runtime._states["agent-c"] = RuntimeState.RUNNING
        assert self.runtime.heartbeat("agent-c") is True
        assert self.runtime.get_state("agent-c") == RuntimeState.RUNNING

    def test_heartbeat_accepted_when_starting(self):
        """STARTING agents accept heartbeats and transition to RUNNING."""
        self.runtime._states["agent-d"] = RuntimeState.STARTING
        assert self.runtime.heartbeat("agent-d") is True
        # State should advance to RUNNING
        assert self.runtime.get_state("agent-d") == RuntimeState.RUNNING

    def test_heartbeat_accepted_when_stopping(self):
        """STOPPING agents still accept heartbeats (not yet fully stopped)."""
        self.runtime._states["agent-e"] = RuntimeState.STOPPING
        assert self.runtime.heartbeat("agent-e") is True
        assert self.runtime.get_state("agent-e") == RuntimeState.RUNNING

    # ------------------------------------------------------------------
    # Heartbeat does NOT revive after stop() completes
    # ------------------------------------------------------------------

    def test_heartbeat_does_not_revive_after_stop(self):
        """After a full stop cycle, a late heartbeat must be ignored."""
        # Simulate agent start (no real subprocess needed)
        self.runtime._states["agent-f"] = RuntimeState.RUNNING
        self.runtime._processes["agent-f"] = None  # placeholder

        # Simulate the stop completing — marks as STOPPED
        self.runtime._states["agent-f"] = RuntimeState.STOPPED

        # Late heartbeat arrives — must be rejected
        assert self.runtime.heartbeat("agent-f") is False
        assert self.runtime.get_state("agent-f") == RuntimeState.STOPPED

    # ------------------------------------------------------------------
    # Idempotency — multiple rejected heartbeats
    # ------------------------------------------------------------------

    def test_multiple_rejected_heartbeats(self):
        """Multiple late heartbeats to a terminal agent are all rejected."""
        self.runtime._states["agent-g"] = RuntimeState.CRASHED
        for _ in range(5):
            assert self.runtime.heartbeat("agent-g") is False
        assert self.runtime.get_state("agent-g") == RuntimeState.CRASHED

    # ------------------------------------------------------------------
    # State isolation — one agent's terminal state doesn't affect others
    # ------------------------------------------------------------------

    def test_state_isolation_across_agents(self):
        """Heartbeat for a live agent still works when another is terminal."""
        self.runtime._states["dead"] = RuntimeState.STOPPED
        self.runtime._states["alive"] = RuntimeState.RUNNING

        assert self.runtime.heartbeat("dead") is False
        assert self.runtime.heartbeat("alive") is True

        assert self.runtime.get_state("dead") == RuntimeState.STOPPED
        assert self.runtime.get_state("alive") == RuntimeState.RUNNING