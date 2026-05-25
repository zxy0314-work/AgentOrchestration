"""Tests for middleware validation error handling.

Verifies that middleware does NOT swallow validation errors — when validation
fails, the error propagates clearly as a structured 4xx response rather than
being silently consumed or turned into an opaque 500.
"""

import json
import logging

import pytest
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.testclient import TestClient

from src.api.middleware import (
    AuthMiddleware,
    LoggingMiddleware,
    RateLimitMiddleware,
)
from src.common.errors import AuthenticationError, ValidationError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app():
    """Create a minimal Starlette app with all three middleware layers."""
    app = Starlette()

    async def ok_endpoint(request):
        return Response("OK", status_code=200)

    async def protected_endpoint(request):
        return Response("protected data", status_code=200)

    async def token_endpoint(request):
        return Response("token", status_code=200)

    async def validation_error_endpoint(request):
        raise ValidationError("Invalid request parameter: agent_id must be a UUID", status_code=422)

    async def auth_error_endpoint(request):
        raise AuthenticationError("Token has expired")

    async def runtime_error_endpoint(request):
        raise ValueError("internal server issue")

    app.add_route("/ok", ok_endpoint, methods=["GET"])
    app.add_route("/api/v2/protected", protected_endpoint, methods=["GET"])
    app.add_route("/api/v2/auth/token", token_endpoint, methods=["GET"])
    app.add_route("/validation-error", validation_error_endpoint, methods=["GET"])
    app.add_route("/auth-error", auth_error_endpoint, methods=["GET"])
    app.add_route("/runtime-error", runtime_error_endpoint, methods=["GET"])

    # Middleware stack: outer -> inner = Logging -> RateLimit -> Auth
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RateLimitMiddleware, max_requests=100, window=60)
    app.add_middleware(AuthMiddleware)

    return app


# ---------------------------------------------------------------------------
# Tests: AuthMiddleware — structured validation errors (not silent "Unauthorized")
# ---------------------------------------------------------------------------


class TestAuthValidationErrors:
    """AuthMiddleware must return structured JSON errors, not swallow them."""

    def test_no_auth_header_returns_structured_error(self):
        """Missing Authorization header -> 401 with JSON error detail."""
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/api/v2/protected")

        assert resp.status_code == 401
        assert resp.headers["content-type"] == "application/json"
        body = resp.json()
        assert body["error"] == "invalid_token"
        assert "Missing or malformed" in body["message"]
        assert "Bearer" in body["message"]

    def test_bearer_without_token_returns_structured_error(self):
        """'Bearer ' with no token value -> 401 with JSON error detail."""
        app = _make_app()
        client = TestClient(app)

        resp = client.get(
            "/api/v2/protected",
            headers={"Authorization": "Bearer "},
        )

        assert resp.status_code == 401
        assert resp.headers["content-type"] == "application/json"
        body = resp.json()
        assert body["error"] == "invalid_token"
        assert "empty" in body["message"].lower()

    def test_bearer_with_whitespace_only_returns_structured_error(self):
        """'Bearer   ' (whitespace only) -> 401 with JSON error detail."""
        app = _make_app()
        client = TestClient(app)

        resp = client.get(
            "/api/v2/protected",
            headers={"Authorization": "Bearer   "},
        )

        assert resp.status_code == 401
        assert resp.headers["content-type"] == "application/json"
        body = resp.json()
        assert body["error"] == "invalid_token"

    def test_non_bearer_auth_header_returns_structured_error(self):
        """'Basic <token>' -> 401 with JSON error detail."""
        app = _make_app()
        client = TestClient(app)

        resp = client.get(
            "/api/v2/protected",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )

        assert resp.status_code == 401
        assert resp.headers["content-type"] == "application/json"
        body = resp.json()
        assert body["error"] == "invalid_token"

    def test_empty_auth_header_returns_structured_error(self):
        """Empty Authorization header -> 401 with JSON error detail."""
        app = _make_app()
        client = TestClient(app)

        resp = client.get(
            "/api/v2/protected",
            headers={"Authorization": ""},
        )

        assert resp.status_code == 401
        assert resp.headers["content-type"] == "application/json"
        body = resp.json()
        assert body["error"] == "invalid_token"

    def test_valid_token_passes_through(self):
        """Valid Bearer token -> 200 and reaches the handler."""
        app = _make_app()
        client = TestClient(app)

        resp = client.get(
            "/api/v2/protected",
            headers={"Authorization": "Bearer valid-token-12345"},
        )

        assert resp.status_code == 200
        assert resp.text == "protected data"

    def test_auth_token_endpoint_skips_auth(self):
        """/api/v2/auth/token is open and does not require auth."""
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/api/v2/auth/token")

        assert resp.status_code == 200
        assert resp.text == "token"

    def test_non_api_v2_route_skips_auth(self):
        """Routes outside /api/v2 are not subject to auth middleware."""
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/ok")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: LoggingMiddleware — does NOT swallow validation/auth errors as 500s
# ---------------------------------------------------------------------------


