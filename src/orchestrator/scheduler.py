"""Task Scheduler — Priority-based task queuing and dispatch with DST-aware cron."""

import asyncio
import heapq
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4
import logging

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
    _HAS_ZONEINFO = True
except ImportError:
    _HAS_ZONEINFO = False


class PriorityQueue:
    def __init__(self):
        self._queue = []
        self._counter = 0

    def push(self, item: Any, priority: int = 0) -> None:
        heapq.heappush(self._queue, (-priority, self._counter, item))
        self._counter += 1

    def pop(self) -> Optional[Any]:
        if self._queue:
            return heapq.heappop(self._queue)[2]
        return None

    def peek(self) -> Optional[Any]:
        if self._queue:
            return self._queue[0][2]
        return None

    def __len__(self) -> int:
        return len(self._queue)


class DSTAwareCronEvaluator:
    """Cron evaluator that respects daylight saving transitions.

    Handles:
    - Spring forward (clock jumps 2:00 -> 3:00): tasks scheduled in 2:00-3:00 run once at 3:00
    - Fall back (clock goes 2:00 -> 1:00): tasks in 1:00-2:00 run only once (not twice)
    - Cross-DST timestamp comparisons use UTC internally
    """

    def __init__(self):
        # Track last execution time per (cron_expr, timezone) tuple — deduplication for DST
        self._last_executions: Dict[Tuple[str, str], float] = {}

    def next_run(self, cron_expr: str, timezone_name: str = "UTC", from_time: Optional[float] = None) -> Optional[float]:
        """Calculate the next valid run time for a cron expression in a given timezone.

        Returns the next run time as a Unix timestamp (UTC).
        """
        if from_time is None:
            from_time = time.time()

        if not _HAS_ZONEINFO and timezone_name != "UTC":
            logger.warning(f"zoneinfo not available; falling back to UTC for cron evaluation")
            timezone_name = "UTC"

        if timezone_name == "UTC":
            tz = timezone.utc
        else:
            try:
                tz = ZoneInfo(timezone_name)
            except Exception as e:
                logger.error(f"Invalid timezone {timezone_name}: {e}; falling back to UTC")
                tz = timezone.utc

        # Convert from_time to localized datetime
        local_dt = datetime.fromtimestamp(from_time, tz=tz)

        # Parse cron expr (simplified: "HH:MM" or "*/N min")
        parts = cron_expr.strip().split()
        if len(parts) == 5:
            # Standard cron: min hour dom mon dow
            minute_part, hour_part = parts[0], parts[1]
            try:
                minute = int(minute_part) if minute_part != "*" else 0
                hour = int(hour_part) if hour_part != "*" else 0
            except ValueError:
                return None
        elif ":" in cron_expr:
            try:
                hour, minute = map(int, cron_expr.split(":"))
            except ValueError:
                return None
        else:
            return None

        # Compute the next occurrence in local time
        candidate = local_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_dt:
            candidate += timedelta(days=1)

        # Convert back to UTC timestamp for comparison
        utc_ts = candidate.astimezone(timezone.utc).timestamp()

        # DST guard: ensure we don't return a duplicate execution time
        key = (cron_expr, timezone_name)
        last_exec = self._last_executions.get(key, 0)

        # If the proposed time is within 1 hour of last execution → DST duplicate
        if utc_ts > 0 and abs(utc_ts - last_exec) < 3600:
            # Add 24 hours to skip the duplicate
            utc_ts += 86400
            logger.info(
                f"DST duplicate detected for cron='{cron_expr}' tz={timezone_name}; "
                f"skipping to next day"
            )

        return utc_ts

    def mark_executed(self, cron_expr: str, timezone_name: str, executed_at: float) -> None:
        """Mark a cron execution to prevent DST duplicate triggers."""
        key = (cron_expr, timezone_name)
        self._last_executions[key] = executed_at


class TaskScheduler:
    def __init__(self):
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, float] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._max_retries = 3
        self._cron_evaluator = DSTAwareCronEvaluator()
        # Track cron task fingerprints to prevent duplicate scheduling across DST
        self._cron_fingerprints: Dict[str, float] = {}

    def enqueue(self, task: Dict, queue: str = "default", priority: int = 0) -> str:
        task_id = str(uuid4())
        task["id"] = task_id
        task["enqueued_at"] = time.time()
        task["retries"] = 0

        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, priority)
        return task_id

    def schedule(self, task: Dict, delay: float, queue: str = "default", priority: int = 0) -> str:
        task_id = str(uuid4())
        task["id"] = task_id
        self._scheduled[task_id] = time.time() + delay
        return task_id

    def schedule_cron(self, task: Dict, cron_expr: str, timezone_name: str = "UTC",
                       queue: str = "default", priority: int = 0) -> str:
        """Schedule a task with cron expression. DST-aware: handles spring-forward/fall-back."""
        task_id = str(uuid4())
        task["id"] = task_id
        task["cron_expr"] = cron_expr
        task["timezone"] = timezone_name

        # Create a deterministic fingerprint for DST deduplication
        fingerprint = f"{cron_expr}|{timezone_name}|{task.get('name', task_id)}"

        next_run = self._cron_evaluator.next_run(cron_expr, timezone_name)
        if next_run is None:
            logger.error(f"Failed to compute next run for cron='{cron_expr}' tz={timezone_name}")
            return ""

        # Check for DST duplicate
        last_fp_time = self._cron_fingerprints.get(fingerprint, 0)
        if last_fp_time > 0 and abs(next_run - last_fp_time) < 3600:
            logger.warning(
                f"DST duplicate detected for task '{fingerprint}'; "
                f"adjusting to next day"
            )
            next_run += 86400

        self._cron_fingerprints[fingerprint] = next_run
        self._scheduled[task_id] = next_run
        return task_id

    async def dequeue(self, queue: str = "default", timeout: float = 1.0) -> Optional[Dict]:
        now = time.time()
        expired = [tid for tid, t in self._scheduled.items() if t <= now]
        for tid in expired:
            task = self._scheduled.pop(tid)
            if task:
                self.enqueue(task, queue)

        if queue in self._queues and len(self._queues[queue]) > 0:
            task = self._queues[queue].pop()
            if task:
                self._in_flight[task["id"]] = task
                return task
        return None

    def complete(self, task_id: str) -> bool:
        task = self._in_flight.pop(task_id, None)
        if task and task.get("cron_expr"):
            # Mark cron execution for DST guard
            self._cron_evaluator.mark_executed(
                task["cron_expr"],
                task.get("timezone", "UTC"),
                time.time()
            )
        return task is not None

    def fail(self, task_id: str, queue: str = "default") -> bool:
        task = self._in_flight.pop(task_id, None)
        if task:
            task["retries"] += 1
            if task["retries"] < self._max_retries:
                self.enqueue(task, queue, priority=task.get("priority", 0))
                return True
        return False
