"""Shared per-execution model controls for chat Runs and durable Task Steps."""

from alcuin_core.contracts import AgentDefinition, ReasoningEffort, RunCreate
from fastapi import HTTPException

from .config import Settings


def resolve_run_model_controls(
    settings: Settings,
    definition: AgentDefinition,
    payload: RunCreate,
    attachments: tuple[dict, ...] = (),
) -> tuple[str, ReasoningEffort]:
    """Resolve controls against the owning Agent provider's explicit catalog."""
    provider = settings.provider(definition.model.provider)
    effective_model = (
        payload.model_override or definition.model.model or provider.default_model
    )
    catalog_model = provider.model(effective_model)
    if payload.model_override is not None and catalog_model is None:
        raise HTTPException(
            status_code=422,
            detail="model_override is unavailable for this Agent provider",
        )

    effort = payload.reasoning_effort
    if effort is None:
        effort = (
            ReasoningEffort.NONE if payload.thinking is False else ReasoningEffort.HIGH
        )
    if payload.reasoning_effort is not None and catalog_model is None:
        raise HTTPException(
            status_code=422,
            detail="reasoning_effort requires a catalog model",
        )
    if (
        catalog_model is not None
        and effort.value not in catalog_model.reasoning_efforts
    ):
        raise HTTPException(
            status_code=422,
            detail="reasoning_effort is unavailable for this model",
        )
    if any(attachment.get("kind") == "image" for attachment in attachments) and (
        catalog_model is None or "image" not in catalog_model.input_modalities
    ):
        raise HTTPException(
            status_code=422,
            detail="selected model does not accept image attachments",
        )
    return effective_model, effort
