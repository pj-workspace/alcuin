import json
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


app = FastAPI(
    title="Alcuin Records API",
    description="A small domain-neutral service for validating governed OpenAPI tools.",
    version="0.1.0",
    servers=[{"url": "http://127.0.0.1:9411"}],
)


class Record(BaseModel):
    id: str
    title: str
    status: Literal["open", "monitoring", "closed"]


class RecordPatch(BaseModel):
    status: Literal["open", "monitoring", "closed"]


records = {
    "checkout-latency": Record(
        id="checkout-latency",
        title="Checkout latency",
        status="monitoring",
    )
}


@app.get("/search", include_in_schema=False)
def search_fixture(q: str = Query(min_length=1), format: str = "json") -> dict:
    """Small SearXNG-compatible response used only by the local E2E environment."""
    if format != "json":
        raise HTTPException(
            status_code=400, detail="Only JSON search responses are supported"
        )
    return {
        "query": q,
        "results": [
            {
                "title": "Alcuin agent platform",
                "url": "https://example.com/alcuin-agent-platform",
                "content": "Alcuin composes governed tools, knowledge, and portable Agent versions.",
                "engine": "e2e-fixture",
            }
        ],
    }


def provider_tool_names(payload: dict[str, Any]) -> set[str]:
    return {
        str(item.get("function", {}).get("name") or "")
        for item in payload.get("tools") or []
        if isinstance(item, dict)
    }


def provider_tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "delta": {
                    "reasoning_content": "Using the explicitly bound capability.",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": f"call_{name}",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(
                                    arguments, separators=(",", ":")
                                ),
                            },
                        }
                    ],
                }
            }
        ]
    }


def provider_final_text(messages: list[dict[str, Any]]) -> str:
    tool_message = next(
        (message for message in reversed(messages) if message.get("role") == "tool"),
        None,
    )
    if not tool_message:
        return "The Agent is ready and will use only its explicitly bound capabilities."
    assistant_call = next(
        (message for message in reversed(messages) if message.get("tool_calls")),
        {},
    )
    calls = assistant_call.get("tool_calls") or []
    tool_name = str(calls[0].get("function", {}).get("name") or "") if calls else ""
    if tool_name == "web_search":
        return "Web evidence confirms that Alcuin composes governed tools, knowledge, and portable Agent versions."
    if tool_name == "knowledge_search":
        return "The bound release policy requires a rollback owner, verified monitoring, and an approval record."
    return (
        "I reviewed the current record for INC-104. Checkout latency is recovering, "
        "the mitigation is active, and no new payment failures have appeared in the last 20 minutes."
    )


@app.post("/v1/chat/completions", include_in_schema=False)
async def chat_completions_fixture(request: Request) -> StreamingResponse:
    """Deterministic OpenAI-compatible stream that still exercises Alcuin's real tool loop."""
    payload = await request.json()
    messages = payload.get("messages") if isinstance(payload, dict) else None
    messages = messages if isinstance(messages, list) else []
    tools = provider_tool_names(payload)
    last_message = messages[-1] if messages else {}
    prompt = str(last_message.get("content") or "").casefold()

    event: dict[str, Any] | None = None
    if last_message.get("role") != "tool":
        if (
            any(term in prompt for term in ("update", "修改", "更新"))
            and "ops_update_ticket" in tools
        ):
            event = provider_tool_call(
                "ops_update_ticket",
                {"ticket_id": "INC-104", "status": "monitoring"},
            )
        elif (
            any(term in prompt for term in ("web", "public", "联网"))
            and "web_search" in tools
        ):
            event = provider_tool_call(
                "web_search",
                {"query": "Alcuin agent platform", "depth": "quick", "max_results": 3},
            )
        elif (
            any(term in prompt for term in ("policy", "knowledge", "handbook", "知识"))
            and "knowledge_search" in tools
        ):
            event = provider_tool_call(
                "knowledge_search",
                {"query": "release readiness policy", "limit": 3},
            )
        elif "ops_search_incidents" in tools:
            event = provider_tool_call("ops_search_incidents", {"query": prompt[:120]})

    async def stream() -> AsyncIterator[str]:
        if event:
            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
        else:
            text = provider_final_text(messages)
            for index in range(0, len(text), 48):
                chunk = {"choices": [{"delta": {"content": text[index : index + 48]}}]}
                yield f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/health", operation_id="healthCheck", tags=["system"])
def health_check() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/records/{record_id}", operation_id="getRecord", tags=["records"])
def get_record(record_id: str) -> Record:
    record = records.get(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


@app.patch("/records/{record_id}", operation_id="updateRecord", tags=["records"])
def update_record(record_id: str, patch: RecordPatch) -> Record:
    record = records.get(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    updated = record.model_copy(update={"status": patch.status})
    records[record_id] = updated
    return updated
