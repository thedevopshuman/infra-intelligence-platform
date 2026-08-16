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
    InvestigationKubernetesEventAssessment,
    InvestigationKubernetesEventInterpretation,
    InvestigationKubernetesEventSelection,
    InvestigationLogAssessment,
    InvestigationLogInterpretation,
    InvestigationLogSelection,
    InvestigationTelemetryAssessment,
    InvestigationTelemetryBaselineAssessment,
    InvestigationTelemetryBaselineComparison,
    InvestigationTelemetryInterpretation,
    InvestigationTelemetrySelection,
    IngestionFreshnessReport,
    IntegrationConfig,
    KubernetesEventEvidenceRequest,
    KubernetesEventEvidenceResult,
    LogEvidenceRequest,
    LogEvidenceResult,
    OtlpLogsEvidence,
    OtlpMetricsEvidence,
    PluginSession,
    ResourceCollectionRequest,
    ResourceCollectionResult,
    ResourceNeighborhood,
    ResourceObservation,
    ResourceTimeline,
    SessionContext,
    TelemetryEvidenceRequest,
    TelemetryEvidenceResult,
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
        telemetry_investigation = InvestigationRequest.from_dict(
            example("investigation-request-telemetry.json")
        )
        baseline_investigation = InvestigationRequest.from_dict(
            example("investigation-request-telemetry-baseline.json")
        )
        event_investigation = InvestigationRequest.from_dict(
            example("investigation-request-kubernetes-events.json")
        )
        log_investigation = InvestigationRequest.from_dict(
            example("investigation-request-logs.json")
        )
        report = InvestigationReport.from_dict(example("investigation-report.json"))
        telemetry_report = InvestigationReport.from_dict(
            example("investigation-report-telemetry.json")
        )
        baseline_report = InvestigationReport.from_dict(
            example("investigation-report-telemetry-baseline.json")
        )
        event_report = InvestigationReport.from_dict(
            example("investigation-report-kubernetes-events.json")
        )
        log_report = InvestigationReport.from_dict(
            example("investigation-report-logs.json")
        )
        freshness = IngestionFreshnessReport.from_dict(
            example("ingestion-freshness-report.json")
        )
        session = SessionContext.from_dict(example("session-context.json"))
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
        telemetry_request = TelemetryEvidenceRequest.from_dict(
            example("telemetry-evidence-request.json")
        )
        telemetry_result = TelemetryEvidenceResult.from_dict(
            example("telemetry-evidence-result.json")
        )
        event_request = KubernetesEventEvidenceRequest.from_dict(
            example("kubernetes-event-evidence-request.json")
        )
        event_result = KubernetesEventEvidenceResult.from_dict(
            example("kubernetes-event-evidence-result.json")
        )
        otlp_metrics = OtlpMetricsEvidence.from_dict(
            example("otlp-metrics-evidence.json")
        )
        log_request = LogEvidenceRequest.from_dict(example("log-evidence-request.json"))
        log_result = LogEvidenceResult.from_dict(example("log-evidence-result.json"))
        otlp_logs = OtlpLogsEvidence.from_dict(example("otlp-logs-evidence.json"))

        self.assertEqual(evidence.to_dict()["kind"], "Evidence")
        self.assertEqual(request.to_dict()["kind"], "InvestigationRequest")
        selection = telemetry_investigation.telemetry_selections[0]
        self.assertIsInstance(selection, InvestigationTelemetrySelection)
        self.assertIsInstance(
            selection.interpretation,
            InvestigationTelemetryInterpretation,
        )
        self.assertEqual(selection.integration_id, "observability-local")
        self.assertEqual(selection.to_dict()["query"]["metric"], "service.request.error_ratio")
        assessment = telemetry_report.telemetry_assessments[0]
        self.assertIsInstance(assessment, InvestigationTelemetryAssessment)
        self.assertEqual(assessment.disposition, "supporting")
        self.assertEqual(assessment.to_dict()["observedValue"], 0.082)
        baseline_selection = baseline_investigation.telemetry_selections[0]
        self.assertIsInstance(
            baseline_selection.baseline_comparison,
            InvestigationTelemetryBaselineComparison,
        )
        self.assertIsNone(baseline_selection.interpretation)
        self.assertEqual(
            baseline_selection.baseline_comparison.calculation,
            "ratio",
        )
        baseline_assessment = baseline_report.telemetry_assessments[0]
        self.assertIsInstance(
            baseline_assessment,
            InvestigationTelemetryBaselineAssessment,
        )
        self.assertEqual(baseline_assessment.comparison_value, 8.2)
        self.assertEqual(
            baseline_assessment.to_dict()["assessmentType"],
            "baseline-comparison",
        )
        event_selection = event_investigation.kubernetes_event_selections[0]
        self.assertIsInstance(event_selection, InvestigationKubernetesEventSelection)
        self.assertIsInstance(
            event_selection.interpretation,
            InvestigationKubernetesEventInterpretation,
        )
        event_assessment = event_report.kubernetes_event_assessments[0]
        self.assertIsInstance(
            event_assessment,
            InvestigationKubernetesEventAssessment,
        )
        self.assertEqual(event_assessment.matched_event_count, 1)
        self.assertEqual(event_assessment.disposition, "supporting")
        log_selection = log_investigation.log_selections[0]
        self.assertIsInstance(log_selection, InvestigationLogSelection)
        self.assertIsInstance(
            log_selection.interpretation,
            InvestigationLogInterpretation,
        )
        self.assertEqual(log_selection.interpretation.min_records, 2)
        log_assessment = log_report.log_assessments[0]
        self.assertIsInstance(log_assessment, InvestigationLogAssessment)
        self.assertEqual(log_assessment.observed_record_count, 2)
        self.assertEqual(log_assessment.disposition, "supporting")
        self.assertEqual(report.to_dict()["kind"], "InvestigationReport")
        self.assertEqual(freshness.to_dict()["kind"], "IngestionFreshnessReport")
        self.assertEqual(session.to_dict()["kind"], "SessionContext")
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
        self.assertEqual(
            telemetry_request.to_dict()["kind"], "TelemetryEvidenceRequest"
        )
        self.assertEqual(
            telemetry_result.to_dict()["kind"], "TelemetryEvidenceResult"
        )
        self.assertEqual(
            event_request.to_dict()["kind"], "KubernetesEventEvidenceRequest"
        )
        self.assertEqual(
            event_result.to_dict()["kind"], "KubernetesEventEvidenceResult"
        )
        self.assertEqual(otlp_metrics.to_dict()["kind"], "OtlpMetricsEvidence")
        self.assertEqual(log_request.to_dict()["kind"], "LogEvidenceRequest")
        self.assertEqual(log_result.to_dict()["kind"], "LogEvidenceResult")
        self.assertEqual(otlp_logs.to_dict()["kind"], "OtlpLogsEvidence")

    def test_sdk_models_reject_crossed_contract_kinds(self) -> None:
        with self.assertRaisesRegex(ValueError, "kind must be Evidence"):
            Evidence.from_dict(example("investigation-report.json"))
        with self.assertRaisesRegex(ValueError, "kind must be InvestigationRequest"):
            InvestigationRequest.from_dict(example("evidence.json"))
        with self.assertRaisesRegex(ValueError, "kind must be InvestigationReport"):
            InvestigationReport.from_dict(example("investigation-request.json"))
        with self.assertRaisesRegex(ValueError, "kind must be EvaluationScenario"):
            EvaluationScenario.from_dict(example("evidence.json"))
        with self.assertRaisesRegex(
            ValueError, "kind must be IngestionFreshnessReport"
        ):
            IngestionFreshnessReport.from_dict(example("investigation-report.json"))
        with self.assertRaisesRegex(
            ValueError, "kind must be TelemetryEvidenceRequest"
        ):
            TelemetryEvidenceRequest.from_dict(
                example("telemetry-evidence-result.json")
            )
        with self.assertRaisesRegex(
            ValueError, "kind must be TelemetryEvidenceResult"
        ):
            TelemetryEvidenceResult.from_dict(
                example("telemetry-evidence-request.json")
            )
        with self.assertRaisesRegex(ValueError, "kind must be OtlpMetricsEvidence"):
            OtlpMetricsEvidence.from_dict(example("telemetry-evidence-result.json"))
        with self.assertRaisesRegex(ValueError, "kind must be LogEvidenceRequest"):
            LogEvidenceRequest.from_dict(example("log-evidence-result.json"))
        with self.assertRaisesRegex(ValueError, "kind must be LogEvidenceResult"):
            LogEvidenceResult.from_dict(example("log-evidence-request.json"))
        with self.assertRaisesRegex(ValueError, "kind must be OtlpLogsEvidence"):
            OtlpLogsEvidence.from_dict(example("otlp-metrics-evidence.json"))
        with self.assertRaisesRegex(
            ValueError, "kind must be KubernetesEventEvidenceRequest"
        ):
            KubernetesEventEvidenceRequest.from_dict(
                example("kubernetes-event-evidence-result.json")
            )

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

    def test_telemetry_assessment_must_match_its_declared_rule(self) -> None:
        path = (
            ROOT
            / "contracts"
            / "examples"
            / "investigation-report-telemetry.json"
        )
        report = copy.deepcopy(self.documents[path])
        report["spec"]["telemetryAssessments"][0]["threshold"] = 0.5
        self.documents[path] = report

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "telemetry assessment threshold must match its request",
            self.errors,
        )

    def test_baseline_assessment_must_match_its_declared_calculation(self) -> None:
        path = (
            ROOT
            / "contracts"
            / "examples"
            / "investigation-report-telemetry-baseline.json"
        )
        report = copy.deepcopy(self.documents[path])
        report["spec"]["telemetryAssessments"][0]["comparisonUnit"] = "percent"
        self.documents[path] = report

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "telemetry assessment comparisonUnit must match its request",
            self.errors,
        )

    def test_supporting_assessment_must_cite_its_hypothesis(self) -> None:
        path = (
            ROOT
            / "contracts"
            / "examples"
            / "investigation-report-telemetry.json"
        )
        report = copy.deepcopy(self.documents[path])
        assessment_id = report["spec"]["telemetryAssessments"][0]["evidenceId"]
        report["spec"]["hypotheses"][0]["supportingEvidenceIds"].remove(
            assessment_id
        )
        self.documents[path] = report

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "telemetry supporting Evidence must cite its hypothesis",
            self.errors,
        )

    def test_kubernetes_event_assessment_must_match_its_declared_rule(self) -> None:
        path = (
            ROOT
            / "contracts"
            / "examples"
            / "investigation-report-kubernetes-events.json"
        )
        report = copy.deepcopy(self.documents[path])
        report["spec"]["kubernetesEventAssessments"][0]["minMatches"] = 2
        self.documents[path] = report

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "Kubernetes Event assessment minMatches must match its request",
            self.errors,
        )

    def test_log_assessment_must_match_its_declared_rule(self) -> None:
        path = (
            ROOT
            / "contracts"
            / "examples"
            / "investigation-report-logs.json"
        )
        report = copy.deepcopy(self.documents[path])
        report["spec"]["logAssessments"][0]["observedRecordCount"] = 1
        self.documents[path] = report

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "log assessment disposition must match its rule",
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

    def test_telemetry_result_rejects_modified_request_digest(self) -> None:
        path = ROOT / "contracts" / "examples" / "telemetry-evidence-request.json"
        request = copy.deepcopy(self.documents[path])
        request["spec"]["query"]["metric"] = "another.metric"
        self.documents[path] = request

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "telemetry evidence result digest must match its request",
            self.errors,
        )

    def test_telemetry_result_rejects_incorrect_summary(self) -> None:
        path = ROOT / "contracts" / "examples" / "telemetry-evidence-result.json"
        result = copy.deepcopy(self.documents[path])
        result["spec"]["summary"]["dataPointCount"] = 2
        self.documents[path] = result

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "telemetry evidence summary must match its series",
            self.errors,
        )

    def test_log_result_rejects_modified_digest_and_summary(self) -> None:
        request_path = ROOT / "contracts" / "examples" / "log-evidence-request.json"
        request = copy.deepcopy(self.documents[request_path])
        request["spec"]["query"]["serviceNames"] = ["another-service"]
        self.documents[request_path] = request
        result_path = ROOT / "contracts" / "examples" / "log-evidence-result.json"
        result = copy.deepcopy(self.documents[result_path])
        result["spec"]["summary"]["errorCount"] = 0
        self.documents[result_path] = result

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn("log evidence result digest must match its request", self.errors)
        self.assertIn("log evidence summary must match its records", self.errors)

    def test_otlp_metrics_evidence_rejects_incorrect_summary(self) -> None:
        path = ROOT / "contracts" / "examples" / "otlp-metrics-evidence.json"
        result = copy.deepcopy(self.documents[path])
        result["spec"]["summary"]["dataPointCount"] = 3
        self.documents[path] = result

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "OTLP metrics evidence summary must match its series",
            self.errors,
        )

    def test_otlp_logs_evidence_rejects_incorrect_summary(self) -> None:
        path = ROOT / "contracts" / "examples" / "otlp-logs-evidence.json"
        result = copy.deepcopy(self.documents[path])
        result["spec"]["summary"]["recordCount"] = 2
        self.documents[path] = result

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "OTLP logs evidence summary must match its records",
            self.errors,
        )


if __name__ == "__main__":
    unittest.main()
