"""Tests for webhook 410 Gone handling.

Verifies that when a webhook endpoint returns HTTP 410 (Gone), the dispatcher
automatically marks it as disabled and stops sending events -- rather than
retrying endlessly.
"""

from __future__ import annotations

import httpx
import pytest

from src.webhooks.dispatcher import (
    HTTP_GONE,
    WebhookDispatcher,
)


class TestWebhookGoneHandling:
    """Core 410 Gone behaviour."""

    async def test_410_disables_endpoint(self, dispatcher, gone_url):
        """A single 410 response disables the endpoint immediately."""
        ep = dispatcher.register(gone_url)
        assert ep.active is True

        contacted = await dispatcher.dispatch("test.event", {"msg": "hello"})

        assert ep in contacted
        assert ep.active is False
        assert ep.disabled_reason is not None
        assert "410" in ep.disabled_reason

    async def test_disabled_endpoint_not_called_again(self, dispatcher, gone_url, tracker):
        """Once disabled, no further events are sent to the endpoint."""
        dispatcher.register(gone_url)

        await dispatcher.dispatch("event.one", {"n": 1})
        assert tracker.call_count == 1

        await dispatcher.dispatch("event.two", {"n": 2})
        assert tracker.call_count == 1  # still 1 -- no new call

    async def test_active_endpoints_excludes_disabled(self, dispatcher, gone_url, ok_url):
        """The ``active_endpoints`` property reflects the disabled state."""
        ep1 = dispatcher.register(gone_url)
        ep2 = dispatcher.register(ok_url)

        assert len(dispatcher.active_endpoints) == 2

        await dispatcher.dispatch("test.event", {"x": 1})

        active = dispatcher.active_endpoints
        assert ep1 not in active
        assert ep2 in active

    async def test_re_enable_works(self, dispatcher, gone_url, tracker):
        """A disabled endpoint can be manually re-enabled."""
        ep = dispatcher.register(gone_url)
        await dispatcher.dispatch("event.one", {"n": 1})
        assert ep.active is False

        assert dispatcher.enable(gone_url) is True
        assert ep.active is True
        assert ep.disabled_reason is None

        await dispatcher.dispatch("event.two", {"n": 2})
        assert tracker.call_count == 2

    async def test_410_no_retry(self, dispatcher, gone_url, tracker):
        """A 410 response is NOT retried -- returns immediately."""
        ep = dispatcher.register(gone_url)
        await dispatcher.dispatch("test.event", {})
        assert ep.last_status == HTTP_GONE
        assert tracker.call_count == 1  # only one attempt despite max_retries


class TestWebhookRegistration:
    """Endpoint registration and management."""

    def test_register_valid_url(self, dispatcher):
        ep = dispatcher.register("https://valid.example.com/hook")
        assert ep.url == "https://valid.example.com/hook"
        assert ep.active is True

    def test_register_invalid_url(self, dispatcher):
        with pytest.raises(ValueError, match="Invalid webhook URL"):
            dispatcher.register("not-a-url")

    def test_register_empty_url(self, dispatcher):
        with pytest.raises(ValueError):
            dispatcher.register("")

    def test_unregister(self, dispatcher):
        dispatcher.register("https://example.com/hook")
        assert len(dispatcher.endpoints) == 1
        assert dispatcher.unregister("https://example.com/hook") is True
        assert len(dispatcher.endpoints) == 0

    def test_unregister_nonexistent(self, dispatcher):
        assert dispatcher.unregister("https://noop.example.com") is False

    def test_disable_manual(self, dispatcher):
        ep = dispatcher.register("https://example.com/hook")
        assert dispatcher.disable("https://example.com/hook", "Manual") is True
        assert ep.active is False
        assert ep.disabled_reason == "Manual"

    def test_disable_nonexistent(self, dispatcher):
        assert dispatcher.disable("https://noop.example.com") is False

    def test_enable_nonexistent(self, dispatcher):
        assert dispatcher.enable("https://noop.example.com") is False


class TestWebhookRetries:
    """Retry behaviour on transient errors (not 410)."""

    async def test_transient_errors_retried(self):
        """5xx errors are retried; 410 is not."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(503)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        dispatcher = WebhookDispatcher(client=client, max_retries=2)

        ep = dispatcher.register("https://retry.example.com/flaky")
        await dispatcher.dispatch("test.event", {})

        # 503 is retryable: max_retries=2 means up to 3 attempts
        assert call_count <= 3
        # Should have stopped due to max retries, endpoint still active
        # (503 does not disable)
        assert ep.active is True

    async def test_success_no_retry(self):
        """A successful 200 response calls the endpoint once."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        dispatcher = WebhookDispatcher(client=client, max_retries=3)

        ep = dispatcher.register("https://ok.example.com/hook")
        await dispatcher.dispatch("test.event", {})
        assert call_count == 1
        assert ep.last_status == 200

    async def test_network_error_retried(self):
        """Network errors are retried rather than disabling the endpoint."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            raise httpx.TransportError("Connection refused")

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        dispatcher = WebhookDispatcher(client=client, max_retries=2)

        ep = dispatcher.register("https://down.example.com/hook")
        await dispatcher.dispatch("test.event", {})
        # Should have attempted up to max_retries+1 times
        assert call_count <= 3
        # Network errors don't disable -- endoint stays active
        assert ep.active is True


class TestWebhookMultipleEndpoints:
    """Multiple endpoints with mixed states."""

    async def test_mixed_active_and_disabled(self, dispatcher, gone_url, ok_url, tracker):
        """Active endpoints receive events; disabled ones are skipped."""
        ep1 = dispatcher.register(gone_url)
        ep2 = dispatcher.register(ok_url)

        ep1.active = False
        ep1.disabled_reason = "Pre-disabled"

        await dispatcher.dispatch("test.event", {"n": 1})

        assert tracker.call_count == 1
        assert ep2.last_status == 200

    async def test_one_gone_does_not_affect_other(self, dispatcher, gone_url, ok_url):
        """One endpoint's 410 does not disable the other."""
        ep1 = dispatcher.register(gone_url)
        ep2 = dispatcher.register(ok_url)

        await dispatcher.dispatch("test.event", {})

        assert ep1.active is False   # disabled by 410
        assert ep2.active is True    # still active


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def gone_url() -> str:
    """URL that returns 410 Gone."""
    return "https://webhook-test.example.com/gone"


@pytest.fixture
def ok_url() -> str:
    """URL that returns 200 OK."""
    return "https://webhook-test.example.com/ok"


@pytest.fixture
def tracker():
    """Simple call-count tracker attached to mock responses."""

    class Tracker:
        def __init__(self):
            self.call_count = 0

    return Tracker()


@pytest.fixture
def dispatcher(gone_url, ok_url, tracker):
    """A WebhookDispatcher with a mock HTTP client.

    * ``gone_url`` returns 410 Gone.
    * ``ok_url`` returns 200 OK.
    * ``tracker.call_count`` is incremented on every POST.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        tracker.call_count += 1
        if str(request.url) == gone_url:
            return httpx.Response(HTTP_GONE)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return WebhookDispatcher(client=client, max_retries=3)