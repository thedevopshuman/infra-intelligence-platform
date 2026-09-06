"""Customer OIDC qualification contract and external-boundary tests."""

from __future__ import annotations

import base64
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator, FormatChecker

from scripts import qualify_customer_oidc as qualification


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts/examples"
SCHEMAS = ROOT / "contracts/schemas"
REVISION = "0123456789abcdef0123456789abcdef01234567"
IMAGE = "sha256:" + "a" * 64
NOW = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)


def document(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def encoded(value: object) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(value, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")


def access_token(profile: dict[str, object]) -> str:
    spec = profile["spec"]
    assert isinstance(spec, dict)
    identity = spec["identity"]
    oidc = spec["oidc"]
    assert isinstance(identity, dict)
    assert isinstance(oidc, dict)
    header = {"alg": "RS256", "kid": "customer-key-1", "typ": "at+jwt"}
    claims = {
        "iss": oidc["issuer"],
        "aud": oidc["audience"],
        "sub": identity["actorId"],
        oidc["actorClaim"]: identity["actorId"],
        oidc["tenantClaim"]: identity["tenantId"],
        oidc["rolesClaim"]: identity["roles"],
        "iat": int(NOW.timestamp()) - 60,
        "exp": int(NOW.timestamp()) + 840,
    }
    return f"{encoded(header)}.{encoded(claims)}.{encoded('signature-material-1234567890')}"


def json_response(status: int, value: object, **headers: str) -> qualification.HttpResponse:
    return qualification.HttpResponse(
        status,
        (("Content-Type", "application/json"), *tuple(headers.items())),
        json.dumps(value, separators=(",", ":")).encode("utf-8"),
    )


class FakeClient:
    def __init__(self, profile: dict[str, object], token: str) -> None:
        spec = profile["spec"]
        assert isinstance(spec, dict)
        self.identity = spec["identity"]
        self.oidc = spec["oidc"]
        assert isinstance(self.identity, dict)
        assert isinstance(self.oidc, dict)
        self.browser = self.oidc["browser"]
        assert isinstance(self.browser, dict)
        self.token = token
        self.requests: list[tuple[str, bool, str]] = []

    @property
    def metadata(self) -> dict[str, object]:
        return {
            "issuer": self.oidc["issuer"],
            "authorization_endpoint": self.browser["authorizationEndpoint"],
            "token_endpoint": self.browser["tokenEndpoint"],
            "jwks_uri": self.oidc["jwksUrl"],
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }

    def request(
        self,
        url: str,
        *,
        issuer: bool,
        method: str = "GET",
        headers: dict[str, str] | None = None,
    ) -> qualification.HttpResponse:
        supplied = dict(headers or {})
        self.requests.append((url, issuer, method))
        if url == self.oidc["discoveryUrl"]:
            return json_response(200, self.metadata)
        if url == self.oidc["jwksUrl"]:
            return json_response(
                200,
                {
                    "keys": [
                        {
                            "kid": "customer-key-1",
                            "kty": "RSA",
                            "alg": "RS256",
                            "use": "sig",
                            "n": "public-modulus",
                            "e": "AQAB",
                        }
                    ]
                },
            )
        if method == "OPTIONS" and url == self.browser["tokenEndpoint"]:
            origin = supplied.get("Origin")
            if origin == "https://control-plane.example.test":
                return qualification.HttpResponse(
                    204,
                    (
                        ("Access-Control-Allow-Origin", origin),
                        ("Access-Control-Allow-Methods", "POST"),
                        ("Access-Control-Allow-Headers", "content-type"),
                    ),
                    b"",
                )
            return qualification.HttpResponse(403, (), b"")
        if url.endswith("/v1/authentication/console"):
            return json_response(
                200, qualification._expected_console_document(PROFILE)
            )
        authorization = supplied.get("Authorization")
        if url.endswith("/v1/session") and authorization == f"Bearer {self.token}":
            return json_response(
                200,
                {
                    "apiVersion": qualification.API_VERSION,
                    "kind": "SessionContext",
                    "metadata": {
                        "tenantId": self.identity["tenantId"],
                        "actorId": self.identity["actorId"],
                    },
                    "spec": {"roles": self.identity["roles"]},
                },
            )
        if url.endswith("/v1/session"):
            return json_response(401, {"code": "authentication.invalid"})
        if url.endswith("/v1/system/version"):
            return json_response(
                200,
                {
                    "apiVersion": qualification.API_VERSION,
                    "kind": "RuntimeVersionReport",
                    "metadata": {
                        "tenantId": self.identity["tenantId"],
                        "evaluatedAt": "2026-09-07T09:00:00Z",
                    },
                    "spec": {
                        "application": {"version": "0.84.0"},
                        "contracts": {"apiVersion": qualification.API_VERSION},
                        "storage": {
                            "requiredMigration": "0015_plugin_invocation_lifecycle.sql"
                        },
                        "build": {"mode": "release", "revision": REVISION},
                        "deployment": {
                            "helmChartVersion": "0.87.0",
                            "imageDigest": IMAGE,
                        },
                    },
                },
            )
        raise AssertionError(f"unexpected request: {method} {url}")


class MissingS256Client(FakeClient):
    @property
    def metadata(self) -> dict[str, object]:
        value = super().metadata
        value["code_challenge_methods_supported"] = []
        return value


PROFILE = document(EXAMPLES / "customer-oidc-qualification-profile.json")


class CustomerOidcContractTests(unittest.TestCase):
    def test_profile_and_report_examples_are_schema_and_semantically_valid(self) -> None:
        for name in (
            "customer-oidc-qualification-profile",
            "customer-oidc-qualification-report",
        ):
            schema = document(SCHEMAS / f"{name}.schema.json")
            example = document(EXAMPLES / f"{name}.json")
            errors = list(
                Draft202012Validator(
                    schema, format_checker=FormatChecker()
                ).iter_errors(example)
            )
            self.assertEqual(errors, [])
        qualification.validate_profile(PROFILE)
        qualification.validate_report_document(
            document(EXAMPLES / "customer-oidc-qualification-report.json")
        )

    def test_report_recomputes_summary_identity_and_measurement_bounds(self) -> None:
        report = document(EXAMPLES / "customer-oidc-qualification-report.json")
        changed = copy.deepcopy(report)
        changed["spec"]["checks"][0] = {
            "id": "profile-binding",
            "status": "failed",
            "errorCode": "customer-oidc-qualification.profile-binding.failed",
        }
        with self.assertRaisesRegex(
            qualification.CustomerOidcQualificationError,
            "report.summary-invalid",
        ):
            qualification.validate_report_document(changed)

        changed = copy.deepcopy(report)
        changed["spec"]["checks"][0] = {
            "id": "profile-binding",
            "status": "failed",
            "errorCode": "customer-oidc-qualification.wrong.failed",
        }
        failed = sum(
            item["status"] == "failed" for item in changed["spec"]["checks"]
        )
        changed["spec"]["status"] = "not-qualified"
        changed["spec"]["summary"] = {
            "totalChecks": len(qualification.CHECK_IDS),
            "passedChecks": len(qualification.CHECK_IDS) - failed,
            "failedChecks": failed,
            "overallStatus": "not-qualified",
        }
        changed["metadata"]["id"] = qualification._report_identifier(
            {key: value for key, value in changed["metadata"].items() if key != "id"},
            changed["spec"],
        )
        with self.assertRaisesRegex(
            qualification.CustomerOidcQualificationError, "checks-invalid"
        ):
            qualification.validate_report_document(changed)

        changed = copy.deepcopy(report)
        changed["spec"]["measurements"]["tokenRemainingSeconds"] = 10
        changed["metadata"]["id"] = qualification._report_identifier(
            {key: value for key, value in changed["metadata"].items() if key != "id"},
            changed["spec"],
        )
        with self.assertRaisesRegex(
            qualification.CustomerOidcQualificationError,
            "measurements-invalid",
        ):
            qualification.validate_report_document(changed)

    def test_report_rejects_sensitive_retained_fields(self) -> None:
        report = document(EXAMPLES / "customer-oidc-qualification-report.json")
        changed = copy.deepcopy(report)
        changed["spec"]["bindings"]["issuer"] = "https://identity.example.test/"
        with self.assertRaisesRegex(
            qualification.CustomerOidcQualificationError,
            "report.schema-invalid",
        ):
            qualification.validate_report_document(changed)

    def test_profile_order_and_endpoint_shape_are_closed(self) -> None:
        changed = copy.deepcopy(PROFILE)
        changed["spec"]["oidc"]["actorClaim"] = changed["spec"]["oidc"][
            "tenantClaim"
        ]
        with self.assertRaisesRegex(
            qualification.CustomerOidcQualificationError, "profile.invalid"
        ):
            qualification.validate_profile(changed)

        changed = copy.deepcopy(PROFILE)
        changed["spec"]["oidc"]["browser"]["redirectUri"] = (
            "https://control-plane.example.test/callback"
        )
        with self.assertRaisesRegex(
            qualification.CustomerOidcQualificationError, "profile.invalid"
        ):
            qualification.validate_profile(changed)


class CustomerOidcBoundaryTests(unittest.TestCase):
    def test_profile_and_token_require_mode_0600_regular_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile_path = root / "profile.json"
            token_path = root / "token"
            profile_path.write_text(json.dumps(PROFILE), encoding="utf-8")
            token_path.write_text(access_token(PROFILE), encoding="ascii")
            os.chmod(profile_path, 0o600)
            os.chmod(token_path, 0o600)
            self.assertEqual(qualification.load_profile(profile_path), PROFILE)
            self.assertEqual(
                qualification.load_access_token(token_path), access_token(PROFILE)
            )
            os.chmod(token_path, 0o644)
            with self.assertRaisesRegex(
                qualification.CustomerOidcQualificationError, "credential.invalid"
            ):
                qualification.load_access_token(token_path)
            os.chmod(token_path, 0o600)
            token_path.write_text("header.payload.", encoding="ascii")
            with self.assertRaisesRegex(
                qualification.CustomerOidcQualificationError, "credential.invalid"
            ):
                qualification.load_access_token(token_path)

    def test_explicit_enable_is_required(self) -> None:
        with self.assertRaisesRegex(
            qualification.CustomerOidcQualificationError, "enable.required"
        ):
            qualification.qualify(
                profile=PROFILE,
                api_base_url="https://control-plane.example.test",
                access_token=access_token(PROFILE),
                api_ca_file=Path("unused"),
                issuer_ca_file=Path("unused"),
                image_digest=IMAGE,
                allow_identity_observation=False,
                client=FakeClient(PROFILE, access_token(PROFILE)),
                now=NOW,
            )

    def test_full_external_flow_is_bound_and_minimized(self) -> None:
        token = access_token(PROFILE)
        client = FakeClient(PROFILE, token)
        with mock.patch.object(
            qualification, "_source_identity", return_value=(REVISION, False)
        ):
            report = qualification.qualify(
                profile=PROFILE,
                api_base_url="https://control-plane.example.test",
                access_token=token,
                api_ca_file=Path("unused"),
                issuer_ca_file=Path("unused"),
                image_digest=IMAGE,
                allow_identity_observation=True,
                client=client,
                now=NOW,
            )
        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(report["spec"]["summary"]["passedChecks"], 17)
        serialized = json.dumps(report, sort_keys=True)
        for protected in (
            token,
            "tenant-acme",
            "qualification-operator",
            "identity.example.test",
            "control-plane.example.test",
        ):
            self.assertNotIn(protected, serialized)
        self.assertEqual(len(client.requests), 8)

    def test_valid_capability_failure_is_retained_as_not_qualified(self) -> None:
        token = access_token(PROFILE)
        client = MissingS256Client(PROFILE, token)
        with mock.patch.object(
            qualification, "_source_identity", return_value=(REVISION, False)
        ):
            report = qualification.qualify(
                profile=PROFILE,
                api_base_url="https://control-plane.example.test",
                access_token=token,
                api_ca_file=Path("unused"),
                issuer_ca_file=Path("unused"),
                image_digest=IMAGE,
                allow_identity_observation=True,
                client=client,
                now=NOW,
            )
        self.assertEqual(report["spec"]["status"], "not-qualified")
        checks = {item["id"]: item for item in report["spec"]["checks"]}
        self.assertEqual(checks["s256-supported"]["status"], "failed")
        qualification.validate_report_document(report)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report_path = root / "report.json"
            profile_path = root / "profile.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            profile_path.write_text(json.dumps(PROFILE), encoding="utf-8")
            os.chmod(profile_path, 0o600)
            with mock.patch.object(
                qualification, "_source_identity", return_value=(REVISION, False)
            ):
                qualification.verify_report(
                    report_path=report_path,
                    profile_path=profile_path,
                    api_base_url="https://control-plane.example.test",
                    image_digest=IMAGE,
                )
                with self.assertRaisesRegex(
                    qualification.CustomerOidcQualificationError,
                    "report.not-qualified",
                ):
                    qualification.verify_report(
                        report_path=report_path,
                        profile_path=profile_path,
                        api_base_url="https://control-plane.example.test",
                        image_digest=IMAGE,
                        require_qualified=True,
                    )

    def test_token_claim_and_lifetime_mismatch_fail_before_network(self) -> None:
        changed = copy.deepcopy(PROFILE)
        changed["spec"]["identity"]["tenantId"] = "tenant-other"
        with self.assertRaisesRegex(
            qualification.CustomerOidcQualificationError, "token.claims-invalid"
        ):
            qualification._token_measurements(access_token(PROFILE), changed, NOW)

    def test_console_origin_must_match_api_target_before_network(self) -> None:
        changed = copy.deepcopy(PROFILE)
        changed["spec"]["oidc"]["browser"]["redirectUri"] = (
            "https://other.example.test/console"
        )
        client = FakeClient(changed, access_token(changed))
        with (
            mock.patch.object(qualification, "_source_identity") as source_identity,
            self.assertRaisesRegex(
                qualification.CustomerOidcQualificationError, "target.crossed"
            ),
        ):
            qualification.qualify(
                profile=changed,
                api_base_url="https://control-plane.example.test",
                access_token=access_token(changed),
                api_ca_file=Path("unused"),
                issuer_ca_file=Path("unused"),
                image_digest=IMAGE,
                allow_identity_observation=True,
                client=client,
                now=NOW,
            )
        source_identity.assert_not_called()
        self.assertEqual(client.requests, [])

    def test_naive_qualification_time_is_rejected(self) -> None:
        with (
            mock.patch.object(
                qualification, "_source_identity", return_value=(REVISION, False)
            ),
            self.assertRaisesRegex(
                qualification.CustomerOidcQualificationError, "time.invalid"
            ),
        ):
            qualification.qualify(
                profile=PROFILE,
                api_base_url="https://control-plane.example.test",
                access_token=access_token(PROFILE),
                api_ca_file=Path("unused"),
                issuer_ca_file=Path("unused"),
                image_digest=IMAGE,
                allow_identity_observation=True,
                client=FakeClient(PROFILE, access_token(PROFILE)),
                now=datetime(2026, 9, 7, 9, 0),
            )

    def test_report_verifier_rebinds_current_profile_target_and_image(self) -> None:
        report = document(EXAMPLES / "customer-oidc-qualification-report.json")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report_path = root / "report.json"
            profile_path = root / "profile.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            profile_path.write_text(json.dumps(PROFILE), encoding="utf-8")
            os.chmod(profile_path, 0o600)
            with mock.patch.object(
                qualification,
                "_source_identity",
                return_value=(report["metadata"]["sourceRevision"], False),
            ):
                qualification.verify_report(
                    report_path=report_path,
                    profile_path=profile_path,
                    api_base_url="https://control-plane.example.test",
                    image_digest=IMAGE,
                )
                with self.assertRaisesRegex(
                    qualification.CustomerOidcQualificationError, "report.crossed"
                ):
                    qualification.verify_report(
                        report_path=report_path,
                        profile_path=profile_path,
                        api_base_url="https://other.example.test",
                        image_digest=IMAGE,
                    )

    def test_report_verifier_rejects_symlink_input(self) -> None:
        report = document(EXAMPLES / "customer-oidc-qualification-report.json")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.json"
            link = root / "report.json"
            target.write_text(json.dumps(report), encoding="utf-8")
            link.symlink_to(target)
            with self.assertRaisesRegex(
                qualification.CustomerOidcQualificationError, "report.unreadable"
            ):
                qualification.verify_report(
                    report_path=link,
                    profile_path=root / "unused-profile.json",
                    api_base_url="https://control-plane.example.test",
                    image_digest=IMAGE,
                )

    def test_network_client_disables_proxies_redirects_and_has_no_token_output(self) -> None:
        source = (ROOT / "scripts/qualify_customer_oidc.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("ProxyHandler({})", source)
        self.assertIn("_DenyRedirects()", source)
        self.assertNotIn("print(access_token", source)
        self.assertNotIn("print(token", source)


if __name__ == "__main__":
    unittest.main()
