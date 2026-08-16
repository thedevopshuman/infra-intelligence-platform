"""Provider-neutral Kubernetes Event evidence collection and normalization."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

from iip.application.collect_evidence import (
    CollectEvidenceCommand,
    EvidenceCollectionService,
    InvalidEvidenceRequestError,
)
from iip.application.ports import (
    ActorContext,
    Clock,
    EvidenceProviderRequest,
    KubernetesEventQuery,
    KubernetesEventRecord,
    KubernetesEventResourceRef,
    KubernetesEventsBackend,
    KubernetesEventsResult,
    RawEvidenceArtifact,
    ResourceRepository,
)


MAX_QUERY_RANGE = timedelta(days=7)
MAX_DEADLINE_OFFSET = timedelta(minutes=5)
_REQUEST_ID = re.compile(r"keq_[a-f0-9]{32}")
_TENANT_ID = re.compile(r"[a-zA-Z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_EVENT_ID = re.compile(r"kve_[a-f0-9]{32}")
_REASON = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}")
_CONDITION = re.compile(r"[a-z][a-z0-9._/-]{2,127}")
_SEVERITIES = frozenset({"normal", "warning"})
_STATUSES = frozenset({"complete", "partial", "no-data"})
_WARNINGS = frozenset({"backend-partial", "event-limit"})


class InvalidKubernetesEventEvidenceRequestError(ValueError):
    """Public Kubernetes Event evidence request violated a stable boundary."""


@dataclass(frozen=True)
class CollectKubernetesEventEvidenceCommand:
    """Authenticated request to collect one normalized event artifact."""

    actor: ActorContext
    request: Mapping[str, Any]


def canonical_digest(document: object) -> str:
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class KubernetesEventEvidenceService:
    """Validate a public query and collect its normalized Evidence artifact."""

    def __init__(self, evidence: EvidenceCollectionService, clock: Clock) -> None:
        self._evidence = evidence
        self._clock = clock

    def execute(
        self, command: CollectKubernetesEventEvidenceCommand
    ) -> Mapping[str, object]:
        request, metadata, spec, time_range, query, limits = self._validate(command)
        provider_query = {
            "requestDigest": canonical_digest(request),
            "requestId": metadata["requestId"],
            "timeRange": time_range,
            "query": query,
            "limits": limits,
        }
        encoded_query = json.dumps(
            provider_query,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(encoded_query) > 4096:
            raise self._invalid()
        return self._evidence.execute(
            CollectEvidenceCommand(
                actor=command.actor,
                provider="kubernetes-events",
                integration_id=spec["integrationId"],
                evidence_type="kubernetes.event",
                resource_uids=tuple(spec["resourceRefs"]),
                locator="kubernetes://events/query/v1",
                query=encoded_query,
                deadline=spec["deadline"],
                max_bytes=limits["maxBytes"],
                sensitivity="internal",
                retention_class="ephemeral",
            )
        )

    def _validate(
        self, command: CollectKubernetesEventEvidenceCommand
    ) -> tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
    ]:
        try:
            request = dict(command.request)
            if set(request) != {"apiVersion", "kind", "metadata", "spec"}:
                raise KeyError
            if (
                request["apiVersion"] != "iip.platform/v1alpha1"
                or request["kind"] != "KubernetesEventEvidenceRequest"
            ):
                raise KeyError
            metadata = dict(request["metadata"])
            spec = dict(request["spec"])
            time_range = dict(spec["timeRange"])
        except (KeyError, TypeError, ValueError):
            raise self._invalid() from None

        if set(metadata) != {"requestId", "tenantId", "actorId", "requestedAt"}:
            raise self._invalid()
        if set(spec) != {
            "integrationId",
            "resourceRefs",
            "timeRange",
            "query",
            "limits",
            "deadline",
        }:
            raise self._invalid()
        if (
            metadata.get("tenantId") != command.actor.tenant_id
            or metadata.get("actorId") != command.actor.actor_id
            or not isinstance(metadata.get("requestId"), str)
            or not _REQUEST_ID.fullmatch(metadata["requestId"])
            or not isinstance(metadata.get("tenantId"), str)
            or not _TENANT_ID.fullmatch(metadata["tenantId"])
            or not self._safe_text(metadata.get("actorId"), maximum=256)
        ):
            raise self._invalid()
        if not isinstance(spec.get("integrationId"), str) or not _INTEGRATION_ID.fullmatch(
            spec["integrationId"]
        ):
            raise self._invalid()

        resource_refs = spec.get("resourceRefs")
        if (
            not isinstance(resource_refs, list)
            or not 1 <= len(resource_refs) <= 256
            or any(
                not isinstance(uid, str) or not _RESOURCE_UID.fullmatch(uid)
                for uid in resource_refs
            )
            or len(resource_refs) != len(set(resource_refs))
        ):
            raise self._invalid()

        requested_at = self._parse_time(metadata.get("requestedAt"))
        now = self._parse_time(self._clock.now())
        start = self._parse_time(time_range.get("start"))
        end = self._parse_time(time_range.get("end"))
        deadline = self._parse_time(spec.get("deadline"))
        if set(time_range) != {"start", "end"} or not (
            start < end <= requested_at <= now <= deadline
        ):
            raise self._invalid()
        if end - start > MAX_QUERY_RANGE or deadline - requested_at > MAX_DEADLINE_OFFSET:
            raise self._invalid()

        query, limits = self.validate_query_contract(spec.get("query"), spec.get("limits"))
        return request, metadata, spec, time_range, query, limits

    @classmethod
    def validate_query_contract(
        cls,
        query_value: object,
        limits_value: object,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Validate query fragments also embedded in investigation selections."""

        try:
            query = dict(query_value)  # type: ignore[arg-type]
            limits = dict(limits_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise cls._invalid() from None
        if set(query) != {"severities", "reasons"}:
            raise cls._invalid()
        severities = query.get("severities")
        reasons = query.get("reasons")
        if (
            not isinstance(severities, list)
            or len(severities) > 2
            or any(item not in _SEVERITIES for item in severities)
            or len(severities) != len(set(severities))
            or not isinstance(reasons, list)
            or len(reasons) > 16
            or any(not isinstance(item, str) or not _REASON.fullmatch(item) for item in reasons)
            or len(reasons) != len(set(reasons))
        ):
            raise cls._invalid()
        if set(limits) != {"maxEvents", "maxBytes"} or not all(
            (
                cls._integer(limits.get("maxEvents"), minimum=1, maximum=1000),
                cls._integer(limits.get("maxBytes"), minimum=1, maximum=16_777_216),
            )
        ):
            raise cls._invalid()
        return query, limits

    @staticmethod
    def _integer(value: object, *, minimum: int, maximum: int) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and minimum <= value <= maximum
        )

    @staticmethod
    def _safe_text(value: object, *, maximum: int, allow_newlines: bool = False) -> bool:
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            return False
        allowed = {10, 13} if allow_newlines else set()
        return not any(
            (ord(character) < 32 and ord(character) not in allowed)
            or ord(character) == 127
            for character in value
        )

    @staticmethod
    def _parse_time(value: object) -> datetime:
        if not isinstance(value, str):
            raise InvalidKubernetesEventEvidenceRequestError(
                "kubernetes.event.request.invalid"
            )
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidKubernetesEventEvidenceRequestError(
                "kubernetes.event.request.invalid"
            ) from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise InvalidKubernetesEventEvidenceRequestError(
                "kubernetes.event.request.invalid"
            )
        return parsed

    @staticmethod
    def _invalid() -> InvalidKubernetesEventEvidenceRequestError:
        return InvalidKubernetesEventEvidenceRequestError(
            "kubernetes.event.request.invalid"
        )


