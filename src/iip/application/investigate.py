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
from iip.application.context_evidence import (
    CollectContextEvidenceCommand,
    ContextEvidenceService,
    InvalidContextEvidenceRequestError,
)
from iip.application.ports import (
    ActorContext,
    Clock,
    EvidenceStore,
    InvestigationExecutionMeasurement,
    InvestigationRepository,
    InvestigationTelemetrySink,
    PersistenceError,
    ResourceRepository,
)
from iip.application.kubernetes_event_evidence import (
    CollectKubernetesEventEvidenceCommand,
    InvalidKubernetesEventEvidenceRequestError,
    KubernetesEventEvidenceService,
)
from iip.application.log_evidence import (
    CollectLogEvidenceCommand,
    InvalidLogEvidenceRequestError,
    LogEvidenceService,
)
from iip.application.resource_change_evidence import (
    CollectResourceChangeEvidenceCommand,
    InvalidResourceChangeEvidenceRequestError,
    ResourceChangeEvidenceService,
)
from iip.application.telemetry_evidence import (
    CollectTelemetryEvidenceCommand,
    InvalidTelemetryEvidenceRequestError,
    TelemetryEvidenceService,
)


_SELECTION_ID = re.compile(r"tqs_[a-f0-9]{16}")
_KUBERNETES_EVENT_SELECTION_ID = re.compile(r"kes_[a-f0-9]{16}")
_LOG_SELECTION_ID = re.compile(r"lqs_[a-f0-9]{16}")
_LOG_RECORD_ID = re.compile(r"log_[a-f0-9]{32}")
_CHANGE_SELECTION_ID = re.compile(r"cqs_[a-f0-9]{16}")
_CHANGE_ID = re.compile(r"chg_[a-f0-9]{32}")
_CONTEXT_SELECTION_ID = re.compile(r"xqs_[a-f0-9]{16}")
_CONTEXT_DOCUMENT_ID = re.compile(r"ctx_[a-f0-9]{32}")
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


