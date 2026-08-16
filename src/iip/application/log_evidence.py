"""Backend-neutral log evidence query and normalization boundary."""

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
    RawEvidenceArtifact,
    TelemetryLogRecord,
    TelemetryLogsBackend,
    TelemetryLogsQuery,
    TelemetryLogsResult,
)


MAX_QUERY_RANGE = timedelta(days=7)
MAX_DEADLINE_OFFSET = timedelta(minutes=5)
_REQUEST_ID = re.compile(r"leq_[a-f0-9]{32}")
_TENANT_ID = re.compile(r"[a-zA-Z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_RECORD_ID = re.compile(r"log_[a-f0-9]{32}")
_SERVICE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{0,127}")
_ATTRIBUTE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]{0,127}")
_TRACE_ID = re.compile(r"[a-f0-9]{32}")
_SPAN_ID = re.compile(r"[a-f0-9]{16}")
_SEVERITIES = frozenset(
    {"trace", "debug", "info", "warn", "error", "fatal", "unspecified"}
)
_STATUSES = frozenset({"complete", "partial", "no-data"})
_WARNINGS = frozenset({"backend-partial", "record-limit"})
_FILTER_OPERATORS = frozenset({"eq", "neq"})
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:authorization|password|passwd|token|secret|api[_-]?key|"
    r"access[_-]?key)\s*[:=]\s*\S+|bearer\s+\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


class InvalidLogEvidenceRequestError(ValueError):
    """Public log evidence request violated a stable boundary."""


@dataclass(frozen=True)
class CollectLogEvidenceCommand:
    """Authenticated request to collect one normalized log artifact."""

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


