"""Runtime identity contract, authority, deployment, and SDK tests."""

from __future__ import annotations

import json
import os
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from infra_intelligence_sdk import Client, RuntimeVersionReport
from iip.application.ports import ActorContext
from iip.application.query_runtime_version import (
    GetRuntimeVersionCommand,
    RuntimeVersionConfigurationError,
    RuntimeVersionIdentity,
    RuntimeVersionService,
)
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "runtime-version-token-0123456789abcdef0123456789abcdef"
REVISION = "0123456789abcdef0123456789abcdef01234567"
DIGEST = "sha256:" + ("a" * 64)


class Clock:
    def now(self) -> str:
        return "2026-08-17T15:00:00Z"


class Authenticator:
    def authenticate_bearer(self, token: str) -> ActorContext:
        if token != TOKEN:
            raise AssertionError(token)
        return ActorContext("customer-operator", "tenant-acme", ("viewer",))


def identity(**overrides: object) -> RuntimeVersionIdentity:
    values: dict[str, object] = {
        "application_version": "0.68.0",
        "contract_api_version": "iip.platform/v1alpha1",
        "required_storage_migration": "0020_ai_savings_ledger.sql",
        "build_revision": REVISION,
        "helm_chart_version": "0.68.0",
        "image_digest": DIGEST,
    }
    values.update(overrides)
    return RuntimeVersionIdentity(**values)  # type: ignore[arg-type]


class RuntimeVersionServiceTests(unittest.TestCase):
    def test_report_is_tenant_contextual_and_contains_only_identity_facts(self) -> None:
        report = RuntimeVersionService(identity(), Clock()).get(
            GetRuntimeVersionCommand(
                ActorContext("customer-operator", "tenant-acme", ("viewer",))
            )
        ).to_dict()

        self.assertEqual(report["metadata"]["tenantId"], "tenant-acme")
        self.assertEqual(report["spec"]["build"], {
            "mode": "release",
            "revision": REVISION,
        })
        self.assertEqual(report["spec"]["deployment"]["imageDigest"], DIGEST)
        serialized = json.dumps(report)
        for forbidden in ("endpoint", "hostname", "credential", "customer-operator"):
            self.assertNotIn(forbidden, serialized)

    def test_development_build_omits_unverified_deployment_identity(self) -> None:
        report = RuntimeVersionService(
            identity(
                build_revision=None,
                helm_chart_version=None,
                image_digest=None,
            ),
            Clock(),
        ).get(
            GetRuntimeVersionCommand(ActorContext("developer", "local", ()))
        ).to_dict()

        self.assertEqual(report["spec"]["build"], {"mode": "development"})
        self.assertEqual(report["spec"]["deployment"], {})

    def test_invalid_identity_fails_at_composition_time(self) -> None:
        for field, value in (
            ("application_version", "latest"),
            ("contract_api_version", "v1"),
            ("required_storage_migration", "9.sql"),
            ("build_revision", "main"),
            ("helm_chart_version", "stable"),
            ("image_digest", "sha256:short"),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    RuntimeVersionConfigurationError,
                    "runtime.version.configuration.invalid",
                ):
                    identity(**{field: value})


class RuntimeVersionHttpAndSdkTests(unittest.TestCase):
    def _handler(self, path: str = "/v1/system/version") -> ApiHandler:
        with patch.dict(
            os.environ,
            {
                "IIP_BUILD_REVISION": REVISION,
                "IIP_DEPLOYMENT_HELM_CHART_VERSION": "0.68.0",
                "IIP_DEPLOYMENT_IMAGE_DIGEST": DIGEST,
            },
            clear=True,
        ):
            runtime = build_local_runtime(Authenticator())
        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.path = path
        handler.headers = {"authorization": f"Bearer {TOKEN}"}
        handler.responses = []
        handler._json = lambda status, body: handler.responses.append((status, body))
        return handler

    def test_http_route_is_authenticated_tenant_derived_and_query_closed(self) -> None:
        handler = self._handler()
        handler.do_GET()

        self.assertEqual(handler.responses[0][0], HTTPStatus.OK)
        report = handler.responses[0][1]
        self.assertEqual(report["kind"], "RuntimeVersionReport")
        self.assertEqual(report["metadata"]["tenantId"], "tenant-acme")
        self.assertEqual(report["spec"]["build"]["revision"], REVISION)

        invalid = self._handler("/v1/system/version?details=host")
        invalid.do_GET()
        self.assertEqual(
            invalid.responses[0],
            (HTTPStatus.BAD_REQUEST, {"error": {"code": "request.invalid"}}),
        )

    def test_python_sdk_calls_version_route_and_parses_contract(self) -> None:
        payload = json.loads(
            (ROOT / "contracts/examples/runtime-version-report.json").read_text(
                encoding="utf-8"
            )
        )
        client = Client("https://control.example", TOKEN)
        client._get = lambda path: payload if path == "/v1/system/version" else {}  # type: ignore[method-assign]

        report = client.get_runtime_version()

        self.assertIsInstance(report, RuntimeVersionReport)
        self.assertEqual(report.to_dict(), payload)


class RuntimeVersionDeploymentTests(unittest.TestCase):
    def test_release_image_and_helm_supply_runtime_identity(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        release = (ROOT / "scripts/build_release_bundle.sh").read_text(
            encoding="utf-8"
        )
        configmap = (
            ROOT
            / "deploy/helm/infra-intelligence/templates/configmap.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn('IIP_BUILD_REVISION="$IIP_IMAGE_REVISION"', dockerfile)
        self.assertIn("ARG IIP_IMAGE_REVISION=development", dockerfile)
        self.assertIn('IIP_IMAGE_REVISION=$IIP_RELEASE_REVISION', release)
        self.assertIn("IIP_DEPLOYMENT_HELM_CHART_VERSION", configmap)
        self.assertIn("IIP_DEPLOYMENT_IMAGE_DIGEST", configmap)


if __name__ == "__main__":
    unittest.main()
