"""API/provider checks; run with the project's isolated PostgreSQL harness."""

import asyncio
import json
from contextlib import contextmanager
from threading import Event

import httpx
from fastapi.testclient import TestClient

from alcuin_api.config import Settings
from alcuin_api.main import create_app
from support import create_test_store
from test_artifacts import HEADERS, wait_for_terminal_run


@contextmanager
def reference_client():
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        content = '<alcuin-answer><alcuin-artifact title="Report" content-type="text/markdown">Original document A</alcuin-artifact></alcuin-answer>'
        return httpx.Response(200, text="data: " + json.dumps({"choices": [{"delta": {"content": content}, "finish_reason": None}]}) + '\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n', headers={"content-type": "text/event-stream"})

    store = create_test_store()
    app = create_app(Settings(deepseek_api_key="reference-provider-secret", deepseek_base_url="http://provider.test/v1", deepseek_protocol="chat_completions"), store=store, provider_transport=httpx.MockTransport(handler))
    try:
        with TestClient(app) as client:
            yield client, store, app, requests
    finally:
        store.close()


def prepare(client):
    thread = client.post("/v1/threads", headers=HEADERS, json={"agent_id": "agt_starter", "context": {}}).json()
    response = client.post(f"/v1/threads/{thread['id']}/runs", headers=HEADERS, json={"input": "Write a document"})
    assert response.status_code == 202, response.text
    run = wait_for_terminal_run(client, response.json()["id"])
    assert run["status"] == "completed", run
    document = client.get(f"/v1/threads/{thread['id']}/artifacts", headers=HEADERS).json()[0]
    return thread, document


def test_provider_sees_accepted_saved_snapshot_even_if_canvas_changes_before_execution(monkeypatch):
    with reference_client() as (client, store, app, requests):
        thread, document = prepare(client)
        edited = client.patch(f"/v1/artifacts/{document['id']}", headers=HEADERS, json={"expected_version": document["version"], "content": "Unique saved revision B"})
        assert edited.status_code == 200, edited.text
        document = edited.json()
        release = Event()
        execute = app.state.runtime.execute

        async def deferred(request):
            await asyncio.to_thread(release.wait, 10)
            return await execute(request)

        monkeypatch.setattr(app.state.runtime, "execute", deferred)
        try:
            accepted = client.post(f"/v1/threads/{thread['id']}/runs", headers=HEADERS, json={"input": "Revise this document", "artifact_references": [{"artifact_id": document["id"], "expected_version": document["version"]}]})
            assert accepted.status_code == 202, accepted.text
            run_id = accepted.json()["id"]
            changed = client.patch(f"/v1/artifacts/{document['id']}", headers=HEADERS, json={"expected_version": document["version"], "content": "Unique saved revision C"})
            assert changed.status_code == 200, changed.text
        finally:
            release.set()
        run = wait_for_terminal_run(client, run_id)
        assert run["status"] == "completed", run
        message = next(item for item in store.list_messages("ws_demo", thread["id"]) if item["id"] == accepted.json()["input_message_id"])
        reference = next(part for part in message["parts"] if part["type"] == "artifact_reference")
        assert reference["content"] == "Unique saved revision B"
        relevant = [body for body in requests if "Unique saved revision B" in json.dumps(body)]
        assert relevant, requests
        assert all("Unique saved revision C" not in json.dumps(body) for body in relevant)
        assert all("reference-provider-secret" not in json.dumps(body) for body in requests)
        assert store.get_artifact("ws_demo", document["id"])["content"] == "Unique saved revision C"
        context = client.get(f"/v1/runs/{run_id}/context", headers=HEADERS)
        assert context.status_code == 200
        assert "Unique saved revision B" not in context.text
        assert "reference-provider-secret" not in context.text


def test_api_rejects_foreign_stale_oversize_and_embed_references():
    with reference_client() as (client, store, _app, _requests):
        thread, document = prepare(client)
        path = f"/v1/threads/{thread['id']}/runs"

        def send(artifact_id=document["id"], version=document["version"], **fields):
            return client.post(path, headers=HEADERS, json={"input": "Revise", "artifact_references": [{"artifact_id": artifact_id, "expected_version": version}], **fields})

        stale = send(version=document["version"] + 1)
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "artifact_reference_changed"
        missing = send(artifact_id="art_missing")
        assert missing.status_code == 422
        assert missing.json()["detail"]["code"] == "artifact_reference_unavailable"
        other = client.post("/v1/threads", headers=HEADERS, json={"agent_id": "agt_starter", "context": {}}).json()
        foreign = client.post(f"/v1/threads/{other['id']}/runs", headers=HEADERS, json={"input": "Revise", "artifact_references": [{"artifact_id": document["id"], "expected_version": document["version"]}]})
        assert foreign.status_code == 422
        assert foreign.json()["detail"] == missing.json()["detail"]
        assert send(input=" ").status_code == 422
        assert send(requested_tool={"name": "test", "arguments": {}, "extension_manifest_id": "ext.test", "ui_block_id": "test"}).status_code == 422
        oversized = client.patch(f"/v1/artifacts/{document['id']}", headers=HEADERS, json={"expected_version": document["version"], "content": "中" * 33_334})
        assert oversized.status_code == 200
        rejected = send(version=oversized.json()["version"])
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "artifact_reference_too_large"
        # This fixture seeds the published starter, not the optional operations extension.
        starter = client.get("/v1/agents/agt_starter", headers=HEADERS)
        assert starter.status_code == 200
        assert starter.json()["published_version_id"]
        session = client.post("/v1/embed/sessions", headers=HEADERS, json={"agent_id": "agt_starter", "origin": "http://localhost:3000"})
        assert session.status_code == 201, session.text
        bearer = {"Authorization": f"Bearer {session.json()['token']}", "Origin": "http://localhost:3000"}
        embedded_response = client.post("/v1/threads", headers=bearer, json={"agent_id": "agt_starter", "context": {}})
        assert embedded_response.status_code == 201, embedded_response.text
        embedded_thread = embedded_response.json()
        denied = client.post(f"/v1/threads/{embedded_thread['id']}/runs", headers=bearer, json={"input": "Revise", "artifact_references": [{"artifact_id": document["id"], "expected_version": document["version"]}]})
        assert denied.status_code == 403, denied.text
