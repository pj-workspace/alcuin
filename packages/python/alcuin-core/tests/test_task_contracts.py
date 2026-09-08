from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from alcuin_core.tasks import (
    InvalidTaskTransition,
    Task,
    TaskCommand,
    TaskCommandName,
    TaskCreate,
    TaskEvent,
    TaskEventType,
    TaskPlan,
    TaskPlanUpdate,
    TaskStatus,
    TaskStep,
    TaskStepDraft,
    TaskStepStatus,
    can_complete_task,
    can_transition_task,
    transition_task_status,
)


def test_task_create_and_plan_update_are_strict_api_contracts() -> None:
    create = TaskCreate(goal="Investigate the checkout incident")
    update = TaskPlanUpdate(
        expected_revision=3,
        steps=[TaskStepDraft(title="Collect evidence")],
    )

    assert create.steps == []
    assert update.expected_revision == 3
    assert update.steps[0].description == ""

    with pytest.raises(ValidationError):
        TaskCreate.model_validate({"goal": "Investigate", "unexpected": True})
    with pytest.raises(ValidationError):
        TaskCreate(goal="   ")


def test_task_status_vocabulary_is_stable() -> None:
    assert {status.value for status in TaskStatus} == {
        "draft",
        "planning",
        "ready",
        "running",
        "pause_requested",
        "paused",
        "waiting_for_approval",
        "waiting_for_user",
        "cancel_requested",
        "cancelled",
        "completed",
        "failed",
    }


def test_task_model_controls_share_run_vocabulary_and_default_to_agent_settings() -> (
    None
):
    default = TaskCreate(goal="Create a brief")
    assert default.model_override is None
    assert default.reasoning_effort is None
    selected = TaskCreate(
        goal="Create a brief", model_override="deepseek-v4-pro", reasoning_effort="none"
    )
    assert selected.model_dump(mode="json")["reasoning_effort"] == "none"
    for controls in (
        {"model_override": ""},
        {"model_override": "model with spaces"},
        {"reasoning_effort": "ultra"},
    ):
        with pytest.raises(ValidationError):
            TaskCreate.model_validate({"goal": "Create a brief", **controls})


def test_task_control_events_have_explicit_stable_types() -> None:
    assert TaskEventType.STEP_RETRY_REQUESTED == "task.step.retry_requested"
    assert TaskEventType.INTERVENTION_STEERED == "task.intervention.steered"
    assert (
        TaskEventType.INTERVENTION_INTERRUPT_REQUESTED
        == "task.intervention.interrupt_requested"
    )


def test_task_transition_matrix_rejects_illegal_and_terminal_transitions() -> None:
    assert (
        transition_task_status(TaskStatus.DRAFT, TaskStatus.READY) is TaskStatus.READY
    )
    assert can_transition_task(TaskStatus.RUNNING, TaskStatus.PAUSE_REQUESTED)
    assert not can_transition_task(TaskStatus.DRAFT, TaskStatus.COMPLETED)

    with pytest.raises(InvalidTaskTransition):
        transition_task_status(TaskStatus.DRAFT, TaskStatus.COMPLETED)
    with pytest.raises(InvalidTaskTransition, match="Terminal Task status"):
        transition_task_status(TaskStatus.COMPLETED, TaskStatus.RUNNING)


def test_task_completion_requires_every_declared_step_to_be_safe() -> None:
    result = {"summary": "Incident contained"}
    assert can_complete_task([], result=result)
    assert can_complete_task(
        [TaskStepStatus.COMPLETED, TaskStepStatus.SKIPPED], result=result
    )
    assert not can_complete_task(
        [TaskStepStatus.COMPLETED, TaskStepStatus.PENDING], result=result
    )
    assert not can_complete_task([TaskStepStatus.COMPLETED], result=None)
    assert (
        transition_task_status(
            TaskStatus.RUNNING,
            TaskStatus.COMPLETED,
            step_statuses=[TaskStepStatus.COMPLETED, TaskStepStatus.SKIPPED],
            result=result,
        )
        is TaskStatus.COMPLETED
    )

    with pytest.raises(InvalidTaskTransition, match="non-empty result"):
        transition_task_status(
            TaskStatus.RUNNING,
            TaskStatus.COMPLETED,
            step_statuses=[TaskStepStatus.COMPLETED, TaskStepStatus.RUNNING],
            result=result,
        )
    with pytest.raises(InvalidTaskTransition, match="non-empty result"):
        transition_task_status(
            TaskStatus.RUNNING,
            TaskStatus.COMPLETED,
            step_statuses=[TaskStepStatus.COMPLETED],
        )


def test_completed_task_projection_cannot_hide_unfinished_steps() -> None:
    step = TaskStep(
        id="stp_1",
        task_id="tsk_1",
        ordinal=0,
        title="Collect evidence",
        status="running",
    )
    plan = TaskPlan(
        id="plan_1",
        task_id="tsk_1",
        steps=[step],
        updated_at="2026-08-30T00:00:00Z",
    )

    with pytest.raises(ValidationError, match="non-empty result"):
        Task(
            id="tsk_1",
            workspace_id="ws_1",
            thread_id="thr_1",
            goal="Investigate",
            status="completed",
            plan=plan,
            result={"summary": "Incomplete"},
            revision=2,
            created_at="2026-08-30T00:00:00Z",
            updated_at="2026-08-30T00:00:00Z",
        )


def test_task_command_schema_distinguishes_control_and_intervention_payloads() -> None:
    pause = TaskCommand(
        command="pause",
        idempotency_key="pause:tsk_1:1",
        expected_revision=2,
        expected_status="running",
    )
    steer = TaskCommand(
        command="steer",
        idempotency_key="steer:tsk_1:1",
        expected_revision=2,
        message="Check the new incident window before continuing.",
    )
    retry = TaskCommand(
        command="retry",
        idempotency_key="retry:stp_1:2",
        expected_revision=2,
        step_id="stp_1",
    )

    assert pause.command is TaskCommandName.PAUSE
    assert steer.message
    assert retry.step_id == "stp_1"

    with pytest.raises(ValidationError, match="require message"):
        TaskCommand(
            command="queue", idempotency_key="queue:tsk_1:1", expected_revision=2
        )
    with pytest.raises(ValidationError, match="require step_id"):
        TaskCommand(
            command="retry", idempotency_key="retry:tsk_1:1", expected_revision=2
        )
    with pytest.raises(ValidationError, match="only valid for retry"):
        TaskCommand(
            command="pause",
            idempotency_key="pause:tsk_1:2",
            expected_revision=2,
            step_id="stp_1",
        )


def test_public_task_schemas_do_not_expose_private_reasoning_fields() -> None:
    forbidden = {"reasoning", "raw_reasoning", "chain_of_thought", "thought"}

    def property_names(value: Any) -> set[str]:
        if isinstance(value, dict):
            names = set(value.get("properties", {}))
            for nested in value.values():
                names.update(property_names(nested))
            return names
        if isinstance(value, list):
            names: set[str] = set()
            for nested in value:
                names.update(property_names(nested))
            return names
        return set()

    for model in (Task, TaskPlan, TaskStep, TaskCommand, TaskEvent):
        assert property_names(model.model_json_schema()).isdisjoint(forbidden)

    with pytest.raises(ValidationError, match="private reasoning"):
        TaskEvent(
            id="tevt_1",
            workspace_id="ws_1",
            task_id="tsk_1",
            sequence=1,
            type="task.started",
            timestamp="2026-08-30T00:00:00Z",
            payload={"nested": {"raw_reasoning": "never public"}},
        )
