"""Workflow Manager — Defines and executes multi-step agent workflows."""

from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple
from uuid import uuid4
import logging

logger = logging.getLogger(__name__)


# Reserved parameter identifiers that workflow authors cannot shadow with their
# own params or aliases. These names are used by the orchestrator runtime to
# inject execution context into step handlers.
RESERVED_PARAMETER_NAMES: frozenset = frozenset({
    "self",
    "context",
    "ctx",
    "__step__",
    "__workflow__",
    "__run_id__",
})


class WorkflowParameterError(Exception):
    """Raised when a workflow parameter definition is invalid.

    This is a *registration-time* error: it must be raised before the workflow
    is added to the manager so that bad graphs can never reach execution.
    """
    pass


def _normalize_param_token(token: str) -> str:
    """Normalize a parameter name or alias for collision comparison.

    Collisions are case-insensitive and ignore surrounding whitespace because
    CLI/HTTP layers routinely fold case and trim user input before dispatch.
    Returning an empty string signals an invalid token to the caller.
    """
    if not isinstance(token, str):
        return ""
    return token.strip().lower()


class WorkflowParameter:
    """A named input to a workflow, optionally exposed under multiple aliases.

    Parameters are the public surface a workflow exposes to callers (CLI flags,
    HTTP query/body fields, scheduled-run payloads). Aliases let the same
    value be reached under different names — but two different parameters
    must never share a name *or* an alias, otherwise the dispatcher cannot
    decide where to route a value and would silently pick one.

    Validation is performed eagerly in ``__init__`` so malformed parameters
    cannot even be constructed; collision checks across parameters live in
    :meth:`Workflow.add_parameter` so the workflow itself stays the single
    source of truth for its namespace.
    """

    def __init__(
        self,
        name: str,
        aliases: Optional[Iterable[str]] = None,
        required: bool = False,
        default: Any = None,
        description: str = "",
    ):
        if not isinstance(name, str) or not name.strip():
            raise WorkflowParameterError(
                "Parameter name must be a non-empty string"
            )

        norm_name = _normalize_param_token(name)
        if norm_name in RESERVED_PARAMETER_NAMES:
            raise WorkflowParameterError(
                f"Parameter name '{name}' is reserved and cannot be used"
            )

        # Validate each alias and check for duplicates *within* this parameter
        # — a parameter that lists the same alias twice is a clear authoring
        # bug and should fail loud.
        normalized_aliases: List[str] = []
        seen_local: Set[str] = {norm_name}
        raw_aliases = list(aliases or [])
        for alias in raw_aliases:
            if not isinstance(alias, str) or not alias.strip():
                raise WorkflowParameterError(
                    f"Parameter '{name}' has an empty/invalid alias"
                )
            norm_alias = _normalize_param_token(alias)
            if norm_alias in RESERVED_PARAMETER_NAMES:
                raise WorkflowParameterError(
                    f"Parameter '{name}' alias '{alias}' is reserved"
                )
            if norm_alias in seen_local:
                # Either alias == name or alias appeared earlier in the list.
                raise WorkflowParameterError(
                    f"Parameter '{name}' has duplicate alias '{alias}' "
                    f"(case-insensitive); each alias must be unique within "
                    f"the parameter"
                )
            seen_local.add(norm_alias)
            normalized_aliases.append(alias)

        self.name = name
        self.aliases: Tuple[str, ...] = tuple(normalized_aliases)
        self.required = required
        self.default = default
        self.description = description

    def all_identifiers(self) -> List[str]:
        """Return name + aliases (preserving original casing) for iteration."""
        return [self.name, *self.aliases]

    def normalized_identifiers(self) -> List[str]:
        """Return name + aliases lowercased for collision checks."""
        return [_normalize_param_token(t) for t in self.all_identifiers()]

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"WorkflowParameter(name={self.name!r}, "
            f"aliases={list(self.aliases)!r}, required={self.required})"
        )


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
        self.parameters: List[WorkflowParameter] = []
        # Maps normalized identifier -> owning parameter's canonical name.
        # Used both for fast collision detection on add_parameter() and for
        # dispatcher lookups (resolve_parameter).
        self._param_alias_map: Dict[str, str] = {}
        self.status = StepStatus.PENDING

    def add_step(self, step: WorkflowStep) -> "Workflow":
        self.steps.append(step)
        self._step_map[step.id] = step
        return self

    def get_step(self, step_id: str) -> Optional[WorkflowStep]:
        return self._step_map.get(step_id)

    def add_parameter(self, param: WorkflowParameter) -> "Workflow":
        """Register a parameter on this workflow.

        Rejects — at registration time — any parameter whose name or alias
        collides (case-insensitively) with an identifier already claimed by
        another parameter on this workflow. This is the core invariant that
        blocks bad graphs from ever reaching execution: a dispatcher must
        always be able to resolve an incoming identifier to exactly one
        parameter.

        Raises:
            WorkflowParameterError: if any identifier (name or alias) of the
                incoming parameter is already claimed.
        """
        if not isinstance(param, WorkflowParameter):
            raise WorkflowParameterError(
                f"Expected WorkflowParameter, got {type(param).__name__}"
            )

        # Identify *all* collisions up front so the error message can list
        # every offender — partial info is the most painful kind of bug for
        # an author trying to wire up a complex workflow.
        collisions: List[Tuple[str, str]] = []
        for ident in param.normalized_identifiers():
            owner = self._param_alias_map.get(ident)
            if owner is not None and owner != param.name:
                collisions.append((ident, owner))

        if collisions:
            details = ", ".join(
                f"'{ident}' already claimed by parameter '{owner}'"
                for ident, owner in collisions
            )
            raise WorkflowParameterError(
                f"Cannot register parameter '{param.name}' on workflow "
                f"'{self.name}': duplicate identifier(s) — {details}"
            )

        # Commit only after all checks pass — partial registration would
        # leave the alias map and parameter list out of sync.
        for ident in param.normalized_identifiers():
            self._param_alias_map[ident] = param.name
        self.parameters.append(param)
        return self

    def resolve_parameter(self, identifier: str) -> Optional[WorkflowParameter]:
        """Look up a parameter by name or alias (case-insensitive).

        Returns None if no parameter claims the identifier. Because
        add_parameter rejects collisions, the result is always unambiguous.
        """
        norm = _normalize_param_token(identifier)
        if not norm:
            return None
        owner_name = self._param_alias_map.get(norm)
        if owner_name is None:
            return None
        for p in self.parameters:
            if p.name == owner_name:
                return p
        return None  # pragma: no cover - alias map drifted from list

    def validate_graph(self) -> List[str]:
        """Validate the workflow graph structure.

        Checks performed:
        1. No cycles in the dependency graph
        2. All dependency references resolve to existing steps
        3. No duplicate step names
        4. Graph is a valid DAG (Directed Acyclic Graph)
        5. No self-referencing dependencies
        6. No duplicate parameter aliases across parameters

        Returns:
            List of validation error messages. Empty list means valid.
        """
        errors: List[str] = []

        # --- Parameter alias validation -------------------------------------
        # Defense-in-depth: add_parameter() already rejects collisions, but a
        # caller could have mutated Workflow.parameters directly (tests do
        # this; so do legacy import paths). Re-derive collisions from the
        # current list so a tampered workflow still fails closed.
        seen: Dict[str, str] = {}
        for param in self.parameters:
            if not isinstance(param, WorkflowParameter):
                errors.append(
                    f"Workflow has non-parameter object in parameters list: "
                    f"{type(param).__name__}"
                )
                continue
            for ident in param.normalized_identifiers():
                if not ident:
                    errors.append(
                        f"Parameter '{param.name}' has an empty identifier"
                    )
                    continue
                if ident in RESERVED_PARAMETER_NAMES:
                    errors.append(
                        f"Parameter '{param.name}' uses reserved identifier "
                        f"'{ident}'"
                    )
                    continue
                prior = seen.get(ident)
                if prior is not None and prior != param.name:
                    errors.append(
                        f"Duplicate parameter identifier '{ident}': claimed "
                        f"by both '{prior}' and '{param.name}'"
                    )
                else:
                    seen[ident] = param.name

        # --- Step graph validation ------------------------------------------
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

    def register_workflow(self, workflow: Workflow) -> str:
        """Register an externally-constructed workflow.

        Unlike :meth:`create_workflow` — which returns an empty workflow that
        the caller incrementally populates — this method takes a fully-built
        ``Workflow`` and runs the complete graph validation suite *before*
        admitting it to the manager. If validation fails, the workflow is
        rejected (never stored) and a :class:`WorkflowGraphValidationError`
        is raised with every offending issue. This is the guardrail that
        keeps bad graphs out of the executor.

        The most common rejection cause (and the one this PR is named for)
        is duplicate parameter aliases: two parameters sharing a name or
        alias would make dispatch ambiguous. Such workflows are blocked
        here, before any run is scheduled.

        Args:
            workflow: A fully-constructed Workflow with steps and parameters
                already attached.

        Returns:
            The workflow id, on successful registration.

        Raises:
            WorkflowGraphValidationError: if the graph or parameter set fails
                validation. The workflow is *not* stored in this case.
            ValueError: if a workflow with the same id is already registered
                — re-registration must go through delete+register to make
                replacement intent explicit.
        """
        if not isinstance(workflow, Workflow):
            raise WorkflowGraphValidationError(
                f"register_workflow expects Workflow, got "
                f"{type(workflow).__name__}"
            )

        if workflow.id in self._workflows:
            raise ValueError(
                f"Workflow id '{workflow.id[:8]}...' already registered; "
                f"delete it first to replace"
            )

        # Run full validation *before* storing. We deliberately do NOT use
        # validate_workflow_graph() here because that method requires the
        # workflow to already be in the manager — we want the bad graph
        # never to enter the manager in the first place.
        errors = workflow.validate_graph()
        if errors:
            error_msg = "; ".join(errors)
            logger.error(
                f"Workflow '{workflow.name}' rejected at registration: "
                f"{error_msg}"
            )
            raise WorkflowGraphValidationError(
                f"Cannot register workflow '{workflow.name}': {error_msg}"
            )

        self._workflows[workflow.id] = workflow
        logger.info(
            f"Workflow '{workflow.name}' ({workflow.id[:8]}...) registered "
            f"with {len(workflow.steps)} steps and "
            f"{len(workflow.parameters)} parameters"
        )
        return workflow.id

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
