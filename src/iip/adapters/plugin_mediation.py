"""Protected binding and direct HTTPS adapters for plugin read mediation."""

from __future__ import annotations

import json
import re
import ssl
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from iip.application.plugin_mediation import PluginMediationGatewayError
from iip.application.ports import (
    ActorContext,
    CredentialBroker,
    CredentialLeaseRequest,
    PluginMediationBinding,
)


_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_PLUGIN = re.compile(r"[a-z][a-z0-9-]{2,63}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?")
_GRANT = re.compile(r"pmg_[a-f0-9]{32}")
_INTEGRATION = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_PROVIDER = re.compile(r"[a-z][a-z0-9._-]{1,63}")
_CREDENTIAL_NAME = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_CREDENTIAL_REF = re.compile(r"credential://[A-Za-z0-9._~:/-]{1,2020}")
_DESTINATION = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?:[1-9][0-9]{0,4}"
)
_QUERY_KEY = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}")
_SCOPE = re.compile(
    r"[a-z][a-z0-9._/-]{0,63}(?::[a-z][a-z0-9._/-]{0,63}){1,2}"
)
_TEMPLATE_SEGMENT = re.compile(
    r"(?:[A-Za-z0-9._~-]+|\{[a-z][A-Za-z0-9]{0,31}\})"
)


