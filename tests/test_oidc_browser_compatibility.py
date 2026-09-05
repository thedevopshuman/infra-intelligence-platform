from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from iip.application.ports import ActorContext
from scripts import run_oidc_browser_compatibility as compatibility
from scripts import validate_repo


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples"


def profile() -> dict[str, object]:
    return {
        "issuer": "https://identity.example.test/",
        "authorizationEndpoint": "https://identity.example.test/authorize",
        "tokenEndpoint": "https://identity.example.test/token",
        "redirectUri": "https://control-plane.example.test/console",
        "clientId": "iip-console",
        "scopes": ["openid", "profile"],
    }


class OidcBrowserCompatibilityTests(unittest.TestCase):
    def test_exchanged_token_crosses_the_http_session_boundary(self) -> None:
        class Authenticator:
            def authenticate_bearer(self, token: str) -> ActorContext:
                self.token = token
                return ActorContext(
                    actor_id="oidc-user-17",
                    tenant_id="tenant-a",
                    roles=("developer", "approver"),
                )

        authenticator = Authenticator()
        token = "t" * 43

        session = compatibility.api_session(authenticator, token)

        self.assertEqual(authenticator.token, token)
        self.assertEqual(
            session,
            {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "SessionContext",
                "metadata": {
                    "tenantId": "tenant-a",
                    "actorId": "oidc-user-17",
                },
                "spec": {"roles": ["approver", "developer"]},
            },
        )

    def test_make_and_ci_run_both_oidc_profiles(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

        self.assertIn("test-oidc: test-oidc-verifier test-oidc-browser", makefile)
        self.assertIn("test-oidc-browser:", makefile)
        self.assertIn("identity-and-policy-compatibility:", workflow)
        self.assertIn("make test-oidc", workflow)

    def test_wire_documents_match_the_console_public_client_profile(self) -> None:
        verifier = "v" * 64
        state = "s" * 43

        authorization = urlsplit(
            compatibility.authorization_url(
                profile(),
                verifier=verifier,
                state=state,
            )
        )
        parameters = parse_qs(authorization.query, strict_parsing=True)
        self.assertEqual(authorization.path, "/authorize")
        self.assertEqual(
            parameters,
            {
                "response_type": ["code"],
                "client_id": ["iip-console"],
                "redirect_uri": ["https://control-plane.example.test/console"],
                "scope": ["openid profile"],
                "state": [state],
                "code_challenge": [compatibility.pkce_challenge(verifier)],
                "code_challenge_method": ["S256"],
            },
        )

        form = parse_qs(
            compatibility.token_form(
                profile(),
                code="c" * 43,
                verifier=verifier,
            ).decode("ascii"),
            strict_parsing=True,
        )
        self.assertEqual(
            form,
            {
                "grant_type": ["authorization_code"],
                "client_id": ["iip-console"],
                "code": ["c" * 43],
                "redirect_uri": ["https://control-plane.example.test/console"],
                "code_verifier": [verifier],
            },
        )
        self.assertNotIn("client_secret", form)
        self.assertNotIn("refresh_token", form)

    def test_report_is_closed_content_addressed_and_schema_valid(self) -> None:
        report = compatibility.compatibility_report(
            revision="0123456789abcdef0123456789abcdef01234567",
            source_dirty=True,
            docker_platform="linux/arm64",
            docker_version="29.1.3",
        )

        compatibility.validate_report(report)
        self.assertEqual(report["metadata"]["id"], compatibility.report_id(report))
        self.assertTrue(report["metadata"]["sourceDirty"])
        self.assertEqual(
            [check["id"] for check in report["spec"]["checks"]],
            list(compatibility.CHECK_IDS),
        )
        encoded = json.dumps(report, sort_keys=True)
        for prohibited in (
            '"authorizationEndpoint":',
            '"tokenEndpoint":',
            '"redirectUri":',
            '"clientId":',
            '"access_token":',
            '"code_verifier":',
        ):
            with self.subTest(prohibited=prohibited):
                self.assertNotIn(prohibited, encoded)

    def test_semantic_validation_rejects_tampering(self) -> None:
        report = json.loads(
            (EXAMPLES / "oidc-browser-compatibility-report.json").read_text(
                encoding="utf-8"
            )
        )
        report["metadata"]["id"] = compatibility.report_id(report)

        changed = copy.deepcopy(report)
        changed["spec"]["checks"][0] = {
            "id": "ca-verified-browser-endpoints",
            "status": "failed",
            "errorCode": "authentication.browser.tls-failed",
        }
        errors: list[str] = []
        validate_repo.validate_oidc_browser_compatibility_document(changed, errors)
        self.assertIn("OIDC browser compatibility status must match its checks", errors)
        self.assertIn("OIDC browser compatibility summary must match its checks", errors)
        self.assertIn("OIDC browser compatibility ID must match its content", errors)

        reordered = copy.deepcopy(report)
        reordered["spec"]["checks"].reverse()
        reordered["metadata"]["id"] = compatibility.report_id(reordered)
        errors = []
        validate_repo.validate_oidc_browser_compatibility_document(
            reordered,
            errors,
        )
        self.assertIn(
            "OIDC browser compatibility checks must match the closed profile",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
