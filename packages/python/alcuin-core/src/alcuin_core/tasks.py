"""Framework-neutral contracts and state transitions for durable Agent tasks.

The models in this module describe operator-visible progress only. They do not
carry provider chain-of-thought or other private model reasoning.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import Field, model_validator

from .contracts import ReasoningEffort, StrictModel


class TaskStatus(StrEnum):
    DRAFT = "draft"
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_USER = "waiting_for_user"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_USER = "waiting_for_user"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class TaskAttemptStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_USER = "waiting_for_user"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class TaskEvidenceKind(StrEnum):
    ARTIFACT = "artifact"
    CITATION = "citation"
    TOOL_RESULT = "tool_result"
    CHECKPOINT = "checkpoint"
    NOTE = "note"


class TaskInterventionKind(StrEnum):
    QUEUE = "queue"
    STEER = "steer"
    INTERRUPT = "interrupt"


class TaskInterventionStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"


class TaskCommandName(StrEnum):
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    RETRY = "retry"
    QUEUE = "queue"
    STEER = "steer"
    INTERRUPT = "interrupt"


class TaskEventType(StrEnum):
    TASK_CREATED = "task.created"
    PLAN_UPDATED = "task.plan.updated"
    TASK_STARTED = "task.started"
    PAUSE_REQUESTED = "task.pause_requested"
    TASK_PAUSED = "task.paused"
    TASK_RESUMED = "task.resumed"
    WAITING_FOR_APPROVAL = "task.waiting_for_approval"
    WAITING_FOR_USER = "task.waiting_for_user"
    CANCEL_REQUESTED = "task.cancel_requested"
    TASK_CANCELLED = "task.cancelled"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    STEP_STARTED = "task.step.started"
    STEP_COMPLETED = "task.step.completed"
    STEP_FAILED = "task.step.failed"
    STEP_RETRY_REQUESTED = "task.step.retry_requested"
    STEP_SKIPPED = "task.step.skipped"
    ATTEMPT_STARTED = "task.attempt.started"
    ATTEMPT_COMPLETED = "task.attempt.completed"
    ATTEMPT_FAILED = "task.attempt.failed"
    CHECKPOINT_CREATED = "task.checkpoint.created"
    EVIDENCE_ADDED = "task.evidence.added"
    INTERVENTION_QUEUED = "task.intervention.queued"
    INTERVENTION_STEERED = "task.intervention.steered"
    INTERVENTION_INTERRUPT_REQUESTED = "task.intervention.interrupt_requested"
    INTERVENTION_APPLIED = "task.intervention.applied"
    INTERVENTION_REJECTED = "task.intervention.rejected"


class TaskStepDraft(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2_000)

    @model_validator(mode="after")
    def validate_text(self) -> "TaskStepDraft":
        if not self.title.strip():
            raise ValueError("Task step title cannot be blank")
        return self


class TaskCreate(StrictModel):
    goal: str = Field(min_length=1, max_length=4_000)
    steps: list[TaskStepDraft] = Field(default_factory=list, max_length=64)
    model_override: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    reasoning_effort: ReasoningEffort | None = None

    @model_validator(mode="after")
    def validate_goal(self) -> "TaskCreate":
        if not self.goal.strip():
            raise ValueError("Task goal cannot be blank")
        return self


class TaskPlanProposalRequest(StrictModel):
    """Request an ephemeral plan; creating or starting a Task is a separate action."""

    goal: str = Field(min_length=1, max_length=4_000)
    model_override: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    reasoning_effort: ReasoningEffort | None = None

    @model_validator(mode="after")
    def validate_goal(self) -> "TaskPlanProposalRequest":
        if not self.goal.strip():
            raise ValueError("Task goal cannot be blank")
        return self


class TaskPlanProposal(StrictModel):
    goal: str = Field(min_length=1, max_length=4_000)
    steps: list[TaskStepDraft] = Field(min_length=1, max_length=8)
    model: str = Field(min_length=1, max_length=200)
    reasoning_effort: ReasoningEffort


class TaskPlanUpdate(StrictModel):
    """Compare-and-swap replacement for the editable Task plan."""

    expected_revision: int = Field(ge=0)
    goal: str | None = Field(default=None, min_length=1, max_length=4_000)
    steps: list[TaskStepDraft] = Field(max_length=64)

    @model_validator(mode="after")
    def validate_goal(self) -> "TaskPlanUpdate":
        if self.goal is not None and not self.goal.strip():
            raise ValueError("Task goal cannot be blank")
        return self


class TaskEvidence(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=160)
    step_id: str = Field(min_length=1, max_length=160)
    attempt_id: str | None = Field(default=None, min_length=1, max_length=160)
    kind: TaskEvidenceKind
    label: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4_000)
    resource_id: str | None = Field(default=None, min_length=1, max_length=200)
    source_uri: str | None = Field(default=None, min_length=1, max_length=2_000)
    created_at: str


class TaskAttempt(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=160)
    step_id: str = Field(min_length=1, max_length=160)
    run_id: str | None = Field(default=None, min_length=1, max_length=160)
    number: int = Field(ge=1)
    status: TaskAttemptStatus
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = Field(default=None, max_length=2_000)


class TaskStep(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=160)
    ordinal: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2_000)
    status: TaskStepStatus = TaskStepStatus.PENDING
    attempts: list[TaskAttempt] = Field(default_factory=list)
    evidence: list[TaskEvidence] = Field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None


class TaskPlan(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=160)
    steps: list[TaskStep] = Field(default_factory=list, max_length=64)
    updated_at: str

    @model_validator(mode="after")
    def validate_steps(self) -> "TaskPlan":
        ordinals = [step.ordinal for step in self.steps]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("Task step ordinals must be unique")
        if any(step.task_id != self.task_id for step in self.steps):
            raise ValueError("Task plan steps must belong to the same Task")
        return self


class TaskCheckpoint(StrictModel):
    """Immutable, operator-safe boundary from which execution may resume."""

    id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=160)
    sequence: int = Field(ge=1)
    task_revision: int = Field(ge=0)
    after_step_id: str | None = Field(default=None, min_length=1, max_length=160)
    next_step_id: str | None = Field(default=None, min_length=1, max_length=160)
    completed_step_ids: list[str] = Field(default_factory=list, max_length=64)
    created_at: str

    @model_validator(mode="after")
    def validate_completed_steps(self) -> "TaskCheckpoint":
        if len(self.completed_step_ids) != len(set(self.completed_step_ids)):
            raise ValueError("completed_step_ids must be unique")
        if self.next_step_id and self.next_step_id in self.completed_step_ids:
            raise ValueError("next_step_id cannot already be completed")
        return self


class TaskIntervention(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=160)
    kind: TaskInterventionKind
    status: TaskInterventionStatus = TaskInterventionStatus.PENDING
    message: str = Field(min_length=1, max_length=40_000)
    target_run_id: str | None = Field(default=None, min_length=1, max_length=160)
    created_at: str
    applied_at: str | None = None

    @model_validator(mode="after")
    def validate_message(self) -> "TaskIntervention":
        if not self.message.strip():
            raise ValueError("Task intervention message cannot be blank")
        return self


class TaskCommand(StrictModel):
    command: TaskCommandName
    idempotency_key: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    expected_revision: int = Field(ge=0)
    expected_status: TaskStatus | None = None
    step_id: str | None = Field(default=None, min_length=1, max_length=160)
    message: str | None = Field(default=None, max_length=40_000)

    @model_validator(mode="after")
    def validate_command_payload(self) -> "TaskCommand":
        if self.command is TaskCommandName.RETRY and self.step_id is None:
            raise ValueError("retry commands require step_id")
        if self.command is not TaskCommandName.RETRY and self.step_id is not None:
            raise ValueError("step_id is only valid for retry commands")
        if self.command in {TaskCommandName.QUEUE, TaskCommandName.STEER}:
            if self.message is None or not self.message.strip():
                raise ValueError(f"{self.command.value} commands require message")
        if self.message is not None and not self.message.strip():
            raise ValueError("Task command message cannot be blank")
        return self


class Task(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    workspace_id: str = Field(min_length=1, max_length=160)
    thread_id: str = Field(min_length=1, max_length=160)
    goal: str = Field(min_length=1, max_length=4_000)
    model_override: str | None = Field(default=None, min_length=1, max_length=200)
    reasoning_effort: ReasoningEffort | None = None
    status: TaskStatus
    plan: TaskPlan
    current_step_id: str | None = Field(default=None, min_length=1, max_length=160)
    result: dict[str, Any] | None = None
    latest_checkpoint: TaskCheckpoint | None = None
    pending_intervention: TaskIntervention | None = None
    revision: int = Field(ge=0)
    created_at: str
    updated_at: str
    completed_at: str | None = None
    error: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_projection(self) -> "Task":
        if self.plan.task_id != self.id:
            raise ValueError("Task plan must belong to the Task")
        step_ids = {step.id for step in self.plan.steps}
        if self.current_step_id and self.current_step_id not in step_ids:
            raise ValueError("current_step_id must reference a Task plan step")
        if self.latest_checkpoint and self.latest_checkpoint.task_id != self.id:
            raise ValueError("latest_checkpoint must belong to the Task")
        if self.pending_intervention and self.pending_intervention.task_id != self.id:
            raise ValueError("pending_intervention must belong to the Task")
        if self.result is not None:
            _reject_private_reasoning_fields(self.result, label="Task result")
        if self.status is TaskStatus.COMPLETED:
            if not can_complete_task(
                (step.status for step in self.plan.steps), result=self.result
            ):
                raise ValueError(
                    "A completed Task requires a non-empty result and no unfinished steps"
                )
        return self


class TaskEvent(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    workspace_id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=160)
    sequence: int = Field(ge=1)
    type: TaskEventType
    timestamp: str
    step_id: str | None = Field(default=None, min_length=1, max_length=160)
    attempt_id: str | None = Field(default=None, min_length=1, max_length=160)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_payload(self) -> "TaskEvent":
        _reject_private_reasoning_fields(self.payload, label="Task event payload")
        return self


TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.CANCELLED, TaskStatus.COMPLETED, TaskStatus.FAILED}
)
COMPLETABLE_STEP_STATUSES = frozenset(
    {TaskStepStatus.COMPLETED, TaskStepStatus.SKIPPED}
)

TASK_STATUS_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = MappingProxyType(
    {
        TaskStatus.DRAFT: frozenset(
            {TaskStatus.PLANNING, TaskStatus.READY, TaskStatus.CANCEL_REQUESTED}
        ),
        TaskStatus.PLANNING: frozenset(
            {TaskStatus.READY, TaskStatus.CANCEL_REQUESTED, TaskStatus.FAILED}
        ),
        TaskStatus.READY: frozenset(
            {
                TaskStatus.PLANNING,
                TaskStatus.RUNNING,
                TaskStatus.CANCEL_REQUESTED,
                TaskStatus.FAILED,
            }
        ),
        TaskStatus.RUNNING: frozenset(
            {
                TaskStatus.PAUSE_REQUESTED,
                TaskStatus.WAITING_FOR_APPROVAL,
                TaskStatus.WAITING_FOR_USER,
                TaskStatus.CANCEL_REQUESTED,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
            }
        ),
        TaskStatus.PAUSE_REQUESTED: frozenset(
            {TaskStatus.PAUSED, TaskStatus.CANCEL_REQUESTED, TaskStatus.FAILED}
        ),
        TaskStatus.PAUSED: frozenset(
            {TaskStatus.RUNNING, TaskStatus.CANCEL_REQUESTED, TaskStatus.FAILED}
        ),
        TaskStatus.WAITING_FOR_APPROVAL: frozenset(
            {
                TaskStatus.RUNNING,
                TaskStatus.PAUSE_REQUESTED,
                TaskStatus.CANCEL_REQUESTED,
                TaskStatus.FAILED,
            }
        ),
        TaskStatus.WAITING_FOR_USER: frozenset(
            {
                TaskStatus.RUNNING,
                TaskStatus.PAUSE_REQUESTED,
                TaskStatus.CANCEL_REQUESTED,
                TaskStatus.FAILED,
            }
        ),
        TaskStatus.CANCEL_REQUESTED: frozenset(
            {TaskStatus.CANCELLED, TaskStatus.FAILED}
        ),
        TaskStatus.CANCELLED: frozenset(),
        TaskStatus.COMPLETED: frozenset(),
        TaskStatus.FAILED: frozenset(),
    }
)

TASK_STEP_STATUS_TRANSITIONS: Mapping[TaskStepStatus, frozenset[TaskStepStatus]] = (
    MappingProxyType(
        {
            TaskStepStatus.PENDING: frozenset(
                {
                    TaskStepStatus.RUNNING,
                    TaskStepStatus.SKIPPED,
                    TaskStepStatus.CANCELLED,
                }
            ),
            TaskStepStatus.RUNNING: frozenset(
                {
                    TaskStepStatus.WAITING_FOR_APPROVAL,
                    TaskStepStatus.WAITING_FOR_USER,
                    TaskStepStatus.COMPLETED,
                    TaskStepStatus.FAILED,
                    TaskStepStatus.CANCELLED,
                }
            ),
            TaskStepStatus.WAITING_FOR_APPROVAL: frozenset(
                {
                    TaskStepStatus.RUNNING,
                    TaskStepStatus.FAILED,
                    TaskStepStatus.CANCELLED,
                }
            ),
            TaskStepStatus.WAITING_FOR_USER: frozenset(
                {
                    TaskStepStatus.RUNNING,
                    TaskStepStatus.FAILED,
                    TaskStepStatus.CANCELLED,
                }
            ),
            TaskStepStatus.COMPLETED: frozenset(),
            TaskStepStatus.FAILED: frozenset(
                {
                    TaskStepStatus.PENDING,
                    TaskStepStatus.RUNNING,
                    TaskStepStatus.CANCELLED,
                }
            ),
            TaskStepStatus.SKIPPED: frozenset(),
            TaskStepStatus.CANCELLED: frozenset(),
        }
    )
)


class InvalidTaskTransition(ValueError):
    """Raised when a Task state transition violates the public state machine."""


def _reject_private_reasoning_fields(value: Any, *, label: str) -> None:
    forbidden = {"reasoning", "raw_reasoning", "chain_of_thought", "thought"}
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in forbidden:
                raise ValueError(f"{label} cannot contain private reasoning fields")
            _reject_private_reasoning_fields(nested, label=label)
    elif isinstance(value, list):
        for nested in value:
            _reject_private_reasoning_fields(nested, label=label)


def can_complete_task(
    step_statuses: Iterable[TaskStepStatus | str],
    *,
    result: Mapping[str, Any] | None,
) -> bool:
    """Return whether a Task has safe steps and a concrete terminal result.

    Empty plans are valid for short Tasks, but a Task never completes on status
    alone: it must also expose a non-empty result projection.
    """

    if not result:
        return False
    return all(
        TaskStepStatus(status) in COMPLETABLE_STEP_STATUSES for status in step_statuses
    )


def validate_task_transition(
    current: TaskStatus | str,
    target: TaskStatus | str,
    *,
    step_statuses: Iterable[TaskStepStatus | str] = (),
    result: Mapping[str, Any] | None = None,
) -> None:
    """Validate a Task transition without mutating Task state."""

    current_status = TaskStatus(current)
    target_status = TaskStatus(target)
    if current_status in TERMINAL_TASK_STATUSES:
        raise InvalidTaskTransition(
            f"Terminal Task status {current_status.value!r} cannot transition"
        )
    if target_status not in TASK_STATUS_TRANSITIONS[current_status]:
        raise InvalidTaskTransition(
            f"Task status cannot transition from {current_status.value!r} "
            f"to {target_status.value!r}"
        )
    if target_status is TaskStatus.COMPLETED and not can_complete_task(
        step_statuses, result=result
    ):
        raise InvalidTaskTransition(
            "Task completion requires a non-empty result and no unfinished steps"
        )


def transition_task_status(
    current: TaskStatus | str,
    target: TaskStatus | str,
    *,
    step_statuses: Iterable[TaskStepStatus | str] = (),
    result: Mapping[str, Any] | None = None,
) -> TaskStatus:
    """Pure state transition helper returning the validated target status."""

    validate_task_transition(
        current, target, step_statuses=step_statuses, result=result
    )
    return TaskStatus(target)


def can_transition_task(
    current: TaskStatus | str,
    target: TaskStatus | str,
    *,
    step_statuses: Iterable[TaskStepStatus | str] = (),
    result: Mapping[str, Any] | None = None,
) -> bool:
    try:
        validate_task_transition(
            current, target, step_statuses=step_statuses, result=result
        )
    except (InvalidTaskTransition, ValueError):
        return False
    return True


def transition_task_step_status(
    current: TaskStepStatus | str, target: TaskStepStatus | str
) -> TaskStepStatus:
    """Pure transition helper for one plan step."""

    current_status = TaskStepStatus(current)
    target_status = TaskStepStatus(target)
    if target_status not in TASK_STEP_STATUS_TRANSITIONS[current_status]:
        raise InvalidTaskTransition(
            f"Task step cannot transition from {current_status.value!r} "
            f"to {target_status.value!r}"
        )
    return target_status
