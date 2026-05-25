"""Tests for the Orchestrator SDK client."""

import pytest
from src.sdk.client import OrchestratorClient


class TestOrchestratorClient:
    def setup_method(self):
        self.client = OrchestratorClient(base_url="http://test.local", api_key="test-key")

    def test_register_agent_validates_blank_name(self):
        """Blank agent names should raise ValueError locally before any HTTP request."""
        with pytest.raises(ValueError, match="must not be blank"):
            self.client.register_agent("", "worker.processor")

    def test_register_agent_validates_whitespace_name(self):
        """Whitespace-only agent names should raise ValueError."""
        with pytest.raises(ValueError, match="must not be blank"):
            self.client.register_agent("   ", "worker.processor")

    def test_register_agent_validates_none_name(self):
        """None agent names should raise ValueError."""
        with pytest.raises(ValueError, match="must not be blank"):
            self.client.register_agent(None, "worker.processor")  # type: ignore

    def test_register_agent_trims_valid_name(self):
        """Leading/trailing whitespace in a valid name should be trimmed."""
        # Patch _request to verify the payload
        original_request = self.client._request
        captured = {}

        def mock_request(method, path, data):
            captured["method"] = method
            captured["path"] = path
            captured["data"] = data
            return {"id": "agent-abc", "name": data["name"], "type": data["agent_type"]}

        self.client._request = mock_request  # type: ignore
        try:
            result = self.client.register_agent("  my-agent  ", "worker.processor")
            assert captured["data"]["name"] == "my-agent"
            assert result["name"] == "my-agent"
        finally:
            self.client._request = original_request

    def test_register_agent_passes_valid_name(self):
        """A normal valid name should be passed through unchanged."""
        original_request = self.client._request
        captured = {}

        def mock_request(method, path, data):
            captured["data"] = data
            return {"id": "agent-xyz", "name": data["name"], "type": data["agent_type"]}

        self.client._request = mock_request  # type: ignore
        try:
            result = self.client.register_agent("my-agent", "worker.processor")
            assert captured["data"]["name"] == "my-agent"
            assert result["id"] == "agent-xyz"
            assert result["name"] == "my-agent"
        finally:
            self.client._request = original_request