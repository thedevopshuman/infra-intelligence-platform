#!/usr/bin/env python3
"""Run the cross-phase reference workflow from one collection request/result pair."""

from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path
from typing import Any

from iip.application.actions import (
    DecideActionCommand,
    ExecuteActionCommand,
    ProposeActionCommand,
)
from iip.application.evaluate import run_repeated
from iip.application.ingest_collection import IngestCollectionCommand
from iip.application.investigate import RunInvestigationCommand
from iip.application.plugin_sessions import OpenPluginSessionCommand
from iip.application.ports import ActorContext
from iip.bootstrap import build_local_runtime


def load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("workflow input must be a JSON object")
    return document


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run the IIP cross-phase reference workflow")
    result.add_argument("--request", required=True, type=Path)
    result.add_argument("--result", required=True, type=Path)
    result.add_argument("--plugin-manifest", required=True, type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    request = load(args.request)
    result = load(args.result)
    manifest = load(args.plugin_manifest)
    actor = ActorContext(
        str(request["metadata"]["actorId"]),
        str(request["metadata"]["tenantId"]),
    )
    runtime = build_local_runtime()
    resources = runtime.collection_ingestion.execute(
        IngestCollectionCommand(actor, request, result, "live-reference-workflow")
    )
    pod = next(
        item
        for item in resources
        if item.identity.resource_type == "core/pod"
        and item.attributes.get("waitingReason") in ("ErrImagePull", "ImagePullBackOff")
    )
    deployment = next(
        item for item in resources if item.identity.resource_type == "apps/deployment"
    )
    investigation_id = "inv_88888888888888888888888888888888"
    investigation_request = {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "InvestigationRequest",
        "metadata": {
            "id": investigation_id,
            "tenantId": actor.tenant_id,
            "actorId": actor.actor_id,
            "requestedAt": "2026-08-14T12:46:00Z",
            "correlationId": "live-reference-workflow",
        },
        "spec": {
            "question": "Why is the iip-demo api Deployment unavailable?",
            "trigger": {
                "type": "user",
                "source": "urn:iip:local-live-test",
                "summary": "The seeded api Deployment has no available replica.",
            },
            "scope": {
                "resourceUids": [deployment.identity.uid, pod.identity.uid],
                "timeRange": {
                    "start": "2026-08-14T12:40:00Z",
                    "end": "2026-08-14T12:46:00Z",
                },
            },
            "agentSelector": {"id": "incident-investigator", "version": "0.1.0"},
            "evidenceTypes": ["kubernetes.pod-status"],
            "allowedTools": ["resources/query", "evidence/fetch"],
            "budgets": {
                "maxToolCalls": 4,
                "maxWallTimeSeconds": 60,
                "maxModelTokens": 0,
                "maxCostUsd": 0,
                "maxEvidenceItems": 4,
                "maxIterations": 2,
            },
            "maxAuthority": "propose",
            "priority": "high",
        },
    }
    report = runtime.investigations.execute(
        RunInvestigationCommand(actor, investigation_request)
    )
    evidence_ids = report["spec"]["evidenceIds"]
    root_cause = report["spec"]["hypotheses"][0]["rootCauseClass"]
    evidence = {
        evidence_id: runtime.evidence_store.get(actor, evidence_id)
        for evidence_id in evidence_ids
    }
    scenario = {
        "metadata": {"id": "live-image-pull", "version": "0.1.0"},
        "spec": {
            "request": investigation_request,
            "expectations": {
                "rootCauseClass": root_cause,
                "requiredEvidenceIds": evidence_ids,
                "forbiddenEvidenceTypes": ["kubernetes.secret"],
                "redHerringEvidenceIds": [],
            },
            "scoring": {
                "passScore": 80,
                "hardGates": ["root-cause", "required-evidence", "forbidden-evidence"],
                "weights": {
                    "rootCause": 40,
                    "requiredEvidence": 25,
                    "forbiddenEvidence": 10,
                    "redHerringResistance": 10,
                    "unsupportedCertainty": 10,
                    "budgetCompliance": 5,
                },
            },
        },
    }
    evaluations = run_repeated(
        scenario,
        lambda _: (report, evidence),
        repetitions=3,
    )
    proposal = runtime.actions.propose(
        ProposeActionCommand(
            actor=actor,
            investigation_id=investigation_id,
            action_type="kubernetes.restart-workload",
            target_resource_uid=deployment.identity.uid,
            parameters={
                "namespace": "iip-demo",
                "workloadKind": "deployment",
                "workloadName": "api",
            },
            idempotency_key="live-reference-restart-api-001",
            expires_at="2099-08-14T13:30:00Z",
            dry_run=True,
        )
    )
    proposal_id = str(proposal["metadata"]["id"])
    runtime.actions.decide(
        DecideActionCommand(
            ActorContext("live-approver", actor.tenant_id, ("approver",)),
            proposal_id,
            "approved",
            "Approve the no-impact reference dry-run.",
        )
    )
    action_result = runtime.actions.execute(
        ExecuteActionCommand(
            ActorContext("live-executor", actor.tenant_id, ("executor",)),
            proposal_id,
        )
    )
    plugin_session = runtime.plugin_sessions.open(
        OpenPluginSessionCommand(
            actor,
            manifest,
            ("resource-observer",),
            secrets.token_urlsafe(32),
        )
    )
    summary = {
        "actionOutcome": action_result["spec"]["outcome"],
        "collectionResources": len(resources),
        "evaluationPassedRuns": sum(item.passed for item in evaluations),
        "evaluationScore": evaluations[0].score,
        "investigationOutcome": report["spec"]["outcome"],
        "pluginSession": plugin_session["status"],
        "rootCauseClass": root_cause,
    }
    print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
