"""Tests for the dependency guard."""
import pytest
from src.orchestrator.dependency_guard import DependencyGuard


class TestDependencyGuard:
    def test_no_deps_allows_execution(self):
        guard = DependencyGuard()
        assert guard.can_execute("agent-a") is True

    def test_dep_blocks_execution(self):
        guard = DependencyGuard()
        guard.add_dependency("agent-b", "agent-a")
        assert guard.can_execute("agent-b") is False

    def test_mark_resolved_unblocks_dependent(self):
        guard = DependencyGuard()
        guard.add_dependency("agent-b", "agent-a")
        guard.mark_resolved("agent-a")
        assert guard.can_execute("agent-b") is True

    def test_cycle_detection_self(self):
        guard = DependencyGuard()
        assert guard.add_dependency("agent-a", "agent-a") is False

    def test_cycle_detection_transitive(self):
        guard = DependencyGuard()
        guard.add_dependency("agent-a", "agent-b")
        guard.add_dependency("agent-b", "agent-c")
        assert guard.add_dependency("agent-c", "agent-a") is False

    def test_unresolved_agents(self):
        guard = DependencyGuard()
        guard.add_dependency("agent-b", "agent-a")
        guard.add_dependency("agent-c", "agent-b")
        unresolved = guard.unresolved_agents()
        assert "agent-b" in unresolved
        assert "agent-c" in unresolved

    def test_remove_dependency(self):
        guard = DependencyGuard()
        guard.add_dependency("agent-b", "agent-a")
        guard.remove_dependency("agent-b", "agent-a")
        assert guard.can_execute("agent-b") is True

    def test_dependents_tracked(self):
        guard = DependencyGuard()
        guard.add_dependency("agent-b", "agent-a")
        guard.add_dependency("agent-c", "agent-a")
        assert guard.get_dependents("agent-a") == {"agent-b", "agent-c"}

    def test_reset_clears_all(self):
        guard = DependencyGuard()
        guard.add_dependency("agent-b", "agent-a")
        guard.reset()
        assert guard.can_execute("agent-b") is True
        assert guard.has_unresolved() is False

    def test_validate_no_parallel_allows_independent(self):
        guard = DependencyGuard()
        result = guard._validate_no_parallel(["agent-a", "agent-b", "agent-c"])
        assert result is True

    def test_validate_no_parallel_blocks_dependent(self):
        guard = DependencyGuard()
        guard.add_dependency("agent-b", "agent-a")
        result = guard._validate_no_parallel(["agent-a", "agent-b"])
        assert result is False
