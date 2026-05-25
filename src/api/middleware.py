"""API middleware components.

Middleware stack (outer to inner):
  1. LoggingMiddleware   - Structured request logging, auth header redaction
  2. RateLimitMiddleware - Rate limiting by client IP
  3. AuthMiddleware       - Bearer token authentication for /api/v2 routes

Sensitive headers (Authorization, Cookie, X-API-Key) are NEVER included
in log output. The sanitized metadata helper explicitly strips them.
"""

import time
import json
import logging
from typing import Callable, Dict, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.common.errors import AuthenticationError, ValidationError

logger = logging.getLogger(__name__)

SENSITIVE_HEADERS = {"authorization", "cookie", "x-api-key", "proxy-authorization"}


def sanitize_request_metadata(request: Request) -> Dict[str, object]:
    """Extract safe, non-sensitive metadata from a request.

    Returns a dict with method, path, client_ip, and any non-sensitive headers.
    The Authorization and other sensitive headers are explicitly excluded.
    """
    client_ip = request.client.host if request.client else "unknown"

    safe_headers = {}
    for key, value in request.headers.items():
        if key.lower() not in SENSITIVE_HEADERS:
            safe_headers[key] = value

    return {
        "method": request.method,
        "path": request.url.path,
        "client_ip": client_ip,
        "safe_headers": safe_headers,
    }


class AuthMiddleware(BaseHTTPMiddleware):
    """Validates Bearer token on /api/v2 routes, skipping /api/v2/auth/token.

    Returns structured JSON error responses with clear messages when
    authentication validation fails, rather than swallowing the error.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path.startswith("/api/v2") and request.url.path != "/api/v2/auth/token":
            auth_header = request.headers.get("Authorization", "")

            # Validation 1: must start with "Bearer "
            if not auth_header.startswith("Bearer "):
                logger.warning(
                    "Auth validation failed: missing or malformed Authorization header "
                    "on %s %s",
                    request.method,
                    request.url.path,
                )
                return Response(
                    status_code=401,
                    content=json.dumps({
                        "error": "invalid_token",
                        "message": "Missing or malformed Authorization header. "
                                   "Expected format: 'Bearer <token>'",
                    }),
                    headers={"Content-Type": "application/json"},
                )

            # Validation 2: token must not be empty after "Bearer "
            token_value = auth_header[len("Bearer "):]
            if not token_value.strip():
                logger.warning(
                    "Auth validation failed: empty token value on %s %s",
                    request.method,
                    request.url.path,
                )
                return Response(
                    status_code=401,
                    content=json.dumps({
                        "error": "invalid_token",
                        "message": "Bearer token value is empty. "
                                   "Expected format: 'Bearer <token>'",
                    }),
                    headers={"Content-Type": "application/json"},
                )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory sliding-window rate limiter."""

    def __init__(self, app, max_requests: int = 100, window: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window
        self._requests: Dict[str, list] = {}

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
    """Logs structured request metadata with sensitive headers redacted.

    Stores sanitized request metadata (without Authorization, Cookie, X-API-Key)
    in ``request.state.metadata`` before passing to the next handler. The
    metadata is cleaned up in a ``finally`` block to prevent state leakage
    across requests.

    Validation and Authentication errors raised by downstream middleware/handlers
    are caught and returned as clear 4xx JSON responses instead of being
    re-raised as silent 500s.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.time()
        response: Optional[Response] = None

        try:
            # Store sanitized metadata BEFORE calling next middleware.
            # Sensitive headers are explicitly excluded from this dict so they
            # can never reach log output even if a downstream handler attaches
            # additional metadata.
            metadata = sanitize_request_metadata(request)
            request.state.metadata = metadata

            response = await call_next(request)

            duration = time.time() - start
            logger.info(
                "%s %s %s %.3fs",
                request.method,
                request.url.path,
                response.status_code,
                duration,
            )
            return response

        except (ValidationError, AuthenticationError) as exc:
            # Validation/Auth errors should produce clear 4xx responses,
            # NOT be re-raised as silent 500s.
            duration = time.time() - start
            status_code = getattr(exc, "status_code", 400)
            logger.warning(
                "Validation failed: %s %s %s %.3fs - %s: %s",
                request.method,
                request.url.path,
                status_code,
                duration,
                type(exc).__name__,
                exc,
            )
            return Response(
                status_code=status_code,
                content=json.dumps({
                    "error": type(exc).__name__,
                    "message": str(exc),
                }),
                headers={"Content-Type": "application/json"},
            )

        except Exception as exc:
            duration = time.time() - start
            status = response.status_code if response else 500
            logger.error(
                "Request failed: %s %s %s %.3fs - %s: %s",
                request.method,
                request.url.path,
                status,
                duration,
                type(exc).__name__,
                exc,
            )
            raise

        finally:
            # Clear request-local state to prevent any accidental leakage
            # of request metadata (including sensitive fields that might have
            # been set by other middleware) across requests.
            if hasattr(request.state, "metadata"):
                del request.state.metadata