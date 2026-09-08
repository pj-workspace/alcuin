from __future__ import annotations

import io
import json
import time
import zipfile

from fastapi.testclient import TestClient

from alcuin_api.config import Settings
from alcuin_api.main import create_app
from support import create_test_store


HEADERS = {"X-Alcuin-Workspace": "ws_demo"}


def _client() -> TestClient:
    return TestClient(
        create_app(
            Settings(
                searxng_url="",
                qdrant_url="",
                dashscope_api_key="",
                deepseek_api_key="",
                openai_api_key="",
            ),
            store=create_test_store(),
        )
    )


def _plugin_archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            ".cursor-plugin/plugin.json",
            '{"name":"portable-review","version":"1.0.0",'
            '"skills":"skills","rules":"rules"}',
        )
        archive.writestr(
            "skills/portable-review/SKILL.md",
            "---\nname: portable-review\ndescription: Review evidence.\n---\n\n"
            "Compare every claim with its evidence.",
        )
        archive.writestr(
            "rules/portable-grounding.mdc",
            "---\nalwaysApply: true\n---\n\nDo not invent evidence.",
        )
    return buffer.getvalue()


def test_customization_resources_apply_to_a_real_run_context() -> None:
    with _client() as client:
        skill_response = client.post(
            "/v1/skills",
            headers=HEADERS,
            json={
                "slug": "evidence-review",
                "definition": {
                    "name": "evidence-review",
                    "description": "Review evidence before answering.",
                    "instructions": "Check every material claim against supplied evidence.",
                    "required_tools": ["admin.not-granted"],
                },
            },
        )
        assert skill_response.status_code == 201
        skill = skill_response.json()

        rule_response = client.post(
            "/v1/rules",
            headers=HEADERS,
            json={
                "slug": "grounded-output",
                "scope": "workspace",
                "definition": {
                    "name": "Grounded output",
                    "content": "Never invent evidence or tool results.",
                    "activation": "always",
                },
            },
        )
        assert rule_response.status_code == 201

        preferences = client.patch(
            "/v1/preferences",
            headers=HEADERS,
            json={
                "expected_revision": 0,
                "content": "Answer concisely in the user's language.",
            },
        )
        assert preferences.status_code == 200
        assert preferences.json()["revision"] == 1

        created_agent = client.post(
            "/v1/agents",
            headers=HEADERS,
            json={
                "slug": "customized-agent",
                "definition": {
                    "identity": {"name": "Customized Agent"},
                    "instructions": "Turn the supplied context into a useful result.",
                    "model": {"provider": "deepseek", "model": "test-model"},
                    "skills": [
                        {
                            "skill_version_id": skill["current_version_id"],
                            "mode": "always",
                        }
                    ],
                },
            },
        )
        assert created_agent.status_code == 201
        agent = created_agent.json()

        thread_response = client.post(
            "/v1/threads",
            headers=HEADERS,
            json={"agent_id": agent["id"], "title": "Customization proof"},
        )
        assert thread_response.status_code == 201
        thread = thread_response.json()
        configuration = client.get(
            f"/v1/threads/{thread['id']}/configuration", headers=HEADERS
        )
        assert configuration.status_code == 200
        assert configuration.json()["revision"] == 0

        run_response = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=HEADERS,
            json={"input": "Summarize the evidence.", "thinking": False},
        )
        assert run_response.status_code == 202
        run_id = run_response.json()["id"]
        for _ in range(100):
            run = client.get(f"/v1/runs/{run_id}", headers=HEADERS).json()
            if run["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert run["status"] == "completed"

        context_response = client.get(
            f"/v1/runs/{run_id}/context", headers=HEADERS
        )
        assert context_response.status_code == 200
        entries = context_response.json()["entries"]
        layers = {entry["label"] for entry in entries}
        assert "workspace_rules" in layers
        assert "user_preferences" in layers
        assert "active_skills" in layers
        customization_entries = [
            entry
            for entry in entries
            if entry["label"]
            in {"workspace_rules", "user_preferences", "active_skills"}
        ]
        assert all(entry["digest"] for entry in customization_entries)
        assert all(entry["source_version"] for entry in customization_entries)


def test_plugin_inspection_requires_same_archive_and_installs_disabled() -> None:
    archive = _plugin_archive()
    with _client() as client:
        inspected = client.post(
            "/v1/plugins/inspect",
            headers=HEADERS,
            files={"file": ("plugin.zip", archive, "application/zip")},
        )
        assert inspected.status_code == 200
        preview = inspected.json()
        assert preview["inspection"]["skills"][0]["name"] == "portable-review"
        assert preview["inspection_receipt"].startswith("alcpir1.")
        assert len(preview["inspection_digest"]) == 64
        assert len(preview["permissions_hash"]) == 64
        assert preview["policy_revision"] == "plugin-install-policy-v1"
        assert preview["expires_at"] > int(time.time())

        digest_only = client.post(
            "/v1/plugins/install",
            headers=HEADERS,
            files={"file": ("plugin.zip", archive, "application/zip")},
            data={"inspection_digest": preview["inspection_digest"]},
        )
        assert digest_only.status_code == 422

        changed = client.post(
            "/v1/plugins/install",
            headers=HEADERS,
            files={"file": ("plugin.zip", archive + b"changed", "application/zip")},
            data={"inspection_receipt": preview["inspection_receipt"]},
        )
        assert changed.status_code == 409
        assert changed.json()["detail"]["code"] == "invalid_plugin_inspection_receipt"

        wrong_workspace = client.post(
            "/v1/plugins/install",
            headers={"X-Alcuin-Workspace": "ws_other"},
            files={"file": ("plugin.zip", archive, "application/zip")},
            data={"inspection_receipt": preview["inspection_receipt"]},
        )
        assert wrong_workspace.status_code == 409
        assert wrong_workspace.json()["detail"]["code"] == "invalid_plugin_inspection_receipt"

        tampered_receipt = preview["inspection_receipt"][:-1] + (
            "A" if preview["inspection_receipt"][-1] != "A" else "B"
        )
        tampered = client.post(
            "/v1/plugins/install",
            headers=HEADERS,
            files={"file": ("plugin.zip", archive, "application/zip")},
            data={"inspection_receipt": tampered_receipt},
        )
        assert tampered.status_code == 409
        assert tampered.json()["detail"]["code"] == "invalid_plugin_inspection_receipt"

        installed = client.post(
            "/v1/plugins/install",
            headers=HEADERS,
            files={"file": ("plugin.zip", archive, "application/zip")},
            data={"inspection_receipt": preview["inspection_receipt"]},
        )
        assert installed.status_code == 201
        result = installed.json()
        assert result["status"] == "installed_disabled"
        assert result["skills"][0]["enabled"] is False
        assert result["rules"][0]["enabled"] is False

        duplicate = client.post(
            "/v1/plugins/install",
            headers=HEADERS,
            files={"file": ("plugin.zip", archive, "application/zip")},
            data={"inspection_receipt": preview["inspection_receipt"]},
        )
        assert duplicate.status_code == 409


def test_customization_summary_endpoints_are_scoped_bounded_and_body_safe() -> None:
    store = create_test_store()
    try:
        with store.connection:
            store.connection.execute(
                "INSERT INTO workspaces(id, name, created_at) VALUES (?, ?, ?)",
                ("ws_other", "Other Workspace", "2026-08-29T00:00:00+00:00"),
            )

        app = create_app(
            Settings(
                searxng_url="",
                qdrant_url="",
                dashscope_api_key="",
                deepseek_api_key="",
                openai_api_key="",
            ),
            store=store,
        )
        with TestClient(app) as client:
            instruction_marker = "PRIVATE_SKILL_BODY_MUST_NOT_LEAK"
            resource_marker = "PRIVATE_SKILL_RESOURCE_MUST_NOT_LEAK"
            rule_marker = "PRIVATE_RULE_BODY_MUST_NOT_LEAK"
            secret_reference = "secret://workspace/customization-api"

            skill = client.post(
                "/v1/skills",
                headers=HEADERS,
                json={
                    "slug": "safe-summary",
                    "source_ref": secret_reference,
                    "definition": {
                        "name": "safe-summary",
                        "description": "Safe Skill list description",
                        "instructions": instruction_marker,
                        "required_tools": ["knowledge.search"],
                        "metadata": {
                            "display_name": "Safe Summary",
                            "credential_ref": secret_reference,
                        },
                        "resources": [
                            {
                                "path": "references/private.md",
                                "kind": "reference",
                                "media_type": "text/markdown",
                                "size": len(resource_marker),
                                "digest": "a" * 64,
                                "content": resource_marker,
                            }
                        ],
                    },
                },
            )
            assert skill.status_code == 201

            disabled_skill = client.post(
                "/v1/skills",
                headers=HEADERS,
                json={
                    "slug": "disabled-summary",
                    "enabled": False,
                    "definition": {
                        "name": "disabled-summary",
                        "description": "Disabled summary",
                        "instructions": "Remain disabled.",
                    },
                },
            )
            assert disabled_skill.status_code == 201

            rule = client.post(
                "/v1/rules",
                headers=HEADERS,
                json={
                    "slug": "safe-rule-summary",
                    "scope": "library",
                    "source_ref": secret_reference,
                    "definition": {
                        "name": "Safe Rule Summary",
                        "description": "Safe Rule list description",
                        "content": rule_marker,
                        "activation": "conditional",
                        "conditions": {
                            "prompt_terms": ["evidence"],
                            "context_paths": ["record.status"],
                        },
                        "priority": 70,
                    },
                },
            )
            assert rule.status_code == 201

            foreign_headers = {"X-Alcuin-Workspace": "ws_other"}
            foreign_skill = client.post(
                "/v1/skills",
                headers=foreign_headers,
                json={
                    "slug": "foreign-summary",
                    "definition": {
                        "name": "foreign-summary",
                        "description": "Foreign Skill",
                        "instructions": "Never cross Workspace boundaries.",
                    },
                },
            )
            assert foreign_skill.status_code == 201
            foreign_rule = client.post(
                "/v1/rules",
                headers=foreign_headers,
                json={
                    "slug": "foreign-rule-summary",
                    "scope": "library",
                    "definition": {
                        "name": "Foreign Rule",
                        "content": "Never cross Workspace boundaries.",
                    },
                },
            )
            assert foreign_rule.status_code == 201

            skills_response = client.get(
                "/v1/skills/summaries?limit=100",
                headers=HEADERS,
            )
            assert skills_response.status_code == 200
            skill_summaries = skills_response.json()
            assert {item["workspace_id"] for item in skill_summaries} == {"ws_demo"}
            assert foreign_skill.json()["id"] not in {
                item["id"] for item in skill_summaries
            }
            safe_skill = next(
                item for item in skill_summaries if item["id"] == skill.json()["id"]
            )
            assert safe_skill == {
                "id": skill.json()["id"],
                "workspace_id": "ws_demo",
                "slug": "safe-summary",
                "enabled": True,
                "current_version_id": skill.json()["current_version_id"],
                "definition_sha256": skill.json()["definition_sha256"],
                "source_kind": "native",
                "name": "safe-summary",
                "display_name": "Safe Summary",
                "description": "Safe Skill list description",
                "disable_model_invocation": False,
                "user_invocable": True,
                "required_tools": ["knowledge.search"],
                "resource_count": 1,
                "created_at": skill.json()["created_at"],
                "updated_at": skill.json()["updated_at"],
            }
            serialized_skills = json.dumps(skill_summaries)
            assert all(
                forbidden_key not in item
                for item in skill_summaries
                for forbidden_key in (
                    "definition",
                    "instructions",
                    "metadata",
                    "resources",
                    "source_ref",
                )
            )
            for private_value in (
                instruction_marker,
                resource_marker,
                secret_reference,
            ):
                assert private_value not in serialized_skills

            enabled_page = client.get(
                "/v1/skills/summaries?enabled_only=true&limit=1&offset=0",
                headers=HEADERS,
            )
            assert enabled_page.status_code == 200
            assert len(enabled_page.json()) == 1
            assert enabled_page.json()[0]["enabled"] is True

            rules_response = client.get(
                "/v1/rules/summaries?scope=library&limit=100",
                headers=HEADERS,
            )
            assert rules_response.status_code == 200
            rule_summaries = rules_response.json()
            assert {item["workspace_id"] for item in rule_summaries} == {"ws_demo"}
            assert foreign_rule.json()["id"] not in {
                item["id"] for item in rule_summaries
            }
            safe_rule = next(
                item for item in rule_summaries if item["id"] == rule.json()["id"]
            )
            assert safe_rule["name"] == "Safe Rule Summary"
            assert safe_rule["description"] == "Safe Rule list description"
            assert safe_rule["activation"] == "conditional"
            assert safe_rule["priority"] == 70
            assert safe_rule["condition_count"] == 2
            serialized_rules = json.dumps(rule_summaries)
            assert all(
                forbidden_key not in item
                for item in rule_summaries
                for forbidden_key in (
                    "definition",
                    "content",
                    "conditions",
                    "source_ref",
                    "preview",
                )
            )
            for private_value in (
                rule_marker,
                secret_reference,
            ):
                assert private_value not in serialized_rules

            assert client.get(
                "/v1/skills/summaries?limit=101",
                headers=HEADERS,
            ).status_code == 422
            assert client.get(
                "/v1/rules/summaries?offset=-1",
                headers=HEADERS,
            ).status_code == 422
    finally:
        store.close()
