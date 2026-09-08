"""Convert a safely inspected plugin archive into disabled Alcuin resources."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from alcuin_core.customization import (
    RuleConditions,
    RuleCreate,
    RuleVersionDefinition,
    SkillCreate,
    SkillResourceDefinition,
    SkillVersionDefinition,
)
from alcuin_customization import PluginBundleInspection

PLUGIN_INSPECTION_RECEIPT_PREFIX = "alcpir1"
PLUGIN_INSPECTION_POLICY_REVISION = "plugin-install-policy-v1"
DEFAULT_PLUGIN_RECEIPT_TTL_SECONDS = 300
MAX_PLUGIN_RECEIPT_TTL_SECONDS = 900
MAX_PLUGIN_RECEIPT_BYTES = 4_096
MAX_PERMISSION_CANONICAL_BYTES = 64 * 1_024
_RECEIPT_KEY_DOMAIN = b"alcuin/plugin-inspection-receipt/signing-key/v1"
_RECEIPT_TYPE = "plugin-inspection-receipt"
_HEX_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class PluginInspectionReceiptError(ValueError):
    """A receipt failure safe to surface without revealing claims or signing material."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class IssuedPluginInspectionReceipt:
    receipt: str
    inspection_digest: str
    permissions_hash: str
    policy_revision: str
    expires_at: int


@dataclass(frozen=True)
class VerifiedPluginInspectionReceipt:
    workspace_id: str
    archive_digest: str
    permissions_hash: str
    policy_revision: str
    issued_at: int
    expires_at: int


