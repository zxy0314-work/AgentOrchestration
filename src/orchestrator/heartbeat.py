"""Heartbeat Monitor — Prevents worker heartbeat from reviving completed runs.

Tracks agent process health and ensures terminal states are respected.
"""
import asyncio
import logging
import time
from enum import Enum
from typing import Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)


class HeartbeatState(Enum):
    ACTIVE = "active"
    STALLED = "stalled"
    TERMINAL = "terminal"  # Run completed — no heartbeat can revive


class HeartbeatMonitor:
    """Monitors agent heartbeat, rejecting signals for completed runs."""

    def __init__(self, stale_threshold: float = 30.0):
        self._heartbeats: Dict[str, float] = {}
        self._terminal_runs: Set[str] = set()
        self._stale_threshold = stale_threshold
        self._on_stall: Optional[Callable] = None
        self._on_recovery: Optional[Callable] = None

    def mark_terminal(self, run_id: str) -> None:
        """Mark a run as terminal — no heartbeat can revive it."""
        self._terminal_runs.add(run_id)
        self._heartbeats.pop(run_id, None)

    def receive_heartbeat(self, run_id: str) -> bool:
        """Process a heartbeat. Returns False if run is terminal."""
        if run_id in self._terminal_runs:
            logger.warning(f"Rejected heartbeat for terminal run: {run_id}")
            return False
        self._heartbeats[run_id] = time.time()
        return True

    def is_stalled(self, run_id: str) -> bool:
        """Check if a run has missed too many heartbeats."""
        if run_id in self._terminal_runs:
            return False  # Terminal runs aren't stalled, they're done
        last = self._heartbeats.get(run_id)
        if last is None:
            return True
        return (time.time() - last) > self._stale_threshold

    def on_stall(self, callback: Callable) -> None:
        self._on_stall = callback

    def on_recovery(self, callback: Callable) -> None:
        self._on_recovery = callback

    async def monitor_loop(self, interval: float = 5.0) -> None:
        """Background loop that detects stalled agents."""
        while True:
            await asyncio.sleep(interval)
            now = time.time()
            for run_id, last_hb in list(self._heartbeats.items()):
                if run_id in self._terminal_runs:
                    continue
                if (now - last_hb) > self._stale_threshold:
                    self._terminal_runs.add(run_id)
                    self._heartbeats.pop(run_id, None)
                    logger.info(f"Run {run_id} marked terminal due to heartbeat stall")
                    if self._on_stall:
                        self._on_stall(run_id)

    def reset(self, run_id: str) -> None:
        """Remove a run from tracking entirely."""
        self._heartbeats.pop(run_id, None)
        self._terminal_runs.discard(run_id)

    def status(self, run_id: str) -> HeartbeatState:
        if run_id in self._terminal_runs:
            return HeartbeatState.TERMINAL
        last = self._heartbeats.get(run_id)
        if last is None:
            return HeartbeatState.STALLED
        if (time.time() - last) > self._stale_threshold:
            return HeartbeatState.STALLED
        return HeartbeatState.ACTIVE

    def active_run_count(self) -> int:
        """Count of currently active (non-terminal, non-stalled) runs."""
        now = time.time()
        return sum(
            1 for run_id, last_hb in self._heartbeats.items()
            if run_id not in self._terminal_runs
            and (now - last_hb) <= self._stale_threshold
        )
