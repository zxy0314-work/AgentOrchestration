"""
Tests for sandbox path traversal protection.

Validates that AgentSandbox.safe_child_path() and child_path()
reject malicious path inputs that could escape the sandbox directory.
"""

import os
import tempfile

import pytest

from src.agent.sandbox import AgentSandbox, SandboxPathError


class TestSafeChildPath:
    """Unit tests for AgentSandbox.safe_child_path()."""

    def setup_method(self):
        self.sandbox = AgentSandbox()

    # ---------- valid cases ----------

    def test_simple_name(self):
        """A plain filename passes validation."""
        assert self.sandbox.safe_child_path("foo.txt") == "foo.txt"

    def test_single_subdirectory(self):
        """A normal relative subdirectory passes."""
        assert self.sandbox.safe_child_path("subdir/file.txt") == "subdir/file.txt"

    def test_nested_subdirectory(self):
        """Deeply nested relative paths pass."""
        assert (
            self.sandbox.safe_child_path("a/b/c/d/e/file.txt")
            == "a/b/c/d/e/file.txt"
        )

    def test_name_with_dots(self):
        """Filenames containing dots (not ..) pass."""
        assert self.sandbox.safe_child_path("data.v2.backup.tar.gz") == "data.v2.backup.tar.gz"

    def test_name_with_hyphen_underscore(self):
        """Filenames with hyphens and underscores pass."""
        assert self.sandbox.safe_child_path("my-file_v2.txt") == "my-file_v2.txt"

    def test_single_component_single_dot(self):
        """A single dot as part of a name is fine."""
        assert self.sandbox.safe_child_path(".hidden") == ".hidden"

    def test_simple_relative_normalized(self):
        """A path like 'foo/bar' normalizes to itself."""
        assert self.sandbox.safe_child_path("foo/bar") == "foo/bar"

    # ---------- rejection: absolute paths ----------

    def test_absolute_path_unix(self):
        """Absolute Unix paths are rejected."""
        with pytest.raises(SandboxPathError, match="absolute"):
            self.sandbox.safe_child_path("/etc/passwd")

    def test_absolute_path_windows(self):
        """Windows-style absolute paths are NOT absolute on Linux; just verify no crash."""
        # On Linux, os.path.isabs("C:\\...") returns False, so this just passes through
        result = self.sandbox.safe_child_path("C:\\Windows\\system32")
        assert isinstance(result, str)

    def test_absolute_path_with_leading_slash(self):
        """Paths starting with / are rejected."""
        with pytest.raises(SandboxPathError, match="absolute"):
            self.sandbox.safe_child_path("/tmp/escape")

    # ---------- rejection: parent traversal ----------

    def test_dotdot_direct(self):
        """'..' alone is rejected."""
        with pytest.raises(SandboxPathError, match="\\.\\."):
            self.sandbox.safe_child_path("..")

    def test_dotdot_prefix(self):
        """'../foo' is rejected."""
        with pytest.raises(SandboxPathError, match="\\.\\."):
            self.sandbox.safe_child_path("../foo")

    def test_dotdot_suffix(self):
        """'foo/..' is rejected."""
        with pytest.raises(SandboxPathError, match="\\.\\."):
            self.sandbox.safe_child_path("foo/..")

    def test_dotdot_deep(self):
        """'foo/bar/../../etc/passwd' is rejected after normalization."""
        with pytest.raises(SandboxPathError, match="\\.\\."):
            self.sandbox.safe_child_path("foo/bar/../../etc/passwd")

    def test_dotdot_multiple(self):
        """'../../../../etc/passwd' is rejected."""
        with pytest.raises(SandboxPathError, match="\\.\\."):
            self.sandbox.safe_child_path("../../../../etc/passwd")

    def test_dotdot_in_middle(self):
        """'foo/../../bar' escapes and is rejected."""
        with pytest.raises(SandboxPathError, match="\\.\\."):
            self.sandbox.safe_child_path("foo/../../bar")

    def test_dotdot_trailing_normalized(self):
        """'foo/bar/..' normalizes to 'foo' but still contains '..' parts."""
        with pytest.raises(SandboxPathError, match="\\.\\."):
            self.sandbox.safe_child_path("foo/bar/..")

    # ---------- rejection: null bytes ----------

    def test_null_byte_in_name(self):
        """Paths containing null bytes are rejected."""
        with pytest.raises(SandboxPathError, match="null byte"):
            self.sandbox.safe_child_path("foo\x00bar")

    def test_null_byte_traversal(self):
        """Combined null byte and traversal is rejected."""
        with pytest.raises(SandboxPathError, match="null byte"):
            self.sandbox.safe_child_path("../etc\x00/passwd")

    # ---------- rejection: empty / edge ----------

    def test_empty_string(self):
        """Empty string is rejected."""
        with pytest.raises(SandboxPathError, match="empty"):
            self.sandbox.safe_child_path("")

    def test_only_slashes(self):
        """A string of only slashes is caught as absolute."""
        with pytest.raises(SandboxPathError, match="absolute"):
            self.sandbox.safe_child_path("//")

    # ---------- regression: known attack patterns ----------

    def test_unicode_normalization_attack(self):
        """Unicode fullwidth solidus is not a path separator on Linux; treated as literal."""
        result = self.sandbox.safe_child_path("\uff0fetc/passwd")
        assert isinstance(result, str)

    def test_dotdot_encoded_as_unicode(self):
        """Unicode characters that might confuse parsers are treated as literal names."""
        result = self.sandbox.safe_child_path("\uff0e\uff0e/etc/passwd")
        # These are Unicode fullwidth periods — not '..' — so they should pass
        assert isinstance(result, str)