class KubernetesEventsEvidenceProvider:
    """Validate backend output and render a canonical public JSON artifact."""

    def __init__(
        self,
        backend: KubernetesEventsBackend,
        resources: ResourceRepository,
    ) -> None:
        self._backend = backend
        self._resources = resources

    def fetch(self, request: EvidenceProviderRequest) -> RawEvidenceArtifact:
        query, request_digest = self._provider_query(request)
        result = self._backend.query_events(query)
        events = self._validated_result(query, result)
        warning_count = sum(event["severity"] == "warning" for event in events)
        document = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "KubernetesEventEvidenceResult",
            "metadata": {
                "requestId": query.request_id,
                "tenantId": query.tenant_id,
                "integrationId": query.integration_id,
                "createdAt": result.executed_at,
            },
            "spec": {
                "requestDigest": request_digest,
                "timeRange": {"start": query.start, "end": query.end},
                "status": result.status,
                "events": events,
                "summary": {
                    "eventCount": len(events),
                    "warningEventCount": warning_count,
                },
                "warnings": list(result.warnings),
            },
        }
        content = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(content) > query.max_bytes:
            raise InvalidEvidenceRequestError("evidence.provider.output-limited")
        summary = (
            "No Kubernetes Events matched the bounded query."
            if result.status == "no-data"
            else f"Normalized {len(events)} Kubernetes Event(s)."
        )
        return RawEvidenceArtifact(
            content=content,
            media_type="application/json",
            observed_at=query.end,
            summary=summary,
        )

    def _provider_query(
        self, request: EvidenceProviderRequest
    ) -> tuple[KubernetesEventQuery, str]:
        if (
            request.evidence_type != "kubernetes.event"
            or request.locator != "kubernetes://events/query/v1"
            or request.query is None
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        try:
            payload = json.loads(request.query)
            if not isinstance(payload, dict) or set(payload) != {
                "requestDigest",
                "requestId",
                "timeRange",
                "query",
                "limits",
            }:
                raise KeyError
            time_range = dict(payload["timeRange"])
            query_fragment, limits = KubernetesEventEvidenceService.validate_query_contract(
                payload["query"], payload["limits"]
            )
            request_digest = payload["requestDigest"]
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            InvalidKubernetesEventEvidenceRequestError,
        ):
            raise InvalidEvidenceRequestError(
                "evidence.provider.output-invalid"
            ) from None
        if (
            not isinstance(request_digest, str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", request_digest)
            or not isinstance(payload.get("requestId"), str)
            or not _REQUEST_ID.fullmatch(payload["requestId"])
            or set(time_range) != {"start", "end"}
            or limits["maxBytes"] != request.max_bytes
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")

        resources = tuple(
            self._resources.get_many(request.tenant_id, request.resource_uids)
        )
        if (
            len(resources) != len(request.resource_uids)
            or {resource.identity.uid for resource in resources}
            != set(request.resource_uids)
        ):
            raise InvalidEvidenceRequestError("evidence.resource.unavailable")
        refs: list[KubernetesEventResourceRef] = []
        by_uid = {resource.identity.uid: resource for resource in resources}
        for uid in request.resource_uids:
            resource = by_uid[uid]
            identity = resource.identity
            if (
                identity.provider != "kubernetes"
                or not self._safe_identity(identity.resource_type)
                or not self._safe_identity(identity.external_id)
            ):
                raise InvalidEvidenceRequestError("evidence.resource.unavailable")
            refs.append(
                KubernetesEventResourceRef(
                    platform_uid=uid,
                    provider=identity.provider,
                    resource_type=identity.resource_type,
                    external_id=identity.external_id,
                )
            )
        return KubernetesEventQuery(
            tenant_id=request.tenant_id,
            actor_id=request.actor_id,
            request_id=payload["requestId"],
            integration_id=request.integration_id,
            resources=tuple(refs),
            start=time_range["start"],
            end=time_range["end"],
            severities=tuple(query_fragment["severities"]),
            reasons=tuple(query_fragment["reasons"]),
            max_events=limits["maxEvents"],
            max_bytes=limits["maxBytes"],
            deadline=request.deadline,
        ), request_digest

    @staticmethod
    def _safe_identity(value: object) -> bool:
        return bool(
            isinstance(value, str)
            and 1 <= len(value) <= 1024
            and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        )

    def _validated_result(
        self,
        query: KubernetesEventQuery,
        result: object,
    ) -> list[dict[str, object]]:
        if not isinstance(result, KubernetesEventsResult):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        executed_at = KubernetesEventEvidenceService._parse_time(result.executed_at)
        end = KubernetesEventEvidenceService._parse_time(query.end)
        deadline = KubernetesEventEvidenceService._parse_time(query.deadline)
        if not end <= executed_at <= deadline or result.status not in _STATUSES:
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        if (
            not isinstance(result.events, tuple)
            or len(result.events) > query.max_events
            or not isinstance(result.warnings, tuple)
            or len(result.warnings) > 8
            or len(set(result.warnings)) != len(result.warnings)
            or any(warning not in _WARNINGS for warning in result.warnings)
            or (result.status == "complete" and (not result.events or result.warnings))
            or (result.status == "partial" and (not result.events or not result.warnings))
            or (result.status == "no-data" and (result.events or result.warnings))
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")

        start = KubernetesEventEvidenceService._parse_time(query.start)
        scoped_uids = {resource.platform_uid for resource in query.resources}
        event_ids: set[str] = set()
        rendered: list[dict[str, object]] = []
        for event in result.events:
            if not isinstance(event, KubernetesEventRecord):
                raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
            first = KubernetesEventEvidenceService._parse_time(event.first_observed_at)
            last = KubernetesEventEvidenceService._parse_time(event.last_observed_at)
            if (
                not isinstance(event.event_id, str)
                or not _EVENT_ID.fullmatch(event.event_id)
                or event.event_id in event_ids
                or event.resource_uid not in scoped_uids
                or event.severity not in _SEVERITIES
                or (query.severities and event.severity not in query.severities)
                or not isinstance(event.reason, str)
                or not _REASON.fullmatch(event.reason)
                or (query.reasons and event.reason not in query.reasons)
                or not isinstance(event.condition, str)
                or not _CONDITION.fullmatch(event.condition)
                or not start <= first <= last <= end
                or not KubernetesEventEvidenceService._integer(
                    event.occurrence_count, minimum=1, maximum=2_147_483_647
                )
                or (
                    event.reporting_controller is not None
                    and not KubernetesEventEvidenceService._safe_text(
                        event.reporting_controller, maximum=256
                    )
                )
                or (
                    event.message is not None
                    and not KubernetesEventEvidenceService._safe_text(
                        event.message, maximum=2048, allow_newlines=True
                    )
                )
            ):
                raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
            item: dict[str, object] = {
                "id": event.event_id,
                "resourceRef": event.resource_uid,
                "severity": event.severity,
                "reason": event.reason,
                "condition": event.condition,
                "firstObservedAt": event.first_observed_at,
                "lastObservedAt": event.last_observed_at,
                "occurrenceCount": event.occurrence_count,
            }
            if event.reporting_controller is not None:
                item["reportingController"] = event.reporting_controller
            if event.message is not None:
                item["message"] = event.message
            rendered.append(item)
            event_ids.add(event.event_id)
        rendered.sort(key=lambda item: (str(item["lastObservedAt"]), str(item["id"])))
        return rendered
