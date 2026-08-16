"""Bounded deterministic investigation reference runtime."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

from iip.application.collect_evidence import (
    CollectEvidenceCommand,
    EvidenceAuthorizationError,
    EvidenceCollectionService,
    EvidenceDeadlineExceededError,
    EvidenceProviderUnavailableError,
    EvidenceRedactionError,
    InvalidEvidenceRequestError,
)
from iip.application.ports import (
    ActorContext,
    Clock,
    InvestigationRepository,
    PersistenceError,
    ResourceRepository,
)
from iip.application.telemetry_evidence import (
    CollectTelemetryEvidenceCommand,
    InvalidTelemetryEvidenceRequestError,
    TelemetryEvidenceService,
)


_SELECTION_ID = re.compile(r"tqs_[a-f0-9]{16}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_ROOT_CAUSE_CLASS = re.compile(r"[a-z][a-z0-9._/-]{2,127}")


class InvalidInvestigationError(ValueError):
    """The request violated contract, identity, scope, or budget invariants."""


class InvestigationConflictError(RuntimeError):
    """An investigation ID was reused with different immutable input."""


@dataclass(frozen=True)
class RunInvestigationCommand:
    actor: ActorContext
    request: Mapping[str, Any]


def canonical_digest(document: object) -> str:
    encoded = json.dumps(
        document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class DeterministicInvestigationService:
    """A no-model incident slice with explicit budgets and evidence citations."""

    def __init__(
        self,
        resources: ResourceRepository,
        evidence: EvidenceCollectionService,
        investigations: InvestigationRepository,
        clock: Clock,
        telemetry: TelemetryEvidenceService | None = None,
    ) -> None:
        self._resources = resources
        self._evidence = evidence
        self._investigations = investigations
        self._clock = clock
        self._telemetry = telemetry

    def execute(self, command: RunInvestigationCommand) -> Mapping[str, object]:
        request, metadata, spec, scope, budgets = self._validate(command)
        investigation_id = metadata["id"]
        existing = self._investigations.get_investigation(command.actor, investigation_id)
        if existing is not None:
            stored_request = self._investigations.get_investigation_request(
                command.actor, investigation_id
            )
            if stored_request is None or canonical_digest(stored_request) != canonical_digest(
                request
            ):
                raise InvestigationConflictError("investigation.id.conflict")
            return existing

        resources = tuple(
            self._resources.get_many(command.actor.tenant_id, scope["resourceUids"])
        )
        if {item.identity.uid for item in resources} != set(scope["resourceUids"]):
            raise InvalidInvestigationError("investigation.resource.unavailable")

        started_at = self._clock.now()
        evidence_documents: list[Mapping[str, object]] = []
        supporting_evidence_ids: list[str] = []
        telemetry_unknowns: list[dict[str, object]] = []
        tool_calls = 0
        allowed_tools = spec.get("allowedTools", [])
        requested_types = spec.get("evidenceTypes", [])
        tools_allow_collection = not allowed_tools or "evidence/fetch" in allowed_tools
        resource_types = [
            evidence_type
            for evidence_type in requested_types
            if evidence_type != "telemetry.metrics"
        ]
        resource_type_allowed = not requested_types or bool(resource_types)
        if (
            budgets["maxToolCalls"] > 0
            and budgets["maxEvidenceItems"] > 0
            and tools_allow_collection
            and resource_type_allowed
        ):
            evidence_type = (
                "kubernetes.pod-status"
                if "kubernetes.pod-status" in resource_types
                else resource_types[0]
                if resource_types
                else "kubernetes.resource-status"
            )
            tool_calls += 1
            resource_evidence = self._evidence.execute(
                CollectEvidenceCommand(
                    actor=command.actor,
                    provider="resource-state",
                    integration_id="platform-resource-state",
                    evidence_type=evidence_type,
                    resource_uids=tuple(scope["resourceUids"]),
                    locator="resource://current",
                    deadline=self._deadline(started_at, budgets["maxWallTimeSeconds"]),
                    max_bytes=1_048_576,
                )
            )
            evidence_documents.append(resource_evidence)
            supporting_evidence_ids.append(resource_evidence["metadata"]["id"])

        root_cause, statement, confidence = self._classify(resources)
        telemetry_allowed = (
            (not requested_types or "telemetry.metrics" in requested_types)
            and (not allowed_tools or "telemetry/query" in allowed_tools)
        )
        matching_selections = self._matching_telemetry_selections(spec, root_cause)
        if matching_selections and telemetry_allowed and self._telemetry is None:
            telemetry_unknowns.append(self._telemetry_unknown("unavailable"))
        elif telemetry_allowed and self._telemetry is not None:
            for selection in matching_selections:
                if (
                    tool_calls >= budgets["maxToolCalls"]
                    or len(evidence_documents) >= budgets["maxEvidenceItems"]
                ):
                    break
                tool_calls += 1
                try:
                    telemetry_evidence = self._telemetry.execute(
                        CollectTelemetryEvidenceCommand(
                            command.actor,
                            self._telemetry_request(
                                command,
                                selection,
                                scope,
                                started_at,
                                budgets,
                            ),
                        )
                    )
                except (
                    EvidenceAuthorizationError,
                    EvidenceDeadlineExceededError,
                    EvidenceProviderUnavailableError,
                    EvidenceRedactionError,
                    InvalidEvidenceRequestError,
                    InvalidTelemetryEvidenceRequestError,
                    PersistenceError,
                ):
                    telemetry_unknowns.append(
                        self._telemetry_unknown(str(selection["id"]))
                    )
                    continue
                evidence_documents.append(telemetry_evidence)

        completed_at = self._clock.now()
        evidence_ids = [
            document["metadata"]["id"] for document in evidence_documents
        ]
        if not supporting_evidence_ids:
            outcome = "inconclusive"
            budget_exhausted = (
                budgets["maxToolCalls"] == 0 or budgets["maxEvidenceItems"] == 0
            )
            terminal_reason = (
                "budget-exhausted" if budget_exhausted else "insufficient-evidence"
            )
            summary = "Current resource status was not collected as evidence."
            hypotheses: list[dict[str, object]] = []
            unknowns = [
                {
                    "statement": "Current resource status was not collected.",
                    "impact": "high",
                    "requestedEvidenceTypes": ["kubernetes.resource-status"],
                }
            ]
        elif root_cause is None:
            outcome = "inconclusive"
            terminal_reason = "insufficient-evidence"
            summary = "Current resource status did not identify a supported failure class."
            hypotheses = []
            unknowns = [
                {
                    "statement": "Additional event, log, or metric evidence is required.",
                    "impact": "medium",
                    "requestedEvidenceTypes": [
                        "kubernetes.event",
                        "kubernetes.pod-log",
                        "telemetry.metrics",
                    ],
                }
            ]
        else:
            outcome = "conclusive"
            terminal_reason = "sufficient-evidence"
            summary = statement
            hypothesis_material = f"{investigation_id}\x1f{root_cause}".encode()
            hypotheses = [
                {
                    "id": "hyp_" + hashlib.sha256(hypothesis_material).hexdigest()[:16],
                    "rank": 1,
                    "statement": statement,
                    "rootCauseClass": root_cause,
                    "confidence": confidence,
                    "disposition": "leading",
                    "supportingEvidenceIds": supporting_evidence_ids,
                    "contradictingEvidenceIds": [],
                }
            ]
            unknowns = []
        unknowns.extend(telemetry_unknowns)

        selector = spec.get("agentSelector")
        if not isinstance(selector, Mapping):
            selector = {"id": "incident-investigator", "version": "0.1.0"}
        agent_material = {"id": selector["id"], "version": selector["version"]}
        report: dict[str, object] = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "InvestigationReport",
            "metadata": {
                "id": investigation_id,
                "tenantId": command.actor.tenant_id,
                "createdAt": completed_at,
            },
            "spec": {
                "requestDigest": canonical_digest(request),
                "outcome": outcome,
                "terminalReason": terminal_reason,
                "startedAt": started_at,
                "completedAt": completed_at,
                "scope": scope,
                "agent": {
                    "id": selector["id"],
                    "version": selector["version"],
                    "manifestDigest": canonical_digest(agent_material),
                    "modelClass": "local",
                },
                "summary": summary,
                "hypotheses": hypotheses,
                "unknowns": unknowns,
                "evidenceIds": evidence_ids,
                "recommendations": self._recommendations(
                    investigation_id, root_cause, supporting_evidence_ids
                ),
                "toolCallLedgerRef": (
                    f"ledger://{command.actor.tenant_id}/investigations/"
                    f"{investigation_id}/tool-calls"
                ),
                "policySnapshotRef": (
                    f"policy://{command.actor.tenant_id}/snapshots/"
                    "deterministic-investigation-v1"
                ),
                "usage": {
                    "toolCalls": tool_calls,
                    "iterations": 1,
                    "modelTokens": 0,
                    "wallTimeSeconds": 0,
                    "costUsd": 0,
                    "evidenceItems": len(evidence_ids),
                },
            },
        }
        self._investigations.commit_investigation(
            command.actor, investigation_id, request, report
        )
        return report

    @staticmethod
    def _matching_telemetry_selections(
        spec: Mapping[str, object],
        root_cause: str | None,
    ) -> tuple[Mapping[str, object], ...]:
        selections = spec.get("telemetrySelections", [])
        if not isinstance(selections, list):
            return ()
        matching = []
        for selection in selections:
            if not isinstance(selection, Mapping):
                continue
            classes = selection.get("rootCauseClasses")
            if classes is None or (
                root_cause is not None
                and isinstance(classes, list)
                and root_cause in classes
            ):
                matching.append(selection)
        return tuple(matching)

    @staticmethod
    def _telemetry_request(
        command: RunInvestigationCommand,
        selection: Mapping[str, object],
        scope: Mapping[str, object],
        started_at: str,
        budgets: Mapping[str, object],
    ) -> Mapping[str, object]:
        material = (
            f"{command.request['metadata']['id']}\x1f{selection['id']}".encode()
        )
        request_id = "teq_" + hashlib.sha256(material).hexdigest()[:32]
        max_wall_time = budgets["maxWallTimeSeconds"]
        if not isinstance(max_wall_time, int) or isinstance(max_wall_time, bool):
            raise InvalidInvestigationError("investigation.contract.invalid")
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "TelemetryEvidenceRequest",
            "metadata": {
                "requestId": request_id,
                "tenantId": command.actor.tenant_id,
                "actorId": command.actor.actor_id,
                "requestedAt": started_at,
            },
            "spec": {
                "integrationId": selection["integrationId"],
                "resourceRefs": list(scope["resourceUids"]),
                "signal": "metrics",
                "timeRange": dict(scope["timeRange"]),
                "query": dict(selection["query"]),
                "limits": dict(selection["limits"]),
                "deadline": DeterministicInvestigationService._deadline(
                    started_at,
                    min(max_wall_time, 300),
                ),
            },
        }

    @staticmethod
    def _telemetry_unknown(selection_id: str) -> dict[str, object]:
        return {
            "statement": (
                "Selected metric evidence could not be collected for "
                f"{selection_id}."
            ),
            "impact": "medium",
            "requestedEvidenceTypes": ["telemetry.metrics"],
        }

    @staticmethod
    def _classify(resources: tuple[object, ...]) -> tuple[str | None, str, float]:
        for resource in resources:
            attributes = getattr(resource, "attributes")
            if attributes.get("waitingReason") in ("ErrImagePull", "ImagePullBackOff"):
                return (
                    "kubernetes.image-pull.manifest-not-found",
                    "A scoped Pod cannot pull its configured image, blocking workload progress.",
                    0.95,
                )
        for resource in resources:
            attributes = getattr(resource, "attributes")
            desired = attributes.get("replicas")
            available = attributes.get("availableReplicas", attributes.get("readyReplicas"))
            if isinstance(desired, int) and isinstance(available, int) and available < desired:
                return (
                    "kubernetes.rollout.unavailable-replicas",
                    "A scoped workload has fewer available replicas than requested.",
                    0.82,
                )
        if any(getattr(resource, "health") in ("degraded", "unhealthy") for resource in resources):
            return (
                "infrastructure.resource.degraded",
                "One or more scoped resources report degraded or unhealthy state.",
                0.7,
            )
        return None, "No supported failure class was found.", 0.0

    @staticmethod
    def _recommendations(
        investigation_id: str, root_cause: str | None, evidence_ids: list[str]
    ) -> list[dict[str, object]]:
        if root_cause is None:
            description = "Collect Kubernetes events and workload logs for the scoped resources."
        elif root_cause.startswith("kubernetes.image-pull"):
            description = "Verify the image repository and tag before proposing a new rollout."
        else:
            description = "Inspect the newest workload revision and its Pod status before acting."
        digest = hashlib.sha256(f"{investigation_id}\x1f{description}".encode()).hexdigest()
        return [
            {
                "id": "rec_" + digest[:16],
                "type": "next-check",
                "description": description,
                "priority": "high",
                "evidenceIds": evidence_ids,
            }
        ]

    @staticmethod
    def _deadline(started_at: str, seconds: int) -> str:
        parsed = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        return (parsed + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _validate(
        command: RunInvestigationCommand,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        try:
            request = dict(command.request)
            if request.get("apiVersion") != "iip.platform/v1alpha1" or request.get(
                "kind"
            ) != "InvestigationRequest":
                raise KeyError
            metadata = dict(request["metadata"])
            spec = dict(request["spec"])
            scope = dict(spec["scope"])
            budgets = dict(spec["budgets"])
        except (KeyError, TypeError, ValueError):
            raise InvalidInvestigationError("investigation.contract.invalid") from None
        if (
            metadata.get("tenantId") != command.actor.tenant_id
            or metadata.get("actorId") != command.actor.actor_id
        ):
            raise InvalidInvestigationError("investigation.scope.mismatch")
        resource_uids = scope.get("resourceUids")
        if (
            not isinstance(resource_uids, list)
            or not 1 <= len(resource_uids) <= 256
            or any(
                not isinstance(uid, str) or not _RESOURCE_UID.fullmatch(uid)
                for uid in resource_uids
            )
            or len(resource_uids) != len(set(resource_uids))
        ):
            raise InvalidInvestigationError("investigation.contract.invalid")
        try:
            time_range = dict(scope["timeRange"])
            start = datetime.fromisoformat(time_range["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(time_range["end"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise InvalidInvestigationError("investigation.contract.invalid") from None
        if (
            set(time_range) != {"start", "end"}
            or start.tzinfo is None
            or end.tzinfo is None
            or start >= end
        ):
            raise InvalidInvestigationError("investigation.contract.invalid")
        scope["timeRange"] = time_range

        integer_budgets = {
            "maxToolCalls": (0, 200),
            "maxWallTimeSeconds": (1, 3600),
            "maxModelTokens": (0, 1_000_000),
            "maxEvidenceItems": (1, 1000),
            "maxIterations": (1, 100),
        }
        for name, (minimum, maximum) in integer_budgets.items():
            value = budgets.get(name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not minimum <= value <= maximum
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
        max_cost = budgets.get("maxCostUsd")
        if (
            isinstance(max_cost, bool)
            or not isinstance(max_cost, (int, float))
            or not 0 <= max_cost <= 1000
        ):
            raise InvalidInvestigationError("investigation.contract.invalid")
        for field, maximum in (("evidenceTypes", 64), ("allowedTools", 128)):
            values = spec.get(field, [])
            if (
                not isinstance(values, list)
                or len(values) > maximum
                or any(
                    not isinstance(value, str)
                    or not _ROOT_CAUSE_CLASS.fullmatch(value)
                    for value in values
                )
                or len(values) != len(set(values))
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            spec[field] = list(values)
        selections = spec.get("telemetrySelections", [])
        if not isinstance(selections, list) or len(selections) > 8:
            raise InvalidInvestigationError("investigation.contract.invalid")
        normalized_selections: list[dict[str, object]] = []
        selection_ids: set[str] = set()
        for value in selections:
            if not isinstance(value, Mapping) or not {
                "id",
                "integrationId",
                "query",
                "limits",
            } <= set(value) or set(value).difference(
                {"id", "integrationId", "query", "limits", "rootCauseClasses"}
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            selection_id = value.get("id")
            integration_id = value.get("integrationId")
            if (
                not isinstance(selection_id, str)
                or not _SELECTION_ID.fullmatch(selection_id)
                or selection_id in selection_ids
                or not isinstance(integration_id, str)
                or not _INTEGRATION_ID.fullmatch(integration_id)
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            try:
                query, limits = TelemetryEvidenceService.validate_query_contract(
                    value.get("query"),
                    value.get("limits"),
                )
            except InvalidTelemetryEvidenceRequestError:
                raise InvalidInvestigationError(
                    "investigation.contract.invalid"
                ) from None
            classes = value.get("rootCauseClasses")
            if classes is not None and (
                not isinstance(classes, list)
                or not 1 <= len(classes) <= 16
                or any(
                    not isinstance(root_cause, str)
                    or not _ROOT_CAUSE_CLASS.fullmatch(root_cause)
                    for root_cause in classes
                )
                or len(classes) != len(set(classes))
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            normalized: dict[str, object] = {
                "id": selection_id,
                "integrationId": integration_id,
                "query": query,
                "limits": limits,
            }
            if classes is not None:
                normalized["rootCauseClasses"] = list(classes)
            normalized_selections.append(normalized)
            selection_ids.add(selection_id)
        spec["telemetrySelections"] = normalized_selections
        return request, metadata, spec, scope, budgets
