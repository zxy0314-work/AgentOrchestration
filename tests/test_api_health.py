"""Tests for the public /health endpoint — must NOT leak sensitive metadata."""

import time

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app, sanitize_health_response

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Public health response — field allowlist enforcement
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """The /health endpoint must expose ONLY safe fields to the public."""

    SAFE_FIELDS = {"status", "version", "uptime"}
    SENSITIVE_EXAMPLES = {
        "agents",
        "agent_names",
        "internal_config",
        "api_keys",
        "metadata",
        "deployment_id",
        "cluster",
        "namespace",
        "db_host",
        "db_port",
        "redis_host",
        "max_agents",
        "secret",
        "token",
        "password",
    }

    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_contains_only_safe_fields(self, client):
        """The response dict must contain *only* keys in SAFE_FIELDS."""
        resp = client.get("/health")
        body = resp.json()
        for key in body:
            assert key in self.SAFE_FIELDS, (
                f"'{key}' is not in the public-health allowlist {self.SAFE_FIELDS}"
            )

    def test_health_has_required_safe_fields(self, client):
        """status and version should always be present."""
        resp = client.get("/health")
        body = resp.json()
        assert "status" in body
        assert "version" in body
        assert body["status"] == "healthy"
        assert body["version"] == "2.4.1"

    def test_health_uptime_is_positive(self, client):
        """uptime should be a positive number of seconds."""
        resp = client.get("/health")
        body = resp.json()
        uptime = body.get("uptime")
        assert uptime is not None, "uptime field is missing"
        assert isinstance(uptime, (int, float)), "uptime must be numeric"
        assert uptime >= 0, "uptime must be non-negative"

    def test_health_uptime_increases(self, client):
        """Making a second request later should show a larger uptime."""
        app = create_app()
        cli = TestClient(app)

        r1 = cli.get("/health")
        t1 = r1.json()["uptime"]

        time.sleep(0.01)  # ~10 ms should be enough to see a difference

        r2 = cli.get("/health")
        t2 = r2.json()["uptime"]

        assert t2 >= t1, f"uptime did not increase: {t2} < {t1}"

    def test_health_no_sensitive_fields_leaked(self, client):
        """Ensure none of the known sensitive field names appear in the response."""
        resp = client.get("/health")
        body = resp.json()
        for sensitive_key in self.SENSITIVE_EXAMPLES:
            assert sensitive_key not in body, (
                f"Sensitive field '{sensitive_key}' leaked in /health response"
            )

    def test_health_response_is_not_raw_dict(self, client):
        """The health endpoint should not simply return all internal fields."""
        resp = client.get("/health")
        body = resp.json()
        # If we got back more than just the safe fields, something is wrong
        assert len(body) <= len(self.SAFE_FIELDS), (
            f"Response has {len(body)} fields but only {len(self.SAFE_FIELDS)} are safe"
        )

    def test_health_content_type(self, client):
        """Response should be application/json."""
        resp = client.get("/health")
        assert "application/json" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Unit tests for sanitize_health_response() directly
# ---------------------------------------------------------------------------

class TestSanitizeHealthResponse:
    """Direct unit tests for the sanitization utility."""

    def test_strips_agent_names(self):
        raw = {
            "status": "healthy",
            "version": "1.0",
            "uptime": 42.0,
            "agents": ["worker-alpha"],
        }
        safe = sanitize_health_response(raw)
        assert "agents" not in safe

    def test_strips_internal_config(self):
        raw = {
            "status": "healthy",
            "version": "1.0",
            "uptime": 42.0,
            "internal_config": {"db_host": "secret"},
        }
        safe = sanitize_health_response(raw)
        assert "internal_config" not in safe

    def test_strips_api_keys(self):
        raw = {
            "status": "healthy",
            "version": "1.0",
            "uptime": 42.0,
            "api_keys": ["sk-prod-abc123"],
        }
        safe = sanitize_health_response(raw)
        assert "api_keys" not in safe

    def test_strips_metadata(self):
        raw = {
            "status": "healthy",
            "version": "1.0",
            "uptime": 42.0,
            "metadata": {"deployment_id": "dep-xyz"},
        }
        safe = sanitize_health_response(raw)
        assert "metadata" not in safe

    def test_preserves_safe_fields(self):
        raw = {
            "status": "healthy",
            "version": "2.4.1",
            "uptime": 123.45,
        }
        safe = sanitize_health_response(raw)
        assert safe["status"] == "healthy"
        assert safe["version"] == "2.4.1"
        assert safe["uptime"] == 123.45

    def test_unknown_safe_fields_are_stripped(self):
        """Even plausible-looking field names that are NOT in the allowlist must be removed."""
        raw = {
            "status": "healthy",
            "version": "1.0",
            "uptime": 10.0,
            "healthy": True,  # plausible — but NOT in the allowlist (should be "status")
        }
        safe = sanitize_health_response(raw)
        assert "healthy" not in safe

    def test_empty_raw_returns_empty(self):
        assert sanitize_health_response({}) == {}