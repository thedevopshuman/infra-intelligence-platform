from __future__ import annotations

import json
import http.client
import logging
import os
import ssl
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import psycopg
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, MetricExportResult
from opentelemetry.sdk.resources import Resource


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("IIP_TEST_OTLP_RECEIVER_ENDPOINT"),
    "set IIP_TEST_OTLP_RECEIVER_ENDPOINT to run the Docker receiver gate",
)
class OtlpReceiverDockerIntegrationTests(unittest.TestCase):
    def receiver_context(
        self,
        *,
        certificate_variable: str | None = None,
        key_variable: str | None = None,
    ) -> ssl.SSLContext:
        context = ssl.create_default_context(
            cafile=os.environ["IIP_TEST_OTLP_CA_FILE"]
        )
        if certificate_variable is not None and key_variable is not None:
            context.load_cert_chain(
                certfile=os.environ[certificate_variable],
                keyfile=os.environ[key_variable],
            )
        return context

    def receiver_request(
        self,
        path: str,
        *,
        token: str,
        context: ssl.SSLContext,
        payload: bytes = b"",
    ) -> int:
        receiver_url = os.environ["IIP_TEST_OTLP_RECEIVER_ENDPOINT"].rstrip("/")
        request = urllib.request.Request(
            receiver_url + path,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/x-protobuf",
            },
        )
        with urllib.request.urlopen(request, context=context, timeout=5) as response:
            return response.status

    def seed_resource(self, base_url: str, control_token: str) -> None:
        resource = json.loads(
            (ROOT / "contracts" / "examples" / "resource.json").read_text()
        )
        request = urllib.request.Request(
            f"{base_url}/v1/resources",
            data=json.dumps(resource).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {control_token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 202)

    def test_official_exporter_reaches_isolated_receiver(self) -> None:
        control_url = os.environ["IIP_TEST_CONTROL_ENDPOINT"].rstrip("/")
        receiver_url = os.environ["IIP_TEST_OTLP_RECEIVER_ENDPOINT"].rstrip("/")
        control_token = os.environ["IIP_TEST_OTLP_CONTROL_TOKEN"]
        channel_token = os.environ["IIP_TEST_OTLP_CHANNEL_TOKEN"]
        self.seed_resource(control_url, control_token)

        reader = InMemoryMetricReader()
        provider = MeterProvider(
            metric_readers=[reader],
            resource=Resource.create(
                {
                    "service.name": "checkout",
                    "deployment.environment.name": "docker-test",
                }
            ),
        )
        meter = provider.get_meter("iip.receiver.docker-test")
        counter = meter.create_counter(
            "http.server.request.count",
            unit="{request}",
        )
        counter.add(1)
        metrics_data = reader.get_metrics_data()
        self.assertIsNotNone(metrics_data)

        exporter = OTLPMetricExporter(
            endpoint=f"{receiver_url}/v1/metrics",
            certificate_file=os.environ["IIP_TEST_OTLP_CA_FILE"],
            client_certificate_file=os.environ[
                "IIP_TEST_OTLP_CLIENT_CERT_FILE"
            ],
            client_key_file=os.environ["IIP_TEST_OTLP_CLIENT_KEY_FILE"],
            headers={"Authorization": f"Bearer {channel_token}"},
            timeout=5,
        )
        try:
            self.assertEqual(
                exporter.export(metrics_data),
                MetricExportResult.SUCCESS,
            )
        finally:
            exporter.shutdown()
            provider.shutdown()

    def test_official_log_exporter_reaches_isolated_receiver(self) -> None:
        control_url = os.environ["IIP_TEST_CONTROL_ENDPOINT"].rstrip("/")
        receiver_url = os.environ["IIP_TEST_OTLP_RECEIVER_ENDPOINT"].rstrip("/")
        control_token = os.environ["IIP_TEST_OTLP_CONTROL_TOKEN"]
        channel_token = os.environ["IIP_TEST_OTLP_CHANNEL_TOKEN"]
        self.seed_resource(control_url, control_token)

        exporter = OTLPLogExporter(
            endpoint=f"{receiver_url}/v1/logs",
            certificate_file=os.environ["IIP_TEST_OTLP_CA_FILE"],
            client_certificate_file=os.environ[
                "IIP_TEST_OTLP_CLIENT_CERT_FILE"
            ],
            client_key_file=os.environ["IIP_TEST_OTLP_CLIENT_KEY_FILE"],
            headers={"Authorization": f"Bearer {channel_token}"},
            timeout=5,
        )
        provider = LoggerProvider(
            resource=Resource.create(
                {
                    "service.name": "checkout",
                    "deployment.environment.name": "docker-test",
                    "k8s.namespace.name": "default",
                }
            )
        )
        provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
        logger = logging.getLogger("iip.receiver.docker-test")
        logger.setLevel(logging.ERROR)
        handler = LoggingHandler(logger_provider=provider)
        logger.addHandler(handler)
        try:
            logger.error("database request exceeded its deadline")
            self.assertTrue(provider.force_flush(timeout_millis=5000))
        finally:
            logger.removeHandler(handler)
            handler.close()
            provider.shutdown()

    def test_control_and_receiver_routes_are_process_isolated(self) -> None:
        control_url = os.environ["IIP_TEST_CONTROL_ENDPOINT"].rstrip("/")
        receiver_url = os.environ["IIP_TEST_OTLP_RECEIVER_ENDPOINT"].rstrip("/")
        channel_token = os.environ["IIP_TEST_OTLP_CHANNEL_TOKEN"]

        control_request = urllib.request.Request(
            f"{control_url}/v1/metrics",
            data=b"",
            method="POST",
            headers={
                "Authorization": f"Bearer {channel_token}",
                "Content-Type": "application/x-protobuf",
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as control_error:
            urllib.request.urlopen(control_request, timeout=5)
        self.assertEqual(control_error.exception.code, 404)
        control_error.exception.close()

        with self.assertRaises(urllib.error.HTTPError) as receiver_error:
            urllib.request.urlopen(
                f"{receiver_url}/console",
                context=self.receiver_context(),
                timeout=5,
            )
        self.assertEqual(receiver_error.exception.code, 404)
        receiver_error.exception.close()

    def test_health_is_tls_verified_but_does_not_require_workload_identity(
        self,
    ) -> None:
        receiver_url = os.environ["IIP_TEST_OTLP_RECEIVER_ENDPOINT"].rstrip("/")
        with urllib.request.urlopen(
            receiver_url + "/healthz",
            context=self.receiver_context(),
            timeout=5,
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read()), {"status": "ok"})

        with self.assertRaises((urllib.error.URLError, ssl.SSLError)):
            urllib.request.urlopen(receiver_url + "/healthz", timeout=5)

        plaintext_url = receiver_url.replace("https://", "http://", 1)
        with self.assertRaises(
            (
                urllib.error.URLError,
                http.client.BadStatusLine,
                http.client.RemoteDisconnected,
                ConnectionResetError,
            )
        ):
            urllib.request.urlopen(plaintext_url + "/healthz", timeout=5)

    def test_mtls_identity_channel_and_bearer_are_all_required(self) -> None:
        channel_token = os.environ["IIP_TEST_OTLP_CHANNEL_TOKEN"]
        control_token = os.environ["IIP_TEST_OTLP_CONTROL_TOKEN"]

        with self.assertRaises(urllib.error.HTTPError) as missing_identity:
            self.receiver_request(
                "/v1/metrics",
                token=channel_token,
                context=self.receiver_context(),
            )
        self.assertEqual(missing_identity.exception.code, 401)
        missing_identity.exception.close()

        other_identity = self.receiver_context(
            certificate_variable="IIP_TEST_OTLP_OTHER_CLIENT_CERT_FILE",
            key_variable="IIP_TEST_OTLP_OTHER_CLIENT_KEY_FILE",
        )
        with self.assertRaises(urllib.error.HTTPError) as wrong_identity:
            self.receiver_request(
                "/v1/metrics",
                token=channel_token,
                context=other_identity,
            )
        self.assertEqual(wrong_identity.exception.code, 401)
        wrong_identity.exception.close()

        valid_identity = self.receiver_context(
            certificate_variable="IIP_TEST_OTLP_CLIENT_CERT_FILE",
            key_variable="IIP_TEST_OTLP_CLIENT_KEY_FILE",
        )
        with self.assertRaises(urllib.error.HTTPError) as wrong_bearer:
            self.receiver_request(
                "/v1/metrics",
                token=control_token,
                context=valid_identity,
            )
        self.assertEqual(wrong_bearer.exception.code, 401)
        wrong_bearer.exception.close()

        untrusted_identity = self.receiver_context(
            certificate_variable="IIP_TEST_OTLP_UNTRUSTED_CLIENT_CERT_FILE",
            key_variable="IIP_TEST_OTLP_UNTRUSTED_CLIENT_KEY_FILE",
        )
        with self.assertRaises((urllib.error.URLError, ssl.SSLError)):
            self.receiver_request(
                "/v1/metrics",
                token=channel_token,
                context=untrusted_identity,
            )

    def test_certificate_rotation_preserves_the_same_spiffe_authority(self) -> None:
        rotated_identity = self.receiver_context(
            certificate_variable="IIP_TEST_OTLP_ROTATED_CLIENT_CERT_FILE",
            key_variable="IIP_TEST_OTLP_ROTATED_CLIENT_KEY_FILE",
        )

        status = self.receiver_request(
            "/v1/metrics",
            token=os.environ["IIP_TEST_OTLP_CHANNEL_TOKEN"],
            context=rotated_identity,
        )

        self.assertEqual(status, 200)

    def test_successful_exports_are_durable_tenant_bound_evidence(self) -> None:
        with psycopg.connect(os.environ["IIP_TEST_OTLP_DATABASE_URL"]) as connection:
            rows = connection.execute(
                """
                SELECT document->'spec'->>'type' AS evidence_type, count(*)
                FROM iip.evidence_artifacts
                WHERE tenant_id = 'local'
                GROUP BY evidence_type
                """
            ).fetchall()

        counts = {str(row[0]): int(row[1]) for row in rows}
        self.assertGreaterEqual(counts.get("telemetry.metrics.push", 0), 1)
        self.assertGreaterEqual(counts.get("telemetry.logs.push", 0), 1)


if __name__ == "__main__":
    unittest.main()
