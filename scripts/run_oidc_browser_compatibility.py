#!/usr/bin/env python3
"""Generate source-bound real-TLS evidence for the console OIDC PKCE flow."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import secrets
import ssl
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import Mapping, Sequence
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from iip import __version__ as APPLICATION_VERSION  # noqa: E402
from iip.adapters.auth import OidcJwtAuthenticator  # noqa: E402
from iip.application.ports import Authenticator  # noqa: E402
from iip.bootstrap import build_local_runtime  # noqa: E402
from iip.surfaces.http import (  # noqa: E402
    ApiHandler,
    DrainingThreadingHTTPServer,
)

import run_oidc_issuer_compatibility as issuer_compatibility  # noqa: E402
import validate_repo  # noqa: E402
import validate_schemas  # noqa: E402


COMPOSE_FILE = ROOT / "deploy" / "docker-compose.oidc-issuer.yml"
PROJECT = "iip-oidc-browser-test"
CHECK_IDS = (
    "ca-verified-browser-endpoints",
    "exact-authorization-request",
    "s256-required",
    "redirect-state-issuer-binding",
    "token-preflight-cors",
    "exact-origin-enforcement",
    "public-client-no-secret-or-cookie",
    "s256-token-exchange",
    "wrong-verifier-denial",
    "authorization-code-replay-denial",
    "exchanged-token-authentication",
    "secret-redaction",
)
MAXIMUM_RESPONSE_BYTES = 65_536


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def header_values(self, name: str) -> tuple[str, ...]:
        normalized = name.lower()
        return tuple(
            value for key, value in self.headers if key.lower() == normalized
        )


class NoRedirect(HTTPRedirectHandler):
    """Return redirect responses to the qualification runner for inspection."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")


def authorization_url(
    profile: Mapping[str, object],
    *,
    verifier: str,
    state: str,
    method: str = "S256",
) -> str:
    endpoint = str(profile["authorizationEndpoint"])
    return endpoint + "?" + urlencode(
        {
            "response_type": "code",
            "client_id": str(profile["clientId"]),
            "redirect_uri": str(profile["redirectUri"]),
            "scope": " ".join(str(value) for value in profile["scopes"]),
            "state": state,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": method,
        }
    )


def token_form(
    profile: Mapping[str, object],
    *,
    code: str,
    verifier: str,
    extra: Mapping[str, str] | None = None,
) -> bytes:
    fields = {
        "grant_type": "authorization_code",
        "client_id": str(profile["clientId"]),
        "code": code,
        "redirect_uri": str(profile["redirectUri"]),
        "code_verifier": verifier,
    }
    fields.update(extra or {})
    return urlencode(fields).encode("ascii")


