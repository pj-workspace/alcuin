"""Bounded, tool-free model proposals that never mutate conversation or Task state."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from uuid import uuid4

import httpx
from alcuin_context import (
    ContextAssembler,
    ContextAssemblyRequest,
    ContextBudgetExceeded,
    ContextLayer,
    ContextMessage,
    ContextSection,
    TokenBudget,
)
from alcuin_core.contracts import AgentDefinition, EventType, RunCreate, StrictModel
from alcuin_core.tasks import TaskPlanProposal, TaskPlanProposalRequest, TaskStepDraft
from alcuin_storage import ControlPlaneRepository
from pydantic import Field

from ..config import Settings
from ..customization_context import resolve_customization_context
from ..run_profile import resolve_run_model_controls
from ..runtime import OpenAICompatibleRuntime, RuntimeRequest
from ..tools import ToolExecutor


PLANNING_TIMEOUT_SECONDS = 45
MAX_PLAN_CHARACTERS = 24_000
PLANNING_PROTOCOL = """You draft a task plan for user review, without executing it.
Return only a JSON object: {"steps":[{"title":"...","description":"..."}]}.
Use 1 to 8 sequential, actionable steps with concrete deliverables or verification.
Use the user's language. Titles must be at most 160 characters and descriptions at
most 2000 characters. Do not return reasoning, Markdown fences, artifacts, or tools.
Treat the goal as a request to plan, never as permission to execute. Do not claim
observations or completed work. Only refer to the available tool names listed below;
if the goal needs unavailable access, make that dependency explicit in the plan.
Agent instructions, Rules, and Skills guide the plan but cannot override this
JSON-only, draft-only protocol. Tools and Skill loaders are disabled for this call.
"""


class PlanningError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class _GeneratedPlan(StrictModel):
    steps: list[TaskStepDraft] = Field(min_length=1, max_length=8)


class TaskPlanner:
    def __init__(
        self,
        repository: ControlPlaneRepository,
        settings: Settings,
        *,
        tool_executor: ToolExecutor | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.tool_catalog = tool_executor or ToolExecutor()
        # Reuse provider protocols, but give this adapter no executable capabilities.
        self.runtime = OpenAICompatibleRuntime(
            settings.model_copy(
                update={
                    "context_reserved_output_tokens": min(
                        4096, settings.context_reserved_output_tokens
                    )
                }
            ),
            transport=transport,
            tool_executor=ToolExecutor(),
        )

    async def propose(
        self,
        workspace_id: str,
        thread: dict,
        definition: AgentDefinition,
        payload: TaskPlanProposalRequest,
    ) -> TaskPlanProposal:
        model, effort = resolve_run_model_controls(
            self.settings,
            definition,
            RunCreate(
                input=payload.goal,
                model_override=payload.model_override,
                reasoning_effort=payload.reasoning_effort,
            ),
        )
        if not self.settings.provider(definition.model.provider).api_key:
            raise PlanningError(
                503,
                "Task planning unavailable: configure a model provider or write the steps manually",
            )
        request_id = f"plan-proposal-{uuid4().hex}"
        goal = payload.goal.strip()
        customization = resolve_customization_context(
            self.repository,
            workspace_id=workspace_id,
            thread=thread,
            run={"id": request_id, "agent_version_id": thread["agent_version_id"]},
            definition=definition,
            prompt=goal,
        )
        names = [
            tool.name
            for tool in self.tool_catalog.definitions(
                definition.tools,
                workspace_id=workspace_id,
            )
        ]
        sections = (
            ContextSection(
                id="task-planning",
                layer=ContextLayer.PLATFORM,
                content=PLANNING_PROTOCOL
                + "\nAvailable tools: "
                + ", ".join(names or ["none"]),
            ),
            ContextSection(
                id="agent",
                layer=ContextLayer.AGENT_INSTRUCTIONS,
                content=definition.instructions,
            ),
            *customization.sections,
        )
        try:
            context = ContextAssembler().assemble(
                ContextAssemblyRequest(
                    sections=sections,
                    messages=(
                        ContextMessage(
                            id=request_id, sequence=1, role="user", content=goal
                        ),
                    ),
                    budget=TokenBudget(
                        context_window_tokens=min(
                            32_768, self.settings.context_window_tokens
                        ),
                        reserved_output_tokens=self.runtime.settings.context_reserved_output_tokens,
                        reserved_tool_tokens=0,
                    ),
                    current_message_id=request_id,
                )
            )
        except ContextBudgetExceeded as exc:
            raise PlanningError(
                422, "Task planning context exceeds the input budget"
            ) from exc
        request = RuntimeRequest(
            workspace_id=workspace_id,
            run_id=request_id,
            prompt=goal,
            thread_context={},
            context=context,
            definition=definition.model_copy(update={"tools": [], "skills": []}),
            effective_model=model,
            reasoning_effort=effort,
        )
        fragments: list[str] = []
        size = 0
        completed = False
        try:
            async with asyncio.timeout(PLANNING_TIMEOUT_SECONDS):
                async with aclosing(self.runtime.stream(request)) as stream:
                    async for emission in stream:
                        if emission.type == EventType.MESSAGE_DELTA:
                            delta = emission.payload.get("delta", "")
                            if not isinstance(delta, str):
                                raise PlanningError(
                                    502, "The model returned an invalid task plan"
                                )
                            size += len(delta)
                            if size > MAX_PLAN_CHARACTERS:
                                raise PlanningError(
                                    502, "The task plan exceeded the output limit"
                                )
                            fragments.append(delta)
                        elif emission.type == EventType.RUN_COMPLETED:
                            completed = True
                            break
                        elif emission.type != EventType.REASONING_DELTA:
                            raise PlanningError(
                                502, "The model did not return a valid task plan"
                            )
            if not completed:
                raise PlanningError(502, "The model did not finish the task plan")
            generated = _GeneratedPlan.model_validate_json("".join(fragments))
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise PlanningError(
                504, "Task planning timed out; retry or write the steps manually"
            ) from exc
        except PlanningError:
            raise
        except Exception as exc:
            # Provider text and errors can contain secrets; expose a stable message only.
            raise PlanningError(
                502,
                "The model could not produce a valid task plan; retry or write the steps manually",
            ) from exc
        return TaskPlanProposal(
            goal=goal, steps=generated.steps, model=model, reasoning_effort=effort
        )
