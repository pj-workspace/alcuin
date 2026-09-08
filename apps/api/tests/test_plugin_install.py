from __future__ import annotations

import hashlib
import io
import zipfile

import pytest
from alcuin_api.plugin_install import (
    PLUGIN_INSPECTION_POLICY_REVISION,
    PLUGIN_INSPECTION_RECEIPT_PREFIX,
    PluginInspectionReceiptError,
    customization_bundle_draft,
    issue_plugin_inspection_receipt,
    plugin_permissions_hash,
    verify_plugin_inspection_receipt,
)
from alcuin_customization import inspect_plugin_archive

SIGNING_SECRET = "test-plugin-receipt-signing-secret-with-enough-entropy"
WORKSPACE_ID = "ws_plugin_review"
PERMISSIONS = (
    {
        "id": "filesystem:read",
        "reason": "Read bundled reference files.",
        "risk": "low",
        "required": True,
    },
    {
        "id": "network:mcp",
        "reason": "Connect to the reviewed MCP endpoint after enablement.",
        "risk": "medium",
        "required": False,
    },
)


def _archive(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def test_cursor_plugin_converts_to_disabled_inert_resources() -> None:
    inspection = inspect_plugin_archive(
        _archive(
            {
                ".cursor-plugin/plugin.json": (
                    '{"name":"review-pack","version":"1.0.0",'
                    '"skills":"skills","rules":"rules"}'
                ),
                "skills/review/SKILL.md": (
                    "---\nname: review\ndescription: Review evidence.\n---\n\n"
                    "Compare every claim with evidence."
                ),
                "skills/review/references/CHECKS.md": "# Checks\nCitations and dates.",
                "skills/review/scripts/run.py": "raise RuntimeError('never execute')",
                "rules/grounded.mdc": (
                    "---\nalwaysApply: true\n---\n\nDo not invent evidence."
                ),
            }
        )
    )

    draft = customization_bundle_draft(inspection)

    assert len(draft.skills) == 1
    assert draft.skills[0].enabled is False
    assert draft.skills[0].source_kind == "cursor_plugin"
    assert (
        draft.skills[0].definition.resources[0].content
        == "# Checks\nCitations and dates."
    )
    script = next(
        item for item in draft.skills[0].definition.resources if item.kind == "script"
    )
    assert script.content is None
    assert len(draft.rules) == 1
    assert draft.rules[0].enabled is False
    assert draft.rules[0].definition.activation.value == "always"


def test_plugin_inspection_receipt_binds_exact_review_scope() -> None:
    archive = _archive({"plugin.json": '{"name":"reviewed","version":"1.0.0"}'})
    issued = issue_plugin_inspection_receipt(
        signing_secret=SIGNING_SECRET,
        workspace_id=WORKSPACE_ID,
        archive=archive,
        permissions=PERMISSIONS,
        now=1_000,
        ttl_seconds=300,
    )

    verified = verify_plugin_inspection_receipt(
        issued.receipt,
        signing_secret=SIGNING_SECRET,
        workspace_id=WORKSPACE_ID,
        archive=archive,
        permissions=PERMISSIONS,
        now=1_299,
    )

    assert issued.receipt.startswith(f"{PLUGIN_INSPECTION_RECEIPT_PREFIX}.")
    assert not issued.receipt.startswith("alc1.")
    assert issued.inspection_digest == hashlib.sha256(archive).hexdigest()
    assert verified.workspace_id == WORKSPACE_ID
    assert verified.archive_digest == issued.inspection_digest
    assert verified.permissions_hash == plugin_permissions_hash(PERMISSIONS)
    assert verified.policy_revision == PLUGIN_INSPECTION_POLICY_REVISION
    assert verified.issued_at == 1_000
    assert verified.expires_at == 1_300


@pytest.mark.parametrize("part_index", [0, 1, 2])
def test_plugin_inspection_receipt_rejects_prefix_payload_or_signature_tampering(
    part_index: int,
) -> None:
    archive = b"exact inspected archive bytes"
    issued = issue_plugin_inspection_receipt(
        signing_secret=SIGNING_SECRET,
        workspace_id=WORKSPACE_ID,
        archive=archive,
        permissions=PERMISSIONS,
        now=1_000,
    )
    parts = issued.receipt.split(".")
    original = parts[part_index]
    parts[part_index] = ("A" if original[0] != "A" else "B") + original[1:]
    tampered = ".".join(parts)

    with pytest.raises(PluginInspectionReceiptError) as rejected:
        verify_plugin_inspection_receipt(
            tampered,
            signing_secret=SIGNING_SECRET,
            workspace_id=WORKSPACE_ID,
            archive=archive,
            permissions=PERMISSIONS,
            now=1_100,
        )

    assert rejected.value.code == "invalid_plugin_inspection_receipt"
    assert SIGNING_SECRET not in rejected.value.message
    assert issued.inspection_digest not in rejected.value.message


def test_plugin_inspection_receipt_rejects_wrong_workspace_without_scope_leak() -> None:
    archive = b"plugin archive"
    issued = issue_plugin_inspection_receipt(
        signing_secret=SIGNING_SECRET,
        workspace_id=WORKSPACE_ID,
        archive=archive,
        permissions=PERMISSIONS,
        now=2_000,
    )

    with pytest.raises(PluginInspectionReceiptError) as rejected:
        verify_plugin_inspection_receipt(
            issued.receipt,
            signing_secret=SIGNING_SECRET,
            workspace_id="ws_other_private",
            archive=archive,
            permissions=PERMISSIONS,
            now=2_100,
        )

    assert rejected.value.code == "invalid_plugin_inspection_receipt"
    assert WORKSPACE_ID not in rejected.value.message
    assert "ws_other_private" not in rejected.value.message


def test_plugin_inspection_receipt_rejects_expired_receipt() -> None:
    archive = b"plugin archive"
    issued = issue_plugin_inspection_receipt(
        signing_secret=SIGNING_SECRET,
        workspace_id=WORKSPACE_ID,
        archive=archive,
        permissions=PERMISSIONS,
        now=3_000,
        ttl_seconds=60,
    )

    with pytest.raises(PluginInspectionReceiptError) as rejected:
        verify_plugin_inspection_receipt(
            issued.receipt,
            signing_secret=SIGNING_SECRET,
            workspace_id=WORKSPACE_ID,
            archive=archive,
            permissions=PERMISSIONS,
            now=3_060,
        )

    assert rejected.value.code == "plugin_inspection_receipt_expired"
    assert WORKSPACE_ID not in rejected.value.message


def test_plugin_inspection_receipt_rejects_changed_permission_set() -> None:
    archive = b"plugin archive"
    issued = issue_plugin_inspection_receipt(
        signing_secret=SIGNING_SECRET,
        workspace_id=WORKSPACE_ID,
        archive=archive,
        permissions=PERMISSIONS,
        now=4_000,
    )
    changed_permissions = (*PERMISSIONS, {"id": "process:execute", "risk": "high"})

    with pytest.raises(PluginInspectionReceiptError) as rejected:
        verify_plugin_inspection_receipt(
            issued.receipt,
            signing_secret=SIGNING_SECRET,
            workspace_id=WORKSPACE_ID,
            archive=archive,
            permissions=changed_permissions,
            now=4_100,
        )

    assert rejected.value.code == "invalid_plugin_inspection_receipt"
    assert "process:execute" not in rejected.value.message


def test_plugin_inspection_receipt_rejects_changed_archive_and_policy() -> None:
    archive = b"plugin archive v1"
    issued = issue_plugin_inspection_receipt(
        signing_secret=SIGNING_SECRET,
        workspace_id=WORKSPACE_ID,
        archive=archive,
        permissions=PERMISSIONS,
        now=5_000,
    )

    for changed_archive, changed_policy in (
        (b"plugin archive v2", PLUGIN_INSPECTION_POLICY_REVISION),
        (archive, "plugin-install-policy-v2"),
    ):
        with pytest.raises(PluginInspectionReceiptError) as rejected:
            verify_plugin_inspection_receipt(
                issued.receipt,
                signing_secret=SIGNING_SECRET,
                workspace_id=WORKSPACE_ID,
                archive=changed_archive,
                permissions=PERMISSIONS,
                policy_revision=changed_policy,
                now=5_100,
            )
        assert rejected.value.code == "invalid_plugin_inspection_receipt"
        assert hashlib.sha256(changed_archive).hexdigest() not in rejected.value.message
        assert changed_policy not in rejected.value.message


def test_permission_hash_is_stable_for_semantically_identical_ordering() -> None:
    reordered = tuple(
        {key: permission[key] for key in reversed(permission)}
        for permission in reversed(PERMISSIONS)
    )

    assert plugin_permissions_hash(reordered) == plugin_permissions_hash(PERMISSIONS)
