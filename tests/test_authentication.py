from __future__ import annotations

import json
import os
import unittest
from email.message import Message
from http import HTTPStatus
from io import BytesIO
from unittest.mock import patch

from iip.adapters.auth import HashedBearerAuthenticator
from iip.application.ports import (
    ActorContext,
    AuthenticationConfigurationError,
    AuthenticationError,
)
from iip.bootstrap import build_runtime_from_env
from iip.surfaces.http import ApiHandler


TOKEN = "test-reference-token-0123456789abcdef0123456789abcdef"
WRONG_TOKEN = "wrong-reference-token-0123456789abcdef0123456789abcde"


def identity_config(
    *,
    token: str = TOKEN,
    actor_id: str = "local-developer",
    tenant_id: str = "local",
    roles: tuple[str, ...] = ("developer", "resource-reader"),
) -> str:
    return json.dumps(
        {
            "identities": [
                {
                    "tokenSha256": HashedBearerAuthenticator.token_sha256(token),
                    "actorId": actor_id,
                    "tenantId": tenant_id,
                    "roles": list(roles),
                }
            ]
        }
    )


class HashedBearerAuthenticatorTests(unittest.TestCase):
    def test_valid_credential_derives_actor_tenant_and_roles(self) -> None:
        authenticator = HashedBearerAuthenticator.from_json(identity_config())

        actor = authenticator.authenticate_bearer(TOKEN)

        self.assertEqual(
            actor,
            ActorContext(
                "local-developer",
                "local",
                ("developer", "resource-reader"),
            ),
        )
        self.assertNotIn(TOKEN, repr(authenticator.__dict__))

    def test_unknown_or_malformed_credential_has_one_stable_failure(self) -> None:
        authenticator = HashedBearerAuthenticator.from_json(identity_config())

        for token in (WRONG_TOKEN, "short", "contains whitespace in credential"):
            with self.subTest(token=token):
                with self.assertRaises(AuthenticationError) as raised:
                    authenticator.authenticate_bearer(token)
                self.assertEqual(str(raised.exception), "authentication.invalid")
                self.assertNotIn(token, str(raised.exception))

    def test_configuration_fails_closed_without_exposing_input(self) -> None:
        malformed = json.dumps(
            {
                "identities": [
                    {
                        "rawToken": TOKEN,
                        "actorId": "local-developer",
                        "tenantId": "local",
                        "roles": ["developer"],
                    }
                ]
            }
        )

        with self.assertRaises(AuthenticationConfigurationError) as raised:
            HashedBearerAuthenticator.from_json(malformed)

        self.assertEqual(
            str(raised.exception),
            "authentication.configuration.invalid",
        )
        self.assertNotIn(TOKEN, str(raised.exception))

    def test_duplicate_verifier_and_anonymous_identity_are_rejected(self) -> None:
        entry = json.loads(identity_config())["identities"][0]
        duplicate = json.dumps({"identities": [entry, entry]})
        anonymous = identity_config(actor_id="anonymous")

        for configuration in (duplicate, anonymous):
            with self.subTest(configuration=configuration):
                with self.assertRaises(AuthenticationConfigurationError):
                    HashedBearerAuthenticator.from_json(configuration)


class AuthenticationCompositionTests(unittest.TestCase):
    def test_runtime_requires_authentication_configuration(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                AuthenticationConfigurationError,
                "authentication.configuration.required",
            ):
                build_runtime_from_env()

    def test_runtime_composes_configured_authenticator(self) -> None:
        with patch.dict(
            os.environ,
            {"IIP_AUTH_IDENTITIES_JSON": identity_config()},
            clear=True,
        ):
            runtime = build_runtime_from_env()

        self.assertEqual(
            runtime.authenticator.authenticate_bearer(TOKEN).tenant_id,
            "local",
        )


class AuthenticationHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        with patch.dict(
            os.environ,
            {"IIP_AUTH_IDENTITIES_JSON": identity_config()},
            clear=True,
        ):
            runtime = build_runtime_from_env()
        self.handler = object.__new__(ApiHandler)
        self.handler.runtime = runtime
        self.handler.path = "/v1/resources"
        self.responses: list[tuple[HTTPStatus, dict]] = []
        self.handler._json = lambda status, payload: self.responses.append(
            (status, payload)
        )

    def test_health_is_public_but_api_requires_bearer_credential(self) -> None:
        self.handler.headers = {}
        self.handler.path = "/healthz"

        self.handler.do_GET()

        self.assertEqual(self.responses, [(HTTPStatus.OK, {"status": "ok"})])
        self.responses.clear()
        self.handler.path = "/v1/resources"

        self.handler.do_GET()

        self.assertEqual(
            self.responses,
            [
                (
                    HTTPStatus.UNAUTHORIZED,
                    {"error": {"code": "authentication.required"}},
                )
            ],
        )

        self.responses.clear()
        self.handler.path = "/v1/resources?unknown=value"
        self.handler.do_GET()
        self.assertEqual(self.responses[0][0], HTTPStatus.UNAUTHORIZED)

    def test_verified_token_allows_api_and_spoofed_identity_headers_do_not(self) -> None:
        self.handler.headers = {
            "authorization": f"Bearer {TOKEN}",
            "x-iip-tenant-id": "another-tenant",
            "x-iip-actor-id": "spoofed-actor",
        }

        self.handler.do_GET()

        self.assertEqual(self.responses, [(HTTPStatus.OK, {"items": []})])

    def test_post_authenticates_before_reading_request_body(self) -> None:
        self.handler.headers = {}
        body_read = False

        def read_body() -> dict:
            nonlocal body_read
            body_read = True
            return {}

        self.handler._read_json = read_body

        self.handler.do_POST()

        self.assertFalse(body_read)
        self.assertEqual(self.responses[0][0], HTTPStatus.UNAUTHORIZED)

    def test_duplicate_authorization_headers_are_rejected(self) -> None:
        headers = Message()
        headers.add_header("Authorization", f"Bearer {TOKEN}")
        headers.add_header("Authorization", f"Bearer {TOKEN}")
        self.handler.headers = headers

        self.handler.do_GET()

        self.assertEqual(self.responses[0][0], HTTPStatus.UNAUTHORIZED)
        self.assertEqual(
            self.responses[0][1]["error"]["code"],
            "authentication.invalid",
        )

    def test_unauthorized_response_advertises_bearer_authentication(self) -> None:
        statuses = []
        headers = []
        self.handler.send_response = lambda status: statuses.append(status)
        self.handler.send_header = lambda name, value: headers.append((name, value))
        self.handler.end_headers = lambda: None
        self.handler.wfile = BytesIO()

        ApiHandler._json(
            self.handler,
            HTTPStatus.UNAUTHORIZED,
            {"error": {"code": "authentication.required"}},
        )

        self.assertEqual(statuses, [HTTPStatus.UNAUTHORIZED.value])
        self.assertIn(("WWW-Authenticate", "Bearer"), headers)


if __name__ == "__main__":
    unittest.main()
