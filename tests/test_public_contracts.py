from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

from infra_intelligence_sdk import (
    ActionApproval,
    ActionProposal,
    ActionResult,
    Evidence,
    EvaluationScenario,
    InvestigationReport,
    InvestigationRequest,
    IntegrationConfig,
    PluginSession,
    ResourceCollectionRequest,
    ResourceCollectionResult,
    ResourceNeighborhood,
    ResourceObservation,
    ResourceTimeline,
)


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_repo  # noqa: E402


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8"))


class PublicContractSdkTests(unittest.TestCase):
    def test_sdk_models_accept_public_contract_examples(self) -> None:
        evidence = Evidence.from_dict(example("evidence.json"))
        request = InvestigationRequest.from_dict(example("investigation-request.json"))
        report = InvestigationReport.from_dict(example("investigation-report.json"))
        collection_request = ResourceCollectionRequest.from_dict(
            example("resource-collection-request.json")
        )
        collection_result = ResourceCollectionResult.from_dict(
            example("resource-collection-result.json")
        )
        tombstone = ResourceObservation.from_dict(example("resource-tombstone.json"))
        neighborhood = ResourceNeighborhood.from_dict(
            example("resource-neighborhood.json")
        )
        timeline = ResourceTimeline.from_dict(example("resource-timeline.json"))
        scenario = EvaluationScenario.from_dict(example("evaluation-scenario.json"))
        integration = IntegrationConfig.from_dict(example("integration-config.json"))
        proposal = ActionProposal.from_dict(example("action-proposal.json"))
        approval = ActionApproval.from_dict(example("action-approval.json"))
        action_result = ActionResult.from_dict(example("action-result.json"))
        plugin_session = PluginSession.from_dict(example("plugin-session.json"))

        self.assertEqual(evidence.to_dict()["kind"], "Evidence")
        self.assertEqual(request.to_dict()["kind"], "InvestigationRequest")
        self.assertEqual(report.to_dict()["kind"], "InvestigationReport")
        self.assertEqual(collection_request.to_dict()["kind"], "ResourceCollectionRequest")
        self.assertEqual(collection_result.to_dict()["kind"], "ResourceCollectionResult")
        self.assertIsNone(collection_request.resume)
        self.assertEqual(
            collection_result.provider_cursors,
            {
                "/apis/apps/v1/namespaces/default/deployments": "398712"
            },
        )
        self.assertEqual(tombstone.to_dict()["status"]["lifecycle"], "deleted")
        self.assertEqual(neighborhood.to_dict()["kind"], "ResourceNeighborhood")
        self.assertEqual(timeline.to_dict()["kind"], "ResourceTimeline")
        self.assertEqual(scenario.to_dict()["kind"], "EvaluationScenario")
        self.assertEqual(integration.to_dict()["kind"], "IntegrationConfig")
        self.assertEqual(proposal.to_dict()["kind"], "ActionProposal")
        self.assertEqual(approval.to_dict()["kind"], "ActionApproval")
        self.assertEqual(action_result.to_dict()["kind"], "ActionResult")
        self.assertEqual(plugin_session.to_dict()["kind"], "PluginSession")

    def test_sdk_models_reject_crossed_contract_kinds(self) -> None:
        with self.assertRaisesRegex(ValueError, "kind must be Evidence"):
            Evidence.from_dict(example("investigation-report.json"))
        with self.assertRaisesRegex(ValueError, "kind must be InvestigationRequest"):
            InvestigationRequest.from_dict(example("evidence.json"))
        with self.assertRaisesRegex(ValueError, "kind must be InvestigationReport"):
            InvestigationReport.from_dict(example("investigation-request.json"))
        with self.assertRaisesRegex(ValueError, "kind must be EvaluationScenario"):
            EvaluationScenario.from_dict(example("evidence.json"))

    def test_sdk_models_reject_unknown_versions(self) -> None:
        payload = example("investigation-request.json")
        payload["apiVersion"] = "iip.platform/v2"
        with self.assertRaisesRegex(ValueError, "unsupported investigation request apiVersion"):
            InvestigationRequest.from_dict(payload)

        scenario = example("evaluation-scenario.json")
        scenario["apiVersion"] = "iip.platform/v2"
        with self.assertRaisesRegex(ValueError, "unsupported evaluation scenario apiVersion"):
            EvaluationScenario.from_dict(scenario)


class PublicContractRepositoryValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.errors: list[str] = []
        self.documents = dict(validate_repo.load_json_documents(self.errors))
        self.assertEqual(self.errors, [])

    def test_examples_satisfy_cross_contract_invariants(self) -> None:
        validate_repo.validate_examples(self.documents, self.errors)
        self.assertEqual(self.errors, [])

    def test_report_rejects_citation_outside_evidence_set(self) -> None:
        report_path = ROOT / "contracts" / "examples" / "investigation-report.json"
        report = copy.deepcopy(self.documents[report_path])
        report["spec"]["hypotheses"][0]["supportingEvidenceIds"].append(
            "evd_00000000000000000000000000000000"
        )
        self.documents[report_path] = report

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "investigation report contains citations outside its evidenceIds set",
            self.errors,
        )

    def test_report_rejects_modified_request_digest(self) -> None:
        request_path = ROOT / "contracts" / "examples" / "investigation-request.json"
        request = copy.deepcopy(self.documents[request_path])
        request["spec"]["question"] = "A different question"
        self.documents[request_path] = request

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "investigation report requestDigest must match the canonical request example",
            self.errors,
        )

    def test_report_rejects_usage_above_request_budget(self) -> None:
        report_path = ROOT / "contracts" / "examples" / "investigation-report.json"
        report = copy.deepcopy(self.documents[report_path])
        report["spec"]["usage"]["toolCalls"] = 25
        self.documents[report_path] = report

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "investigation report toolCalls exceeds request maxToolCalls",
            self.errors,
        )

    def test_report_rejects_inconsistent_terminal_reason(self) -> None:
        report_path = ROOT / "contracts" / "examples" / "investigation-report.json"
        report = copy.deepcopy(self.documents[report_path])
        report["spec"]["terminalReason"] = "runtime-error"
        self.documents[report_path] = report

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "investigation report outcome and terminalReason are inconsistent",
            self.errors,
        )

    def test_scenario_rejects_unresolved_required_evidence(self) -> None:
        path = ROOT / "contracts" / "examples" / "evaluation-scenario.json"
        scenario = copy.deepcopy(self.documents[path])
        scenario["spec"]["expectations"]["requiredEvidenceIds"].append(
            "evd_00000000000000000000000000000000"
        )
        self.documents[path] = scenario

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "evaluation scenario expected evidence IDs must resolve",
            self.errors,
        )

    def test_scenario_rejects_cross_tenant_evidence(self) -> None:
        path = ROOT / "contracts" / "examples" / "evaluation-scenario.json"
        scenario = copy.deepcopy(self.documents[path])
        scenario["spec"]["fixtures"]["evidence"][0]["metadata"][
            "tenantId"
        ] = "another-tenant"
        self.documents[path] = scenario

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "evaluation scenario evidence must share its tenant",
            self.errors,
        )

    def test_scenario_rejects_current_graph_history_drift(self) -> None:
        path = ROOT / "contracts" / "examples" / "evaluation-scenario.json"
        scenario = copy.deepcopy(self.documents[path])
        scenario["spec"]["fixtures"]["graph"]["resources"][0]["spec"][
            "attributes"
        ]["availableReplicas"] = 1
        self.documents[path] = scenario

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "evaluation scenario current graph must match latest accepted history",
            self.errors,
        )

    def test_scenario_rejects_scoring_weights_above_100(self) -> None:
        path = ROOT / "contracts" / "examples" / "evaluation-scenario.json"
        scenario = copy.deepcopy(self.documents[path])
        scenario["spec"]["scoring"]["weights"]["rootCause"] = 36
        self.documents[path] = scenario

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "evaluation scenario scoring weights must sum to 100",
            self.errors,
        )

    def test_scenario_rejects_forbidden_evidence_in_request_scope(self) -> None:
        path = ROOT / "contracts" / "examples" / "evaluation-scenario.json"
        scenario = copy.deepcopy(self.documents[path])
        scenario["spec"]["request"]["spec"]["evidenceTypes"].append(
            "kubernetes.secret"
        )
        self.documents[path] = scenario

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "evaluation scenario forbidden evidence must not be exposed",
            self.errors,
        )


if __name__ == "__main__":
    unittest.main()
