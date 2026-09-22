"""Human decisions are durable records, not evidence of successful execution."""
import json
import time

import pytest

from test_api import make_client

HEADERS = {"X-Alcuin-Workspace": "ws_demo"}


def waiting_approval(client):
    thread = client.post("/v1/threads", headers=HEADERS, json={"agent_id": "agt_operations"}).json()
    run = client.post(f"/v1/threads/{thread['id']}/runs", headers=HEADERS, json={"input": "Update the record"}).json()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        snapshot = client.get(f"/v1/runs/{run['id']}", headers=HEADERS).json()
        required = [event for event in snapshot["events"] if event["type"] == "approval.required"]
        if required:
            return thread, run, required[0]["payload"]["approval_id"]
        time.sleep(0.01)
    raise AssertionError("Run did not reach its approval boundary")


@pytest.mark.parametrize("decision", ["approved", "denied"])
def test_decision_note_receipt_and_history_are_durable_and_scoped(decision):
    with make_client() as client:
        thread, run, approval_id = waiting_approval(client)
        url = f"/v1/runs/{run['id']}/approvals/{approval_id}"
        body = {"decision": decision, "note": "已核对记录和操作范围"}
        assert client.post(url, headers={"X-Alcuin-Workspace": "ws_other"}, json=body).status_code == 404
        response = client.post(url, headers=HEADERS, json=body)
        assert response.status_code == 200
        snapshot = client.get(f"/v1/runs/{run['id']}", headers=HEADERS).json()
        records = [e for e in snapshot["events"] if e["type"] == "approval.decided"]
        assert len(records) == 1
        assert records[0]["payload"]["note"] == body["note"]
        assert records[0]["payload"]["decision"] == decision
        assert records[0]["payload"]["approval_id"] == approval_id
        tool_result = next(e for e in snapshot["events"] if e["type"] == "tool.completed")
        assert records[0]["sequence"] < tool_result["sequence"]
        assert records[0]["payload"]["call_id"] == tool_result["payload"]["call_id"]
        assert client.post(url, headers=HEADERS, json=body).status_code == 409
        assert client.get(f"/v1/runs/{run['id']}", headers=HEADERS).json()["events"] == snapshot["events"]
        history = client.get(f"/v1/threads/{thread['id']}", headers=HEADERS).json()["approval_events"]
        assert [e["type"] for e in history] == ["approval.required", "approval.decided", "tool.completed"]
        assert "result" not in history[-1]["payload"]
        store = client.app.state.store
        assert store.list_thread_approval_events("ws_other", thread["id"], [run["id"]]) == []
        assert store.list_thread_approval_events("ws_demo", "another-thread", [run["id"]]) == []


def test_note_redacts_known_secrets_and_rejects_credential_shaped_text():
    with make_client() as client:
        _, run, approval_id = waiting_approval(client)
        url = f"/v1/runs/{run['id']}/approvals/{approval_id}"
        response = client.post(url, headers=HEADERS, json={"decision": "denied", "note": "sk-" + "a" * 30})
        assert response.status_code == 422
        assert "sk-" not in response.text
        response = client.post(url, headers=HEADERS, json={"decision": "denied", "note": "Do not use test-provider-key"})
        assert response.status_code == 200
        snapshot = client.get(f"/v1/runs/{run['id']}", headers=HEADERS).json()
        assert "test-provider-key" not in json.dumps(snapshot)
        assert response.json()["note"] == "Do not use [REDACTED]"


def test_failed_run_cannot_execute_an_old_pending_approval():
    with make_client() as client:
        _, run, approval_id = waiting_approval(client)
        client.app.state.store.set_run_status("ws_demo", run["id"], "failed")
        response = client.post(f"/v1/runs/{run['id']}/approvals/{approval_id}", headers=HEADERS, json={"decision": "approved"})
        assert response.status_code == 409
        assert client.app.state.store.get_approval("ws_demo", approval_id)["status"] == "pending"


def test_record_and_decision_rollback_together_and_restart_does_not_replay(monkeypatch):
    with make_client() as client:
        _, run, approval_id = waiting_approval(client)
        store = client.app.state.store
        original = store._append_event_locked

        def fail_after_append(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected transaction failure")

        with monkeypatch.context() as patch:
            patch.setattr(store, "_append_event_locked", fail_after_append)
            with pytest.raises(RuntimeError, match="injected"):
                store.decide_approval("ws_demo", approval_id, "approved", "Reviewed")
        assert store.get_approval("ws_demo", approval_id)["status"] == "pending"
        assert not any(e["type"] == "approval.decided" for e in store.list_events("ws_demo", run["id"]))
        store.decide_approval("ws_demo", approval_id, "approved", "Reviewed")
        assert store.get_run("ws_demo", run["id"])["status"] == "running"
        store.recover_interrupted_runs()
        assert store.get_run("ws_demo", run["id"])["status"] == "failed"
        assert store.decide_approval("ws_demo", approval_id, "approved", "Again") is None
