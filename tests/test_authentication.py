from __future__ import annotations

import json
import os
import unittest
from base64 import urlsafe_b64encode
from datetime import datetime, timedelta, timezone
from email.message import Message
from http import HTTPStatus
from io import BytesIO
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from iip.adapters.auth import (
    HashedBearerAuthenticator,
    OidcConfiguration,
    OidcJwtAuthenticator,
)
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

    def test_runtime_composes_explicit_oidc_mode_without_fetching_at_startup(self) -> None:
        configuration = json.dumps(
            {
                "issuer": "https://identity.example.test/",
                "audience": "iip-control-plane",
                "jwksUrl": "https://identity.example.test/jwks",
                "tenantClaim": "tenant_id",
                "rolesClaim": "roles",
            }
        )
        with patch.dict(
            os.environ,
            {"IIP_AUTH_MODE": "oidc", "IIP_AUTH_OIDC_CONFIG_JSON": configuration},
            clear=True,
        ):
            runtime = build_runtime_from_env()

        self.assertIsInstance(runtime.authenticator, OidcJwtAuthenticator)

    def test_unknown_authentication_mode_fails_at_startup(self) -> None:
        with patch.dict(os.environ, {"IIP_AUTH_MODE": "unknown"}, clear=True):
            with self.assertRaisesRegex(
                AuthenticationConfigurationError, "configuration.invalid"
            ):
                build_runtime_from_env()


class OidcJwtAuthenticatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.private_key.public_key().public_numbers()

        def encoded(value: int) -> str:
            raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
            return urlsafe_b64encode(raw).decode("ascii").rstrip("=")

        self.jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": "test-key-1",
                    "use": "sig",
                    "alg": "RS256",
                    "n": encoded(numbers.n),
                    "e": encoded(numbers.e),
                }
            ]
        }
        self.configuration = OidcConfiguration(
            issuer="https://identity.example.test/",
            audience="iip-control-plane",
            jwks_url="https://identity.example.test/.well-known/jwks.json",
            tenant_claim="iip_tenant_id",
            roles_claim="iip_roles",
        )

    def token(self, **overrides) -> str:
        now = datetime.now(timezone.utc)
        claims = {
            "iss": self.configuration.issuer,
            "aud": self.configuration.audience,
            "sub": "oidc-user-17",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "iip_tenant_id": "tenant-a",
            "iip_roles": ["developer", "approver"],
            **overrides,
        }
        return jwt.encode(
            claims,
            self.private_key,
            algorithm="RS256",
            headers={"kid": "test-key-1", "typ": "JWT"},
        )

    def authenticator(self):
        outer = self

        class Transport:
            def __init__(inner_self):
                inner_self.calls = 0

            def fetch(inner_self, url, context):
                del context
                outer.assertEqual(url, outer.configuration.jwks_url)
                inner_self.calls += 1
                return outer.jwks

        transport = Transport()
        return OidcJwtAuthenticator(self.configuration, transport), transport

    def test_verified_oidc_claims_derive_actor_tenant_and_roles(self) -> None:
        authenticator, transport = self.authenticator()

        actor = authenticator.authenticate_bearer(self.token())
        repeated = authenticator.authenticate_bearer(self.token())

        self.assertEqual(actor.actor_id, "oidc-user-17")
        self.assertEqual(actor.tenant_id, "tenant-a")
        self.assertEqual(actor.roles, ("developer", "approver"))
        self.assertEqual(repeated, actor)
        self.assertEqual(transport.calls, 1)

    def test_wrong_audience_expiry_and_malformed_roles_fail_closed(self) -> None:
        authenticator, _ = self.authenticator()
        now = datetime.now(timezone.utc)

        for token in (
            self.token(aud="another-api"),
            self.token(exp=now - timedelta(minutes=1)),
            self.token(iip_roles="developer"),
        ):
            with self.assertRaisesRegex(AuthenticationError, "authentication.invalid"):
                authenticator.authenticate_bearer(token)

    def test_unknown_key_cannot_force_an_immediate_jwks_refetch(self) -> None:
        authenticator, transport = self.authenticator()
        now = datetime.now(timezone.utc)
        unknown = jwt.encode(
            {
                "iss": self.configuration.issuer,
                "aud": self.configuration.audience,
                "sub": "oidc-user-17",
                "iat": now,
                "exp": now + timedelta(minutes=5),
                "iip_tenant_id": "tenant-a",
                "iip_roles": ["developer"],
            },
            self.private_key,
            algorithm="RS256",
            headers={"kid": "rotated-key"},
        )

        with self.assertRaisesRegex(AuthenticationError, "^authentication.invalid$"):
            authenticator.authenticate_bearer(unknown)

        self.assertEqual(transport.calls, 1)

    def test_oidc_configuration_is_tls_only_and_closed(self) -> None:
        valid = {
            "issuer": "https://identity.example.test/",
            "audience": "iip-control-plane",
            "jwksUrl": "https://identity.example.test/jwks",
            "tenantClaim": "tenant_id",
            "rolesClaim": "roles",
        }
        parsed = OidcConfiguration.from_json(json.dumps(valid))
        self.assertEqual(parsed.actor_claim, "sub")

        for invalid in (
            {**valid, "issuer": "http://identity.example.test"},
            {**valid, "jwksUrl": "http://identity.example.test/jwks"},
            {**valid, "unexpected": True},
            {**valid, "clockSkewSeconds": 301},
        ):
            with self.assertRaises(AuthenticationConfigurationError):
                OidcConfiguration.from_json(json.dumps(invalid))


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