class LogEvidenceService:
    """Validate a public log query and collect its normalized Evidence artifact."""

    def __init__(self, evidence: EvidenceCollectionService, clock: Clock) -> None:
        self._evidence = evidence
        self._clock = clock

    def execute(self, command: CollectLogEvidenceCommand) -> Mapping[str, object]:
        request, metadata, spec, time_range, query, limits = self._validate(command)
        provider_query = {
            "requestDigest": canonical_digest(request),
            "requestId": metadata["requestId"],
            "signal": "logs",
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
                provider="log-query",
                integration_id=spec["integrationId"],
                evidence_type="telemetry.logs",
                resource_uids=tuple(spec["resourceRefs"]),
                locator="telemetry://logs/query/v1",
                query=encoded_query,
                deadline=spec["deadline"],
                max_bytes=limits["maxBytes"],
                sensitivity="confidential",
                retention_class="ephemeral",
            )
        )

    def _validate(
        self, command: CollectLogEvidenceCommand
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
                or request["kind"] != "LogEvidenceRequest"
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
            "signal",
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
        if (
            not isinstance(spec.get("integrationId"), str)
            or not _INTEGRATION_ID.fullmatch(spec["integrationId"])
            or spec.get("signal") != "logs"
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
        try:
            query = dict(query_value)  # type: ignore[arg-type]
            limits = dict(limits_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise cls._invalid() from None
        if set(query) != {"serviceNames", "severities", "filters"}:
            raise cls._invalid()
        services = query.get("serviceNames")
        severities = query.get("severities")
        filters = query.get("filters")
        if (
            not isinstance(services, list)
            or not 1 <= len(services) <= 16
            or any(not isinstance(item, str) or not _SERVICE_NAME.fullmatch(item) for item in services)
            or len(services) != len(set(services))
            or not isinstance(severities, list)
            or len(severities) > len(_SEVERITIES)
            or any(item not in _SEVERITIES for item in severities)
            or len(severities) != len(set(severities))
            or not isinstance(filters, list)
            or len(filters) > 8
        ):
            raise cls._invalid()
        normalized_filters: list[dict[str, str]] = []
        for item in filters:
            if not isinstance(item, Mapping) or set(item) != {
                "attribute",
                "operator",
                "value",
            }:
                raise cls._invalid()
            attribute = item.get("attribute")
            operator = item.get("operator")
            value = item.get("value")
            if (
                not isinstance(attribute, str)
                or not _ATTRIBUTE.fullmatch(attribute)
                or operator not in _FILTER_OPERATORS
                or not cls._safe_text(value, maximum=128)
                or _SENSITIVE_TEXT.search(value)
            ):
                raise cls._invalid()
            normalized_filters.append(dict(item))
        query["filters"] = normalized_filters
        if set(limits) != {"maxRecords", "maxBytes"} or not all(
            (
                cls._integer(limits.get("maxRecords"), minimum=1, maximum=1000),
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
    def _safe_text(
        value: object,
        *,
        maximum: int,
        allow_formatting: bool = False,
    ) -> bool:
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            return False
        allowed = {9, 10, 13} if allow_formatting else set()
        return not any(
            (ord(character) < 32 and ord(character) not in allowed)
            or ord(character) == 127
            for character in value
        )

    @staticmethod
    def _parse_time(value: object) -> datetime:
        if not isinstance(value, str):
            raise InvalidLogEvidenceRequestError("logs.request.invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidLogEvidenceRequestError("logs.request.invalid") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise InvalidLogEvidenceRequestError("logs.request.invalid")
        return parsed

    @staticmethod
    def _invalid() -> InvalidLogEvidenceRequestError:
        return InvalidLogEvidenceRequestError("logs.request.invalid")


class TelemetryLogsEvidenceProvider:
    """Validate backend output and render a canonical public JSON artifact."""

    def __init__(self, backend: TelemetryLogsBackend) -> None:
        self._backend = backend

    def fetch(self, request: EvidenceProviderRequest) -> RawEvidenceArtifact:
        query, request_digest = self._provider_query(request)
        result = self._backend.query_logs(query)
        records = self._validated_result(query, result)
        error_count = sum(record["severity"] in ("error", "fatal") for record in records)
        document = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "LogEvidenceResult",
            "metadata": {
                "requestId": query.request_id,
                "tenantId": query.tenant_id,
                "integrationId": query.integration_id,
                "createdAt": result.executed_at,
            },
            "spec": {
                "signal": "logs",
                "requestDigest": request_digest,
                "timeRange": {"start": query.start, "end": query.end},
                "status": result.status,
                "records": records,
                "summary": {
                    "recordCount": len(records),
                    "errorCount": error_count,
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
            "No log records matched the bounded query."
            if result.status == "no-data"
            else f"Normalized {len(records)} log record(s)."
        )
        return RawEvidenceArtifact(
            content=content,
            media_type="application/json",
            observed_at=query.end,
            summary=summary,
        )

    def _provider_query(
        self, request: EvidenceProviderRequest
    ) -> tuple[TelemetryLogsQuery, str]:
        if (
            request.evidence_type != "telemetry.logs"
            or request.locator != "telemetry://logs/query/v1"
            or request.query is None
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        try:
            payload = json.loads(request.query)
            if not isinstance(payload, dict) or set(payload) != {
                "requestDigest",
                "requestId",
                "signal",
                "timeRange",
                "query",
                "limits",
            }:
                raise KeyError
            if payload["signal"] != "logs":
                raise KeyError
            time_range = dict(payload["timeRange"])
            query_fragment, limits = LogEvidenceService.validate_query_contract(
                payload["query"], payload["limits"]
            )
            request_digest = payload["requestDigest"]
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            InvalidLogEvidenceRequestError,
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid") from None
        if (
            not isinstance(request_digest, str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", request_digest)
            or not isinstance(payload.get("requestId"), str)
            or not _REQUEST_ID.fullmatch(payload["requestId"])
            or set(time_range) != {"start", "end"}
            or limits["maxBytes"] != request.max_bytes
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        return TelemetryLogsQuery(
            tenant_id=request.tenant_id,
            actor_id=request.actor_id,
            request_id=payload["requestId"],
            integration_id=request.integration_id,
            resource_uids=request.resource_uids,
            start=time_range["start"],
            end=time_range["end"],
            service_names=tuple(query_fragment["serviceNames"]),
            severities=tuple(query_fragment["severities"]),
            filters=tuple(
                (item["attribute"], item["operator"], item["value"])
                for item in query_fragment["filters"]
            ),
            max_records=limits["maxRecords"],
            max_bytes=limits["maxBytes"],
            deadline=request.deadline,
        ), request_digest

    def _validated_result(
        self,
        query: TelemetryLogsQuery,
        result: object,
    ) -> list[dict[str, object]]:
        if not isinstance(result, TelemetryLogsResult):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        executed_at = LogEvidenceService._parse_time(result.executed_at)
        end = LogEvidenceService._parse_time(query.end)
        deadline = LogEvidenceService._parse_time(query.deadline)
        if not end <= executed_at <= deadline or result.status not in _STATUSES:
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        if (
            not isinstance(result.records, tuple)
            or len(result.records) > query.max_records
            or not isinstance(result.warnings, tuple)
            or len(result.warnings) > 8
            or len(set(result.warnings)) != len(result.warnings)
            or any(warning not in _WARNINGS for warning in result.warnings)
            or (result.status == "complete" and (not result.records or result.warnings))
            or (result.status == "partial" and (not result.records or not result.warnings))
            or (result.status == "no-data" and (result.records or result.warnings))
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")

        start = LogEvidenceService._parse_time(query.start)
        scoped_uids = set(query.resource_uids)
        record_ids: set[str] = set()
        rendered: list[dict[str, object]] = []
        for record in result.records:
            item = self._validated_record(
                query, record, start, end, executed_at, scoped_uids, record_ids
            )
            rendered.append(item)
            record_ids.add(record.record_id)
        rendered.sort(key=lambda item: (str(item["timestamp"]), str(item["id"])))
        return rendered

    def _validated_record(
        self,
        query: TelemetryLogsQuery,
        record: object,
        start: datetime,
        end: datetime,
        executed_at: datetime,
        scoped_uids: set[str],
        record_ids: set[str],
    ) -> dict[str, object]:
        if not isinstance(record, TelemetryLogRecord):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        timestamp = LogEvidenceService._parse_time(record.timestamp)
        observed = (
            LogEvidenceService._parse_time(record.observed_timestamp)
            if record.observed_timestamp is not None
            else None
        )
        if (
            not isinstance(record.record_id, str)
            or not _RECORD_ID.fullmatch(record.record_id)
            or record.record_id in record_ids
            or record.resource_uid not in scoped_uids
            or not start <= timestamp <= end
            or (observed is not None and not timestamp <= observed <= executed_at)
            or record.severity not in _SEVERITIES
            or (query.severities and record.severity not in query.severities)
            or not isinstance(record.service_name, str)
            or not _SERVICE_NAME.fullmatch(record.service_name)
            or record.service_name not in query.service_names
            or not LogEvidenceService._safe_text(
                record.body, maximum=4096, allow_formatting=True
            )
            or (record.trace_id is None) != (record.span_id is None)
            or (record.trace_id is not None and not _TRACE_ID.fullmatch(record.trace_id))
            or (record.span_id is not None and not _SPAN_ID.fullmatch(record.span_id))
            or not isinstance(record.attributes, tuple)
            or len(record.attributes) > 16
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")

        attributes: dict[str, str] = {}
        for pair in record.attributes:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
            name, value = pair
            if (
                not isinstance(name, str)
                or not _ATTRIBUTE.fullmatch(name)
                or name in attributes
                or not LogEvidenceService._safe_text(value, maximum=256)
                or _SENSITIVE_TEXT.search(value)
            ):
                raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
            attributes[name] = value
        if not self._matches_filters(attributes, query.filters):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")

        item: dict[str, object] = {
            "id": record.record_id,
            "resourceRef": record.resource_uid,
            "timestamp": record.timestamp,
            "severity": record.severity,
            "serviceName": record.service_name,
            "body": record.body,
            "attributes": attributes,
        }
        if record.observed_timestamp is not None:
            item["observedTimestamp"] = record.observed_timestamp
        if record.trace_id is not None:
            item["traceId"] = record.trace_id
            item["spanId"] = record.span_id
        return item

    @staticmethod
    def _matches_filters(
        attributes: Mapping[str, str],
        filters: tuple[tuple[str, str, str], ...],
    ) -> bool:
        for name, operator, expected in filters:
            actual = attributes.get(name)
            if operator == "eq" and actual != expected:
                return False
            if operator == "neq" and (actual is None or actual == expected):
                return False
        return True
