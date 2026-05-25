"""Task Scheduler — Priority-based task queuing and dispatch with DST-aware scheduling."""

import asyncio
import heapq
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4


class PriorityQueue:
    """Priority queue implementation using heapq.

    Higher priority values are dequeued first. When priorities are equal,
    items are dequeued in FIFO order.
    """

    def __init__(self):
        self._queue: list = []
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


class TaskScheduler:
    """Task scheduler with DST-safe timezone-aware scheduling.

    All scheduled times are stored internally as timezone-aware UTC datetimes,
    ensuring correct behavior across daylight saving time transitions.
    """

    def __init__(self):
        self._queues: Dict[str, PriorityQueue] = {}
        # Stores: task_id -> (scheduled_utc_datetime, task_dict)
        self._scheduled: Dict[str, Tuple[datetime, Dict]] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._max_retries = 3

    def _utcnow(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        return datetime.now(timezone.utc)

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
        """Schedule a task after a relative delay in seconds.

        The delay is applied to the current UTC time, so DST transitions
                do not affect the scheduling interval.

        Args:
            task: The task to schedule.
            delay: Delay in seconds before the task becomes available.
            queue: Target queue name.
            priority: Priority level (higher = dequeued first).

        Returns:
            The task ID.
        """
        task_id = str(uuid4())
        task["id"] = task_id
        scheduled_time = self._utcnow() + timedelta(seconds=delay)
        self._scheduled[task_id] = (scheduled_time, task)
        return task_id

    def schedule_at(self, task: Dict, when: datetime, queue: str = "default", priority: int = 0) -> str:
        """Schedule a task at an absolute time with DST-safe timezone handling.

        Timezone-aware datetimes are converted to UTC for internal storage.
        Naive (tzinfo-less) datetimes are assumed to be in the system's local
        timezone and are converted to UTC accordingly.

        Args:
            task: The task to schedule.
            when: Timezone-aware or naive datetime. Naive datetimes are
                  interpreted as the system's local time.
            queue: Target queue name.
            priority: Priority level (higher = dequeued first).

        Returns:
            The task ID.
        """
        task_id = str(uuid4())
        task["id"] = task_id

        if when.tzinfo is not None:
            # Already timezone-aware — convert to UTC
            utc_time = when.astimezone(timezone.utc)
        else:
            # Naive datetime — treat as system local time via zoneinfo
            import zoneinfo
            local_tz = zoneinfo.ZoneInfo("localtime")
            local_dt = when.replace(tzinfo=local_tz)
            utc_time = local_dt.astimezone(timezone.utc)

        self._scheduled[task_id] = (utc_time, task)
        return task_id

    async def dequeue(self, queue: str = "default", timeout: float = 1.0) -> Optional[Dict]:
        """Dequeue the highest-priority task, promoting expired scheduled tasks first.

        Scheduled tasks whose UTC deadline has passed are moved into the
        target queue before dequeuing.

        Args:
            queue: The queue to dequeue from.
            timeout: Not currently used (reserved for future blocking dequeue).

        Returns:
            The next task dict, or None if the queue is empty.
        """
        now = self._utcnow()

        # Promote expired scheduled tasks into the active queue
        expired = [tid for tid, (t, _) in self._scheduled.items() if t <= now]
        for tid in expired:
            _, task = self._scheduled.pop(tid)
            self.enqueue(task, queue)

        # Dequeue from the active queue
        if queue in self._queues and len(self._queues[queue]) > 0:
            task = self._queues[queue].pop()
            if task:
                self._in_flight[task["id"]] = task
                return task
        return None

    def complete(self, task_id: str) -> bool:
        """Mark a task as successfully completed."""
        return self._in_flight.pop(task_id, None) is not None

    def fail(self, task_id: str, queue: str = "default") -> bool:
        """Mark a task as failed, retrying if retries remain."""
        task = self._in_flight.pop(task_id, None)
        if task:
            task["retries"] += 1
            if task["retries"] < self._max_retries:
                self.enqueue(task, queue, priority=task.get("priority", 0))
                return True
        return False