"""Tests for authentication middleware."""

import pytest
from unittest.mock import MagicMock, AsyncMock
from starlette.requests import Request
from starlette.responses import Response
from starlette.middleware.base import RequestResponseEndpoint

from src.api.middleware import AuthMiddleware


class MockApp:
    """Mock ASGI app for testing middleware."""
    async def __call__(self, scope, receive, send):
        response = Response(content="OK", status_code=200)
        await response(scope, receive, send)


def make_request(path: str, token: str = "") -> Request:
    """Create a mock request with given path and token."""
    headers = {}
    if token:
        headers["Authorization"] = token
    
    scope = {
        "type": "http",
        "method": "GET" if "docs" not in path else "GET",
        "path": path,
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "query_string": b"",
        "client": ("127.0.0.1", 8000),
        "server": ("testserver", 8000),
        "scheme": "http",
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_health_endpoint_no_auth_required():
    """Health endpoint should be accessible without authentication."""
    middleware = AuthMiddleware(MockApp())
    request = make_request("/health")
    
    async def call_next(req):
        return Response(content="OK", status_code=200)
    
    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_api_endpoint_requires_auth():
    """API endpoints should require authentication."""
    middleware = AuthMiddleware(MockApp())
    request = make_request("/api/v2/agents")
    
    async def call_next(req):
        return Response(content="OK", status_code=200)
    
    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_api_endpoint_with_valid_token():
    """API endpoints should accept valid Bearer tokens."""
    middleware = AuthMiddleware(MockApp())
    token = f"Bearer {'x' * 40}"  # Valid-looking token
    request = make_request("/api/v2/agents", token)
    
    async def call_next(req):
        return Response(content="OK", status_code=200)
    
    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_docs_endpoint_requires_auth():
    """Documentation endpoints should require authentication."""
    middleware = AuthMiddleware(MockApp())
    
    for docs_path in ["/api/docs", "/api/redoc", "/openapi.json"]:
        request = make_request(docs_path)
        
        async def call_next(req):
            return Response(content="OK", status_code=200)
        
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 401, f"{docs_path} should require auth"


@pytest.mark.asyncio
async def test_docs_endpoint_with_valid_token():
    """Documentation endpoints should accept valid tokens."""
    middleware = AuthMiddleware(MockApp())
    token = f"Bearer {'x' * 40}"
    
    for docs_path in ["/api/docs", "/api/redoc", "/openapi.json"]:
        request = make_request(docs_path, token)
        
        async def call_next(req):
            return Response(content="OK", status_code=200)
        
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 200, f"{docs_path} should accept valid tokens"


@pytest.mark.asyncio
async def test_rejects_stale_tokens():
    """Stale/expired tokens should be rejected with 401."""
    middleware = AuthMiddleware(MockApp())
    
    for stale_token in ["stale_abc123", "expired_xyz789", "revoked_def456"]:
        request = make_request("/api/v2/agents", f"Bearer {stale_token}")
        
        async def call_next(req):
            return Response(content="OK", status_code=200)
        
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 401, f"Stale token {stale_token[:10]}... should be rejected"


@pytest.mark.asyncio
async def test_rejects_malformed_tokens():
    """Malformed tokens should be rejected."""
    middleware = AuthMiddleware(MockApp())
    
    bad_tokens = [
        "Basic xyz123",
        "Bearer ",  # empty token
        "Bearer short",  # too short
        "NotABearer token",
        "",  # empty header
        "Bearer token with spaces",
    ]
    
    for bad in bad_tokens:
        request = make_request("/api/v2/agents", bad)
        
        async def call_next(req):
            return Response(content="OK", status_code=200)
        
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 401, f"Bad token '{bad[:20]}' should be rejected"


@pytest.mark.asyncio
async def test_anonymous_token_denied_for_docs():
    """Anonymous tokens should be denied access to documentation endpoints."""
    middleware = AuthMiddleware(MockApp())
    
    for anon_token in ["anonymous_test123", "guest_token_abc", "temp_session_xyz"]:
        request = make_request("/api/docs", f"Bearer {anon_token}")
        
        async def call_next(req):
            return Response(content="OK", status_code=200)
        
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 403, f"Anonymous token '{anon_token[:15]}...' should be denied docs"


@pytest.mark.asyncio
async def test_auth_token_endpoint_exempt():
    """The /auth/token endpoint should be exempt from auth."""
    middleware = AuthMiddleware(MockApp())
    request = make_request("/api/v2/auth/token")
    
    async def call_next(req):
        return Response(content="OK", status_code=200)
    
    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_internal_route_requires_internal_scope():
    """Internal/admin routes should require elevated scope."""
    middleware = AuthMiddleware(MockApp())
    
    # Token without internal scope
    request = make_request("/api/v2/internal/config", f"Bearer {'x' * 40}")
    
    async def call_next(req):
        return Response(content="OK", status_code=200)
    
    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_internal_route_with_admin_scope():
    """Internal routes should allow tokens with admin scope."""
    middleware = AuthMiddleware(MockApp())
    
    token = f"Bearer {'x' * 40}:admin"
    request = make_request("/api/v2/internal/config", token)
    
    async def call_next(req):
        return Response(content="OK", status_code=200)
    
    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 200
