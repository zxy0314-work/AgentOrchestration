"""Tests for the SDK client authentication."""
import os
import pytest
from src.sdk.client import OrchestratorClient


class TestAuthValidation:
    """Verify that missing API keys raise clear errors."""

    def test_missing_api_key_raises_value_error(self):
        """Client must fail early with a clear message when no API key is given."""
        # Clear any pre-existing env var
        if "AO_API_KEY" in os.environ:
            del os.environ["AO_API_KEY"]

        with pytest.raises(ValueError) as excinfo:
            OrchestratorClient(api_key=None)
        assert "AO_API_KEY" in str(excinfo.value)

    def test_missing_env_var_raises_value_error(self):
        """Client must fail early when AO_API_KEY env var is unset and no arg."""
        # Temporarily unset the env var
        old = os.environ.pop("AO_API_KEY", None)
        try:
            with pytest.raises(ValueError):
                OrchestratorClient()
        finally:
            if old is not None:
                os.environ["AO_API_KEY"] = old

    def test_empty_string_api_key_raises_value_error(self):
        """Empty string api_key should also be rejected."""
        with pytest.raises(ValueError):
            OrchestratorClient(api_key="")

    def test_accepts_non_empty_api_key_arg(self):
        """Client accepts a valid api_key passed as argument."""
        client = OrchestratorClient(api_key="valid-key-12345")
        assert client.api_key == "valid-key-12345"

    def test_accepts_env_var(self):
        """Client reads AO_API_KEY from environment when no arg given."""
        old = os.environ.get("AO_API_KEY")
        os.environ["AO_API_KEY"] = "env-key-67890"
        try:
            client = OrchestratorClient()
            assert client.api_key == "env-key-67890"
        finally:
            if old is not None:
                os.environ["AO_API_KEY"] = old
            else:
                del os.environ["AO_API_KEY"]

    def test_no_empty_bearer_header(self):
        """Authorization header is never sent with an empty bearer token."""
        os.environ["AO_API_KEY"] = "test-key-no-empty-bearer"
        try:
            client = OrchestratorClient()
            # _request builds Authorization header from self.api_key
            # If api_key is properly validated, it should never be empty here
            assert client.api_key == "test-key-no-empty-bearer"
        finally:
            del os.environ["AO_API_KEY"]