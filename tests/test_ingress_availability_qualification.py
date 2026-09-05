from __future__ import annotations

import copy
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from iip.adapters.auth import HashedBearerAuthenticator
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from qualify_ingress_availability import (  # noqa: E402
    CHECK_IDS,
    IngressQualificationError,
    generate_report,
    validate_report_document,
    verify_report,
)


TOKEN = "ingress-qualification-token-0123456789abcdef"


class IngressAvailabilityQualificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.token_file = self.root / "bearer-token"
        self.token_file.write_text(TOKEN + "\n", encoding="ascii")
        self.output = self.root / "report.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _actual_server(self):
        authenticator = HashedBearerAuthenticator.from_json(
            json.dumps(
                {
                    "identities": [
                        {
                            "tokenSha256": HashedBearerAuthenticator.token_sha256(
                                TOKEN
                            ),
                            "actorId": "synthetic-operator",
                            "tenantId": "tenant-private",
                            "roles": ["developer"],
                        }
                    ]
                }
            )
        )
        runtime = build_local_runtime(authenticator)
        requests: list[tuple[str, str | None]] = []

        class Handler(ApiHandler):
            def log_message(self, format: str, *args: object) -> None:
                del format, args

            def do_GET(self) -> None:  # noqa: N802
                requests.append((self.path, self.headers.get("Authorization")))
                super().do_GET()

        Handler.runtime = runtime
        with patch("http.server.socket.getfqdn", return_value="localhost"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return runtime, requests, server, thread

    def test_local_probe_reaches_real_routes_and_retains_only_aggregates(self) -> None:
        runtime, requests, server, thread = self._actual_server()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            report = generate_report(
                profile="local-loopback",
                base_url=base_url,
                token_file=self.token_file,
                output=self.output,
                sample_count=3,
                minimum_availability_basis_points=10_000,
                maximum_p95_latency_milliseconds=2000,
                request_timeout_milliseconds=2000,
                interval_milliseconds=0,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            runtime.close()

        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(report["spec"]["summary"]["totalChecks"], len(CHECK_IDS))
        self.assertEqual(report["spec"]["measurements"]["successfulSamples"], 3)
        self.assertEqual(
            [item["id"] for item in report["spec"]["measurements"]["paths"]],
            ["liveness", "readiness", "runtime-identity"],
        )
        for path, authorization in requests:
            if path == "/v1/system/version":
                self.assertEqual(authorization, f"Bearer {TOKEN}")
            else:
                self.assertIsNone(authorization)
        serialized = json.dumps(report, sort_keys=True)
        for forbidden in (TOKEN, base_url, "tenant-private", "synthetic-operator"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(verify_report(self.output)["spec"]["status"], "qualified")

    def test_redirect_is_not_followed_and_credential_does_not_move(self) -> None:
        redirected_requests: list[str] = []

        class RedirectHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                del format, args

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/v1/system/version":
                    self.send_response(302)
                    self.send_header("Location", "/credential-capture")
                    self.end_headers()
                    return
                if self.path == "/credential-capture":
                    redirected_requests.append(self.headers.get("Authorization", ""))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status":"ok"}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')

        with patch("http.server.socket.getfqdn", return_value="localhost"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            report = generate_report(
                profile="local-loopback",
                base_url=f"http://127.0.0.1:{server.server_port}",
                token_file=self.token_file,
                output=self.output,
                sample_count=3,
                minimum_availability_basis_points=1,
                interval_milliseconds=0,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(report["spec"]["status"], "not-qualified")
        self.assertEqual(
            report["spec"]["measurements"]["failureCategories"]["http-status"],
            3,
        )
        self.assertEqual(redirected_requests, [])

    def test_customer_profile_requires_https_release_inputs_and_sample_floor(self) -> None:
        cases = (
            (
                {"sample_count": 99, "base_url": "https://iip.example.test", "image_digest": "sha256:" + "a" * 64},
                "ingress-qualification.objective.customer-samples-insufficient",
            ),
            (
                {"sample_count": 100, "base_url": "http://iip.example.test", "image_digest": "sha256:" + "a" * 64},
                "ingress-qualification.transport.https-required",
            ),
            (
                {"sample_count": 100, "base_url": "https://iip.example.test", "image_digest": None},
                "ingress-qualification.image.required",
            ),
        )
        for overrides, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(IngressQualificationError, code):
                    generate_report(
                        profile="customer-ingress",
                        token_file=self.token_file,
                        output=self.output,
                        interval_milliseconds=0,
                        **overrides,
                    )

    def test_credential_symlink_is_rejected_before_network_access(self) -> None:
        linked = self.root / "linked-token"
        linked.symlink_to(self.token_file)
        with self.assertRaisesRegex(
            IngressQualificationError,
            "ingress-qualification.credential.invalid",
        ):
            generate_report(
                profile="local-loopback",
                base_url="http://127.0.0.1:9",
                token_file=linked,
                output=self.output,
                sample_count=3,
                interval_milliseconds=0,
            )

    def test_semantic_validation_rejects_tampered_arithmetic_and_check_status(self) -> None:
        runtime, _, server, thread = self._actual_server()
        try:
            report = generate_report(
                profile="local-loopback",
                base_url=f"http://127.0.0.1:{server.server_port}",
                token_file=self.token_file,
                output=self.output,
                sample_count=3,
                interval_milliseconds=0,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            runtime.close()

        tampered = copy.deepcopy(report)
        tampered["spec"]["measurements"]["successfulSamples"] = 2
        with self.assertRaisesRegex(
            IngressQualificationError,
            "ingress-qualification.report.measurements-invalid",
        ):
            validate_report_document(tampered)

        tampered = copy.deepcopy(report)
        tampered["spec"]["checks"][-1] = {
            "id": "latency-objective",
            "status": "failed",
            "errorCode": "ingress-qualification.latency.objective-missed",
        }
        with self.assertRaisesRegex(
            IngressQualificationError,
            "ingress-qualification.report.checks-invalid",
        ):
            validate_report_document(tampered)

    def test_contract_example_is_schema_and_semantically_valid(self) -> None:
        schema = json.loads(
            (
                ROOT
                / "contracts/schemas/ingress-availability-qualification-report.schema.json"
            ).read_text(encoding="utf-8")
        )
        example = json.loads(
            (
                ROOT
                / "contracts/examples/ingress-availability-qualification-report.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(example)
        validate_report_document(example)

    def test_source_is_direct_bounded_and_credential_file_only(self) -> None:
        source = (ROOT / "scripts/qualify_ingress_availability.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("ProxyHandler({})", source)
        self.assertIn("_DenyRedirects()", source)
        self.assertIn("MAX_RESPONSE_BYTES + 1", source)
        self.assertIn('headers["Authorization"]', source)
        self.assertNotIn("--token\"", source)
        self.assertNotIn("IIP_AUTH_BEARER_TOKEN", source)


if __name__ == "__main__":
    unittest.main()
