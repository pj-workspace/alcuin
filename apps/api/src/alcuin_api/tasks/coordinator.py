"""Single-Agent Task coordinator built above durable Runs.

The coordinator is intentionally provider agnostic. It schedules sequential Step Attempts,
waits for canonical Run state, checkpoints at Step boundaries, and never replays a completed
external side effect after restart.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from alcuin_core.contracts import AgentDefinition, ReasoningEffort, RunCreate
from alcuin_storage import (
    ControlPlaneRepository,
    RepositoryConflict,
    TaskRevisionConflict,
    TaskTransitionConflict,
)

from ..config import Settings
from ..run_profile import resolve_run_model_controls
from ..runtime import RuntimeOrchestrator, RuntimeRequest


TaskStepRunner = Callable[
    [str, dict[str, Any], dict[str, Any], str],
    Awaitable[dict[str, Any]],
]


class RuntimeTaskStepRunner:
    """Create and execute one existing Alcuin Run for a durable Task Step Attempt."""

    def __init__(
        self,
        repository: ControlPlaneRepository,
        settings: Settings,
        runtime: RuntimeOrchestrator,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.runtime = runtime

    async def __call__(
        self,
        workspace_id: str,
        task: dict[str, Any],
        step: dict[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]:
        thread = self.repository.get_thread(workspace_id, str(task["thread_id"]))
        if thread is None:
            raise RepositoryConflict("Task Thread is unavailable")
        definition_record = self.repository.get_agent_version(
            workspace_id,
            str(thread["agent_version_id"]),
        )
        if definition_record is None:
            raise RepositoryConflict("Task Agent definition is unavailable")
        definition = AgentDefinition.model_validate(definition_record["definition"])
        instruction = self._step_instruction(task, step)
        effective_model, effort = resolve_run_model_controls(
            self.settings,
            definition,
            RunCreate(
                input=instruction,
                model_override=task.get("model_override"),
                reasoning_effort=task.get("reasoning_effort"),
            ),
        )
        parts = [
            {
                "type": "task_instruction",
                "task_id": str(task["id"]),
                "step_id": str(step["id"]),
                "text": instruction,
            }
        ]
        run = self.repository.create_run_with_messages(
            workspace_id,
            str(task["thread_id"]),
            str(thread["agent_version_id"]),
            "",
            parts,
            max(1, (len(instruction.encode("utf-8")) + 2) // 3),
        )
        self.repository.link_task_run(
            workspace_id,
            str(task["id"]),
            str(step["id"]),
            attempt_id,
            str(run["id"]),
        )
        request = RuntimeRequest(
            workspace_id=workspace_id,
            run_id=str(run["id"]),
            prompt=instruction,
            thread_context=thread["context"],
            definition=definition,
            current_message_id=run.get("input_message_id"),
            attachments=(),
            thinking=effort is not ReasoningEffort.NONE,
            effective_model=effective_model,
            reasoning_effort=effort,
            requested_tool=None,
            include_workspace_preferences=True,
        )
        await self.runtime.execute(request)
        canonical = self.repository.get_run(workspace_id, str(run["id"]))
        if canonical is None:
            raise RepositoryConflict("Task Step Run disappeared")
        return canonical

    @staticmethod
    def _step_instruction(task: dict[str, Any], step: dict[str, Any]) -> str:
        intervention = task.get("pending_intervention")
        intervention_text = ""
        if (
            isinstance(intervention, dict)
            and str(intervention.get("message") or "").strip()
        ):
            intervention_text = (
                "\n\nUser adjustment for the remaining work:\n"
                + str(intervention["message"]).strip()
            )
        description = str(step.get("description") or "").strip()
        return (
            "Execute exactly one step of a user-visible Alcuin Task.\n\n"
            f"Task goal:\n{str(task['goal']).strip()}\n\n"
            f"Current step:\n{str(step['title']).strip()}"
            + (f"\n\nStep details:\n{description}" if description else "")
            + intervention_text
            + "\n\nUse only capabilities bound to this Agent. Ground factual claims in "
            "available evidence, preserve citations, and request approval before any governed "
            "mutation. Return the concrete result of this step; do not claim that the overall "
            "Task is complete."
        )


class TaskCoordinator:
    """Drive one sequential Task at a time per process; storage remains authoritative."""

    def __init__(
        self,
        repository: ControlPlaneRepository,
        step_runner: TaskStepRunner,
    ) -> None:
        self.repository = repository
        self.step_runner = step_runner
        self._lease_owner = f"task-coordinator:{uuid.uuid4().hex}"
        self._active: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._closed = False

    def schedule(self, workspace_id: str, task_id: str) -> None:
        if self._closed:
            return
        key = (workspace_id, task_id)
        running = self._active.get(key)
        if running is not None and not running.done():
            return
        worker = asyncio.create_task(
            self._drive(workspace_id, task_id),
            name=f"alcuin-task:{task_id}",
        )
        self._active[key] = worker
        worker.add_done_callback(
            lambda completed, item=key: self._forget(item, completed)
        )

    def recover(self) -> list[dict[str, Any]]:
        recovered = self.repository.recover_nonterminal_tasks()
        for task in recovered:
            if task.get("status") == "running":
                self.schedule(str(task["workspace_id"]), str(task["id"]))
        return recovered

    async def close(self) -> None:
        self._closed = True
        workers = list(self._active.values())
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._active.clear()

    def _forget(self, key: tuple[str, str], completed: asyncio.Task[None]) -> None:
        if self._active.get(key) is completed:
            self._active.pop(key, None)
        with suppress(asyncio.CancelledError, Exception):
            completed.result()

    async def _drive(self, workspace_id: str, task_id: str) -> None:
        owns_dispatch = False
        try:
            for _ in range(128):
                task = self.repository.get_task(workspace_id, task_id)
                if task is None:
                    return
                status = str(task["status"])
                if status == "pause_requested":
                    self.repository.mark_task_paused(
                        workspace_id,
                        task_id,
                        expected_revision=int(task["revision"]),
                        reason=task.get("control_reason"),
                    )
                    return
                if status == "cancel_requested":
                    self.repository.mark_task_cancelled(
                        workspace_id,
                        task_id,
                        expected_revision=int(task["revision"]),
                        reason=task.get("control_reason"),
                    )
                    return
                if status != "running":
                    return

                step = self._current_step(task)
                if step is None:
                    task = self.repository.complete_task(
                        workspace_id,
                        task_id,
                        expected_revision=int(task["revision"]),
                        result=self._task_result(workspace_id, task),
                    )
                    if task["status"] == "running":
                        owns_dispatch = False
                        continue
                    return
                if not owns_dispatch:
                    owns_dispatch = self.repository.claim_task_dispatch(
                        workspace_id,
                        task_id,
                        self._lease_owner,
                    )
                    if not owns_dispatch:
                        return
                    task = self.repository.get_task(workspace_id, task_id) or task
                    if task["status"] != "running":
                        return
                    step = self._current_step(task)
                    if step is None:
                        continue
                step_status = str(step["status"])
                if step_status == "pending":
                    task = self.repository.start_task_step(
                        workspace_id,
                        task_id,
                        str(step["id"]),
                        expected_revision=int(task["revision"]),
                        input={"goal": task["goal"]},
                    )
                    step = self._current_step(task) or step
                attempt = self._active_attempt(step)
                if attempt is None:
                    raise RepositoryConflict("Task Step has no active Attempt")

                # Queue/steer interventions are consumed at the next safe Run
                # boundary. The instruction projection is retained locally while the
                # durable intervention is atomically marked applied before execution.
                task_for_run = task
                intervention = task.get("pending_intervention")
                if (
                    not attempt.get("run_id")
                    and isinstance(intervention, dict)
                    and intervention.get("kind") in {"queue", "steer"}
                ):
                    self.repository.apply_task_intervention(
                        workspace_id,
                        task_id,
                        str(intervention["id"]),
                        expected_revision=int(task["revision"]),
                    )

                run = await self._run_with_lease(
                    workspace_id,
                    task_id,
                    task_for_run,
                    step,
                    attempt,
                )
                run_status = str(run["status"])
                task = self.repository.get_task(workspace_id, task_id) or task

                # Operator control always wins at the next durable Run boundary.
                # A pending approval has not executed its governed mutation, so it
                # is safely denied before the Attempt is paused or cancelled.
                if task["status"] in {"pause_requested", "cancel_requested"}:
                    if run_status == "completed":
                        task = self.repository.complete_task_step(
                            workspace_id,
                            task_id,
                            str(step["id"]),
                            expected_revision=int(task["revision"]),
                            output=self._run_output(workspace_id, run),
                            evidence=self._run_evidence(workspace_id, run),
                        )
                    elif run_status == "waiting_for_approval":
                        self._abandon_pending_approval(workspace_id, run)
                        task = self.repository.get_task(workspace_id, task_id) or task
                    if task["status"] == "pause_requested":
                        self.repository.mark_task_paused(
                            workspace_id,
                            task_id,
                            expected_revision=int(task["revision"]),
                            reason=task.get("control_reason"),
                        )
                    elif task["status"] == "cancel_requested":
                        self.repository.mark_task_cancelled(
                            workspace_id,
                            task_id,
                            expected_revision=int(task["revision"]),
                            reason=task.get("control_reason"),
                        )
                    return
                if run_status == "waiting_for_approval":
                    approval_id = self._approval_id(workspace_id, str(run["id"]))
                    self.repository.mark_task_waiting_for_approval(
                        workspace_id,
                        task_id,
                        str(step["id"]),
                        approval_id,
                        expected_revision=int(task["revision"]),
                    )
                    return
                if run_status == "completed":
                    task = self.repository.complete_task_step(
                        workspace_id,
                        task_id,
                        str(step["id"]),
                        expected_revision=int(task["revision"]),
                        output=self._run_output(workspace_id, run),
                        evidence=self._run_evidence(workspace_id, run),
                    )
                    owns_dispatch = task.get("current_step_id") is None
                    # A Pause/Cancel request is acknowledged only after the active Run and
                    # its Step checkpoint are durable.
                    if task["status"] == "pause_requested":
                        self.repository.mark_task_paused(
                            workspace_id,
                            task_id,
                            expected_revision=int(task["revision"]),
                            reason=task.get("control_reason"),
                        )
                        return
                    if task["status"] == "cancel_requested":
                        self.repository.mark_task_cancelled(
                            workspace_id,
                            task_id,
                            expected_revision=int(task["revision"]),
                            reason=task.get("control_reason"),
                        )
                        return
                    continue
                if run_status in {"failed", "cancelled"}:
                    self.repository.fail_task_step(
                        workspace_id,
                        task_id,
                        str(step["id"]),
                        expected_revision=int(task["revision"]),
                        error={
                            "code": "step_run_failed",
                            "message": "The Step Run did not complete. Review it before retrying.",
                            "run_id": run["id"],
                        },
                    )
                    return
                raise RepositoryConflict(
                    f"Unsupported Task Step Run status: {run_status}"
                )
            raise RuntimeError("Task exceeded the sequential coordinator safety limit")
        except asyncio.CancelledError:
            raise
        except (TaskRevisionConflict, TaskTransitionConflict):
            # Another coordinator or operator won the durable CAS. The canonical
            # state is authoritative; this worker must never convert that race into
            # a business failure.
            return
        except Exception as exc:
            task = self.repository.get_task(workspace_id, task_id)
            if task and task.get("status") in {
                "running",
                "waiting_for_approval",
                "waiting_for_user",
            }:
                with suppress(Exception):
                    self.repository.fail_task(
                        workspace_id,
                        task_id,
                        expected_revision=int(task["revision"]),
                        error={
                            "code": "task_coordinator_error",
                            "message": str(exc)[:500],
                        },
                    )

    async def _run_with_lease(
        self,
        workspace_id: str,
        task_id: str,
        task: dict[str, Any],
        step: dict[str, Any],
        attempt: dict[str, Any],
    ) -> dict[str, Any]:
        heartbeat = asyncio.create_task(
            self._renew_lease(workspace_id, task_id),
            name=f"alcuin-task-lease:{task_id}",
        )
        try:
            return await self._run_or_reconcile(workspace_id, task, step, attempt)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _renew_lease(self, workspace_id: str, task_id: str) -> None:
        while True:
            await asyncio.sleep(10)
            if not self.repository.renew_task_dispatch(
                workspace_id,
                task_id,
                self._lease_owner,
            ):
                return

    def _abandon_pending_approval(
        self,
        workspace_id: str,
        run: dict[str, Any],
    ) -> None:
        run_id = str(run["id"])
        with suppress(RepositoryConflict):
            approval_id = self._approval_id(workspace_id, run_id)
            self.repository.decide_approval(
                workspace_id,
                approval_id,
                "denied",
                "Task was paused or cancelled before approval execution.",
            )
        self.repository.set_run_status(workspace_id, run_id, "cancelled")

    async def _run_or_reconcile(
        self,
        workspace_id: str,
        task: dict[str, Any],
        step: dict[str, Any],
        attempt: dict[str, Any],
    ) -> dict[str, Any]:
        run_id = attempt.get("run_id")
        if run_id:
            run = self.repository.get_run(workspace_id, str(run_id))
            if run is None:
                raise RepositoryConflict("Task Step Run is unavailable")
            return run
        return await self.step_runner(
            workspace_id,
            task,
            step,
            str(attempt["id"]),
        )

    def _approval_id(self, workspace_id: str, run_id: str) -> str:
        for event in reversed(self.repository.list_events(workspace_id, run_id)):
            if event["type"] == "approval.required" and event["payload"].get(
                "approval_id"
            ):
                return str(event["payload"]["approval_id"])
        raise RepositoryConflict(
            "Run waits for approval without a durable approval request"
        )

    def _run_output(
        self,
        workspace_id: str,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        message_id = run.get("output_message_id")
        message = (
            self.repository.get_message(workspace_id, str(message_id))
            if message_id
            else None
        )
        text = ""
        if message:
            text = "".join(
                str(part.get("text") or "")
                for part in message.get("parts") or []
                if part.get("type") == "text"
            )
        return {
            "run_id": run["id"],
            "message_id": message_id,
            "summary": text[:4_000],
        }

    def _run_evidence(
        self,
        workspace_id: str,
        run: dict[str, Any],
    ) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        for event in self.repository.list_events(workspace_id, str(run["id"])):
            payload = event.get("payload") or {}
            if event["type"] == "citation.created":
                evidence.append(
                    {
                        "kind": "citation",
                        "label": str(
                            payload.get("label") or payload.get("source") or "Source"
                        ),
                        "summary": str(payload.get("snippet") or "")[:1_000],
                        "resource_id": event["id"],
                        "source_uri": payload.get("locator"),
                    }
                )
            elif event["type"] == "artifact.updated":
                artifact = payload.get("artifact") or {}
                evidence.append(
                    {
                        "kind": "artifact",
                        "label": str(artifact.get("title") or "Artifact"),
                        "summary": str(artifact.get("kind") or "document"),
                        "resource_id": artifact.get("id"),
                    }
                )
            elif (
                event["type"] == "tool.completed"
                and payload.get("status") == "succeeded"
            ):
                evidence.append(
                    {
                        "kind": "tool_result",
                        "label": str(payload.get("tool") or "Tool result"),
                        "summary": str(payload.get("result_summary") or "")[:1_000],
                        "resource_id": payload.get("call_id"),
                    }
                )
        return evidence

    @staticmethod
    def _current_step(task: dict[str, Any]) -> dict[str, Any] | None:
        plan = task.get("plan")
        steps = plan.get("steps") if isinstance(plan, dict) else []
        current_id = task.get("current_step_id")
        return next(
            (step for step in steps or [] if step.get("id") == current_id),
            None,
        )

    @staticmethod
    def _active_attempt(step: dict[str, Any]) -> dict[str, Any] | None:
        attempts = step.get("attempts")
        if not isinstance(attempts, list):
            return None
        return next(
            (
                attempt
                for attempt in reversed(attempts)
                if isinstance(attempt, dict)
                and attempt.get("status")
                in {
                    "queued",
                    "running",
                    "waiting_for_approval",
                    "waiting_for_user",
                }
            ),
            None,
        )

    def _task_result(
        self,
        workspace_id: str,
        task: dict[str, Any],
    ) -> dict[str, Any]:
        plan = task.get("plan") or {}
        steps = plan.get("steps") or []
        completed_steps: list[dict[str, Any]] = []
        for step in steps:
            attempts = step.get("attempts") if isinstance(step, dict) else None
            latest = attempts[-1] if isinstance(attempts, list) and attempts else None
            run_id = latest.get("run_id") if isinstance(latest, dict) else None
            output: dict[str, Any] = {}
            if run_id:
                run = self.repository.get_run(workspace_id, str(run_id))
                if run is not None:
                    output = self._run_output(workspace_id, run)
            completed_steps.append(
                {
                    "step_id": str(step.get("id") or ""),
                    "title": str(step.get("title") or "Step"),
                    "run_id": run_id,
                    "summary": str(output.get("summary") or "")[:4_000],
                }
            )
        concrete_summaries = [
            item["summary"] for item in completed_steps if item["summary"].strip()
        ]
        return {
            "title": "Task result",
            "summary": (
                concrete_summaries[-1]
                if concrete_summaries
                else f"Completed {len(steps)} planned steps."
            ),
            "completed_step_ids": [
                str(step["id"])
                for step in steps
                if step.get("status") in {"completed", "skipped"}
            ],
            "steps": completed_steps,
        }
