"""Tests for workflow graph validation."""

import pytest
from src.orchestrator.workflow import (
    WorkflowManager, Workflow, WorkflowStep, WorkflowStep as Step,
    WorkflowGraphValidationError, StepStatus
)


def simple_handler():
    return "done"


def failing_handler():
    raise ValueError("handler failed")


class TestWorkflowGraphValidation:

    def test_valid_graph_passes(self):
        """A valid linear workflow graph should pass validation."""
        wf = Workflow("test")
        step1 = Step("step1", simple_handler)
        step2 = Step("step2", simple_handler, depends_on=[step1.id])
        wf.add_step(step1).add_step(step2)

        errors = wf.validate_graph()
        assert errors == []

    def test_empty_workflow_fails(self):
        """A workflow with no steps should fail validation."""
        wf = Workflow("empty")
        errors = wf.validate_graph()
        assert len(errors) > 0
        assert "no steps" in errors[0].lower()

    def test_cycle_detection(self):
        """A cycle in the dependency graph should be detected."""
        wf = Workflow("cycle")
        step1 = Step("step1", simple_handler)
        step2 = Step("step2", simple_handler, depends_on=[step1.id])
        step3 = Step("step3", simple_handler, depends_on=[step2.id])
        # Create cycle: step1 depends on step3
        step1.depends_on = [step3.id]

        wf.add_step(step1).add_step(step2).add_step(step3)

        errors = wf.validate_graph()
        assert any("cycle" in e.lower() for e in errors)

    def test_nonexistent_dependency_detected(self):
        """A dependency on a non-existent step should be detected."""
        wf = Workflow("missing")
        step1 = Step("step1", simple_handler, depends_on=["nonexistent-id"])
        wf.add_step(step1)

        errors = wf.validate_graph()
        assert any("non-existent" in e.lower() or "not found" in e.lower() for e in errors)

    def test_self_referencing_dependency(self):
        """A step that depends on itself should be detected."""
        wf = Workflow("self-ref")
        step1 = Step("step1", simple_handler, depends_on=["self"])
        # Use actual id
        step1.depends_on = [step1.id]
        wf.add_step(step1)

        errors = wf.validate_graph()
        assert any("self-referencing" in e.lower() for e in errors)

    def test_duplicate_step_names_detected(self):
        """Duplicate step names should be detected."""
        wf = Workflow("dup")
        step1 = Step("same_name", simple_handler)
        step2 = Step("same_name", simple_handler)  # Same name
        wf.add_step(step1).add_step(step2)

        errors = wf.validate_graph()
        assert any("duplicate" in e.lower() for e in errors)

    def test_no_entry_point_detected(self):
        """Graph with all steps having dependencies (circular) should fail."""
        wf = Workflow("no-entry")
        step1 = Step("step1", simple_handler)
        step2 = Step("step2", simple_handler, depends_on=[step1.id])
        step1.depends_on = [step2.id]  # Mutual dependency
        wf.add_step(step1).add_step(step2)

        errors = wf.validate_graph()
        # Should detect cycle and/or missing entry point
        assert len(errors) > 0

    def test_diamond_graph_is_valid(self):
        """A diamond-shaped DAG should pass validation."""
        wf = Workflow("diamond")
        start = Step("start", simple_handler)
        mid1 = Step("mid1", simple_handler, depends_on=[start.id])
        mid2 = Step("mid2", simple_handler, depends_on=[start.id])
        end = Step("end", simple_handler, depends_on=[mid1.id, mid2.id])
        for s in [start, mid1, mid2, end]:
            wf.add_step(s)

        errors = wf.validate_graph()
        assert errors == []


class TestWorkflowManager:

    def setup_method(self):
        self.manager = WorkflowManager()

    def test_execute_valid_workflow(self):
        """A valid workflow should execute successfully."""
        wf = self.manager.create_workflow("test")
        wf.add_step(Step("step1", simple_handler))
        wf.add_step(Step("step2", simple_handler))

        result = self.manager.execute_workflow(wf.id)
        assert result is True
        assert wf.status == StepStatus.COMPLETED

    def test_execute_invalid_workflow_raises(self):
        """An invalid workflow graph should raise WorkflowGraphValidationError."""
        wf = self.manager.create_workflow("invalid")
        step1 = Step("step1", simple_handler)
        step2 = Step("step2", simple_handler, depends_on=[step1.id])
        step1.depends_on = [step2.id]  # Cycle
        wf.add_step(step1).add_step(step2)

        with pytest.raises(WorkflowGraphValidationError) as exc:
            self.manager.execute_workflow(wf.id)

        assert "cycle" in str(exc.value).lower() or "graph" in str(exc.value).lower()

    def test_execute_nonexistent_workflow_returns_false(self):
        """Executing a non-existent workflow should return False."""
        result = self.manager.execute_workflow("nonexistent")
        assert result is False

    def test_validate_before_execution(self):
        """Validation should occur before state mutation."""
        wf = self.manager.create_workflow("gate-test")
        # Create a cycle to trigger validation
        step1 = Step("step1", simple_handler)
        step1.depends_on = [step1.id]  # Self-reference
        wf.add_step(step1)

        # State should still be PENDING when validation fails
        assert wf.status == StepStatus.PENDING

        with pytest.raises(WorkflowGraphValidationError):
            self.manager.execute_workflow(wf.id)

        # State should NOT have changed to RUNNING
        assert wf.status == StepStatus.PENDING, (
            f"State mutated to {wf.status} despite validation failure"
        )

    def test_workflow_with_failing_step(self):
        """A workflow with a failing step should report the step failure."""
        wf = self.manager.create_workflow("fail-test")
        wf.add_step(Step("good", simple_handler))
        wf.add_step(Step("bad", failing_handler))

        with pytest.raises(WorkflowGraphValidationError):
            # Should pass validation but fail during execution
            # First check validation
            self.manager.validate_workflow_graph(wf.id)
            errors = self.manager.validate_workflow_graph(wf.id)
            assert errors == []

        # The execution will fail but not due to validation
        # It will return False for the failing step
        result = self.manager.execute_workflow(wf.id)
        assert result is False
        assert wf.status == StepStatus.FAILED

    def test_validate_already_running(self):
        """Validating a running workflow should return error."""
        wf = self.manager.create_workflow("running-test")
        wf.add_step(Step("step1", simple_handler))

        # Set status to RUNNING
        wf.status = StepStatus.RUNNING

        errors = self.manager.validate_workflow_graph(wf.id)
        assert any("already running" in e.lower() for e in errors)
