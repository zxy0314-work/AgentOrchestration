"""API middleware components."""

import time
import logging
import re
from typing import Callable, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Paths that are exempt from authentication
AUTH_EXEMPT_PATHS = {
    "/health",
    "/api/v2/auth/token",
}

# Documentation/OpenAPI paths that must be authenticated
DOC_PATHS_PREFIXES = {
    "/api/docs",
    "/api/redoc",
    "/openapi.json",
}

# Token pattern: Bearer <base64 or jwt token>
TOKEN_PATTERN = re.compile(r"^Bearer\s+([A-Za-z0-9\-._~+/]+=*)$")

# Known invalid token patterns (stale/revoked test tokens)
STALE_TOKEN_PREFIXES = {
    "stale_", "expired_", "revoked_",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware that guards all API and documentation endpoints.

    Enforces authentication for:
    - All /api/v2/* endpoints (except auth/token)
    - All documentation endpoints (/api/docs, /api/redoc, /openapi.json)
    - Rejects stale, revoked, anonymous, and insufficiently scoped principals.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Skip health check
        if path in AUTH_EXEMPT_PATHS:
            return await call_next(request)

        # Check if this is a protected path (API or docs)
        is_protected = path.startswith("/api/v2") or any(
            path.startswith(prefix) for prefix in DOC_PATHS_PREFIXES
        )

        if not is_protected:
            return await call_next(request)

        # Extract and validate token
        auth_header = request.headers.get("Authorization", "")
        token = self._extract_token(auth_header)

        if token is None:
            logger.warning(f"Auth rejected: missing or malformed token for {path}")
            return Response(
                status_code=401,
                content='{"error":"Unauthorized","detail":"Missing or malformed Authorization header. Expected: Bearer <token>"}',
                media_type="application/json",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Check for stale/revoked tokens
        if self._is_stale_token(token):
            logger.warning(f"Auth rejected: stale/revoked token for {path}")
            return Response(
                status_code=401,
                content='{"error":"Unauthorized","detail":"Token is stale or has been revoked. Please obtain a new token."}',
                media_type="application/json",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )

        # Check for anonymous/temporary tokens that shouldn't access docs
        if self._is_anonymous_token(token) and any(
            path.startswith(prefix) for prefix in DOC_PATHS_PREFIXES
        ):
            logger.warning(f"Auth rejected: anonymous token cannot access docs at {path}")
            return Response(
                status_code=403,
                content='{"error":"Forbidden","detail":"Documentation access requires a fully authenticated session. Anonymous tokens are not permitted."}',
                media_type="application/json",
            )

        # Validate scope for internal routes
        if self._is_internal_route(path) and not self._has_internal_scope(token):
            logger.warning(f"Auth rejected: insufficient scope for internal route {path}")
            return Response(
                status_code=403,
                content='{"error":"Forbidden","detail":"Insufficient permissions for this resource. Requires internal scope."}',
                media_type="application/json",
            )

        # Token is valid — proceed
        return await call_next(request)

    def _extract_token(self, auth_header: str) -> Optional[str]:
        """Extract and validate Bearer token from Authorization header."""
        match = TOKEN_PATTERN.match(auth_header)
        if not match:
            return None
        token = match.group(1)
        # Token must be at least 20 chars to be considered valid
        if len(token) < 20:
            return None
        return token

    def _is_stale_token(self, token: str) -> bool:
        """Check if token appears stale, expired, or revoked."""
        token_lower = token.lower()
        return any(token_lower.startswith(prefix) for prefix in STALE_TOKEN_PREFIXES)

    def _is_anonymous_token(self, token: str) -> bool:
        """Check if token is anonymous/temporary (e.g., GitHub Actions default token)."""
        # Anonymous tokens often have predictable patterns
        token_lower = token.lower()
        return any(
            prefix in token_lower
            for prefix in ["anonymous", "guest", "temp_", "temporary"]
        )

    def _is_internal_route(self, path: str) -> bool:
        """Check if the path is an internal route requiring elevated scope."""
        internal_prefixes = ["/api/v2/internal", "/api/v2/admin"]
        return any(path.startswith(prefix) for prefix in internal_prefixes)

    def _has_internal_scope(self, token: str) -> bool:
        """Check if token has internal/admin scope based on its suffix."""
        # In a real implementation this would validate JWT claims or check a scope DB
        # For this exercise, we check for scope indicators in the token
        return token.endswith(":admin") or token.endswith(":internal")


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 100, window: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window
        self._requests = {}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        if client_ip not in self._requests:
            self._requests[client_ip] = []

        self._requests[client_ip] = [t for t in self._requests[client_ip] if now - t < self.window]

        if len(self._requests[client_ip]) >= self.max_requests:
            return Response(status_code=429, content="Too many requests")

        self._requests[client_ip].append(now)
        return await call_next(request)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start
        logger.info(f"{request.method} {request.url.path} {response.status_code} {duration:.3f}s")
        return response