class NoPluginMediationRedirectHandler(HTTPRedirectHandler):
    """Prevent provider credentials from following redirects."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None


class PluginMediationHttpTransport(Protocol):
    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        ca_bundle_path: str | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        """Perform one direct TLS-verifying JSON GET without redirects."""


class UrllibPluginMediationHttpTransport:
    """Standard-library bounded HTTPS provider transport."""

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        ca_bundle_path: str | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        try:
            context = ssl.create_default_context(cafile=ca_bundle_path)
            opener = build_opener(
                HTTPSHandler(context=context), NoPluginMediationRedirectHandler()
            )
            request = Request(url, headers=dict(headers), method="GET")
            with opener.open(request, timeout=timeout_seconds) as response:
                if response.status != 200:
                    raise PluginMediationGatewayError(
                        "plugin.mediation.provider-unavailable"
                    )
                if response.headers.get_content_type() != "application/json":
                    raise PluginMediationGatewayError(
                        "plugin.mediation.response-invalid"
                    )
                content = response.read(max_response_bytes + 1)
        except PluginMediationGatewayError:
            raise
        except Exception:
            raise PluginMediationGatewayError(
                "plugin.mediation.provider-unavailable"
            ) from None
        if len(content) > max_response_bytes:
            raise PluginMediationGatewayError("plugin.mediation.response-too-large")
        return content


class StaticPluginMediationBindingRegistry:
    """Closed, protected binding registry for runner composition and tests."""

    def __init__(self, bindings: Iterable[PluginMediationBinding]) -> None:
        indexed: dict[tuple[str, str, str, str], PluginMediationBinding] = {}
        for binding in bindings:
            _validate_binding(binding)
            key = (
                binding.tenant_id,
                binding.plugin_id,
                binding.plugin_version,
                binding.grant_id,
            )
            if key in indexed:
                raise ValueError("plugin.mediation.binding-configuration-invalid")
            indexed[key] = binding
        self._bindings = indexed

    def __repr__(self) -> str:
        return f"StaticPluginMediationBindingRegistry(bindings={len(self._bindings)})"

    def resolve_plugin_mediation_binding(
        self,
        actor: ActorContext,
        plugin_id: str,
        plugin_version: str,
        grant_id: str,
    ) -> PluginMediationBinding | None:
        return self._bindings.get(
            (actor.tenant_id, plugin_id, plugin_version, grant_id)
        )


class HttpsJsonPluginMediationGateway:
    """Resolve one short lease and perform one exact provider JSON read."""

    def __init__(
        self,
        credential_broker: CredentialBroker,
        *,
        transport: PluginMediationHttpTransport | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._credential_broker = credential_broker
        self._transport = transport or UrllibPluginMediationHttpTransport()
        self._now = now

    def __repr__(self) -> str:
        return "HttpsJsonPluginMediationGateway(configuration=<protected>)"

    def fetch_plugin_json(
        self,
        actor: ActorContext,
        binding: PluginMediationBinding,
        *,
        path: str,
        query: Mapping[str, tuple[str, ...]],
        deadline: str,
        max_response_bytes: int,
    ) -> object:
        try:
            _validate_binding(binding)
            if actor.tenant_id != binding.tenant_id:
                raise ValueError
            endpoint = _validated_endpoint(binding.endpoint, binding.destination)
            deadline_time = _timestamp(deadline)
            remaining = (deadline_time - self._now().astimezone(timezone.utc)).total_seconds()
            if remaining <= 0:
                raise PluginMediationGatewayError(
                    "plugin.mediation.deadline-exceeded"
                )
            if (
                not isinstance(max_response_bytes, int)
                or isinstance(max_response_bytes, bool)
                or max_response_bytes != binding.max_response_bytes
            ):
                raise ValueError
            lease = self._credential_broker.resolve(
                CredentialLeaseRequest(
                    tenant_id=actor.tenant_id,
                    actor_id=actor.actor_id,
                    integration_id=binding.integration_id,
                    credential_ref=binding.credential_ref,
                    provider=binding.provider,
                    scopes=binding.scopes,
                    deadline=deadline,
                )
            )
        except PluginMediationGatewayError:
            raise
        except ValueError:
            raise PluginMediationGatewayError("plugin.mediation.request-invalid") from None
        except Exception:
            raise PluginMediationGatewayError(
                "plugin.mediation.credential-unavailable"
            ) from None
        if (
            lease.scheme != "bearer"
            or not isinstance(lease.secret, str)
            or not 16 <= len(lease.secret) <= 16_384
            or not all(33 <= ord(character) <= 126 for character in lease.secret)
        ):
            raise PluginMediationGatewayError(
                "plugin.mediation.credential-unavailable"
            )
        query_pairs = [
            (key, value)
            for key in sorted(query)
            for value in query[key]
        ]
        url = endpoint + path
        if query_pairs:
            url += "?" + urlencode(query_pairs)
        content = self._transport.get(
            url,
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {lease.secret}",
                "User-Agent": "iip-plugin-mediation/1.0",
            },
            ca_bundle_path=binding.ca_bundle_path,
            timeout_seconds=min(30.0, remaining),
            max_response_bytes=max_response_bytes,
        )
        try:
            document = json.loads(
                content.decode("utf-8"), parse_constant=_reject_json_constant
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PluginMediationGatewayError(
                "plugin.mediation.response-invalid"
            ) from None
        if not isinstance(document, (dict, list)):
            raise PluginMediationGatewayError("plugin.mediation.response-invalid")
        return document


def _validated_endpoint(endpoint: object, destination: str) -> str:
    if (
        not isinstance(endpoint, str)
        or not isinstance(destination, str)
        or _DESTINATION.fullmatch(destination) is None
    ):
        raise ValueError
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except (TypeError, ValueError):
        raise ValueError from None
    actual_destination = f"{parsed.hostname}:{port or 443}"
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or actual_destination.lower() != destination.lower()
    ):
        raise ValueError
    return endpoint.rstrip("/")


def _validate_binding(binding: object) -> None:
    if not isinstance(binding, PluginMediationBinding):
        raise ValueError("plugin.mediation.binding-configuration-invalid")
    _validated_endpoint(binding.endpoint, binding.destination)
    if (
        not _matches(_TENANT, binding.tenant_id)
        or not _matches(_PLUGIN, binding.plugin_id)
        or not _matches(_VERSION, binding.plugin_version)
        or not _matches(_GRANT, binding.grant_id)
        or not _matches(_INTEGRATION, binding.integration_id)
        or not _matches(_PROVIDER, binding.provider)
        or not _matches(_CREDENTIAL_NAME, binding.credential_name)
        or not _matches(_CREDENTIAL_REF, binding.credential_ref)
        or not isinstance(binding.path_templates, tuple)
        or not 1 <= len(binding.path_templates) <= 32
        or any(not isinstance(item, str) for item in binding.path_templates)
        or len(binding.path_templates) != len(set(binding.path_templates))
        or any(not _valid_path_template(item) for item in binding.path_templates)
        or not isinstance(binding.query_keys, tuple)
        or len(binding.query_keys) > 32
        or any(not isinstance(item, str) for item in binding.query_keys)
        or len(binding.query_keys) != len(set(binding.query_keys))
        or any(_QUERY_KEY.fullmatch(item) is None for item in binding.query_keys)
        or not isinstance(binding.scopes, tuple)
        or not 1 <= len(binding.scopes) <= 16
        or any(not isinstance(item, str) for item in binding.scopes)
        or len(binding.scopes) != len(set(binding.scopes))
        or any(_SCOPE.fullmatch(item) is None for item in binding.scopes)
        or isinstance(binding.max_requests, bool)
        or not isinstance(binding.max_requests, int)
        or not 1 <= binding.max_requests <= 64
        or isinstance(binding.max_response_bytes, bool)
        or not isinstance(binding.max_response_bytes, int)
        or not 1024 <= binding.max_response_bytes <= 4_194_304
        or binding.ca_bundle_path is not None
        and (
            not isinstance(binding.ca_bundle_path, str)
            or not Path(binding.ca_bundle_path).is_absolute()
        )
    ):
        raise ValueError("plugin.mediation.binding-configuration-invalid")


def _valid_path_template(value: object) -> bool:
    if not isinstance(value, str) or not 2 <= len(value) <= 512 or not value.startswith("/"):
        return False
    segments = value.split("/")[1:]
    return bool(
        segments
        and all(
            segment not in {"", ".", ".."}
            and _TEMPLATE_SEGMENT.fullmatch(segment) is not None
            for segment in segments
        )
    )


def _reject_json_constant(value: str) -> object:
    del value
    raise ValueError


def _matches(pattern: re.Pattern[str], value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(timezone.utc)
