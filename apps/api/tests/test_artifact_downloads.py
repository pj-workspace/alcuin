from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient

from alcuin_api.main import create_app
from alcuin_api.config import Settings
from support import create_test_store

HEADERS = {"X-Alcuin-Workspace": "ws_demo"}


def test_multi_output_artifacts_download_real_docx_and_isolated_html():
    store = create_test_store()
    try:
        thread = store.create_thread("ws_demo", "agt_starter", "Artifacts", {})
        agent = store.list_agents("ws_demo")[0]
        run = store.create_run(
            "ws_demo", thread["id"], agent["current_version_id"], "Generate"
        )
        store.set_run_status("ws_demo", run["id"], "running")
        outputs = [
            {
                "generation_key": "1",
                "title": "说明文档",
                "content_type": "text/markdown",
                "content": "# 说明文档\n\n已检索证据。[[cite:s1]]\n\n|项目|状态|\n|---|---|\n|接口|可用|",
            },
            {
                "generation_key": "2",
                "title": "交互页面",
                "content_type": "text/html",
                "content": '<!doctype html><button onclick="this.textContent=1">测试</button>',
            },
        ]
        first = store.append_artifact_event("ws_demo", run["id"], outputs[0])
        second = store.append_artifact_event("ws_demo", run["id"], outputs[1])
        replay = store.append_artifact_event("ws_demo", run["id"], outputs[0])
        assert replay["id"] == first["id"], (
            "Replay must not borrow another artifact's event"
        )
        assert replay["sequence"] != second["sequence"]
        document = first["payload"]["artifact"]
        html = second["payload"]["artifact"]
        assert document["id"] != html["id"]
        assert len(store.list_thread_artifacts("ws_demo", thread["id"])) == 2
        store.append_event(
            "ws_demo",
            run["id"],
            "citation.created",
            {
                "citation_id": "s1",
                "label": "接口说明",
                "source": "Example",
                "locator": "https://example.test/api",
                "snippet": "接口可用。",
            },
        )
        with TestClient(create_app(Settings(), store=store)) as client:
            # Startup correctly fails orphaned Runs. Simulate an active generation
            # after startup rather than treating an orphan as still running.
            store.set_run_status("ws_demo", run["id"], "running")
            url = f"/v1/artifacts/{document['id']}/download"
            assert client.get(url, headers=HEADERS).status_code == 409
            store.set_run_status("ws_demo", run["id"], "completed")
            response = client.get(url, headers=HEADERS)
            assert response.status_code == 200, response.text[:200]
            assert response.content.startswith(b"PK")
            assert "wordprocessingml.document" in response.headers["content-type"]
            assert "attachment;" in response.headers["content-disposition"]
            assert "filename*=UTF-8''" in response.headers["content-disposition"]
            with ZipFile(BytesIO(response.content)) as archive:
                xml = archive.read("word/document.xml").decode()
                assert "说明文档" in xml and "接口" in xml
                assert "[[cite:" not in xml
                assert "接口说明" in xml
                assert "w:tblHeader" in xml
            assert client.get(
                url, headers={"X-Alcuin-Workspace": "ws_other"}
            ).status_code in {403, 404}
            assert client.get(url + "?format=html", headers=HEADERS).status_code == 422
            markdown = client.get(url + "?format=md", headers=HEADERS)
            assert markdown.status_code == 200
            assert "[[cite:" not in markdown.text
            assert "接口说明" in markdown.text
            html_response = client.get(
                f"/v1/artifacts/{html['id']}/download?format=html", headers=HEADERS
            )
            assert html_response.status_code == 200
            assert html_response.text == outputs[1]["content"]
            assert html_response.headers["x-content-type-options"] == "nosniff"
            assert html_response.headers["content-security-policy"].startswith(
                "sandbox;"
            )
            assert (
                client.get(
                    f"/v1/artifacts/{html['id']}/download?format=docx", headers=HEADERS
                ).status_code
                == 422
            )
    finally:
        store.close()
