"""Private dependency-aware health surface for the workflow worker."""

from __future__ import annotations

import ipaddress
import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Mapping
from urllib.parse import urlparse

from iip.application.ports import ReadinessProbe
from iip.surfaces.http import DrainingThreadingHTTPServer


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8081


class WorkerHealthConfigurationError(ValueError):
    """The private health listener configuration is unsafe or invalid."""


def worker_health_address(
    environment: Mapping[str, str] | None = None,
) -> tuple[str, int]:
    """Read a bounded literal IPv4 listener address from process configuration."""

    values = os.environ if environment is None else environment
    host = values.get("IIP_WORKER_HEALTH_HOST", DEFAULT_HOST)
    port_text = values.get("IIP_WORKER_HEALTH_PORT", str(DEFAULT_PORT))
    try:
        parsed_host = ipaddress.ip_address(host)
        port = int(port_text)
    except (TypeError, ValueError):
        raise WorkerHealthConfigurationError(
            "worker.health.configuration.invalid"
        ) from None
    if (
        not isinstance(parsed_host, ipaddress.IPv4Address)
        or str(parsed_host) != host
        or not 1024 <= port <= 65_535
    ):
        raise WorkerHealthConfigurationError(
            "worker.health.configuration.invalid"
        )
    return host, port


class WorkerHealthState:
    """Keep shutdown state separate from the dependency readiness adapter."""

    def __init__(self, readiness: ReadinessProbe) -> None:
        self._readiness = readiness
        self._draining = threading.Event()

    def begin_draining(self) -> None:
        self._draining.set()

    def ready(self) -> bool:
        if self._draining.is_set():
            return False
        try:
            self._readiness.check()
        except Exception:
            return False
        return True


class WorkerHealthHandler(BaseHTTPRequestHandler):
    """Expose only non-secret liveness and readiness on the private listener."""

    server_version = "IIPWorkerHealth"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parsed = urlparse(self.path)
        if parsed.query:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "request.invalid"}},
            )
            return
        if parsed.path == "/healthz":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if parsed.path == "/readyz":
            if self._state().ready():
                self._json(HTTPStatus.OK, {"status": "ok"})
            else:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {
                        "status": "unavailable",
                        "error": {"code": "readiness.unavailable"},
                    },
                )
            return
        self._not_found()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self._not_found()

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self._not_found()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self._not_found()

    def _state(self) -> WorkerHealthState:
        state = getattr(self.server, "worker_health_state", None)
        if not isinstance(state, WorkerHealthState):
            raise RuntimeError("worker.health.state.invalid")
        return state

    def _not_found(self) -> None:
        self._json(
            HTTPStatus.NOT_FOUND,
            {"error": {"code": "route.not_found"}},
        )

    def _json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("content-type", "application/json")
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        """Do not emit probe traffic or dependency details to process logs."""

        del args


class WorkerHealthServer:
    """Own the private HTTP thread and expose an explicit draining transition."""

    def __init__(
        self,
        readiness: ReadinessProbe,
        *,
        host: str,
        port: int,
    ) -> None:
        self._host = host
        self._port = port
        self._state = WorkerHealthState(readiness)
        self._server: DrainingThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        server = self._server
        if server is None:
            raise RuntimeError("worker.health.not-started")
        return int(server.server_port)

    def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("worker.health.already-started")
        server = DrainingThreadingHTTPServer(
            (self._host, self._port), WorkerHealthHandler
        )
        server.worker_health_state = self._state  # type: ignore[attr-defined]
        thread = threading.Thread(
            target=server.serve_forever,
            name="iip-worker-health",
            daemon=False,
        )
        self._server = server
        self._thread = thread
        thread.start()

    def begin_draining(self) -> None:
        self._state.begin_draining()

    def close(self) -> None:
        server = self._server
        thread = self._thread
        if server is None or thread is None:
            return
        server.shutdown()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("worker.health.shutdown-timeout")
        server.server_close()
        self._server = None
        self._thread = None
