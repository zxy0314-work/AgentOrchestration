"""Tests for archived-run rejection guard (issue #3880)."""

import pytest
import asyncio
from unittest.mock import MagicMock, patch

from src.agent.registry import AgentRegistry, AgentStatus, ARCHIVED_STATES


class TestArchivedRunGuard:

    def setup_method(self):
        self.registry = AgentRegistry()
        self.agent_id = self.registry.register("test-agent", "worker.test")

    def test_archive_agent(self):
        """An agent can be archived."""
        assert self.registry.archive(self.agent_id)
        assert self.registry.is_archived(self.agent_id)

    def test_archived_states_constant(self):
        """ARCHIVED_STATES contains expected terminal states."""
        assert AgentStatus.COMPLETED in ARCHIVED_STATES
        assert AgentStatus.FAILED in ARCHIVED_STATES
        assert AgentStatus.ARCHIVED in ARCHIVED_STATES
        assert AgentStatus.RUNNING not in ARCHIVED_STATES
        assert AgentStatus.PENDING not in ARCHIVED_STATES

    def test_reject_status_change_on_archived_agent(self):
        """Status changes on archived agents must be rejected."""
        # Archive the agent
        self.registry.archive(self.agent_id)
        assert self.registry.is_archived(self.agent_id)

        # Try to change status — should be rejected
        result = self.registry.update_status(self.agent_id, AgentStatus.RUNNING)
        assert result is False, "Status change on archived agent should be rejected"

        # Verify status hasn't changed
        agent = self.registry.get(self.agent_id)
        assert agent["status"] == AgentStatus.ARCHIVED.value

    def test_reject_status_change_on_completed_agent(self):
        """Status changes on completed agents must be rejected."""
        # Move agent to COMPLETED state via direct update (bypassing guard for setup)
        self.registry._agents[self.agent_id]["status"] = AgentStatus.COMPLETED.value

        # Try to change status — should be rejected
        result = self.registry.update_status(self.agent_id, AgentStatus.RUNNING)
        assert result is False
        assert self.registry.get(self.agent_id)["status"] == AgentStatus.COMPLETED.value

    def test_reject_status_change_on_failed_agent(self):
        """Status changes on failed agents must be rejected."""
        self.registry._agents[self.agent_id]["status"] = AgentStatus.FAILED.value

        result = self.registry.update_status(self.agent_id, AgentStatus.RUNNING)
        assert result is False
        assert self.registry.get(self.agent_id)["status"] == AgentStatus.FAILED.value

    def test_rejection_recorded_in_event_log(self):
        """Rejections should be recorded in the event log for audit."""
        self.registry.archive(self.agent_id)
        self.registry.update_status(self.agent_id, AgentStatus.RUNNING)

        events = self.registry.get_events(self.agent_id)
        assert len(events) > 0
        last_event = events[-1]
        assert last_event["type"] == "status_change_rejected"
        assert last_event["reason"] == "archived_run"
        assert "attempted_status" in last_event
        assert "current_status" in last_event

    def test_event_log_bounded(self):
        """Event log should be bounded to prevent unbounded growth."""
        self.registry.archive(self.agent_id)
        # Try 150 updates — only 100 should be retained
        for i in range(150):
            self.registry.update_status(self.agent_id, AgentStatus.RUNNING)

        events = self.registry.get_events(self.agent_id)
        assert len(events) <= 100, f"Event log not bounded: {len(events)} events"

    def test_valid_transition_succeeds(self):
        """Valid status transitions on non-archived agents should succeed."""
        result = self.registry.update_status(self.agent_id, AgentStatus.RUNNING)
        assert result is True
        assert self.registry.get(self.agent_id)["status"] == AgentStatus.RUNNING.value

    def test_revision_increments_on_valid_update(self):
        """Revision number should increment on valid status updates."""
        initial_revision = self.registry.get(self.agent_id).get("revision", 0)
        self.registry.update_status(self.agent_id, AgentStatus.RUNNING)
        new_revision = self.registry.get(self.agent_id).get("revision", 0)
        assert new_revision > initial_revision

    def test_revision_not_incremented_on_rejected_update(self):
        """Revision number should NOT increment on rejected status updates."""
        self.registry.archive(self.agent_id)
        initial_revision = self.registry.get(self.agent_id).get("revision", 0)
        self.registry.update_status(self.agent_id, AgentStatus.RUNNING)
        new_revision = self.registry.get(self.agent_id).get("revision", 0)
        assert new_revision == initial_revision, "Revision incremented despite rejection"

    def test_archive_nonexistent_agent_returns_false(self):
        """Archiving a non-existent agent should return False."""
        assert self.registry.archive("nonexistent-id") is False

    def test_is_archived_for_nonexistent_returns_false(self):
        """Checking archived status for non-existent agent returns False."""
        assert self.registry.is_archived("nonexistent-id") is False
