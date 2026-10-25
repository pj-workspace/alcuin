"""Authorize explicit Canvas references and capture immutable model input."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import HTTPException

from alcuin_core.contracts import ArtifactReferenceInput
from alcuin_storage import RuntimeRepository

MAX_ARTIFACT_REFERENCE_BYTES = 100_000


def resolve_artifact_references(
    repository: RuntimeRepository,
    workspace_id: str,
    thread_id: str,
    references: list[ArtifactReferenceInput],
    *,
    embed: bool = False,
) -> list[dict[str, Any]]:
    """Only server-owned fields enter snapshots; absent and foreign ids look alike."""
    if references and embed:
        raise HTTPException(status_code=403, detail="Artifact references are unavailable in embedded sessions")
    snapshots: list[dict[str, Any]] = []
    for reference in references:
        artifact = repository.get_artifact(workspace_id, reference.artifact_id)
        if artifact is None or artifact["workspace_id"] != workspace_id or artifact["thread_id"] != thread_id:
            raise HTTPException(status_code=422, detail={
                "code": "artifact_reference_unavailable",
                "message": "The referenced document is unavailable in this conversation",
            })
        if artifact["version"] != reference.expected_version:
            raise HTTPException(status_code=409, detail={
                "code": "artifact_reference_changed",
                "message": "The document changed. Review the latest saved content before sending again.",
            })
        content = artifact["content"]
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_ARTIFACT_REFERENCE_BYTES:
            raise HTTPException(status_code=422, detail={
                "code": "artifact_reference_too_large",
                "message": "The referenced document exceeds 100000 UTF-8 bytes",
            })
        source_run = repository.get_run(workspace_id, artifact["source_run_id"])
        snapshots.append({
            "type": "artifact_reference",
            "artifact_id": artifact["id"],
            "version": artifact["version"],
            "title": artifact["title"],
            "content_type": artifact["content_type"],
            "source_run_id": artifact["source_run_id"],
            "source_run_status": source_run["status"] if source_run else "unknown",
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "content": content,
        })
    return snapshots