class TestLoggingMiddlewareValidationHandling:
    """LoggingMiddleware must catch ValidationError/AuthenticationError
    and return them as 4xx, not re-raise them into 500s."""

    def test_validation_error_returns_422_not_500(self):
        """ValidationError raised downstream -> 422 JSON, not 500."""
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/validation-error")

        assert resp.status_code == 422
        assert resp.headers["content-type"] == "application/json"
        body = resp.json()
        assert body["error"] == "ValidationError"
        assert "agent_id must be a UUID" in body["message"]

    def test_authentication_error_returns_4xx_not_500(self):
        """AuthenticationError raised downstream -> 4xx JSON, not 500."""
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/auth-error")

        assert resp.status_code == 400  # default status_code
        assert resp.headers["content-type"] == "application/json"
        body = resp.json()
        assert body["error"] == "AuthenticationError"
        assert "expired" in body["message"]

    def test_runtime_error_still_bubbles_as_500(self):
        """Unrelated exceptions (ValueError) still re-raise as 500."""
        app = _make_app()
        client = TestClient(app)

        with pytest.raises(Exception) as excinfo:
            client.get("/runtime-error")

        assert "internal server issue" in str(excinfo.value)

    def test_validation_error_logged_as_warning(self, caplog):
        """Validation errors are logged at WARNING level with details."""
        caplog.set_level(logging.WARNING)
        app = _make_app()
        client = TestClient(app)

        client.get("/validation-error")

        found = False
        for record in caplog.records:
            if "Validation failed" in record.getMessage():
                found = True
                assert "ValidationError" in record.getMessage()
                assert "/validation-error" in record.getMessage()
                assert "422" in record.getMessage()
                break
        assert found, "No WARNING log record found for validation error"


# ---------------------------------------------------------------------------
# Tests: Auth validation error logging
# ---------------------------------------------------------------------------


class TestAuthValidationLogging:
    """Auth validation failures should be logged."""

    def test_missing_token_logged_as_warning(self, caplog):
        caplog.set_level(logging.WARNING)
        app = _make_app()
        client = TestClient(app)

        client.get("/api/v2/protected")

        found = False
        for record in caplog.records:
            if "Auth validation failed" in record.getMessage():
                found = True
                assert "/api/v2/protected" in record.getMessage()
                break
        assert found, "No WARNING log record found for auth validation failure"

    def test_empty_token_logged_as_warning(self, caplog):
        caplog.set_level(logging.WARNING)
        app = _make_app()
        client = TestClient(app)

        client.get(
            "/api/v2/protected",
            headers={"Authorization": "Bearer "},
        )

        found = False
        for record in caplog.records:
            if "Auth validation failed" in record.getMessage():
                found = True
                assert "empty token" in record.getMessage().lower()
                break
        assert found, "No WARNING log record found for empty token validation"