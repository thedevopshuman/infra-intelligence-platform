#!/usr/bin/env python3
"""Qualify one customer OIDC verifier and console-browser prerequisite boundary."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import ssl
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
API_VERSION = "iip.platform/v1alpha1"
PROFILE_KIND = "CustomerOidcQualificationProfile"
REPORT_KIND = "CustomerOidcQualificationReport"
QUALIFICATION = "customer-oidc-verifier-browser-prerequisites-v1"
PROFILE_SCHEMA = ROOT / "contracts/schemas/customer-oidc-qualification-profile.schema.json"
REPORT_SCHEMA = ROOT / "contracts/schemas/customer-oidc-qualification-report.schema.json"
REPORT_ID = re.compile(r"^coq_[a-f0-9]{32}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")
BEARER = re.compile(r"[a-zA-Z0-9._~+/-]{32,8192}=*")
JWT_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
CHECK_IDS = (
    "profile-binding",
    "source-binding",
    "api-target-binding",
    "ca-verified-discovery",
    "issuer-metadata-binding",
    "jwks-metadata-binding",
    "authorization-code-supported",
    "s256-supported",
    "public-client-supported",
    "console-profile-binding",
    "token-cors-exact-origin",
    "token-cors-other-origin-denied",
    "access-token-claims-binding",
    "access-token-api-authentication",
    "tampered-token-denial",
    "runtime-release-binding",
    "minimized-output",
)
LIMITATIONS = (
    "interactive-authorization-and-mfa-not-observed",
    "logout-session-and-consent-not-qualified",
    "disablement-and-revocation-latency-not-qualified",
    "issuer-ha-certificate-and-key-rotation-not-qualified",
)
FORBIDDEN_RETAINED_KEYS = frozenset(
    {
        "accessToken",
        "actorId",
        "audience",
        "authorizationEndpoint",
        "baseUrl",
        "clientId",
        "credential",
        "discoveryUrl",
        "issuer",
        "jwksUrl",
        "redirectUri",
        "roles",
        "secret",
        "tenantId",
        "token",
        "tokenEndpoint",
        "url",
    }
)


class CustomerOidcQualificationError(RuntimeError):
    """A stable external OIDC qualification failure."""


def _fail(code: str) -> None:
    raise CustomerOidcQualificationError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        _fail("customer-oidc-qualification.time.invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: object, code: str) -> datetime:
    if not isinstance(value, str):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None:
        _fail(code)
    return parsed.astimezone(timezone.utc)


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _integer(value: object, minimum: int, maximum: int, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail(code)
    return value


def _schema(path: Path, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _validate_schema(document: Mapping[str, Any], path: Path, code: str) -> None:
    errors = sorted(
        Draft202012Validator(
            _schema(path, code), format_checker=FormatChecker()
        ).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        _fail(code)


def _protected_bytes(path: Path, *, maximum_bytes: int, code: str) -> bytes:
    descriptor = -1
    candidate = path.expanduser()
    try:
        if candidate.is_symlink():
            _fail(code)
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_size > maximum_bytes
        ):
            _fail(code)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            payload = handle.read(maximum_bytes + 1)
        if len(payload) > maximum_bytes:
            _fail(code)
        return payload
    except CustomerOidcQualificationError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load_profile(path: Path) -> Mapping[str, Any]:
    payload = _protected_bytes(
        path,
        maximum_bytes=262_144,
        code="customer-oidc-qualification.profile.invalid",
    )
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-oidc-qualification.profile.invalid")
    if not isinstance(document, Mapping):
        _fail("customer-oidc-qualification.profile.invalid")
    validate_profile(document)
    return document


def load_access_token(path: Path) -> str:
    payload = _protected_bytes(
        path,
        maximum_bytes=8194,
        code="customer-oidc-qualification.credential.invalid",
    )
    try:
        raw = payload.decode("ascii")
    except UnicodeDecodeError:
        _fail("customer-oidc-qualification.credential.invalid")
    token = raw.rstrip("\r\n")
    segments = token.split(".")
    if (
        raw not in (token, token + "\n", token + "\r\n")
        or BEARER.fullmatch(token) is None
        or len(segments) != 3
        or any(JWT_SEGMENT.fullmatch(segment) is None for segment in segments)
        or not 32 <= len(segments[2]) <= 4096
    ):
        _fail("customer-oidc-qualification.credential.invalid")
    return token


def _ca_context(path: Path, code: str) -> ssl.SSLContext:
    candidate = path.expanduser()
    try:
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or candidate.stat().st_size > 2 * 1024 * 1024
        ):
            _fail(code)
        return ssl.create_default_context(cafile=str(candidate.resolve()))
    except CustomerOidcQualificationError:
        raise
    except (OSError, ssl.SSLError):
        _fail(code)


def _strict_https_url(value: object, code: str, *, root_only: bool = False) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 2048
        or re.search(r"[\x00-\x20\x7f]", value) is not None
    ):
        _fail(code)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        _fail(code)
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or not parsed.hostname.isascii()
        or len(parsed.hostname) > 253
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (root_only and parsed.path not in ("", "/"))
    ):
        _fail(code)
    host = parsed.hostname.lower()
    host_text = f"[{host}]" if ":" in host else host
    authority = host_text if port in (None, 443) else f"{host_text}:{port}"
    path = parsed.path or ("" if root_only else "/")
    if root_only:
        path = ""
    return f"https://{authority}{path}"


def _console_origin(redirect_uri: str) -> str:
    parsed = urlsplit(redirect_uri)
    if parsed.scheme != "https" or parsed.hostname is None:
        _fail("customer-oidc-qualification.profile.invalid")
    port = parsed.port
    host = parsed.hostname.lower()
    host_text = f"[{host}]" if ":" in host else host
    authority = host_text if port in (None, 443) else f"{host_text}:{port}"
    return f"https://{authority}"


def validate_profile(profile: Mapping[str, Any]) -> None:
    _validate_schema(
        profile,
        PROFILE_SCHEMA,
        "customer-oidc-qualification.profile.invalid",
    )
    metadata = _mapping(
        profile.get("metadata"), "customer-oidc-qualification.profile.invalid"
    )
    spec = _mapping(profile.get("spec"), "customer-oidc-qualification.profile.invalid")
    identity = _mapping(
        spec.get("identity"), "customer-oidc-qualification.profile.invalid"
    )
    oidc = _mapping(spec.get("oidc"), "customer-oidc-qualification.profile.invalid")
    browser = _mapping(
        oidc.get("browser"), "customer-oidc-qualification.profile.invalid"
    )
    objective = _mapping(
        spec.get("objective"), "customer-oidc-qualification.profile.invalid"
    )
    actor_claim = oidc.get("actorClaim")
    tenant_claim = oidc.get("tenantClaim")
    roles_claim = oidc.get("rolesClaim")
    roles = identity.get("roles")
    scopes = browser.get("scopes")
    if (
        profile.get("apiVersion") != API_VERSION
        or profile.get("kind") != PROFILE_KIND
        or not isinstance(metadata.get("name"), str)
        or not isinstance(identity.get("tenantId"), str)
        or not isinstance(identity.get("actorId"), str)
        or not isinstance(roles, list)
        or not isinstance(scopes, list)
        or len({actor_claim, tenant_claim, roles_claim}) != 3
    ):
        _fail("customer-oidc-qualification.profile.invalid")
    _parse_timestamp(
        metadata.get("reviewedAt"), "customer-oidc-qualification.profile.invalid"
    )
    for key in ("issuer", "discoveryUrl", "jwksUrl"):
        _strict_https_url(oidc.get(key), "customer-oidc-qualification.profile.invalid")
    for key in ("authorizationEndpoint", "tokenEndpoint"):
        _strict_https_url(browser.get(key), "customer-oidc-qualification.profile.invalid")
    redirect = _strict_https_url(
        browser.get("redirectUri"), "customer-oidc-qualification.profile.invalid"
    )
    if urlsplit(redirect).path not in ("/console", "/console/"):
        _fail("customer-oidc-qualification.profile.invalid")
    minimum_remaining = _integer(
        objective.get("minimumRemainingTokenSeconds"),
        30,
        3600,
        "customer-oidc-qualification.profile.invalid",
    )
    maximum_lifetime = _integer(
        objective.get("maximumTokenLifetimeSeconds"),
        300,
        86400,
        "customer-oidc-qualification.profile.invalid",
    )
    if minimum_remaining >= maximum_lifetime:
        _fail("customer-oidc-qualification.profile.invalid")


def _source_identity() -> tuple[str, bool]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        _fail("customer-oidc-qualification.source.unavailable")
    if REVISION.fullmatch(revision) is None:
        _fail("customer-oidc-qualification.source.invalid")
    return revision, dirty


class _DenyRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, url):
        del request, file_pointer, code, message, headers, url
        return None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def values(self, name: str) -> tuple[str, ...]:
        normalized = name.lower()
        return tuple(value for key, value in self.headers if key.lower() == normalized)


class HttpsClient:
    """Direct, CA-verified, redirect-denying HTTP client for the qualification."""

    def __init__(
        self,
        *,
        api_context: ssl.SSLContext,
        issuer_context: ssl.SSLContext,
        timeout_milliseconds: int,
        maximum_response_bytes: int,
    ) -> None:
        self._api = build_opener(
            ProxyHandler({}), HTTPSHandler(context=api_context), _DenyRedirects()
        )
        self._issuer = build_opener(
            ProxyHandler({}), HTTPSHandler(context=issuer_context), _DenyRedirects()
        )
        self._timeout = timeout_milliseconds / 1000
        self._maximum = maximum_response_bytes

    def request(
        self,
        url: str,
        *,
        issuer: bool,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        outgoing = Request(
            url,
            method=method,
            headers={
                "Accept": "application/json",
                "Connection": "close",
                "User-Agent": "iip-customer-oidc-qualification/1",
                **dict(headers or {}),
            },
        )
        opener = self._issuer if issuer else self._api
        try:
            response = opener.open(outgoing, timeout=self._timeout)
        except HTTPError as error:
            response = error
        except (URLError, OSError, TimeoutError, ssl.SSLError, HTTPException, ValueError):
            _fail("customer-oidc-qualification.transport.unavailable")
        with response:
            try:
                body = response.read(self._maximum + 1)
            except (OSError, TimeoutError, ssl.SSLError, HTTPException):
                _fail("customer-oidc-qualification.transport.unavailable")
            if len(body) > self._maximum:
                _fail("customer-oidc-qualification.response.oversized")
            raw_items = getattr(response.headers, "raw_items", response.headers.items)
            return HttpResponse(
                status=int(response.status),
                headers=tuple(raw_items()),
                body=body,
            )


def _json_response(response: HttpResponse, code: str) -> Mapping[str, Any]:
    content_types = response.values("Content-Type")
    if response.status != 200 or len(content_types) != 1:
        _fail(code)
    media_type = content_types[0].split(";", 1)[0].strip().lower()
    if media_type not in ("application/json", "application/jwk-set+json"):
        _fail(code)
    try:
        value = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _jwt_parts(token: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    segments = token.split(".")
    if (
        len(segments) != 3
        or any(JWT_SEGMENT.fullmatch(segment) is None for segment in segments)
        or not 32 <= len(segments[2]) <= 4096
    ):
        _fail("customer-oidc-qualification.credential.invalid")
    values: list[Mapping[str, Any]] = []
    for encoded in segments[:2]:
        try:
            padding = "=" * (-len(encoded) % 4)
            value = json.loads(base64.urlsafe_b64decode(encoded + padding))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            _fail("customer-oidc-qualification.credential.invalid")
        if not isinstance(value, Mapping):
            _fail("customer-oidc-qualification.credential.invalid")
        values.append(value)
    return values[0], values[1]


def _token_measurements(
    token: str, profile: Mapping[str, Any], now: datetime
) -> tuple[int, int, str]:
    spec = _mapping(profile.get("spec"), "customer-oidc-qualification.profile.invalid")
    identity = _mapping(spec.get("identity"), "customer-oidc-qualification.profile.invalid")
    oidc = _mapping(spec.get("oidc"), "customer-oidc-qualification.profile.invalid")
    objective = _mapping(spec.get("objective"), "customer-oidc-qualification.profile.invalid")
    header, claims = _jwt_parts(token)
    algorithm = header.get("alg")
    key_id = header.get("kid")
    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    audience = claims.get("aud")
    audience_matches = audience == oidc.get("audience") or (
        isinstance(audience, list)
        and all(isinstance(value, str) for value in audience)
        and oidc.get("audience") in audience
    )
    roles = claims.get(str(oidc.get("rolesClaim")))
    if (
        algorithm != "RS256"
        or not isinstance(key_id, str)
        or not 1 <= len(key_id) <= 256
        or claims.get("iss") != oidc.get("issuer")
        or not audience_matches
        or isinstance(issued_at, bool)
        or not isinstance(issued_at, int)
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or claims.get(str(oidc.get("actorClaim"))) != identity.get("actorId")
        or claims.get(str(oidc.get("tenantClaim"))) != identity.get("tenantId")
        or roles != identity.get("roles")
    ):
        _fail("customer-oidc-qualification.token.claims-invalid")
    lifetime = expires_at - issued_at
    remaining = max(0, int(expires_at - now.timestamp()))
    if (
        lifetime <= 0
        or lifetime > objective.get("maximumTokenLifetimeSeconds")
        or remaining < objective.get("minimumRemainingTokenSeconds")
    ):
        _fail("customer-oidc-qualification.token.lifetime-invalid")
    return lifetime, remaining, key_id


def _expected_console_document(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    oidc = _mapping(
        _mapping(profile.get("spec"), "customer-oidc-qualification.profile.invalid").get("oidc"),
        "customer-oidc-qualification.profile.invalid",
    )
    browser = _mapping(oidc.get("browser"), "customer-oidc-qualification.profile.invalid")
    return {
        "apiVersion": API_VERSION,
        "kind": "ConsoleAuthenticationConfiguration",
        "spec": {
            "mode": "oidc-pkce",
            "oidc": {
                "issuer": oidc["issuer"],
                "clientId": browser["clientId"],
                "authorizationEndpoint": browser["authorizationEndpoint"],
                "tokenEndpoint": browser["tokenEndpoint"],
                "redirectUri": browser["redirectUri"],
                "scopes": browser["scopes"],
                "providerLabel": browser["providerLabel"],
                "pkceMethod": "S256",
            },
        },
    }


def _header_tokens(response: HttpResponse, name: str) -> set[str]:
    return {
        token.strip().lower()
        for value in response.values(name)
        for token in value.split(",")
        if token.strip()
    }


def _advertises(value: object, expected: str) -> bool:
    return (
        isinstance(value, list)
        and 1 <= len(value) <= 100
        and all(isinstance(item, str) for item in value)
        and expected in value
    )


def _cors_exact(response: HttpResponse, origin: str) -> bool:
    return (
        response.status in (200, 204)
        and response.values("Access-Control-Allow-Origin") == (origin,)
        and "post" in _header_tokens(response, "Access-Control-Allow-Methods")
        and "content-type" in _header_tokens(response, "Access-Control-Allow-Headers")
        and not response.values("Access-Control-Allow-Credentials")
        and not response.values("Set-Cookie")
    )


def _session_matches(document: Mapping[str, Any], profile: Mapping[str, Any]) -> bool:
    identity = _mapping(
        _mapping(profile.get("spec"), "customer-oidc-qualification.profile.invalid").get("identity"),
        "customer-oidc-qualification.profile.invalid",
    )
    return document == {
        "apiVersion": API_VERSION,
        "kind": "SessionContext",
        "metadata": {
            "tenantId": identity["tenantId"],
            "actorId": identity["actorId"],
        },
        "spec": {"roles": identity["roles"]},
    }


def _runtime_subject(
    document: Mapping[str, Any], *, tenant_id: str, revision: str, image_digest: str
) -> dict[str, str]:
    if document.get("apiVersion") != API_VERSION or document.get("kind") != "RuntimeVersionReport":
        _fail("customer-oidc-qualification.runtime.invalid")
    metadata = _mapping(document.get("metadata"), "customer-oidc-qualification.runtime.invalid")
    spec = _mapping(document.get("spec"), "customer-oidc-qualification.runtime.invalid")
    application = _mapping(spec.get("application"), "customer-oidc-qualification.runtime.invalid")
    contracts = _mapping(spec.get("contracts"), "customer-oidc-qualification.runtime.invalid")
    storage = _mapping(spec.get("storage"), "customer-oidc-qualification.runtime.invalid")
    build = _mapping(spec.get("build"), "customer-oidc-qualification.runtime.invalid")
    deployment = _mapping(spec.get("deployment"), "customer-oidc-qualification.runtime.invalid")
    if (
        metadata.get("tenantId") != tenant_id
        or contracts != {"apiVersion": API_VERSION}
        or build != {"mode": "release", "revision": revision}
        or deployment.get("imageDigest") != image_digest
        or set(deployment) != {"helmChartVersion", "imageDigest"}
    ):
        _fail("customer-oidc-qualification.runtime.crossed")
    subject = {
        "applicationVersion": application.get("version"),
        "chartVersion": deployment.get("helmChartVersion"),
        "contractsApiVersion": API_VERSION,
        "requiredMigration": storage.get("requiredMigration"),
        "sourceRevision": revision,
        "imageDigest": image_digest,
    }
    if not all(isinstance(value, str) for value in subject.values()):
        _fail("customer-oidc-qualification.runtime.invalid")
    return subject  # type: ignore[return-value]


def _tampered_token(token: str) -> str:
    segments = token.split(".")
    if len(segments) != 3 or not segments[2]:
        _fail("customer-oidc-qualification.credential.invalid")
    header, payload, signature = segments
    first = "A" if signature[0] != "A" else "B"
    return f"{header}.{payload}.{first}{signature[1:]}"


def _check(identifier: str, passed: bool, code: str) -> dict[str, str]:
    if passed:
        return {"id": identifier, "status": "passed"}
    return {"id": identifier, "status": "failed", "errorCode": code}


def _report_identifier(metadata: Mapping[str, object], spec: Mapping[str, object]) -> str:
    return "coq_" + hashlib.sha256(
        _canonical({"metadata": metadata, "spec": spec})
    ).hexdigest()[:32]


def _has_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_RETAINED_KEYS or _has_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(child) for child in value)
    return False


def _expected_summary(checks: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    failed = sum(item.get("status") == "failed" for item in checks)
    status = "qualified" if failed == 0 else "not-qualified"
    return {
        "totalChecks": len(CHECK_IDS),
        "passedChecks": len(CHECK_IDS) - failed,
        "failedChecks": failed,
        "overallStatus": status,
    }


def build_report(
    *,
    revision: str,
    source_dirty: bool,
    profile: Mapping[str, Any],
    api_base_url: str,
    issuer_metadata: Mapping[str, Any],
    subject: Mapping[str, str],
    started_at: datetime,
    completed_at: datetime,
    discovery_response_bytes: int,
    jwks_response_bytes: int,
    jwks_key_count: int,
    token_lifetime_seconds: int,
    token_remaining_seconds: int,
    observations: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    validate_profile(profile)
    if source_dirty:
        _fail("customer-oidc-qualification.source.dirty")
    profile_spec = _mapping(profile.get("spec"), "customer-oidc-qualification.profile.invalid")
    objective = _mapping(profile_spec.get("objective"), "customer-oidc-qualification.profile.invalid")
    canonical_api = _strict_https_url(
        api_base_url, "customer-oidc-qualification.target.invalid", root_only=True
    )
    browser = _mapping(
        _mapping(
            _mapping(
                profile.get("spec"),
                "customer-oidc-qualification.profile.invalid",
            ).get("oidc"),
            "customer-oidc-qualification.profile.invalid",
        ).get("browser"),
        "customer-oidc-qualification.profile.invalid",
    )
    if _console_origin(str(browser["redirectUri"])) != canonical_api:
        _fail("customer-oidc-qualification.target.crossed")
    facts = {identifier: True for identifier in CHECK_IDS}
    facts.update(observations or {})
    facts["profile-binding"] = True
    facts["source-binding"] = REVISION.fullmatch(revision) is not None and not source_dirty
    facts["api-target-binding"] = True
    facts["minimized-output"] = True
    checks = [
        _check(
            identifier,
            facts.get(identifier) is True,
            f"customer-oidc-qualification.{identifier}.failed",
        )
        for identifier in CHECK_IDS
    ]
    summary = _expected_summary(checks)
    completed = _timestamp(completed_at)
    spec: dict[str, Any] = {
        "status": summary["overallStatus"],
        "qualification": QUALIFICATION,
        "subject": dict(subject),
        "bindings": {
            "apiTargetBindingDigest": _digest_value(canonical_api),
            "profileDigest": _digest_value(profile),
            "issuerMetadataDigest": _digest_value(issuer_metadata),
            "runtimeBindingDigest": _digest_value(subject),
        },
        "objective": dict(objective),
        "protocol": {
            "discoveryTransport": "ca-verified-https-no-redirect",
            "apiTransport": "ca-verified-https-no-redirect",
            "flow": "authorization-code",
            "pkceMethod": "S256",
            "tokenEndpointAuthentication": "none",
            "accessTokenAlgorithm": "RS256",
            "apiAuthentication": "bearer",
        },
        "measurements": {
            "startedAt": _timestamp(started_at),
            "completedAt": completed,
            "discoveryResponseBytes": discovery_response_bytes,
            "jwksResponseBytes": jwks_response_bytes,
            "jwksKeyCount": jwks_key_count,
            "tokenLifetimeSeconds": token_lifetime_seconds,
            "tokenRemainingSeconds": token_remaining_seconds,
        },
        "checks": checks,
        "limitations": list(LIMITATIONS),
        "summary": summary,
    }
    metadata_without_id: dict[str, object] = {
        "generatedAt": completed,
        "sourceRevision": revision,
        "sourceDirty": False,
    }
    report = {
        "apiVersion": API_VERSION,
        "kind": REPORT_KIND,
        "metadata": {
            "id": _report_identifier(metadata_without_id, spec),
            **metadata_without_id,
        },
        "spec": spec,
    }
    validate_report_document(report)
    return report


def validate_report_document(report: Mapping[str, Any]) -> None:
    _validate_schema(
        report,
        REPORT_SCHEMA,
        "customer-oidc-qualification.report.schema-invalid",
    )
    metadata = _mapping(
        report.get("metadata"), "customer-oidc-qualification.report.invalid"
    )
    spec = _mapping(report.get("spec"), "customer-oidc-qualification.report.invalid")
    subject = _mapping(spec.get("subject"), "customer-oidc-qualification.report.invalid")
    bindings = _mapping(spec.get("bindings"), "customer-oidc-qualification.report.invalid")
    measurements = _mapping(
        spec.get("measurements"), "customer-oidc-qualification.report.invalid"
    )
    checks = spec.get("checks")
    if not isinstance(checks, list) or [
        item.get("id") for item in checks if isinstance(item, Mapping)
    ] != list(CHECK_IDS):
        _fail("customer-oidc-qualification.report.checks-invalid")
    for identifier, item in zip(CHECK_IDS, checks):
        current = _mapping(
            item, "customer-oidc-qualification.report.checks-invalid"
        )
        status = current.get("status")
        expected = _check(
            identifier,
            status == "passed",
            f"customer-oidc-qualification.{identifier}.failed",
        )
        if current != expected:
            _fail("customer-oidc-qualification.report.checks-invalid")
    started = _parse_timestamp(
        measurements.get("startedAt"), "customer-oidc-qualification.report.time-invalid"
    )
    completed = _parse_timestamp(
        measurements.get("completedAt"), "customer-oidc-qualification.report.time-invalid"
    )
    if (
        completed < started
        or metadata.get("generatedAt") != measurements.get("completedAt")
        or metadata.get("sourceRevision") != subject.get("sourceRevision")
        or bindings.get("runtimeBindingDigest") != _digest_value(subject)
        or spec.get("limitations") != list(LIMITATIONS)
        or _has_forbidden_key(report)
    ):
        _fail("customer-oidc-qualification.report.invalid")
    objective = _mapping(spec.get("objective"), "customer-oidc-qualification.report.invalid")
    maximum = _integer(
        objective.get("maximumResponseBytes"),
        4096,
        262144,
        "customer-oidc-qualification.report.objective-invalid",
    )
    for key in ("discoveryResponseBytes", "jwksResponseBytes"):
        _integer(
            measurements.get(key),
            2,
            maximum,
            "customer-oidc-qualification.report.measurements-invalid",
        )
    lifetime = _integer(
        measurements.get("tokenLifetimeSeconds"),
        1,
        86400,
        "customer-oidc-qualification.report.measurements-invalid",
    )
    remaining = _integer(
        measurements.get("tokenRemainingSeconds"),
        0,
        86400,
        "customer-oidc-qualification.report.measurements-invalid",
    )
    if (
        lifetime > objective.get("maximumTokenLifetimeSeconds")
        or remaining < objective.get("minimumRemainingTokenSeconds")
    ):
        _fail("customer-oidc-qualification.report.measurements-invalid")
    summary = _expected_summary(checks)
    if spec.get("summary") != summary or spec.get("status") != summary["overallStatus"]:
        _fail("customer-oidc-qualification.report.summary-invalid")
    metadata_without_id = dict(metadata)
    identifier = metadata_without_id.pop("id", None)
    if (
        not isinstance(identifier, str)
        or REPORT_ID.fullmatch(identifier) is None
        or identifier != _report_identifier(metadata_without_id, spec)
    ):
        _fail("customer-oidc-qualification.report.id-invalid")


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        _fail("customer-oidc-qualification.output.invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except OSError:
        _fail("customer-oidc-qualification.output.invalid")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def qualify(
    *,
    profile: Mapping[str, Any],
    api_base_url: str,
    access_token: str,
    api_ca_file: Path,
    issuer_ca_file: Path,
    image_digest: str,
    allow_identity_observation: bool,
    client: HttpsClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not allow_identity_observation:
        _fail("customer-oidc-qualification.enable.required")
    if DIGEST.fullmatch(image_digest) is None:
        _fail("customer-oidc-qualification.image.invalid")
    validate_profile(profile)
    canonical_api = _strict_https_url(
        api_base_url, "customer-oidc-qualification.target.invalid", root_only=True
    )
    spec = _mapping(profile.get("spec"), "customer-oidc-qualification.profile.invalid")
    identity = _mapping(spec.get("identity"), "customer-oidc-qualification.profile.invalid")
    oidc = _mapping(spec.get("oidc"), "customer-oidc-qualification.profile.invalid")
    browser = _mapping(oidc.get("browser"), "customer-oidc-qualification.profile.invalid")
    objective = _mapping(spec.get("objective"), "customer-oidc-qualification.profile.invalid")
    if _console_origin(str(browser["redirectUri"])) != canonical_api:
        _fail("customer-oidc-qualification.target.crossed")
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-oidc-qualification.source.dirty")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        _fail("customer-oidc-qualification.time.invalid")
    instant = current.astimezone(timezone.utc)
    lifetime, remaining, key_id = _token_measurements(access_token, profile, instant)
    transport = client or HttpsClient(
        api_context=_ca_context(api_ca_file, "customer-oidc-qualification.api-ca.invalid"),
        issuer_context=_ca_context(
            issuer_ca_file, "customer-oidc-qualification.issuer-ca.invalid"
        ),
        timeout_milliseconds=int(objective["requestTimeoutMilliseconds"]),
        maximum_response_bytes=int(objective["maximumResponseBytes"]),
    )

    discovery_response = transport.request(str(oidc["discoveryUrl"]), issuer=True)
    discovery = _json_response(
        discovery_response, "customer-oidc-qualification.discovery.invalid"
    )
    issuer_bound = (
        discovery.get("issuer") == oidc["issuer"]
        and discovery.get("authorization_endpoint") == browser["authorizationEndpoint"]
        and discovery.get("token_endpoint") == browser["tokenEndpoint"]
    )
    jwks_bound = discovery.get("jwks_uri") == oidc["jwksUrl"]
    code_supported = _advertises(discovery.get("response_types_supported"), "code")
    s256_supported = _advertises(
        discovery.get("code_challenge_methods_supported"), "S256"
    )
    public_supported = _advertises(
        discovery.get("token_endpoint_auth_methods_supported"), "none"
    )

    jwks_response = transport.request(str(oidc["jwksUrl"]), issuer=True)
    jwks = _json_response(jwks_response, "customer-oidc-qualification.jwks.invalid")
    keys = jwks.get("keys")
    if not isinstance(keys, list) or not 1 <= len(keys) <= 100 or any(
        not isinstance(item, Mapping) for item in keys
    ):
        _fail("customer-oidc-qualification.jwks.invalid")
    matching_keys = [
        item.get("kid") == key_id
        and item.get("kty") == "RSA"
        and item.get("alg") in (None, "RS256")
        and item.get("use") in (None, "sig")
        and isinstance(item.get("n"), str)
        and JWT_SEGMENT.fullmatch(item.get("n", "")) is not None
        and isinstance(item.get("e"), str)
        and JWT_SEGMENT.fullmatch(item.get("e", "")) is not None
        and (
            "key_ops" not in item
            or (
                isinstance(item.get("key_ops"), list)
                and "verify" in item.get("key_ops", [])
            )
        )
        for item in keys
        if isinstance(item, Mapping)
    ]
    matching_key = sum(matching_keys) == 1

    console_response = transport.request(
        canonical_api + "/v1/authentication/console", issuer=False
    )
    console = _json_response(
        console_response, "customer-oidc-qualification.console.invalid"
    )
    console_bound = console == _expected_console_document(profile)

    origin = _console_origin(str(browser["redirectUri"]))
    preflight_headers = {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }
    preflight = transport.request(
        str(browser["tokenEndpoint"]),
        issuer=True,
        method="OPTIONS",
        headers=preflight_headers,
    )
    other_origin = "https://iip-qualification.invalid"
    denied_preflight = transport.request(
        str(browser["tokenEndpoint"]),
        issuer=True,
        method="OPTIONS",
        headers={**preflight_headers, "Origin": other_origin},
    )
    cors_exact = _cors_exact(preflight, origin)
    cors_denied = other_origin not in denied_preflight.values(
        "Access-Control-Allow-Origin"
    ) and "*" not in denied_preflight.values("Access-Control-Allow-Origin")

    bearer_headers = {"Authorization": f"Bearer {access_token}"}
    session_response = transport.request(
        canonical_api + "/v1/session", issuer=False, headers=bearer_headers
    )
    session = _json_response(
        session_response, "customer-oidc-qualification.session.invalid"
    )
    session_matches = _session_matches(session, profile)
    runtime_response = transport.request(
        canonical_api + "/v1/system/version", issuer=False, headers=bearer_headers
    )
    runtime = _json_response(
        runtime_response, "customer-oidc-qualification.runtime.invalid"
    )
    subject = _runtime_subject(
        runtime,
        tenant_id=str(identity["tenantId"]),
        revision=revision,
        image_digest=image_digest,
    )
    tampered_response = transport.request(
        canonical_api + "/v1/session",
        issuer=False,
        headers={"Authorization": f"Bearer {_tampered_token(access_token)}"},
    )
    tampered_denied = tampered_response.status == 401

    observations = {
        "ca-verified-discovery": True,
        "issuer-metadata-binding": issuer_bound,
        "jwks-metadata-binding": jwks_bound and matching_key,
        "authorization-code-supported": code_supported,
        "s256-supported": s256_supported,
        "public-client-supported": public_supported,
        "console-profile-binding": console_bound,
        "token-cors-exact-origin": cors_exact,
        "token-cors-other-origin-denied": cors_denied,
        "access-token-claims-binding": True,
        "access-token-api-authentication": session_matches,
        "tampered-token-denial": tampered_denied,
        "runtime-release-binding": True,
    }
    completed = datetime.now(timezone.utc) if now is None else instant
    report = build_report(
        revision=revision,
        source_dirty=False,
        profile=profile,
        api_base_url=canonical_api,
        issuer_metadata=discovery,
        subject=subject,
        started_at=instant,
        completed_at=completed,
        discovery_response_bytes=len(discovery_response.body),
        jwks_response_bytes=len(jwks_response.body),
        jwks_key_count=len(keys),
        token_lifetime_seconds=lifetime,
        token_remaining_seconds=remaining,
        observations=observations,
    )
    return report


def verify_report(
    *,
    report_path: Path,
    profile_path: Path,
    api_base_url: str,
    image_digest: str,
    require_qualified: bool = False,
) -> Mapping[str, Any]:
    candidate = report_path.expanduser()
    try:
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or candidate.stat().st_size > 1024 * 1024
        ):
            _fail("customer-oidc-qualification.report.unreadable")
        payload = candidate.read_bytes()
        report = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-oidc-qualification.report.unreadable")
    if not isinstance(report, Mapping):
        _fail("customer-oidc-qualification.report.unreadable")
    validate_report_document(report)
    profile = load_profile(profile_path)
    revision, dirty = _source_identity()
    metadata = _mapping(report.get("metadata"), "customer-oidc-qualification.report.invalid")
    spec = _mapping(report.get("spec"), "customer-oidc-qualification.report.invalid")
    subject = _mapping(spec.get("subject"), "customer-oidc-qualification.report.invalid")
    bindings = _mapping(spec.get("bindings"), "customer-oidc-qualification.report.invalid")
    canonical_api = _strict_https_url(
        api_base_url, "customer-oidc-qualification.target.invalid", root_only=True
    )
    profile_spec = _mapping(
        profile.get("spec"), "customer-oidc-qualification.profile.invalid"
    )
    profile_oidc = _mapping(
        profile_spec.get("oidc"), "customer-oidc-qualification.profile.invalid"
    )
    profile_browser = _mapping(
        profile_oidc.get("browser"), "customer-oidc-qualification.profile.invalid"
    )
    if (
        dirty
        or metadata.get("sourceRevision") != revision
        or subject.get("sourceRevision") != revision
        or subject.get("imageDigest") != image_digest
        or bindings.get("apiTargetBindingDigest") != _digest_value(canonical_api)
        or bindings.get("profileDigest") != _digest_value(profile)
        or _console_origin(str(profile_browser["redirectUri"])) != canonical_api
    ):
        _fail("customer-oidc-qualification.report.crossed")
    if require_qualified and spec.get("status") != "qualified":
        _fail("customer-oidc-qualification.report.not-qualified")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    qualify_parser = subparsers.add_parser("qualify")
    qualify_parser.add_argument("--profile", type=Path, required=True)
    qualify_parser.add_argument("--api-base-url", required=True)
    qualify_parser.add_argument("--access-token-file", type=Path, required=True)
    qualify_parser.add_argument("--api-ca-file", type=Path, required=True)
    qualify_parser.add_argument("--issuer-ca-file", type=Path, required=True)
    qualify_parser.add_argument("--image-digest", required=True)
    qualify_parser.add_argument("--output", type=Path, required=True)
    qualify_parser.add_argument("--allow-identity-observation", action="store_true")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--report", type=Path, required=True)
    verify_parser.add_argument("--profile", type=Path, required=True)
    verify_parser.add_argument("--api-base-url", required=True)
    verify_parser.add_argument("--image-digest", required=True)
    verify_parser.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "qualify":
            profile = load_profile(arguments.profile)
            token = load_access_token(arguments.access_token_file)
            report = qualify(
                profile=profile,
                api_base_url=arguments.api_base_url,
                access_token=token,
                api_ca_file=arguments.api_ca_file,
                issuer_ca_file=arguments.issuer_ca_file,
                image_digest=arguments.image_digest,
                allow_identity_observation=arguments.allow_identity_observation,
            )
            _write_report(arguments.output, report)
            print(
                f"customer OIDC qualification {report['spec']['status']}: "
                f"{arguments.output}"
            )
            return 0 if report["spec"]["status"] == "qualified" else 1
        else:
            verify_report(
                report_path=arguments.report,
                profile_path=arguments.profile,
                api_base_url=arguments.api_base_url,
                image_digest=arguments.image_digest,
                require_qualified=arguments.require_qualified,
            )
            print("customer OIDC qualification report verified")
    except CustomerOidcQualificationError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
