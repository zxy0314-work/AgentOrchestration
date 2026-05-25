"""Tests that AgentRegistry rejects unknown config fields during registration."""

import pytest
from src.agent.registry import AgentRegistry
from src.common.errors import ConfigurationError


class TestRegistryRejectUnknownFields:
    def setup_method(self):
        self.registry = AgentRegistry()

    def test_register_with_allowed_config_fields(self):
        """Should succeed when config contains only allowed fields."""
        agent_id = self.registry.register(
            "test-agent",
            "worker.processor",
            config={
                "description": "A test worker",
                "owner": "team-alpha",
                "tags": ["worker", "test"],
                "priority": 5,
                "timeout": 300,
                "retries": 3,
                "max_instances": 10,
                "labels": {"env": "staging"},
                "metadata": {"source": "ci"},
                "group": "worker",
                "namespace": "default",
                "environment": "staging",
                "entrypoint": "run.sh",
                "args": ["--verbose"],
                "env_vars": {"DEBUG": "1"},
                "resources": {"cpu": "1", "memory": "512Mi"},
                "version": "2.0.0",
            },
        )
        agent = self.registry.get(agent_id)
        assert agent is not None
        assert agent["config"]["description"] == "A test worker"

    def test_register_rejects_single_unknown_field(self):
        """Should raise ConfigurationError for a single unknown field."""
        with pytest.raises(ConfigurationError) as exc_info:
            self.registry.register(
                "bad-agent",
                "worker",
                config={"unknown_field": "value"},
            )
        assert "unknown_field" in str(exc_info.value)
        assert "Allowed fields" in str(exc_info.value)

    def test_register_rejects_multiple_unknown_fields(self):
        """Should raise ConfigurationError listing all unknown fields."""
        with pytest.raises(ConfigurationError) as exc_info:
            self.registry.register(
                "bad-agent",
                "worker",
                config={
                    "drifted_field": "val1",
                    "bogus_option": "val2",
                    "invalid_setting": "val3",
                },
            )
        msg = str(exc_info.value)
        assert "drifted_field" in msg
        assert "bogus_option" in msg
        assert "invalid_setting" in msg
        assert "Allowed fields" in msg

    def test_register_rejects_unknown_with_allowed_mix(self):
        """Should raise even when some fields are valid."""
        with pytest.raises(ConfigurationError) as exc_info:
            self.registry.register(
                "mixed-agent",
                "worker",
                config={
                    "description": "valid",
                    "owner": "valid",
                    "bad_field": "invalid",
                },
            )
        assert "bad_field" in str(exc_info.value)

    def test_register_with_no_config(self):
        """Should succeed when config is None (default)."""
        agent_id = self.registry.register("no-config-agent", "worker")
        assert agent_id is not None
        agent = self.registry.get(agent_id)
        assert agent["config"] == {}

    def test_register_with_empty_config(self):
        """Should succeed when config is an empty dict."""
        agent_id = self.registry.register("empty-config-agent", "worker", config={})
        assert agent_id is not None
        agent = self.registry.get(agent_id)
        assert agent["config"] == {}

    def test_register_rejects_nested_unknown_keys(self):
        """Unknown keys at top level are rejected regardless of nesting."""
        with pytest.raises(ConfigurationError):
            self.registry.register(
                "nested-bad",
                "worker",
                config={
                    "metadata": {"valid": True},
                    "malicious_key": {"nested": "data"},
                },
            )

    def test_register_legacy_fields_still_work(self):
        """Known fields used by existing callers should still work."""
        agent_id = self.registry.register(
            "legacy-agent",
            "worker.processor",
            config={
                "group": "worker",
                "version": "1.0.0",
                "metadata": {"key": "value"},
            },
        )
        assert agent_id is not None
        agent = self.registry.get(agent_id)
        assert agent["config"]["group"] == "worker"