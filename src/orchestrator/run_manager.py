"""Run Manager — Tracks execution runs with tenant association."""

import time
from typing import Any, Dict, List, Optional
from uuid import uuid4


class RunManager:
    """In-memory store for execution runs, keyed by run ID with tenant ownership.

    Each run tracks:
      - run_id: unique identifier
      - tenant_id: owning tenant
      - agent_id: target agent
      - status: current run status
      - created_at / updated_at: timestamps
    """

    def __init__(self):
        self._runs: Dict[str, Dict[str, Any]] = {}

    def create_run(self, tenant_id: str, agent_id: str) -> str:
        run_id = str(uuid4())
        now = time.time()
        self._runs[run_id] = {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "status": "running",
            "created_at": now,
            "updated_at": now,
        }
        return run_id

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._runs.get(run_id)

    def cancel_run(self, run_id: str, tenant_id: str) -> bool:
        """Cancel a run only if it belongs to the given tenant."""
        run = self._runs.get(run_id)
        if not run:
            return False
        if run["tenant_id"] != tenant_id:
            return False
        if run["status"] in ("cancelled", "completed", "failed"):
            return False
        run["status"] = "cancelled"
        run["updated_at"] = time.time()
        return True

    def batch_cancel(self, run_ids: List[str], tenant_id: str) -> Dict[str, Any]:
        """Cancel multiple runs, scoped to the given tenant.

        Returns a summary with:
          - cancelled: list of run_ids that were successfully cancelled
          - not_found: run_ids that don't exist
          - not_owned: run_ids that belong to a different tenant
          - already_terminal: run_ids already in a final state
        """
        cancelled = []
        not_found = []
        not_owned = []
        already_terminal = []

        for run_id in run_ids:
            run = self._runs.get(run_id)
            if not run:
                not_found.append(run_id)
                continue
            if run["tenant_id"] != tenant_id:
                not_owned.append(run_id)
                continue
            if run["status"] in ("cancelled", "completed", "failed"):
                already_terminal.append(run_id)
                continue
            run["status"] = "cancelled"
            run["updated_at"] = time.time()
            cancelled.append(run_id)

        return {
            "cancelled": cancelled,
            "not_found": not_found,
            "not_owned": not_owned,
            "already_terminal": already_terminal,
        }

    def list_runs(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if tenant_id:
            return [r for r in self._runs.values() if r["tenant_id"] == tenant_id]
        return list(self._runs.values())

    def count(self) -> int:
        return len(self._runs)