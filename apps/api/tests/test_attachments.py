from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
from fastapi.testclient import TestClient

from alcuin_api.config import Settings
from alcuin_api.main import create_app
from alcuin_storage import PostgresStore
from support import create_test_store


HEADERS = {"X-Alcuin-Workspace": "ws_demo"}


@contextmanager
def attachment_client(
    **setting_overrides: Any,
) -> Iterator[tuple[TestClient, PostgresStore, list[dict[str, Any]]]]:
    payloads: list[dict[str, Any]] = []
    naming_payloads: list[dict[str, Any]] = []

    async def provider_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        messages = payload.get("messages") or []
        is_naming = bool(messages) and "Generate a short descriptive conversation title" in str(
            messages[0].get("content") or ""
        )
        (naming_payloads if is_naming else payloads).append(payload)
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"Attachment understood."}}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    store = create_test_store()
    settings = Settings(
        deepseek_api_key="attachment-test-provider-key",
        deepseek_base_url="http://provider.test/v1",
        deepseek_protocol="chat_completions",
        **setting_overrides,
    )
    app = create_app(
        settings,
        store=store,
        provider_transport=httpx.MockTransport(provider_handler),
    )
    app.state.attachment_test_naming_payloads = naming_payloads
    try:
        with TestClient(app) as client:
            yield client, store, payloads
    finally:
        store.close()


def create_thread(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/v1/threads",
        headers=HEADERS,
        json={"agent_id": "agt_starter", "context": {}},
    )
    assert response.status_code == 201, response.text
    return response.json()


def assert_naming_did_not_receive_attachments(client: TestClient, *forbidden: str) -> None:
    naming_payloads = client.app.state.attachment_test_naming_payloads
    assert len(naming_payloads) == 1
    payload = naming_payloads[0]
    assert "tools" not in payload
    serialized = json.dumps(payload, ensure_ascii=False)
    for text in ("<untrusted_document_attachment", "data:image/", "attachment-test-provider-key", *forbidden):
        assert text not in serialized


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


def upload(
    client: TestClient,
    *,
    upload_id: str,
    name: str,
    content: bytes,
    media_type: str,
) -> httpx.Response:
    return client.post(
        "/v1/attachments",
        headers=HEADERS,
        data={"upload_id": upload_id},
        files={"file": (name, content, media_type)},
    )


def test_document_attachment_public_contract_content_auth_and_later_turn_context() -> (
    None
):
    document = b"# Risk note\n\nTungsten supply pressure is elevated."
    with attachment_client() as (client, store, provider_payloads):
        uploaded = upload(
            client,
            upload_id="document-public-contract",
            name="risk-note.md",
            content=document,
            media_type="text/markdown",
        )
        assert uploaded.status_code == 201, uploaded.text
        resource = uploaded.json()
        assert set(resource) == {
            "id",
            "workspace_id",
            "kind",
            "name",
            "media_type",
            "size_bytes",
            "status",
            "document",
            "created_at",
            "expires_at",
        }
        assert resource["document"] == {
            "format": "markdown",
            "page_count": None,
            "extracted_chars": len(document.decode("utf-8")),
        }
        assert all(
            private not in resource for private in ("upload_id", "sha256", "bound")
        )

        repeated = upload(
            client,
            upload_id="document-public-contract",
            name="risk-note.md",
            content=document,
            media_type="text/markdown",
        )
        assert repeated.status_code == 201
        assert repeated.json()["id"] == resource["id"]
        conflict = upload(
            client,
            upload_id="document-public-contract",
            name="risk-note.md",
            content=b"different text",
            media_type="text/markdown",
        )
        assert conflict.status_code == 409

        hidden = client.get(
            f"/v1/attachments/{resource['id']}",
            headers={"X-Alcuin-Workspace": "ws_other"},
        )
        hidden_content = client.get(
            f"/v1/attachments/{resource['id']}/content",
            headers={"X-Alcuin-Workspace": "ws_other"},
        )
        assert hidden.status_code == 404
        assert hidden_content.status_code == 404

        content = client.get(
            f"/v1/attachments/{resource['id']}/content",
            headers=HEADERS,
        )
        assert content.status_code == 200
        assert content.content == document
        assert content.headers["x-content-type-options"] == "nosniff"
        assert content.headers["cache-control"] == "private, no-store"
        assert content.headers["content-disposition"].startswith("attachment;")

        thread = create_thread(client)
        first = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=HEADERS,
            json={
                "input": "Use the attached risk note.",
                "attachment_ids": [resource["id"]],
            },
        )
        assert first.status_code == 202, first.text
        assert wait_for_terminal_run(client, first.json()["id"])["status"] == "completed"

        second = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=HEADERS,
            json={"input": "What pressure did the earlier note describe?"},
        )
        assert second.status_code == 202, second.text
        assert wait_for_terminal_run(client, second.json()["id"])["status"] == "completed"
        assert len(provider_payloads) == 2
        assert_naming_did_not_receive_attachments(
            client, "Tungsten supply pressure is elevated.", "risk-note.md",
        )
        for payload in provider_payloads:
            serialized_payload = json.dumps(payload, ensure_ascii=False)
            assert "<untrusted_document_attachment" in serialized_payload
            assert "Tungsten supply pressure is elevated." in serialized_payload
            assert "data:" not in serialized_payload.casefold()
            assert "attachment-test-provider-key" not in serialized_payload

        messages = store.list_messages("ws_demo", thread["id"])
        events = [
            *store.list_events("ws_demo", first.json()["id"]),
            *store.list_events("ws_demo", second.json()["id"]),
        ]
        assemblies = [
            store.get_context_assembly("ws_demo", first.json()["id"]),
            store.get_context_assembly("ws_demo", second.json()["id"]),
        ]
        persisted = json.dumps(
            {"messages": messages, "events": events, "assemblies": assemblies},
            ensure_ascii=False,
            default=str,
        ).casefold()
        assert "data:" not in persisted
        assert "storage_key" not in persisted
        assert "attachment-test-provider-key" not in persisted

        bound_delete = client.delete(
            f"/v1/attachments/{resource['id']}", headers=HEADERS
        )
        assert bound_delete.status_code == 409


