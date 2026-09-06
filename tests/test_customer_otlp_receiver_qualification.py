from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from jsonschema import Draft202012Validator, FormatChecker
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from scripts import qualify_customer_otlp_receiver as qualification
from scripts import validate_schemas


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts/examples"
SCHEMAS = ROOT / "contracts/schemas"
REVISION = "0123456789abcdef0123456789abcdef01234567"
IMAGE = "sha256:" + "a" * 64
REPOSITORY = {
    "applicationVersion": "0.84.0",
    "chartVersion": "0.87.0",
    "requiredMigration": "0023_ai_model_suitability.sql",
}
NOW = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)


def document(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def successful_result() -> qualification.ProbeResult:
    return qualification.ProbeResult(
        config_valid=True,
        collector_started=True,
        accepted={signal: True for signal in qualification.SIGNALS},
        delivered={signal: 1 for signal in qualification.SIGNALS},
        send_failed={signal: 0 for signal in qualification.SIGNALS},
        queue_size={signal: 0 for signal in qualification.SIGNALS},
        latency_milliseconds={"metrics": 800, "logs": 1200, "traces": 1700},
    )


def built_report(
    result: qualification.ProbeResult | None = None,
    *,
    api_identity_valid: bool = True,
    credentials_separate: bool = True,
    direct_receiver_accepted_count: int = 3,
) -> dict[str, object]:
    return qualification.build_report(
        revision=REVISION,
        repository=REPOSITORY,
        image_digest=IMAGE,
        api_target_digest="sha256:" + "1" * 64,
        profile=document(
            EXAMPLES / "customer-otlp-receiver-qualification-profile.json"
        ),
        api_ca_digest="sha256:" + "2" * 64,
        receiver_ca_digest="sha256:" + "3" * 64,
        client_certificate_digest="sha256:" + "4" * 64,
        started_at=NOW,
        completed_at=datetime(2026, 9, 8, 10, 0, 3, tzinfo=timezone.utc),
        api_identity_valid=api_identity_valid,
        credentials_separate=credentials_separate,
        direct_receiver_accepted_count=direct_receiver_accepted_count,
        result=result or successful_result(),
    )


class FakeProbe:
    def __init__(self, result: qualification.ProbeResult | None = None) -> None:
        self.result = result or successful_result()
        self.payloads: dict[str, bytes] | None = None
        self.arguments: dict[str, int] | None = None

    def run(self, payloads, **arguments):
        self.payloads = dict(payloads)
        self.arguments = dict(arguments)
        return self.result


class CustomerOtlpReceiverContractTests(unittest.TestCase):
    def test_examples_are_schema_and_semantically_valid(self) -> None:
        profile = document(
            EXAMPLES / "customer-otlp-receiver-qualification-profile.json"
        )
        report = document(
            EXAMPLES / "customer-otlp-receiver-qualification-report.json"
        )
        for stem, value in (
            ("customer-otlp-receiver-qualification-profile", profile),
            ("customer-otlp-receiver-qualification-report", report),
        ):
            schema = document(SCHEMAS / f"{stem}.schema.json")
            Draft202012Validator(
                schema, format_checker=FormatChecker()
            ).validate(value)
            self.assertEqual(
                validate_schemas.instance_validation_errors(
                    schema, value, label=stem
                ),
                [],
            )
        qualification.validate_profile(profile)
        qualification.validate_report_document(report)

    def test_profile_is_closed_pinned_and_has_bounded_objectives(self) -> None:
        profile = document(
            EXAMPLES / "customer-otlp-receiver-qualification-profile.json"
        )
        mutations = (
            lambda value: value["spec"]["collector"].update(
                {"version": "latest"}
            ),
            lambda value: value["spec"].update(
                {"receiverEndpoint": "http://receiver.example.com"}
            ),
            lambda value: value["spec"]["objective"].update(
                {
                    "deliveryTimeoutSeconds": 10,
                    "maximumDeliveryLatencyMilliseconds": 11000,
                }
            ),
            lambda value: value["spec"]["signals"]["genAiTraces"].update(
                {"prompt": "must-not-exist"}
            ),
        )
        for mutate in mutations:
            changed = copy.deepcopy(profile)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(
                qualification.CustomerOtlpReceiverQualificationError
            ):
                qualification.validate_profile(changed)

    def test_report_recomputes_identity_checks_counts_and_time(self) -> None:
        report = built_report()
        mutations = (
            lambda value: value["spec"]["measurements"].update(
                {"receiverDeliveredItemCount": 2}
            ),
            lambda value: value["spec"]["summary"].update(
                {"passedChecks": 19}
            ),
            lambda value: value["spec"]["checks"][18].update(
                {
                    "status": "failed",
                    "errorCode": "customer-otlp-receiver-qualification.wrong.failed",
                }
            ),
            lambda value: value["metadata"].update(
                {"id": "corq_" + "0" * 32}
            ),
        )
        for mutate in mutations:
            changed = copy.deepcopy(report)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(
                qualification.CustomerOtlpReceiverQualificationError
            ):
                qualification.validate_report_document(changed)

    def test_report_is_minimized_and_failure_is_not_promoted(self) -> None:
        failed = successful_result()
        failed = qualification.ProbeResult(
            **{
                **failed.__dict__,
                "delivered": {"metrics": 1, "logs": 0, "traces": 1},
                "send_failed": {"metrics": 0, "logs": 1, "traces": 0},
                "queue_size": {"metrics": 0, "logs": 1, "traces": 0},
            }
        )
        report = built_report(failed)
        self.assertEqual(report["spec"]["status"], "not-qualified")
        checks = {item["id"]: item for item in report["spec"]["checks"]}
        self.assertEqual(checks["logs-delivery"]["status"], "failed")
        self.assertEqual(checks["collector-zero-send-failure"]["status"], "failed")
        redirected = built_report(direct_receiver_accepted_count=2)
        redirected_checks = {
            item["id"]: item for item in redirected["spec"]["checks"]
        }
        self.assertEqual(redirected["spec"]["status"], "not-qualified")
        self.assertEqual(
            redirected_checks["direct-no-proxy-no-redirect"]["status"],
            "failed",
        )
        encoded = json.dumps(report, sort_keys=True)
        for protected in (
            "otlp.iip.example.com",
            "customer-qualification-probe",
            "example.foundation-model-v1:0",
            "aws.bedrock",
        ):
            self.assertNotIn(protected, encoded)


class CustomerOtlpReceiverBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.profile = document(
            EXAMPLES / "customer-otlp-receiver-qualification-profile.json"
        )
        self.profile_path = self.directory / "profile.json"
        self.profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        self.api_token = self._protected("api-token", "a" * 64)
        self.metrics_token = self._protected("metrics-token", "m" * 64)
        self.logs_token = self._protected("logs-token", "l" * 64)
        self.traces_token = self._protected("traces-token", "t" * 64)
        self.client_key = self._protected("client-key.pem", "test-private-key")
        self.api_ca = self._regular("api-ca.pem", b"api-ca")
        self.receiver_ca = self._regular("receiver-ca.pem", b"receiver-ca")
        self.client_certificate = self._regular(
            "client-cert.pem", b"client-certificate"
        )
        os.chmod(self.profile_path, 0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _protected(self, name: str, value: str) -> Path:
        path = self.directory / name
        path.write_text(value, encoding="ascii")
        os.chmod(path, 0o600)
        return path

    def _regular(self, name: str, value: bytes) -> Path:
        path = self.directory / name
        path.write_bytes(value)
        return path

    def qualify(self, probe: FakeProbe, **changes):
        arguments = {
            "profile": self.profile,
            "api_base_url": "https://iip.example.com",
            "receiver_endpoint": "https://otlp.iip.example.com:4318",
            "api_token_path": self.api_token,
            "api_ca_path": self.api_ca,
            "metrics_token_path": self.metrics_token,
            "logs_token_path": self.logs_token,
            "traces_token_path": self.traces_token,
            "receiver_ca_path": self.receiver_ca,
            "client_certificate_path": self.client_certificate,
            "client_key_path": self.client_key,
            "image_digest": IMAGE,
            "allow_observation": True,
            "probe": probe,
            "now": NOW,
            "api_identity_valid": True,
            "direct_receiver_accepted_count": 3,
        }
        arguments.update(changes)
        with patch.object(
            qualification, "_source_identity", return_value=(REVISION, False)
        ), patch.object(
            qualification.ingress,
            "_repository_identity",
            return_value=REPOSITORY,
        ):
            return qualification.qualify(**arguments)

    def test_full_flow_builds_three_metadata_bounded_payloads(self) -> None:
        probe = FakeProbe()
        report = self.qualify(probe)
        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(set(probe.payloads or {}), set(qualification.SIGNALS))
        metrics = ExportMetricsServiceRequest.FromString(probe.payloads["metrics"])
        logs = ExportLogsServiceRequest.FromString(probe.payloads["logs"])
        traces = ExportTraceServiceRequest.FromString(probe.payloads["traces"])
        self.assertEqual(len(metrics.resource_metrics), 1)
        self.assertEqual(len(logs.resource_logs), 1)
        self.assertEqual(len(traces.resource_spans), 1)
        encoded_trace = str(traces)
        for prohibited in ("gen_ai.prompt", "gen_ai.input", "gen_ai.output"):
            self.assertNotIn(prohibited, encoded_trace)
        self.assertEqual(probe.arguments["delivery_timeout_seconds"], 60)

    def test_credentials_are_protected_and_must_be_separate(self) -> None:
        os.chmod(self.metrics_token, 0o644)
        with self.assertRaisesRegex(
            qualification.CustomerOtlpReceiverQualificationError,
            "channel-credential.invalid",
        ):
            self.qualify(FakeProbe())
        os.chmod(self.metrics_token, 0o600)
        report = self.qualify(
            FakeProbe(),
            logs_token_path=self.metrics_token,
        )
        checks = {item["id"]: item for item in report["spec"]["checks"]}
        self.assertEqual(report["spec"]["status"], "not-qualified")
        self.assertEqual(checks["separate-channel-credentials"]["status"], "failed")
        report = self.qualify(
            FakeProbe(),
            metrics_token_path=self.api_token,
        )
        checks = {item["id"]: item for item in report["spec"]["checks"]}
        self.assertEqual(report["spec"]["status"], "not-qualified")
        self.assertEqual(checks["separate-channel-credentials"]["status"], "failed")

    def test_explicit_enable_clean_source_and_endpoint_binding_are_required(self) -> None:
        with self.assertRaisesRegex(
            qualification.CustomerOtlpReceiverQualificationError,
            "enable.required",
        ):
            self.qualify(FakeProbe(), allow_observation=False)
        with self.assertRaisesRegex(
            qualification.CustomerOtlpReceiverQualificationError,
            "receiver-target.crossed",
        ):
            self.qualify(
                FakeProbe(),
                receiver_endpoint="https://other.example.com:4318",
            )
        with patch.object(
            qualification, "_source_identity", return_value=(REVISION, True)
        ), patch.object(
            qualification.ingress,
            "_repository_identity",
            return_value=REPOSITORY,
        ), self.assertRaisesRegex(
            qualification.CustomerOtlpReceiverQualificationError,
            "source.dirty",
        ):
            qualification.qualify(
                profile=self.profile,
                api_base_url="https://iip.example.com",
                receiver_endpoint="https://otlp.iip.example.com:4318",
                api_token_path=self.api_token,
                api_ca_path=self.api_ca,
                metrics_token_path=self.metrics_token,
                logs_token_path=self.logs_token,
                traces_token_path=self.traces_token,
                receiver_ca_path=self.receiver_ca,
                client_certificate_path=self.client_certificate,
                client_key_path=self.client_key,
                image_digest=IMAGE,
                allow_observation=True,
                probe=FakeProbe(),
                now=NOW,
                api_identity_valid=True,
                direct_receiver_accepted_count=3,
            )

    def test_verifier_rebinds_source_profile_targets_trust_and_certificate(self) -> None:
        report = self.qualify(FakeProbe())
        report_path = self.directory / "report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        with patch.object(
            qualification, "_source_identity", return_value=(REVISION, False)
        ), patch.object(
            qualification.ingress,
            "_repository_identity",
            return_value=REPOSITORY,
        ):
            verified = qualification.verify_report(
                report_path=report_path,
                profile_path=self.profile_path,
                api_base_url="https://iip.example.com",
                receiver_endpoint="https://otlp.iip.example.com:4318",
                api_ca_path=self.api_ca,
                receiver_ca_path=self.receiver_ca,
                client_certificate_path=self.client_certificate,
                image_digest=IMAGE,
                require_qualified=True,
            )
            self.assertEqual(verified["spec"]["status"], "qualified")
            self.receiver_ca.write_bytes(b"changed-ca")
            with self.assertRaisesRegex(
                qualification.CustomerOtlpReceiverQualificationError,
                "report.crossed",
            ):
                qualification.verify_report(
                    report_path=report_path,
                    profile_path=self.profile_path,
                    api_base_url="https://iip.example.com",
                    receiver_endpoint="https://otlp.iip.example.com:4318",
                    api_ca_path=self.api_ca,
                    receiver_ca_path=self.receiver_ca,
                    client_certificate_path=self.client_certificate,
                    image_digest=IMAGE,
                )

    def test_collector_config_and_command_do_not_contain_credentials(self) -> None:
        tokens = {"metrics": "m" * 64, "logs": "l" * 64, "traces": "t" * 64}
        probe = qualification.DockerCollectorProbe(
            docker="docker",
            receiver_endpoint="https://otlp.iip.example.com:4318",
            tokens=tokens,
            receiver_ca=b"ca",
            client_certificate=b"certificate",
            client_key=b"private-key",
        )
        config = qualification._collector_configuration()
        arguments = probe._container_arguments(
            "qualification", self.directory / "queue", self.directory
        )
        encoded = json.dumps(arguments) + config
        for token in tokens.values():
            self.assertNotIn(token, encoded)
        self.assertIn("${env:IIP_QUAL_METRICS_TOKEN}", config)
        self.assertEqual(
            probe._environment(),
            {
                "HTTP_PROXY": "",
                "HTTPS_PROXY": "",
                "ALL_PROXY": "",
                "NO_PROXY": "*",
                "http_proxy": "",
                "https_proxy": "",
                "all_proxy": "",
                "no_proxy": "*",
            },
        )
        self.assertIn(qualification.COLLECTOR_IMAGE, arguments)

    def test_collector_self_metric_parser_is_exporter_exact(self) -> None:
        payload = "\n".join(
            (
                '# HELP otelcol_exporter_sent_spans Number of spans.',
                'otelcol_exporter_sent_spans{exporter="otlphttp/iip-traces",service_instance_id="a"} 1',
                'otelcol_exporter_sent_spans{exporter="another",service_instance_id="a"} 99',
            )
        )
        self.assertEqual(
            qualification._metric_value(
                payload,
                "otelcol_exporter_sent_spans",
                "otlphttp/iip-traces",
            ),
            1,
        )

    def test_direct_receiver_probe_denies_partial_or_redirected_delivery(self) -> None:
        context = MagicMock()
        opener = MagicMock()

        def response_for(request, *, timeout):
            self.assertEqual(timeout, 5)
            self.assertTrue(
                request.full_url.startswith(
                    "https://otlp.iip.example.com:4318/v1/"
                )
            )
            self.assertTrue(request.get_header("Authorization").startswith("Bearer "))
            self.assertEqual(request.data, b"")
            if request.full_url.endswith("/v1/logs"):
                raise OSError("redirect or transport failure")
            response = MagicMock()
            response.status = 200
            response.headers.get_content_type.return_value = "application/x-protobuf"
            response.read.return_value = b""
            response.__enter__.return_value = response
            return response

        opener.open.side_effect = response_for
        with patch.object(
            qualification.ssl, "create_default_context", return_value=context
        ), patch.object(qualification, "build_opener", return_value=opener):
            accepted = qualification._direct_receiver_delivery(
                receiver_endpoint="https://otlp.iip.example.com:4318",
                tokens={
                    "metrics": "m" * 64,
                    "logs": "l" * 64,
                    "traces": "t" * 64,
                },
                receiver_ca_path=self.receiver_ca,
                client_certificate_path=self.client_certificate,
                client_key_path=self.client_key,
                timeout_seconds=5,
            )
        self.assertEqual(accepted, 2)
        context.load_cert_chain.assert_called_once_with(
            certfile=str(self.client_certificate), keyfile=str(self.client_key)
        )


if __name__ == "__main__":
    unittest.main()
