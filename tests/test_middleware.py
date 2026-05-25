"""Tests for API middleware: auth redaction, logging, rate limiting."""

import time
import logging

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.testclient import TestClient

from src.api.middleware import (
    AuthMiddleware,
    LoggingMiddleware,
    RateLimitMiddleware,
    sanitize_request_metadata,
    SENSITIVE_HEADERS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(scope_overrides: dict = None) -> Request:
    """Build a raw Starlette Request for testing sanitize_request_metadata."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 50000),
    }
    if scope_overrides:
        scope.update(scope_overrides)
    return Request(scope)


def _make_app():
    """Create a minimal Starlette app with all three middleware layers."""
    app = Starlette()

    async def ok_endpoint(request):
        return Response("OK", status_code=200)

    async def protected_endpoint(request):
        return Response("protected data", status_code=200)

    async def token_endpoint(request):
        return Response("token", status_code=200)

    async def error_endpoint(request):
        raise ValueError("intentional test error")

    app.add_route("/ok", ok_endpoint, methods=["GET"])
    app.add_route("/api/v2/protected", protected_endpoint, methods=["GET"])
    app.add_route("/api/v2/auth/token", token_endpoint, methods=["GET"])
    app.add_route("/error", error_endpoint, methods=["GET"])

    # Middleware stack: outer → inner = Logging → RateLimit → Auth
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RateLimitMiddleware, max_requests=100, window=60)
    app.add_middleware(AuthMiddleware)

    return app


def _scope_with_headers(headers: dict) -> dict:
    """Convert a dict of headers into a scope dict with raw header bytes."""
    return {
        "headers": [
            (k.lower().encode("latin-1"), v.encode("latin-1"))
            for k, v in headers.items()
        ]
    }


# ---------------------------------------------------------------------------
# Tests: sanitize_request_metadata (fully synchronous)
# ---------------------------------------------------------------------------


class TestSanitizeRequestMetadata:
    """Verify the helper strips sensitive headers and keeps safe ones."""

    def test_strips_authorization(self):
        req = _make_request(_scope_with_headers({
            "Authorization": "Bearer super-secret-token-12345",
            "Accept": "application/json",
        }))
        metadata = sanitize_request_metadata(req)

        assert metadata["method"] == "GET"
        assert metadata["path"] == "/test"
        assert "safe_headers" in metadata
        assert "authorization" not in metadata["safe_headers"]
        # Accept is kept
        assert metadata["safe_headers"]["accept"] == "application/json"

    def test_strips_all_sensitive_headers(self):
        req = _make_request(_scope_with_headers({
            "Authorization": "Bearer abc",
            "Cookie": "session=xyz",
            "X-API-Key": "key-123",
            "Proxy-Authorization": "Basic dGVzdDp0ZXN0",
            "User-Agent": "test-agent",
        }))
        metadata = sanitize_request_metadata(req)

        for h in SENSITIVE_HEADERS:
            assert h not in metadata["safe_headers"], (
                f"Sensitive header '{h}' leaked into safe_headers"
            )
        assert metadata["safe_headers"]["user-agent"] == "test-agent"

    def test_no_headers(self):
        req = _make_request()
        metadata = sanitize_request_metadata(req)
        assert metadata["safe_headers"] == {}
        assert metadata["method"] == "GET"
        assert metadata["path"] == "/test"

    def test_lowercase_variants_stripped(self):
        """Sensitive headers in different cases are still caught."""
        req = _make_request(_scope_with_headers({
            "AUTHORIZATION": "Bearer xyz",
            "authorization": "Bearer xyz",
            "Authorization": "Bearer xyz",
        }))
        metadata = sanitize_request_metadata(req)
        assert "authorization" not in metadata["safe_headers"]
        assert "authorization" not in metadata["safe_headers"]

    def test_client_ip_unknown(self):
        req = _make_request({"client": None})
        metadata = sanitize_request_metadata(req)
        assert metadata["client_ip"] == "unknown"
        assert metadata["method"] == "GET"


# ---------------------------------------------------------------------------
# Tests: LoggingMiddleware — integration via TestClient
# ---------------------------------------------------------------------------


class TestLoggingMiddleware:
    """End-to-end checks that logging works and doesn't leak secrets."""

    def test_normal_request_logged(self, caplog):
        caplog.set_level(logging.INFO)
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/ok")
        assert resp.status_code == 200

        assert len(caplog.records) >= 1
        record = caplog.records[0]
        message = record.getMessage()
        # Should contain method, path, status, duration
        assert "GET" in message
        assert "/ok" in message
        assert "200" in message
        assert "s" in message  # duration suffix (e.g. "0.001s")

    def test_logged_message_does_not_contain_auth_header(self, caplog):
        caplog.set_level(logging.INFO)
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/ok", headers={"Authorization": "Bearer my-secret-token"})
        assert resp.status_code == 200

        # Ensure no log record contains the bearer token string
        for record in caplog.records:
            message = record.getMessage()
            assert "my-secret-token" not in message, (
                f"Authorization token leaked into log: {message}"
            )

    def test_unauthenticated_request_logged_without_token(self, caplog):
        caplog.set_level(logging.INFO)
        app = _make_app()
        client = TestClient(app)

        # /api/v2/protected without valid token → 401
        resp = client.get(
            "/api/v2/protected",
            headers={"Authorization": "Invalid token-value-123"},
        )
        assert resp.status_code == 401

        for record in caplog.records:
            message = record.getMessage()
            assert "token-value-123" not in message, (
                f"Invalid token leaked into log: {message}"
            )
            # Should still log the 401
            if "401" in message:
                break
        else:
            pytest.fail("No log record contained the 401 status code")

    def test_logging_on_rejected_auth_request(self, caplog):
        """Even rejected (401) requests should produce a sanitized log entry."""
        caplog.set_level(logging.INFO)
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/api/v2/protected")
        assert resp.status_code == 401

        found_log = False
        for record in caplog.records:
            message = record.getMessage()
            if "/api/v2/protected" in message and "401" in message:
                found_log = True
                # Ensure the token from Authorization (none here) isn't in log
                assert "Bearer" not in message
        assert found_log, "No log record found for the rejected request"

    def test_multiple_requests_state_isolation(self, caplog):
        """Each request should have its own clean state."""
        caplog.set_level(logging.INFO)
        app = _make_app()
        client = TestClient(app)

        for i in range(10):
            resp = client.get("/ok", headers={"X-Request-Id": str(i)})
            assert resp.status_code == 200

        # Only count our middleware's log records (httpx also logs)
        our_records = [r for r in caplog.records if r.name == "src.api.middleware"]
        assert len(our_records) == 10


# ---------------------------------------------------------------------------
# Tests: Error path — exception during request
# ---------------------------------------------------------------------------


class TestLoggingMiddlewareErrors:
    """Ensure errors are logged without leaking state or sensitive data."""

    def test_exception_logged_without_sensitive_data(self, caplog):
        caplog.set_level(logging.ERROR)
        app = _make_app()
        client = TestClient(app)

        with pytest.raises(Exception):
            client.get("/error", headers={"Authorization": "Bearer my-secret-token"})

        assert len(caplog.records) >= 1
        for record in caplog.records:
            message = record.getMessage()
            assert "my-secret-token" not in message, (
                f"Secret leaked in error log: {message}"
            )
            assert "ValueError" in message or "intentional test error" in message, (
                f"Error details missing from log: {message}"
            )


# ---------------------------------------------------------------------------
# Tests: RateLimitMiddleware
# ---------------------------------------------------------------------------


class TestRateLimitMiddleware:
    """Basic validation that rate limiting still works."""

    def test_rate_limit_block(self):
        app = Starlette()

        async def limit_endpoint(request):
            return Response("ok", status_code=200)

        app.add_route("/limit-test", limit_endpoint, methods=["GET"])

        app.add_middleware(RateLimitMiddleware, max_requests=3, window=60)
        client = TestClient(app)

        # First 3 should succeed
        for _ in range(3):
            resp = client.get("/limit-test")
            assert resp.status_code == 200

        # 4th should be blocked
        resp = client.get("/limit-test")
        assert resp.status_code == 429

    def test_rate_limit_window_expiry(self):
        app = Starlette()

        async def expiry_endpoint(request):
            return Response("ok", status_code=200)

        # Use a very short window
        app.add_route("/limit-expiry", expiry_endpoint, methods=["GET"])
        app.add_middleware(RateLimitMiddleware, max_requests=1, window=1)
        client = TestClient(app)

        resp = client.get("/limit-expiry")
        assert resp.status_code == 200

        resp = client.get("/limit-expiry")
        assert resp.status_code == 429

        # Wait for window to expire
        time.sleep(1.1)
        resp = client.get("/limit-expiry")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: AuthMiddleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    """Basic validation that auth still works."""

    def test_unauthenticated_access_to_protected_route(self):
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/api/v2/protected")
        assert resp.status_code == 401

    def test_authenticated_access_to_protected_route(self):
        app = _make_app()
        client = TestClient(app)

        resp = client.get(
            "/api/v2/protected",
            headers={"Authorization": "Bearer valid-token"},
        )
        assert resp.status_code == 200
        assert resp.text == "protected data"

    def test_auth_token_endpoint_is_open(self):
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/api/v2/auth/token")
        assert resp.status_code == 200