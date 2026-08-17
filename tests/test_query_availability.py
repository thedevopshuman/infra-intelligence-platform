from __future__ import annotations

import io
import json
import threading
import time
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import Request, urlopen
from unittest.mock import patch

from iip.application.observe_query_availability import (
    QUERY_OPERATIONS,
    QueryAvailabilityObjectives,
    QueryAvailabilityService,
    RecordQueryAvailabilityCommand,
)
from iip.adapters.auth import HashedBearerAuthenticator
from iip.bootstrap import (
    _query_availability_objectives_from_env,
    build_local_runtime,
)
from iip.surfaces.http import ApiHandler


class RecordingSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.measurements = []

    def record_query_availability(self, measurement) -> None:
        if self.fail:
            raise RuntimeError("telemetry must remain isolated")
        self.measurements.append(measurement)


class QueryAvailabilityServiceTests(unittest.TestCase):
    def test_statuses_are_classified_without_request_identity(self) -> None:
        sink = RecordingSink()
        service = QueryAvailabilityService(
            sink,
            QueryAvailabilityObjectives(
                window_seconds=7200,
                minimum_availability_basis_points=9950,
                minimum_eligible_requests=200,
            ),
        )
        cases = (
            (200, False, "success", "available"),
            (404, False, "not-found", "available"),
            (409, False, "conflict", "available"),
            (400, False, "invalid", "excluded"),
            (401, False, "unauthenticated", "excluded"),
            (403, False, "denied", "excluded"),
            (429, False, "invalid", "excluded"),
            (503, False, "unavailable", "unavailable"),
            (500, True, "internal-error", "unavailable"),
        )

        for status, uncaught, outcome, availability in cases:
            service.record(
                RecordQueryAvailabilityCommand(
                    operation="runtime-version",
                    status_code=status,
                    duration_seconds=0.25,
                    uncaught_failure=uncaught,
                )
            )
            measurement = sink.measurements[-1]
            self.assertEqual(measurement.outcome, outcome)
            self.assertEqual(measurement.availability, availability)
            self.assertEqual(measurement.objective_window_seconds, 7200)
            self.assertEqual(
                measurement.objective_minimum_availability_basis_points,
                9950,
            )
            self.assertEqual(measurement.objective_minimum_eligible_requests, 200)
            serialized = json.dumps(measurement.__dict__)
            for forbidden in (
                "tenant",
                "actor",
                "credential",
                "query",
                "resource_id",
                "error_text",
            ):
                self.assertNotIn(forbidden, serialized)

    def test_invalid_observations_and_sink_failure_never_affect_serving(self) -> None:
        sink = RecordingSink(fail=True)
        service = QueryAvailabilityService(sink)

        service.record(
            RecordQueryAvailabilityCommand(
                operation="runtime-version",
                status_code=200,
                duration_seconds=1.0,
            )
        )
        service.record(
            RecordQueryAvailabilityCommand(
                operation="raw/customer/path",
                status_code=200,
                duration_seconds=1.0,
            )
        )
        self.assertEqual(sink.measurements, [])

    def test_configuration_is_bounded(self) -> None:
        for changes in (
            {"window_seconds": 299},
            {"minimum_availability_basis_points": 0},
            {"minimum_eligible_requests": 0},
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(
                ValueError,
                "query.availability.configuration.invalid",
            ):
                QueryAvailabilityObjectives(**changes)

    def test_environment_configuration_is_closed_and_bounded(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "IIP_QUERY_AVAILABILITY_SLO_WINDOW_SECONDS": "7200",
                "IIP_QUERY_AVAILABILITY_SLO_MINIMUM_BASIS_POINTS": "9950",
                "IIP_QUERY_AVAILABILITY_SLO_MINIMUM_ELIGIBLE_REQUESTS": "250",
            },
            clear=True,
        ):
            objectives = _query_availability_objectives_from_env()
        self.assertEqual(objectives.window_seconds, 7200)
        self.assertEqual(objectives.minimum_availability_basis_points, 9950)
        self.assertEqual(objectives.minimum_eligible_requests, 250)

        with patch.dict(
            "os.environ",
            {"IIP_QUERY_AVAILABILITY_SLO_WINDOW_SECONDS": "unbounded"},
            clear=True,
        ), self.assertRaisesRegex(
            ValueError,
            "query.availability.configuration.invalid",
        ):
            _query_availability_objectives_from_env()


