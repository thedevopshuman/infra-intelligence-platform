from __future__ import annotations

import json
import ssl
import tempfile
import unittest
from http import HTTPStatus
from io import BytesIO
from pathlib import Path

from cryptography import x509

from iip.application.ingest_otlp_metrics import (
    OtlpReceiverAuthenticationError,
    OtlpReceiverConfigurationError,
)
from iip.surfaces.otlp_receiver import OtlpReceiverHandler
from iip.surfaces.otlp_tls import (
    OtlpTlsConfiguration,
    SpiffeClientIdentityRegistry,
)
from scripts.write_otlp_mtls_fixture import (
    AUTHORIZED_SPIFFE_ID,
    write_fixture,
)
from tests.test_otlp_receiver import (
    CHANNEL_TOKEN,
    ingest_fixture,
    receiver_config,
)
from iip.adapters.otlp_receiver import ConfiguredOtlpMetricsReceiver
from iip.bootstrap import build_local_runtime


def identities() -> str:
    return json.dumps(
        {
            "identities": [
                {
                    "spiffeId": AUTHORIZED_SPIFFE_ID,
                    "channelIds": ["otlp-docker", "otlp-logs-docker"],
                }
            ]
        }
    )


class _Connection:
    def __init__(self, certificate: object) -> None:
        self.certificate = certificate

    def getpeercert(self) -> object:
        return self.certificate


