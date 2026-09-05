"""Production-configurable policy adapter behind the application PDP port."""

from __future__ import annotations

import hashlib
import json
import re
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from iip.application.ports import (
    ActorContext,
    PolicyConfigurationError,
    PolicyDecision,
)


_REASON = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+")
_SNAPSHOT = re.compile(r"policy://[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}/snapshots/[a-zA-Z0-9][a-zA-Z0-9._:/@-]{0,511}")
_ACTION = re.compile(r"[a-z][a-z0-9-]*:[a-z][a-z0-9-]*")
_ACTOR_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/@-]{0,255}")
_TENANT_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}")
_ROLE = re.compile(r"[a-z][a-z0-9._:-]{0,63}")
_CONFIG_KEYS = {
    "endpoint",
    "caBundlePath",
    "bearerTokenPath",
    "timeoutSeconds",
    "maxResponseBytes",
}


@dataclass(frozen=True)
class ExternalPolicyConfiguration:
    endpoint: str
    ca_bundle_path: str | None = None
    bearer_token_path: str | None = None
    timeout_seconds: int = 5
    max_response_bytes: int = 65_536

    @classmethod
    def from_json(cls, raw: str) -> "ExternalPolicyConfiguration":
        if not isinstance(raw, str) or not 1 <= len(raw) <= 65_536:
            raise PolicyConfigurationError("policy.configuration.invalid")
        try:
            payload = json.loads(raw)
            if (
                not isinstance(payload, dict)
                or "endpoint" not in payload
                or set(payload).difference(_CONFIG_KEYS)
            ):
                raise ValueError
            configuration = cls(
                endpoint=payload["endpoint"],
                ca_bundle_path=payload.get("caBundlePath"),
                bearer_token_path=payload.get("bearerTokenPath"),
                timeout_seconds=payload.get("timeoutSeconds", 5),
                max_response_bytes=payload.get("maxResponseBytes", 65_536),
            )
            configuration.validate()
            return configuration
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise PolicyConfigurationError("policy.configuration.invalid") from None

    def validate(self) -> None:
        endpoint = urlsplit(self.endpoint) if isinstance(self.endpoint, str) else None
        if (
            endpoint is None
            or endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.fragment
            or any(
                value is not None
                and (
                    not isinstance(value, str)
                    or not value.startswith("/")
                    or len(value) > 4096
                )
                for value in (self.ca_bundle_path, self.bearer_token_path)
            )
            or isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or not 1 <= self.timeout_seconds <= 30
            or isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or not 1024 <= self.max_response_bytes <= 1_048_576
        ):
            raise PolicyConfigurationError("policy.configuration.invalid")


