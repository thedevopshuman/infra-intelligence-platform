from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy" / "helm" / "infra-intelligence"


class OperationalAlertContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template = (CHART / "templates" / "operational-alerts.yaml").read_text(
            encoding="utf-8"
        )
        cls.overlay = (
            CHART
            / "examples"
            / "production-operational-alerts.values.yaml"
        ).read_text(encoding="utf-8")

    def test_rule_profile_is_optional_and_selectable(self) -> None:
        self.assertIn("{{- if .Values.operationalAlerts.enabled }}", self.template)
        self.assertIn("kind: PrometheusRule", self.template)
        self.assertIn(
            "default .Release.Namespace .Values.operationalAlerts.namespace | quote",
            self.template,
        )
        self.assertIn("enabled: true", self.overlay)
        self.assertIn("namespace: observability", self.overlay)
        self.assertIn("prometheus: platform", self.overlay)

    def test_expected_rules_and_metrics_are_bound(self) -> None:
        for alert in (
            "IIPQueryAvailabilityBelowObjective",
            "IIPOtlpReceiverAvailabilityBelowObjective",
            "IIPIngestionFreshnessObjectiveViolated",
            "IIPTelemetryRecordingFailures",
            "IIPAiUsageCoverageIncomplete",
            "IIPAiCostCoverageUnresolved",
        ):
            with self.subTest(alert=alert):
                self.assertEqual(self.template.count(f"alert: {alert}"), 1)

        for metric in (
            "iip_query_requests",
            "iip_otlp_receiver_requests",
            "iip_ingestion_within_objective",
            "iip_telemetry_record_failures",
            "iip_ai_usage_incomplete_requests",
            "iip_ai_cost_requests",
        ):
            with self.subTest(metric=metric):
                self.assertIn(metric, self.template)

    def test_rules_are_feature_gated_and_escape_service_names(self) -> None:
        self.assertIn(
            "or .Values.otlpReceiver.enabled .Values.otlpLogsReceiver.enabled .Values.aiUsageReceiver.enabled",
            self.template,
        )
        self.assertIn("if .Values.aiUsageReceiver.enabled", self.template)
        self.assertIn("if .Values.aiCostEngine.enabled", self.template)
        self.assertGreaterEqual(self.template.count("| toJson"), 6)
        self.assertGreaterEqual(self.template.count("regexQuoteMeta"), 5)

    def test_alert_labels_do_not_export_dynamic_identity(self) -> None:
        labels = self.template.split("labels:", 2)[-1]
        for prohibited in (
            "iip_tenant_id",
            "iip_source_id",
            "iip_resource_id",
            "iip_ai_profile_id",
            "gen_ai_request_id",
            "gen_ai_prompt",
            "gen_ai_completion",
        ):
            with self.subTest(prohibited=prohibited):
                self.assertNotIn(prohibited, labels)

        self.assertIn("severity: critical", self.template)
        self.assertIn("severity: warning", self.template)
        self.assertIn("iip_alert_scope: platform", self.template)
        self.assertIn("iip_alert_scope: ai-economics", self.template)

    def test_recording_failure_rule_does_not_claim_export_delivery(self) -> None:
        self.assertIn(
            "exporter and Collector delivery require independent monitoring",
            self.template,
        )
        self.assertNotIn("iip_telemetry_export", self.template)


if __name__ == "__main__":
    unittest.main()
