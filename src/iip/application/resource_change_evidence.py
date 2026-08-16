"""Tenant-scoped resource-change evidence derived from immutable observations."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    RawEvidenceArtifact,
    ResourceObservationRecord,
    ResourceRepository,
)


MAX_QUERY_RANGE = timedelta(days=7)
MAX_DEADLINE_OFFSET = timedelta(minutes=5)
_REQUEST_ID = re.compile(r"ceq_[a-f0-9]{32}")
_TENANT_ID = re.compile(r"[a-zA-Z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_CHANGE_KINDS = frozenset(
    {
        "created",
        "configuration",
        "image",
        "scale",
        "relationships",
        "status",
        "deleted",
    }
)


class InvalidResourceChangeEvidenceRequestError(ValueError):
    """Public resource-change request violated a stable boundary."""


@dataclass(frozen=True)
class CollectResourceChangeEvidenceCommand:
    """Authenticated request to collect normalized resource changes."""

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


class ResourceChangeEvidenceService:
    """Validate a public change query and commit its normalized artifact."""

    def __init__(self, evidence: EvidenceCollectionService, clock: Clock) -> None:
        self._evidence = evidence
        self._clock = clock

    def execute(
        self, command: CollectResourceChangeEvidenceCommand
    ) -> Mapping[str, object]:
        request, metadata, spec, time_range, query, limits = self._validate(command)
        encoded_query = json.dumps(
            {
                "requestDigest": canonical_digest(request),
                "requestId": metadata["requestId"],
                "timeRange": time_range,
                "query": query,
                "limits": limits,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(encoded_query) > 4096:
            raise self._invalid()
        return self._evidence.execute(
            CollectEvidenceCommand(
                actor=command.actor,
                provider="resource-history",
                integration_id=spec["integrationId"],
                evidence_type="resource.change",
                resource_uids=tuple(spec["resourceRefs"]),
                locator="resource://history/changes/v1",
                query=encoded_query,
                deadline=spec["deadline"],
                max_bytes=limits["maxBytes"],
                sensitivity="internal",
                retention_class="ephemeral",
            )
        )

    def _validate(
        self, command: CollectResourceChangeEvidenceCommand
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
                or request["kind"] != "ResourceChangeEvidenceRequest"
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
            or not self._safe_text(metadata.get("actorId"), 256)
            or not isinstance(spec.get("integrationId"), str)
            or not _INTEGRATION_ID.fullmatch(spec["integrationId"])
        ):
            raise self._invalid()
        resource_refs = spec.get("resourceRefs")
        if (
            not isinstance(resource_refs, list)
            or not 1 <= len(resource_refs) <= 32
            or len(resource_refs) != len(set(resource_refs))
            or any(
                not isinstance(item, str) or not _RESOURCE_UID.fullmatch(item)
                for item in resource_refs
            )
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
        cls, query_value: object, limits_value: object
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            query = dict(query_value)  # type: ignore[arg-type]
            limits = dict(limits_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise cls._invalid() from None
        kinds = query.get("changeKinds")
        if (
            set(query) != {"changeKinds"}
            or not isinstance(kinds, list)
            or len(kinds) > len(_CHANGE_KINDS)
            or len(kinds) != len(set(kinds))
            or any(item not in _CHANGE_KINDS for item in kinds)
            or set(limits) != {
                "maxChanges",
                "maxObservationsPerResource",
                "maxBytes",
            }
            or not cls._integer(limits.get("maxChanges"), 1, 500)
            or not cls._integer(limits.get("maxObservationsPerResource"), 2, 1000)
            or not cls._integer(limits.get("maxBytes"), 1, 16_777_216)
        ):
            raise cls._invalid()
        return query, limits

    @staticmethod
    def _integer(value: object, minimum: int, maximum: int) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and minimum <= value <= maximum
        )

    @staticmethod
    def _safe_text(value: object, maximum: int) -> bool:
        return bool(
            isinstance(value, str)
            and 1 <= len(value) <= maximum
            and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        )

    @staticmethod
    def _parse_time(value: object) -> datetime:
        if not isinstance(value, str):
            raise ResourceChangeEvidenceService._invalid()
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ResourceChangeEvidenceService._invalid() from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ResourceChangeEvidenceService._invalid()
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _invalid() -> InvalidResourceChangeEvidenceRequestError:
        return InvalidResourceChangeEvidenceRequestError("change.request.invalid")


class ResourceHistoryChangeEvidenceProvider:
    """Derive value-minimized change records from accepted observation history."""

    def __init__(self, resources: ResourceRepository, clock: Clock) -> None:
        self._resources = resources
        self._clock = clock

    def fetch(self, request: EvidenceProviderRequest) -> RawEvidenceArtifact:
        payload, time_range, query, limits = self._provider_query(request)
        start = ResourceChangeEvidenceService._parse_time(time_range["start"])
        end = ResourceChangeEvidenceService._parse_time(time_range["end"])
        allowed_kinds = set(query["changeKinds"]) or set(_CHANGE_KINDS)
        changes: list[dict[str, object]] = []
        warnings: set[str] = set()
        change_limit_reached = False
        created_at = self._clock.now()
        executed = ResourceChangeEvidenceService._parse_time(created_at)
        for uid in request.resource_uids:
            records = tuple(
                self._resources.history(
                    request.tenant_id,
                    uid,
                    limit=limits["maxObservationsPerResource"] + 1,
                )
            )
            if len(records) > limits["maxObservationsPerResource"]:
                warnings.add("observation-limit")
                records = records[: limits["maxObservationsPerResource"]]
            if any(
                record.resource.identity.tenant_id != request.tenant_id
                or record.resource.identity.uid != uid
                or not re.fullmatch(r"[a-f0-9]{64}", record.observation_hash)
                for record in records
            ):
                raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
            accepted = sorted(
                (record for record in records if record.disposition.value == "accepted"),
                key=lambda record: (
                    ResourceChangeEvidenceService._parse_time(record.resource.observed_at),
                    record.offset,
                ),
            )
            prior: ResourceObservationRecord | None = None
            for record in accepted:
                observed = ResourceChangeEvidenceService._parse_time(
                    record.resource.observed_at
                )
                recorded = ResourceChangeEvidenceService._parse_time(record.recorded_at)
                if not observed <= recorded <= executed:
                    raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
                if observed < start:
                    prior = record
                    continue
                if observed > end:
                    continue
                derived = self._derive_changes(uid, prior, record, allowed_kinds)
                remaining = limits["maxChanges"] + 1 - len(changes)
                changes.extend(derived[:remaining])
                prior = record
                if len(changes) > limits["maxChanges"]:
                    warnings.add("change-limit")
                    change_limit_reached = True
                    break
            if change_limit_reached:
                break
        changes.sort(
            key=lambda item: (
                str(item["observedAt"]),
                str(item["resourceRef"]),
                str(item["kind"]),
                str(item["id"]),
            )
        )
        changes = changes[: limits["maxChanges"]]
        counts: dict[str, int] = {}
        for item in changes:
            kind = str(item["kind"])
            counts[kind] = counts.get(kind, 0) + 1
        status = "partial" if warnings else "complete" if changes else "no-data"
        document = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ResourceChangeEvidenceResult",
            "metadata": {
                "requestId": payload["requestId"],
                "tenantId": request.tenant_id,
                "integrationId": request.integration_id,
                "createdAt": created_at,
            },
            "spec": {
                "requestDigest": payload["requestDigest"],
                "timeRange": time_range,
                "status": status,
                "changes": changes,
                "summary": {
                    "changeCount": len(changes),
                    "affectedResourceCount": len(
                        {item["resourceRef"] for item in changes}
                    ),
                    "countsByKind": dict(sorted(counts.items())),
                },
                "warnings": sorted(warnings),
            },
        }
        content = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(content) > limits["maxBytes"]:
            raise InvalidEvidenceRequestError("evidence.provider.output-limited")
        summary = (
            "No resource changes matched the bounded history query."
            if status == "no-data"
            else f"Normalized {len(changes)} resource change(s)."
        )
        return RawEvidenceArtifact(
            content=content,
            media_type="application/json",
            observed_at=time_range["end"],
            summary=summary,
        )

    @staticmethod
    def _provider_query(
        request: EvidenceProviderRequest,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        if (
            request.evidence_type != "resource.change"
            or request.locator != "resource://history/changes/v1"
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
            if (
                not isinstance(payload["requestDigest"], str)
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", payload["requestDigest"])
                or not isinstance(payload["requestId"], str)
                or not _REQUEST_ID.fullmatch(payload["requestId"])
            ):
                raise TypeError
            time_range = dict(payload["timeRange"])
            if set(time_range) != {"start", "end"}:
                raise TypeError
            ResourceChangeEvidenceService._parse_time(time_range["start"])
            ResourceChangeEvidenceService._parse_time(time_range["end"])
            query, limits = ResourceChangeEvidenceService.validate_query_contract(
                payload["query"], payload["limits"]
            )
            if limits["maxBytes"] != request.max_bytes:
                raise TypeError
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            InvalidResourceChangeEvidenceRequestError,
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid") from None
        return payload, time_range, query, limits

    @classmethod
    def _derive_changes(
        cls,
        uid: str,
        before: ResourceObservationRecord | None,
        after: ResourceObservationRecord,
        allowed_kinds: set[str],
    ) -> list[dict[str, object]]:
        if before is None:
            return (
                [cls._change(uid, "created", ("/",), before, after)]
                if "created" in allowed_kinds
                else []
            )
        before_document = cls._comparable(before.resource.to_dict())
        after_document = cls._comparable(after.resource.to_dict())
        paths = tuple(sorted(cls._changed_paths(before_document, after_document)))
        if not paths:
            return []
        lifecycle = after_document.get("status")
        if (
            isinstance(lifecycle, dict)
            and lifecycle.get("lifecycle") == "deleted"
        ):
            return (
                [cls._change(uid, "deleted", paths, before, after)]
                if "deleted" in allowed_kinds
                else []
            )
        grouped: dict[str, list[str]] = {}
        for path in paths:
            kind = cls._kind(path)
            if kind in allowed_kinds:
                grouped.setdefault(kind, []).append(path)
        return [
            cls._change(uid, kind, tuple(grouped[kind]), before, after)
            for kind in sorted(grouped)
        ]

    @staticmethod
    def _comparable(document: Mapping[str, Any]) -> dict[str, object]:
        metadata = document.get("metadata", {})
        spec = document.get("spec", {})
        return {
            "metadata": {
                "labels": metadata.get("labels", {})
                if isinstance(metadata, dict)
                else {}
            },
            "spec": spec,
            "status": document.get("status"),
        }

    @classmethod
    def _changed_paths(
        cls, before: object, after: object, prefix: str = ""
    ) -> set[str]:
        if isinstance(before, dict) and isinstance(after, dict):
            paths: set[str] = set()
            for key in sorted(set(before) | set(after)):
                escaped = str(key).replace("~", "~0").replace("/", "~1")
                path = f"{prefix}/{escaped}"
                if key not in before or key not in after:
                    paths.add(path)
                else:
                    paths.update(cls._changed_paths(before[key], after[key], path))
                if len(paths) > 64:
                    return {prefix or "/"}
            return paths
        if before != after:
            return {prefix or "/"}
        return set()

    @staticmethod
    def _kind(path: str) -> str:
        if path.startswith("/status"):
            return "status"
        if path.startswith("/spec/relationships"):
            return "relationships"
        if path == "/spec/attributes/replicas":
            return "scale"
        if path in {
            "/spec/attributes/availableReplicas",
            "/spec/attributes/readyReplicas",
            "/spec/attributes/updatedReplicas",
        }:
            return "status"
        if path.startswith("/spec/attributes") and (
            path.endswith("/image") or "/image/" in path
        ):
            return "image"
        return "configuration"

    @staticmethod
    def _change(
        uid: str,
        kind: str,
        paths: tuple[str, ...],
        before: ResourceObservationRecord | None,
        after: ResourceObservationRecord,
    ) -> dict[str, object]:
        material = "\n".join((uid, kind, after.observation_hash, *paths))
        document = after.resource.to_dict()
        observation = document["metadata"]["observation"]
        source = {
            "sourceId": observation["sourceId"],
            "streamId": observation["streamId"],
            "sequence": observation["sequence"],
        }
        if "resourceVersion" in observation:
            source["resourceVersion"] = observation["resourceVersion"]
        change: dict[str, object] = {
            "id": "chg_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32],
            "resourceRef": uid,
            "kind": kind,
            "observedAt": after.resource.observed_at,
            "recordedAt": after.recorded_at,
            "afterObservationHash": after.observation_hash,
            "changedPaths": list(paths),
            "source": source,
        }
        if before is not None:
            change["beforeObservationHash"] = before.observation_hash
        return change
