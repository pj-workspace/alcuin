from alcuin_core.contracts import (
    AgentDefinition,
    EventType,
    ExtensionManifest,
    ReasoningEffort,
    RunCreate,
)
from alcuin_core.tools import ToolContext, ToolResult


def test_core_contracts_are_importable_without_the_api_application() -> None:
    definition = AgentDefinition.model_validate(
        {
            "identity": {"name": "Portable Agent"},
            "instructions": "Use only the context and capabilities explicitly provided.",
        }
    )
    manifest = ExtensionManifest.model_validate(
        {
            "id": "example.tools",
            "name": "Example Tools",
            "version": "0.1.0",
            "entrypoints": [{"type": "builtin", "adapter": "example"}],
        }
    )

    assert definition.identity.name == "Portable Agent"
    assert manifest.id == "example.tools"
    assert EventType.RUN_STARTED == "run.started"
    assert EventType.CONTEXT_ASSEMBLED == "context.assembled"
    assert EventType.CONTEXT_COMPACTION_COMPLETED == "context.compaction.completed"


def test_tool_envelopes_are_framework_neutral() -> None:
    context = ToolContext("ws_test", "run_test", {"record": {"id": "REC-1"}})
    result = ToolResult(data={"record_id": "REC-1"}, summary="Record found")

    assert context.thread_context["record"]["id"] == "REC-1"
    assert result.model_content() == '{"record_id":"REC-1"}'


def test_tool_result_can_separate_model_content_from_public_event_data() -> None:
    result = ToolResult(
        data={"content": "provider-only text"},
        summary="Loaded content",
        public_data={"digest": "a" * 64, "size": 18},
    )

    assert "provider-only text" in result.model_content()
    assert result.event_data() == {"digest": "a" * 64, "size": 18}


def test_run_controls_remain_optional_and_provider_neutral() -> None:
    inherited = RunCreate(input="Use the Agent defaults")
    controlled = RunCreate(
        input="Use this run profile",
        model_override="deepseek-v4-pro",
        reasoning_effort="medium",
        thinking=False,
    )

    assert inherited.model_override is None
    assert inherited.reasoning_effort is None
    assert inherited.thinking is None
    assert controlled.reasoning_effort is ReasoningEffort.MEDIUM