def _receipt_error(*, expired: bool = False) -> PluginInspectionReceiptError:
    if expired:
        return PluginInspectionReceiptError(
            "plugin_inspection_receipt_expired",
            "Plugin inspection receipt expired; inspect the archive again.",
        )
    return PluginInspectionReceiptError(
        "invalid_plugin_inspection_receipt",
        "Plugin inspection receipt is invalid; inspect the archive again.",
    )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    try:
        encoded = value.encode("ascii")
        decoded = base64.b64decode(
            encoded + b"=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
        if not hmac.compare_digest(_b64encode(decoded), value):
            raise _receipt_error()
        return decoded
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise _receipt_error() from exc


def _signing_key(signing_secret: str) -> bytes:
    if not isinstance(signing_secret, str) or not signing_secret:
        raise _receipt_error()
    return hmac.new(
        signing_secret.encode("utf-8"),
        _RECEIPT_KEY_DOMAIN,
        hashlib.sha256,
    ).digest()


def _signature(payload: str, signing_secret: str) -> bytes:
    signed = f"{PLUGIN_INSPECTION_RECEIPT_PREFIX}.{payload}".encode("ascii")
    return hmac.new(_signing_key(signing_secret), signed, hashlib.sha256).digest()


def plugin_permissions_hash(permissions: Sequence[Mapping[str, Any]]) -> str:
    """Hash permission semantics independently of mapping and permission-list order."""
    try:
        if isinstance(permissions, (str, bytes)) or not isinstance(
            permissions, Sequence
        ):
            raise TypeError("permissions must be a sequence")
        normalized = sorted(
            json.dumps(
                dict(permission),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            for permission in permissions
            if isinstance(permission, Mapping)
        )
        if len(normalized) != len(permissions):
            raise TypeError("permissions must contain mappings")
        canonical = ("[" + ",".join(normalized) + "]").encode("utf-8")
        if len(canonical) > MAX_PERMISSION_CANONICAL_BYTES:
            raise ValueError("permissions exceed the receipt limit")
    except (TypeError, ValueError) as exc:
        raise _receipt_error() from exc
    return hashlib.sha256(canonical).hexdigest()


def issue_plugin_inspection_receipt(
    *,
    signing_secret: str,
    workspace_id: str,
    archive: bytes,
    permissions: Sequence[Mapping[str, Any]],
    policy_revision: str = PLUGIN_INSPECTION_POLICY_REVISION,
    ttl_seconds: int = DEFAULT_PLUGIN_RECEIPT_TTL_SECONDS,
    now: int | None = None,
) -> IssuedPluginInspectionReceipt:
    """Issue authorization for later disabled install of these exact inspected bytes.

    ``inspection_digest`` is returned for human display only. Installation must submit the signed
    ``receipt`` and the archive bytes, then call ``verify_plugin_inspection_receipt``; a digest
    echoed by the client is not authorization.
    """
    if (
        not isinstance(workspace_id, str)
        or not workspace_id
        or len(workspace_id) > 200
        or not isinstance(archive, bytes)
        or not isinstance(policy_revision, str)
        or not policy_revision
        or len(policy_revision) > 200
        or not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or not 1 <= ttl_seconds <= MAX_PLUGIN_RECEIPT_TTL_SECONDS
    ):
        raise _receipt_error()
    issued_at = int(time.time()) if now is None else now
    if not isinstance(issued_at, int) or isinstance(issued_at, bool) or issued_at < 0:
        raise _receipt_error()
    archive_digest = hashlib.sha256(archive).hexdigest()
    permissions_hash = plugin_permissions_hash(permissions)
    claims = {
        "typ": _RECEIPT_TYPE,
        "v": 1,
        "workspace_id": workspace_id,
        "archive_digest": archive_digest,
        "permissions_hash": permissions_hash,
        "policy_revision": policy_revision,
        "issued_at": issued_at,
        "expires_at": issued_at + ttl_seconds,
    }
    payload = _b64encode(
        json.dumps(
            claims,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    receipt = (
        f"{PLUGIN_INSPECTION_RECEIPT_PREFIX}.{payload}."
        f"{_b64encode(_signature(payload, signing_secret))}"
    )
    return IssuedPluginInspectionReceipt(
        receipt=receipt,
        inspection_digest=archive_digest,
        permissions_hash=permissions_hash,
        policy_revision=policy_revision,
        expires_at=claims["expires_at"],
    )


def verify_plugin_inspection_receipt(
    receipt: str,
    *,
    signing_secret: str,
    workspace_id: str,
    archive: bytes,
    permissions: Sequence[Mapping[str, Any]],
    policy_revision: str = PLUGIN_INSPECTION_POLICY_REVISION,
    now: int | None = None,
) -> VerifiedPluginInspectionReceipt:
    """Verify signature, Workspace, exact bytes, permission set, policy revision, and TTL."""
    try:
        if (
            not isinstance(receipt, str)
            or len(receipt.encode("utf-8")) > MAX_PLUGIN_RECEIPT_BYTES
        ):
            raise _receipt_error()
        prefix, payload, signature = receipt.split(".")
        if prefix != PLUGIN_INSPECTION_RECEIPT_PREFIX:
            raise _receipt_error()
        if not hmac.compare_digest(
            _signature(payload, signing_secret),
            _b64decode(signature),
        ):
            raise _receipt_error()
        decoded = json.loads(_b64decode(payload))
        if not isinstance(decoded, dict) or set(decoded) != {
            "typ",
            "v",
            "workspace_id",
            "archive_digest",
            "permissions_hash",
            "policy_revision",
            "issued_at",
            "expires_at",
        }:
            raise _receipt_error()
        if decoded.get("typ") != _RECEIPT_TYPE or decoded.get("v") != 1:
            raise _receipt_error()
        issued_at = decoded.get("issued_at")
        expires_at = decoded.get("expires_at")
        if (
            not isinstance(issued_at, int)
            or isinstance(issued_at, bool)
            or not isinstance(expires_at, int)
            or isinstance(expires_at, bool)
            or issued_at < 0
            or expires_at <= issued_at
            or expires_at - issued_at > MAX_PLUGIN_RECEIPT_TTL_SECONDS
        ):
            raise _receipt_error()
        current_time = int(time.time()) if now is None else now
        if (
            not isinstance(current_time, int)
            or isinstance(current_time, bool)
            or current_time < 0
            or issued_at > current_time + 30
        ):
            raise _receipt_error()
        if expires_at <= current_time:
            raise _receipt_error(expired=True)
        archive_digest = hashlib.sha256(archive).hexdigest()
        permissions_hash = plugin_permissions_hash(permissions)
        expected_values = (
            (decoded.get("workspace_id"), workspace_id),
            (decoded.get("archive_digest"), archive_digest),
            (decoded.get("permissions_hash"), permissions_hash),
            (decoded.get("policy_revision"), policy_revision),
        )
        if any(
            not isinstance(actual, str)
            or not isinstance(expected, str)
            or not hmac.compare_digest(actual, expected)
            for actual, expected in expected_values
        ):
            raise _receipt_error()
        if not _HEX_SHA256.fullmatch(
            str(decoded["archive_digest"])
        ) or not _HEX_SHA256.fullmatch(str(decoded["permissions_hash"])):
            raise _receipt_error()
        return VerifiedPluginInspectionReceipt(
            workspace_id=str(decoded["workspace_id"]),
            archive_digest=str(decoded["archive_digest"]),
            permissions_hash=str(decoded["permissions_hash"]),
            policy_revision=str(decoded["policy_revision"]),
            issued_at=issued_at,
            expires_at=expires_at,
        )
    except PluginInspectionReceiptError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _receipt_error() from exc


@dataclass(frozen=True)
class CustomizationBundleDraft:
    skills: tuple[SkillCreate, ...]
    rules: tuple[RuleCreate, ...]


def _slug(value: str, *, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    normalized = normalized[:64].rstrip("-")
    if not normalized:
        normalized = fallback
    if not normalized[0].isalnum():
        normalized = f"rule-{normalized}"[:64]
    return normalized


def customization_bundle_draft(
    inspection: PluginBundleInspection,
) -> CustomizationBundleDraft:
    """Build inert, disabled resources from one already-inspected archive."""
    source_kind = (
        "agent_plugin" if inspection.format == "agent-plugin-1.0" else "cursor_plugin"
    )
    source_prefix = f"{inspection.name}@{inspection.version}"
    skills = tuple(
        SkillCreate(
            slug=skill.name,
            enabled=False,
            source_kind=source_kind,
            source_ref=f"{source_prefix}:{skill.source_path}",
            definition=SkillVersionDefinition(
                name=skill.name,
                description=skill.description,
                instructions=skill.instructions,
                disable_model_invocation=skill.disable_model_invocation,
                user_invocable=skill.user_invocable,
                required_tools=list(skill.required_tools),
                paths=list(skill.paths),
                metadata=skill.metadata,
                resources=[
                    SkillResourceDefinition(
                        path=resource.path,
                        kind=resource.kind,  # type: ignore[arg-type]
                        media_type=resource.media_type,
                        size=resource.size,
                        digest=resource.digest,
                        content=resource.content,
                    )
                    for resource in skill.resources
                ],
            ),
        )
        for skill in inspection.skills
    )
    rules = tuple(
        RuleCreate(
            slug=_slug(rule.name, fallback="imported-rule"),
            scope="library",
            enabled=False,
            source_kind=source_kind,
            source_ref=f"{source_prefix}:{rule.source_path}",
            definition=RuleVersionDefinition(
                name=rule.name,
                description=rule.description,
                content=rule.content,
                activation=rule.activation.value,
                conditions=RuleConditions.model_validate(rule.conditions),
                priority=rule.priority,
            ),
        )
        for rule in inspection.rules
    )
    skill_slugs = [item.slug for item in skills]
    rule_slugs = [item.slug for item in rules]
    if len(skill_slugs) != len(set(skill_slugs)):
        raise ValueError("Plugin contains duplicate Skill names")
    if len(rule_slugs) != len(set(rule_slugs)):
        raise ValueError("Plugin contains duplicate Rule names after normalization")
    return CustomizationBundleDraft(skills=skills, rules=rules)
