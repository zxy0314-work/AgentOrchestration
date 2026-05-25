"""Workflow Manager — Defines and executes multi-step agent workflows."""

from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from uuid import uuid4
import logging

logger = logging.getLogger(__name__)


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowStep:
    def __init__(self, name: str, handler: Callable, retries: int = 0, timeout: int = 300, depends_on: Optional[List[str]] = None):
        self.id = str(uuid4())
        self.name = name
        self.handler = handler
        self.retries = retries
        self.timeout = timeout
        self.depends_on = depends_on or []  # IDs of steps this step depends on
        self.status = StepStatus.PENDING
        self.result: Any = None
        self.error: Optional[str] = None


class WorkflowGraphValidationError(Exception):
    """Raised when workflow graph validation fails."""
    pass


class Workflow:
    def __init__(self, name: str, description: str = ""):
        self.id = str(uuid4())
        self.name = name
        self.description = description
        self.steps: List[WorkflowStep] = []
        self._step_map: Dict[str, WorkflowStep] = {}
        self.status = StepStatus.PENDING

    def add_step(self, step: WorkflowStep) -> "Workflow":
        self.steps.append(step)
        self._step_map[step.id] = step
        return self

    def get_step(self, step_id: str) -> Optional[WorkflowStep]:
        return self._step_map.get(step_id)

    def validate_graph(self) -> List[str]:
        """Validate the workflow graph structure.

        Checks performed:
        1. No cycles in the dependency graph
        2. All dependency references resolve to existing steps
        3. No duplicate step names
        4. Graph is a valid DAG (Directed Acyclic Graph)
        5. No self-referencing dependencies

        Returns:
            List of validation error messages. Empty list means valid.
        """
        errors: List[str] = []

        if not self.steps:
            errors.append("Workflow has no steps")
            return errors

        # Check for duplicate step names
        name_counts: Dict[str, int] = {}
        for step in self.steps:
            name_counts[step.name] = name_counts.get(step.name, 0) + 1
        for name, count in name_counts.items():
            if count > 1:
                errors.append(f"Duplicate step name '{name}' found ({count} occurrences)")

        # Build dependency map and validate references
        step_ids = {step.id for step in self.steps}
        dependency_map: Dict[str, List[str]] = {}

        for step in self.steps:
            # Check self-referencing dependencies
            if step.id in step.depends_on:
                errors.append(f"Step '{step.name}' has a self-referencing dependency")

            for dep_id in step.depends_on:
                if dep_id not in step_ids:
                    errors.append(
                        f"Step '{step.name}' depends on non-existent step '{dep_id[:8]}...'"
                    )
            dependency_map[step.id] = step.depends_on

        # Check for cycles using DFS-based topological sort
        if step_ids and not self._has_valid_topological_order(step_ids, dependency_map):
            errors.append(
                "Workflow graph contains a cycle. Dependencies must form a Directed Acyclic Graph (DAG)."
            )

        # Check that there's at least one entry point (step with no dependencies)
        if step_ids:
            has_entry_point = any(
                not self._step_map[sid].depends_on
                for sid in step_ids
                if sid in self._step_map
            )
            if not has_entry_point:
                errors.append("No entry point found: all steps have dependencies (circular dependency chain)")

        return errors

    def _has_valid_topological_order(
        self, step_ids: Set[str], deps: Dict[str, List[str]]
    ) -> bool:
        """Check if the dependency graph is a valid DAG using DFS cycle detection.

        Returns True if no cycles exist.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {sid: WHITE for sid in step_ids}

        def dfs(node: str) -> bool:
            """Returns True if a cycle is found."""
            color[node] = GRAY
            for neighbor in deps.get(node, []):
                if neighbor not in color:
                    continue  # Skip external refs (already caught above)
                if color[neighbor] == GRAY:
                    return True  # Back edge = cycle
                if color[neighbor] == WHITE:
                    if dfs(neighbor):
                        return True
            color[node] = BLACK
            return False

        for sid in step_ids:
            if color[sid] == WHITE:
                if dfs(sid):
                    return False  # Cycle found

        return True  # Valid DAG


class WorkflowManager:
    def __init__(self):
        self._workflows: Dict[str, Workflow] = {}

    def create_workflow(self, name: str, description: str = "") -> Workflow:
        workflow = Workflow(name, description)
        self._workflows[workflow.id] = workflow
        return workflow

    def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        return self._workflows.get(workflow_id)

    def list_workflows(self) -> List[Workflow]:
        return list(self._workflows.values())

    def delete_workflow(self, workflow_id: str) -> bool:
        return self._workflows.pop(workflow_id, None) is not None

    def validate_workflow_graph(self, workflow_id: str) -> List[str]:
        """Validate the workflow graph before execution.

        This is the public validation method that enforces policy injection
        validation. It must be called BEFORE execute_workflow to ensure
        the automatic guard nodes invariant is enforced.

        Args:
            workflow_id: The ID of the workflow to validate.

        Returns:
            List of validation errors. Empty list means the graph is valid.
        """
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return [f"Workflow '{workflow_id[:8]}...' not found"]

        # Check lifecycle state
        if workflow.status == StepStatus.RUNNING:
            return ["Cannot validate graph: workflow is already running"]
        if workflow.status == StepStatus.COMPLETED:
            return ["Cannot validate graph: workflow is already completed"]
        if workflow.status == StepStatus.FAILED:
            return ["Cannot validate graph: workflow is in failed state"]

        # Perform graph validation
        errors = workflow.validate_graph()
        if errors:
            return errors

        return []  # Valid

    def execute_workflow(self, workflow_id: str) -> bool:
        """Execute a workflow after validating its graph.

        Validates the workflow graph before any state mutation.
        Raises WorkflowGraphValidationError if the graph is invalid.
        """
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return False

        # Validate graph BEFORE state mutation (automatic guard nodes enforcement)
        validation_errors = self.validate_workflow_graph(workflow_id)
        if validation_errors:
            error_msg = "; ".join(validation_errors)
            logger.error(
                f"Workflow '{workflow.name}' ({workflow_id[:8]}...) rejected: "
                f"graph validation failed: {error_msg}"
            )
            raise WorkflowGraphValidationError(
                f"Cannot execute workflow '{workflow.name}': {error_msg}"
            )

        # All validations passed — proceed with execution
        workflow.status = StepStatus.RUNNING
        logger.info(
            f"Workflow '{workflow.name}' ({workflow_id[:8]}...) starting execution "
            f"with {len(workflow.steps)} steps"
        )

        for step in workflow.steps:
            step.status = StepStatus.RUNNING
            try:
                result = step.handler()
                step.result = result
                step.status = StepStatus.COMPLETED
                logger.debug(f"Step '{step.name}' completed successfully")
            except Exception as e:
                step.error = str(e)
                step.status = StepStatus.FAILED
                workflow.status = StepStatus.FAILED
                logger.error(
                    f"Step '{step.name}' failed in workflow '{workflow.name}': {e}"
                )
                return False

        workflow.status = StepStatus.COMPLETED
        logger.info(
            f"Workflow '{workflow.name}' ({workflow_id[:8]}...) completed successfully"
        )
        return True
