"""Dependency-free HTTP surface for the reference vertical slice."""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict
from urllib.parse import urlparse

from iip.application.ingest_resource import (
    AuthorizationError,
    IngestResourceCommand,
    InvalidInputError,
    ObservationConflictError,
    StaleObservationError,
)
from iip.application.ports import ActorContext
from iip.bootstrap import Runtime, build_local_runtime


class ApiHandler(BaseHTTPRequestHandler):
    """Small HTTP adapter; production authentication and persistence are out of scope."""

    runtime: Runtime
    server_version = "IIPReference/0.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = urlparse(self.path).path
        if path in ("/healthz", "/readyz"):
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/v1/resources":
            tenant_id = self.headers.get("x-iip-tenant-id", "local")
            items = [resource.to_dict() for resource in self.runtime.resources.list(tenant_id)]
            self._json(HTTPStatus.OK, {"items": items})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": {"code": "route.not_found"}})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if urlparse(self.path).path != "/v1/resources":
            self._json(HTTPStatus.NOT_FOUND, {"error": {"code": "route.not_found"}})
            return
        try:
            payload = self._read_json()
            actor = ActorContext(
                actor_id=self.headers.get("x-iip-actor-id", "local-developer"),
                tenant_id=self.headers.get("x-iip-tenant-id", "local"),
                roles=("developer",),
            )
            resource = self.runtime.ingestion.execute(
                IngestResourceCommand(
                    actor=actor,
                    payload=payload,
                    correlation_id=self.headers.get("x-correlation-id"),
                )
            )
            self._json(HTTPStatus.ACCEPTED, resource.to_dict())
        except InvalidInputError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": "contract.invalid"}})
        except AuthorizationError:
            self._json(HTTPStatus.FORBIDDEN, {"error": {"code": "policy.denied"}})
        except StaleObservationError:
            self._json(
                HTTPStatus.CONFLICT,
                {"error": {"code": "resource.observation.stale"}},
            )
        except ObservationConflictError:
            self._json(
                HTTPStatus.CONFLICT,
                {"error": {"code": "resource.observation.conflict"}},
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": "request.invalid_json"}})

    def log_message(self, format: str, *args: object) -> None:
        """Keep the reference surface quiet; production uses structured telemetry."""

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        if length <= 0 or length > 1_048_576:
            raise ValueError("invalid content length")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    """Start the local reference API."""

    host = os.environ.get("IIP_HTTP_HOST", "0.0.0.0")
    port = int(os.environ.get("IIP_HTTP_PORT", "8080"))
    ApiHandler.runtime = build_local_runtime()
    server = ThreadingHTTPServer((host, port), ApiHandler)
    print(f"IIP reference API listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("IIP reference API stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
