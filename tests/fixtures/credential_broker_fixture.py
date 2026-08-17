#!/usr/bin/env python3
"""TLS-only credential-broker fixture for executable client compatibility tests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import ssl
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock


FIXTURE_ROOT = Path("/fixture")
POLICY = json.loads((FIXTURE_ROOT / "policy.json").read_text(encoding="utf-8"))
AUDIT_PATH = FIXTURE_ROOT / "audit.jsonl"
REQUEST_ID = re.compile(r"crq_[a-f0-9]{32}")
MAX_BODY_BYTES = 32_768
STATE_LOCK = Lock()
ROTATED = False


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def decode_segment(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def verify_workload_token(value: object) -> tuple[str | None, str]:
    global ROTATED

    if not isinstance(value, str) or not value.startswith("Bearer "):
        return None, "workload-authentication"
    token = value[7:]
    try:
        encoded_header, encoded_claims, encoded_signature = token.split(".")
        signed = f"{encoded_header}.{encoded_claims}".encode("ascii")
        expected_signature = hmac.new(
            POLICY["jwtSigningKey"].encode("ascii"), signed, hashlib.sha256
        ).digest()
        signature = decode_segment(encoded_signature)
        header = json.loads(decode_segment(encoded_header))
        claims = json.loads(decode_segment(encoded_claims))
    except Exception:
        return None, "workload-authentication"
    if (
        header != {"alg": "HS256", "typ": "JWT"}
        or not hmac.compare_digest(signature, expected_signature)
        or not isinstance(claims, dict)
        or claims.get("iss") != POLICY["issuer"]
    ):
        return None, "workload-authentication"
    if claims.get("aud") != POLICY["audience"]:
        return None, "audience-denied"
    if claims.get("sub") != POLICY["subject"]:
        return None, "subject-denied"
    now = int(datetime.now(timezone.utc).timestamp())
    if (
        not isinstance(claims.get("iat"), int)
        or not isinstance(claims.get("exp"), int)
        or claims["iat"] > now + 5
        or claims["exp"] <= now
        or claims["exp"] - claims["iat"] > 600
        or claims.get("jti") not in POLICY["workloadGenerations"]
    ):
        return None, "workload-authentication"
    generation = claims["jti"]
    with STATE_LOCK:
        if ROTATED and generation == POLICY["workloadGenerations"][0]:
            return None, "workload-revoked"
    return generation, "authenticated"


def audit(request_id: str | None, result: str, reason: str) -> None:
    document = {
        "recordedAt": timestamp(datetime.now(timezone.utc)),
        "requestId": request_id,
        "result": result,
        "reason": reason,
    }
    with STATE_LOCK:
        with AUDIT_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(document, sort_keys=True, separators=(",", ":")))
            stream.write("\n")


class CredentialBrokerFixtureHandler(BaseHTTPRequestHandler):
    server_version = "CredentialBrokerFixture/1"
    sys_version = ""

    def log_message(self, *args: object) -> None:
        del args

    def do_GET(self) -> None:
        if self.path != "/healthz":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._json(HTTPStatus.OK, {"status": "ok"})

    def do_POST(self) -> None:
        global ROTATED

        if self.path != "/v1/credential-leases":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        generation, identity_reason = verify_workload_token(
            self.headers.get("Authorization")
        )
        if generation is None:
            audit(None, "denied", identity_reason)
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        document = self._request_document()
        if document is None:
            audit(None, "denied", "request-invalid")
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid-request"})
            return
        metadata = document["metadata"]
        spec = document["spec"]
        request_id = metadata.get("requestId")
        if metadata.get("tenantId") != POLICY["tenantId"]:
            audit(request_id, "denied", "tenant-denied")
            self._json(HTTPStatus.FORBIDDEN, {"error": "policy-denied"})
            return
        expected = {
            "integrationId": POLICY["integrationId"],
            "credentialRef": POLICY["credentialRef"],
            "provider": POLICY["provider"],
            "scopes": POLICY["scopes"],
        }
        if any(spec.get(key) != value for key, value in expected.items()):
            reason = (
                "scope-denied"
                if spec.get("scopes") != POLICY["scopes"]
                else "provider-policy-denied"
            )
            audit(request_id, "denied", reason)
            self._json(HTTPStatus.FORBIDDEN, {"error": "policy-denied"})
            return
        now = datetime.now(timezone.utc)
        try:
            deadline = datetime.fromisoformat(
                str(spec["deadline"]).replace("Z", "+00:00")
            )
        except (KeyError, ValueError):
            audit(request_id, "denied", "deadline-denied")
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid-request"})
            return
        if deadline <= now or deadline > now + timedelta(seconds=120):
            audit(request_id, "denied", "deadline-denied")
            self._json(HTTPStatus.FORBIDDEN, {"error": "policy-denied"})
            return
        issued_at = now
        expires_at = max(deadline + timedelta(seconds=5), now + timedelta(seconds=60))
        material = f"{generation}:{request_id}".encode("utf-8")
        secret = "fixture-lease-" + hmac.new(
            POLICY["leaseDerivationKey"].encode("ascii"), material, hashlib.sha256
        ).hexdigest()
        if generation == POLICY["workloadGenerations"][1]:
            with STATE_LOCK:
                ROTATED = True
        audit(request_id, "issued", "exact-scope")
        self._json(
            HTTPStatus.OK,
            {
                "apiVersion": "iip.broker/v1alpha1",
                "kind": "CredentialLease",
                "metadata": {
                    "requestId": request_id,
                    "issuedAt": timestamp(issued_at),
                },
                "spec": {
                    "scheme": "bearer",
                    "secret": secret,
                    "expiresAt": timestamp(expires_at),
                },
            },
        )

    def _request_document(self) -> dict[str, object] | None:
        if self.headers.get_content_type() != "application/json":
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if not 1 <= length <= MAX_BODY_BYTES:
            return None
        try:
            document = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(document, dict)
            or set(document) != {"apiVersion", "kind", "metadata", "spec"}
            or document.get("apiVersion") != "iip.broker/v1alpha1"
            or document.get("kind") != "CredentialLeaseRequest"
            or not isinstance(document.get("metadata"), dict)
            or not isinstance(document.get("spec"), dict)
        ):
            return None
        metadata = document["metadata"]
        spec = document["spec"]
        if (
            set(metadata) != {"requestId", "tenantId", "actorId", "requestedAt"}
            or set(spec)
            != {
                "integrationId",
                "credentialRef",
                "provider",
                "scopes",
                "deadline",
            }
            or not isinstance(metadata.get("requestId"), str)
            or REQUEST_ID.fullmatch(metadata["requestId"]) is None
        ):
            return None
        return document

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
    server = ThreadingHTTPServer(("0.0.0.0", 8443), CredentialBrokerFixtureHandler)
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
