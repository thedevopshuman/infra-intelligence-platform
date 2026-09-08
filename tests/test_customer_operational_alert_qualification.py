from __future__ import annotations

import copy
import json
import os
import ssl
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from infra_intelligence_sdk import (
    CustomerOperationalAlertQualificationProfile,
    CustomerOperationalAlertQualificationReport,
)
from scripts import qualify_customer_operational_alerts as qualification
from scripts import validate_schemas
from scripts.compatibility_tls import write_tls_material


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts/examples"
SCHEMAS = ROOT / "contracts/schemas"
NOW = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
REVISION = "a" * 40
IMAGE = "sha256:" + "b" * 64
REPOSITORY = {
    "applicationVersion": "0.84.0",
    "chartVersion": "0.87.0",
    "requiredMigration": "0023_ai_model_suitability.sql",
}


def document(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def successful_result() -> qualification.ProbeResult:
    return qualification.ProbeResult(
        prometheus_ready=True,
        verified_https=True,
        production_group_loaded=True,
        loaded_expected_rule_count=9,
        unhealthy_expected_rule_count=0,
        observed_component_count=3,
        synthetic_rule_loaded=True,
        synthetic_rule_healthy=True,
        synthetic_rule_inactive=True,
        alertmanager_ready=True,
        receipt_evidence_current=True,
        firing_notification_count=1,
        recovery_notification_count=1,
        maximum_notification_latency_milliseconds=2400,
    )


def built_report(
    result: qualification.ProbeResult | None = None,
    *,
    protected_inputs: bool = True,
) -> dict[str, object]:
    return dict(
        qualification.build_report(
            revision=REVISION,
            repository=REPOSITORY,
            image_digest=IMAGE,
            profile=document(
                EXAMPLES / "customer-operational-alert-qualification-profile.json"
            ),
            prometheus_ca_digest="sha256:" + "c" * 64,
            alertmanager_ca_digest="sha256:" + "d" * 64,
            receipt_ca_digest="sha256:" + "e" * 64,
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=5),
            protected_inputs=protected_inputs,
            result=result or successful_result(),
        )
    )


