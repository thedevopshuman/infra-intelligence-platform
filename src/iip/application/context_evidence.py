"""Tenant-scoped repository and runbook context evidence boundary."""

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
    ContextDocumentQuery,
    ContextDocumentsBackend,
    EvidenceProviderRequest,
    EvidenceRedactor,
    RawEvidenceArtifact,
)


MAX_DEADLINE_OFFSET = timedelta(minutes=5)
_REQUEST_ID = re.compile(r"ctq_[a-f0-9]{32}")
_TENANT_ID = re.compile(r"[a-zA-Z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_REFERENCE_ID = re.compile(r"[a-z][a-z0-9._/-]{2,127}")
_KINDS = frozenset({"runbook", "source", "configuration", "service-catalog"})
_STATUSES = frozenset({"complete", "partial", "no-data"})
_WARNINGS = frozenset({"backend-partial", "document-limit", "excerpt-limit"})


class InvalidContextEvidenceRequestError(ValueError):
    """Public repository/runbook context request violated a stable boundary."""


@dataclass(frozen=True)
class CollectContextEvidenceCommand:
    """Authenticated request to collect one normalized context artifact."""

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


class ContextEvidenceService:
    """Validate a public context query and commit its normalized artifact."""

    def __init__(self, evidence: EvidenceCollectionService, clock: Clock) -> None:
        self._evidence = evidence
        self._clock = clock

    def execute(self, command: CollectContextEvidenceCommand) -> Mapping[str, object]:
        request, metadata, spec, query, limits = self._validate(command)
        encoded_query = json.dumps(
            {
                "requestDigest": canonical_digest(request),
                "requestId": metadata["requestId"],
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
                provider="context-query",
                integration_id=spec["integrationId"],
                evidence_type="repository.context",
                resource_uids=tuple(spec["resourceRefs"]),
                locator="context://documents/query/v1",
                query=encoded_query,
                deadline=spec["deadline"],
                max_bytes=limits["maxBytes"],
                sensitivity="confidential",
                retention_class="ephemeral",
            )
        )

    def _validate(
        self, command: CollectContextEvidenceCommand
    ) -> tuple[
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
                or request["kind"] != "ContextEvidenceRequest"
            ):
                raise KeyError
            metadata = dict(request["metadata"])
            spec = dict(request["spec"])
        except (KeyError, TypeError, ValueError):
            raise self._invalid() from None
        if set(metadata) != {"requestId", "tenantId", "actorId", "requestedAt"}:
            raise self._invalid()
        if set(spec) != {
            "integrationId",
            "resourceRefs",
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
        refs = spec.get("resourceRefs")
        if (
            not isinstance(refs, list)
            or not 1 <= len(refs) <= 32
            or len(refs) != len(set(refs))
            or any(
                not isinstance(item, str) or not _RESOURCE_UID.fullmatch(item)
                for item in refs
            )
        ):
            raise self._invalid()
        requested_at = self._parse_time(metadata.get("requestedAt"))
        now = self._parse_time(self._clock.now())
        deadline = self._parse_time(spec.get("deadline"))
        if not requested_at <= now <= deadline:
            raise self._invalid()
        if deadline - requested_at > MAX_DEADLINE_OFFSET:
            raise self._invalid()
        query, limits = self.validate_query_contract(spec.get("query"), spec.get("limits"))
        return request, metadata, spec, query, limits

    @classmethod
    def validate_query_contract(
        cls, query_value: object, limits_value: object
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            query = dict(query_value)  # type: ignore[arg-type]
            limits = dict(limits_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise cls._invalid() from None
        kinds = query.get("kinds")
        refs = query.get("referenceIds")
        if (
            set(query) != {"kinds", "referenceIds"}
            or not isinstance(kinds, list)
            or len(kinds) > len(_KINDS)
            or len(kinds) != len(set(kinds))
            or any(kind not in _KINDS for kind in kinds)
            or not isinstance(refs, list)
            or len(refs) > 32
            or len(refs) != len(set(refs))
            or any(not isinstance(ref, str) or not _REFERENCE_ID.fullmatch(ref) for ref in refs)
            or set(limits) != {"maxDocuments", "maxExcerptChars", "maxBytes"}
            or not cls._integer(limits.get("maxDocuments"), 1, 32)
            or not cls._integer(limits.get("maxExcerptChars"), 1, 8192)
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
            raise ContextEvidenceService._invalid()
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ContextEvidenceService._invalid() from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ContextEvidenceService._invalid()
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _invalid() -> InvalidContextEvidenceRequestError:
        return InvalidContextEvidenceRequestError("context.request.invalid")


class ContextEvidenceProvider:
    """Normalize and redact allowlisted context returned by a backend adapter."""

    def __init__(
        self,
        backend: ContextDocumentsBackend,
        redactor: EvidenceRedactor,
        clock: Clock,
    ) -> None:
        self._backend = backend
        self._redactor = redactor
        self._clock = clock

    def fetch(self, request: EvidenceProviderRequest) -> RawEvidenceArtifact:
        payload, query, limits = self._provider_query(request)
        result = self._backend.query_context(
            ContextDocumentQuery(
                tenant_id=request.tenant_id,
                actor_id=request.actor_id,
                request_id=payload["requestId"],
                integration_id=request.integration_id,
                resource_uids=request.resource_uids,
                kinds=tuple(query["kinds"]),
                reference_ids=tuple(query["referenceIds"]),
                max_documents=limits["maxDocuments"],
                max_excerpt_chars=limits["maxExcerptChars"],
                max_bytes=limits["maxBytes"],
                deadline=request.deadline,
            )
        )
        if result.status not in _STATUSES or any(
            warning not in _WARNINGS for warning in result.warnings
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        warnings = set(result.warnings)
        documents = list(result.documents)
        if len(documents) > limits["maxDocuments"]:
            documents = documents[: limits["maxDocuments"]]
            warnings.add("document-limit")
        requested_kinds = set(query["kinds"]) or set(_KINDS)
        requested_references = set(query["referenceIds"])
        requested_resources = set(request.resource_uids)
        normalized: list[dict[str, object]] = []
        seen: set[str] = set()
        for document in documents:
            if (
                not _REFERENCE_ID.fullmatch(document.reference_id)
                or document.reference_id in seen
                or document.kind not in requested_kinds
                or (requested_references and document.reference_id not in requested_references)
                or not document.resource_uids
                or not set(document.resource_uids).issubset(requested_resources)
                or len(document.resource_uids) != len(set(document.resource_uids))
                or any(not _RESOURCE_UID.fullmatch(uid) for uid in document.resource_uids)
                or not ContextEvidenceService._safe_text(document.title, 256)
                or not ContextEvidenceService._safe_text(document.locator, 2048)
                or not ContextEvidenceService._safe_text(document.revision, 128)
                or not isinstance(document.content, str)
                or not document.content
            ):
                raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
            excerpt = document.content
            if len(excerpt) > limits["maxExcerptChars"]:
                excerpt = excerpt[: limits["maxExcerptChars"]]
                warnings.add("excerpt-limit")
            try:
                inspected = self._redactor.redact(
                    excerpt.encode("utf-8"),
                    media_type="text/plain; charset=utf-8",
                    evidence_type="repository.context",
                )
                redacted = inspected.content.decode("utf-8")
            except (UnicodeError, ValueError):
                raise InvalidEvidenceRequestError(
                    "evidence.provider.output-invalid"
                ) from None
            if not redacted:
                raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
            digest = "sha256:" + hashlib.sha256(inspected.content).hexdigest()
            id_material = (
                f"{payload['requestId']}\x1f{document.reference_id}\x1f"
                f"{document.revision}\x1f{digest}"
            ).encode()
            normalized.append(
                {
                    "id": "ctx_" + hashlib.sha256(id_material).hexdigest()[:32],
                    "referenceId": document.reference_id,
                    "resourceRefs": list(document.resource_uids),
                    "kind": document.kind,
                    "title": document.title,
                    "locator": document.locator,
                    "revision": document.revision,
                    "excerpt": redacted,
                    "excerptHash": digest,
                    "redactionMethods": list(inspected.methods or ("none",)),
                    "trust": "untrusted",
                    "instructionPolicy": "data-only",
                }
            )
            seen.add(document.reference_id)
        normalized.sort(key=lambda item: (str(item["kind"]), str(item["referenceId"])))
        if result.status == "no-data" and (normalized or warnings):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        if result.status == "complete" and result.warnings:
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        if result.status == "partial" and not result.warnings:
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        status = "partial" if warnings else "complete" if normalized else "no-data"
        counts: dict[str, int] = {}
        for document in normalized:
            kind = str(document["kind"])
            counts[kind] = counts.get(kind, 0) + 1
        created_at = self._clock.now()
        document = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ContextEvidenceResult",
            "metadata": {
                "requestId": payload["requestId"],
                "tenantId": request.tenant_id,
                "integrationId": request.integration_id,
                "createdAt": created_at,
            },
            "spec": {
                "requestDigest": payload["requestDigest"],
                "status": status,
                "documents": normalized,
                "summary": {
                    "documentCount": len(normalized),
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
            "No allowlisted repository or runbook context matched the request."
            if status == "no-data"
            else f"Normalized {len(normalized)} untrusted context document(s)."
        )
        return RawEvidenceArtifact(
            content=content,
            media_type="application/json",
            observed_at=result.executed_at,
            summary=summary,
        )

    @staticmethod
    def _provider_query(
        request: EvidenceProviderRequest,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        if (
            request.evidence_type != "repository.context"
            or request.locator != "context://documents/query/v1"
            or not isinstance(request.query, str)
        ):
            raise InvalidEvidenceRequestError("evidence.provider.input-invalid")
        try:
            payload = json.loads(request.query)
            if set(payload) != {"requestDigest", "requestId", "query", "limits"}:
                raise KeyError
            query, limits = ContextEvidenceService.validate_query_contract(
                payload["query"], payload["limits"]
            )
        except (
            json.JSONDecodeError,
            KeyError,
            TypeError,
            InvalidContextEvidenceRequestError,
        ):
            raise InvalidEvidenceRequestError(
                "evidence.provider.input-invalid"
            ) from None
        if (
            not isinstance(payload.get("requestDigest"), str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", payload["requestDigest"])
            or not isinstance(payload.get("requestId"), str)
            or not _REQUEST_ID.fullmatch(payload["requestId"])
        ):
            raise InvalidEvidenceRequestError("evidence.provider.input-invalid")
        return payload, query, limits
