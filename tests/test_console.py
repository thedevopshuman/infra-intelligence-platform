"""Customer console and authenticated session boundary tests."""

from __future__ import annotations

import hashlib
import json
import os
import unittest
from email.message import Message
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from infra_intelligence_sdk import Client, SessionContext
from iip.bootstrap import build_runtime_from_env
from iip.surfaces.http import ApiHandler


TOKEN = "console-test-token-with-more-than-thirty-two-bytes"


def identity_config() -> str:
    digest = hashlib.sha256(TOKEN.encode("utf-8")).hexdigest()
    return json.dumps(
        {
            "identities": [
                {
                    "tokenSha256": f"sha256:{digest}",
                    "actorId": "local-operator",
                    "tenantId": "local",
                    "roles": ["developer", "platform-admin"],
                }
            ]
        }
    )


class ConsoleHttpTests(unittest.TestCase):
    def _wire_response(self, handler: ApiHandler) -> tuple[list[int], list[tuple[str, str]]]:
        statuses: list[int] = []
        headers: list[tuple[str, str]] = []
        handler.send_response = lambda status: statuses.append(status)
        handler.send_header = lambda name, value: headers.append((name, value))
        handler.end_headers = lambda: None
        handler.wfile = BytesIO()
        return statuses, headers

    def test_console_shell_and_assets_are_public_and_hardened(self) -> None:
        for path, expected_type, expected_text in (
            ("/", "text/html; charset=utf-8", "Evidence before answers"),
            ("/console/app.css", "text/css; charset=utf-8", "--cyan"),
            ("/console/app.js", "text/javascript; charset=utf-8", "/v1/session"),
        ):
            with self.subTest(path=path):
                handler = object.__new__(ApiHandler)
                handler.path = path
                handler.headers = Message()
                statuses, headers = self._wire_response(handler)

                handler.do_GET()

                self.assertEqual(statuses, [HTTPStatus.OK.value])
                self.assertIn(("content-type", expected_type), headers)
                self.assertTrue(
                    any(
                        name == "content-security-policy"
                        and "default-src 'none'" in value
                        and "frame-ancestors 'none'" in value
                        for name, value in headers
                    )
                )
                self.assertIn(("x-content-type-options", "nosniff"), headers)
                self.assertIn(expected_text, handler.wfile.getvalue().decode("utf-8"))

        handler = object.__new__(ApiHandler)
        handler.path = "/console"
        handler.headers = Message()
        self._wire_response(handler)
        handler.do_GET()
        self.assertIn(
            "What this control plane is serving",
            handler.wfile.getvalue().decode("utf-8"),
        )
        console = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("Quarantined event replay", console)
        self.assertIn("Investigate newest quarantine", console)
        self.assertIn("SLO attainment", console)
        self.assertIn("Investigation reliability", console)
        self.assertIn("Evidence retention", console)
        self.assertIn("Telemetry delivery", console)
        self.assertIn("Invocation operations", console)
        self.assertIn("Close unknown outcome", console)
        self.assertIn("This never replays", console)

        handler = object.__new__(ApiHandler)
        handler.path = "/console/app.js"
        handler.headers = Message()
        self._wire_response(handler)
        handler.do_GET()
        self.assertIn(
            "/v1/system/version",
            handler.wfile.getvalue().decode("utf-8"),
        )
        script = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("Reviewed catalog", script)
        self.assertIn("protected-catalog", script)
        self.assertIn("resource.change", script)
        self.assertIn("/v1/operations/events/delivery-health?limit=20", script)
        self.assertIn("/v1/operations/events/delivery-slo", script)
        self.assertIn(
            "/v1/operations/investigations/completion-slo",
            script,
        )
        self.assertIn("/v1/operations/evidence/retention", script)
        self.assertIn(
            "/v1/operations/telemetry/deployment-export-health", script
        )
        self.assertIn("Event delivery", script)
        self.assertIn('actionType === "event-delivery.requeue"', script)
        self.assertIn("exact quarantine generation", script)
        self.assertIn("/v1/plugin-invocations/${encodeURIComponent(invocationId)}/status", script)
        self.assertIn("PluginInvocationCancellationRequest", script)
        self.assertIn("PluginInvocationReconciliationRequest", script)
        self.assertIn("No work was replayed", script)

        handler = object.__new__(ApiHandler)
        handler.path = "/console/app.css"
        handler.headers = Message()
        self._wire_response(handler)
        handler.do_GET()
        self.assertIn(
            ".detail-actions[hidden] { display: none; }",
            handler.wfile.getvalue().decode("utf-8"),
        )
        self.assertIn(
            ".plugin-lifecycle-controls[hidden] { display: none; }",
            handler.wfile.getvalue().decode("utf-8"),
        )

    def test_session_is_derived_from_the_credential(self) -> None:
        with patch.dict(
            os.environ,
            {"IIP_AUTH_IDENTITIES_JSON": identity_config()},
            clear=True,
        ):
            runtime = build_runtime_from_env()
        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.path = "/v1/session"
        handler.headers = {"authorization": f"Bearer {TOKEN}"}
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda status, payload: responses.append((status, payload))

        handler.do_GET()

        self.assertEqual(responses[0][0], HTTPStatus.OK)
        document = responses[0][1]
        self.assertEqual(document["kind"], "SessionContext")
        self.assertEqual(document["metadata"], {"tenantId": "local", "actorId": "local-operator"})
        self.assertEqual(document["spec"]["roles"], ["developer", "platform-admin"])
        self.assertNotIn(TOKEN, json.dumps(document))

    def test_session_requires_authentication(self) -> None:
        with patch.dict(
            os.environ,
            {"IIP_AUTH_IDENTITIES_JSON": identity_config()},
            clear=True,
        ):
            runtime = build_runtime_from_env()
        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.path = "/v1/session"
        handler.headers = {}
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda status, payload: responses.append((status, payload))

        handler.do_GET()

        self.assertEqual(responses[0][0], HTTPStatus.UNAUTHORIZED)


class SessionSdkTests(unittest.TestCase):
    def test_python_sdk_parses_session_contract(self) -> None:
        client = Client("https://control-plane.example", TOKEN)
        payload = json.loads(
            (Path(__file__).resolve().parents[1] / "contracts/examples/session-context.json").read_text(
                encoding="utf-8"
            )
        )
        client._get = lambda path: payload  # type: ignore[method-assign]

        context = client.get_session()

        self.assertIsInstance(context, SessionContext)
        self.assertEqual(context.to_dict(), payload)


if __name__ == "__main__":
    unittest.main()
