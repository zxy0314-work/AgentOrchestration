"""Agent Registry — Manages agent lifecycle and metadata."""

import json
import re
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional


class AgentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"
    TERMINATED = "terminated"
    COMPLETED = "completed"
    ARCHIVED = "archived"


# Terminal/archived states — late events to these runs must be rejected
ARCHIVED_STATES = {AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.TERMINATED, AgentStatus.ARCHIVED}


# --- Workspace validation (issue #4223) ---
# Workspace IDs must be safe identifiers: alnum + dash/underscore, 1..64 chars.
_WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_GROUP_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


class WorkspaceFilterError(ValueError):
    """Raised when workspace filter inputs fail validation BEFORE any lookup."""

    def __init__(self, field: str, reason: str):
        self.field = field
        self.reason = reason
        super().__init__(f"invalid {field}: {reason}")


def validate_workspace_id(workspace_id: Any) -> str:
    """Validate a workspace_id string. Raises WorkspaceFilterError on failure.

    Performed BEFORE any registry lookup so that malformed/empty workspace IDs
    are rejected at the boundary rather than silently returning all agents.
    """
    if workspace_id is None:
        raise WorkspaceFilterError("workspace_id", "missing")
    if not isinstance(workspace_id, str):
        raise WorkspaceFilterError("workspace_id", "must be a string")
    if not workspace_id.strip():
        raise WorkspaceFilterError("workspace_id", "empty")
    if not _WORKSPACE_ID_RE.match(workspace_id):
        raise WorkspaceFilterError("workspace_id", "must match [A-Za-z0-9_-]{1,64}")
    return workspace_id


def _validate_optional_group(group: Any) -> Optional[str]:
    if group is None:
        return None
    if not isinstance(group, str):
        raise WorkspaceFilterError("group", "must be a string")
    if not group:
        raise WorkspaceFilterError("group", "empty")
    if not _GROUP_RE.match(group):
        raise WorkspaceFilterError("group", "must match [A-Za-z0-9_-]{1,64}")
    return group


def _validate_optional_status(status: Any) -> Optional["AgentStatus"]:
    if status is None:
        return None
    if isinstance(status, AgentStatus):
        return status
    if not isinstance(status, str) or not status:
        raise WorkspaceFilterError("status", "must be a non-empty string")
    try:
        return AgentStatus(status)
    except ValueError:
        raise WorkspaceFilterError("status", f"unknown status '{status}'")


