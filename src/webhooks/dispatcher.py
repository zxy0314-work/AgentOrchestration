"""Webhook event dispatcher with 410 Gone handling.

When a webhook endpoint returns HTTP 410 (Gone), the dispatcher marks that
endpoint as **disabled** and stops sending events to it. This prevents
endless retries against a permanently-gone resource.

Typical usage::

    dispatcher = WebhookDispatcher()

    # Register an active endpoint
    dispatcher.register("https://hooks.example.com/events")

    # Dispatch an event -- the dispatcher POSTs to all active endpoints
    await dispatcher.dispatch("agent.started", {"agent_id": "abc123"})

    # If the endpoint returns 410, it is auto-disabled
    assert not dispatcher.endpoints[0].active
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HTTP_GONE = 410
"""HTTP status code indicating the resource is permanently gone."""

DEFAULT_TIMEOUT = 30.0
"""Default HTTP request timeout in seconds."""

MAX_RETRIES = 3
"""Maximum number of retries for transient failures (not 410s)."""

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
"""Status codes that warrant a retry (with backoff)."""

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class WebhookEvent:
    """An event to be dispatched to webhook endpoints."""

    event_type: str
    payload: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


@dataclass
class WebhookEndpoint:
    """A registered webhook endpoint with runtime state.

    Attributes:
        url:         Target URL for the webhook.
        active:      Whether the endpoint is currently receiving events.
        secret:      Optional HMAC secret for request signing.
        disabled_reason:  Human-readable reason for disablement (if any).
        last_status:      Last HTTP status code received from this endpoint.
        last_attempt:     Timestamp of the last dispatch attempt.
    """

    url: str
    active: bool = True
    secret: Optional[str] = None
    disabled_reason: Optional[str] = None
    last_status: Optional[int] = None
    last_attempt: Optional[float] = None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class WebhookDispatcher:
    """Dispatches events to registered webhook endpoints.

    Features:
        * Automatic disablement on HTTP 410 Gone.
        * Retry with exponential backoff for transient server errors (5xx, 429).
        * Configurable HTTP client (for testing with respx / httpx mocks).
    """

    def __init__(
        self,
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self._endpoints: List[WebhookEndpoint] = []
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout))

        # Optional callbacks invoked after dispatch attempts
        self._on_dispatch: Optional[Callable[[WebhookEndpoint, WebhookEvent, int], None]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, url: str, secret: Optional[str] = None) -> WebhookEndpoint:
        """Register a new webhook endpoint.

        Raises ``ValueError`` if the URL is malformed.
        """
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid webhook URL: {url}")

        endpoint = WebhookEndpoint(url=url, secret=secret)
        self._endpoints.append(endpoint)
        logger.info("Registered webhook endpoint: %s", url)
        return endpoint

    def unregister(self, url: str) -> bool:
        """Remove a registered webhook endpoint. Returns True if found."""
        before = len(self._endpoints)
        self._endpoints = [ep for ep in self._endpoints if ep.url != url]
        return len(self._endpoints) < before

    def disable(self, url: str, reason: str = "Manually disabled") -> bool:
        """Disable a specific endpoint by URL. Returns True if found."""
        for ep in self._endpoints:
            if ep.url == url:
                ep.active = False
                ep.disabled_reason = reason
                logger.warning("Disabled webhook endpoint %s: %s", url, reason)
                return True
        return False

    def enable(self, url: str) -> bool:
        """Re-enable a previously-disabled endpoint. Returns True if found."""
        for ep in self._endpoints:
            if ep.url == url:
                ep.active = True
                ep.disabled_reason = None
                logger.info("Re-enabled webhook endpoint: %s", url)
                return True
        return False

    @property
    def endpoints(self) -> List[WebhookEndpoint]:
        """Return a copy of the registered endpoints list."""
        return list(self._endpoints)

    @property
    def active_endpoints(self) -> List[WebhookEndpoint]:
        """Return only active (non-disabled) endpoints."""
        return [ep for ep in self._endpoints if ep.active]

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, event_type: str, payload: Dict[str, Any]) -> List[WebhookEndpoint]:
        """Dispatch an event to all active webhook endpoints.

        For each endpoint:
          * POST the event as JSON.
          * On HTTP 410 -> immediately disable the endpoint.
          * On transient errors (5xx, 429) -> retry up to ``max_retries`` times
            with exponential backoff.
          * On success -> update ``last_status`` and ``last_attempt``.

        Returns a list of endpoints that were contacted (including those
        that were disabled during dispatch).
        """
        event = WebhookEvent(event_type=event_type, payload=payload)
        contacted: List[WebhookEndpoint] = []

        for endpoint in self._endpoints:
            if not endpoint.active:
                continue

            contacted.append(endpoint)
            status = await self._dispatch_to_endpoint(endpoint, event)

            if status == HTTP_GONE:
                endpoint.active = False
                endpoint.disabled_reason = (
                    "Endpoint returned HTTP 410 Gone -- automatically disabled"
                )
                logger.warning(
                    "Webhook endpoint %s returned 410 Gone -- disabled",
                    endpoint.url,
                )

            endpoint.last_status = status
            endpoint.last_attempt = time.time()

            if self._on_dispatch:
                self._on_dispatch(endpoint, event, status)

        return contacted

    def on_dispatch(self, callback: Callable[[WebhookEndpoint, WebhookEvent, int], None]) -> None:
        """Register a callback invoked after each dispatch attempt.

        The callback receives ``(endpoint, event, status_code)``.
        Useful for instrumentation or logging in tests.
        """
        self._on_dispatch = callback

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _dispatch_to_endpoint(
        self,
        endpoint: WebhookEndpoint,
        event: WebhookEvent,
    ) -> int:
        """Send the event to a single endpoint, with retries for transient errors.

        Returns the HTTP status code.
        """
        body = event.to_dict()
        headers = {"Content-Type": "application/json"}

        if endpoint.secret:
            headers["X-Webhook-Secret"] = endpoint.secret

        last_status = 0

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post(
                    endpoint.url,
                    json=body,
                    headers=headers,
                )
                last_status = response.status_code

                # 410 Gone is non-retryable -- return immediately so the
                # caller can disable the endpoint.
                if last_status == HTTP_GONE:
                    return last_status

                # Transient errors -> retry with backoff
                if last_status in RETRYABLE_STATUSES and attempt < self._max_retries:
                    wait = 0.5 * (2 ** attempt)
                    logger.info(
                        "Retrying webhook %s in %.1fs (attempt %d/%d, status=%d)",
                        endpoint.url,
                        wait,
                        attempt + 1,
                        self._max_retries,
                        last_status,
                    )
                    await asyncio.sleep(wait)
                    continue

                # Any other status -- success or non-retryable failure
                return last_status

            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_status = 0  # network error, no HTTP status
                logger.warning(
                    "Webhook %s connection error (attempt %d/%d): %s",
                    endpoint.url,
                    attempt + 1,
                    self._max_retries,
                    exc,
                )
                if attempt < self._max_retries:
                    wait = 0.5 * (2 ** attempt)
                    await asyncio.sleep(wait)
                    continue
                return 0

        return last_status

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()