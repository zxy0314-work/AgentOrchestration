"""Tests for AgentRegistry — disabled entry filtering."""
import pytest
from src.agent.registry import AgentRegistry, AgentStatus


class TestRegistryDisabledEntries:
    """Verify terminated/disabled agents don't leak into default listings."""

    def test_list_excludes_terminated_by_default(self):
        """list() should not include terminated agents."""
        reg = AgentRegistry()
        aid = reg.register("worker-1", "worker")
        reg.update_status(aid, AgentStatus.TERMINATED)

        all_agents = reg.list()
        assert aid not in [a["id"] for a in all_agents]

    def test_list_includes_terminated_when_requested(self):
        """list(include_terminated=True) should include terminated agents."""
        reg = AgentRegistry()
        aid = reg.register("worker-1", "worker")
        reg.update_status(aid, AgentStatus.TERMINATED)

        all_agents = reg.list(include_terminated=True)
        assert aid in [a["id"] for a in all_agents]

    def test_running_agents_always_appear(self):
        """Normal running agents should appear in default list."""
        reg = AgentRegistry()
        aid = reg.register("worker-1", "worker")
        all_agents = reg.list()
        assert aid in [a["id"] for a in all_agents]

    def test_mixed_registry_filters_properly(self):
        """With both active and terminated agents, only active appear by default."""
        reg = AgentRegistry()
        alive = reg.register("alive-1", "worker")
        dead = reg.register("dead-1", "worker")
        reg.update_status(dead, AgentStatus.TERMINATED)

        result = reg.list()
        ids = [a["id"] for a in result]
        assert alive in ids
        assert dead not in ids

    def test_list_with_status_filter_still_works(self):
        """Explicit status=TERMINATED filter should still return terminated agents."""
        reg = AgentRegistry()
        aid = reg.register("worker-1", "worker")
        reg.update_status(aid, AgentStatus.TERMINATED)

        result = reg.list(status=AgentStatus.TERMINATED)
        assert aid in [a["id"] for a in result]

    def test_list_with_status_and_exclude(self):
        """status=RUNNING should work correctly even without include_terminated."""
        reg = AgentRegistry()
        running = reg.register("run-1", "worker")
        reg.update_status(running, AgentStatus.RUNNING)
        stopped = reg.register("stop-1", "worker")
        reg.update_status(stopped, AgentStatus.STOPPED)
        dead = reg.register("dead-1", "worker")
        reg.update_status(dead, AgentStatus.TERMINATED)

        result = reg.list(status=AgentStatus.RUNNING)
        ids = [a["id"] for a in result]
        assert running in ids
        assert stopped not in ids
        assert dead not in ids

    def test_count_still_includes_terminated(self):
        """count() should still reflect total agents including terminated."""
        reg = AgentRegistry()
        reg.register("a1", "worker")
        a2 = reg.register("a2", "worker")
        reg.update_status(a2, AgentStatus.TERMINATED)
        assert reg.count() == 2  # Total, not filtered