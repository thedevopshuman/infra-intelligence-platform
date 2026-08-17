"""Least-privilege plugin handshake and bounded session issuance."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

from iip.application.investigate import canonical_digest
from iip.application.ports import (
    ActorContext,
    Clock,
    PluginSessionRepository,
    PolicyDecisionPoint,
)


class PluginHandshakeError(RuntimeError):
    """Handshake failure carrying only a stable external code."""


@dataclass(frozen=True)
class OpenPluginSessionCommand:
    actor: ActorContext
    manifest: Mapping[str, Any]
    requested_capabilities: tuple[str, ...]
    capability_token: str
    max_requests: int = 1
    max_wall_time_seconds: int = 60
    max_output_bytes: int = 16_777_216


class PluginSessionService:
    """Validate declarations and issue a metadata-only capability session."""

    def __init__(
        self,
        policy: PolicyDecisionPoint,
        repository: PluginSessionRepository,
        clock: Clock,
    ) -> None:
        self._policy = policy
        self._repository = repository
        self._clock = clock

    def open(self, command: OpenPluginSessionCommand) -> Mapping[str, object]:
        metadata, spec = self._validate(command)
        declared = set(spec["capabilities"])
        requested = set(command.requested_capabilities)
        if not requested or not requested.issubset(declared):
            raise PluginHandshakeError("plugin.capability.not-declared")
        decision = self._policy.decide(
            command.actor,
            "plugin:open-session",
            {
                "tenantId": command.actor.tenant_id,
                "pluginId": metadata["id"],
                "pluginVersion": metadata["version"],
                "capabilities": sorted(requested),
            },
        )
        if not decision.allowed:
            raise PluginHandshakeError("plugin.session.policy-denied")

        token_digest = "sha256:" + hashlib.sha256(
            command.capability_token.encode("utf-8")
        ).hexdigest()
        material = (
            f"{command.actor.tenant_id}\x1f{metadata['id']}\x1f"
            f"{metadata['version']}\x1f{token_digest}"
        ).encode()
        session_id = "psn_" + hashlib.sha256(material).hexdigest()[:32]
        existing = self._repository.get_plugin_session(command.actor, session_id)
        if existing is not None:
            return existing
        created_at = self._clock.now()
        expires_at = (
            datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            + timedelta(seconds=command.max_wall_time_seconds)
        ).isoformat().replace("+00:00", "Z")
        session: dict[str, object] = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginSession",
            "metadata": {
                "id": session_id,
                "tenantId": command.actor.tenant_id,
                "pluginId": metadata["id"],
                "pluginVersion": metadata["version"],
                "createdAt": created_at,
            },
            "spec": {
                "manifestDigest": canonical_digest(command.manifest),
                "protocolVersion": spec["protocolVersion"],
                "grantedCapabilities": sorted(requested),
                "capabilityTokenRef": (
                    f"capability://{command.actor.tenant_id}/sessions/{session_id}"
                ),
                "capabilityTokenDigest": token_digest,
                "expiresAt": expires_at,
                "cancellation": {
                    "supported": True,
                    "endpoint": "/v1/plugin-invocations/{invocationId}/cancel",
                },
                "limits": {
                    "maxRequests": command.max_requests,
                    "maxWallTimeSeconds": command.max_wall_time_seconds,
                    "maxOutputBytes": command.max_output_bytes,
                },
            },
            "status": "ready",
        }
        self._repository.commit_plugin_session(command.actor, session)
        return session

    @staticmethod
    def _validate(
        command: OpenPluginSessionCommand,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            if command.manifest.get("apiVersion") != "iip.platform/v1alpha1" or command.manifest.get(
                "kind"
            ) != "Plugin":
                raise KeyError
            metadata = dict(command.manifest["metadata"])
            spec = dict(command.manifest["spec"])
        except (KeyError, TypeError, ValueError):
            raise PluginHandshakeError("plugin.manifest.invalid") from None
        if spec.get("protocolVersion") != "1.0" or not isinstance(
            spec.get("capabilities"), list
        ):
            raise PluginHandshakeError("plugin.protocol.unsupported")
        if (
            not isinstance(command.capability_token, str)
            or len(command.capability_token) < 32
            or any(character.isspace() for character in command.capability_token)
        ):
            raise PluginHandshakeError("plugin.capability-token.invalid")
        if not 1 <= command.max_requests <= 10_000:
            raise PluginHandshakeError("plugin.limit.invalid")
        if not 1 <= command.max_wall_time_seconds <= 3600:
            raise PluginHandshakeError("plugin.limit.invalid")
        if not 1024 <= command.max_output_bytes <= 16_777_216:
            raise PluginHandshakeError("plugin.limit.invalid")
        return metadata, spec