def test_image_is_inline_for_content_but_data_url_exists_only_in_provider_payload() -> (
    None
):
    image = b"\x89PNG\r\n\x1a\n"
    with attachment_client() as (client, store, provider_payloads):
        uploaded = upload(
            client,
            upload_id="image-transient-provider",
            name="sample.png",
            content=image,
            media_type="image/png",
        )
        assert uploaded.status_code == 201, uploaded.text
        resource = uploaded.json()
        assert resource["kind"] == "image"
        assert resource["document"] is None
        content = client.get(
            f"/v1/attachments/{resource['id']}/content", headers=HEADERS
        )
        assert content.status_code == 200
        assert content.headers["content-disposition"].startswith("inline;")
        assert content.headers["x-content-type-options"] == "nosniff"

        thread = create_thread(client)
        vision = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=HEADERS,
            json={
                "input": "Inspect this image.",
                "attachment_ids": [resource["id"]],
            },
        )
        assert vision.status_code == 202, vision.text
        assert wait_for_terminal_run(client, vision.json()["id"])["status"] == "completed"
        first_payload = json.dumps(provider_payloads[0], ensure_ascii=False)
        assert "data:image/png;base64," in first_payload
        assert_naming_did_not_receive_attachments(client, "sample.png")

        text_only = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=HEADERS,
            json={
                "input": "Continue without inspecting earlier images.",
                "model_override": "deepseek-v4-flash",
            },
        )
        assert text_only.status_code == 202, text_only.text
        assert wait_for_terminal_run(client, text_only.json()["id"])["status"] == "completed"
        second_payload = json.dumps(provider_payloads[1], ensure_ascii=False)
        assert "data:image/" not in second_payload

        persisted = json.dumps(
            {
                "messages": store.list_messages("ws_demo", thread["id"]),
                "vision_events": store.list_events("ws_demo", vision.json()["id"]),
                "text_events": store.list_events("ws_demo", text_only.json()["id"]),
                "vision_context": store.get_context_assembly(
                    "ws_demo", vision.json()["id"]
                ),
                "text_context": store.get_context_assembly(
                    "ws_demo", text_only.json()["id"]
                ),
            },
            ensure_ascii=False,
            default=str,
        ).casefold()
        assert "data:image/" not in persisted
        text_started = next(
            event
            for event in store.list_events("ws_demo", text_only.json()["id"])
            if event["type"] == "run.started"
        )
        assert text_started["payload"]["model"] == "deepseek-v4-flash"
        assert text_started["payload"]["input_modalities"] == ["text"]


def test_attachment_rejects_forbidden_mime_and_enforces_file_and_run_limits() -> (
    None
):
    limit = 64 * 1024
    with attachment_client(
        attachment_image_max_bytes=limit,
        attachment_document_max_bytes=limit,
        attachment_run_total_max_bytes=limit,
    ) as (client, _store, _provider_payloads):
        gif = upload(
            client,
            upload_id="forbidden-gif",
            name="animation.gif",
            content=b"GIF89a",
            media_type="image/gif",
        )
        svg = upload(
            client,
            upload_id="forbidden-svg",
            name="vector.svg",
            content=b"<svg></svg>",
            media_type="image/svg+xml",
        )
        mismatch = upload(
            client,
            upload_id="mismatched-image",
            name="sample.png",
            content=b"\x89PNG\r\n\x1a\n",
            media_type="image/jpeg",
        )
        oversized = upload(
            client,
            upload_id="oversized-image",
            name="large.png",
            content=b"\x89PNG\r\n\x1a\n" + b"0" * (limit + 1),
            media_type="image/png",
        )
        assert gif.status_code == 415
        assert svg.status_code == 415
        assert mismatch.status_code == 415
        assert oversized.status_code == 413

        images = []
        for index in range(2):
            response = upload(
                client,
                upload_id=f"run-total-{index}",
                name=f"total-{index}.png",
                content=b"\x89PNG\r\n\x1a\n" + b"0" * 40_000,
                media_type="image/png",
            )
            assert response.status_code == 201, response.text
            images.append(response.json()["id"])
        thread = create_thread(client)
        too_large = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=HEADERS,
            json={"input": "Inspect both", "attachment_ids": images},
        )
        assert too_large.status_code == 422
        assert too_large.json()["detail"]["code"] == "attachment_total_too_large"
        assert client.get("/v1/runs", headers=HEADERS).json() == []


def test_staged_attachment_can_be_deleted_once() -> None:
    with attachment_client() as (client, _store, _provider_payloads):
        uploaded = upload(
            client,
            upload_id="delete-staged",
            name="notes.txt",
            content=b"temporary notes",
            media_type="text/plain",
        )
        assert uploaded.status_code == 201
        attachment_id = uploaded.json()["id"]
        deleted = client.delete(f"/v1/attachments/{attachment_id}", headers=HEADERS)
        assert deleted.status_code == 204
        assert (
            client.get(f"/v1/attachments/{attachment_id}", headers=HEADERS).status_code
            == 404
        )
