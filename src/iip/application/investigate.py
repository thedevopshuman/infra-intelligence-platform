"""Bounded deterministic investigation reference runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

from iip.application.collect_evidence import CollectEvidenceCommand, EvidenceCollectionService
from iip.application.ports import ActorContext, Clock, InvestigationRepository, ResourceRepository


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
    ) -> None:
        self._resources = resources
        self._evidence = evidence
        self._investigations = investigations
        self._clock = clock

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
        evidence_document: Mapping[str, object] | None = None
        allowed_tools = spec.get("allowedTools", [])
        tools_allow_collection = not allowed_tools or "evidence/fetch" in allowed_tools
        if (
            budgets["maxToolCalls"] > 0
            and budgets["maxEvidenceItems"] > 0
            and tools_allow_collection
        ):
            requested_types = spec.get("evidenceTypes", [])
            evidence_type = (
                "kubernetes.pod-status"
                if "kubernetes.pod-status" in requested_types
                else requested_types[0]
                if requested_types
                else "kubernetes.resource-status"
            )
            evidence_document = self._evidence.execute(
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

        completed_at = self._clock.now()
        evidence_ids = (
            [evidence_document["metadata"]["id"]]
            if evidence_document is not None
            else []
        )
        root_cause, statement, confidence = self._classify(resources)
        if evidence_document is None:
            outcome = "inconclusive"
            terminal_reason = "budget-exhausted"
            summary = "The investigation budget prohibited evidence collection."
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
                    "requestedEvidenceTypes": ["kubernetes.event", "kubernetes.pod-log"],
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
                    "supportingEvidenceIds": evidence_ids,
                    "contradictingEvidenceIds": [],
                }
            ]
            unknowns = []

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
                    investigation_id, root_cause, evidence_ids
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
                    "toolCalls": 1 if evidence_document is not None else 0,
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
        if not isinstance(scope.get("resourceUids"), list) or not scope["resourceUids"]:
            raise InvalidInvestigationError("investigation.contract.invalid")
        for name in (
            "maxToolCalls",
            "maxWallTimeSeconds",
            "maxModelTokens",
            "maxEvidenceItems",
            "maxIterations",
        ):
            if isinstance(budgets.get(name), bool) or not isinstance(budgets.get(name), int):
                raise InvalidInvestigationError("investigation.contract.invalid")
        return request, metadata, spec, scope, budgets