def request(
    url: str,
    ca_bundle: Path,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> HttpResponse:
    context = ssl.create_default_context(cafile=str(ca_bundle))
    opener = build_opener(
        ProxyHandler({}),
        HTTPSHandler(context=context),
        NoRedirect(),
    )
    outgoing = Request(
        url,
        data=body,
        method=method,
        headers={"Accept": "application/json", **dict(headers or {})},
    )
    try:
        response = opener.open(outgoing, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        content = response.read(MAXIMUM_RESPONSE_BYTES + 1)
        if len(content) > MAXIMUM_RESPONSE_BYTES:
            raise RuntimeError("OIDC browser endpoint returned an oversized response")
        raw_items = getattr(response.headers, "raw_items", response.headers.items)
        return HttpResponse(
            status=response.status,
            headers=tuple(raw_items()),
            body=content,
        )


def response_json(response: HttpResponse) -> Mapping[str, object]:
    try:
        value = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("OIDC browser endpoint returned invalid JSON") from None
    if not isinstance(value, dict):
        raise RuntimeError("OIDC browser endpoint returned a non-object")
    return value


def api_session(authenticator: Authenticator, token: str) -> Mapping[str, object]:
    """Authenticate the exchanged token through the shipped HTTP API boundary."""

    runtime = build_local_runtime(authenticator)

    class QualificationApiHandler(ApiHandler):
        pass

    QualificationApiHandler.runtime = runtime
    server = DrainingThreadingHTTPServer(
        ("127.0.0.1", 0),
        QualificationApiHandler,
    )
    thread = Thread(
        target=server.serve_forever,
        name="iip-oidc-browser-api",
        daemon=True,
    )
    thread.start()
    try:
        port = server.server_address[1]
        outgoing = Request(
            f"http://127.0.0.1:{port}/v1/session",
            method="GET",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        opener = build_opener(ProxyHandler({}))
        try:
            response = opener.open(outgoing, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            body = response.read(MAXIMUM_RESPONSE_BYTES + 1)
            if len(body) > MAXIMUM_RESPONSE_BYTES:
                raise RuntimeError("session API returned an oversized response")
            result = HttpResponse(
                status=response.status,
                headers=tuple(response.headers.items()),
                body=body,
            )
        require_status(result, 200, "exchanged-token session API")
        return response_json(result)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        runtime.close()


def require_status(response: HttpResponse, expected: int, stage: str) -> None:
    if response.status != expected:
        raise RuntimeError(f"{stage} returned HTTP {response.status}")


def authorize(
    profile: Mapping[str, object],
    ca_bundle: Path,
    *,
    verifier: str,
    state: str,
) -> tuple[str, str]:
    response = request(
        authorization_url(profile, verifier=verifier, state=state),
        ca_bundle,
    )
    require_status(response, 302, "authorization")
    if response.body or response.header_values("Set-Cookie"):
        raise RuntimeError("authorization response carried unexpected browser state")
    locations = response.header_values("Location")
    if len(locations) != 1:
        raise RuntimeError("authorization response did not contain one redirect")
    location = urlsplit(locations[0])
    redirect = urlsplit(str(profile["redirectUri"]))
    if (
        (location.scheme, location.netloc, location.path)
        != (redirect.scheme, redirect.netloc, redirect.path)
        or location.fragment
    ):
        raise RuntimeError("authorization response changed the exact redirect")
    parameters = parse_qs(
        location.query,
        keep_blank_values=True,
        strict_parsing=True,
    )
    if set(parameters) != {"code", "state", "iss"} or any(
        len(values) != 1 for values in parameters.values()
    ):
        raise RuntimeError("authorization response contained ambiguous parameters")
    if (
        parameters["state"][0] != state
        or parameters["iss"][0] != profile["issuer"]
    ):
        raise RuntimeError("authorization response changed state or issuer")
    code = parameters["code"][0]
    if not code:
        raise RuntimeError("authorization response omitted its code")
    return code, locations[0]


def exchange(
    profile: Mapping[str, object],
    ca_bundle: Path,
    *,
    code: str,
    verifier: str,
    origin: str,
    extra: Mapping[str, str] | None = None,
    cookie: str | None = None,
) -> HttpResponse:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": origin,
    }
    if cookie is not None:
        headers["Cookie"] = cookie
    return request(
        str(profile["tokenEndpoint"]),
        ca_bundle,
        method="POST",
        body=token_form(profile, code=code, verifier=verifier, extra=extra),
        headers=headers,
    )


def report_id(report: Mapping[str, object]) -> str:
    metadata = report["metadata"]
    spec = report["spec"]
    if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
        raise ValueError("OIDC browser report shape is invalid")
    identity = {
        "sourceRevision": metadata["sourceRevision"],
        "sourceDirty": metadata["sourceDirty"],
        "environment": spec["environment"],
        "profile": spec["profile"],
        "checks": spec["checks"],
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return "obc_" + hashlib.sha256(encoded).hexdigest()[:32]


def compatibility_report(
    *,
    revision: str,
    source_dirty: bool,
    docker_platform: str,
    docker_version: str,
) -> dict[str, object]:
    checks = [{"id": check_id, "status": "passed"} for check_id in CHECK_IDS]
    report: dict[str, object] = {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "OidcBrowserCompatibilityReport",
        "metadata": {
            "id": "obc_" + "0" * 32,
            "generatedAt": issuer_compatibility.timestamp(
                issuer_compatibility.utc_now()
            ),
            "sourceRevision": revision,
            "sourceDirty": source_dirty,
        },
        "spec": {
            "status": "compatible",
            "environment": {
                "platform": docker_platform,
                "containerRuntime": "docker",
                "containerRuntimeVersion": docker_version,
                "pythonVersion": platform.python_version(),
                "applicationVersion": APPLICATION_VERSION,
            },
            "profile": {
                "name": "local-oidc-browser-pkce-v1",
                "flow": "authorization-code",
                "pkceMethod": "S256",
                "responseMode": "query",
                "tokenEndpointAuthentication": "none",
                "redirectProfile": "exact-loopback-console",
                "corsProfile": "exact-console-origin",
                "authorizationCodeLifetimeSeconds": 120,
                "maximumOutstandingAuthorizationCodes": 128,
            },
            "checks": checks,
            "summary": {
                "totalChecks": len(checks),
                "passedChecks": len(checks),
                "failedChecks": 0,
                "overallStatus": "compatible",
            },
        },
    }
    report["metadata"]["id"] = report_id(report)  # type: ignore[index]
    return report


def validate_report(report: dict[str, object]) -> None:
    schema = json.loads(
        (
            ROOT
            / "contracts/schemas/oidc-browser-compatibility-report.schema.json"
        ).read_text(encoding="utf-8")
    )
    errors = validate_schemas.instance_validation_errors(
        schema,
        report,
        label="generated OIDC browser compatibility report",
    )
    if errors:
        raise RuntimeError("; ".join(errors))
    semantic_errors: list[str] = []
    validate_repo.validate_oidc_browser_compatibility_document(
        report,
        semantic_errors,
    )
    if semantic_errors:
        raise RuntimeError("; ".join(semantic_errors))


def _write_browser_token(directory: Path, token: str) -> None:
    path = directory / "browser-access-token"
    path.write_text(token, encoding="ascii")
    os.chmod(path, 0o644)


def _compose_command(docker: str, *arguments: str) -> list[str]:
    return [
        docker,
        "compose",
        "--project-name",
        PROJECT,
        "--file",
        str(COMPOSE_FILE),
        *arguments,
    ]


def _run_command(
    command: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    capture: bool = False,
) -> str:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=dict(environment) if environment is not None else None,
        text=True,
        capture_output=capture,
        timeout=180,
        check=True,
    )
    return completed.stdout.strip() if capture else ""


def _assert_cors(response: HttpResponse, origin: str) -> None:
    if (
        response.header_values("Access-Control-Allow-Origin") != (origin,)
        or response.header_values("Access-Control-Allow-Credentials")
        or response.header_values("Set-Cookie")
    ):
        raise RuntimeError("token endpoint returned an unsafe CORS policy")


def run_profile(docker: str, report_path: Path) -> dict[str, object]:
    report_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="iip-oidc-browser-") as temporary:
        directory = Path(temporary)
        os.chmod(directory, 0o755)
        keys = issuer_compatibility.write_fixture(directory)
        browser_token = issuer_compatibility.access_token(
            keys["generation-a"],
            "generation-a",
        )
        _write_browser_token(directory, browser_token)
        configuration = issuer_compatibility.configuration(directory)
        discovery = configuration.console_authentication_document()
        profile = discovery.get("spec", {}).get("oidc")  # type: ignore[union-attr]
        if not isinstance(profile, Mapping):
            raise RuntimeError("OIDC browser discovery was unavailable")
        ca_bundle = directory / "ca.crt"
        redirect = urlsplit(str(profile["redirectUri"]))
        origin = f"{redirect.scheme}://{redirect.netloc}"
        environment = dict(os.environ)
        environment["IIP_OIDC_ISSUER_FIXTURE_DIR"] = str(directory)
        down = _compose_command(docker, "down", "--volumes", "--remove-orphans")
        _run_command(down, environment=environment)
        protected_values: list[str] = []
        try:
            _run_command(
                _compose_command(docker, "up", "--detach", "--wait"),
                environment=environment,
            )

            health = request(
                str(profile["authorizationEndpoint"]).removesuffix("/authorize")
                + "/healthz",
                ca_bundle,
            )
            require_status(health, 200, "CA-verified browser endpoint")

            verifier = secrets.token_urlsafe(64)
            state = secrets.token_urlsafe(32)
            protected_values.extend((verifier, pkce_challenge(verifier), state))
            invalid_method = request(
                authorization_url(
                    profile,
                    verifier=verifier,
                    state=state,
                    method="plain",
                ),
                ca_bundle,
            )
            require_status(invalid_method, 400, "non-S256 authorization")
            if invalid_method.header_values("Location"):
                raise RuntimeError("non-S256 authorization returned a redirect")

            code, location = authorize(
                profile,
                ca_bundle,
                verifier=verifier,
                state=state,
            )
            protected_values.extend((code, location))

            preflight = request(
                str(profile["tokenEndpoint"]),
                ca_bundle,
                method="OPTIONS",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            require_status(preflight, 204, "token preflight")
            _assert_cors(preflight, origin)
            if (
                preflight.header_values("Access-Control-Allow-Methods") != ("POST",)
                or preflight.header_values("Access-Control-Allow-Headers")
                != ("content-type",)
            ):
                raise RuntimeError("token preflight did not return the exact policy")
            denied_preflight = request(
                str(profile["tokenEndpoint"]),
                ca_bundle,
                method="OPTIONS",
                headers={
                    "Origin": "https://other-console.invalid",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            require_status(denied_preflight, 403, "unapproved-origin preflight")
            if denied_preflight.header_values("Access-Control-Allow-Origin"):
                raise RuntimeError("unapproved origin received CORS authority")

            secret_code, _ = authorize(
                profile,
                ca_bundle,
                verifier=verifier,
                state=secrets.token_urlsafe(32),
            )
            protected_values.append(secret_code)
            client_secret = "forbidden-browser-client-secret"
            protected_values.append(client_secret)
            secret_response = exchange(
                profile,
                ca_bundle,
                code=secret_code,
                verifier=verifier,
                origin=origin,
                extra={"client_secret": client_secret},
            )
            require_status(secret_response, 400, "client-secret token exchange")
            cookie_response = exchange(
                profile,
                ca_bundle,
                code=secret_code,
                verifier=verifier,
                origin=origin,
                cookie="fixture-session=forbidden",
            )
            require_status(cookie_response, 400, "cookie token exchange")

            origin_code, _ = authorize(
                profile,
                ca_bundle,
                verifier=verifier,
                state=secrets.token_urlsafe(32),
            )
            protected_values.append(origin_code)
            origin_response = exchange(
                profile,
                ca_bundle,
                code=origin_code,
                verifier=verifier,
                origin="https://other-console.invalid",
            )
            require_status(origin_response, 403, "unapproved-origin token exchange")
            if origin_response.header_values("Access-Control-Allow-Origin"):
                raise RuntimeError("unapproved token origin received CORS authority")

            wrong_verifier = secrets.token_urlsafe(64)
            wrong_code, _ = authorize(
                profile,
                ca_bundle,
                verifier=verifier,
                state=secrets.token_urlsafe(32),
            )
            protected_values.extend((wrong_verifier, wrong_code))
            wrong_response = exchange(
                profile,
                ca_bundle,
                code=wrong_code,
                verifier=wrong_verifier,
                origin=origin,
            )
            require_status(wrong_response, 400, "wrong-verifier token exchange")
            if response_json(wrong_response) != {"error": "invalid_grant"}:
                raise RuntimeError("wrong verifier returned an unstable denial")

            valid_response = exchange(
                profile,
                ca_bundle,
                code=code,
                verifier=verifier,
                origin=origin,
            )
            require_status(valid_response, 200, "S256 token exchange")
            _assert_cors(valid_response, origin)
            token_document = response_json(valid_response)
            if (
                set(token_document) != {"access_token", "token_type", "expires_in"}
                or token_document.get("access_token") != browser_token
                or token_document.get("token_type") != "Bearer"
                or token_document.get("expires_in") != 300
            ):
                raise RuntimeError("token endpoint returned an invalid public response")

            replay = exchange(
                profile,
                ca_bundle,
                code=code,
                verifier=verifier,
                origin=origin,
            )
            require_status(replay, 400, "authorization-code replay")
            if response_json(replay) != {"error": "invalid_grant"}:
                raise RuntimeError("authorization-code replay returned an unstable denial")

            authenticator = OidcJwtAuthenticator(configuration)
            session = api_session(authenticator, browser_token)
            if session != {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "SessionContext",
                "metadata": {
                    "tenantId": "tenant-a",
                    "actorId": "oidc-user-17",
                },
                "spec": {"roles": ["approver", "developer"]},
            }:
                raise RuntimeError("exchanged token derived the wrong API identity")

            audits = issuer_compatibility.audit_documents(directory)
            allowed_routes = {
                "/authorize",
                "/token",
                "/token/preflight",
                "/jwks",
            }
            if (
                not audits
                or any(set(entry) != {"recordedAt", "route", "generation"} for entry in audits)
                or any(entry.get("route") not in allowed_routes for entry in audits)
                or any(entry.get("generation") != "generation-a" for entry in audits)
                or not allowed_routes.issubset(
                    {str(entry.get("route")) for entry in audits}
                )
            ):
                raise RuntimeError("OIDC browser audit was not value-minimized")

            revision, dirty = issuer_compatibility.checked_revision()
            report = compatibility_report(
                revision=revision,
                source_dirty=dirty,
                docker_platform=_run_command(
                    [docker, "info", "--format", "{{.OSType}}/{{.Architecture}}"],
                    capture=True,
                ),
                docker_version=_run_command(
                    [docker, "version", "--format", "{{.Server.Version}}"],
                    capture=True,
                ),
            )
            private_material = tuple(
                key.private_bytes(
                    issuer_compatibility.serialization.Encoding.PEM,
                    issuer_compatibility.serialization.PrivateFormat.PKCS8,
                    issuer_compatibility.serialization.NoEncryption(),
                ).decode("ascii")
                for key in keys.values()
            )
            protected_values.extend(
                (
                    browser_token,
                    str(profile["issuer"]),
                    str(profile["authorizationEndpoint"]),
                    str(profile["tokenEndpoint"]),
                    str(profile["redirectUri"]),
                    str(profile["clientId"]),
                    origin,
                    "oidc-user-17",
                    "tenant-a",
                    "developer",
                    "approver",
                )
            )
            encoded_report = json.dumps(report, sort_keys=True)
            audit_text = (directory / "audit.jsonl").read_text(encoding="utf-8")
            server_key = (directory / "server.key").read_text(encoding="ascii")
            if any(
                value and (value in encoded_report or value in audit_text)
                for value in private_material
                + (server_key,)
                + tuple(protected_values)
            ):
                raise RuntimeError("OIDC browser evidence exposed protected material")
            validate_report(report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, indent=2, sort_keys=False) + "\n",
                encoding="utf-8",
            )
            return report
        finally:
            _run_command(down, environment=environment)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "dist" / "oidc-browser-compatibility-report.json",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    report_path = arguments.report.resolve()
    docker = os.environ.get("IIP_DOCKER_BIN", "docker")
    try:
        report = run_profile(docker, report_path)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        report_path.unlink(missing_ok=True)
        print(f"OIDC browser compatibility failed: {error}", file=sys.stderr)
        return 1
    summary = report["spec"]["summary"]  # type: ignore[index]
    print(
        "OIDC browser compatibility passed: "
        f"{summary['passedChecks']}/{summary['totalChecks']} checks; "  # type: ignore[index]
        "real-TLS authorization, S256 exchange, CORS, replay denial, and API authentication"
    )
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
