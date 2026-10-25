"""Single-worker execution ownership for durable, user-requested Run stops.

Cancellation intent lives in storage. A local task handle only interrupts waits;
it is not the source of truth and never grants permission to replay an action.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from alcuin_storage import RuntimeRepository


class RunStopRequested(asyncio.CancelledError):
    """A cooperative stop, not a provider or tool failure."""


@dataclass
class _Execution:
    task: asyncio.Task[None] | None = None
    mutating: bool = False
    mutation_started: bool = False


class RunControls:
    def __init__(self, store: RuntimeRepository, finish: Callable[[str, str, bool], None]) -> None:
        self.store = store
        self.finish = finish
        self.executions: dict[tuple[str, str], _Execution] = {}

    def checkpoint(self, workspace_id: str, run_id: str) -> None:
        run = self.store.get_run(workspace_id, run_id)
        if not run or run["status"] in {"cancel_requested", "cancelled"}:
            raise RunStopRequested()

    def before_tool(self, workspace_id: str, run_id: str, *, mutating: bool) -> None:
        self.checkpoint(workspace_id, run_id)
        execution = self.executions.get((workspace_id, run_id))
        if execution:
            execution.mutating = mutating
            execution.mutation_started |= mutating

    def after_tool(self, workspace_id: str, run_id: str) -> None:
        # Called only after its receipt and evidence have been persisted. Keep
        # in-flight writes shielded from user cancellation until that boundary.
        execution = self.executions.get((workspace_id, run_id))
        if execution:
            execution.mutating = False
        self.checkpoint(workspace_id, run_id)

    def schedule(self, workspace_id: str, run_id: str, work: Callable[[], Awaitable[None]]) -> asyncio.Task[None]:
        key = workspace_id, run_id
        previous = self.executions.get(key)
        if previous and previous.task and not previous.task.done():
            raise RuntimeError("Run already has an active worker")
        execution = _Execution()

        async def owned() -> None:
            try:
                self.checkpoint(workspace_id, run_id)
                await work()
            except asyncio.CancelledError:
                run = self.store.get_run(workspace_id, run_id)
                if not run or run["status"] not in {"cancel_requested", "cancelled"}:
                    raise  # Shutdown/disconnection is not a user stop.
            except Exception:
                run = self.store.get_run(workspace_id, run_id)
                if not run or run["status"] not in {"cancel_requested", "cancelled"}:
                    raise
            finally:
                self.finish(workspace_id, run_id, execution.mutation_started)

        task = asyncio.create_task(owned())
        execution.task = task
        self.executions[key] = execution

        def settled(_: asyncio.Task[None]) -> None:
            # A task cancelled before its first instruction never enters finally.
            try:
                self.finish(workspace_id, run_id, execution.mutation_started)
            finally:
                if self.executions.get(key) is execution:
                    self.executions.pop(key, None)

        task.add_done_callback(settled)
        return task

    def cancel(self, workspace_id: str, run_id: str) -> dict[str, Any] | None:
        execution = self.executions.get((workspace_id, run_id))
        in_flight = bool(execution and execution.mutating)
        run = self.store.request_run_cancel(workspace_id, run_id, tool_in_flight=in_flight)
        if not run or run["status"] != "cancel_requested":
            return run
        if execution and execution.task and not execution.task.done():
            if not in_flight:
                execution.task.cancel()
        else:
            self.finish(workspace_id, run_id, False)
        return self.store.get_run(workspace_id, run_id)
