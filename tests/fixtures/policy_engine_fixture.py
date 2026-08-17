#!/usr/bin/env python3
"""TLS-only policy decision fixture for executable compatibility tests."""

from __future__ import annotations

import hashlib
import json
import ssl
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock


FIXTURE_ROOT = Path("/fixture")
TOKENS = json.loads((FIXTURE_ROOT / "tokens.json").read_text(encoding="utf-8"))
AUDIT_PATH = FIXTURE_ROOT / "audit.jsonl"
AUDIT_LOCK = Lock()
TENANT_ID = "tenant-a"
ACTOR_ID = "operator-a"
RESOURCE_UID = "res_" + ("a" * 32)


def current_generation() -> str:
    value = (FIXTURE_ROOT / "generation").read_text(encoding="ascii")
    if value not in TOKENS:
        raise RuntimeError("invalid fixture generation")
    return value


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def audit(route: str, generation: str, outcome: str) -> None:
    document = {
        "recordedAt": timestamp(),
        "route": route,
        "generation": generation,
        "outcome": outcome,
    }
    with AUDIT_LOCK:
        with AUDIT_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(document, sort_keys=True, separators=(",", ":")))
            stream.write("\n")


def canonical_digest(document: object) -> str:
    encoded = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def exact_input(document: object) -> tuple[bool, str | None]:
    if not isinstance(document, dict) or set(document) != {"input"}:
        return False, None
    request = document.get("input")
    if not isinstance(request, dict) or set(request) != {
        "apiVersion",
        "kind",
        "metadata",
        "spec",
    }:
        return False, None
    metadata = request.get("metadata")
    spec = request.get("spec")
    if (
        request.get("apiVersion") != "iip.platform/v1alpha1"
        or request.get("kind") != "PolicyDecisionRequest"
        or metadata != {"tenantId": TENANT_ID, "actorId": ACTOR_ID}
        or not isinstance(spec, dict)
        or set(spec) != {"action", "roles", "resource"}
        or spec.get("action") != "resource:read"
        or spec.get("roles") != ["developer", "approver"]
    ):
        return False, None
    resource = spec.get("resource")
    if not isinstance(resource, dict) or set(resource) not in (
        {"tenantId", "resourceUid"},
        {"tenantId", "resourceUid", "scenario"},
    ):
        return False, None
    if resource.get("tenantId") != TENANT_ID or resource.get("resourceUid") != RESOURCE_UID:
        return False, None
    scenario = resource.get("scenario")
    if scenario is not None and scenario not in {
        "deny",
        "stale-digest",
        "cross-tenant",
        "cross-snapshot",
    }:
        return False, None
    return True, scenario if isinstance(scenario, str) else None


class PolicyEngineFixtureHandler(BaseHTTPRequestHandler):
    server_version = "PolicyEngineFixture/1"
    sys_version = ""

    def log_message(self, *args: object) -> None:
        del args

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not-found"})

    def do_POST(self) -> None:
        generation = current_generation()
        if self.path == "/redirect":
            audit("/redirect", generation, "redirect")
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/v1/decision")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if self.path not in {
            "/v1/decision",
            "/wrong-content-type",
            "/oversized",
        }:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not-found"})
            return
        expected = "Bearer " + TOKENS[generation]
        if self.headers.get("Authorization") != expected:
            audit(self.path, generation, "authentication-denied")
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        if self.headers.get_content_type() != "application/json":
            audit(self.path, generation, "input-denied")
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "content-type"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 262_144:
                raise ValueError
            document = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            audit(self.path, generation, "input-denied")
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid-input"})
            return
        valid, scenario = exact_input(document)
        if not valid:
            audit(self.path, generation, "input-denied")
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid-input"})
            return
        if self.path == "/oversized":
            audit(self.path, generation, "oversized-response")
            self._raw_json(HTTPStatus.OK, b'{' + (b' ' * 2048) + b'}')
            return
        request = document["input"]
        tenant_id = "tenant-b" if scenario == "cross-tenant" else TENANT_ID
        snapshot_tenant = "tenant-b" if scenario == "cross-snapshot" else tenant_id
        decision = {
            "result": {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "PolicyDecision",
                "metadata": {"tenantId": tenant_id},
                "spec": {
                    "allowed": scenario != "deny",
                    "inputDigest": (
                        "sha256:" + ("0" * 64)
                        if scenario == "stale-digest"
                        else canonical_digest(request)
                    ),
                    "reasonCode": (
                        "policy.fixture-denied"
                        if scenario == "deny"
                        else "policy.fixture-allowed"
                    ),
                    "policySnapshotRef": (
                        f"policy://{snapshot_tenant}/snapshots/bundle-generation-1"
                    ),
                },
            }
        }
        outcome = "denied" if scenario == "deny" else "allowed"
        if scenario in {"stale-digest", "cross-tenant", "cross-snapshot"}:
            outcome = "invalid-response"
        audit(self.path, generation, outcome)
        if self.path == "/wrong-content-type":
            self._json(HTTPStatus.OK, decision, content_type="text/plain")
            return
        self._json(HTTPStatus.OK, decision)

    def _raw_json(self, status: HTTPStatus, content: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _json(
        self,
        status: HTTPStatus,
        document: dict[str, object],
        *,
        content_type: str = "application/json",
    ) -> None:
        content = json.dumps(document, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8443), PolicyEngineFixtureHandler)
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
