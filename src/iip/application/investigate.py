"""Bounded deterministic investigation reference runtime."""

from __future__ import annotations

import hashlib
import json
import math
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
    EvidenceStore,
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
_INTERPRETATION_STATISTICS = frozenset({"minimum", "maximum", "mean"})
_INTERPRETATION_OPERATORS = frozenset({"lt", "lte", "gt", "gte"})
_INTERPRETATION_DISPOSITIONS = frozenset(
    {"supports", "contradicts", "neutral"}
)


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
        evidence_store: EvidenceStore | None = None,
    ) -> None:
        self._resources = resources
        self._evidence = evidence
        self._investigations = investigations
        self._clock = clock
        self._telemetry = telemetry
        self._evidence_store = evidence_store

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
        resource_evidence_ids: list[str] = []
        supporting_evidence_ids: list[str] = []
        contradicting_evidence_ids: list[str] = []
        telemetry_assessments: list[dict[str, object]] = []
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
            resource_evidence_id = resource_evidence["metadata"]["id"]
            resource_evidence_ids.append(resource_evidence_id)
            supporting_evidence_ids.append(resource_evidence_id)

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
                    telemetry_request = self._telemetry_request(
                        command,
                        selection,
                        scope,
                        started_at,
                        budgets,
                    )
                    telemetry_evidence = self._telemetry.execute(
                        CollectTelemetryEvidenceCommand(
                            command.actor,
                            telemetry_request,
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
                interpretation = selection.get("interpretation")
                baseline_comparison = selection.get("baselineComparison")
                if isinstance(interpretation, Mapping) or isinstance(
                    baseline_comparison, Mapping
                ):
                    assessment = self._assess_telemetry(
                        command.actor,
                        selection,
                        telemetry_evidence,
                        root_cause,
                        telemetry_request,
                    )
                    if assessment is None:
                        telemetry_unknowns.append(
                            self._telemetry_assessment_unknown(
                                str(selection["id"])
                            )
                        )
                    else:
                        telemetry_assessments.append(assessment)
                        evidence_id = str(assessment["evidenceId"])
                        if assessment["disposition"] == "supporting":
                            supporting_evidence_ids.append(evidence_id)
                        elif assessment["disposition"] == "contradicting":
                            contradicting_evidence_ids.append(evidence_id)

        completed_at = self._clock.now()
        evidence_ids = [
            document["metadata"]["id"] for document in evidence_documents
        ]
        if not resource_evidence_ids:
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
                    "contradictingEvidenceIds": contradicting_evidence_ids,
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
        if telemetry_assessments:
            report_spec = report["spec"]
            if isinstance(report_spec, dict):
                report_spec["telemetryAssessments"] = telemetry_assessments
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
    def _telemetry_assessment_unknown(selection_id: str) -> dict[str, object]:
        return {
            "statement": (
                "Selected metric evidence could not be safely assessed for "
                f"{selection_id}."
            ),
            "impact": "medium",
            "requestedEvidenceTypes": ["telemetry.metrics"],
        }

    def _assess_telemetry(
        self,
        actor: ActorContext,
        selection: Mapping[str, object],
        evidence: Mapping[str, object],
        root_cause: str | None,
        telemetry_request: Mapping[str, object],
    ) -> dict[str, object] | None:
        interpretation = selection.get("interpretation")
        baseline_comparison = selection.get("baselineComparison")
        rule = (
            interpretation
            if isinstance(interpretation, Mapping)
            else baseline_comparison
        )
        if (
            self._evidence_store is None
            or root_cause is None
            or not isinstance(rule, Mapping)
        ):
            return None
        try:
            metadata = evidence["metadata"]
            if not isinstance(metadata, Mapping):
                return None
            evidence_id = metadata["id"]
            if not isinstance(evidence_id, str):
                return None
            artifact = self._evidence_store.read_artifact(actor, evidence_id)
        except (KeyError, PersistenceError):
            return None
        if not isinstance(artifact, bytes):
            return None
        try:
            document = json.loads(artifact.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(document, Mapping) or (
            document.get("apiVersion") != "iip.platform/v1alpha1"
            or document.get("kind") != "TelemetryEvidenceResult"
        ):
            return None
        artifact_metadata = document.get("metadata")
        artifact_spec = document.get("spec")
        query = selection.get("query")
        request_metadata = telemetry_request.get("metadata")
        request_spec = telemetry_request.get("spec")
        if (
            not isinstance(artifact_metadata, Mapping)
            or not isinstance(request_metadata, Mapping)
            or artifact_metadata.get("requestId") != request_metadata.get("requestId")
            or artifact_metadata.get("tenantId") != actor.tenant_id
            or artifact_metadata.get("integrationId") != selection.get("integrationId")
            or not isinstance(artifact_spec, Mapping)
            or not isinstance(request_spec, Mapping)
            or artifact_spec.get("signal") != "metrics"
            or artifact_spec.get("requestDigest")
            != canonical_digest(telemetry_request)
            or artifact_spec.get("timeRange") != request_spec.get("timeRange")
            or not isinstance(query, Mapping)
            or not isinstance(query.get("metric"), str)
        ):
            return None
        status = artifact_spec.get("status")
        if status not in {"complete", "partial", "no-data"}:
            return None
        if isinstance(baseline_comparison, Mapping):
            assessment: dict[str, object] = {
                "assessmentType": "baseline-comparison",
                "selectionId": selection["id"],
                "evidenceId": evidence_id,
                "rootCauseClass": root_cause,
                "metric": query["metric"],
                "statistic": baseline_comparison["statistic"],
                "unit": baseline_comparison["unit"],
                "baselineTimeRange": dict(
                    baseline_comparison["baselineTimeRange"]
                ),
                "evaluationTimeRange": dict(
                    baseline_comparison["evaluationTimeRange"]
                ),
                "calculation": baseline_comparison["calculation"],
                "comparisonUnit": (
                    "1"
                    if baseline_comparison["calculation"] == "ratio"
                    else baseline_comparison["unit"]
                ),
                "operator": baseline_comparison["operator"],
                "threshold": baseline_comparison["threshold"],
            }
        elif isinstance(interpretation, Mapping):
            assessment = {
                "selectionId": selection["id"],
                "evidenceId": evidence_id,
                "rootCauseClass": root_cause,
                "metric": query["metric"],
                "statistic": interpretation["statistic"],
                "unit": interpretation["unit"],
                "operator": interpretation["operator"],
                "threshold": interpretation["threshold"],
            }
        else:
            return None
        if status == "no-data":
            assessment["disposition"] = "no-data"
            return assessment
        if status == "partial":
            assessment["disposition"] = "incomplete"
            return assessment

        points = self._metric_points(
            artifact_spec,
            query["metric"],
            rule["unit"],
            request_spec.get("timeRange"),
        )
        if points is None:
            return None
        if isinstance(baseline_comparison, Mapping):
            return self._baseline_assessment(
                assessment,
                points,
                baseline_comparison,
            )
        if not isinstance(interpretation, Mapping):
            return None
        observed_value = self._statistic(
            [value for _, value in points], interpretation["statistic"]
        )
        if observed_value is None:
            return None
        matched = self._compare(
            observed_value,
            float(interpretation["threshold"]),
            interpretation["operator"],
        )
        configured = interpretation[
            "whenMatched" if matched else "whenNotMatched"
        ]
        assessment["observedValue"] = observed_value
        assessment["disposition"] = self._assessment_disposition(configured)
        return assessment

    @staticmethod
    def _metric_points(
        artifact_spec: Mapping[str, object],
        metric: object,
        unit: object,
        time_range: object,
    ) -> list[tuple[datetime, float]] | None:
        if not isinstance(time_range, Mapping):
            return None
        range_start = DeterministicInvestigationService._parse_datetime(
            time_range.get("start")
        )
        range_end = DeterministicInvestigationService._parse_datetime(
            time_range.get("end")
        )
        series = artifact_spec.get("series")
        if (
            range_start is None
            or range_end is None
            or not isinstance(series, list)
            or not series
        ):
            return None
        values: list[tuple[datetime, float]] = []
        for item in series:
            if (
                not isinstance(item, Mapping)
                or item.get("metric") != metric
                or item.get("unit") != unit
                or not isinstance(item.get("points"), list)
                or not item["points"]
            ):
                return None
            for point in item["points"]:
                if not isinstance(point, Mapping):
                    return None
                timestamp = DeterministicInvestigationService._parse_datetime(
                    point.get("timestamp")
                )
                value = point.get("value")
                if (
                    timestamp is None
                    or not range_start <= timestamp <= range_end
                    or isinstance(value, bool)
                    or not isinstance(value, (int, float))
                ):
                    return None
                try:
                    normalized_value = float(value)
                except (OverflowError, ValueError):
                    return None
                if not math.isfinite(normalized_value):
                    return None
                values.append((timestamp, normalized_value))
        return values or None

    @staticmethod
    def _baseline_assessment(
        assessment: dict[str, object],
        points: list[tuple[datetime, float]],
        comparison: Mapping[str, object],
    ) -> dict[str, object]:
        baseline_range = comparison["baselineTimeRange"]
        evaluation_range = comparison["evaluationTimeRange"]
        if not isinstance(baseline_range, Mapping) or not isinstance(
            evaluation_range, Mapping
        ):
            assessment["disposition"] = "incomplete"
            return assessment
        baseline_start = DeterministicInvestigationService._parse_datetime(
            baseline_range.get("start")
        )
        baseline_end = DeterministicInvestigationService._parse_datetime(
            baseline_range.get("end")
        )
        evaluation_start = DeterministicInvestigationService._parse_datetime(
            evaluation_range.get("start")
        )
        evaluation_end = DeterministicInvestigationService._parse_datetime(
            evaluation_range.get("end")
        )
        if any(
            value is None
            for value in (
                baseline_start,
                baseline_end,
                evaluation_start,
                evaluation_end,
            )
        ):
            assessment["disposition"] = "incomplete"
            return assessment
        baseline_values = [
            value
            for timestamp, value in points
            if baseline_start <= timestamp <= baseline_end
        ]
        evaluation_values = [
            value
            for timestamp, value in points
            if evaluation_start <= timestamp <= evaluation_end
        ]
        statistic = comparison["statistic"]
        baseline_value = DeterministicInvestigationService._statistic(
            baseline_values, statistic
        )
        evaluation_value = DeterministicInvestigationService._statistic(
            evaluation_values, statistic
        )
        if baseline_value is None or evaluation_value is None:
            assessment["disposition"] = "incomplete"
            return assessment
        calculation = comparison["calculation"]
        try:
            if calculation == "difference":
                comparison_value = evaluation_value - baseline_value
            elif baseline_value != 0:
                comparison_value = evaluation_value / baseline_value
            else:
                assessment["disposition"] = "incomplete"
                return assessment
        except (OverflowError, ValueError):
            assessment["disposition"] = "incomplete"
            return assessment
        if not math.isfinite(comparison_value):
            assessment["disposition"] = "incomplete"
            return assessment
        matched = DeterministicInvestigationService._compare(
            comparison_value,
            float(comparison["threshold"]),
            comparison["operator"],
        )
        configured = comparison[
            "whenMatched" if matched else "whenNotMatched"
        ]
        assessment.update(
            {
                "baselineValue": baseline_value,
                "evaluationValue": evaluation_value,
                "comparisonValue": comparison_value,
                "disposition": DeterministicInvestigationService._assessment_disposition(
                    configured
                ),
            }
        )
        return assessment

    @staticmethod
    def _statistic(values: list[float], statistic: object) -> float | None:
        if not values:
            return None
        try:
            if statistic == "minimum":
                result = min(values)
            elif statistic == "maximum":
                result = max(values)
            elif statistic == "mean":
                result = math.fsum(values) / len(values)
            else:
                return None
        except (OverflowError, ValueError):
            return None
        return result if math.isfinite(result) else None

    @staticmethod
    def _compare(value: float, threshold: float, operator: object) -> bool:
        return {
            "lt": value < threshold,
            "lte": value <= threshold,
            "gt": value > threshold,
            "gte": value >= threshold,
        }[operator]

    @staticmethod
    def _assessment_disposition(configured: object) -> str:
        return {
            "supports": "supporting",
            "contradicts": "contradicting",
            "neutral": "neutral",
        }[configured]

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed

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
                {
                    "id",
                    "integrationId",
                    "query",
                    "limits",
                    "rootCauseClasses",
                    "interpretation",
                    "baselineComparison",
                }
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
            interpretation = value.get("interpretation")
            baseline_comparison = value.get("baselineComparison")
            if interpretation is not None and baseline_comparison is not None:
                raise InvalidInvestigationError("investigation.contract.invalid")
            normalized_interpretation = None
            if interpretation is not None:
                if classes is None:
                    raise InvalidInvestigationError(
                        "investigation.contract.invalid"
                    )
                normalized_interpretation = (
                    DeterministicInvestigationService._validate_interpretation(
                        interpretation
                    )
                )
            normalized_baseline_comparison = None
            if baseline_comparison is not None:
                if classes is None:
                    raise InvalidInvestigationError(
                        "investigation.contract.invalid"
                    )
                normalized_baseline_comparison = (
                    DeterministicInvestigationService._validate_baseline_comparison(
                        baseline_comparison,
                        scope_start=start,
                        scope_end=end,
                    )
                )
            normalized: dict[str, object] = {
                "id": selection_id,
                "integrationId": integration_id,
                "query": query,
                "limits": limits,
            }
            if classes is not None:
                normalized["rootCauseClasses"] = list(classes)
            if normalized_interpretation is not None:
                normalized["interpretation"] = normalized_interpretation
            if normalized_baseline_comparison is not None:
                normalized["baselineComparison"] = normalized_baseline_comparison
            normalized_selections.append(normalized)
            selection_ids.add(selection_id)
        spec["telemetrySelections"] = normalized_selections
        return request, metadata, spec, scope, budgets

    @staticmethod
    def _validate_interpretation(value: object) -> dict[str, object]:
        if not isinstance(value, Mapping) or set(value) != {
            "statistic",
            "unit",
            "operator",
            "threshold",
            "whenMatched",
            "whenNotMatched",
        }:
            raise InvalidInvestigationError("investigation.contract.invalid")
        statistic = value.get("statistic")
        unit = value.get("unit")
        operator = value.get("operator")
        threshold = value.get("threshold")
        when_matched = value.get("whenMatched")
        when_not_matched = value.get("whenNotMatched")
        try:
            threshold_is_finite = math.isfinite(float(threshold))
        except (OverflowError, TypeError, ValueError):
            threshold_is_finite = False
        if (
            statistic not in _INTERPRETATION_STATISTICS
            or not isinstance(unit, str)
            or not 1 <= len(unit) <= 64
            or any(ord(character) < 32 or ord(character) == 127 for character in unit)
            or operator not in _INTERPRETATION_OPERATORS
            or isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not threshold_is_finite
            or when_matched not in _INTERPRETATION_DISPOSITIONS
            or when_not_matched not in _INTERPRETATION_DISPOSITIONS
            or when_matched == when_not_matched
        ):
            raise InvalidInvestigationError("investigation.contract.invalid")
        return dict(value)

    @staticmethod
    def _validate_baseline_comparison(
        value: object,
        *,
        scope_start: datetime,
        scope_end: datetime,
    ) -> dict[str, object]:
        if not isinstance(value, Mapping) or set(value) != {
            "statistic",
            "unit",
            "baselineTimeRange",
            "evaluationTimeRange",
            "calculation",
            "operator",
            "threshold",
            "whenMatched",
            "whenNotMatched",
        }:
            raise InvalidInvestigationError("investigation.contract.invalid")
        statistic = value.get("statistic")
        unit = value.get("unit")
        calculation = value.get("calculation")
        operator = value.get("operator")
        threshold = value.get("threshold")
        when_matched = value.get("whenMatched")
        when_not_matched = value.get("whenNotMatched")
        try:
            threshold_is_finite = math.isfinite(float(threshold))
        except (OverflowError, TypeError, ValueError):
            threshold_is_finite = False
        baseline_range, baseline_start, baseline_end = (
            DeterministicInvestigationService._validate_assessment_range(
                value.get("baselineTimeRange")
            )
        )
        evaluation_range, evaluation_start, evaluation_end = (
            DeterministicInvestigationService._validate_assessment_range(
                value.get("evaluationTimeRange")
            )
        )
        if (
            statistic not in _INTERPRETATION_STATISTICS
            or not isinstance(unit, str)
            or not 1 <= len(unit) <= 64
            or any(ord(character) < 32 or ord(character) == 127 for character in unit)
            or calculation not in {"difference", "ratio"}
            or operator not in _INTERPRETATION_OPERATORS
            or isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not threshold_is_finite
            or when_matched not in _INTERPRETATION_DISPOSITIONS
            or when_not_matched not in _INTERPRETATION_DISPOSITIONS
            or when_matched == when_not_matched
            or baseline_range is None
            or evaluation_range is None
            or baseline_start is None
            or baseline_end is None
            or evaluation_start is None
            or evaluation_end is None
            or not (
                scope_start
                <= baseline_start
                < baseline_end
                < evaluation_start
                < evaluation_end
                <= scope_end
            )
        ):
            raise InvalidInvestigationError("investigation.contract.invalid")
        normalized = dict(value)
        normalized["baselineTimeRange"] = baseline_range
        normalized["evaluationTimeRange"] = evaluation_range
        return normalized

    @staticmethod
    def _validate_assessment_range(
        value: object,
    ) -> tuple[dict[str, object] | None, datetime | None, datetime | None]:
        if not isinstance(value, Mapping) or set(value) != {"start", "end"}:
            return None, None, None
        start = DeterministicInvestigationService._parse_datetime(value.get("start"))
        end = DeterministicInvestigationService._parse_datetime(value.get("end"))
        if start is None or end is None or start >= end:
            return None, None, None
        return dict(value), start, end