class TestChildPath:
    """Integration tests for AgentSandbox.child_path()."""

    def setup_method(self):
        self.sandbox = AgentSandbox()
        self.agent_id = "test-agent"
        self.sandbox.create(self.agent_id)

    def test_valid_child_path(self):
        """A valid child name resolves inside the sandbox."""
        child = self.sandbox.child_path(self.agent_id, "data.txt")
        assert str(child).startswith(str(self.sandbox.get_path(self.agent_id)))
        assert child.name == "data.txt"

    def test_valid_nested_child_path(self):
        """A valid nested child path resolves correctly."""
        child = self.sandbox.child_path(self.agent_id, "subdir/file.txt")
        assert str(child).startswith(str(self.sandbox.get_path(self.agent_id)))
        assert child.name == "file.txt"

    def test_unknown_agent_raises_error(self):
        """child_path on unknown agent_id raises SandboxPathError."""
        with pytest.raises(SandboxPathError, match="No sandbox found"):
            self.sandbox.child_path("unknown-agent", "foo.txt")

    def test_traversal_attempt_raises_error(self):
        """Traversal via child_path raises SandboxPathError."""
        with pytest.raises(SandboxPathError):
            self.sandbox.child_path(self.agent_id, "../../etc/passwd")

    def test_null_byte_in_child_raises_error(self):
        """Null byte in child path raises SandboxPathError."""
        with pytest.raises(SandboxPathError, match="null byte"):
            self.sandbox.child_path(self.agent_id, "foo\x00bar")

    def test_absolute_child_raises_error(self):
        """Absolute path as child raises SandboxPathError."""
        with pytest.raises(SandboxPathError, match="absolute"):
            self.sandbox.child_path(self.agent_id, "/etc/passwd")


class TestCreatePathTraversal:
    """Integration tests ensuring AgentSandbox.create() rejects traversal in agent_id."""

    def setup_method(self):
        self.sandbox = AgentSandbox()

    def test_create_with_dotdot_agent_id(self):
        """create() rejects agent_ids containing '..'."""
        with pytest.raises(SandboxPathError, match="\\.\\."):
            self.sandbox.create("../../etc/passwd")

    def test_create_with_absolute_agent_id(self):
        """create() rejects agent_ids that are absolute paths."""
        with pytest.raises(SandboxPathError, match="absolute"):
            self.sandbox.create("/etc/passwd")

    def test_create_with_null_byte_agent_id(self):
        """create() rejects agent_ids containing null bytes."""
        with pytest.raises(SandboxPathError, match="null byte"):
            self.sandbox.create("foo\x00bar")

    def test_create_with_normal_agent_id(self):
        """create() succeeds with a normal agent_id."""
        path = self.sandbox.create("legit-agent")
        assert path.exists()
        assert path.name == "legit-agent"


class TestOwnership:
    """Tests that resolved paths stay within the sandbox directory."""

    def setup_method(self):
        self.base = tempfile.mkdtemp(prefix="test_ownership_")
        self.sandbox = AgentSandbox(base_path=self.base)
        self.agent_id = "agent-42"
        self.sandbox.create(self.agent_id)
        self.sandbox_dir = str(self.sandbox.get_path(self.agent_id))

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def test_child_path_stays_in_sandbox(self):
        """Resolved child path is always a descendant of sandbox dir."""
        child = self.sandbox.child_path(self.agent_id, "some/nested/file.txt")
        assert str(child).startswith(self.sandbox_dir)

    def test_child_path_does_not_escape_real_fs(self):
        """Even with symlinks or weird names, the path is within sandbox."""
        child = self.sandbox.child_path(self.agent_id, "subdir")
        # Verify no path traversal happened
        resolved = os.path.realpath(child)
        sandbox_real = os.path.realpath(self.sandbox_dir)
        assert resolved.startswith(sandbox_real)

    def test_safe_child_path_multiple_agents_isolation(self):
        """Multiple agents each get their own sandbox, no cross-contamination."""
        agent_a = "agent-a"
        agent_b = "agent-b"
        self.sandbox.create(agent_a)
        self.sandbox.create(agent_b)

        child_a = self.sandbox.child_path(agent_a, "file.txt")
        child_b = self.sandbox.child_path(agent_b, "file.txt")
        assert child_a != child_b
        assert str(child_a).startswith(str(self.sandbox.get_path(agent_a)))
        assert str(child_b).startswith(str(self.sandbox.get_path(agent_b)))