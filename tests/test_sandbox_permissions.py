"""Tests to verify sandbox directories are created with restrictive permissions."""

import os
import stat

import pytest

from src.agent.sandbox import AgentSandbox


class TestSandboxPermissions:
    """Verify that sandbox directories use mode 0o700 (owner-only access)."""

    def test_sandbox_directory_has_restrictive_permissions(self, tmp_path):
        """Created sandbox dir must have permissions no wider than 0o700."""
        base = tmp_path / "sandbox_base"
        sandbox = AgentSandbox(base_path=str(base))

        agent_path = sandbox.create("agent-42")

        assert agent_path.exists()
        assert agent_path.is_dir()

        mode = stat.S_IMODE(os.stat(agent_path).st_mode)
        # 0o700 = only owner can rwx; anything wider (e.g. world-readable) fails
        assert mode == 0o700, (
            f"Expected 0o700 but got {oct(mode)} for {agent_path}"
        )

    def test_multiple_sandboxes_each_have_restrictive_permissions(self, tmp_path):
        """Every sandbox dir, not just the first, gets restrictive perms."""
        base = tmp_path / "multi_base"
        sandbox = AgentSandbox(base_path=str(base))

        paths = [sandbox.create(f"agent-{i}") for i in range(5)]

        for p in paths:
            mode = stat.S_IMODE(os.stat(p).st_mode)
            assert mode == 0o700, (
                f"Expected 0o700 but got {oct(mode)} for {p}"
            )

    def test_sandbox_permissions_survive_exist_ok(self, tmp_path):
        """Re-creating an existing sandbox doesn't loosen permissions."""
        base = tmp_path / "exist_ok_base"
        sandbox = AgentSandbox(base_path=str(base))

        # First creation
        path = sandbox.create("agent-exist")
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o700

        # Second creation (exist_ok=True)
        path2 = sandbox.create("agent-exist")
        mode2 = stat.S_IMODE(os.stat(path2).st_mode)
        assert mode2 == 0o700

    def test_base_path_itself_uses_default_permissions(self, tmp_path):
        """Only sandbox agent dirs get 0o700; the base dir uses default umask."""
        base = tmp_path / "base_dir"
        base.mkdir()  # pre-create with default umask perms
        sandbox = AgentSandbox(base_path=str(base))

        sandbox.create("agent-default")

        # Base directory should not have been restricted by our code
        mode_base = stat.S_IMODE(os.stat(base).st_mode)
        assert mode_base != 0o700, (
            "Base path should not be restricted to 0o700 by sandbox code"
        )
        # Agent directory should be 0o700
        mode_agent = stat.S_IMODE(os.stat(base / "agent-default").st_mode)
        assert mode_agent == 0o700