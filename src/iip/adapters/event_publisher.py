"""Tenant-bound structured-log and HTTPS CloudEvents publishers."""

from __future__ import annotations

import json
import re
import ssl
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from iip.application.ports import (
    EventPublicationError,
    EventPublisherConfigurationError,
)
from iip.domain.models import PlatformEvent


_TENANT_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}")
_TOKEN = re.compile(r"[A-Za-z0-9._~+/-]{32,8192}=*")
_CONFIG_KEYS = {
    "endpoint",
    "tenantIds",
    "bearerTokenPath",
    "caBundlePath",
    "timeoutSeconds",
    "maxResponseBytes",
}


def _validated_tenants(tenant_ids: tuple[str, ...]) -> frozenset[str]:
    if (
        not isinstance(tenant_ids, tuple)
        or not 1 <= len(tenant_ids) <= 1000
        or any(
            not isinstance(tenant_id, str)
            or _TENANT_ID.fullmatch(tenant_id) is None
            or tenant_id == "*"
            for tenant_id in tenant_ids
        )
        or len(set(tenant_ids)) != len(tenant_ids)
    ):
        raise EventPublisherConfigurationError("event.publisher.configuration.invalid")
    return frozenset(tenant_ids)


class StructuredLogEventPublisher:
    """Development-only sink that writes bounded CloudEvents JSON to stdout."""

    def __init__(
        self,
        tenant_ids: tuple[str, ...],
        stream: TextIO | None = None,
    ) -> None:
        self._tenant_ids = _validated_tenants(tenant_ids)
        self._stream = stream or sys.stdout

    def publish(self, event: PlatformEvent) -> None:
        if event.tenant_id not in self._tenant_ids:
            raise EventPublicationError("event.publisher.tenant-denied")
        encoded = json.dumps(
            {"event": "outbox.event.published", "cloudEvent": event.to_dict()},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(encoded.encode("utf-8")) > 1_048_576:
            raise EventPublicationError("event.publisher.input-too-large")
        try:
            self._stream.write(encoded + "\n")
            self._stream.flush()
        except (OSError, ValueError):
            raise EventPublicationError("event.publisher.unavailable") from None


@dataclass(frozen=True)
class HttpsEventPublisherConfiguration:
    endpoint: str
    tenant_ids: tuple[str, ...]
    bearer_token_path: str
    ca_bundle_path: str | None = None
    timeout_seconds: int = 5
    max_response_bytes: int = 4096

    @classmethod
    def from_json(cls, raw: str) -> "HttpsEventPublisherConfiguration":
        if not isinstance(raw, str) or not 1 <= len(raw) <= 65_536:
            raise EventPublisherConfigurationError(
                "event.publisher.configuration.invalid"
            )
        try:
            payload = json.loads(raw)
            if (
                not isinstance(payload, dict)
                or set(payload).difference(_CONFIG_KEYS)
                or not {"endpoint", "tenantIds", "bearerTokenPath"}.issubset(payload)
                or not isinstance(payload["tenantIds"], list)
            ):
                raise ValueError
            configuration = cls(
                endpoint=payload["endpoint"],
                tenant_ids=tuple(payload["tenantIds"]),
                bearer_token_path=payload["bearerTokenPath"],
                ca_bundle_path=payload.get("caBundlePath"),
                timeout_seconds=payload.get("timeoutSeconds", 5),
                max_response_bytes=payload.get("maxResponseBytes", 4096),
            )
            configuration.validate()
            return configuration
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise EventPublisherConfigurationError(
                "event.publisher.configuration.invalid"
            ) from None

    def validate(self) -> None:
        endpoint = urlsplit(self.endpoint) if isinstance(self.endpoint, str) else None
        if (
            endpoint is None
            or endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username is not None
            or endpoint.password is not None
            or bool(endpoint.query)
            or bool(endpoint.fragment)
            or not isinstance(self.bearer_token_path, str)
            or not self.bearer_token_path.startswith("/")
            or len(self.bearer_token_path) > 4096
            or (
                self.ca_bundle_path is not None
                and (
                    not isinstance(self.ca_bundle_path, str)
                    or not self.ca_bundle_path.startswith("/")
                    or len(self.ca_bundle_path) > 4096
                )
            )
            or isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or not 1 <= self.timeout_seconds <= 30
            or isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or not 0 <= self.max_response_bytes <= 65_536
        ):
            raise EventPublisherConfigurationError(
                "event.publisher.configuration.invalid"
            )
        _validated_tenants(self.tenant_ids)


class EventPublisherTransport(Protocol):
    def post(
        self,
        endpoint: str,
        body: bytes,
        headers: Mapping[str, str],
        context: ssl.SSLContext,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> None:
        """Post one bounded structured CloudEvent to its configured endpoint."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        del request, file_pointer, code, message, headers, new_url
        return None


class HttpsEventPublisherTransport:
    def post(
        self,
        endpoint: str,
        body: bytes,
        headers: Mapping[str, str],
        context: ssl.SSLContext,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> None:
        opener = build_opener(HTTPSHandler(context=context), _NoRedirect())
        request = Request(endpoint, data=body, headers=dict(headers), method="POST")
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                result = response.read(max_response_bytes + 1)
                if not 200 <= response.status < 300 or len(result) > max_response_bytes:
                    raise OSError
        except (HTTPError, URLError, OSError, TimeoutError):
            raise OSError("event publisher transport unavailable") from None


class HttpsCloudEventsPublisher:
    """Publish exact-tenant structured CloudEvents over bounded authenticated HTTPS."""

    MAX_REQUEST_BYTES = 1_048_576

    def __init__(
        self,
        configuration: HttpsEventPublisherConfiguration,
        transport: EventPublisherTransport | None = None,
    ) -> None:
        configuration.validate()
        self._configuration = configuration
        self._tenant_ids = frozenset(configuration.tenant_ids)
        try:
            self._ssl_context = ssl.create_default_context(
                cafile=configuration.ca_bundle_path
            )
        except (OSError, ssl.SSLError):
            raise EventPublisherConfigurationError(
                "event.publisher.configuration.invalid"
            ) from None
        self._transport = transport or HttpsEventPublisherTransport()

    @classmethod
    def from_json(cls, raw: str) -> "HttpsCloudEventsPublisher":
        return cls(HttpsEventPublisherConfiguration.from_json(raw))

    def publish(self, event: PlatformEvent) -> None:
        if event.tenant_id not in self._tenant_ids:
            raise EventPublicationError("event.publisher.tenant-denied")
        body = json.dumps(
            event.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(body) > self.MAX_REQUEST_BYTES:
            raise EventPublicationError("event.publisher.input-too-large")
        try:
            token = Path(self._configuration.bearer_token_path).read_text(
                encoding="utf-8"
            ).strip()
            if _TOKEN.fullmatch(token) is None:
                raise OSError
            self._transport.post(
                self._configuration.endpoint,
                body,
                {
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/cloudevents+json",
                    "Idempotency-Key": event.event_id,
                    "User-Agent": "iip-event-publisher/0.68.0",
                },
                self._ssl_context,
                self._configuration.timeout_seconds,
                self._configuration.max_response_bytes,
            )
        except (OSError, UnicodeError):
            raise EventPublicationError("event.publisher.unavailable") from None
