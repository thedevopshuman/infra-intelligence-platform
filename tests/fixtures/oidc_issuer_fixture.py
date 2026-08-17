#!/usr/bin/env python3
"""TLS-only public JWKS fixture for executable OIDC compatibility tests."""

from __future__ import annotations

import json
import ssl
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock


FIXTURE_ROOT = Path("/fixture")
KEYS = json.loads((FIXTURE_ROOT / "public-keys.json").read_text(encoding="utf-8"))
AUDIT_PATH = FIXTURE_ROOT / "audit.jsonl"
AUDIT_LOCK = Lock()


def current_generation() -> str:
    value = (FIXTURE_ROOT / "generation").read_text(encoding="ascii")
    if value not in KEYS:
        raise RuntimeError("invalid fixture generation")
    return value


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def audit(route: str, generation: str) -> None:
    document = {
        "recordedAt": timestamp(),
        "route": route,
        "generation": generation,
    }
    with AUDIT_LOCK:
        with AUDIT_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(document, sort_keys=True, separators=(",", ":")))
            stream.write("\n")


class OidcIssuerFixtureHandler(BaseHTTPRequestHandler):
    server_version = "OidcIssuerFixture/1"
    sys_version = ""

    def log_message(self, *args: object) -> None:
        del args

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        generation = current_generation()
        if self.path == "/redirect":
            audit("/redirect", generation)
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/jwks")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if self.path == "/jwks":
            audit("/jwks", generation)
            self._json(HTTPStatus.OK, {"keys": [KEYS[generation]]})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not-found"})

    def _json(self, status: HTTPStatus, document: dict[str, object]) -> None:
        content = json.dumps(document, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8443), OidcIssuerFixtureHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(
        certfile=str(FIXTURE_ROOT / "server.crt"),
        keyfile=str(FIXTURE_ROOT / "server.key"),
    )
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
