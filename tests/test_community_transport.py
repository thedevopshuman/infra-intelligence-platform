"""The persistent preview must not inherit disposable demo security defaults."""

from __future__ import annotations

import json
import os
import re
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID

from scripts.community_transport import (
    CHANNEL_ID, COLLECTOR_SPIFFE_ID, HEALTH_SPIFFE_ID, TRANSPORT_FILES,
    transport_environment, validate_transport, write_transport,
)


ROOT = Path(__file__).resolve().parents[1]


class CommunityTransportTests(unittest.TestCase):
    def test_fresh_material_is_private_independent_and_not_fixture_lifetime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            first = state / "first"
            second = state / "second"
            first.mkdir(mode=0o700)
            second.mkdir(mode=0o700)
            environment = write_transport(first)
            write_transport(second)
            self.assertEqual(environment, transport_environment())
            self.assertNotEqual((first / "transport/ca.crt").read_bytes(), (second / "transport/ca.crt").read_bytes())
            self.assertEqual({p.name for p in (first / "transport").iterdir()}, set(TRANSPORT_FILES))
            for path in (first / "transport").iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual((first / "transport").stat().st_mode & 0o777, 0o700)
            validate_transport(first)
            certificate = x509.load_pem_x509_certificate((first / "transport/postgres.crt").read_bytes())
            self.assertGreater(certificate.not_valid_after_utc, datetime.now(timezone.utc) + timedelta(days=360))
            self.assertEqual(environment["IIP_DATABASE_TRANSPORT_MODE"], "verify-full")

    def test_server_and_client_purposes_and_health_authority_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            environment = write_transport(state)
            identities = json.loads(environment["IIP_OTLP_MTLS_IDENTITIES_JSON"])["identities"]
            self.assertEqual(identities, [{"spiffeId": COLLECTOR_SPIFFE_ID, "channelIds": [CHANNEL_ID]}])
            self.assertNotIn(HEALTH_SPIFFE_ID, environment["IIP_OTLP_MTLS_IDENTITIES_JSON"])
            for name in ("collector", "receiver-health"):
                certificate = x509.load_pem_x509_certificate((state / f"transport/{name}.crt").read_bytes())
                self.assertEqual(list(certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value), [ExtendedKeyUsageOID.CLIENT_AUTH])
            # The same cryptography output can be consumed by real TLS clients.
            context = ssl.create_default_context(cafile=str(state / "transport/ca.crt"))
            context.load_cert_chain(str(state / "transport/collector.crt"), str(state / "transport/collector.key"))

    def test_existing_transport_is_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            write_transport(state)
            original = (state / "transport/ca.crt").read_bytes()
            with self.assertRaises(FileExistsError):
                write_transport(state)
            self.assertEqual(original, (state / "transport/ca.crt").read_bytes())

    def test_refuse_symlink_or_public_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / "private"
            private.mkdir(mode=0o700)
            link = root / "link"
            link.symlink_to(private, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "community.transport.state.invalid"):
                write_transport(link)
            os.chmod(private, 0o755)
            with self.assertRaisesRegex(ValueError, "community.transport.state.invalid"):
                write_transport(private)

    def test_validate_rejects_exposure_wrong_identity_key_and_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            write_transport(state)
            key = state / "transport/collector.key"
            os.chmod(key, 0o644)
            with self.assertRaisesRegex(ValueError, "community.transport.invalid-or-expired"):
                validate_transport(state)
            os.chmod(key, 0o600)
            original = key.read_bytes()
            key.write_bytes((state / "transport/receiver.key").read_bytes())
            with self.assertRaisesRegex(ValueError, "community.transport.invalid-or-expired"):
                validate_transport(state)
            key.write_bytes(original)
            with patch("scripts.community_transport.datetime") as clock:
                clock.now.return_value = datetime.now(timezone.utc) + timedelta(days=366)
                with self.assertRaisesRegex(ValueError, "community.transport.invalid-or-expired"):
                    validate_transport(state)
            key.unlink()
            key.symlink_to(state / "transport/receiver.key")
            with self.assertRaisesRegex(ValueError, "community.transport.invalid-or-expired"):
                validate_transport(state)


class CommunityTopologyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compose = (ROOT / "deploy/docker-compose.community.yml").read_text()
        self.collector = (ROOT / "deploy/community/collector.yaml").read_text()

    def service(self, name: str) -> str:
        match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z][a-z-]*:|^[a-z]|\Z)", self.compose, re.M | re.S)
        self.assertIsNotNone(match)
        assert match is not None
        return match[1]

    def test_serve_nonroot_with_private_persistent_volumes_and_loopback_ports(self) -> None:
        self.assertIn('user: "0:0"', self.service("initialize"))
        self.assertIn("network_mode: none", self.service("initialize"))
        for name in ("postgres", "otel-collector", "prometheus", "grafana"):
            service = self.service(name)
            self.assertNotIn('user: "0:', service)
            self.assertIn("read_only: true", service)
            self.assertIn('cap_drop: ["ALL"]', service)
        for name in ("api", "ai-usage-receiver", "workflow-worker", "migrate"):
            self.assertIn("<<: *iip", self.service(name))
        self.assertIn('user: "10001:10001"', self.compose.split("x-database:")[0])
        ports = re.findall(r"ports: \[(.*)\]", self.compose)
        self.assertEqual(len(ports), 4)
        self.assertTrue(all(value.startswith('"127.0.0.1:') for value in ports))
        self.assertNotIn("ports:", self.service("postgres"))
        self.assertNotIn("ports:", self.service("ai-usage-receiver"))
        for volume in ("postgres-data", "collector-queue", "prometheus-data", "grafana-data"):
            self.assertIn(f"  {volume}:\n", self.compose.split("\nvolumes:")[1])
        self.assertIn("internal: true", self.compose.split("\nnetworks:")[1])
        self.assertNotIn("loki", self.compose)

    def test_no_fixture_or_authentication_downgrade(self) -> None:
        self.assertIn("IIP_DATABASE_TRANSPORT_MODE: verify-full", self.compose)
        for name in ("api", "workflow-worker", "ai-usage-receiver", "migrate"):
            self.assertIn("*database", self.service(name))
        for name in ("api", "workflow-worker"):
            self.assertIn("*economics", self.service(name))
        self.assertIn('IIP_AI_PRICE_CATALOG_REQUIRE_QUALIFICATION: "true"', self.compose)
        fixture_flags = re.findall(r'ALLOW_TEST_FIXTURES: "([^"\n]+)"', self.compose)
        self.assertEqual(fixture_flags, ["false", "false", "false"])
        self.assertIn("IIP_OTLP_TLS_MODE: mutual-spiffe", self.service("ai-usage-receiver"))
        self.assertIn('GF_AUTH_ANONYMOUS_ENABLED: "false"', self.service("grafana"))
        self.assertNotIn("POSTGRES_HOST_AUTH_METHOD", self.service("postgres"))
        self.assertIn("hostnossl all all 0.0.0.0/0 reject", (ROOT / "deploy/community/pg_hba.conf").read_text())

    def test_customer_trace_path_authenticates_and_filters_before_durable_queue(self) -> None:
        self.assertIn("processors: [memory_limiter, filter/metadata_only, transform/metadata_only, transform/scope_cleanup, groupbyattrs/metadata_resource, batch]", self.collector)
        self.assertIn("authenticator: bearertokenauth/input", self.collector)
        self.assertIn("cert_file: /run/iip/collector-input.crt", self.collector)
        self.assertIn("endpoint: https://ai-usage-receiver:4318", self.collector)
        for key in ("ca_file", "cert_file", "key_file"):
            self.assertIn(key, self.collector.split("exporters:")[1])
        self.assertIn("storage: file_storage", self.collector)
        self.assertIn("max_elapsed_time: 0s", self.collector)
        self.assertNotIn("logs:", self.collector.split("pipelines:")[1])
        self.assertEqual(self.collector.count("error_mode: propagate"), 3)
        self.assertIn("Len(events) > 0 or Len(links) > 0", self.collector)
        self.assertIn('set(status.message, "")', self.collector)
        self.assertIn('set(name, "")', self.collector)
        self.assertIn('set(attributes["cloud.region"], instrumentation_scope.attributes["cloud.region"])', self.collector)
        self.assertIn('delete_matching_keys(attributes, ".*")', self.collector)

    def test_init_reads_only_transport_dashboard_and_public_assets(self) -> None:
        initialize = self.service("initialize")
        self.assertEqual(initialize.count("type: bind"), 3)
        self.assertIn("target: /transport\n        read_only: true", initialize)
        self.assertIn("target: /dashboard.json\n        read_only: true", initialize)
        self.assertIn("target: /collector.yaml\n        read_only: true", initialize)
        self.assertNotIn("ca.key", (ROOT / "deploy/community/initialize.py").read_text())


if __name__ == "__main__":
    unittest.main()
