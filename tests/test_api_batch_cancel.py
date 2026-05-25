"""Tests for tenant-scoped batch cancel endpoint."""

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.orchestrator.run_manager import RunManager


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_runs():
    """Reset the run manager before each test."""
    # Access the module-level run_manager from routes
    import src.api.routes as routes_mod
    routes_mod.run_manager._runs.clear()
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TENANT_A = "tenant-alpha"
TENANT_B = "tenant-beta"
AGENT_ID = "agent-1234"


def _create_run_for(
    client,
    tenant_id: str,
    agent_id: str = AGENT_ID,
    token: str = None,
) -> str:
    """Create a run and return its run_id."""
    headers = {
        "Authorization": f"Bearer {token or tenant_id + ':sometoken'}",
    }
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id
    resp = client.post(
        "/api/v2/runs",
        params={"agent_id": agent_id},
        headers=headers,
    )
    assert resp.status_code == 200, f"create_run failed: {resp.text}"
    return resp.json()["run_id"]


def _batch_cancel(client, run_ids, tenant_id=None, token=None):
    """Helper to call batch-cancel and return the response."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["Authorization"] = f"Bearer {tenant_id or 'default'}:canceltoken"
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id
    return client.post(
        "/api/v2/runs/batch-cancel",
        json={"run_ids": run_ids},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Batch cancel tests
# ---------------------------------------------------------------------------


class TestBatchCancel:
    """Verify that batch-cancel respects tenant scope."""

    def test_cancel_runs_belonging_to_tenant(self, client):
        """Runs owned by the requesting tenant are cancelled."""
        run_id = _create_run_for(client, TENANT_A)

        resp = _batch_cancel(client, [run_id], tenant_id=TENANT_A)
        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled"] == [run_id]
        assert body["not_found"] == []
        assert body["not_owned"] == []
        assert body["already_terminal"] == []

    def test_does_not_cancel_other_tenant_runs(self, client):
        """Runs belonging to tenant-B are NOT cancelled when tenant-A requests it."""
        run_a = _create_run_for(client, TENANT_A)
        run_b = _create_run_for(client, TENANT_B)

        resp = _batch_cancel(client, [run_a, run_b], tenant_id=TENANT_A)
        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled"] == [run_a]
        assert body["not_owned"] == [run_b]
        assert body["not_found"] == []
        assert body["already_terminal"] == []

    def test_cancel_nonexistent_runs_reported(self, client):
        """Non-existent run IDs appear in not_found list."""
        resp = _batch_cancel(client, ["nonexistent-run-id"], tenant_id=TENANT_A)
        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled"] == []
        assert body["not_found"] == ["nonexistent-run-id"]

    def test_cancel_empty_list(self, client):
        """Empty run list returns empty results with no error."""
        resp = _batch_cancel(client, [], tenant_id=TENANT_A)
        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled"] == []
        assert body["not_found"] == []
        assert body["not_owned"] == []
        assert body["already_terminal"] == []

    def test_already_cancelled_run_skipped(self, client):
        """A run already in cancelled/terminal state is reported as already_terminal."""
        run_id = _create_run_for(client, TENANT_A)

        # Cancel once
        resp1 = _batch_cancel(client, [run_id], tenant_id=TENANT_A)
        assert resp1.status_code == 200
        assert resp1.json()["cancelled"] == [run_id]

        # Cancel again — should be already_terminal
        resp2 = _batch_cancel(client, [run_id], tenant_id=TENANT_A)
        assert resp2.status_code == 200
        body = resp2.json()
        assert body["cancelled"] == []
        assert body["already_terminal"] == [run_id]

    def test_mixed_scenario(self, client):
        """Multiple run IDs with different states produce correct per-category lists."""
        run_a1 = _create_run_for(client, TENANT_A)  # owned, active
        run_a2 = _create_run_for(client, TENANT_A)  # owned, will be cancelled first
        run_b = _create_run_for(client, TENANT_B)  # not owned

        # Cancel run_a2 first
        _batch_cancel(client, [run_a2], tenant_id=TENANT_A)

        resp = _batch_cancel(
            client, [run_a1, run_a2, run_b, "ghost-run"], tenant_id=TENANT_A
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled"] == [run_a1]
        assert body["already_terminal"] == [run_a2]
        assert body["not_owned"] == [run_b]
        assert body["not_found"] == ["ghost-run"]

    # ------------------------------------------------------------------
    # Tenant extraction from Bearer token
    # ------------------------------------------------------------------

    def test_tenant_from_bearer_token(self, client):
        """Tenant is extracted from Bearer token prefix (tenant:session format)."""
        run_id = _create_run_for(client, TENANT_A)

        resp = _batch_cancel(client, [run_id], token=f"{TENANT_A}:abc123session")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled"] == [run_id]
        assert body["tenant_id"] == TENANT_A

    def test_default_tenant_when_no_tenant_header_or_token(self, client):
        """When no tenant info is present, 'default' tenant is used."""
        # Create a run under default tenant
        resp_create = client.post(
            "/api/v2/runs",
            params={"agent_id": AGENT_ID},
            headers={"Authorization": "Bearer default:somekey"},
        )
        run_id = resp_create.json()["run_id"]

        # Cancel with a token that has no colon separator (no tenant in token)
        resp = _batch_cancel(client, [run_id], token="justatoken")
        assert resp.status_code == 200
        body = resp.json()
        assert body["tenant_id"] == "default"
        # The run was created under "default" (from the create), so it matches
        assert body["cancelled"] == [run_id]

    def test_tenant_header_takes_precedence_over_token(self, client):
        """X-Tenant-ID header overrides tenant extracted from Bearer token."""
        run_id = _create_run_for(client, TENANT_A)

        # Header says TENANT_A, token says TENANT_B — header wins
        resp = client.post(
            "/api/v2/runs/batch-cancel",
            json={"run_ids": [run_id]},
            headers={
                "Authorization": f"Bearer {TENANT_B}:sessionkey",
                "X-Tenant-ID": TENANT_A,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled"] == [run_id]
        assert body["tenant_id"] == TENANT_A


# ---------------------------------------------------------------------------
# Run manager unit tests
# ---------------------------------------------------------------------------


class TestRunManager:
    """Direct unit tests for RunManager.batch_cancel."""

    def test_batch_cancel_rejects_wrong_tenant(self):
        """batch_cancel returns runs belonging to other tenants in not_owned."""
        from src.orchestrator.run_manager import RunManager

        rm = RunManager()
        rm.create_run("tenant-1", "agent-a")
        rm.create_run("tenant-2", "agent-b")

        all_runs = list(rm._runs.keys())
        result = rm.batch_cancel(all_runs, "tenant-1")

        # tenant-2's run should be in not_owned
        run2 = [r for r in all_runs if rm._runs[r]["tenant_id"] == "tenant-2"][0]
        assert run2 in result["not_owned"]
        # After batch cancel, tenant-2's run should still be "running"
        assert rm._runs[run2]["status"] == "running"

    def test_batch_cancel_idempotent(self):
        """Cancelling the same run twice does not error."""
        from src.orchestrator.run_manager import RunManager

        rm = RunManager()
        rid = rm.create_run("tenant-1", "agent-a")

        r1 = rm.batch_cancel([rid], "tenant-1")
        assert r1["cancelled"] == [rid]

        r2 = rm.batch_cancel([rid], "tenant-1")
        assert r2["cancelled"] == []
        assert r2["already_terminal"] == [rid]
        # Status should remain "cancelled"
        assert rm._runs[rid]["status"] == "cancelled"