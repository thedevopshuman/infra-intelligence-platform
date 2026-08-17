"""Public console authentication discovery and OIDC PKCE boundary tests."""

from __future__ import annotations

import copy
import json
import os
import unittest
from email.message import Message
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from infra_intelligence_sdk import (
    ConsoleAuthenticationConfiguration,
    discover_console_authentication,
)
from iip.adapters.auth import (
    HashedBearerAuthenticator,
    OidcConfiguration,
)
from iip.application.ports import AuthenticationConfigurationError
from iip.bootstrap import build_runtime_from_env
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "console-oidc-test-token-0123456789abcdef0123456789abcdef"


def local_identity_config() -> str:
    return json.dumps(
        {
            "identities": [
                {
                    "tokenSha256": HashedBearerAuthenticator.token_sha256(TOKEN),
                    "actorId": "local-operator",
                    "tenantId": "local",
                    "roles": ["developer"],
                }
            ]
        }
    )


def oidc_config() -> dict[str, object]:
    return {
        "issuer": "https://identity.example.test/",
        "audience": "iip-control-plane",
        "jwksUrl": "https://identity.example.test/jwks",
        "tenantClaim": "tenant_id",
        "rolesClaim": "roles",
        "browser": {
            "clientId": "iip-console",
            "authorizationEndpoint": "https://identity.example.test/oauth2/authorize",
            "tokenEndpoint": "https://identity.example.test/oauth2/token",
            "redirectUri": "https://control-plane.example.test/console",
            "scopes": ["openid", "profile"],
            "providerLabel": "Organization SSO",
        },
    }