class AgentRegistry:
    def __init__(self, storage_backend: str = "memory"):
        self.storage_backend = storage_backend
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._index: Dict[str, List[str]] = {}
        # Audit log for rejected late events
        self._event_log: Dict[str, List[Dict]] = {}

    def register(self, name: str, agent_type: str, config: Optional[Dict] = None,
                 workspace_id: Optional[str] = None) -> str:
        agent_id = str(uuid.uuid4())
        timestamp = time.time()
        self._agents[agent_id] = {
            "id": agent_id,
            "name": name,
            "type": agent_type,
            "status": AgentStatus.PENDING.value,
            "config": config or {},
            "workspace_id": workspace_id,
            "created_at": timestamp,
            "updated_at": timestamp,
            "version": "1.0.0",
            "metrics": {"tasks_completed": 0, "errors": 0, "uptime": 0},
            "revision": 0,
        }
        group = agent_type.split(".")[0]
        if group not in self._index:
            self._index[group] = []
        self._index[group].append(agent_id)
        return agent_id

    def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        return self._agents.get(agent_id)

    def list(self, status: Optional[AgentStatus] = None, group: Optional[str] = None) -> List[Dict[str, Any]]:
        agents = self._agents.values()
        if status:
            agents = [a for a in agents if a["status"] == status.value]
        if group:
            agent_ids = self._index.get(group, [])
            agents = [a for a in agents if a["id"] in agent_ids]
        return list(agents)

    # ------------------------------------------------------------------
    # Shared service: workspace-scoped agent listing (issue #4223)
    # ------------------------------------------------------------------
    def list_for_workspace(
        self,
        workspace_id: str,
        status: Optional[Any] = None,
        group: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return agents belonging to ``workspace_id``.

        The workspace filter is ENFORCED here — it is not optional and is not
        the caller's responsibility. This guard lives in the service layer so
        every entry point (FastAPI router, CLI, internal callers) shares one
        boundary and cannot accidentally bypass tenant isolation.

        All inputs are validated BEFORE any lookup so malformed requests fail
        fast without touching agent storage. Returns an empty list (never a
        cross-workspace bleed) when the workspace exists but has no agents.
        """
        # Validate every input BEFORE lookup. This is the security boundary.
        workspace_id = validate_workspace_id(workspace_id)
        status_enum = _validate_optional_status(status)
        group_clean = _validate_optional_group(group)

        # Enforce workspace filter. We iterate explicitly rather than reusing
        # ``list()`` so the workspace check cannot be skipped if upstream
        # filters change.
        results: List[Dict[str, Any]] = []
        for agent in self._agents.values():
            if agent.get("workspace_id") != workspace_id:
                continue
            if status_enum is not None and agent["status"] != status_enum.value:
                continue
            if group_clean is not None:
                agent_ids = self._index.get(group_clean, [])
                if agent["id"] not in agent_ids:
                    continue
            results.append(agent)
        return results

    def update_status(self, agent_id: str, status: AgentStatus) -> bool:
        if agent_id not in self._agents:
            return False
        # Guard: reject status change for archived runs (late worker messages)
        current = self._agents[agent_id]["status"]
        try:
            current_enum = AgentStatus(current)
        except ValueError:
            current_enum = AgentStatus.PENDING
        if current_enum in ARCHIVED_STATES:
            self.record_event(agent_id, {
                "type": "status_change_rejected",
                "reason": "archived_run",
                "current_status": current,
                "attempted_status": status.value,
                "timestamp": time.time(),
            })
            return False
        self._agents[agent_id]["status"] = status.value
        self._agents[agent_id]["updated_at"] = time.time()
        self._agents[agent_id]["revision"] = self._agents[agent_id].get("revision", 0) + 1
        return True

    def archive(self, agent_id: str) -> bool:
        """Archive an agent — marks it as ARCHIVED. Future events will be rejected."""
        if agent_id not in self._agents:
            return False
        self._agents[agent_id]["status"] = AgentStatus.ARCHIVED.value
        self._agents[agent_id]["archived_at"] = time.time()
        return True

    def is_archived(self, agent_id: str) -> bool:
        """Check if an agent is in an archived state."""
        agent = self._agents.get(agent_id)
        if not agent:
            return False
        try:
            return AgentStatus(agent["status"]) in ARCHIVED_STATES
        except ValueError:
            return False

    def record_event(self, agent_id: str, event: Dict[str, Any]) -> None:
        """Record an event for audit logging (e.g., rejected late messages)."""
        if agent_id not in self._event_log:
            self._event_log[agent_id] = []
        # Bound the audit log to prevent unbounded growth
        if len(self._event_log[agent_id]) >= 100:
            self._event_log[agent_id] = self._event_log[agent_id][-99:]
        self._event_log[agent_id].append({**event, "recorded_at": time.time()})

    def get_events(self, agent_id: str) -> List[Dict[str, Any]]:
        """Get audit events for an agent."""
        return list(self._event_log.get(agent_id, []))

    def delete(self, agent_id: str) -> bool:
        if agent_id not in self._agents:
            return False
        agent = self._agents.pop(agent_id)
        group = agent["type"].split(".")[0]
        if group in self._index and agent_id in self._index[group]:
            self._index[group].remove(agent_id)
        return True

    def count(self) -> int:
        return len(self._agents)