class InvestigationInProgressError(RuntimeError):
    """The immutable investigation ID already has a live execution lease."""


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
        kubernetes_events: KubernetesEventEvidenceService | None = None,
        telemetry: TelemetryEvidenceService | None = None,
        logs: LogEvidenceService | None = None,
        resource_changes: ResourceChangeEvidenceService | None = None,
        context: ContextEvidenceService | None = None,
        evidence_store: EvidenceStore | None = None,
        telemetry_sink: InvestigationTelemetrySink | None = None,
    ) -> None:
        self._resources = resources
        self._evidence = evidence
        self._investigations = investigations
        self._clock = clock
        self._kubernetes_events = kubernetes_events
        self._telemetry = telemetry
        self._logs = logs
        self._resource_changes = resource_changes
        self._context = context
        self._evidence_store = evidence_store
        self._telemetry_sink = telemetry_sink

    def execute(self, command: RunInvestigationCommand) -> Mapping[str, object]:
        request, metadata, spec, scope, budgets = self._validate(command)
        investigation_id = metadata["id"]
        existing = self._investigations.get_investigation(command.actor, investigation_id)
        stored_request = self._investigations.get_investigation_request(
            command.actor, investigation_id
        )
        if existing is not None:
            if stored_request is None or canonical_digest(stored_request) != canonical_digest(
                request
            ):
                raise InvestigationConflictError("investigation.id.conflict")
            return existing
        if stored_request is not None:
            if canonical_digest(stored_request) != canonical_digest(request):
                raise InvestigationConflictError("investigation.id.conflict")
            status = self._investigations.get_investigation_status(
                command.actor, investigation_id
            )
            if status is None:
                raise PersistenceError("storage.unavailable")
            if self._lease_expired(status, self._clock.now()):
                return self._recover_abandoned(
                    command.actor,
                    request,
                    scope,
                    status,
                )
            status_spec = status.get("spec")
            if (
                not isinstance(status_spec, Mapping)
                or status_spec.get("state")
                not in {"running", "cancellation-requested"}
            ):
                raise PersistenceError("storage.unavailable")
            raise InvestigationInProgressError("investigation.in_progress")

        resources = tuple(
            self._resources.get_many(command.actor.tenant_id, scope["resourceUids"])
        )
        if {item.identity.uid for item in resources} != set(scope["resourceUids"]):
            raise InvalidInvestigationError("investigation.resource.unavailable")

        started_at = self._clock.now()
        running_status = self._running_status(
            command.actor,
            investigation_id,
            request,
            started_at,
            budgets["maxWallTimeSeconds"],
        )
        try:
            self._investigations.start_investigation(
                command.actor,
                investigation_id,
                request,
                running_status,
            )
        except PersistenceError as exc:
            if str(exc) != "storage.conflict":
                raise
            concurrent_request = self._investigations.get_investigation_request(
                command.actor, investigation_id
            )
            if (
                concurrent_request is None
                or canonical_digest(concurrent_request) != canonical_digest(request)
            ):
                raise InvestigationConflictError("investigation.id.conflict") from None
            raise InvestigationInProgressError("investigation.in_progress") from None
        evidence_documents: list[Mapping[str, object]] = []
        resource_evidence_ids: list[str] = []
        supporting_evidence_ids: list[str] = []
        contradicting_evidence_ids: list[str] = []
        kubernetes_event_assessments: list[dict[str, object]] = []
        kubernetes_event_unknowns: list[dict[str, object]] = []
        telemetry_assessments: list[dict[str, object]] = []
        telemetry_unknowns: list[dict[str, object]] = []
        log_assessments: list[dict[str, object]] = []
        log_unknowns: list[dict[str, object]] = []
        change_assessments: list[dict[str, object]] = []
        change_unknowns: list[dict[str, object]] = []
        context_assessments: list[dict[str, object]] = []
        context_unknowns: list[dict[str, object]] = []
        tool_calls = 0
        allowed_tools = spec.get("allowedTools", [])
        requested_types = spec.get("evidenceTypes", [])
        tools_allow_collection = not allowed_tools or "evidence/fetch" in allowed_tools
        resource_types = [
            evidence_type
            for evidence_type in requested_types
            if evidence_type
            not in {
                "kubernetes.event",
                "resource.change",
                "repository.context",
                "telemetry.metrics",
                "telemetry.logs",
            }
        ]
        resource_type_allowed = not requested_types or bool(resource_types)
        if (
            budgets["maxToolCalls"] > 0
            and budgets["maxEvidenceItems"] > 0
            and tools_allow_collection
            and resource_type_allowed
            and not self._cancellation_requested(command.actor, investigation_id)
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
        kubernetes_events_allowed = (
            (not requested_types or "kubernetes.event" in requested_types)
            and (not allowed_tools or "events/search" in allowed_tools)
        )
        matching_event_selections = self._matching_kubernetes_event_selections(
            spec, root_cause
        )
        if (
            matching_event_selections
            and kubernetes_events_allowed
            and self._kubernetes_events is None
        ):
            kubernetes_event_unknowns.append(
                self._kubernetes_event_unknown("unavailable")
            )
        elif kubernetes_events_allowed and self._kubernetes_events is not None:
            for selection in matching_event_selections:
                if (
                    self._cancellation_requested(command.actor, investigation_id)
                    or tool_calls >= budgets["maxToolCalls"]
                    or len(evidence_documents) >= budgets["maxEvidenceItems"]
                ):
                    break
                tool_calls += 1
                try:
                    event_request = self._kubernetes_event_request(
                        command,
                        selection,
                        scope,
                        started_at,
                        budgets,
                    )
                    event_evidence = self._kubernetes_events.execute(
                        CollectKubernetesEventEvidenceCommand(
                            command.actor,
                            event_request,
                        )
                    )
                except (
                    EvidenceAuthorizationError,
                    EvidenceDeadlineExceededError,
                    EvidenceProviderUnavailableError,
                    EvidenceRedactionError,
                    InvalidEvidenceRequestError,
                    InvalidKubernetesEventEvidenceRequestError,
                    PersistenceError,
                ):
                    kubernetes_event_unknowns.append(
                        self._kubernetes_event_unknown(str(selection["id"]))
                    )
                    continue
                evidence_documents.append(event_evidence)
                if isinstance(selection.get("interpretation"), Mapping):
                    assessment = self._assess_kubernetes_events(
                        command.actor,
                        selection,
                        event_evidence,
                        root_cause,
                        event_request,
                    )
                    if assessment is None:
                        kubernetes_event_unknowns.append(
                            self._kubernetes_event_assessment_unknown(
                                str(selection["id"])
                            )
                        )
                    else:
                        kubernetes_event_assessments.append(assessment)
                        evidence_id = str(assessment["evidenceId"])
                        if assessment["disposition"] == "supporting":
                            supporting_evidence_ids.append(evidence_id)
                        elif assessment["disposition"] == "contradicting":
                            contradicting_evidence_ids.append(evidence_id)

        context_allowed = (
            (not requested_types or "repository.context" in requested_types)
            and tools_allow_collection
        )
        matching_context_selections = self._matching_context_selections(spec, root_cause)
        if matching_context_selections and context_allowed and self._context is None:
            context_unknowns.append(self._context_unknown("unavailable"))
        elif context_allowed and self._context is not None:
            for selection in matching_context_selections:
                if (
                    self._cancellation_requested(command.actor, investigation_id)
                    or tool_calls >= budgets["maxToolCalls"]
                    or len(evidence_documents) >= budgets["maxEvidenceItems"]
                ):
                    break
                tool_calls += 1
                try:
                    context_request = self._context_request(
                        command,
                        selection,
                        scope,
                        started_at,
                        budgets,
                    )
                    context_evidence = self._context.execute(
                        CollectContextEvidenceCommand(command.actor, context_request)
                    )
                except (
                    EvidenceAuthorizationError,
                    EvidenceDeadlineExceededError,
                    EvidenceProviderUnavailableError,
                    EvidenceRedactionError,
                    InvalidContextEvidenceRequestError,
                    InvalidEvidenceRequestError,
                    PersistenceError,
                ):
                    context_unknowns.append(
                        self._context_unknown(str(selection["id"]))
                    )
                    continue
                evidence_documents.append(context_evidence)
                if isinstance(selection.get("interpretation"), Mapping):
                    assessment = self._assess_context(
                        command.actor,
                        selection,
                        context_evidence,
                        root_cause,
                        context_request,
                    )
                    if assessment is None:
                        context_unknowns.append(
                            self._context_assessment_unknown(str(selection["id"]))
                        )
                    else:
                        context_assessments.append(assessment)
                        evidence_id = str(assessment["evidenceId"])
                        if assessment["disposition"] == "supporting":
                            supporting_evidence_ids.append(evidence_id)
                        elif assessment["disposition"] == "contradicting":
                            contradicting_evidence_ids.append(evidence_id)

        changes_allowed = (
            (not requested_types or "resource.change" in requested_types)
            and tools_allow_collection
        )
        matching_change_selections = self._matching_change_selections(spec, root_cause)
        if (
            matching_change_selections
            and changes_allowed
            and self._resource_changes is None
        ):
            change_unknowns.append(self._change_unknown("unavailable"))
        elif changes_allowed and self._resource_changes is not None:
            for selection in matching_change_selections:
                if (
                    self._cancellation_requested(command.actor, investigation_id)
                    or tool_calls >= budgets["maxToolCalls"]
                    or len(evidence_documents) >= budgets["maxEvidenceItems"]
                ):
                    break
                tool_calls += 1
                try:
                    change_request = self._change_request(
                        command,
                        selection,
                        scope,
                        started_at,
                        budgets,
                    )
                    change_evidence = self._resource_changes.execute(
                        CollectResourceChangeEvidenceCommand(
                            command.actor,
                            change_request,
                        )
                    )
                except (
                    EvidenceAuthorizationError,
                    EvidenceDeadlineExceededError,
                    EvidenceProviderUnavailableError,
                    EvidenceRedactionError,
                    InvalidEvidenceRequestError,
                    InvalidResourceChangeEvidenceRequestError,
                    PersistenceError,
                ):
                    change_unknowns.append(
                        self._change_unknown(str(selection["id"]))
                    )
                    continue
                evidence_documents.append(change_evidence)
                if isinstance(selection.get("interpretation"), Mapping):
                    assessment = self._assess_changes(
                        command.actor,
                        selection,
                        change_evidence,
                        root_cause,
                        change_request,
                    )
                    if assessment is None:
                        change_unknowns.append(
                            self._change_assessment_unknown(str(selection["id"]))
                        )
                    else:
                        change_assessments.append(assessment)
                        evidence_id = str(assessment["evidenceId"])
                        if assessment["disposition"] == "supporting":
                            supporting_evidence_ids.append(evidence_id)
                        elif assessment["disposition"] == "contradicting":
                            contradicting_evidence_ids.append(evidence_id)

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
                    self._cancellation_requested(command.actor, investigation_id)
                    or tool_calls >= budgets["maxToolCalls"]
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

        logs_allowed = (
            (not requested_types or "telemetry.logs" in requested_types)
            and (not allowed_tools or "telemetry/query" in allowed_tools)
        )
        matching_log_selections = self._matching_log_selections(spec, root_cause)
        if matching_log_selections and logs_allowed and self._logs is None:
            log_unknowns.append(self._log_unknown("unavailable"))
        elif logs_allowed and self._logs is not None:
            for selection in matching_log_selections:
                if (
                    self._cancellation_requested(command.actor, investigation_id)
                    or tool_calls >= budgets["maxToolCalls"]
                    or len(evidence_documents) >= budgets["maxEvidenceItems"]
                ):
                    break
                tool_calls += 1
                try:
                    log_request = self._log_request(
                        command,
                        selection,
                        scope,
                        started_at,
                        budgets,
                    )
                    log_evidence = self._logs.execute(
                        CollectLogEvidenceCommand(command.actor, log_request)
                    )
                except (
                    EvidenceAuthorizationError,
                    EvidenceDeadlineExceededError,
                    EvidenceProviderUnavailableError,
                    EvidenceRedactionError,
                    InvalidEvidenceRequestError,
                    InvalidLogEvidenceRequestError,
                    PersistenceError,
                ):
                    log_unknowns.append(self._log_unknown(str(selection["id"])))
                    continue
                evidence_documents.append(log_evidence)
                if isinstance(selection.get("interpretation"), Mapping):
                    assessment = self._assess_logs(
                        command.actor,
                        selection,
                        log_evidence,
                        root_cause,
                        log_request,
                    )
                    if assessment is None:
                        log_unknowns.append(
                            self._log_assessment_unknown(str(selection["id"]))
                        )
                    else:
                        log_assessments.append(assessment)
                        evidence_id = str(assessment["evidenceId"])
                        if assessment["disposition"] == "supporting":
                            supporting_evidence_ids.append(evidence_id)
                        elif assessment["disposition"] == "contradicting":
                            contradicting_evidence_ids.append(evidence_id)

        completed_at = self._clock.now()
        wall_time_seconds = self._wall_time_seconds(
            started_at,
            completed_at,
            budgets["maxWallTimeSeconds"],
        )
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
                        "telemetry.logs",
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
        unknowns.extend(kubernetes_event_unknowns)
        unknowns.extend(change_unknowns)
        unknowns.extend(context_unknowns)
        unknowns.extend(telemetry_unknowns)
        unknowns.extend(log_unknowns)

        lifecycle_status = self._investigations.get_investigation_status(
            command.actor, investigation_id
        )
        lifecycle_spec = (
            lifecycle_status.get("spec")
            if isinstance(lifecycle_status, Mapping)
            else None
        )
        cancellation = (
            lifecycle_spec.get("cancellation")
            if isinstance(lifecycle_spec, Mapping)
            and lifecycle_spec.get("state") == "cancellation-requested"
            else None
        )
        if isinstance(cancellation, Mapping):
            outcome = "cancelled"
            terminal_reason = "cancelled"
            summary = "The investigation stopped after a cooperative cancellation request."
            hypotheses = []
            unknowns.append(
                {
                    "statement": "The investigation ended before every eligible evidence source was attempted.",
                    "impact": "medium",
                    "requestedEvidenceTypes": (
                        list(requested_types)
                        if requested_types
                        else ["kubernetes.resource-status"]
                    ),
                }
            )

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
                "recommendations": (
                    []
                    if outcome == "cancelled"
                    else self._recommendations(
                        investigation_id, root_cause, supporting_evidence_ids
                    )
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
                    "wallTimeSeconds": wall_time_seconds,
                    "costUsd": 0,
                    "evidenceItems": len(evidence_ids),
                },
            },
        }
        if telemetry_assessments:
            report_spec = report["spec"]
            if isinstance(report_spec, dict):
                report_spec["telemetryAssessments"] = telemetry_assessments
        if kubernetes_event_assessments:
            report_spec = report["spec"]
            if isinstance(report_spec, dict):
                report_spec["kubernetesEventAssessments"] = (
                    kubernetes_event_assessments
                )
        if log_assessments:
            report_spec = report["spec"]
            if isinstance(report_spec, dict):
                report_spec["logAssessments"] = log_assessments
        if change_assessments:
            report_spec = report["spec"]
            if isinstance(report_spec, dict):
                report_spec["changeAssessments"] = change_assessments
        if context_assessments:
            report_spec = report["spec"]
            if isinstance(report_spec, dict):
                report_spec["contextAssessments"] = context_assessments
        terminal_status = self._terminal_status(
            command.actor,
            investigation_id,
            request,
            started_at,
            completed_at,
            outcome,
            cancellation if isinstance(cancellation, Mapping) else None,
        )
        try:
            self._investigations.commit_investigation(
                command.actor, investigation_id, request, report, terminal_status
            )
        except PersistenceError as exc:
            if str(exc) != "storage.conflict" or outcome == "cancelled":
                raise
            latest_status = self._investigations.get_investigation_status(
                command.actor, investigation_id
            )
            latest_spec = (
                latest_status.get("spec")
                if isinstance(latest_status, Mapping)
                else None
            )
            late_cancellation = (
                latest_spec.get("cancellation")
                if isinstance(latest_spec, Mapping)
                and latest_spec.get("state") == "cancellation-requested"
                else None
            )
            report_spec = report.get("spec")
            if not isinstance(late_cancellation, Mapping) or not isinstance(
                report_spec, dict
            ):
                raise
            report_spec.update(
                {
                    "outcome": "cancelled",
                    "terminalReason": "cancelled",
                    "summary": "The investigation stopped after a cooperative cancellation request.",
                    "hypotheses": [],
                    "recommendations": [],
                }
            )
            report_unknowns = report_spec.get("unknowns")
            if isinstance(report_unknowns, list):
                report_unknowns.append(
                    {
                        "statement": "The investigation ended before every eligible evidence source was attempted.",
                        "impact": "medium",
                        "requestedEvidenceTypes": (
                            list(requested_types)
                            if requested_types
                            else ["kubernetes.resource-status"]
                        ),
                    }
                )
            terminal_status = self._terminal_status(
                command.actor,
                investigation_id,
                request,
                started_at,
                completed_at,
                "cancelled",
                late_cancellation,
            )
            self._investigations.commit_investigation(
                command.actor, investigation_id, request, report, terminal_status
            )
        self._record_telemetry(report)
        return report

    @staticmethod
    def _running_status(
        actor: ActorContext,
        investigation_id: str,
        request: Mapping[str, object],
        started_at: str,
        max_wall_time_seconds: int,
    ) -> dict[str, object]:
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "InvestigationStatus",
            "metadata": {
                "id": investigation_id,
                "tenantId": actor.tenant_id,
                "updatedAt": started_at,
            },
            "spec": {
                "requestDigest": canonical_digest(request),
                "state": "running",
                "startedAt": started_at,
                "leaseExpiresAt": DeterministicInvestigationService._deadline(
                    started_at, max_wall_time_seconds
                ),
            },
        }

    @staticmethod
    def _terminal_status(
        actor: ActorContext,
        investigation_id: str,
        request: Mapping[str, object],
        started_at: str,
        completed_at: str,
        outcome: str,
        cancellation: Mapping[str, object] | None,
    ) -> dict[str, object]:
        state = {
            "conclusive": "completed",
            "inconclusive": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }[outcome]
        spec: dict[str, object] = {
            "requestDigest": canonical_digest(request),
            "state": state,
            "startedAt": started_at,
            "completedAt": completed_at,
            "reportRef": (
                f"investigation://{actor.tenant_id}/{investigation_id}/report"
            ),
        }
        if state == "cancelled" and cancellation is not None:
            spec["cancellation"] = dict(cancellation)
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "InvestigationStatus",
            "metadata": {
                "id": investigation_id,
                "tenantId": actor.tenant_id,
                "updatedAt": completed_at,
            },
            "spec": spec,
        }

    @staticmethod
    def _lease_expired(status: Mapping[str, object], now: str) -> bool:
        spec = status.get("spec")
        if not isinstance(spec, Mapping) or spec.get("state") not in {
            "running",
            "cancellation-requested",
        }:
            return False
        lease = DeterministicInvestigationService._parse_datetime(
            spec.get("leaseExpiresAt")
        )
        current = DeterministicInvestigationService._parse_datetime(now)
        if lease is None or current is None:
            raise PersistenceError("storage.unavailable")
        return current >= lease

    def _cancellation_requested(
        self, actor: ActorContext, investigation_id: str
    ) -> bool:
        status = self._investigations.get_investigation_status(
            actor, investigation_id
        )
        spec = status.get("spec") if isinstance(status, Mapping) else None
        return bool(
            isinstance(spec, Mapping)
            and spec.get("state") == "cancellation-requested"
            and isinstance(spec.get("cancellation"), Mapping)
        )

    def _recover_abandoned(
        self,
        actor: ActorContext,
        request: Mapping[str, object],
        scope: Mapping[str, object],
        status: Mapping[str, object],
    ) -> Mapping[str, object]:
        metadata = request.get("metadata")
        request_spec = request.get("spec")
        status_spec = status.get("spec")
        if (
            not isinstance(metadata, Mapping)
            or not isinstance(request_spec, Mapping)
            or not isinstance(status_spec, Mapping)
            or not isinstance(status_spec.get("startedAt"), str)
        ):
            raise PersistenceError("storage.unavailable")
        investigation_id = str(metadata["id"])
        started_at = str(status_spec["startedAt"])
        completed_at = self._clock.now()
        cancellation = status_spec.get("cancellation")
        cancelled = (
            status_spec.get("state") == "cancellation-requested"
            and isinstance(cancellation, Mapping)
        )
        evidence_types = request_spec.get("evidenceTypes", [])
        if not isinstance(evidence_types, list) or not evidence_types:
            evidence_types = ["kubernetes.resource-status"]
        request_budgets = request_spec.get("budgets")
        max_wall_time = (
            request_budgets.get("maxWallTimeSeconds")
            if isinstance(request_budgets, Mapping)
            else 0
        )
        if not isinstance(max_wall_time, int) or isinstance(max_wall_time, bool):
            raise PersistenceError("storage.unavailable")
        report: dict[str, object] = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "InvestigationReport",
            "metadata": {
                "id": investigation_id,
                "tenantId": actor.tenant_id,
                "createdAt": completed_at,
            },
            "spec": {
                "requestDigest": canonical_digest(request),
                "outcome": "cancelled" if cancelled else "failed",
                "terminalReason": "cancelled" if cancelled else "runtime-error",
                "startedAt": started_at,
                "completedAt": completed_at,
                "scope": dict(scope),
                "summary": (
                    "The investigation was cancelled before its prior execution could commit a terminal report."
                    if cancelled
                    else "The prior execution lease expired before a terminal report was committed."
                ),
                "hypotheses": [],
                "unknowns": [
                    {
                        "statement": "The interrupted execution did not commit a complete evidence result.",
                        "impact": "high",
                        "requestedEvidenceTypes": list(evidence_types),
                    }
                ],
                "evidenceIds": [],
                "recommendations": [],
                "toolCallLedgerRef": (
                    f"ledger://{actor.tenant_id}/investigations/"
                    f"{investigation_id}/tool-calls"
                ),
                "policySnapshotRef": (
                    f"policy://{actor.tenant_id}/snapshots/"
                    "deterministic-investigation-v1"
                ),
                "usage": {
                    "toolCalls": 0,
                    "iterations": 0,
                    "modelTokens": 0,
                    "wallTimeSeconds": self._wall_time_seconds(
                        started_at, completed_at, max_wall_time
                    ),
                    "costUsd": 0,
                    "evidenceItems": 0,
                },
            },
        }
        terminal_status = self._terminal_status(
            actor,
            investigation_id,
            request,
            started_at,
            completed_at,
            "cancelled" if cancelled else "failed",
            cancellation if isinstance(cancellation, Mapping) else None,
        )
        self._investigations.commit_investigation(
            actor,
            investigation_id,
            request,
            report,
            terminal_status,
        )
        self._record_telemetry(report)
        return report

    def _record_telemetry(self, report: Mapping[str, object]) -> None:
        if self._telemetry_sink is None:
            return
        try:
            metadata = report["metadata"]
            spec = report["spec"]
            if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
                return
            usage = spec["usage"]
            if not isinstance(usage, Mapping):
                return
            self._telemetry_sink.record_investigation_execution(
                InvestigationExecutionMeasurement(
                    tenant_id=str(metadata["tenantId"]),
                    investigation_id=str(metadata["id"]),
                    outcome=str(spec["outcome"]),
                    terminal_reason=str(spec["terminalReason"]),
                    started_at=str(spec["startedAt"]),
                    completed_at=str(spec["completedAt"]),
                    wall_time_seconds=float(usage["wallTimeSeconds"]),
                    tool_calls=int(usage["toolCalls"]),
                    evidence_items=int(usage["evidenceItems"]),
                )
            )
        except Exception:
            # Observability is intentionally failure-isolated from product behavior.
            return

    @staticmethod
    def _wall_time_seconds(
        started_at: str,
        completed_at: str,
        maximum: int,
    ) -> float:
        started = DeterministicInvestigationService._parse_datetime(started_at)
        completed = DeterministicInvestigationService._parse_datetime(completed_at)
        if started is None or completed is None:
            raise PersistenceError("storage.unavailable")
        elapsed = max(0.0, (completed - started).total_seconds())
        return round(min(float(maximum), elapsed), 3)

    @staticmethod
    def _matching_context_selections(
        spec: Mapping[str, object],
        root_cause: str | None,
    ) -> tuple[Mapping[str, object], ...]:
        selections = spec.get("contextSelections", [])
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
    def _context_request(
        command: RunInvestigationCommand,
        selection: Mapping[str, object],
        scope: Mapping[str, object],
        started_at: str,
        budgets: Mapping[str, object],
    ) -> Mapping[str, object]:
        material = (
            f"{command.request['metadata']['id']}\x1f{selection['id']}".encode()
        )
        request_id = "ctq_" + hashlib.sha256(material).hexdigest()[:32]
        max_wall_time = budgets["maxWallTimeSeconds"]
        if not isinstance(max_wall_time, int) or isinstance(max_wall_time, bool):
            raise InvalidInvestigationError("investigation.contract.invalid")
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ContextEvidenceRequest",
            "metadata": {
                "requestId": request_id,
                "tenantId": command.actor.tenant_id,
                "actorId": command.actor.actor_id,
                "requestedAt": started_at,
            },
            "spec": {
                "integrationId": selection["integrationId"],
                "resourceRefs": list(scope["resourceUids"]),
                "query": dict(selection["query"]),
                "limits": dict(selection["limits"]),
                "deadline": DeterministicInvestigationService._deadline(
                    started_at,
                    min(max_wall_time, 300),
                ),
            },
        }

    @staticmethod
    def _context_unknown(selection_id: str) -> dict[str, object]:
        return {
            "statement": (
                "Selected repository/runbook context could not be collected for "
                f"{selection_id}."
            ),
            "impact": "medium",
            "requestedEvidenceTypes": ["repository.context"],
        }

    @staticmethod
    def _context_assessment_unknown(selection_id: str) -> dict[str, object]:
        return {
            "statement": (
                "Selected repository/runbook context could not be safely assessed for "
                f"{selection_id}."
            ),
            "impact": "medium",
            "requestedEvidenceTypes": ["repository.context"],
        }

    def _assess_context(
        self,
        actor: ActorContext,
        selection: Mapping[str, object],
        evidence: Mapping[str, object],
        root_cause: str | None,
        context_request: Mapping[str, object],
    ) -> dict[str, object] | None:
        interpretation = selection.get("interpretation")
        if (
            self._evidence_store is None
            or root_cause is None
            or not isinstance(interpretation, Mapping)
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
        if (
            not isinstance(document, Mapping)
            or set(document) != {"apiVersion", "kind", "metadata", "spec"}
            or document.get("apiVersion") != "iip.platform/v1alpha1"
            or document.get("kind") != "ContextEvidenceResult"
        ):
            return None
        artifact_metadata = document.get("metadata")
        artifact_spec = document.get("spec")
        request_metadata = context_request.get("metadata")
        request_spec = context_request.get("spec")
        if (
            not isinstance(artifact_metadata, Mapping)
            or set(artifact_metadata)
            != {"requestId", "tenantId", "integrationId", "createdAt"}
            or not isinstance(request_metadata, Mapping)
            or artifact_metadata.get("requestId") != request_metadata.get("requestId")
            or artifact_metadata.get("tenantId") != actor.tenant_id
            or artifact_metadata.get("integrationId") != selection.get("integrationId")
            or self._parse_datetime(artifact_metadata.get("createdAt")) is None
            or not isinstance(artifact_spec, Mapping)
            or set(artifact_spec)
            != {"requestDigest", "status", "documents", "summary", "warnings"}
            or not isinstance(request_spec, Mapping)
            or artifact_spec.get("requestDigest") != canonical_digest(context_request)
        ):
            return None
        status = artifact_spec.get("status")
        documents = artifact_spec.get("documents")
        summary = artifact_spec.get("summary")
        warnings = artifact_spec.get("warnings")
        query = request_spec.get("query")
        limits = request_spec.get("limits")
        refs = request_spec.get("resourceRefs")
        if (
            status not in {"complete", "partial", "no-data"}
            or not isinstance(documents, list)
            or not isinstance(summary, Mapping)
            or set(summary) != {"documentCount", "countsByKind"}
            or not isinstance(warnings, list)
            or len(warnings) != len(set(warnings))
            or any(
                warning
                not in {"backend-partial", "document-limit", "excerpt-limit"}
                for warning in warnings
            )
            or not isinstance(query, Mapping)
            or not isinstance(query.get("kinds"), list)
            or not isinstance(query.get("referenceIds"), list)
            or not isinstance(limits, Mapping)
            or not isinstance(limits.get("maxDocuments"), int)
            or not isinstance(limits.get("maxExcerptChars"), int)
            or len(documents) > limits["maxDocuments"]
            or not isinstance(refs, list)
        ):
            return None
        if status == "no-data":
            if documents or warnings:
                return None
        elif status == "partial":
            if not warnings:
                return None
        elif not documents or warnings:
            return None

        requested_kinds = set(query["kinds"])
        requested_references = set(query["referenceIds"])
        known_kinds = {"runbook", "source", "configuration", "service-catalog"}
        if not requested_kinds:
            requested_kinds = known_kinds
        ids: set[str] = set()
        references: set[str] = set()
        counts: dict[str, int] = {}
        sort_keys: list[tuple[str, str]] = []
        required = {
            "id",
            "referenceId",
            "resourceRefs",
            "kind",
            "title",
            "locator",
            "revision",
            "excerpt",
            "excerptHash",
            "redactionMethods",
            "trust",
            "instructionPolicy",
        }
        for item in documents:
            if not isinstance(item, Mapping) or set(item) != required:
                return None
            document_id = item.get("id")
            reference_id = item.get("referenceId")
            document_refs = item.get("resourceRefs")
            kind = item.get("kind")
            excerpt = item.get("excerpt")
            methods = item.get("redactionMethods")
            if (
                not isinstance(document_id, str)
                or not _CONTEXT_DOCUMENT_ID.fullmatch(document_id)
                or document_id in ids
                or not isinstance(reference_id, str)
                or reference_id in references
                or (requested_references and reference_id not in requested_references)
                or kind not in requested_kinds
                or not isinstance(document_refs, list)
                or not document_refs
                or not set(document_refs).issubset(refs)
                or len(document_refs) != len(set(document_refs))
                or not self._safe_log_text(item.get("title"), maximum=256)
                or not self._safe_log_text(item.get("locator"), maximum=2048)
                or not self._safe_log_text(item.get("revision"), maximum=128)
                or not isinstance(excerpt, str)
                or not 1 <= len(excerpt) <= limits["maxExcerptChars"]
                or item.get("excerptHash")
                != "sha256:" + hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
                or not isinstance(methods, list)
                or not methods
                or len(methods) != len(set(methods))
                or any(
                    not isinstance(method, str)
                    or not _ROOT_CAUSE_CLASS.fullmatch(method)
                    for method in methods
                )
                or item.get("trust") != "untrusted"
                or item.get("instructionPolicy") != "data-only"
            ):
                return None
            ids.add(document_id)
            references.add(reference_id)
            counts[str(kind)] = counts.get(str(kind), 0) + 1
            sort_keys.append((str(kind), reference_id))
        if (
            sort_keys != sorted(sort_keys)
            or summary.get("documentCount") != len(documents)
            or summary.get("countsByKind") != dict(sorted(counts.items()))
        ):
            return None

        assessment: dict[str, object] = {
            "selectionId": selection["id"],
            "evidenceId": evidence_id,
            "rootCauseClass": root_cause,
            "kinds": list(query["kinds"]),
            "referenceIds": list(query["referenceIds"]),
            "minDocuments": interpretation["minDocuments"],
        }
        if status == "no-data":
            assessment["disposition"] = "no-data"
            return assessment
        if status == "partial":
            assessment["disposition"] = "incomplete"
            return assessment
        observed_count = len(documents)
        configured = interpretation[
            "whenMatched"
            if observed_count >= interpretation["minDocuments"]
            else "whenNotMatched"
        ]
        assessment["observedDocumentCount"] = observed_count
        assessment["observedDocumentIds"] = sorted(ids)
        assessment["observedReferenceIds"] = sorted(references)
        assessment["disposition"] = self._assessment_disposition(configured)
        return assessment

    @staticmethod
    def _matching_change_selections(
        spec: Mapping[str, object],
        root_cause: str | None,
    ) -> tuple[Mapping[str, object], ...]:
        selections = spec.get("changeSelections", [])
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
    def _change_request(
        command: RunInvestigationCommand,
        selection: Mapping[str, object],
        scope: Mapping[str, object],
        started_at: str,
        budgets: Mapping[str, object],
    ) -> Mapping[str, object]:
        material = (
            f"{command.request['metadata']['id']}\x1f{selection['id']}".encode()
        )
        request_id = "ceq_" + hashlib.sha256(material).hexdigest()[:32]
        max_wall_time = budgets["maxWallTimeSeconds"]
        if not isinstance(max_wall_time, int) or isinstance(max_wall_time, bool):
            raise InvalidInvestigationError("investigation.contract.invalid")
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ResourceChangeEvidenceRequest",
            "metadata": {
                "requestId": request_id,
                "tenantId": command.actor.tenant_id,
                "actorId": command.actor.actor_id,
                "requestedAt": started_at,
            },
            "spec": {
                "integrationId": selection["integrationId"],
                "resourceRefs": list(scope["resourceUids"]),
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
    def _change_unknown(selection_id: str) -> dict[str, object]:
        return {
            "statement": (
                "Selected resource-change evidence could not be collected for "
                f"{selection_id}."
            ),
            "impact": "medium",
            "requestedEvidenceTypes": ["resource.change"],
        }

    @staticmethod
    def _change_assessment_unknown(selection_id: str) -> dict[str, object]:
        return {
            "statement": (
                "Selected resource-change evidence could not be safely assessed for "
                f"{selection_id}."
            ),
            "impact": "medium",
            "requestedEvidenceTypes": ["resource.change"],
        }

    def _assess_changes(
        self,
        actor: ActorContext,
        selection: Mapping[str, object],
        evidence: Mapping[str, object],
        root_cause: str | None,
        change_request: Mapping[str, object],
    ) -> dict[str, object] | None:
        interpretation = selection.get("interpretation")
        if (
            self._evidence_store is None
            or root_cause is None
            or not isinstance(interpretation, Mapping)
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
        if (
            not isinstance(document, Mapping)
            or set(document) != {"apiVersion", "kind", "metadata", "spec"}
            or document.get("apiVersion") != "iip.platform/v1alpha1"
            or document.get("kind") != "ResourceChangeEvidenceResult"
        ):
            return None
        artifact_metadata = document.get("metadata")
        artifact_spec = document.get("spec")
        request_metadata = change_request.get("metadata")
        request_spec = change_request.get("spec")
        if (
            not isinstance(artifact_metadata, Mapping)
            or set(artifact_metadata)
            != {"requestId", "tenantId", "integrationId", "createdAt"}
            or not isinstance(request_metadata, Mapping)
            or artifact_metadata.get("requestId") != request_metadata.get("requestId")
            or artifact_metadata.get("tenantId") != actor.tenant_id
            or artifact_metadata.get("integrationId") != selection.get("integrationId")
            or self._parse_datetime(artifact_metadata.get("createdAt")) is None
            or not isinstance(artifact_spec, Mapping)
            or set(artifact_spec)
            != {"requestDigest", "timeRange", "status", "changes", "summary", "warnings"}
            or not isinstance(request_spec, Mapping)
            or artifact_spec.get("requestDigest") != canonical_digest(change_request)
            or artifact_spec.get("timeRange") != request_spec.get("timeRange")
        ):
            return None
        status = artifact_spec.get("status")
        changes = artifact_spec.get("changes")
        summary = artifact_spec.get("summary")
        warnings = artifact_spec.get("warnings")
        query = request_spec.get("query")
        refs = request_spec.get("resourceRefs")
        time_range = request_spec.get("timeRange")
        limits = request_spec.get("limits")
        if (
            status not in {"complete", "partial", "no-data"}
            or not isinstance(changes, list)
            or not isinstance(summary, Mapping)
            or set(summary) != {"changeCount", "affectedResourceCount", "countsByKind"}
            or not isinstance(warnings, list)
            or len(warnings) != len(set(warnings))
            or any(
                warning not in {"change-limit", "observation-limit"}
                for warning in warnings
            )
            or not isinstance(query, Mapping)
            or not isinstance(query.get("changeKinds"), list)
            or not isinstance(refs, list)
            or not isinstance(time_range, Mapping)
            or not isinstance(limits, Mapping)
            or not isinstance(limits.get("maxChanges"), int)
            or len(changes) > limits["maxChanges"]
        ):
            return None
        if status == "no-data":
            if changes or warnings:
                return None
        elif status == "partial":
            if not warnings:
                return None
        elif not changes or warnings:
            return None

        allowed_kinds = set(query["changeKinds"])
        known_kinds = {
            "created",
            "configuration",
            "image",
            "scale",
            "relationships",
            "status",
            "deleted",
        }
        if not allowed_kinds:
            allowed_kinds = known_kinds
        start = self._parse_datetime(time_range.get("start"))
        end = self._parse_datetime(time_range.get("end"))
        created_at = self._parse_datetime(artifact_metadata.get("createdAt"))
        if start is None or end is None or created_at is None:
            return None
        ids: set[str] = set()
        affected: set[str] = set()
        counts: dict[str, int] = {}
        sort_keys: list[tuple[str, str, str, str]] = []
        required = {
            "id",
            "resourceRef",
            "kind",
            "observedAt",
            "recordedAt",
            "afterObservationHash",
            "changedPaths",
            "source",
        }
        for change in changes:
            if (
                not isinstance(change, Mapping)
                or not required <= set(change)
                or set(change).difference(required | {"beforeObservationHash"})
            ):
                return None
            change_id = change.get("id")
            resource_ref = change.get("resourceRef")
            kind = change.get("kind")
            observed = self._parse_datetime(change.get("observedAt"))
            recorded = self._parse_datetime(change.get("recordedAt"))
            before_hash = change.get("beforeObservationHash")
            after_hash = change.get("afterObservationHash")
            paths = change.get("changedPaths")
            source = change.get("source")
            if (
                not isinstance(change_id, str)
                or not _CHANGE_ID.fullmatch(change_id)
                or change_id in ids
                or resource_ref not in refs
                or kind not in allowed_kinds
                or observed is None
                or recorded is None
                or not start <= observed <= recorded <= created_at
                or not isinstance(after_hash, str)
                or not re.fullmatch(r"[a-f0-9]{64}", after_hash)
                or (
                    before_hash is not None
                    and (
                        not isinstance(before_hash, str)
                        or not re.fullmatch(r"[a-f0-9]{64}", before_hash)
                        or before_hash == after_hash
                    )
                )
                or not isinstance(paths, list)
                or not 1 <= len(paths) <= 64
                or len(paths) != len(set(paths))
                or any(
                    not isinstance(path, str)
                    or not path.startswith("/")
                    or len(path) > 512
                    for path in paths
                )
                or not isinstance(source, Mapping)
                or not {"sourceId", "streamId", "sequence"} <= set(source)
                or set(source).difference(
                    {"sourceId", "streamId", "sequence", "resourceVersion"}
                )
                or not isinstance(source.get("sourceId"), str)
                or not isinstance(source.get("streamId"), str)
                or not re.fullmatch(r"obs_[a-f0-9]{32}", str(source.get("streamId")))
                or not isinstance(source.get("sequence"), int)
                or isinstance(source.get("sequence"), bool)
            ):
                return None
            ids.add(change_id)
            affected.add(str(resource_ref))
            counts[str(kind)] = counts.get(str(kind), 0) + 1
            sort_keys.append(
                (str(change["observedAt"]), str(resource_ref), str(kind), change_id)
            )
        if (
            sort_keys != sorted(sort_keys)
            or summary.get("changeCount") != len(changes)
            or summary.get("affectedResourceCount") != len(affected)
            or summary.get("countsByKind") != dict(sorted(counts.items()))
        ):
            return None

        assessment: dict[str, object] = {
            "selectionId": selection["id"],
            "evidenceId": evidence_id,
            "rootCauseClass": root_cause,
            "changeKinds": list(query["changeKinds"]),
            "minChanges": interpretation["minChanges"],
        }
        if status == "no-data":
            assessment["disposition"] = "no-data"
            return assessment
        if status == "partial":
            assessment["disposition"] = "incomplete"
            return assessment
        observed_count = len(changes)
        configured = interpretation[
            "whenMatched"
            if observed_count >= interpretation["minChanges"]
            else "whenNotMatched"
        ]
        assessment["observedChangeCount"] = observed_count
        assessment["observedChangeIds"] = sorted(ids)
        assessment["disposition"] = self._assessment_disposition(configured)
        return assessment

    @staticmethod
    def _matching_kubernetes_event_selections(
        spec: Mapping[str, object],
        root_cause: str | None,
    ) -> tuple[Mapping[str, object], ...]:
        selections = spec.get("kubernetesEventSelections", [])
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
    def _kubernetes_event_request(
        command: RunInvestigationCommand,
        selection: Mapping[str, object],
        scope: Mapping[str, object],
        started_at: str,
        budgets: Mapping[str, object],
    ) -> Mapping[str, object]:
        material = (
            f"{command.request['metadata']['id']}\x1f{selection['id']}".encode()
        )
        request_id = "keq_" + hashlib.sha256(material).hexdigest()[:32]
        max_wall_time = budgets["maxWallTimeSeconds"]
        if not isinstance(max_wall_time, int) or isinstance(max_wall_time, bool):
            raise InvalidInvestigationError("investigation.contract.invalid")
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "KubernetesEventEvidenceRequest",
            "metadata": {
                "requestId": request_id,
                "tenantId": command.actor.tenant_id,
                "actorId": command.actor.actor_id,
                "requestedAt": started_at,
            },
            "spec": {
                "integrationId": selection["integrationId"],
                "resourceRefs": list(scope["resourceUids"]),
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
    def _kubernetes_event_unknown(selection_id: str) -> dict[str, object]:
        return {
            "statement": (
                "Selected Kubernetes Event evidence could not be collected for "
                f"{selection_id}."
            ),
            "impact": "medium",
            "requestedEvidenceTypes": ["kubernetes.event"],
        }

    @staticmethod
    def _kubernetes_event_assessment_unknown(
        selection_id: str,
    ) -> dict[str, object]:
        return {
            "statement": (
                "Selected Kubernetes Event evidence could not be safely assessed for "
                f"{selection_id}."
            ),
            "impact": "medium",
            "requestedEvidenceTypes": ["kubernetes.event"],
        }

    def _assess_kubernetes_events(
        self,
        actor: ActorContext,
        selection: Mapping[str, object],
        evidence: Mapping[str, object],
        root_cause: str | None,
        event_request: Mapping[str, object],
    ) -> dict[str, object] | None:
        interpretation = selection.get("interpretation")
        if (
            self._evidence_store is None
            or root_cause is None
            or not isinstance(interpretation, Mapping)
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
        if (
            not isinstance(document, Mapping)
            or set(document) != {"apiVersion", "kind", "metadata", "spec"}
            or document.get("apiVersion") != "iip.platform/v1alpha1"
            or document.get("kind") != "KubernetesEventEvidenceResult"
        ):
            return None
        artifact_metadata = document.get("metadata")
        artifact_spec = document.get("spec")
        request_metadata = event_request.get("metadata")
        request_spec = event_request.get("spec")
        if (
            not isinstance(artifact_metadata, Mapping)
            or set(artifact_metadata)
            != {"requestId", "tenantId", "integrationId", "createdAt"}
            or not isinstance(request_metadata, Mapping)
            or artifact_metadata.get("requestId") != request_metadata.get("requestId")
            or artifact_metadata.get("tenantId") != actor.tenant_id
            or artifact_metadata.get("integrationId") != selection.get("integrationId")
            or self._parse_datetime(artifact_metadata.get("createdAt")) is None
            or not isinstance(artifact_spec, Mapping)
            or set(artifact_spec)
            != {"requestDigest", "timeRange", "status", "events", "summary", "warnings"}
            or not isinstance(request_spec, Mapping)
            or artifact_spec.get("requestDigest") != canonical_digest(event_request)
            or artifact_spec.get("timeRange") != request_spec.get("timeRange")
        ):
            return None

        assessment: dict[str, object] = {
            "selectionId": selection["id"],
            "evidenceId": evidence_id,
            "rootCauseClass": root_cause,
            "conditions": list(interpretation["conditions"]),
            "minMatches": interpretation["minMatches"],
        }
        status = artifact_spec.get("status")
        events = artifact_spec.get("events")
        summary = artifact_spec.get("summary")
        warnings = artifact_spec.get("warnings")
        if (
            status not in {"complete", "partial", "no-data"}
            or not isinstance(events, list)
            or not isinstance(summary, Mapping)
            or set(summary) != {"eventCount", "warningEventCount"}
            or not isinstance(warnings, list)
            or len(warnings) != len(set(warnings))
            or any(warning not in {"backend-partial", "event-limit"} for warning in warnings)
            or summary.get("eventCount") != len(events)
            or summary.get("warningEventCount")
            != sum(
                isinstance(event, Mapping) and event.get("severity") == "warning"
                for event in events
            )
        ):
            return None
        if status == "no-data":
            if events or warnings:
                return None
            assessment["disposition"] = "no-data"
            return assessment
        if status == "partial":
            if not events or not warnings:
                return None
        elif not events or warnings:
            return None

        request_query = request_spec.get("query")
        request_refs = request_spec.get("resourceRefs")
        request_range = request_spec.get("timeRange")
        if (
            not isinstance(request_query, Mapping)
            or not isinstance(request_refs, list)
            or not isinstance(request_range, Mapping)
        ):
            return None
        start = self._parse_datetime(request_range.get("start"))
        end = self._parse_datetime(request_range.get("end"))
        severities = request_query.get("severities")
        reasons = request_query.get("reasons")
        if (
            start is None
            or end is None
            or not isinstance(severities, list)
            or not isinstance(reasons, list)
        ):
            return None
        event_ids: set[str] = set()
        matched_ids: list[str] = []
        previous_key: tuple[str, str] | None = None
        required_fields = {
            "id",
            "resourceRef",
            "severity",
            "reason",
            "condition",
            "firstObservedAt",
            "lastObservedAt",
            "occurrenceCount",
        }
        for event in events:
            if (
                not isinstance(event, Mapping)
                or not required_fields <= set(event)
                or set(event).difference(
                    required_fields | {"reportingController", "message"}
                )
            ):
                return None
            event_id = event.get("id")
            first = self._parse_datetime(event.get("firstObservedAt"))
            last = self._parse_datetime(event.get("lastObservedAt"))
            key = (str(event.get("lastObservedAt")), str(event_id))
            if (
                not isinstance(event_id, str)
                or not re.fullmatch(r"kve_[a-f0-9]{32}", event_id)
                or event_id in event_ids
                or event.get("resourceRef") not in request_refs
                or event.get("severity") not in {"normal", "warning"}
                or (severities and event.get("severity") not in severities)
                or not isinstance(event.get("reason"), str)
                or not re.fullmatch(
                    r"[A-Za-z][A-Za-z0-9_.-]{0,127}", str(event.get("reason"))
                )
                or (reasons and event.get("reason") not in reasons)
                or not isinstance(event.get("condition"), str)
                or not _ROOT_CAUSE_CLASS.fullmatch(str(event.get("condition")))
                or first is None
                or last is None
                or not start <= first <= last <= end
                or not isinstance(event.get("occurrenceCount"), int)
                or isinstance(event.get("occurrenceCount"), bool)
                or not 1 <= event["occurrenceCount"] <= 2_147_483_647
                or (previous_key is not None and key < previous_key)
            ):
                return None
            event_ids.add(event_id)
            previous_key = key
            if event["condition"] in interpretation["conditions"]:
                matched_ids.append(event_id)

        if status == "partial":
            assessment["disposition"] = "incomplete"
            return assessment

        matched = len(matched_ids) >= interpretation["minMatches"]
        configured = interpretation[
            "whenMatched" if matched else "whenNotMatched"
        ]
        assessment.update(
            {
                "matchedEventCount": len(matched_ids),
                "matchedEventIds": matched_ids,
                "disposition": self._assessment_disposition(configured),
            }
        )
        return assessment

    @staticmethod
    def _matching_log_selections(
        spec: Mapping[str, object],
        root_cause: str | None,
    ) -> tuple[Mapping[str, object], ...]:
        selections = spec.get("logSelections", [])
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
    def _log_request(
        command: RunInvestigationCommand,
        selection: Mapping[str, object],
        scope: Mapping[str, object],
        started_at: str,
        budgets: Mapping[str, object],
    ) -> Mapping[str, object]:
        material = (
            f"{command.request['metadata']['id']}\x1f{selection['id']}".encode()
        )
        request_id = "leq_" + hashlib.sha256(material).hexdigest()[:32]
        max_wall_time = budgets["maxWallTimeSeconds"]
        if not isinstance(max_wall_time, int) or isinstance(max_wall_time, bool):
            raise InvalidInvestigationError("investigation.contract.invalid")
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "LogEvidenceRequest",
            "metadata": {
                "requestId": request_id,
                "tenantId": command.actor.tenant_id,
                "actorId": command.actor.actor_id,
                "requestedAt": started_at,
            },
            "spec": {
                "integrationId": selection["integrationId"],
                "resourceRefs": list(scope["resourceUids"]),
                "signal": "logs",
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
    def _log_unknown(selection_id: str) -> dict[str, object]:
        return {
            "statement": (
                "Selected log evidence could not be collected for "
                f"{selection_id}."
            ),
            "impact": "medium",
            "requestedEvidenceTypes": ["telemetry.logs"],
        }

    @staticmethod
    def _log_assessment_unknown(selection_id: str) -> dict[str, object]:
        return {
            "statement": (
                "Selected log evidence could not be safely assessed for "
                f"{selection_id}."
            ),
            "impact": "medium",
            "requestedEvidenceTypes": ["telemetry.logs"],
        }

    def _assess_logs(
        self,
        actor: ActorContext,
        selection: Mapping[str, object],
        evidence: Mapping[str, object],
        root_cause: str | None,
        log_request: Mapping[str, object],
    ) -> dict[str, object] | None:
        interpretation = selection.get("interpretation")
        if (
            self._evidence_store is None
            or root_cause is None
            or not isinstance(interpretation, Mapping)
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
        if (
            not isinstance(document, Mapping)
            or set(document) != {"apiVersion", "kind", "metadata", "spec"}
            or document.get("apiVersion") != "iip.platform/v1alpha1"
            or document.get("kind") != "LogEvidenceResult"
        ):
            return None
        artifact_metadata = document.get("metadata")
        artifact_spec = document.get("spec")
        request_metadata = log_request.get("metadata")
        request_spec = log_request.get("spec")
        if (
            not isinstance(artifact_metadata, Mapping)
            or set(artifact_metadata)
            != {"requestId", "tenantId", "integrationId", "createdAt"}
            or not isinstance(request_metadata, Mapping)
            or artifact_metadata.get("requestId") != request_metadata.get("requestId")
            or artifact_metadata.get("tenantId") != actor.tenant_id
            or artifact_metadata.get("integrationId") != selection.get("integrationId")
            or not isinstance(artifact_spec, Mapping)
            or set(artifact_spec)
            != {
                "signal",
                "requestDigest",
                "timeRange",
                "status",
                "records",
                "summary",
                "warnings",
            }
            or not isinstance(request_spec, Mapping)
            or artifact_spec.get("signal") != "logs"
            or artifact_spec.get("requestDigest") != canonical_digest(log_request)
            or artifact_spec.get("timeRange") != request_spec.get("timeRange")
        ):
            return None
        created_at = self._parse_datetime(artifact_metadata.get("createdAt"))
        deadline = self._parse_datetime(request_spec.get("deadline"))
        request_range = request_spec.get("timeRange")
        query = request_spec.get("query")
        limits = request_spec.get("limits")
        if (
            created_at is None
            or deadline is None
            or created_at > deadline
            or not isinstance(request_range, Mapping)
            or not isinstance(query, Mapping)
            or not isinstance(limits, Mapping)
        ):
            return None
        start = self._parse_datetime(request_range.get("start"))
        end = self._parse_datetime(request_range.get("end"))
        services = query.get("serviceNames")
        severities = query.get("severities")
        filters = query.get("filters")
        resource_refs = request_spec.get("resourceRefs")
        max_records = limits.get("maxRecords")
        records = artifact_spec.get("records")
        summary = artifact_spec.get("summary")
        warnings = artifact_spec.get("warnings")
        status = artifact_spec.get("status")
        if (
            start is None
            or end is None
            or not end <= created_at <= deadline
            or not isinstance(services, list)
            or not isinstance(severities, list)
            or not isinstance(filters, list)
            or not isinstance(resource_refs, list)
            or not isinstance(max_records, int)
            or isinstance(max_records, bool)
            or not isinstance(records, list)
            or len(records) > max_records
            or not isinstance(summary, Mapping)
            or set(summary) != {"recordCount", "errorCount"}
            or not isinstance(warnings, list)
            or len(warnings) > 8
            or any(not isinstance(warning, str) for warning in warnings)
            or len(warnings) != len(set(warnings))
            or any(warning not in {"backend-partial", "record-limit"} for warning in warnings)
            or status not in {"complete", "partial", "no-data"}
        ):
            return None

        record_ids: set[str] = set()
        previous_key: tuple[str, str] | None = None
        error_count = 0
        for record in records:
            if not self._valid_log_record(
                record,
                start=start,
                end=end,
                created_at=created_at,
                resource_refs=resource_refs,
                services=services,
                severities=severities,
                filters=filters,
                record_ids=record_ids,
                previous_key=previous_key,
            ):
                return None
            record_id = str(record["id"])
            record_ids.add(record_id)
            previous_key = (str(record["timestamp"]), record_id)
            if record.get("severity") in {"error", "fatal"}:
                error_count += 1
        if (
            not isinstance(summary.get("recordCount"), int)
            or isinstance(summary.get("recordCount"), bool)
            or not isinstance(summary.get("errorCount"), int)
            or isinstance(summary.get("errorCount"), bool)
            or summary.get("recordCount") != len(records)
            or summary.get("errorCount") != error_count
        ):
            return None

        assessment: dict[str, object] = {
            "selectionId": selection["id"],
            "evidenceId": evidence_id,
            "rootCauseClass": root_cause,
            "minRecords": interpretation["minRecords"],
        }
        if status == "no-data":
            if records or warnings:
                return None
            assessment["disposition"] = "no-data"
            return assessment
        if status == "partial":
            if not records or not warnings:
                return None
            assessment["disposition"] = "incomplete"
            return assessment
        if not records or warnings:
            return None
        observed_count = len(records)
        matched = observed_count >= interpretation["minRecords"]
        configured = interpretation[
            "whenMatched" if matched else "whenNotMatched"
        ]
        assessment["observedRecordCount"] = observed_count
        assessment["disposition"] = self._assessment_disposition(configured)
        return assessment

    @staticmethod
    def _valid_log_record(
        record: object,
        *,
        start: datetime,
        end: datetime,
        created_at: datetime,
        resource_refs: list[object],
        services: list[object],
        severities: list[object],
        filters: list[object],
        record_ids: set[str],
        previous_key: tuple[str, str] | None,
    ) -> bool:
        required = {
            "id",
            "resourceRef",
            "timestamp",
            "severity",
            "serviceName",
            "body",
            "attributes",
        }
        optional = {"observedTimestamp", "traceId", "spanId"}
        if (
            not isinstance(record, Mapping)
            or not required <= set(record)
            or set(record).difference(required | optional)
        ):
            return False
        record_id = record.get("id")
        timestamp = DeterministicInvestigationService._parse_datetime(
            record.get("timestamp")
        )
        observed = (
            DeterministicInvestigationService._parse_datetime(
                record.get("observedTimestamp")
            )
            if "observedTimestamp" in record
            else None
        )
        key = (str(record.get("timestamp")), str(record_id))
        attributes = record.get("attributes")
        severity = record.get("severity")
        trace_id = record.get("traceId")
        span_id = record.get("spanId")
        if (
            not isinstance(record_id, str)
            or not _LOG_RECORD_ID.fullmatch(record_id)
            or record_id in record_ids
            or record.get("resourceRef") not in resource_refs
            or timestamp is None
            or not start <= timestamp <= end
            or ("observedTimestamp" in record and observed is None)
            or (observed is not None and not timestamp <= observed <= created_at)
            or not isinstance(severity, str)
            or severity
            not in ("trace", "debug", "info", "warn", "error", "fatal", "unspecified")
            or (severities and severity not in severities)
            or record.get("serviceName") not in services
            or not DeterministicInvestigationService._safe_log_text(
                record.get("body"), maximum=4096, allow_formatting=True
            )
            or not isinstance(attributes, Mapping)
            or len(attributes) > 16
            or (trace_id is None) != (span_id is None)
            or (
                trace_id is not None
                and (
                    not isinstance(trace_id, str)
                    or not re.fullmatch(r"[a-f0-9]{32}", trace_id)
                )
            )
            or (
                span_id is not None
                and (
                    not isinstance(span_id, str)
                    or not re.fullmatch(r"[a-f0-9]{16}", span_id)
                )
            )
            or (previous_key is not None and key < previous_key)
        ):
            return False
        for name, value in attributes.items():
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:/-]{0,127}", name)
                or not DeterministicInvestigationService._safe_log_text(
                    value, maximum=256
                )
            ):
                return False
        return DeterministicInvestigationService._log_filters_match(
            attributes, filters
        )

    @staticmethod
    def _safe_log_text(
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
    def _log_filters_match(
        attributes: Mapping[object, object],
        filters: list[object],
    ) -> bool:
        for item in filters:
            if not isinstance(item, Mapping):
                return False
            name = item.get("attribute")
            operator = item.get("operator")
            expected = item.get("value")
            actual = attributes.get(name)
            if operator == "eq" and actual != expected:
                return False
            if operator == "neq" and (actual is None or actual == expected):
                return False
        return True

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
        event_selections = spec.get("kubernetesEventSelections", [])
        if not isinstance(event_selections, list) or len(event_selections) > 8:
            raise InvalidInvestigationError("investigation.contract.invalid")
        normalized_event_selections: list[dict[str, object]] = []
        event_selection_ids: set[str] = set()
        for value in event_selections:
            if (
                not isinstance(value, Mapping)
                or not {"id", "integrationId", "query", "limits"} <= set(value)
                or set(value).difference(
                    {
                        "id",
                        "integrationId",
                        "query",
                        "limits",
                        "rootCauseClasses",
                        "interpretation",
                    }
                )
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            event_selection_id = value.get("id")
            integration_id = value.get("integrationId")
            if (
                not isinstance(event_selection_id, str)
                or not _KUBERNETES_EVENT_SELECTION_ID.fullmatch(event_selection_id)
                or event_selection_id in event_selection_ids
                or not isinstance(integration_id, str)
                or not _INTEGRATION_ID.fullmatch(integration_id)
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            try:
                event_query, event_limits = (
                    KubernetesEventEvidenceService.validate_query_contract(
                        value.get("query"), value.get("limits")
                    )
                )
            except InvalidKubernetesEventEvidenceRequestError:
                raise InvalidInvestigationError(
                    "investigation.contract.invalid"
                ) from None
            event_classes = value.get("rootCauseClasses")
            if event_classes is not None and (
                not isinstance(event_classes, list)
                or not 1 <= len(event_classes) <= 16
                or any(
                    not isinstance(root_cause, str)
                    or not _ROOT_CAUSE_CLASS.fullmatch(root_cause)
                    for root_cause in event_classes
                )
                or len(event_classes) != len(set(event_classes))
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            event_interpretation = value.get("interpretation")
            normalized_event_interpretation = None
            if event_interpretation is not None:
                if event_classes is None:
                    raise InvalidInvestigationError(
                        "investigation.contract.invalid"
                    )
                normalized_event_interpretation = (
                    DeterministicInvestigationService._validate_kubernetes_event_interpretation(
                        event_interpretation,
                        maximum_matches=event_limits["maxEvents"],
                    )
                )
            normalized_event: dict[str, object] = {
                "id": event_selection_id,
                "integrationId": integration_id,
                "query": event_query,
                "limits": event_limits,
            }
            if event_classes is not None:
                normalized_event["rootCauseClasses"] = list(event_classes)
            if normalized_event_interpretation is not None:
                normalized_event["interpretation"] = normalized_event_interpretation
            normalized_event_selections.append(normalized_event)
            event_selection_ids.add(event_selection_id)
        spec["kubernetesEventSelections"] = normalized_event_selections

        context_selections = spec.get("contextSelections", [])
        if not isinstance(context_selections, list) or len(context_selections) > 8:
            raise InvalidInvestigationError("investigation.contract.invalid")
        normalized_context_selections: list[dict[str, object]] = []
        context_selection_ids: set[str] = set()
        for value in context_selections:
            if (
                not isinstance(value, Mapping)
                or not {"id", "integrationId", "query", "limits"} <= set(value)
                or set(value).difference(
                    {
                        "id",
                        "integrationId",
                        "query",
                        "limits",
                        "rootCauseClasses",
                        "interpretation",
                    }
                )
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            context_selection_id = value.get("id")
            integration_id = value.get("integrationId")
            if (
                not isinstance(context_selection_id, str)
                or not _CONTEXT_SELECTION_ID.fullmatch(context_selection_id)
                or context_selection_id in context_selection_ids
                or not isinstance(integration_id, str)
                or not _INTEGRATION_ID.fullmatch(integration_id)
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            try:
                context_query, context_limits = (
                    ContextEvidenceService.validate_query_contract(
                        value.get("query"), value.get("limits")
                    )
                )
            except InvalidContextEvidenceRequestError:
                raise InvalidInvestigationError(
                    "investigation.contract.invalid"
                ) from None
            context_classes = value.get("rootCauseClasses")
            if context_classes is not None and (
                not isinstance(context_classes, list)
                or not 1 <= len(context_classes) <= 16
                or any(
                    not isinstance(root_cause, str)
                    or not _ROOT_CAUSE_CLASS.fullmatch(root_cause)
                    for root_cause in context_classes
                )
                or len(context_classes) != len(set(context_classes))
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            context_interpretation = value.get("interpretation")
            normalized_context_interpretation = None
            if context_interpretation is not None:
                if context_classes is None:
                    raise InvalidInvestigationError(
                        "investigation.contract.invalid"
                    )
                normalized_context_interpretation = (
                    DeterministicInvestigationService._validate_context_interpretation(
                        context_interpretation,
                        maximum_documents=context_limits["maxDocuments"],
                    )
                )
            normalized_context: dict[str, object] = {
                "id": context_selection_id,
                "integrationId": integration_id,
                "query": context_query,
                "limits": context_limits,
            }
            if context_classes is not None:
                normalized_context["rootCauseClasses"] = list(context_classes)
            if normalized_context_interpretation is not None:
                normalized_context["interpretation"] = normalized_context_interpretation
            normalized_context_selections.append(normalized_context)
            context_selection_ids.add(context_selection_id)
        spec["contextSelections"] = normalized_context_selections

        change_selections = spec.get("changeSelections", [])
        if not isinstance(change_selections, list) or len(change_selections) > 8:
            raise InvalidInvestigationError("investigation.contract.invalid")
        normalized_change_selections: list[dict[str, object]] = []
        change_selection_ids: set[str] = set()
        for value in change_selections:
            if (
                not isinstance(value, Mapping)
                or not {"id", "integrationId", "query", "limits"} <= set(value)
                or set(value).difference(
                    {
                        "id",
                        "integrationId",
                        "query",
                        "limits",
                        "rootCauseClasses",
                        "interpretation",
                    }
                )
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            change_selection_id = value.get("id")
            integration_id = value.get("integrationId")
            if (
                not isinstance(change_selection_id, str)
                or not _CHANGE_SELECTION_ID.fullmatch(change_selection_id)
                or change_selection_id in change_selection_ids
                or not isinstance(integration_id, str)
                or not _INTEGRATION_ID.fullmatch(integration_id)
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            try:
                change_query, change_limits = (
                    ResourceChangeEvidenceService.validate_query_contract(
                        value.get("query"), value.get("limits")
                    )
                )
            except InvalidResourceChangeEvidenceRequestError:
                raise InvalidInvestigationError(
                    "investigation.contract.invalid"
                ) from None
            change_classes = value.get("rootCauseClasses")
            if change_classes is not None and (
                not isinstance(change_classes, list)
                or not 1 <= len(change_classes) <= 16
                or any(
                    not isinstance(root_cause, str)
                    or not _ROOT_CAUSE_CLASS.fullmatch(root_cause)
                    for root_cause in change_classes
                )
                or len(change_classes) != len(set(change_classes))
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            change_interpretation = value.get("interpretation")
            normalized_change_interpretation = None
            if change_interpretation is not None:
                if change_classes is None:
                    raise InvalidInvestigationError(
                        "investigation.contract.invalid"
                    )
                normalized_change_interpretation = (
                    DeterministicInvestigationService._validate_change_interpretation(
                        change_interpretation,
                        maximum_changes=change_limits["maxChanges"],
                    )
                )
            normalized_change: dict[str, object] = {
                "id": change_selection_id,
                "integrationId": integration_id,
                "query": change_query,
                "limits": change_limits,
            }
            if change_classes is not None:
                normalized_change["rootCauseClasses"] = list(change_classes)
            if normalized_change_interpretation is not None:
                normalized_change["interpretation"] = normalized_change_interpretation
            normalized_change_selections.append(normalized_change)
            change_selection_ids.add(change_selection_id)
        spec["changeSelections"] = normalized_change_selections

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

        log_selections = spec.get("logSelections", [])
        if not isinstance(log_selections, list) or len(log_selections) > 8:
            raise InvalidInvestigationError("investigation.contract.invalid")
        normalized_log_selections: list[dict[str, object]] = []
        log_selection_ids: set[str] = set()
        for value in log_selections:
            if (
                not isinstance(value, Mapping)
                or not {"id", "integrationId", "query", "limits"} <= set(value)
                or set(value).difference(
                    {
                        "id",
                        "integrationId",
                        "query",
                        "limits",
                        "rootCauseClasses",
                        "interpretation",
                    }
                )
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            log_selection_id = value.get("id")
            integration_id = value.get("integrationId")
            if (
                not isinstance(log_selection_id, str)
                or not _LOG_SELECTION_ID.fullmatch(log_selection_id)
                or log_selection_id in log_selection_ids
                or not isinstance(integration_id, str)
                or not _INTEGRATION_ID.fullmatch(integration_id)
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            try:
                log_query, log_limits = LogEvidenceService.validate_query_contract(
                    value.get("query"), value.get("limits")
                )
            except InvalidLogEvidenceRequestError:
                raise InvalidInvestigationError(
                    "investigation.contract.invalid"
                ) from None
            log_classes = value.get("rootCauseClasses")
            if log_classes is not None and (
                not isinstance(log_classes, list)
                or not 1 <= len(log_classes) <= 16
                or any(
                    not isinstance(root_cause, str)
                    or not _ROOT_CAUSE_CLASS.fullmatch(root_cause)
                    for root_cause in log_classes
                )
                or len(log_classes) != len(set(log_classes))
            ):
                raise InvalidInvestigationError("investigation.contract.invalid")
            log_interpretation = value.get("interpretation")
            normalized_log_interpretation = None
            if log_interpretation is not None:
                if log_classes is None:
                    raise InvalidInvestigationError(
                        "investigation.contract.invalid"
                    )
                normalized_log_interpretation = (
                    DeterministicInvestigationService._validate_log_interpretation(
                        log_interpretation,
                        maximum_records=log_limits["maxRecords"],
                    )
                )
            normalized_log: dict[str, object] = {
                "id": log_selection_id,
                "integrationId": integration_id,
                "query": log_query,
                "limits": log_limits,
            }
            if log_classes is not None:
                normalized_log["rootCauseClasses"] = list(log_classes)
            if normalized_log_interpretation is not None:
                normalized_log["interpretation"] = normalized_log_interpretation
            normalized_log_selections.append(normalized_log)
            log_selection_ids.add(log_selection_id)
        spec["logSelections"] = normalized_log_selections
        return request, metadata, spec, scope, budgets

    @staticmethod
    def _validate_log_interpretation(
        value: object,
        *,
        maximum_records: int,
    ) -> dict[str, object]:
        if not isinstance(value, Mapping) or set(value) != {
            "minRecords",
            "whenMatched",
            "whenNotMatched",
        }:
            raise InvalidInvestigationError("investigation.contract.invalid")
        min_records = value.get("minRecords")
        when_matched = value.get("whenMatched")
        when_not_matched = value.get("whenNotMatched")
        if (
            not isinstance(min_records, int)
            or isinstance(min_records, bool)
            or not 1 <= min_records <= maximum_records
            or when_matched not in _INTERPRETATION_DISPOSITIONS
            or when_not_matched not in _INTERPRETATION_DISPOSITIONS
            or when_matched == when_not_matched
        ):
            raise InvalidInvestigationError("investigation.contract.invalid")
        return dict(value)

    @staticmethod
    def _validate_change_interpretation(
        value: object,
        *,
        maximum_changes: int,
    ) -> dict[str, object]:
        if not isinstance(value, Mapping) or set(value) != {
            "minChanges",
            "whenMatched",
            "whenNotMatched",
        }:
            raise InvalidInvestigationError("investigation.contract.invalid")
        minimum = value.get("minChanges")
        when_matched = value.get("whenMatched")
        when_not_matched = value.get("whenNotMatched")
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not 1 <= minimum <= maximum_changes
            or when_matched not in _INTERPRETATION_DISPOSITIONS
            or when_not_matched not in _INTERPRETATION_DISPOSITIONS
            or when_matched == when_not_matched
        ):
            raise InvalidInvestigationError("investigation.contract.invalid")
        return dict(value)

    @staticmethod
    def _validate_context_interpretation(
        value: object,
        *,
        maximum_documents: int,
    ) -> dict[str, object]:
        if not isinstance(value, Mapping) or set(value) != {
            "minDocuments",
            "whenMatched",
            "whenNotMatched",
        }:
            raise InvalidInvestigationError("investigation.contract.invalid")
        minimum = value.get("minDocuments")
        when_matched = value.get("whenMatched")
        when_not_matched = value.get("whenNotMatched")
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not 1 <= minimum <= maximum_documents
            or when_matched not in _INTERPRETATION_DISPOSITIONS
            or when_not_matched not in _INTERPRETATION_DISPOSITIONS
            or when_matched == when_not_matched
        ):
            raise InvalidInvestigationError("investigation.contract.invalid")
        return dict(value)

    @staticmethod
    def _validate_kubernetes_event_interpretation(
        value: object,
        *,
        maximum_matches: int,
    ) -> dict[str, object]:
        if not isinstance(value, Mapping) or set(value) != {
            "conditions",
            "minMatches",
            "whenMatched",
            "whenNotMatched",
        }:
            raise InvalidInvestigationError("investigation.contract.invalid")
        conditions = value.get("conditions")
        min_matches = value.get("minMatches")
        when_matched = value.get("whenMatched")
        when_not_matched = value.get("whenNotMatched")
        if (
            not isinstance(conditions, list)
            or not 1 <= len(conditions) <= 16
            or any(
                not isinstance(condition, str)
                or not _ROOT_CAUSE_CLASS.fullmatch(condition)
                for condition in conditions
            )
            or len(conditions) != len(set(conditions))
            or not isinstance(min_matches, int)
            or isinstance(min_matches, bool)
            or not 1 <= min_matches <= maximum_matches
            or when_matched not in _INTERPRETATION_DISPOSITIONS
            or when_not_matched not in _INTERPRETATION_DISPOSITIONS
            or when_matched == when_not_matched
        ):
            raise InvalidInvestigationError("investigation.contract.invalid")
        return dict(value)

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
