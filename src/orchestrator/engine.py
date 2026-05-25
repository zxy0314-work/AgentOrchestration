"""Orchestration Engine — Core execution and coordination logic."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from src.agent import AgentRegistry, AgentStatus
from src.orchestrator.scheduler import TaskScheduler

logger = logging.getLogger(__name__)

# Valid lifecycle state transitions
_VALID_TRANSITIONS = {
    AgentStatus.PENDING: [AgentStatus.RUNNING, AgentStatus.PAUSED],
    AgentStatus.RUNNING: [AgentStatus.PAUSED, AgentStatus.COMPLETED, AgentStatus.FAILED],
    AgentStatus.PAUSED: [AgentStatus.RUNNING, AgentStatus.COMPLETED, AgentStatus.FAILED],
    AgentStatus.COMPLETED: [],  # Terminal state — no transitions out
    AgentStatus.FAILED: [],     # Terminal state — no transitions out
    AgentStatus.ARCHIVED: [],   # Terminal state — no transitions out
}

# Archived states where late events must be rejected
_ARCHIVED_STATES = {AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.ARCHIVED}


class OrchestrationEngine:
    def __init__(self, max_workers: int = 10, agent_timeout: int = 300):
        self.registry = AgentRegistry()
        self.scheduler = TaskScheduler()
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.agent_timeout = agent_timeout
        self._running = False
        self._hooks: Dict[str, List[Callable]] = {
            "pre_execute": [],
            "post_execute": [],
            "on_error": [],
            "on_complete": [],
        }

    def register_hook(self, event: str, callback: Callable) -> None:
        if event in self._hooks:
            self._hooks[event].append(callback)

    async def start(self) -> None:
        self._running = True
        logger.info("Orchestration engine started")
        while self._running:
            task = await self.scheduler.dequeue()
            if task:
                asyncio.create_task(self._execute_task(task))
            await asyncio.sleep(0.1)

    def stop(self) -> None:
        self._running = False
        logger.info("Orchestration engine stopped")

    def _validate_state_transition(self, agent_id: str, current_status: AgentStatus, target_status: AgentStatus) -> bool:
        """Validate a lifecycle state transition.

        Rejects transitions to archived runs (late worker messages).
        Rejects invalid state transitions per _VALID_TRANSITIONS.
        """
        # Reject any event targeting an archived/completed/failed run
        if current_status in _ARCHIVED_STATES:
            logger.warning(
                f"Rejected event for archived run agent={agent_id}: "
                f"current={current_status.value}, target={target_status.value}"
            )
            return False

        # Validate the transition is legal
        allowed = _VALID_TRANSITIONS.get(current_status, [])
        if target_status not in allowed:
            logger.warning(
                f"Invalid state transition for agent={agent_id}: "
                f"{current_status.value} -> {target_status.value} "
                f"(allowed: {[s.value for s in allowed]})"
            )
            return False

        return True

    async def _execute_task(self, task: Dict[str, Any]) -> None:
        task_id = task["id"]
        agent_id = task["target_agent"]
        logger.info(f"Executing task {task_id} on agent {agent_id}")

        # Guard: reject events for archived runs — late worker messages guard
        agent = self.registry.get(agent_id)
        if not agent:
            logger.error(f"Agent {agent_id} not found for task {task_id}")
            return

        current_status = agent.get("status", AgentStatus.PENDING)

        # Check if this agent is in an archived state — reject late events
        if current_status in _ARCHIVED_STATES:
            logger.warning(
                f"Rejected late worker message: task={task_id}, "
                f"agent={agent_id}, status={current_status.value}"
            )
            # Record the rejection for audit
            self.registry.record_event(agent_id, {
                "type": "late_message_rejected",
                "task_id": task_id,
                "current_status": current_status.value,
                "reason": "Agent is in archived state"
            })
            return

        for hook in self._hooks["pre_execute"]:
            await hook(task)

        try:
            if not agent:
                raise ValueError(f"Agent {agent_id} not found")

            # Validate transition: PENDING -> RUNNING
            if not self._validate_state_transition(agent_id, current_status, AgentStatus.RUNNING):
                raise ValueError(f"Cannot start task {task_id}: agent {agent_id} is in {current_status.value} state")

            self.registry.update_status(agent_id, AgentStatus.RUNNING)
            result = await asyncio.wait_for(
                self._run_agent_task(agent, task),
                timeout=self.agent_timeout,
            )

            # Validate transition: RUNNING -> PAUSED
            self._validate_state_transition(agent_id, AgentStatus.RUNNING, AgentStatus.PAUSED)
            self.registry.update_status(agent_id, AgentStatus.PAUSED)

            for hook in self._hooks["post_execute"]:
                await hook(task, result)

            logger.info(f"Task {task_id} completed successfully")

        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}")
            for hook in self._hooks["on_error"]:
                await hook(task, e)

    async def _run_agent_task(self, agent: Dict, task: Dict) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            self._execute_in_thread,
            agent,
            task,
        )

    def _execute_in_thread(self, agent: Dict, task: Dict) -> Any:
        return {"status": "completed", "output": f"Task {task['id']} processed by {agent['name']}"}