class ConsoleAuthenticationConfigurationTests(unittest.TestCase):
    def test_oidc_browser_profile_exposes_only_non_secret_public_client_fields(self) -> None:
        configuration = OidcConfiguration.from_json(json.dumps(oidc_config()))

        document = configuration.console_authentication_document()
        expected = json.loads(
            (ROOT / "contracts/examples/console-authentication-oidc.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(document, expected)
        serialized = json.dumps(document)
        for prohibited in (
            "jwksUrl",
            "audience",
            "tenantClaim",
            "rolesClaim",
            "caBundlePath",
            "clientSecret",
            "refresh_token",
        ):
            with self.subTest(prohibited=prohibited):
                self.assertNotIn(prohibited, serialized)
        self.assertEqual(
            configuration.console_token_origin,
            "https://identity.example.test",
        )

    def test_oidc_without_browser_profile_requires_an_issued_access_token(self) -> None:
        payload = oidc_config()
        payload.pop("browser")

        configuration = OidcConfiguration.from_json(json.dumps(payload))

        self.assertEqual(
            configuration.console_authentication_document()["spec"],
            {"mode": "access-token"},
        )
        self.assertIsNone(configuration.console_token_origin)

    def test_browser_profile_rejects_unsafe_or_ambiguous_configuration(self) -> None:
        invalid_mutations = (
            ("authorizationEndpoint", "http://identity.example.test/authorize"),
            ("authorizationEndpoint", "https://user@identity.example.test/authorize"),
            ("authorizationEndpoint", "https://identity.example.test/authorize?prompt=login"),
            ("tokenEndpoint", "https://identity.example.test/token#fragment"),
            ("tokenEndpoint", "https://identity.example.test/\nheader"),
            ("redirectUri", "http://control-plane.example.test/console"),
            ("redirectUri", "https://control-plane.example.test/callback"),
            ("redirectUri", "https://control-plane.example.test/console?next=/"),
            ("clientId", "client id with spaces"),
            ("scopes", ["profile"]),
            ("scopes", ["openid", "openid"]),
            ("providerLabel", "unsafe\nlabel"),
        )
        for field, value in invalid_mutations:
            with self.subTest(field=field, value=value):
                payload = oidc_config()
                browser = copy.deepcopy(payload["browser"])
                assert isinstance(browser, dict)
                browser[field] = value
                payload["browser"] = browser
                with self.assertRaisesRegex(
                    AuthenticationConfigurationError,
                    "authentication.configuration.invalid",
                ):
                    OidcConfiguration.from_json(json.dumps(payload))

    def test_loopback_redirect_is_the_only_http_exception(self) -> None:
        payload = oidc_config()
        browser = copy.deepcopy(payload["browser"])
        assert isinstance(browser, dict)
        browser["redirectUri"] = "http://127.0.0.1:8080/console"
        payload["browser"] = browser

        configuration = OidcConfiguration.from_json(json.dumps(payload))

        self.assertEqual(
            configuration.console_authentication_document()["spec"]["oidc"][
                "redirectUri"
            ],
            "http://127.0.0.1:8080/console",
        )


class ConsoleAuthenticationHttpTests(unittest.TestCase):
    @staticmethod
    def wire(handler: ApiHandler) -> tuple[list[int], list[tuple[str, str]]]:
        statuses: list[int] = []
        headers: list[tuple[str, str]] = []
        handler.send_response = lambda status: statuses.append(status)
        handler.send_header = lambda name, value: headers.append((name, value))
        handler.end_headers = lambda: None
        handler.wfile = BytesIO()
        return statuses, headers

    def test_local_discovery_is_public_but_session_remains_authenticated(self) -> None:
        with patch.dict(
            os.environ,
            {"IIP_AUTH_IDENTITIES_JSON": local_identity_config()},
            clear=True,
        ):
            runtime = build_runtime_from_env()
        self.addCleanup(runtime.close)
        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.path = "/v1/authentication/console"
        handler.headers = Message()
        statuses, headers = self.wire(handler)

        handler.do_GET()

        self.assertEqual(statuses, [HTTPStatus.OK.value])
        document = json.loads(handler.wfile.getvalue())
        expected = json.loads(
            (ROOT / "contracts/examples/console-authentication-local.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(document, expected)
        self.assertIn(("cache-control", "no-store"), headers)

        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.path = "/v1/session"
        handler.headers = Message()
        protected_statuses, _ = self.wire(handler)
        handler.do_GET()
        self.assertEqual(protected_statuses, [HTTPStatus.UNAUTHORIZED.value])

    def test_oidc_discovery_and_console_csp_share_only_the_token_origin(self) -> None:
        payload = oidc_config()
        with patch.dict(
            os.environ,
            {
                "IIP_AUTH_MODE": "oidc",
                "IIP_AUTH_OIDC_CONFIG_JSON": json.dumps(payload),
            },
            clear=True,
        ):
            runtime = build_runtime_from_env()
        self.addCleanup(runtime.close)

        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.path = "/v1/authentication/console"
        handler.headers = Message()
        statuses, _ = self.wire(handler)
        handler.do_GET()
        self.assertEqual(statuses, [HTTPStatus.OK.value])
        self.assertEqual(
            json.loads(handler.wfile.getvalue()),
            runtime.console_authentication,
        )

        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.path = "/console"
        handler.headers = Message()
        _, headers = self.wire(handler)
        handler.do_GET()
        policies = [value for name, value in headers if name == "content-security-policy"]
        self.assertEqual(len(policies), 1)
        self.assertIn(
            "connect-src 'self' https://identity.example.test",
            policies[0],
        )
        self.assertNotIn("/oauth2/token", policies[0])
        self.assertNotIn("clientId", policies[0])

    def test_discovery_rejects_query_without_authentication(self) -> None:
        with patch.dict(
            os.environ,
            {"IIP_AUTH_IDENTITIES_JSON": local_identity_config()},
            clear=True,
        ):
            runtime = build_runtime_from_env()
        self.addCleanup(runtime.close)
        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.path = "/v1/authentication/console?mode=oidc-pkce"
        handler.headers = Message()
        statuses, _ = self.wire(handler)

        handler.do_GET()

        self.assertEqual(statuses, [HTTPStatus.BAD_REQUEST.value])
        self.assertEqual(
            json.loads(handler.wfile.getvalue()),
            {"error": {"code": "request.invalid"}},
        )


class ConsoleAuthenticationAssetTests(unittest.TestCase):
    def test_console_contains_pkce_state_bounded_exchange_and_manual_fallback(self) -> None:
        script = (ROOT / "src/iip/surfaces/static/app.js").read_text(
            encoding="utf-8"
        )
        page = (ROOT / "src/iip/surfaces/static/index.html").read_text(
            encoding="utf-8"
        )

        for expected in (
            "/v1/authentication/console",
            "code_challenge_method",
            '"S256"',
            "OIDC_TRANSACTION_MAX_AGE_MILLIS",
            "window.history.replaceState",
            'credentials: "omit"',
            'redirect: "error"',
            "readBoundedJson",
            "sessionStorage.removeItem(OIDC_TRANSACTION_KEY)",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, script)
        self.assertIn("Continue with organization SSO", page)
        self.assertIn("Issued access token", script)
        self.assertNotIn("client_secret", script)
        self.assertNotIn("refresh_token", script)


class ConsoleAuthenticationSdkTests(unittest.TestCase):
    def test_python_sdk_discovers_without_a_credential_and_bounds_the_response(self) -> None:
        payload = (
            ROOT / "contracts/examples/console-authentication-oidc.json"
        ).read_bytes()
        observed = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, maximum):
                self.maximum = maximum
                return payload

        response = Response()

        def open_request(request, *, timeout):
            observed.append((request, timeout))
            return response

        with patch(
            "infra_intelligence_sdk.client.urlopen",
            side_effect=open_request,
        ):
            configuration = discover_console_authentication(
                "https://control-plane.example.test/",
                timeout_seconds=4,
            )

        self.assertIsInstance(configuration, ConsoleAuthenticationConfiguration)
        self.assertEqual(configuration.to_dict()["spec"]["mode"], "oidc-pkce")
        request, timeout = observed[0]
        self.assertEqual(
            request.full_url,
            "https://control-plane.example.test/v1/authentication/console",
        )
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(timeout, 4)
        self.assertEqual(response.maximum, 65_537)

    def test_python_sdk_rejects_crossed_modes_and_boolean_timeout(self) -> None:
        crossed = json.loads(
            (ROOT / "contracts/examples/console-authentication-local.json").read_text(
                encoding="utf-8"
            )
        )
        crossed["spec"]["oidc"] = oidc_config()["browser"]

        with self.assertRaisesRegex(ValueError, "inconsistent"):
            ConsoleAuthenticationConfiguration.from_dict(crossed)
        with self.assertRaisesRegex(ValueError, "timeout_seconds"):
            discover_console_authentication(
                "https://control-plane.example.test",
                timeout_seconds=True,
            )


if __name__ == "__main__":
    unittest.main()