class OtlpTlsConfigurationTests(unittest.TestCase):
    def test_modes_are_closed_and_mutual_profile_requires_every_input(self) -> None:
        disabled = OtlpTlsConfiguration.from_environment({})
        self.assertEqual(disabled.mode, "disabled")
        self.assertEqual(disabled.scheme, "http")

        server = OtlpTlsConfiguration.from_environment(
            {
                "IIP_OTLP_TLS_MODE": "server",
                "IIP_OTLP_TLS_CERTIFICATE_PATH": "/tls/server.crt",
                "IIP_OTLP_TLS_PRIVATE_KEY_PATH": "/tls/server.key",
            }
        )
        self.assertEqual(server.scheme, "https")
        self.assertIsNone(server.client_identities)

        mutual = OtlpTlsConfiguration.from_environment(
            {
                "IIP_OTLP_TLS_MODE": "mutual-spiffe",
                "IIP_OTLP_TLS_CERTIFICATE_PATH": "/tls/server.crt",
                "IIP_OTLP_TLS_PRIVATE_KEY_PATH": "/tls/server.key",
                "IIP_OTLP_TLS_CLIENT_CA_PATH": "/client-ca/ca.crt",
                "IIP_OTLP_MTLS_IDENTITIES_JSON": identities(),
            }
        )
        self.assertIsNotNone(mutual.client_identities)

        for invalid in (
            {"IIP_OTLP_TLS_MODE": "other"},
            {
                "IIP_OTLP_TLS_MODE": "disabled",
                "IIP_OTLP_TLS_CERTIFICATE_PATH": "/tls/server.crt",
            },
            {
                "IIP_OTLP_TLS_MODE": "server",
                "IIP_OTLP_TLS_CERTIFICATE_PATH": "relative.crt",
                "IIP_OTLP_TLS_PRIVATE_KEY_PATH": "/tls/server.key",
            },
            {
                "IIP_OTLP_TLS_MODE": "mutual-spiffe",
                "IIP_OTLP_TLS_CERTIFICATE_PATH": "/tls/server.crt",
                "IIP_OTLP_TLS_PRIVATE_KEY_PATH": "/tls/server.key",
                "IIP_OTLP_TLS_CLIENT_CA_PATH": "/client-ca/ca.crt",
            },
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    OtlpReceiverConfigurationError,
                    "otlp.tls.configuration.invalid",
                ):
                    OtlpTlsConfiguration.from_environment(invalid)

    def test_ephemeral_mutual_context_has_server_and_client_auth_material(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixture"
            write_fixture(root)
            configuration = OtlpTlsConfiguration.from_environment(
                {
                    "IIP_OTLP_TLS_MODE": "mutual-spiffe",
                    "IIP_OTLP_TLS_CERTIFICATE_PATH": str(root / "server.crt"),
                    "IIP_OTLP_TLS_PRIVATE_KEY_PATH": str(root / "server.key"),
                    "IIP_OTLP_TLS_CLIENT_CA_PATH": str(root / "ca.crt"),
                    "IIP_OTLP_MTLS_IDENTITIES_JSON": identities(),
                }
            )

            context = configuration.ssl_context()

            self.assertIsNotNone(context)
            assert context is not None
            self.assertEqual(context.verify_mode, ssl.CERT_OPTIONAL)
            self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
            certificate = x509.load_pem_x509_certificate(
                (root / "collector-a.crt").read_bytes()
            )
            uri_names = certificate.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value.get_values_for_type(x509.UniformResourceIdentifier)
            self.assertEqual(uri_names, [AUTHORIZED_SPIFFE_ID])

    def test_missing_or_malformed_tls_files_fail_with_stable_configuration_code(
        self,
    ) -> None:
        configuration = OtlpTlsConfiguration.from_environment(
            {
                "IIP_OTLP_TLS_MODE": "server",
                "IIP_OTLP_TLS_CERTIFICATE_PATH": "/missing/server.crt",
                "IIP_OTLP_TLS_PRIVATE_KEY_PATH": "/missing/server.key",
            }
        )
        with self.assertRaisesRegex(
            OtlpReceiverConfigurationError,
            "otlp.tls.configuration.invalid",
        ):
            configuration.ssl_context()


class SpiffeClientIdentityTests(unittest.TestCase):
    def test_exact_uri_san_and_channel_are_both_required(self) -> None:
        registry = SpiffeClientIdentityRegistry.from_json(identities())
        valid_certificate = {
            "subjectAltName": (("URI", AUTHORIZED_SPIFFE_ID),)
        }

        registry.authorize(valid_certificate, "otlp-docker")

        for certificate, channel_id in (
            ({}, "otlp-docker"),
            ({"subjectAltName": (("DNS", "collector.example"),)}, "otlp-docker"),
            (
                {
                    "subjectAltName": (
                        ("URI", AUTHORIZED_SPIFFE_ID),
                        ("URI", "spiffe://customer.example/other"),
                    )
                },
                "otlp-docker",
            ),
            (valid_certificate, "other-channel"),
        ):
            with self.subTest(certificate=certificate, channel_id=channel_id):
                with self.assertRaisesRegex(
                    OtlpReceiverAuthenticationError,
                    "otlp.authentication.invalid",
                ):
                    registry.authorize(certificate, channel_id)

    def test_registry_rejects_ambiguous_or_non_spiffe_configuration(self) -> None:
        for document in (
            {"identities": []},
            {
                "identities": [
                    {"spiffeId": "https://collector.example", "channelIds": ["abc"]}
                ]
            },
            {
                "identities": [
                    {"spiffeId": AUTHORIZED_SPIFFE_ID, "channelIds": ["abc", "abc"]}
                ]
            },
            {
                "identities": [
                    {"spiffeId": AUTHORIZED_SPIFFE_ID, "channelIds": ["abc"]},
                    {"spiffeId": AUTHORIZED_SPIFFE_ID, "channelIds": ["def"]},
                ]
            },
        ):
            with self.subTest(document=document):
                with self.assertRaisesRegex(
                    OtlpReceiverConfigurationError,
                    "otlp.tls.configuration.invalid",
                ):
                    SpiffeClientIdentityRegistry.from_json(json.dumps(document))

    def test_identity_denial_happens_before_telemetry_body_read(self) -> None:
        runtime = build_local_runtime(
            otlp_metrics_receiver=ConfiguredOtlpMetricsReceiver.from_json(
                receiver_config()
            )
        )
        self.addCleanup(runtime.close)
        ingest_fixture(runtime)
        handler = object.__new__(OtlpReceiverHandler)
        handler.runtime = runtime
        handler.client_identities = SpiffeClientIdentityRegistry.from_json(
            identities()
        )
        handler.connection = _Connection(
            {"subjectAltName": (("URI", "spiffe://customer.example/other"),)}
        )
        handler.path = "/v1/metrics"
        handler.headers = {
            "authorization": f"Bearer {CHANNEL_TOKEN}",
            "content-type": "application/x-protobuf",
            "content-length": "100",
        }
        handler.rfile = None
        output = BytesIO()
        handler.wfile = output
        statuses: list[int] = []
        handler.send_response = lambda status: statuses.append(status)
        handler.send_header = lambda _name, _value: None
        handler.end_headers = lambda: None

        handler.do_POST()

        self.assertEqual(statuses, [HTTPStatus.UNAUTHORIZED])
        self.assertNotEqual(output.getvalue(), b"")


if __name__ == "__main__":
    unittest.main()
