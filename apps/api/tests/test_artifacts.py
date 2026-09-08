from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import httpx
from fastapi.testclient import TestClient

from alcuin_api.config import Settings
from alcuin_api.main import create_app
from alcuin_api.runtime import RuntimeOrchestrator
from alcuin_storage import PostgresStore, RuntimeRepository
from support import create_test_store


HEADERS = {"X-Alcuin-Workspace": "ws_demo"}


@contextmanager
def artifact_client() -> Iterator[tuple[TestClient, PostgresStore]]:
    async def provider_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"<alcuin-answer><alcuin-artifact title=\\"Risk brief\\" content-type=\\"text/markdown\\"># Risk brief\\n\\nStable finding.</alcuin-artifact></alcuin-answer>"}}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    store = create_test_store()
    app = create_app(
        Settings(
            deepseek_api_key="artifact-test-provider-key",
            deepseek_base_url="http://provider.test/v1",
            deepseek_protocol="chat_completions",
        ),
        store=store,
        provider_transport=httpx.MockTransport(provider_handler),
    )
    try:
        with TestClient(app) as client:
            yield client, store
    finally:
        store.close()


def wait_for_terminal_run(client: TestClient, run_id: str) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for _ in range(200):
        response = client.get(f"/v1/runs/{run_id}", headers=HEADERS)
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.01)
    raise AssertionError(f"Run did not finish: {body}")


def test_runtime_artifact_is_canonical_queryable_editable_and_workspace_scoped() -> (
    None
):
    with artifact_client() as (client, store):
        thread_response = client.post(
            "/v1/threads",
            headers=HEADERS,
            json={"agent_id": "agt_starter", "context": {}},
        )
        assert thread_response.status_code == 201
        thread = thread_response.json()
        run_response = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=HEADERS,
            json={"input": "Generate the risk brief", "reasoning_effort": "low"},
        )
        assert run_response.status_code == 202, run_response.text
        run_id = run_response.json()["id"]
        terminal = wait_for_terminal_run(client, run_id)
        assert terminal["status"] == "completed"

        events = terminal["events"]
        assert events[-1]["type"] == "run.completed"
        artifact_event = next(
            event for event in events if event["type"] == "artifact.updated"
        )
        artifact = artifact_event["payload"]["artifact"]
        assert set(artifact) == {
            "id",
            "workspace_id",
            "thread_id",
            "source_run_id",
            "title",
            "kind",
            "content_type",
            "version",
            "content",
            "created_at",
            "updated_at",
        }
        assert artifact["id"].startswith("art_")
        assert artifact["workspace_id"] == "ws_demo"
        assert artifact["thread_id"] == thread["id"]
        assert artifact["source_run_id"] == run_id
        assert artifact["version"] == 1
        assert artifact["content"] == "# Risk brief\n\nStable finding."

        listed = client.get(
            f"/v1/threads/{thread['id']}/artifacts", headers=HEADERS
        )
        fetched = client.get(f"/v1/artifacts/{artifact['id']}", headers=HEADERS)
        assert listed.status_code == 200
        assert listed.json() == [artifact]
        assert fetched.status_code == 200
        assert fetched.json() == artifact
        assert store.get_artifact("ws_demo", artifact["id"]) == artifact

        other_headers = {"X-Alcuin-Workspace": "ws_other"}
        assert (
            client.get(
                f"/v1/threads/{thread['id']}/artifacts", headers=other_headers
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/v1/artifacts/{artifact['id']}", headers=other_headers
            ).status_code
            == 404
        )
        assert (
            client.patch(
                f"/v1/artifacts/{artifact['id']}",
                headers=other_headers,
                json={"expected_version": 1, "content": "Cross-Workspace edit"},
            ).status_code
            == 404
        )

        edited_response = client.patch(
            f"/v1/artifacts/{artifact['id']}",
            headers=HEADERS,
            json={
                "expected_version": 1,
                "title": "Reviewed risk brief",
                "content": "# Reviewed\n\nUser-confirmed finding.",
            },
        )
        assert edited_response.status_code == 200, edited_response.text
        edited = edited_response.json()
        assert edited["version"] == 2
        assert edited["title"] == "Reviewed risk brief"
        assert edited["content"] == "# Reviewed\n\nUser-confirmed finding."

        stale = client.patch(
            f"/v1/artifacts/{artifact['id']}",
            headers=HEADERS,
            json={"expected_version": 1, "content": "Overwrite"},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"] == {
            "code": "artifact_version_conflict",
            "message": "Artifact changed since it was opened; refresh before saving",
            "current_version": 2,
        }
        assert (
            client.get(f"/v1/artifacts/{artifact['id']}", headers=HEADERS).json()
            == edited
        )
        assert client.get(
            f"/v1/threads/{thread['id']}/artifacts?limit=0", headers=HEADERS
        ).status_code == 422


class ArtifactCaptureStore:
    def __init__(self) -> None:
        self.artifact: dict[str, Any] | None = None

    def append_artifact_event(
        self,
        _workspace_id: str,
        _run_id: str,
        artifact: dict[str, Any],
    ) -> dict[str, Any]:
        self.artifact = artifact
        return {"payload": {"artifact": artifact}}


def test_runtime_normalizes_generated_artifact_title_without_relaxing_contract() -> None:
    capture = ArtifactCaptureStore()
    orchestrator = RuntimeOrchestrator(
        cast(RuntimeRepository, capture),
        Settings(),
    )
    orchestrator._persist_artifact_update(
        "ws_demo",
        "run_test",
        {
            "artifact": {
                "title": "  " + "summary " * 80,
                "kind": "tool-result",
                "content": "result",
            }
        },
    )
    assert capture.artifact is not None
    assert len(capture.artifact["title"]) <= 200
    assert capture.artifact["title"] == capture.artifact["title"].strip()
