from __future__ import annotations

import json
import os
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from iip.adapters.auth import DenyAllAuthenticator
from iip.adapters.otlp_receiver import ConfiguredOtlpMetricsReceiver
from iip.application.ingest_otlp_metrics import OtlpReceiverConfigurationError
from iip.bootstrap import build_local_runtime, build_otlp_receiver_runtime_from_env
from iip.surfaces.otlp_receiver import OtlpReceiverHandler, TokenBucketRateLimiter

from tests.test_otlp_receiver import CHANNEL_TOKEN, ingest_fixture, receiver_config


class _Clock:
    def __init__(self) -> None:
        self.value = 10.0

    def __call__(self) -> float:
        return self.value


class DedicatedOtlpSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = build_local_runtime()

    def tearDown(self) -> None:
        self.runtime.close()

    def _handler(self, path: str) -> tuple[OtlpReceiverHandler, list[int], BytesIO]:
        handler = object.__new__(OtlpReceiverHandler)
        handler.runtime = self.runtime
        handler.path = path
        handler.headers = {}
        output = BytesIO()
        handler.wfile = output
        statuses: list[int] = []
        handler.send_response = lambda status: statuses.append(status)
        handler.send_header = lambda _name, _value: None
        handler.end_headers = lambda: None
        return handler, statuses, output

    def test_health_is_public_but_console_and_control_routes_do_not_exist(self) -> None:
        for path, method, expected in (
            ("/healthz", "do_GET", 200),
            ("/readyz", "do_GET", 200),
            ("/console", "do_GET", 404),
            ("/v1/session", "do_GET", 404),
            ("/v1/resources", "do_POST", 404),
        ):
            with self.subTest(path=path):
                handler, statuses, output = self._handler(path)
                getattr(handler, method)()
                self.assertEqual(statuses, [expected])
                document = json.loads(output.getvalue())
                if expected == 404:
                    self.assertEqual(document["error"]["code"], "route.not_found")

    def test_rate_limit_is_per_channel_and_refills(self) -> None:
        clock = _Clock()
        limiter = TokenBucketRateLimiter(2, 2, clock=clock)

        self.assertTrue(limiter.admit("channel-a"))
        self.assertTrue(limiter.admit("channel-a"))
        self.assertFalse(limiter.admit("channel-a"))
        self.assertTrue(limiter.admit("channel-b"))
        clock.value += 0.5
        self.assertTrue(limiter.admit("channel-a"))

    def test_rate_limit_configuration_is_bounded(self) -> None:
        for rate, burst in ((0, 1), (1, 0), (100_001, 100_001)):
            with self.subTest(rate=rate, burst=burst):
                with self.assertRaisesRegex(
                    ValueError, "otlp.rate-limit.configuration.invalid"
                ):
                    TokenBucketRateLimiter(rate, burst)

    def test_authenticated_channel_is_rate_limited_before_second_body_read(self) -> None:
        runtime = build_local_runtime(
            otlp_metrics_receiver=ConfiguredOtlpMetricsReceiver.from_json(
                receiver_config()
            )
        )
        self.addCleanup(runtime.close)
        ingest_fixture(runtime)
        limiter = TokenBucketRateLimiter(1, 1, clock=lambda: 10.0)

        for expected in (200, 429):
            handler, statuses, _output = self._handler("/v1/metrics")
            handler.runtime = runtime
            handler.rate_limiter = limiter
            handler.headers = {
                "authorization": f"Bearer {CHANNEL_TOKEN}",
                "content-type": "application/x-protobuf",
                "content-length": "0",
            }
            handler.rfile = BytesIO()

            handler.do_POST()

            self.assertEqual(statuses, [expected])


class DedicatedOtlpCompositionTests(unittest.TestCase):
    def test_receiver_drains_active_requests_on_process_signals(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "iip"
            / "surfaces"
            / "otlp_receiver.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "server = DrainingThreadingHTTPServer((host, port), OtlpReceiverHandler)",
            source,
        )
        self.assertIn("IIP OTLP receiver draining active requests", source)
        self.assertIn(
            "for shutdown_signal in (signal.SIGTERM, signal.SIGINT)", source
        )
        self.assertLess(source.rindex("server.server_close()"), source.rindex("runtime.close()"))

    def test_receiver_requires_a_durable_database(self) -> None:
        with patch.dict(
            os.environ,
            {
                "IIP_OTLP_RECEIVER_ENABLED": "true",
                "IIP_OTLP_RECEIVER_CHANNELS_JSON": receiver_config(),
            },
            clear=True,
        ):
            with self.assertRaisesRegex(
                OtlpReceiverConfigurationError,
                "otlp.database.configuration.required",
            ):
                build_otlp_receiver_runtime_from_env()

    def test_receiver_does_not_require_or_compose_interactive_authentication(self) -> None:
        sentinel = object()
        with (
            patch.dict(
                os.environ,
                {
                    "IIP_DATABASE_URL": "postgresql://receiver.example/iip",
                    "IIP_OTLP_RECEIVER_ENABLED": "true",
                    "IIP_OTLP_RECEIVER_CHANNELS_JSON": receiver_config(),
                },
                clear=True,
            ),
            patch("iip.bootstrap.build_postgres_runtime", return_value=sentinel) as build,
        ):
            runtime = build_otlp_receiver_runtime_from_env()

        self.assertIs(runtime, sentinel)
        self.assertIsInstance(build.call_args.kwargs["authenticator"], DenyAllAuthenticator)
        self.assertIsNotNone(build.call_args.kwargs["otlp_metrics_receiver"])
        self.assertIsNone(build.call_args.kwargs["otlp_logs_receiver"])

    def test_control_runtime_defaults_to_receiver_disabled(self) -> None:
        from iip.bootstrap import build_runtime_from_env

        with patch.dict(
            os.environ,
            {
                "IIP_AUTH_IDENTITIES_JSON": json.dumps(
                    {
                        "identities": [
                            {
                                "actorId": "operator",
                                "tenantId": "local",
                                "roles": ["operator"],
                                "tokenSha256": "sha256:"
                                + "0" * 64,
                            }
                        ]
                    }
                ),
                "IIP_OTLP_RECEIVER_ENABLED": "true",
            },
            clear=True,
        ):
            runtime = build_runtime_from_env()
        self.addCleanup(runtime.close)
        self.assertIsNone(runtime.otlp_metrics_ingestion)


if __name__ == "__main__":
    unittest.main()
