"""Queue Pause Manager — Graceful queue pause before schema changes.

Allows the queue to drain gracefully before maintenance operations,
then resume when ready.
"""
import asyncio
import logging
import time
from enum import Enum
from typing import Callable, Optional, Set

logger = logging.getLogger(__name__)


class QueuePauseState(Enum):
    RUNNING = "running"
    DRAINING = "draining"
    PAUSED = "paused"


class QueuePauseManager:
    """Manages graceful queue pause/resume for maintenance windows."""

    def __init__(self, drain_timeout: float = 300.0):
        self._state = QueuePauseState.RUNNING
        self._drain_timeout = drain_timeout
        self._in_flight: Set[str] = set()
        self._pause_requested_at: Optional[float] = None
        self._on_pause: Optional[Callable] = None
        self._on_resume: Optional[Callable] = None

    @property
    def state(self) -> QueuePauseState:
        return self._state

    def register_in_flight(self, task_id: str) -> None:
        """Register a task that's currently being processed."""
        self._in_flight.add(task_id)

    def complete_in_flight(self, task_id: str) -> None:
        """Mark an in-flight task as complete."""
        self._in_flight.discard(task_id)

    async def request_pause(self) -> bool:
        """Request a graceful pause. Returns True when fully drained."""
        if self._state == QueuePauseState.PAUSED:
            return True

        self._state = QueuePauseState.DRAINING
        self._pause_requested_at = time.time()
        logger.info(f"Queue draining requested. {len(self._in_flight)} in-flight tasks.")

        if self._on_pause:
            self._on_pause()

        start = time.time()
        while self._in_flight:
            if (time.time() - start) > self._drain_timeout:
                logger.warning(f"Drain timeout ({self._drain_timeout}s) reached. "
                              f"{len(self._in_flight)} tasks still in flight.")
                return False
            await asyncio.sleep(1.0)

        self._state = QueuePauseState.PAUSED
        logger.info("Queue fully drained and paused.")
        return True

    def resume(self) -> None:
        """Resume queue operations."""
        if self._state == QueuePauseState.RUNNING:
            return
        self._state = QueuePauseState.RUNNING
        self._pause_requested_at = None
        logger.info("Queue resumed.")
        if self._on_resume:
            self._on_resume()

    def can_dequeue(self) -> bool:
        """Check if a new task can be dequeued."""
        return self._state == QueuePauseState.RUNNING

    def on_pause(self, callback: Callable) -> None:
        self._on_pause = callback

    def on_resume(self, callback: Callable) -> None:
        self._on_resume = callback

    def reset(self) -> None:
        """Reset to running state (for recovery)."""
        self._state = QueuePauseState.RUNNING
        self._in_flight.clear()
        self._pause_requested_at = None
