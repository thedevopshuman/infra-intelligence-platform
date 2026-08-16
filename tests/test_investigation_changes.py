from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from iip.application.ingest_resource import IngestResourceCommand
from iip.application.investigate import (
    InvalidInvestigationError,
    RunInvestigationCommand,
)
from iip.application.ports import ActorContext
from iip.bootstrap import build_local_runtime
from infra_intelligence_sdk import InvestigationReport, InvestigationRequest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class InvestigationChangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = ActorContext("developer", "local")
        self.runtime = build_local_runtime()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.uid = self._ingest(1, "registry.example/api:1.4.0", minutes_ago=4)
        self._ingest(2, "registry.example/api:1.5.0", minutes_ago=2)

    def _ingest(self, sequence: int, image: str, *, minutes_ago: int) -> str:
        resource = copy.deepcopy(example("resource.json"))
        resource["metadata"]["observedAt"] = iso(
            self.now - timedelta(minutes=minutes_ago)
        )
        resource["metadata"]["observation"].update(
            {
                "sequence": sequence,
                "resourceVersion": str(sequence),
                "checkpoint": f"investigation-change:{sequence}",
            }
        )
        resource["spec"]["attributes"].update(
            {
                "image": image,
                "replicas": 3,
                "availableReplicas": 2,
            }
        )
        resource["status"] = {"health": "degraded", "lifecycle": "active"}
        stored = self.runtime.ingestion.execute(
            IngestResourceCommand(self.actor, resource)
        )
        return stored.identity.uid

    def request(self, marker: str = "a") -> dict:
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "InvestigationRequest",
            "metadata": {
                "id": "inv_" + marker * 32,
                "tenantId": self.actor.tenant_id,
                "actorId": self.actor.actor_id,
                "requestedAt": iso(self.now),
            },
            "spec": {
                "question": "Did a rollout change precede the unavailable replica?",
                "trigger": {
                    "type": "alert",
                    "source": "urn:iip:test:alert",
                    "summary": "One replica is unavailable.",
                },
                "scope": {
                    "resourceUids": [self.uid],
                    "timeRange": {
                        "start": iso(self.now - timedelta(minutes=5)),
                        "end": iso(self.now),
                    },
                },
                "evidenceTypes": ["kubernetes.pod-status", "resource.change"],
                "allowedTools": ["evidence/fetch"],
                "changeSelections": [
                    {
                        "id": "cqs_0123456789abcdef",
                        "integrationId": "platform-resource-history",
                        "rootCauseClasses": [
                            "kubernetes.rollout.unavailable-replicas"
                        ],
                        "query": {"changeKinds": ["image"]},
                        "limits": {
                            "maxChanges": 10,
                            "maxObservationsPerResource": 100,
                            "maxBytes": 262144,
                        },
                        "interpretation": {
                            "minChanges": 1,
                            "whenMatched": "supports",
                            "whenNotMatched": "contradicts",
                        },
                    }
                ],
                "budgets": {
                    "maxToolCalls": 4,
                    "maxWallTimeSeconds": 60,
                    "maxModelTokens": 0,
                    "maxCostUsd": 0,
                    "maxEvidenceItems": 4,
                    "maxIterations": 1,
                },
                "maxAuthority": "read",
            },
        }

    def test_investigation_assesses_committed_change_without_values(self) -> None:
        request = self.request()
        report = self.runtime.investigations.execute(
            RunInvestigationCommand(self.actor, request)
        )
        replay = self.runtime.investigations.execute(
            RunInvestigationCommand(self.actor, request)
        )

        self.assertEqual(report, replay)
        assessment = report["spec"]["changeAssessments"][0]
        self.assertEqual(assessment["disposition"], "supporting")
        self.assertEqual(assessment["observedChangeCount"], 1)
        self.assertEqual(len(assessment["observedChangeIds"]), 1)
        self.assertIn(
            assessment["evidenceId"],
            report["spec"]["hypotheses"][0]["supportingEvidenceIds"],
        )
        artifact = self.runtime.evidence_store.read_artifact(
            self.actor, assessment["evidenceId"]
        )
        self.assertNotIn(b"registry.example", artifact or b"")
        self.assertEqual(report["spec"]["usage"]["toolCalls"], 2)
        self.assertEqual(report["spec"]["usage"]["evidenceItems"], 2)
        schema = json.loads(
            (ROOT / "contracts/schemas/investigation-report.schema.json").read_text()
        )
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                schema, report, label="investigation change report"
            ),
            [],
        )
        sdk_request = InvestigationRequest.from_dict(request)
        sdk_report = InvestigationReport.from_dict(report)
        self.assertEqual(sdk_request.change_selections[0].interpretation.min_changes, 1)
        self.assertEqual(
            sdk_report.change_assessments[0].observed_change_count,
            1,
        )

    def test_no_data_and_partial_changes_never_become_citations(self) -> None:
        no_data = self.request("b")
        no_data["spec"]["changeSelections"][0]["query"] = {
            "changeKinds": ["relationships"]
        }
        no_data_report = self.runtime.investigations.execute(
            RunInvestigationCommand(self.actor, no_data)
        )
        no_data_assessment = no_data_report["spec"]["changeAssessments"][0]
        self.assertEqual(no_data_assessment["disposition"], "no-data")
        self.assertNotIn("observedChangeCount", no_data_assessment)

        self._ingest(3, "registry.example/api:1.6.0", minutes_ago=1)
        partial = self.request("c")
        partial["spec"]["changeSelections"][0]["limits"][
            "maxObservationsPerResource"
        ] = 2
        partial_report = self.runtime.investigations.execute(
            RunInvestigationCommand(self.actor, partial)
        )
        partial_assessment = partial_report["spec"]["changeAssessments"][0]
        self.assertEqual(partial_assessment["disposition"], "incomplete")
        self.assertNotIn("observedChangeCount", partial_assessment)
        for candidate, report in (
            (no_data_assessment, no_data_report),
            (partial_assessment, partial_report),
        ):
            self.assertNotIn(
                candidate["evidenceId"],
                report["spec"]["hypotheses"][0]["supportingEvidenceIds"],
            )

    def test_invalid_change_rule_fails_before_evidence_collection(self) -> None:
        request = self.request("d")
        request["spec"]["changeSelections"][0]["interpretation"][
            "minChanges"
        ] = 11

        with self.assertRaisesRegex(
            InvalidInvestigationError,
            "investigation.contract.invalid",
        ):
            self.runtime.investigations.execute(
                RunInvestigationCommand(self.actor, request)
            )
        self.assertEqual(self.runtime.evidence_store.list(self.actor), ())


if __name__ == "__main__":
    unittest.main()