class PolicyTransport(Protocol):
    def post(
        self,
        endpoint: str,
        body: bytes,
        headers: Mapping[str, str],
        context: ssl.SSLContext,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> bytes:
        """Post one bounded policy input to the configured TLS endpoint."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        del request, file_pointer, code, message, headers, new_url
        return None


class HttpsPolicyTransport:
    def post(
        self,
        endpoint: str,
        body: bytes,
        headers: Mapping[str, str],
        context: ssl.SSLContext,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> bytes:
        opener = build_opener(HTTPSHandler(context=context), _NoRedirect())
        request = Request(
            endpoint,
            data=body,
            headers=dict(headers),
            method="POST",
        )
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                content_type = response.headers.get_content_type()
                result = response.read(max_response_bytes + 1)
                if (
                    response.status != 200
                    or content_type != "application/json"
                    or len(result) > max_response_bytes
                ):
                    raise OSError
                return result
        except (HTTPError, URLError, OSError, TimeoutError):
            raise OSError("policy transport unavailable") from None


class ExternalHttpPolicyDecisionPoint:
    """Send exact tenant-scoped inputs to an external PDP and fail closed."""

    MAX_REQUEST_BYTES = 262_144

    def __init__(
        self,
        configuration: ExternalPolicyConfiguration,
        transport: PolicyTransport | None = None,
    ) -> None:
        configuration.validate()
        self._configuration = configuration
        try:
            self._ssl_context = ssl.create_default_context(
                cafile=configuration.ca_bundle_path
            )
        except (OSError, ssl.SSLError):
            raise PolicyConfigurationError("policy.configuration.invalid") from None
        self._transport = transport or HttpsPolicyTransport()

    @classmethod
    def from_json(cls, raw: str) -> "ExternalHttpPolicyDecisionPoint":
        return cls(ExternalPolicyConfiguration.from_json(raw))

    def decide(
        self,
        actor: ActorContext,
        action: str,
        resource: Mapping[str, object],
    ) -> PolicyDecision:
        unavailable = PolicyDecision(
            False,
            "policy.unavailable",
            f"policy://{actor.tenant_id}/snapshots/unavailable",
        )
        if (
            not isinstance(action, str)
            or len(action) > 128
            or _ACTION.fullmatch(action) is None
            or not isinstance(actor.actor_id, str)
            or actor.actor_id == "anonymous"
            or _ACTOR_ID.fullmatch(actor.actor_id) is None
            or not isinstance(actor.tenant_id, str)
            or _TENANT_ID.fullmatch(actor.tenant_id) is None
            or not isinstance(actor.roles, tuple)
            or len(actor.roles) > 64
            or len(set(actor.roles)) != len(actor.roles)
            or any(
                not isinstance(role, str) or _ROLE.fullmatch(role) is None
                for role in actor.roles
            )
            or resource.get("tenantId") != actor.tenant_id
        ):
            return PolicyDecision(
                False,
                "policy.input-invalid",
                f"policy://{actor.tenant_id}/snapshots/input-invalid",
            )
        request_document = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PolicyDecisionRequest",
            "metadata": {
                "tenantId": actor.tenant_id,
                "actorId": actor.actor_id,
            },
            "spec": {
                "action": action,
                "roles": list(actor.roles),
                "resource": dict(resource),
            },
        }
        try:
            input_digest = _canonical_digest(request_document)
            body = json.dumps(
                {"input": request_document},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(body) > self.MAX_REQUEST_BYTES:
                return PolicyDecision(
                    False,
                    "policy.input-too-large",
                    f"policy://{actor.tenant_id}/snapshots/input-too-large",
                )
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "iip-policy-adapter/0.75.0",
            }
            token = self._bearer_token()
            if token is not None:
                headers["Authorization"] = f"Bearer {token}"
            raw = self._transport.post(
                self._configuration.endpoint,
                body,
                headers,
                self._ssl_context,
                self._configuration.timeout_seconds,
                self._configuration.max_response_bytes,
            )
            response = json.loads(raw)
            result = response.get("result") if isinstance(response, dict) else None
            metadata = result.get("metadata") if isinstance(result, dict) else None
            spec = result.get("spec") if isinstance(result, dict) else None
            if (
                not isinstance(result, dict)
                or set(result) != {"apiVersion", "kind", "metadata", "spec"}
                or result.get("apiVersion") != "iip.platform/v1alpha1"
                or result.get("kind") != "PolicyDecision"
                or not isinstance(metadata, dict)
                or set(metadata) != {"tenantId"}
                or metadata.get("tenantId") != actor.tenant_id
                or not isinstance(spec, dict)
                or set(spec) != {
                    "allowed",
                    "inputDigest",
                    "policySnapshotRef",
                    "reasonCode",
                }
                or spec.get("inputDigest") != input_digest
            ):
                return unavailable
            allowed = spec["allowed"]
            reason = spec["reasonCode"]
            snapshot = spec["policySnapshotRef"]
            if (
                not isinstance(allowed, bool)
                or not isinstance(reason, str)
                or len(reason) > 128
                or _REASON.fullmatch(reason) is None
                or not isinstance(snapshot, str)
                or len(snapshot) > 2048
                or _SNAPSHOT.fullmatch(snapshot) is None
                or not snapshot.startswith(f"policy://{actor.tenant_id}/snapshots/")
            ):
                return unavailable
            return PolicyDecision(allowed, reason, snapshot)
        except Exception:
            return unavailable

    def _bearer_token(self) -> str | None:
        path = self._configuration.bearer_token_path
        if path is None:
            return None
        try:
            token = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            raise OSError("policy token unavailable") from None
        if not re.fullmatch(r"[A-Za-z0-9._~+/-]{32,8192}=*", token):
            raise OSError("policy token unavailable")
        return token


def _canonical_digest(document: object) -> str:
    encoded = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
