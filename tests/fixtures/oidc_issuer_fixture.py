#!/usr/bin/env python3
"""TLS-only OIDC fixture for verifier and browser compatibility tests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import ssl
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlencode, urlsplit


FIXTURE_ROOT = Path("/fixture")
KEYS = json.loads((FIXTURE_ROOT / "public-keys.json").read_text(encoding="utf-8"))
AUDIT_PATH = FIXTURE_ROOT / "audit.jsonl"
AUDIT_LOCK = Lock()
AUTHORIZATION_CODE_LOCK = Lock()
AUTHORIZATION_CODES: dict[str, dict[str, object]] = {}
ISSUER = "https://127.0.0.1:19443/"
CLIENT_ID = "iip-console-fixture"
REDIRECT_URI = "http://127.0.0.1:8080/console"
CONSOLE_ORIGIN = "http://127.0.0.1:8080"
SCOPES = "openid profile"
CODE_LIFETIME_SECONDS = 120
MAXIMUM_OUTSTANDING_CODES = 128
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")


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
    server_version = "OidcIssuerFixture/2"
    sys_version = ""

    def log_message(self, *args: object) -> None:
        del args

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz" and not parsed.query:
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        generation = current_generation()
        if parsed.path == "/redirect" and not parsed.query:
            audit("/redirect", generation)
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/jwks")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if parsed.path == "/jwks" and not parsed.query:
            audit("/jwks", generation)
            self._json(HTTPStatus.OK, {"keys": [KEYS[generation]]})
            return
        if parsed.path == "/authorize":
            audit("/authorize", generation)
            self._authorize(parsed.query)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not-found"})

    def do_OPTIONS(self) -> None:
        parsed = urlsplit(self.path)
        generation = current_generation()
        if parsed.path != "/token" or parsed.query:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not-found"})
            return
        audit("/token/preflight", generation)
        origins = self.headers.get_all("Origin", failobj=[])
        requested_methods = self.headers.get_all(
            "Access-Control-Request-Method", failobj=[]
        )
        requested_headers = self.headers.get_all(
            "Access-Control-Request-Headers", failobj=[]
        )
        if (
            origins != [CONSOLE_ORIGIN]
            or requested_methods != ["POST"]
            or len(requested_headers) != 1
            or {
                value.strip().lower()
                for value in requested_headers[0].split(",")
                if value.strip()
            }
            != {"content-type"}
        ):
            self._json(HTTPStatus.FORBIDDEN, {"error": "origin-denied"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "POST")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        generation = current_generation()
        if parsed.path != "/token" or parsed.query:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not-found"})
            return
        audit("/token", generation)
        origins = self.headers.get_all("Origin", failobj=[])
        if origins != [CONSOLE_ORIGIN]:
            self._json(HTTPStatus.FORBIDDEN, {"error": "origin-denied"})
            return
        if self.headers.get_all("Cookie", failobj=[]):
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request"},
                cors=True,
            )
            return
        content_types = self.headers.get_all("Content-Type", failobj=[])
        lengths = self.headers.get_all("Content-Length", failobj=[])
        try:
            if (
                len(content_types) != 1
                or content_types[0].split(";", 1)[0].strip().lower()
                != "application/x-www-form-urlencoded"
                or len(lengths) != 1
                or not lengths[0].isdigit()
                or not 1 <= int(lengths[0]) <= 8_192
            ):
                raise ValueError
            body = self.rfile.read(int(lengths[0])).decode("ascii")
            parameters = parse_qs(
                body,
                keep_blank_values=True,
                strict_parsing=True,
            )
            expected = {
                "grant_type",
                "client_id",
                "code",
                "redirect_uri",
                "code_verifier",
            }
            if set(parameters) != expected or any(
                len(values) != 1 for values in parameters.values()
            ):
                raise ValueError
            values = {key: entries[0] for key, entries in parameters.items()}
            verifier = values["code_verifier"]
            if (
                values["grant_type"] != "authorization_code"
                or values["client_id"] != CLIENT_ID
                or values["redirect_uri"] != REDIRECT_URI
                or not 43 <= len(verifier) <= 128
                or _BASE64URL.fullmatch(verifier) is None
                or not 1 <= len(values["code"]) <= 128
                or _BASE64URL.fullmatch(values["code"]) is None
            ):
                raise ValueError
        except (UnicodeDecodeError, ValueError):
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request"},
                cors=True,
            )
            return

        with AUTHORIZATION_CODE_LOCK:
            authorization = AUTHORIZATION_CODES.pop(values["code"], None)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        if (
            authorization is None
            or not isinstance(authorization.get("expiresAt"), float)
            or time.monotonic() > authorization["expiresAt"]
            or not isinstance(authorization.get("codeChallenge"), str)
            or not hmac.compare_digest(authorization["codeChallenge"], challenge)
        ):
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_grant"},
                cors=True,
            )
            return
        try:
            access_token = (FIXTURE_ROOT / "browser-access-token").read_text(
                encoding="ascii"
            )
        except OSError:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "temporarily_unavailable"},
                cors=True,
            )
            return
        if (
            access_token != access_token.strip()
            or not 32 <= len(access_token) <= 8_192
        ):
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "temporarily_unavailable"},
                cors=True,
            )
            return
        self._json(
            HTTPStatus.OK,
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": 300,
            },
            cors=True,
        )

    def _authorize(self, query: str) -> None:
        try:
            parameters = parse_qs(
                query,
                keep_blank_values=True,
                strict_parsing=True,
            )
            expected = {
                "response_type",
                "client_id",
                "redirect_uri",
                "scope",
                "state",
                "code_challenge",
                "code_challenge_method",
            }
            if set(parameters) != expected or any(
                len(values) != 1 for values in parameters.values()
            ):
                raise ValueError
            values = {key: entries[0] for key, entries in parameters.items()}
            if (
                values["response_type"] != "code"
                or values["client_id"] != CLIENT_ID
                or values["redirect_uri"] != REDIRECT_URI
                or values["scope"] != SCOPES
                or values["code_challenge_method"] != "S256"
                or not 43 <= len(values["state"]) <= 128
                or _BASE64URL.fullmatch(values["state"]) is None
                or len(values["code_challenge"]) != 43
                or _BASE64URL.fullmatch(values["code_challenge"]) is None
            ):
                raise ValueError
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return

        code = secrets.token_urlsafe(32)
        with AUTHORIZATION_CODE_LOCK:
            now = time.monotonic()
            for expired in tuple(AUTHORIZATION_CODES):
                expires_at = AUTHORIZATION_CODES[expired].get("expiresAt")
                if isinstance(expires_at, float) and now > expires_at:
                    del AUTHORIZATION_CODES[expired]
            if len(AUTHORIZATION_CODES) >= MAXIMUM_OUTSTANDING_CODES:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "temporarily_unavailable"},
                )
                return
            AUTHORIZATION_CODES[code] = {
                "codeChallenge": values["code_challenge"],
                "expiresAt": now + CODE_LIFETIME_SECONDS,
            }
        location = REDIRECT_URI + "?" + urlencode(
            {"code": code, "state": values["state"], "iss": ISSUER}
        )
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", CONSOLE_ORIGIN)
        self.send_header("Vary", "Origin")

    def _json(
        self,
        status: HTTPStatus,
        document: dict[str, object],
        *,
        cors: bool = False,
    ) -> None:
        content = json.dumps(document, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        if cors:
            self._cors_headers()
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