class QueryAvailabilityHttpBoundaryTests(unittest.TestCase):
    def test_real_http_query_reaches_the_composed_sink(self) -> None:
        token = "query-slo-token-0123456789abcdef0123456789abcdef"
        authenticator = HashedBearerAuthenticator.from_json(
            json.dumps(
                {
                    "identities": [
                        {
                            "tokenSha256": HashedBearerAuthenticator.token_sha256(
                                token
                            ),
                            "actorId": "operator",
                            "tenantId": "local",
                            "roles": ["developer"],
                        }
                    ]
                }
            )
        )
        sink = RecordingSink()
        runtime = build_local_runtime(
            authenticator,
            query_availability_sink=sink,
        )
        handler_type = type(
            "QueryAvailabilityHandler",
            (ApiHandler,),
            {
                "runtime": runtime,
                "log_message": lambda self, format, *args: None,
            },
        )
        with patch("http.server.socket.getfqdn", return_value="localhost"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_type)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/v1/system/version",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, HTTPStatus.OK)
                self.assertEqual(json.load(response)["kind"], "RuntimeVersionReport")
        finally:
            thread.join(timeout=5)
            server.server_close()
            runtime.close()

        self.assertEqual(len(sink.measurements), 1)
        self.assertEqual(sink.measurements[0].operation, "runtime-version")
        self.assertEqual(sink.measurements[0].availability, "available")

    def test_only_closed_query_routes_resolve_to_stable_operations(self) -> None:
        cases = {
            "/v1/session": "session",
            "/v1/system/version": "runtime-version",
            "/v1/resources": "resources-list",
            "/v1/resources/res_secret/neighborhood": "resource-neighborhood",
            "/v1/resources/res_secret/timeline": "resource-timeline",
            "/v1/telemetry/ingestion": "ingestion-freshness",
            "/v1/operations/telemetry/export-health": "telemetry-export-health",
            "/v1/operations/events/delivery-health": "event-delivery-health",
            "/v1/operations/events/delivery-slo": "event-delivery-slo",
            "/v1/operations/investigations/completion-slo": (
                "investigation-completion-slo"
            ),
            "/v1/operations/evidence/retention": "evidence-retention",
            "/v1/evidence/ev_secret": "evidence-get",
            "/v1/investigations/inv_secret": "investigation-get",
            "/v1/investigations/inv_secret/status": "investigation-status",
            "/v1/investigation-jobs/inv_secret": "investigation-job-get",
            "/v1/actions": "actions-list",
            "/v1/actions/act_secret": "action-get",
            "/v1/actions/act_secret/workflow": "action-workflow-get",
            "/v1/plugin-sessions/plg_secret": "plugin-session-get",
            "/healthz": None,
            "/readyz": None,
            "/console": None,
            "/v1/customer/raw/path": None,
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(ApiHandler._query_operation(path), expected)
        self.assertEqual(
            {value for value in cases.values() if value is not None},
            QUERY_OPERATIONS,
        )

    def test_json_response_records_after_the_response_write(self) -> None:
        commands = []

        class Recorder:
            def record(self, command) -> None:
                commands.append(command)

        handler = object.__new__(ApiHandler)
        handler.runtime = SimpleNamespace(query_availability=Recorder())
        handler._query_availability_context = (
            "runtime-version",
            time.monotonic() - 0.01,
        )
        handler.wfile = io.BytesIO()
        handler.send_response = lambda status: None
        handler.send_header = lambda name, value: None
        handler.end_headers = lambda: None
        handler._security_headers = lambda: None

        handler._json(HTTPStatus.OK, {"kind": "RuntimeVersionReport"})

        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].operation, "runtime-version")
        self.assertEqual(commands[0].status_code, 200)
        self.assertGreaterEqual(commands[0].duration_seconds, 0.01)
        self.assertIsNone(handler._query_availability_context)

    def test_uncaught_failure_is_recorded_once(self) -> None:
        commands = []
        handler = object.__new__(ApiHandler)
        handler.runtime = SimpleNamespace(
            query_availability=SimpleNamespace(record=commands.append)
        )
        handler._query_availability_context = ("resources-list", time.monotonic())

        handler._finish_query_availability(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            uncaught_failure=True,
        )
        handler._finish_query_availability(HTTPStatus.OK)

        self.assertEqual(len(commands), 1)
        self.assertTrue(commands[0].uncaught_failure)


if __name__ == "__main__":
    unittest.main()
