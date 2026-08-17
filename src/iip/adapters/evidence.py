"""Reference evidence adapters for local development and boundary tests."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Iterable, Mapping, Optional

from iip.application.ports import (
    ActorContext,
    EvidenceProviderRequest,
    EvidenceRedactionResult,
    EvidenceRetentionState,
    KubernetesEventQuery,
    KubernetesEventsResult,
    PersistenceError,
    RawEvidenceArtifact,
    TelemetryMetricsQuery,
    TelemetryMetricsResult,
    TelemetryLogsQuery,
    TelemetryLogsResult,
)


_SENSITIVE_KEY = re.compile(
    r"(?:authorization|password|passwd|token|secret|apikey|accesskey|privatekey)$",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|password|passwd|token|secret|api[_-]?key|"
    r"access[_-]?key)(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


class InMemoryEvidenceStore:
    """Tenant-scoped atomic metadata/artifact store for the local profile."""

    def __init__(self) -> None:
        self._items: dict[
            tuple[str, str],
            tuple[dict[str, object], bytes | None, str | None],
        ] = {}
        self._retention_audit: list[dict[str, object]] = []
        self._lock = RLock()

    def commit(
        self,
        actor: ActorContext,
        evidence_id: str,
        document: Mapping[str, object],
        decoded_content: bytes,
    ) -> None:
        metadata = document.get("metadata")
        spec = document.get("spec")
        artifact = spec.get("artifact") if isinstance(spec, Mapping) else None
        expected_hash = (
            "sha256:" + hashlib.sha256(decoded_content).hexdigest()
            if isinstance(decoded_content, bytes)
            else None
        )
        if (
            not self._valid_actor(actor)
            or not isinstance(metadata, Mapping)
            or metadata.get("tenantId") != actor.tenant_id
            or metadata.get("id") != evidence_id
            or not isinstance(decoded_content, bytes)
            or not isinstance(artifact, Mapping)
            or artifact.get("contentHash") != expected_hash
            or artifact.get("sizeBytes") != len(decoded_content)
            or artifact.get("storageRef")
            != f"evidence://{actor.tenant_id}/{evidence_id}/artifact"
        ):
            raise PersistenceError("storage.input-invalid")
        key = (actor.tenant_id, evidence_id)
        with self._lock:
            if key in self._items:
                raise PersistenceError("storage.conflict")
            self._items[key] = (
                copy.deepcopy(dict(document)),
                bytes(decoded_content),
                None,
            )

    def get(
        self,
        actor: ActorContext,
        evidence_id: str,
    ) -> Optional[Mapping[str, object]]:
        if not self._valid_actor(actor):
            return None
        with self._lock:
            stored = self._items.get((actor.tenant_id, evidence_id))
            return copy.deepcopy(stored[0]) if stored is not None else None

    def read_artifact(
        self,
        actor: ActorContext,
        evidence_id: str,
    ) -> Optional[bytes]:
        if not self._valid_actor(actor):
            return None
        with self._lock:
            stored = self._items.get((actor.tenant_id, evidence_id))
            return (
                bytes(stored[1])
                if stored is not None and stored[1] is not None
                else None
            )

    def list(
        self,
        actor: ActorContext,
        *,
        resource_uids: tuple[str, ...] = (),
        evidence_types: tuple[str, ...] = (),
        limit: int = 100,
    ) -> Iterable[Mapping[str, object]]:
        if not self._valid_actor(actor) or not 1 <= limit <= 1000:
            return ()
        requested_resources = set(resource_uids)
        requested_types = set(evidence_types)
        with self._lock:
            documents = []
            for (tenant_id, _), (document, _, _) in sorted(self._items.items()):
                if tenant_id != actor.tenant_id:
                    continue
                spec = document.get("spec")
                if not isinstance(spec, Mapping):
                    continue
                refs = spec.get("resourceRefs", [])
                if requested_resources and not requested_resources.intersection(refs):
                    continue
                if requested_types and spec.get("type") not in requested_types:
                    continue
                documents.append(copy.deepcopy(document))
                if len(documents) == limit:
                    break
            return tuple(documents)

    def evaluate_evidence_retention(
        self,
        tenant_id: str,
        evaluated_at: str,
        *,
        ephemeral_seconds: int,
        standard_seconds: int,
        extended_seconds: int,
        limit: int,
        expire: bool,
        policy_digest: str,
    ) -> EvidenceRetentionState:
        if (
            not isinstance(tenant_id, str)
            or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", tenant_id) is None
            or not isinstance(expire, bool)
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in (
                    ephemeral_seconds,
                    standard_seconds,
                    extended_seconds,
                    limit,
                )
            )
            or not (
                3_600 <= ephemeral_seconds <= 2_592_000
                and 86_400 <= standard_seconds <= 31_536_000
                and 86_400 <= extended_seconds <= 315_360_000
                and ephemeral_seconds <= standard_seconds <= extended_seconds
            )
            or not 1 <= limit <= 1000
            or re.fullmatch(r"sha256:[a-f0-9]{64}", policy_digest) is None
        ):
            raise PersistenceError("storage.input-invalid")
        evaluated = self._retention_time(evaluated_at)
        durations = {
            "ephemeral": ephemeral_seconds,
            "standard": standard_seconds,
            "extended": extended_seconds,
        }
        with self._lock:
            stored = 0
            legal_hold = 0
            candidates: list[tuple[datetime, str]] = []
            for (candidate_tenant, evidence_id), (document, artifact, _) in (
                self._items.items()
            ):
                if candidate_tenant != tenant_id or artifact is None:
                    continue
                stored += 1
                expiry = self._effective_retention_expiry(document, durations)
                if expiry is None:
                    legal_hold += 1
                elif expiry <= evaluated:
                    candidates.append((expiry, evidence_id))
            candidates.sort()
            expired = 0
            if expire:
                for _, evidence_id in candidates[:limit]:
                    key = (tenant_id, evidence_id)
                    document, artifact, _ = self._items[key]
                    if artifact is None:
                        continue
                    self._items[key] = (document, None, evaluated_at)
                    expired += 1
            audit_ref = None
            if expired:
                encoded = (
                    f"{tenant_id}:{evaluated_at}:{policy_digest}:{expired}"
                ).encode()
                digest = hashlib.sha256(encoded).hexdigest()[:32]
                audit_ref = f"audit://{tenant_id}/evidence-retention/{digest}"
                self._retention_audit.append(
                    {
                        "tenantId": tenant_id,
                        "evaluatedAt": evaluated_at,
                        "policyDigest": policy_digest,
                        "expiredArtifacts": expired,
                        "remainingEligibleArtifacts": len(candidates) - expired,
                    }
                )
            return EvidenceRetentionState(
                tenant_id=tenant_id,
                evaluated_at=evaluated_at,
                stored_artifacts=stored,
                eligible_artifacts=len(candidates),
                expired_artifacts=expired,
                remaining_eligible_artifacts=len(candidates) - expired,
                legal_hold_artifacts=legal_hold,
                audit_ref=audit_ref,
            )

    @classmethod
    def _effective_retention_expiry(
        cls,
        document: Mapping[str, object],
        durations: Mapping[str, int],
    ) -> datetime | None:
        metadata = document.get("metadata")
        spec = document.get("spec")
        handling = spec.get("handling") if isinstance(spec, Mapping) else None
        if not isinstance(metadata, Mapping) or not isinstance(handling, Mapping):
            raise PersistenceError("storage.corrupt")
        retention_class = handling.get("retentionClass")
        if retention_class == "legal-hold":
            return None
        if retention_class not in durations:
            raise PersistenceError("storage.corrupt")
        explicit = handling.get("expiresAt")
        if explicit is not None:
            return cls._retention_time(str(explicit))
        recorded = cls._retention_time(str(metadata.get("recordedAt")))
        return recorded + timedelta(seconds=durations[retention_class])

    @staticmethod
    def _retention_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            raise PersistenceError("storage.corrupt") from None
        if parsed.tzinfo is None:
            raise PersistenceError("storage.corrupt")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _valid_actor(actor: ActorContext) -> bool:
        return bool(
            actor.actor_id
            and actor.actor_id != "anonymous"
            and actor.tenant_id
        )


class UuidEvidenceIdGenerator:
    """Generate opaque Evidence identifiers independent of artifact content."""

    def new_id(self) -> str:
        return f"evd_{uuid.uuid4().hex}"


class SystemClock:
    """UTC wall clock for the local reference runtime."""

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class StaticEvidenceProvider:
    """Deterministic provider whose credential-free locators map to fixtures."""

    def __init__(self, artifacts: Mapping[str, RawEvidenceArtifact]) -> None:
        self._artifacts = dict(artifacts)

    def fetch(self, request: EvidenceProviderRequest) -> RawEvidenceArtifact:
        try:
            return self._artifacts[request.locator]
        except KeyError:
            raise LookupError("evidence.provider.not-found") from None


class ResourceStateEvidenceProvider:
    """Render canonical resource projections as safe, deterministic JSON evidence."""

    def __init__(self, resources: object) -> None:
        self._resources = resources

    def fetch(self, request: EvidenceProviderRequest) -> RawEvidenceArtifact:
        if request.locator != "resource://current":
            raise LookupError("evidence.provider.not-found")
        get_many = getattr(self._resources, "get_many")
        resources = tuple(get_many(request.tenant_id, request.resource_uids))
        if len(resources) != len(request.resource_uids):
            raise LookupError("evidence.provider.not-found")
        documents = [resource.to_dict() for resource in resources]
        content = json.dumps(
            {"resources": documents},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        observed_at = max(resource.observed_at for resource in resources)
        return RawEvidenceArtifact(
            content=content,
            media_type="application/json",
            observed_at=observed_at,
            summary=f"Current canonical status for {len(resources)} scoped resource(s).",
        )


class NoDataTelemetryMetricsBackend:
    """Safe local backend that proves query plumbing without inventing metrics."""

    def __init__(self, clock: object) -> None:
        self._clock = clock

    def query_metrics(self, request: TelemetryMetricsQuery) -> TelemetryMetricsResult:
        del request
        now = getattr(self._clock, "now")()
        return TelemetryMetricsResult(
            executed_at=now,
            status="no-data",
            series=(),
        )


class NoDataTelemetryLogsBackend:
    """Safe local backend that proves log plumbing without inventing records."""

    def __init__(self, clock: object) -> None:
        self._clock = clock

    def query_logs(self, request: TelemetryLogsQuery) -> TelemetryLogsResult:
        del request
        now = getattr(self._clock, "now")()
        return TelemetryLogsResult(
            executed_at=now,
            status="no-data",
            records=(),
        )


class NoDataKubernetesEventsBackend:
    """Safe local backend that proves event plumbing without inventing Events."""

    def __init__(self, clock: object) -> None:
        self._clock = clock

    def query_events(self, request: KubernetesEventQuery) -> KubernetesEventsResult:
        del request
        now = getattr(self._clock, "now")()
        return KubernetesEventsResult(
            executed_at=now,
            status="no-data",
            events=(),
        )


class StructuredTextRedactor:
    """Inspect JSON and UTF-8 text for common credential-bearing patterns."""

    def redact(
        self,
        content: bytes,
        *,
        media_type: str,
        evidence_type: str,
    ) -> EvidenceRedactionResult:
        del evidence_type
        normalized_media_type = media_type.lower().split(";", 1)[0]
        if normalized_media_type == "application/json" or normalized_media_type.endswith(
            "+json"
        ):
            return self._redact_json(content)
        if normalized_media_type.startswith("text/"):
            return self._redact_utf8_text(content)
        raise ValueError("evidence.redaction.unsupported-media-type")

    def _redact_json(self, content: bytes) -> EvidenceRedactionResult:
        try:
            parsed = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("evidence.redaction.invalid-json") from None

        methods: set[str] = set()
        redacted = self._walk_json(parsed, methods)
        if not methods:
            return EvidenceRedactionResult(content=content, methods=())
        encoded = json.dumps(
            redacted,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return EvidenceRedactionResult(content=encoded, methods=tuple(sorted(methods)))

    def _walk_json(self, value: object, methods: set[str]) -> object:
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if _SENSITIVE_KEY.search(normalized_key):
                    result[key] = "[REDACTED]"
                    methods.add("structured-secret-fields")
                else:
                    result[key] = self._walk_json(item, methods)
            return result
        if isinstance(value, list):
            return [self._walk_json(item, methods) for item in value]
        if isinstance(value, str):
            redacted, changed = self._redact_text(value)
            if changed:
                methods.add("secret-pattern")
            return redacted
        return value

    def _redact_utf8_text(self, content: bytes) -> EvidenceRedactionResult:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("evidence.redaction.invalid-utf8") from None
        redacted, changed = self._redact_text(text)
        if not changed:
            return EvidenceRedactionResult(content=content, methods=())
        return EvidenceRedactionResult(
            content=redacted.encode("utf-8"),
            methods=("secret-pattern",),
        )

    @staticmethod
    def _redact_text(value: str) -> tuple[str, bool]:
        redacted = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", value)
        redacted = _BEARER.sub("Bearer [REDACTED]", redacted)
        redacted = _SECRET_ASSIGNMENT.sub(
            lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
            redacted,
        )
        return redacted, redacted != value
