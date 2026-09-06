from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from iip.application.ports import ReadinessError
from iip.surfaces.worker_health import (
    WorkerHealthConfigurationError,
    WorkerHealthServer,
    worker_health_address,
)


ROOT = Path(__file__).resolve().parents[1]


class Ready:
    def check(self) -> None:
        return None


class Unavailable:
    def check(self) -> None:
        raise ReadinessError("database unavailable")


def request(
    server: WorkerHealthServer,
    path: str,
    *,
    method: str = "GET",
) -> tuple[int, dict, dict[str, str]]:
    try:
        response = urlopen(
            Request(
                f"http://127.0.0.1:{server.port}{path}",
                method=method,
            ),
            timeout=2,
        )
    except HTTPError as exc:
        with exc:
            return (
                exc.code,
                json.loads(exc.read().decode("utf-8")),
                {key.lower(): value for key, value in exc.headers.items()},
            )
    with response:
        return (
            response.status,
            json.loads(response.read().decode("utf-8")),
            {key.lower(): value for key, value in response.headers.items()},
        )


class WorkerHealthTests(unittest.TestCase):
    def test_liveness_and_dependency_readiness_are_distinct(self) -> None:
        healthy = WorkerHealthServer(Ready(), host="127.0.0.1", port=0)
        unhealthy = WorkerHealthServer(Unavailable(), host="127.0.0.1", port=0)
        healthy.start()
        unhealthy.start()
        try:
            self.assertEqual(request(healthy, "/healthz")[:2], (200, {"status": "ok"}))
            ready_status, ready_body, ready_headers = request(healthy, "/readyz")
            self.assertEqual((ready_status, ready_body), (200, {"status": "ok"}))
            self.assertEqual(ready_headers["cache-control"], "no-store")
            self.assertEqual(ready_headers["x-content-type-options"], "nosniff")

            self.assertEqual(
                request(unhealthy, "/healthz")[:2],
                (200, {"status": "ok"}),
            )
            self.assertEqual(
                request(unhealthy, "/readyz")[:2],
                (
                    503,
                    {
                        "status": "unavailable",
                        "error": {"code": "readiness.unavailable"},
                    },
                ),
            )
        finally:
            healthy.close()
            unhealthy.close()

    def test_draining_removes_readiness_without_failing_liveness(self) -> None:
        server = WorkerHealthServer(Ready(), host="127.0.0.1", port=0)
        server.start()
        try:
            server.begin_draining()
            self.assertEqual(request(server, "/healthz")[0], 200)
            self.assertEqual(request(server, "/readyz")[0], 503)
        finally:
            server.close()

    def test_surface_is_closed_and_value_minimized(self) -> None:
        server = WorkerHealthServer(Ready(), host="127.0.0.1", port=0)
        server.start()
        try:
            self.assertEqual(
                request(server, "/healthz?details=true")[:2],
                (400, {"error": {"code": "request.invalid"}}),
            )
            self.assertEqual(
                request(server, "/v1/resources")[:2],
                (404, {"error": {"code": "route.not_found"}}),
            )
            self.assertEqual(
                request(server, "/readyz", method="POST")[:2],
                (404, {"error": {"code": "route.not_found"}}),
            )
        finally:
            server.close()

    def test_listener_configuration_is_literal_and_unprivileged(self) -> None:
        self.assertEqual(worker_health_address({}), ("0.0.0.0", 8081))
        self.assertEqual(
            worker_health_address(
                {
                    "IIP_WORKER_HEALTH_HOST": "127.0.0.1",
                    "IIP_WORKER_HEALTH_PORT": "18081",
                }
            ),
            ("127.0.0.1", 18081),
        )
        for environment in (
            {"IIP_WORKER_HEALTH_HOST": "localhost"},
            {"IIP_WORKER_HEALTH_HOST": "::"},
            {"IIP_WORKER_HEALTH_HOST": "127.00.0.1"},
            {"IIP_WORKER_HEALTH_PORT": "80"},
            {"IIP_WORKER_HEALTH_PORT": "65536"},
            {"IIP_WORKER_HEALTH_PORT": "not-a-port"},
        ):
            with self.subTest(environment=environment), self.assertRaisesRegex(
                WorkerHealthConfigurationError,
                "worker.health.configuration.invalid",
            ):
                worker_health_address(environment)

    def test_worker_enters_draining_before_waiting_for_bounded_work(self) -> None:
        source = (ROOT / "src/iip/surfaces/worker.py").read_text(encoding="utf-8")
        stop = source.index("def stop(")
        loop = source.index("while not stopped.is_set()", stop)
        shutdown = source.index("finally:", loop)
        self.assertLess(source.index("health.begin_draining()", stop), loop)
        self.assertLess(
            source.index("health.begin_draining()", shutdown),
            source.index("scheduler.close()", shutdown),
        )
        self.assertLess(
            source.index("scheduler.close()", shutdown),
            source.index("health.close()", shutdown),
        )

    def test_worker_isolates_recurring_store_loss_from_process_liveness(self) -> None:
        source = (ROOT / "src/iip/surfaces/worker.py").read_text(encoding="utf-8")
        maintenance = source.index("for tenant_id in tenants:", source.index("def main"))
        sampling = source.index("if ingestion_sampler is not None", maintenance)
        self.assertIn("except PersistenceError:", source[maintenance:sampling])
        self.assertIn(
            "except PersistenceError:",
            source[sampling : source.index("if retention_enabled", sampling)],
        )

    def test_every_docker_worker_uses_private_dependency_readiness(self) -> None:
        for relative in (
            "deploy/docker-compose.yml",
            "deploy/docker-compose.ai-finops.yml",
            "deploy/docker-compose.otlp-receiver.yml",
        ):
            with self.subTest(compose=relative):
                compose = (ROOT / relative).read_text(encoding="utf-8")
                match = re.search(
                    r"^  workflow-worker:\n(?P<body>.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)",
                    compose,
                    re.MULTILINE | re.DOTALL,
                )
                self.assertIsNotNone(match)
                assert match is not None
                worker = match.group("body")
                self.assertIn('IIP_WORKER_HEALTH_PORT: "8081"', worker)
                self.assertIn("healthcheck:", worker)
                self.assertIn("http://127.0.0.1:8081/readyz", worker)
                self.assertNotIn("\n    ports:", worker)

    def test_openapi_keeps_worker_health_surface_private_and_closed(self) -> None:
        document = json.loads(
            (ROOT / "api/openapi/worker-health.openapi.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(document["openapi"], "3.1.0")
        self.assertEqual(document["security"], [])
        self.assertEqual(
            document["servers"],
            [
                {
                    "url": "http://127.0.0.1:8081",
                    "description": "Private pod/container health listener",
                }
            ],
        )
        self.assertEqual(set(document["paths"]), {"/healthz", "/readyz"})
        self.assertEqual(set(document["paths"]["/healthz"]), {"get"})
        self.assertEqual(set(document["paths"]["/readyz"]), {"get"})
        self.assertEqual(
            set(document["paths"]["/healthz"]["get"]["responses"]),
            {"200", "400"},
        )
        self.assertEqual(
            set(document["paths"]["/readyz"]["get"]["responses"]),
            {"200", "400", "503"},
        )
        schemas = document["components"]["schemas"]
        self.assertEqual(schemas["Healthy"]["properties"]["status"]["const"], "ok")
        self.assertEqual(
            schemas["Unavailable"]["properties"]["error"]["properties"]["code"][
                "const"
            ],
            "readiness.unavailable",
        )


if __name__ == "__main__":
    unittest.main()
