from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from scripts import run_oidc_issuer_compatibility as compatibility
from scripts import validate_repo


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples"


class OidcIssuerCompatibilityTests(unittest.TestCase):
    def test_public_jwk_contains_no_private_rsa_parameters(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        document = compatibility.public_jwk(private_key, "generation-a")

        self.assertEqual(document["alg"], "RS256")
        self.assertEqual(document["key_ops"], ["verify"])
        self.assertEqual(set(document), {"kty", "kid", "use", "key_ops", "alg", "n", "e"})
        self.assertTrue(set(document).isdisjoint({"d", "p", "q", "dp", "dq", "qi"}))

    def test_token_and_discovery_are_exact_and_verifier_policy_is_minimized(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = compatibility.access_token(private_key, "generation-a")
        header = jwt.get_unverified_header(token)
        claims = jwt.decode(token, options={"verify_signature": False})

        self.assertEqual(header["alg"], "RS256")
        self.assertEqual(header["kid"], "generation-a")
        self.assertEqual(claims["aud"], "iip-control-plane")
        self.assertEqual(claims["iip_tenant_id"], "tenant-a")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ca.crt").write_text("unused", encoding="utf-8")
            discovery = compatibility.configuration(root).console_authentication_document()
        encoded = json.dumps(discovery, sort_keys=True)
        self.assertEqual(discovery["spec"]["oidc"]["pkceMethod"], "S256")
        self.assertNotIn("iip-control-plane", encoded)
        self.assertNotIn("iip_tenant_id", encoded)
        self.assertNotIn("/jwks", encoded)

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
            (EXAMPLES / "oidc-issuer-compatibility-report.json").read_text(
                encoding="utf-8"
            )
        )
        report["spec"]["checks"][0] = {
            "id": "ca-verified-tls",
            "status": "failed",
            "errorCode": "authentication.invalid",
        }

        errors: list[str] = []
        validate_repo.validate_oidc_issuer_compatibility_document(report, errors)

        self.assertIn("OIDC issuer compatibility status must match its checks", errors)
        self.assertIn("OIDC issuer compatibility summary must match its checks", errors)


if __name__ == "__main__":
    unittest.main()
