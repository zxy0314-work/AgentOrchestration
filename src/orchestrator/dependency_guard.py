"""Dependency Guard — Prevents implicit dependency on declaration order.

Tracks agent/workflow dependencies and ensures execution order is explicit,
preventing silent parallel execution where ordering is required.
"""
import logging
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class DependencyCycleError(Exception):
    """Raised when a dependency cycle is detected."""
    pass


class DependencyGuard:
    """Tracks dependencies and validates execution order.

    Prevents implicit parallelization of dependent tasks
    and detects dependency cycles.
    """

    def __init__(self):
        # agent_id -> set of agent_ids it depends on
        self._dependencies: Dict[str, Set[str]] = {}
        # agent_id -> set of agent_ids that depend on it
        self._dependents: Dict[str, Set[str]] = {}
        # agent_ids whose dependencies are fully satisfied
        self._resolved: Set[str] = set()

    def add_dependency(self, agent_id: str, depends_on: str) -> bool:
        """Add a dependency. Returns False if cycle detected."""
        # Check for cycles
        if agent_id == depends_on:
            logger.error(f"Self-dependency detected: {agent_id}")
            return False

        # Check if depends_on transitively depends on agent_id
        visited = set()
        stack = [depends_on]
        while stack:
            current = stack.pop()
            if current == agent_id:
                logger.error(f"Cycle detected: {agent_id} -> ... -> {depends_on} -> {agent_id}")
                return False
            if current in visited:
                continue
            visited.add(current)
            stack.extend(self._dependencies.get(current, set()))

        if agent_id not in self._dependencies:
            self._dependencies[agent_id] = set()
        self._dependencies[agent_id].add(depends_on)

        if depends_on not in self._dependents:
            self._dependents[depends_on] = set()
        self._dependents[depends_on].add(agent_id)

        # Check if this made the dependency already resolvable
        self._check_resolved(agent_id)
        return True

    def remove_dependency(self, agent_id: str, depends_on: str) -> None:
        """Remove a dependency."""
        if agent_id in self._dependencies:
            self._dependencies[agent_id].discard(depends_on)
            if not self._dependencies[agent_id]:
                del self._dependencies[agent_id]

        if depends_on in self._dependents:
            self._dependents[depends_on].discard(agent_id)
            if not self._dependents[depends_on]:
                del self._dependents[depends_on]

        self._resolved.discard(agent_id)

    def mark_resolved(self, agent_id: str) -> None:
        """Mark an agent as having its dependencies resolved."""
        self._resolved.add(agent_id)
        # Check if dependents can now run
        for dependent in list(self._dependents.get(agent_id, set())):
            self._check_resolved(dependent)

    def _check_resolved(self, agent_id: str) -> None:
        """Check if an agent's dependencies are all resolved."""
        deps = self._dependencies.get(agent_id, set())
        if deps and deps.issubset(self._resolved):
            self._resolved.add(agent_id)

    def can_execute(self, agent_id: str) -> bool:
        """Check if an agent can execute (all deps resolved)."""
        deps = self._dependencies.get(agent_id, set())
        if not deps:
            return True  # No deps = can run immediately
        return deps.issubset(self._resolved)

    def get_dependencies(self, agent_id: str) -> Set[str]:
        """Get all dependencies for an agent."""
        return self._dependencies.get(agent_id, set())

    def get_dependents(self, agent_id: str) -> Set[str]:
        """Get all agents that depend on this one."""
        return self._dependents.get(agent_id, set())

    def has_unresolved(self) -> bool:
        """Check if there are any unresolved dependencies."""
        all_tracked = set(self._dependencies.keys()) | set(self._dependents.keys())
        return bool(all_tracked - self._resolved)

    def unresolved_agents(self) -> List[str]:
        """List agents with unresolved dependencies."""
        all_tracked = set(self._dependencies.keys()) | set(self._dependents.keys())
        return [a for a in all_tracked if a not in self._resolved]

    def reset(self) -> None:
        """Clear all dependency tracking."""
        self._dependencies.clear()
        self._dependents.clear()
        self._resolved.clear()

    def _validate_no_parallel(self, agent_ids: List[str]) -> bool:
        """Validate that none of the given agents are serial-dependent on each other.

        Use before parallel dispatch to catch implicit ordering.
        """
        agent_set = set(agent_ids)
        for agent_id in agent_ids:
            deps = self._dependencies.get(agent_id, set())
            # If this agent depends on something in the proposed parallel group
            blocking = deps & agent_set
            if blocking:
                logger.warning(
                    f"Cannot parallelize {agent_id}: depends on {blocking} "
                    f"which would create implicit ordering"
                )
                return False
        return True
