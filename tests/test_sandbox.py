"""Tests for Agent Sandbox ResourceLimits validation."""

import pytest
from src.agent.sandbox import ResourceLimits, AgentSandbox


class TestResourceLimits:
    """ResourceLimits constructor guardrails — must reject non-positive values."""

    def test_default_limits_are_valid(self):
        """Default constructor values should be accepted."""
        limits = ResourceLimits()
        assert limits.cpu_time == 60
        assert limits.memory_mb == 512
        assert limits.disk_mb == 100

    def test_explicit_positive_values(self):
        """Explicit positive integers should be accepted."""
        limits = ResourceLimits(cpu_time=30, memory_mb=256, disk_mb=50)
        assert limits.cpu_time == 30
        assert limits.memory_mb == 256
        assert limits.disk_mb == 50

    def test_rejects_zero_cpu_time(self):
        with pytest.raises(ValueError, match="cpu_time"):
            ResourceLimits(cpu_time=0)

    def test_rejects_negative_cpu_time(self):
        with pytest.raises(ValueError, match="cpu_time"):
            ResourceLimits(cpu_time=-1)

    def test_rejects_zero_memory_mb(self):
        with pytest.raises(ValueError, match="memory_mb"):
            ResourceLimits(memory_mb=0)

    def test_rejects_negative_memory_mb(self):
        with pytest.raises(ValueError, match="memory_mb"):
            ResourceLimits(memory_mb=-100)

    def test_rejects_zero_disk_mb(self):
        with pytest.raises(ValueError, match="disk_mb"):
            ResourceLimits(disk_mb=0)

    def test_rejects_negative_disk_mb(self):
        with pytest.raises(ValueError, match="disk_mb"):
            ResourceLimits(disk_mb=-50)

    def test_rejects_float_cpu_time(self):
        with pytest.raises(ValueError, match="cpu_time"):
            ResourceLimits(cpu_time=1.5)

    def test_rejects_none_cpu_time(self):
        with pytest.raises(ValueError, match="cpu_time"):
            ResourceLimits(cpu_time=None)

    def test_rejects_string_memory(self):
        with pytest.raises(ValueError, match="memory_mb"):
            ResourceLimits(memory_mb="512")


class TestAgentSandbox:
    """AgentSandbox basic lifecycle tests."""

    def test_create_sandbox(self, tmp_path):
        sandbox = AgentSandbox(base_path=str(tmp_path))
        path = sandbox.create("agent-1")
        assert path.exists()
        assert path.name == "agent-1"

    def test_create_with_limits(self, tmp_path):
        limits = ResourceLimits(cpu_time=10, memory_mb=128, disk_mb=50)
        sandbox = AgentSandbox(base_path=str(tmp_path))
        path = sandbox.create("agent-2", limits)
        assert path.exists()

    def test_destroy_sandbox(self, tmp_path):
        sandbox = AgentSandbox(base_path=str(tmp_path))
        sandbox.create("agent-3")
        assert sandbox.destroy("agent-3") is True
        assert sandbox.destroy("agent-3") is False

    def test_get_path(self, tmp_path):
        sandbox = AgentSandbox(base_path=str(tmp_path))
        sandbox.create("agent-4")
        path = sandbox.get_path("agent-4")
        assert path is not None
        assert path.exists()

    def test_get_path_nonexistent(self, tmp_path):
        sandbox = AgentSandbox(base_path=str(tmp_path))
        assert sandbox.get_path("ghost") is None

    def test_cleanup_all(self, tmp_path):
        sandbox = AgentSandbox(base_path=str(tmp_path))
        sandbox.create("a")
        sandbox.create("b")
        sandbox.cleanup_all()
        assert sandbox.get_path("a") is None
        assert sandbox.get_path("b") is None

    def test_apply_limits_valid(self, tmp_path):
        sandbox = AgentSandbox(base_path=str(tmp_path))
        sandbox.create("agent-5")
        limits = ResourceLimits(cpu_time=60, memory_mb=512, disk_mb=100)
        # Should not raise
        sandbox.apply_limits("agent-5", limits)

    def test_rejects_invalid_limits_in_create(self, tmp_path):
        sandbox = AgentSandbox(base_path=str(tmp_path))
        with pytest.raises(ValueError, match="cpu_time"):
            ResourceLimits(cpu_time=0)
