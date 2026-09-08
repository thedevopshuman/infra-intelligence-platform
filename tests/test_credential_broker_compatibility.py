from __future__ import annotations

import base64
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from iip.adapters.credential_broker import CredentialBrokerUnavailableError
from scripts import run_credential_broker_compatibility as compatibility
from scripts import validate_repo


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples"


def decode_claims(token: str) -> dict[str, object]:
    encoded = token.split(".")[1]
    encoded += "=" * ((4 - len(encoded) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))


class CredentialBrokerCompatibilityTests(unittest.TestCase):
    def test_denial_helper_requires_the_expected_stable_error(self) -> None:
        broker = Mock()
        broker.resolve.side_effect = CredentialBrokerUnavailableError(
            "credential.broker.request.denied"
        )

        compatibility.expect_stable_denial(
            broker,
            compatibility.lease_request(),
            expected_error="credential.broker.request.denied",
        )
        with self.assertRaisesRegex(RuntimeError, "unstable error"):
            compatibility.expect_stable_denial(
                broker,
                compatibility.lease_request(),
                expected_error="credential.broker.upstream.unavailable",
            )

    def test_workload_tokens_are_signed_short_lived_and_generation_bound(self) -> None:
        now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        token_a = compatibility.workload_token(
            "fixture-signing-key",
            issuer="https://issuer.fixture.invalid",
            audience="iip-credential-broker",
            subject="system:serviceaccount:iip-system:iip-api",
            generation="generation-a",
            now=now,
        )
        token_b = compatibility.workload_token(
            "fixture-signing-key",
            issuer="https://issuer.fixture.invalid",
            audience="iip-credential-broker",
            subject="system:serviceaccount:iip-system:iip-api",
            generation="generation-b",
            now=now,
        )

        claims = decode_claims(token_a)
        self.assertEqual(claims["iss"], "https://issuer.fixture.invalid")
        self.assertEqual(claims["aud"], "iip-credential-broker")
        self.assertEqual(claims["jti"], "generation-a")
        self.assertEqual(claims["exp"] - claims["iat"], 301)
        self.assertNotEqual(token_a, token_b)
        self.assertNotIn("fixture-signing-key", token_a)

    def test_report_is_closed_source_bound_and_schema_valid(self) -> None:
        report = compatibility.compatibility_report(
            revision="0123456789abcdef0123456789abcdef01234567",
            source_dirty=True,
            docker_platform="linux/arm64",
            docker_version="28.3.3",
        )

        compatibility.validate_report(report)
        self.assertTrue(report["metadata"]["sourceDirty"])
        self.assertEqual(
            [check["id"] for check in report["spec"]["checks"]],
            list(compatibility.CHECK_IDS),
        )
        self.assertEqual(report["spec"]["summary"]["overallStatus"], "compatible")

    def test_semantic_validation_rejects_inconsistent_summary(self) -> None:
        report = json.loads(
            (EXAMPLES / "credential-broker-compatibility-report.json").read_text(
                encoding="utf-8"
            )
        )
        report["spec"]["checks"][0] = {
            "id": "ca-verified-tls",
            "status": "failed",
            "errorCode": "credential.broker.upstream.unavailable",
        }

        errors: list[str] = []
        validate_repo.validate_credential_broker_compatibility_document(
            report, errors
        )

        self.assertIn(
            "credential broker compatibility status must match its checks", errors
        )
        self.assertIn(
            "credential broker compatibility summary must match its checks", errors
        )


if __name__ == "__main__":
    unittest.main()
