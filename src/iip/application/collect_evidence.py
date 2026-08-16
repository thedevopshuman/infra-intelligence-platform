"""Bounded, contract-first evidence collection use case."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional

from iip.application.ports import (
    ActorContext,
    Clock,
    EvidenceIdGenerator,
    EvidenceProvider,
    EvidenceProviderRequest,
    EvidenceRedactionResult,
    EvidenceRedactor,
    EvidenceStore,
    PolicyDecisionPoint,
    RawEvidenceArtifact,
    ResourceRepository,
)


MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_PROVIDER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_TENANT_ID = re.compile(r"[a-zA-Z0-9._-]{1,128}")
_INTEGRATION = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_EVIDENCE_TYPE = re.compile(r"[a-z][a-z0-9._/-]{2,127}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_EVIDENCE_ID = re.compile(r"evd_[a-f0-9]{32}")
_MEDIA_TYPE = re.compile(r"[^/\s]+/[^/\s]+")
_REDACTION_METHOD = re.compile(r"[a-z][a-z0-9._-]{2,63}")
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:authorization|password|passwd|token|secret|api[_-]?key|"
    r"access[_-]?key)\s*[:=]\s*\S+|bearer\s+\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_SIGNED_QUERY = re.compile(
    r"(?i)(?:^|[?&])(?:x-amz-(?:credential|signature|security-token)|"
    r"signature|sig|token|access_token|api[_-]?key)="
)
_URI_USERINFO = re.compile(r"^[a-z][a-z0-9+.-]*://[^/?#\s]*@", re.IGNORECASE)


class EvidenceAuthorizationError(PermissionError):
    """Policy denied a tenant-scoped evidence collection."""


class InvalidEvidenceRequestError(ValueError):
    """The request or untrusted provider result violated the boundary."""


class EvidenceProviderUnavailableError(RuntimeError):
    """The selected provider could not produce a safe artifact."""


class EvidenceDeadlineExceededError(TimeoutError):
    """Collection exceeded its caller-supplied deadline."""


class EvidenceRedactionError(RuntimeError):
    """The artifact could not be inspected and redacted safely."""


@dataclass(frozen=True)
class CollectEvidenceCommand:
    """Caller-authorized upper bound for one evidence retrieval."""

    actor: ActorContext
    provider: str
    integration_id: str
    evidence_type: str
    resource_uids: tuple[str, ...]
    locator: str
    deadline: str
    query: Optional[str] = None
    max_bytes: int = MAX_ARTIFACT_BYTES
    sensitivity: str = "internal"
    retention_class: str = "standard"
    expires_at: Optional[str] = None


class EvidenceCollectionService:
    """Authorize, retrieve, redact, hash, and atomically commit evidence."""

    def __init__(
        self,
        resources: ResourceRepository,
        providers: Mapping[str, EvidenceProvider],
        store: EvidenceStore,
        redactor: EvidenceRedactor,
        policy: PolicyDecisionPoint,
        ids: EvidenceIdGenerator,
        clock: Clock,
    ) -> None:
        self._resources = resources
        self._providers = dict(providers)
        self._store = store
        self._redactor = redactor
        self._policy = policy
        self._ids = ids
        self._clock = clock

    def execute(self, command: CollectEvidenceCommand) -> Mapping[str, object]:
        """Collect one immutable evidence artifact within explicit bounds."""

        deadline = self._authorize(command)

        provider = self._providers.get(command.provider)
        if provider is None:
            raise EvidenceProviderUnavailableError("evidence.provider.unavailable")

        provider_request = EvidenceProviderRequest(
            tenant_id=command.actor.tenant_id,
            actor_id=command.actor.actor_id,
            evidence_type=command.evidence_type,
            integration_id=command.integration_id,
            resource_uids=command.resource_uids,
            locator=command.locator,
            query=command.query,
            max_bytes=command.max_bytes,
            deadline=command.deadline,
        )
        try:
            artifact = provider.fetch(provider_request)
        except Exception:
            raise EvidenceProviderUnavailableError(
                "evidence.provider.unavailable"
            ) from None

        return self._commit_artifact(command, artifact, deadline)

    def record_artifact(
        self,
        command: CollectEvidenceCommand,
        artifact: RawEvidenceArtifact,
    ) -> Mapping[str, object]:
        """Authorize and commit an already supplied untrusted artifact.

        Push receivers use this path after their protocol adapter has decoded and
        bounded a payload. It deliberately applies the same tenant, policy,
        redaction, hashing, and immutable-storage controls as provider retrieval.
        """

        deadline = self._authorize(command)
        return self._commit_artifact(command, artifact, deadline)

    def _authorize(self, command: CollectEvidenceCommand) -> datetime:
        deadline = self._validate_command(command)
        started_at = self._now()
        self._enforce_deadline(started_at, deadline)

        decision = self._policy.decide(
            actor=command.actor,
            action="evidence:collect",
            resource={
                "tenantId": command.actor.tenant_id,
                "provider": command.provider,
                "integrationId": command.integration_id,
                "evidenceType": command.evidence_type,
                "resourceUids": command.resource_uids,
            },
        )
        if not decision.allowed:
            raise EvidenceAuthorizationError(decision.reason_code)

        requested_uids = set(command.resource_uids)
        resolved_uids = {
            resource.identity.uid
            for resource in self._resources.get_many(
                command.actor.tenant_id,
                command.resource_uids,
            )
        }
        if resolved_uids != requested_uids:
            raise InvalidEvidenceRequestError("evidence.resource.unavailable")
        return deadline

    def _commit_artifact(
        self,
        command: CollectEvidenceCommand,
        artifact: RawEvidenceArtifact,
        deadline: datetime,
    ) -> Mapping[str, object]:
        retrieved_at = self._now()
        self._enforce_deadline(retrieved_at, deadline)
        self._validate_artifact(
            artifact,
            retrieved_at=retrieved_at,
            max_bytes=command.max_bytes,
        )

        try:
            redacted = self._redactor.redact(
                artifact.content,
                media_type=artifact.media_type,
                evidence_type=command.evidence_type,
            )
        except Exception:
            raise EvidenceRedactionError("evidence.redaction.failed") from None

        if not isinstance(redacted, EvidenceRedactionResult):
            raise EvidenceRedactionError("evidence.redaction.failed")
        if not isinstance(redacted.content, bytes):
            raise EvidenceRedactionError("evidence.redaction.failed")
        if len(redacted.content) > command.max_bytes:
            raise EvidenceRedactionError("evidence.redaction.output-limited")
        try:
            methods = tuple(redacted.methods)
        except TypeError:
            raise EvidenceRedactionError("evidence.redaction.invalid-metadata") from None
        invalid_methods = any(
            not isinstance(method, str) or not _REDACTION_METHOD.fullmatch(method)
            for method in methods
        )
        if (
            len(methods) > 32
            or invalid_methods
            or len(set(methods)) != len(methods)
        ):
            raise EvidenceRedactionError("evidence.redaction.invalid-metadata")

        recorded_at = self._now()
        self._enforce_deadline(recorded_at, deadline)
        if retrieved_at > recorded_at:
            raise InvalidEvidenceRequestError("evidence.time.invalid")
        if command.expires_at is not None:
            expires_at = self._parse_time(command.expires_at)
            if expires_at <= recorded_at:
                raise InvalidEvidenceRequestError("evidence.expiry.invalid")

        evidence_id = self._ids.new_id()
        if not isinstance(evidence_id, str) or not _EVIDENCE_ID.fullmatch(evidence_id):
            raise InvalidEvidenceRequestError("evidence.id.invalid")

        digest = "sha256:" + hashlib.sha256(redacted.content).hexdigest()
        handling: dict[str, object] = {
            "redaction": {
                "status": "applied" if methods else "not-required",
                "methods": list(methods),
            },
            "sensitivity": command.sensitivity,
            "retentionClass": command.retention_class,
        }
        if command.expires_at is not None:
            handling["expiresAt"] = command.expires_at

        source: dict[str, object] = {
            "provider": command.provider,
            "integrationId": command.integration_id,
            "locator": command.locator,
        }
        spec: dict[str, object] = {
            "type": command.evidence_type,
            "source": source,
            "observedAt": artifact.observed_at,
            "retrievedAt": self._format_time(retrieved_at),
            "resourceRefs": list(command.resource_uids),
            "summary": artifact.summary,
            "artifact": {
                "mediaType": artifact.media_type,
                "contentHash": digest,
                "sizeBytes": len(redacted.content),
                "storageRef": (
                    f"evidence://{command.actor.tenant_id}/{evidence_id}/artifact"
                ),
                "encoding": "identity",
            },
            "handling": handling,
        }
        if command.query is not None:
            spec["query"] = command.query

        document: dict[str, object] = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "Evidence",
            "metadata": {
                "id": evidence_id,
                "tenantId": command.actor.tenant_id,
                "recordedAt": self._format_time(recorded_at),
            },
            "spec": spec,
        }
        self._store.commit(
            command.actor,
            evidence_id,
            document,
            redacted.content,
        )
        return document

    def _validate_command(self, command: CollectEvidenceCommand) -> datetime:
        if not isinstance(command.actor.tenant_id, str) or not _TENANT_ID.fullmatch(
            command.actor.tenant_id
        ):
            raise InvalidEvidenceRequestError("evidence.request.invalid")
        if (
            not isinstance(command.actor.actor_id, str)
            or not command.actor.actor_id
            or len(command.actor.actor_id) > 256
            or any(ord(character) < 32 for character in command.actor.actor_id)
        ):
            raise InvalidEvidenceRequestError("evidence.request.invalid")
        if not isinstance(command.provider, str) or not _PROVIDER.fullmatch(
            command.provider
        ):
            raise InvalidEvidenceRequestError("evidence.request.invalid")
        if not isinstance(command.integration_id, str) or not _INTEGRATION.fullmatch(
            command.integration_id
        ):
            raise InvalidEvidenceRequestError("evidence.request.invalid")
        if not isinstance(command.evidence_type, str) or not _EVIDENCE_TYPE.fullmatch(
            command.evidence_type
        ):
            raise InvalidEvidenceRequestError("evidence.request.invalid")
        if (
            not isinstance(command.resource_uids, tuple)
            or not 1 <= len(command.resource_uids) <= 256
            or any(
                not isinstance(uid, str) or not _RESOURCE_UID.fullmatch(uid)
                for uid in command.resource_uids
            )
            or len(set(command.resource_uids)) != len(command.resource_uids)
        ):
            raise InvalidEvidenceRequestError("evidence.request.invalid")
        if (
            not isinstance(command.max_bytes, int)
            or isinstance(command.max_bytes, bool)
            or not 1 <= command.max_bytes <= MAX_ARTIFACT_BYTES
        ):
            raise InvalidEvidenceRequestError("evidence.request.invalid")
        if command.sensitivity not in (
            "public",
            "internal",
            "confidential",
            "restricted",
        ):
            raise InvalidEvidenceRequestError("evidence.request.invalid")
        if command.retention_class not in (
            "ephemeral",
            "standard",
            "extended",
            "legal-hold",
        ):
            raise InvalidEvidenceRequestError("evidence.request.invalid")
        self._validate_safe_text(command.locator, minimum=1, maximum=2048)
        if any(character.isspace() for character in command.locator):
            raise InvalidEvidenceRequestError("evidence.request.invalid")
        if _URI_USERINFO.search(command.locator) or _SIGNED_QUERY.search(command.locator):
            raise InvalidEvidenceRequestError("evidence.request.contains-secret")
        if command.query is not None:
            self._validate_safe_text(command.query, minimum=1, maximum=4096)
            if _SIGNED_QUERY.search(command.query):
                raise InvalidEvidenceRequestError("evidence.request.contains-secret")
        return self._parse_time(command.deadline)

    def _validate_artifact(
        self,
        artifact: object,
        *,
        retrieved_at: datetime,
        max_bytes: int,
    ) -> datetime:
        if not isinstance(artifact, RawEvidenceArtifact):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        if not isinstance(artifact.content, bytes) or len(artifact.content) > max_bytes:
            raise InvalidEvidenceRequestError("evidence.provider.output-limited")
        if (
            not isinstance(artifact.media_type, str)
            or len(artifact.media_type) > 128
            or not _MEDIA_TYPE.fullmatch(artifact.media_type)
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        self._validate_safe_text(artifact.summary, minimum=1, maximum=4096)
        observed_at = self._parse_time(artifact.observed_at)
        if observed_at > retrieved_at:
            raise InvalidEvidenceRequestError("evidence.time.invalid")
        return observed_at

    @staticmethod
    def _validate_safe_text(value: object, *, minimum: int, maximum: int) -> None:
        if not isinstance(value, str) or not minimum <= len(value) <= maximum:
            raise InvalidEvidenceRequestError("evidence.request.invalid")
        if any(ord(character) < 32 for character in value):
            raise InvalidEvidenceRequestError("evidence.request.invalid")
        if _SENSITIVE_TEXT.search(value):
            raise InvalidEvidenceRequestError("evidence.request.contains-secret")

    def _now(self) -> datetime:
        try:
            return self._parse_time(self._clock.now())
        except InvalidEvidenceRequestError:
            raise InvalidEvidenceRequestError("evidence.clock.invalid") from None

    @staticmethod
    def _parse_time(value: object) -> datetime:
        if not isinstance(value, str):
            raise InvalidEvidenceRequestError("evidence.time.invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidEvidenceRequestError("evidence.time.invalid") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise InvalidEvidenceRequestError("evidence.time.invalid")
        return parsed

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _enforce_deadline(now: datetime, deadline: datetime) -> None:
        if now > deadline:
            raise EvidenceDeadlineExceededError("evidence.deadline.exceeded")