class CustomerOperationalAlertContractTests(unittest.TestCase):
    def test_examples_are_schema_semantic_and_sdk_valid(self) -> None:
        profile = document(
            EXAMPLES / "customer-operational-alert-qualification-profile.json"
        )
        report = document(
            EXAMPLES / "customer-operational-alert-qualification-report.json"
        )
        for stem, value in (
            ("customer-operational-alert-qualification-profile", profile),
            ("customer-operational-alert-qualification-report", report),
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
        self.assertEqual(
            CustomerOperationalAlertQualificationProfile.from_dict(
                profile
            ).to_dict(),
            profile,
        )
        self.assertEqual(
            CustomerOperationalAlertQualificationReport.from_dict(
                report
            ).to_dict(),
            report,
        )

    def test_profile_is_closed_https_and_rule_set_bound(self) -> None:
        profile = document(
            EXAMPLES / "customer-operational-alert-qualification-profile.json"
        )
        mutations = (
            lambda value: value["spec"]["monitoring"].update(
                {"prometheusBaseUrl": "http://prometheus.example.test"}
            ),
            lambda value: value["spec"]["monitoring"]["services"].reverse(),
            lambda value: value["spec"]["monitoring"]["services"][1].update(
                {"serviceName": "iip-control-plane"}
            ),
            lambda value: value["spec"]["monitoring"].update(
                {"receiptUrl": "https://notification-receipts.example.test/"}
            ),
            lambda value: value["spec"]["monitoring"].update(
                {"syntheticRuleGroup": "iip.platform.availability"}
            ),
            lambda value: value["spec"].update({"ruleSet": "all"}),
        )
        for mutate in mutations:
            changed = copy.deepcopy(profile)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(
                qualification.CustomerOperationalAlertQualificationError
            ):
                qualification.validate_profile(changed)

    def test_report_recomputes_checks_summary_and_identity(self) -> None:
        report = built_report()
        mutations = (
            lambda value: value["spec"]["measurements"].update(
                {"loadedExpectedRuleCount": 8}
            ),
            lambda value: value["spec"]["observations"].update(
                {"alertmanagerReady": False}
            ),
            lambda value: value["spec"]["summary"].update({"passedChecks": 18}),
            lambda value: value["metadata"].update(
                {"id": "coar_" + "0" * 32}
            ),
        )
        for mutate in mutations:
            changed = copy.deepcopy(report)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(
                qualification.CustomerOperationalAlertQualificationError
            ):
                qualification.validate_report_document(changed)

    def test_failed_route_is_not_promoted_and_output_is_minimized(self) -> None:
        failed = qualification.ProbeResult(
            **{
                **successful_result().__dict__,
                "receipt_evidence_current": False,
                "recovery_notification_count": 0,
            }
        )
        report = built_report(failed)
        self.assertEqual(report["spec"]["status"], "not-qualified")
        checks = {item["id"]: item for item in report["spec"]["checks"]}
        self.assertEqual(checks["receipt-evidence-current"]["status"], "failed")
        self.assertEqual(
            checks["recovery-notification-delivered"]["status"], "failed"
        )
        encoded = json.dumps(report, sort_keys=True)
        for protected in (
            "prometheus.example.test",
            "alertmanager.example.test",
            "notification-receipts.example.test",
            "iip-control-plane",
            "platform-primary",
            "iip-alert-route-20260908",
        ):
            self.assertNotIn(protected, encoded)

    def test_slow_current_receipt_is_reported_as_not_qualified(self) -> None:
        slow = qualification.ProbeResult(
            **{
                **successful_result().__dict__,
                "maximum_notification_latency_milliseconds": 600_000,
            }
        )
        report = built_report(slow)
        qualification.validate_report_document(report)
        self.assertEqual(report["spec"]["status"], "not-qualified")
        self.assertEqual(
            report["spec"]["measurements"][
                "maximumNotificationLatencyMilliseconds"
            ],
            600_000,
        )
        checks = {item["id"]: item for item in report["spec"]["checks"]}
        self.assertEqual(checks["receipt-evidence-current"]["status"], "passed")
        self.assertEqual(checks["notification-latency-objective"]["status"], "failed")

        extreme_profile = document(
            EXAMPLES / "customer-operational-alert-qualification-profile.json"
        )
        extreme_profile["spec"]["objective"].update(
            {
                "maximumObservationAgeSeconds": 3600,
                "maximumClockSkewSeconds": 300,
            }
        )
        extreme = qualification.ProbeResult(
            **{
                **successful_result().__dict__,
                "maximum_notification_latency_milliseconds": 3_900_000,
            }
        )
        extreme_report = qualification.build_report(
            revision=REVISION,
            repository=REPOSITORY,
            image_digest=IMAGE,
            profile=extreme_profile,
            prometheus_ca_digest="sha256:" + "c" * 64,
            alertmanager_ca_digest="sha256:" + "d" * 64,
            receipt_ca_digest="sha256:" + "e" * 64,
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=5),
            protected_inputs=True,
            result=extreme,
        )
        qualification.validate_report_document(extreme_report)
        self.assertEqual(extreme_report["spec"]["status"], "not-qualified")

    def test_report_validity_is_capped_by_profile_age(self) -> None:
        profile = document(
            EXAMPLES / "customer-operational-alert-qualification-profile.json"
        )
        profile["spec"]["objective"]["maximumProfileAgeSeconds"] = 7200
        report = qualification.build_report(
            revision=REVISION,
            repository=REPOSITORY,
            image_digest=IMAGE,
            profile=profile,
            prometheus_ca_digest="sha256:" + "c" * 64,
            alertmanager_ca_digest="sha256:" + "d" * 64,
            receipt_ca_digest="sha256:" + "e" * 64,
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=5),
            protected_inputs=True,
            result=successful_result(),
        )
        self.assertEqual(report["metadata"]["validUntil"], "2026-09-08T11:00:00Z")


class CustomerOperationalAlertProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = document(
            EXAMPLES / "customer-operational-alert-qualification-profile.json"
        )

    def test_live_probe_matches_rules_heartbeats_and_receipt_lifecycle(self) -> None:
        rules = [
            {"name": name, "health": "ok"} for name in qualification.AI_RULES
        ]
        responses = [
            {"status": "success", "data": {"version": "test"}},
            {
                "status": "success",
                "data": {
                    "groups": [
                        {
                            "name": "iip.platform.availability",
                            "rules": rules,
                        },
                        {
                            "name": "iip.qualification",
                            "rules": [
                                {
                                    "name": "IIPQualificationSynthetic",
                                    "health": "ok",
                                    "state": "inactive",
                                }
                            ],
                        },
                    ]
                },
            },
            {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {"job": name},
                            "value": [1788861600, "1"],
                        }
                        for name in (
                            "iip-control-plane",
                            "iip-workflow-worker",
                            "iip-otlp-receiver",
                        )
                    ]
                },
            },
            {"cluster": {"status": "ready"}},
            {
                "apiVersion": "iip.qualification/v1",
                "kind": "NotificationReceipt",
                "spec": {
                    "probeId": "iip-alert-route-20260908",
                    "alertName": "IIPQualificationSynthetic",
                    "routeId": "platform-primary",
                    "events": [
                        {
                            "state": "firing",
                            "sourceAt": "2026-09-08T09:58:00Z",
                            "receivedAt": "2026-09-08T09:58:02Z",
                        },
                        {
                            "state": "resolved",
                            "sourceAt": "2026-09-08T09:59:00Z",
                            "receivedAt": "2026-09-08T09:59:03Z",
                        },
                    ],
                },
            },
        ]
        probe = qualification.LiveAlertProbe(
            prometheus_token="p" * 32,
            alertmanager_token="a" * 32,
            receipt_token="r" * 32,
            prometheus_ca_file=Path("prometheus-ca.pem"),
            alertmanager_ca_file=Path("alertmanager-ca.pem"),
            receipt_ca_file=Path("receipt-ca.pem"),
        )
        with patch.object(
            qualification, "_request_json", side_effect=responses
        ) as request:
            result = probe.run(self.profile, now=NOW)

        self.assertEqual(result, successful_result().__class__(
            **{
                **successful_result().__dict__,
                "maximum_notification_latency_milliseconds": 3000,
            }
        ))
        urls = [call.args[0] for call in request.call_args_list]
        self.assertIn("/api/v1/rules?type=alert", urls[1])
        self.assertIn("iip_telemetry_heartbeat", urls[2])
        self.assertTrue(urls[4].endswith("?probeId=iip-alert-route-20260908"))

        responses[1]["data"]["groups"][1]["rules"][0]["state"] = "firing"
        with patch.object(
            qualification, "_request_json", side_effect=responses
        ):
            firing_result = probe.run(self.profile, now=NOW)
        self.assertFalse(firing_result.synthetic_rule_inactive)
        firing_report = built_report(firing_result)
        self.assertEqual(firing_report["spec"]["status"], "not-qualified")
        checks = {item["id"]: item for item in firing_report["spec"]["checks"]}
        self.assertEqual(checks["synthetic-rule-inactive"]["status"], "failed")

    def test_receipt_requires_exact_recent_firing_then_recovery(self) -> None:
        receipt = {
            "apiVersion": "iip.qualification/v1",
            "kind": "NotificationReceipt",
            "spec": {
                "probeId": "probe-123",
                "alertName": "IIPQualificationSynthetic",
                "routeId": "primary-route",
                "events": [
                    {
                        "state": "resolved",
                        "sourceAt": "2026-09-08T09:59:00Z",
                        "receivedAt": "2026-09-08T09:59:01Z",
                    },
                    {
                        "state": "firing",
                        "sourceAt": "2026-09-08T09:59:02Z",
                        "receivedAt": "2026-09-08T09:59:03Z",
                    },
                ],
            },
        }
        self.assertEqual(
            qualification._receipt_result(
                receipt,
                probe_id="probe-123",
                route_id="primary-route",
                alert_name="IIPQualificationSynthetic",
                now=NOW,
                maximum_age_seconds=900,
                maximum_future_skew_seconds=5,
            ),
            (False, 0, 0, 0),
        )
        receipt["spec"]["events"] = [
            {
                "state": "firing",
                "sourceAt": "2026-09-08T09:48:00Z",
                "receivedAt": "2026-09-08T09:58:00Z",
            },
            {
                "state": "resolved",
                "sourceAt": "2026-09-08T09:49:00Z",
                "receivedAt": "2026-09-08T09:59:00Z",
            },
        ]
        self.assertEqual(
            qualification._receipt_result(
                receipt,
                probe_id="probe-123",
                route_id="primary-route",
                alert_name="IIPQualificationSynthetic",
                now=NOW,
                maximum_age_seconds=900,
                maximum_future_skew_seconds=5,
            ),
            (True, 1, 1, 600_000),
        )
        receipt["spec"]["events"] = [
            {
                "state": "firing",
                "sourceAt": "2026-09-08T09:59:02Z",
                "receivedAt": "2026-09-08T09:59:03Z",
            },
            {
                "state": "resolved",
                "sourceAt": "2026-09-08T09:59:00Z",
                "receivedAt": "2026-09-08T09:59:04Z",
            },
        ]
        self.assertEqual(
            qualification._receipt_result(
                receipt,
                probe_id="probe-123",
                route_id="primary-route",
                alert_name="IIPQualificationSynthetic",
                now=NOW,
                maximum_age_seconds=900,
                maximum_future_skew_seconds=5,
            ),
            (False, 0, 0, 0),
        )

    def test_real_ca_verified_https_probe_uses_separate_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tls = Path(temporary) / "tls"
            tls.mkdir()
            write_tls_material(tls, common_name="localhost", dns_name="localhost")
            requests: list[tuple[str, str | None]] = []

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, format: str, *args: object) -> None:
                    del format, args

                def do_GET(self) -> None:  # noqa: N802
                    authorization = self.headers.get("Authorization")
                    requests.append((self.path, authorization))
                    if self.path.startswith("/api/v1/status/buildinfo"):
                        payload = {"status": "success", "data": {"version": "test"}}
                        expected = "Bearer " + "p" * 32
                    elif self.path.startswith("/api/v1/rules"):
                        payload = {
                            "status": "success",
                            "data": {
                                "groups": [
                                    {
                                        "name": "iip.platform.availability",
                                        "rules": [
                                            {"name": name, "health": "ok"}
                                            for name in qualification.AI_RULES
                                        ],
                                    },
                                    {
                                        "name": "iip.qualification",
                                        "rules": [
                                            {
                                                "name": "IIPQualificationSynthetic",
                                                "health": "ok",
                                                "state": "inactive",
                                            }
                                        ],
                                    },
                                ]
                            },
                        }
                        expected = "Bearer " + "p" * 32
                    elif self.path.startswith("/api/v1/query"):
                        payload = {
                            "status": "success",
                            "data": {
                                "resultType": "vector",
                                "result": [
                                    {
                                        "metric": {"job": name},
                                        "value": [1788861600, "1"],
                                    }
                                    for name in (
                                        "iip-control-plane",
                                        "iip-workflow-worker",
                                        "iip-otlp-receiver",
                                    )
                                ]
                            },
                        }
                        expected = "Bearer " + "p" * 32
                    elif self.path == "/api/v2/status":
                        payload = {"cluster": {"status": "ready"}}
                        expected = "Bearer " + "a" * 32
                    elif self.path.startswith("/v1/receipts?"):
                        payload = {
                            "apiVersion": "iip.qualification/v1",
                            "kind": "NotificationReceipt",
                            "spec": {
                                "probeId": "iip-alert-route-20260908",
                                "alertName": "IIPQualificationSynthetic",
                                "routeId": "platform-primary",
                                "events": [
                                    {
                                        "state": "firing",
                                        "sourceAt": "2026-09-08T09:58:00Z",
                                        "receivedAt": "2026-09-08T09:58:02Z",
                                    },
                                    {
                                        "state": "resolved",
                                        "sourceAt": "2026-09-08T09:59:00Z",
                                        "receivedAt": "2026-09-08T09:59:03Z",
                                    },
                                ],
                            },
                        }
                        expected = "Bearer " + "r" * 32
                    else:
                        self.send_error(404)
                        return
                    if authorization != expected:
                        self.send_error(401)
                        return
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(tls / "server.crt", tls / "server.key")
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                profile = copy.deepcopy(self.profile)
                base = f"https://localhost:{server.server_port}"
                profile["spec"]["monitoring"].update(
                    {
                        "prometheusBaseUrl": base,
                        "alertmanagerBaseUrl": base,
                        "receiptUrl": base + "/v1/receipts",
                    }
                )
                probe = qualification.LiveAlertProbe(
                    prometheus_token="p" * 32,
                    alertmanager_token="a" * 32,
                    receipt_token="r" * 32,
                    prometheus_ca_file=tls / "ca.crt",
                    alertmanager_ca_file=tls / "ca.crt",
                    receipt_ca_file=tls / "ca.crt",
                )
                result = probe.run(profile, now=NOW)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertTrue(result.verified_https)
        self.assertEqual(result.loaded_expected_rule_count, 9)
        self.assertEqual(result.observed_component_count, 3)
        self.assertEqual(result.firing_notification_count, 1)
        self.assertEqual(result.recovery_notification_count, 1)
        self.assertEqual(
            [authorization for _, authorization in requests],
            [
                "Bearer " + "p" * 32,
                "Bearer " + "p" * 32,
                "Bearer " + "p" * 32,
                "Bearer " + "a" * 32,
                "Bearer " + "r" * 32,
            ],
        )
class CustomerOperationalAlertFileBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _file(self, name: str, value: str, mode: int = 0o600) -> Path:
        path = self.directory / name
        path.write_text(value, encoding="utf-8")
        os.chmod(path, mode)
        return path

    def test_profile_and_credentials_require_protected_regular_files(self) -> None:
        profile = self._file(
            "profile.json",
            json.dumps(
                document(
                    EXAMPLES
                    / "customer-operational-alert-qualification-profile.json"
                )
            ),
            0o644,
        )
        with self.assertRaisesRegex(
            qualification.CustomerOperationalAlertQualificationError,
            "profile.unreadable",
        ):
            qualification.load_profile(profile)
        token = self._file("token", "x" * 32, 0o600)
        self.assertEqual(qualification.load_token(token), "x" * 32)
        os.chmod(token, 0o640)
        with self.assertRaisesRegex(
            qualification.CustomerOperationalAlertQualificationError,
            "credential.invalid",
        ):
            qualification.load_token(token)

    def test_qualifier_rejects_reused_endpoint_credentials(self) -> None:
        ca = self._file("ca.pem", "test-ca", 0o644)
        with patch.object(
            qualification, "_source_identity", return_value=(REVISION, False)
        ), self.assertRaisesRegex(
            qualification.CustomerOperationalAlertQualificationError,
            "credential.reused",
        ):
            qualification.qualify(
                profile=document(
                    EXAMPLES
                    / "customer-operational-alert-qualification-profile.json"
                ),
                image_digest=IMAGE,
                prometheus_token="x" * 32,
                alertmanager_token="x" * 32,
                receipt_token="r" * 32,
                prometheus_ca_file=ca,
                alertmanager_ca_file=ca,
                receipt_ca_file=ca,
                allow_monitoring_observation=True,
                now=NOW,
            )

    def test_live_observation_requires_explicit_authorization(self) -> None:
        ca = self._file("ca.pem", "test-ca", 0o644)
        with self.assertRaisesRegex(
            qualification.CustomerOperationalAlertQualificationError,
            "observation.not-authorized",
        ):
            qualification.qualify(
                profile=document(
                    EXAMPLES
                    / "customer-operational-alert-qualification-profile.json"
                ),
                image_digest=IMAGE,
                prometheus_token="p" * 32,
                alertmanager_token="a" * 32,
                receipt_token="r" * 32,
                prometheus_ca_file=ca,
                alertmanager_ca_file=ca,
                receipt_ca_file=ca,
                now=NOW,
            )

    def test_offline_verifier_rebinds_profile_release_and_ca_files(self) -> None:
        profile_value = document(
            EXAMPLES / "customer-operational-alert-qualification-profile.json"
        )
        profile = self._file("profile.json", json.dumps(profile_value))
        prometheus_ca = self._file("prometheus-ca.pem", "prometheus-ca", 0o644)
        alertmanager_ca = self._file(
            "alertmanager-ca.pem", "alertmanager-ca", 0o644
        )
        receipt_ca = self._file("receipt-ca.pem", "receipt-ca", 0o644)
        report_value = qualification.build_report(
            revision=REVISION,
            repository=REPOSITORY,
            image_digest=IMAGE,
            profile=profile_value,
            prometheus_ca_digest=qualification._digest_bytes(b"prometheus-ca"),
            alertmanager_ca_digest=qualification._digest_bytes(b"alertmanager-ca"),
            receipt_ca_digest=qualification._digest_bytes(b"receipt-ca"),
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=5),
            protected_inputs=True,
            result=successful_result(),
        )
        report = self._file("report.json", json.dumps(report_value), 0o600)
        with patch.object(
            qualification, "_source_identity", return_value=(REVISION, False)
        ), patch.object(
            qualification, "_repository_identity", return_value=REPOSITORY
        ):
            verified = qualification.verify_report(
                report_path=report,
                profile_path=profile,
                image_digest=IMAGE,
                prometheus_ca_file=prometheus_ca,
                alertmanager_ca_file=alertmanager_ca,
                receipt_ca_file=receipt_ca,
                require_qualified=True,
                now=NOW + timedelta(seconds=6),
            )
            self.assertEqual(verified["spec"]["status"], "qualified")
            with self.assertRaisesRegex(
                qualification.CustomerOperationalAlertQualificationError,
                "report.crossed",
            ):
                qualification.verify_report(
                    report_path=report,
                    profile_path=profile,
                    image_digest="sha256:" + "f" * 64,
                    prometheus_ca_file=prometheus_ca,
                    alertmanager_ca_file=alertmanager_ca,
                    receipt_ca_file=receipt_ca,
                    now=NOW + timedelta(seconds=6),
                )

    def test_offline_verifier_rejects_rewritten_profile_semantics(self) -> None:
        protected_profile = document(
            EXAMPLES / "customer-operational-alert-qualification-profile.json"
        )
        profile_path = self._file("profile.json", json.dumps(protected_profile))
        prometheus_ca = self._file("prometheus-ca.pem", "prometheus-ca", 0o644)
        alertmanager_ca = self._file(
            "alertmanager-ca.pem", "alertmanager-ca", 0o644
        )
        receipt_ca = self._file("receipt-ca.pem", "receipt-ca", 0o644)

        variants: list[tuple[str, dict[str, object], qualification.ProbeResult]] = []
        relaxed = copy.deepcopy(protected_profile)
        relaxed["spec"]["objective"][
            "maximumNotificationLatencyMilliseconds"
        ] = 300_000
        variants.append(("objective", relaxed, successful_result()))
        re_reviewed = copy.deepcopy(protected_profile)
        re_reviewed["metadata"]["reviewedAt"] = "2026-09-08T08:00:00Z"
        variants.append(("reviewed", re_reviewed, successful_result()))
        core = copy.deepcopy(protected_profile)
        core["spec"]["ruleSet"] = "core-v1"
        core["spec"]["monitoring"]["services"] = core["spec"]["monitoring"][
            "services"
        ][:2]
        core_result = qualification.ProbeResult(
            **{
                **successful_result().__dict__,
                "loaded_expected_rule_count": 5,
                "observed_component_count": 2,
            }
        )
        variants.append(("rule-set", core, core_result))

        with patch.object(
            qualification, "_source_identity", return_value=(REVISION, False)
        ), patch.object(
            qualification, "_repository_identity", return_value=REPOSITORY
        ):
            for name, crossed_profile, result in variants:
                with self.subTest(name=name):
                    crossed = qualification.build_report(
                        revision=REVISION,
                        repository=REPOSITORY,
                        image_digest=IMAGE,
                        profile=crossed_profile,
                        prometheus_ca_digest=qualification._digest_bytes(
                            b"prometheus-ca"
                        ),
                        alertmanager_ca_digest=qualification._digest_bytes(
                            b"alertmanager-ca"
                        ),
                        receipt_ca_digest=qualification._digest_bytes(b"receipt-ca"),
                        started_at=NOW,
                        completed_at=NOW + timedelta(seconds=5),
                        protected_inputs=True,
                        result=result,
                    )
                    crossed["spec"]["bindings"]["profileDigest"] = (
                        qualification._digest_value(protected_profile)
                    )
                    metadata_without_id = dict(crossed["metadata"])
                    metadata_without_id.pop("id")
                    crossed["metadata"]["id"] = qualification._report_identifier(
                        metadata_without_id, crossed["spec"]
                    )
                    qualification.validate_report_document(crossed)
                    report_path = self._file(
                        f"crossed-{name}.json", json.dumps(crossed), 0o600
                    )
                    with self.assertRaisesRegex(
                        qualification.CustomerOperationalAlertQualificationError,
                        "report.crossed",
                    ):
                        qualification.verify_report(
                            report_path=report_path,
                            profile_path=profile_path,
                            image_digest=IMAGE,
                            prometheus_ca_file=prometheus_ca,
                            alertmanager_ca_file=alertmanager_ca,
                            receipt_ca_file=receipt_ca,
                            now=NOW + timedelta(seconds=6),
                        )

    def test_offline_verifier_rejects_future_generated_report(self) -> None:
        profile_value = document(
            EXAMPLES / "customer-operational-alert-qualification-profile.json"
        )
        profile_path = self._file("profile.json", json.dumps(profile_value))
        prometheus_ca = self._file("prometheus-ca.pem", "prometheus-ca", 0o644)
        alertmanager_ca = self._file(
            "alertmanager-ca.pem", "alertmanager-ca", 0o644
        )
        receipt_ca = self._file("receipt-ca.pem", "receipt-ca", 0o644)
        future = qualification.build_report(
            revision=REVISION,
            repository=REPOSITORY,
            image_digest=IMAGE,
            profile=profile_value,
            prometheus_ca_digest=qualification._digest_bytes(b"prometheus-ca"),
            alertmanager_ca_digest=qualification._digest_bytes(b"alertmanager-ca"),
            receipt_ca_digest=qualification._digest_bytes(b"receipt-ca"),
            started_at=NOW + timedelta(seconds=55),
            completed_at=NOW + timedelta(seconds=60),
            protected_inputs=True,
            result=successful_result(),
        )
        report_path = self._file("future.json", json.dumps(future), 0o600)
        with patch.object(
            qualification, "_source_identity", return_value=(REVISION, False)
        ), patch.object(
            qualification, "_repository_identity", return_value=REPOSITORY
        ), self.assertRaisesRegex(
            qualification.CustomerOperationalAlertQualificationError,
            "report.crossed",
        ):
            qualification.verify_report(
                report_path=report_path,
                profile_path=profile_path,
                image_digest=IMAGE,
                prometheus_ca_file=prometheus_ca,
                alertmanager_ca_file=alertmanager_ca,
                receipt_ca_file=receipt_ca,
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
