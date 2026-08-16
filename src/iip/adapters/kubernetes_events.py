"""Read-only Kubernetes API adapter for normalized Event evidence."""

from __future__ import annotations

import hashlib
import json
import re
import ssl
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Protocol
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from iip.application.ports import (
    Clock,
    CredentialBroker,
    CredentialLease,
    CredentialLeaseRequest,
    KubernetesEventQuery,
    KubernetesEventRecord,
    KubernetesEventResourceRef,
    KubernetesEventsResult,
)


_TENANT_ID = re.compile(r"[a-zA-Z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_CREDENTIAL_REF = re.compile(r"credential://[A-Za-z0-9._~:/-]{1,2020}")
_SEGMENT = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?")
_KUBERNETES_NAME = re.compile(
    r"[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?"
)
_REASON = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}")
_CONDITION = re.compile(r"[a-z][a-z0-9._/-]{2,127}")
_MAX_CONFIGURATION_BYTES = 1_048_576
_MAX_INTEGRATIONS = 128
_MAX_CREDENTIALS = 256
_MAX_NAMESPACES = 128
_MAX_CONDITIONS = 256


class KubernetesEventsConfigurationError(RuntimeError):
    """Protected Kubernetes Event integration configuration is invalid."""


class KubernetesEventsBackendError(RuntimeError):
    """The Kubernetes API could not safely satisfy a normalized query."""


@dataclass(frozen=True)
class KubernetesResourceBinding:
    """Allowlisted translation from one platform resource type to an API path."""

    resource_type: str
    api_prefix: str
    plural: str
    api_version: str
    kind: str
    namespaced: bool


_RESOURCE_BINDINGS = {
    binding.resource_type: binding
    for binding in (
        KubernetesResourceBinding(
            "core/namespace", "/api/v1", "namespaces", "v1", "Namespace", False
        ),
        KubernetesResourceBinding(
            "core/node", "/api/v1", "nodes", "v1", "Node", False
        ),
        KubernetesResourceBinding(
            "core/pod", "/api/v1", "pods", "v1", "Pod", True
        ),
        KubernetesResourceBinding(
            "core/service", "/api/v1", "services", "v1", "Service", True
        ),
        KubernetesResourceBinding(
            "core/configmap", "/api/v1", "configmaps", "v1", "ConfigMap", True
        ),
        KubernetesResourceBinding(
            "apps/deployment",
            "/apis/apps/v1",
            "deployments",
            "apps/v1",
            "Deployment",
            True,
        ),
        KubernetesResourceBinding(
            "apps/replicaset",
            "/apis/apps/v1",
            "replicasets",
            "apps/v1",
            "ReplicaSet",
            True,
        ),
        KubernetesResourceBinding(
            "apps/statefulset",
            "/apis/apps/v1",
            "statefulsets",
            "apps/v1",
            "StatefulSet",
            True,
        ),
        KubernetesResourceBinding(
            "apps/daemonset",
            "/apis/apps/v1",
            "daemonsets",
            "apps/v1",
            "DaemonSet",
            True,
        ),
        KubernetesResourceBinding(
            "networking.k8s.io/ingress",
            "/apis/networking.k8s.io/v1",
            "ingresses",
            "networking.k8s.io/v1",
            "Ingress",
            True,
        ),
    )
}

_DEFAULT_CONDITIONS = {
    "BackOff": "workload.container-backoff",
    "ErrImagePull": "workload.image-pull-failed",
    "Evicted": "workload.evicted",
    "FailedAttachVolume": "workload.volume-attach-failed",
    "FailedMount": "workload.volume-mount-failed",
    "FailedScheduling": "workload.scheduling-failed",
    "ImagePullBackOff": "workload.image-pull-failed",
    "ProgressDeadlineExceeded": "workload.progress-deadline-exceeded",
    "Unhealthy": "workload.health-check-failed",
}


@dataclass(frozen=True)
class KubernetesEventsIntegration:
    """One protected tenant-bound, read-only Kubernetes API configuration."""

    tenant_id: str
    integration_id: str
    endpoint: str
    credential_ref: str
    ca_bundle_path: str | None
    cluster_external_id: str
    namespaces: tuple[str, ...]
    cluster_event_namespace: str
    resource_types: tuple[str, ...]
    request_timeout_seconds: int
    max_response_bytes: int
    page_size: int
    max_pages: int
    condition_mappings: tuple[tuple[str, str], ...]
    enabled: bool

    def condition(self, reason: str) -> str:
        for mapped_reason, condition in self.condition_mappings:
            if mapped_reason == reason:
                return condition
        return _DEFAULT_CONDITIONS.get(reason, "kubernetes.event.unclassified")


class KubernetesEventsIntegrationRegistry:
    """Validated in-process view of non-secret Kubernetes integration data."""

    def __init__(self, integrations: tuple[KubernetesEventsIntegration, ...]) -> None:
        self._integrations = {
            (item.tenant_id, item.integration_id): item for item in integrations
        }

    @classmethod
    def from_json(cls, encoded: str) -> "KubernetesEventsIntegrationRegistry":
        document = _json_document(encoded, "kubernetes.events.configuration.invalid")
        try:
            if set(document) != {"integrations"}:
                raise KeyError
            values = document["integrations"]
            if not isinstance(values, list) or not 1 <= len(values) <= _MAX_INTEGRATIONS:
                raise TypeError
            integrations = tuple(cls._integration(value) for value in values)
        except (KeyError, TypeError, ValueError):
            raise KubernetesEventsConfigurationError(
                "kubernetes.events.configuration.invalid"
            ) from None
        keys = {(item.tenant_id, item.integration_id) for item in integrations}
        if len(keys) != len(integrations):
            raise KubernetesEventsConfigurationError(
                "kubernetes.events.configuration.invalid"
            )
        return cls(integrations)

    @staticmethod
    def _integration(value: object) -> KubernetesEventsIntegration:
        expected = {
            "tenantId",
            "integrationId",
            "provider",
            "endpoint",
            "credentialRef",
            "caBundlePath",
            "clusterExternalId",
            "namespaces",
            "clusterEventNamespace",
            "resourceTypes",
            "requestTimeoutSeconds",
            "maxResponseBytes",
            "pageSize",
            "maxPages",
            "conditionMappings",
            "enabled",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise TypeError
        tenant_id = value["tenantId"]
        integration_id = value["integrationId"]
        endpoint = value["endpoint"]
        credential_ref = value["credentialRef"]
        ca_bundle_path = value["caBundlePath"]
        cluster_external_id = value["clusterExternalId"]
        namespaces = value["namespaces"]
        cluster_event_namespace = value["clusterEventNamespace"]
        resource_types = value["resourceTypes"]
        mappings = value["conditionMappings"]
        if (
            not isinstance(tenant_id, str)
            or not _TENANT_ID.fullmatch(tenant_id)
            or not isinstance(integration_id, str)
            or not _INTEGRATION_ID.fullmatch(integration_id)
            or value["provider"] != "kubernetes"
            or not isinstance(credential_ref, str)
            or not _CREDENTIAL_REF.fullmatch(credential_ref)
            or not isinstance(cluster_external_id, str)
            or not _SEGMENT.fullmatch(cluster_external_id)
            or not isinstance(value["enabled"], bool)
            or not _integer(value["requestTimeoutSeconds"], 1, 120)
            or not _integer(value["maxResponseBytes"], 1_024, 16_777_216)
            or not _integer(value["pageSize"], 1, 500)
            or not _integer(value["maxPages"], 1, 20)
        ):
            raise TypeError
        _validate_endpoint(endpoint)
        if ca_bundle_path is not None and (
            not isinstance(ca_bundle_path, str)
            or not Path(ca_bundle_path).is_absolute()
            or not 1 <= len(ca_bundle_path) <= 4096
            or any(ord(character) < 32 for character in ca_bundle_path)
        ):
            raise TypeError
        normalized_namespaces = _unique_names(namespaces, _MAX_NAMESPACES)
        if (
            not isinstance(cluster_event_namespace, str)
            or cluster_event_namespace not in normalized_namespaces
        ):
            raise TypeError
        if (
            not isinstance(resource_types, list)
            or not 1 <= len(resource_types) <= len(_RESOURCE_BINDINGS)
            or any(item not in _RESOURCE_BINDINGS for item in resource_types)
            or len(resource_types) != len(set(resource_types))
        ):
            raise TypeError
        if not isinstance(mappings, dict) or len(mappings) > _MAX_CONDITIONS:
            raise TypeError
        condition_mappings = []
        for reason, condition in mappings.items():
            if (
                not isinstance(reason, str)
                or not _REASON.fullmatch(reason)
                or not isinstance(condition, str)
                or not _CONDITION.fullmatch(condition)
            ):
                raise TypeError
            condition_mappings.append((reason, condition))
        return KubernetesEventsIntegration(
            tenant_id=tenant_id,
            integration_id=integration_id,
            endpoint=endpoint.rstrip("/"),
            credential_ref=credential_ref,
            ca_bundle_path=ca_bundle_path,
            cluster_external_id=cluster_external_id,
            namespaces=normalized_namespaces,
            cluster_event_namespace=cluster_event_namespace,
            resource_types=tuple(sorted(resource_types)),
            request_timeout_seconds=value["requestTimeoutSeconds"],
            max_response_bytes=value["maxResponseBytes"],
            page_size=value["pageSize"],
            max_pages=value["maxPages"],
            condition_mappings=tuple(sorted(condition_mappings)),
            enabled=value["enabled"],
        )

    def resolve(self, tenant_id: str, integration_id: str) -> KubernetesEventsIntegration:
        integration = self._integrations.get((tenant_id, integration_id))
        if integration is None or not integration.enabled:
            raise KubernetesEventsBackendError(
                "kubernetes.events.integration.unavailable"
            )
        return integration


class StaticKubernetesBearerCredentialBroker:
    """Local secret-backed broker; production brokers should issue short leases."""

    def __init__(self, credentials: Mapping[tuple[str, str, str], CredentialLease]) -> None:
        self._credentials = dict(credentials)

    def __repr__(self) -> str:
        return f"StaticKubernetesBearerCredentialBroker(credentials={len(self._credentials)})"

    @classmethod
    def from_json(cls, encoded: str) -> "StaticKubernetesBearerCredentialBroker":
        document = _json_document(
            encoded, "kubernetes.events.credential.configuration.invalid"
        )
        try:
            if set(document) != {"credentials"}:
                raise KeyError
            values = document["credentials"]
            if not isinstance(values, list) or not 1 <= len(values) <= _MAX_CREDENTIALS:
                raise TypeError
            credentials: dict[tuple[str, str, str], CredentialLease] = {}
            for value in values:
                if not isinstance(value, dict) or set(value) != {
                    "tenantId",
                    "integrationId",
                    "credentialRef",
                    "bearerToken",
                    "expiresAt",
                }:
                    raise TypeError
                tenant_id = value["tenantId"]
                integration_id = value["integrationId"]
                credential_ref = value["credentialRef"]
                bearer_token = value["bearerToken"]
                expires_at = value["expiresAt"]
                if (
                    not isinstance(tenant_id, str)
                    or not _TENANT_ID.fullmatch(tenant_id)
                    or not isinstance(integration_id, str)
                    or not _INTEGRATION_ID.fullmatch(integration_id)
                    or not isinstance(credential_ref, str)
                    or not _CREDENTIAL_REF.fullmatch(credential_ref)
                    or not _safe_secret(bearer_token)
                    or (expires_at is not None and not isinstance(expires_at, str))
                ):
                    raise TypeError
                if expires_at is not None:
                    _parse_time(expires_at, "kubernetes.events.credential.configuration.invalid")
                key = (tenant_id, integration_id, credential_ref)
                if key in credentials:
                    raise TypeError
                credentials[key] = CredentialLease("bearer", bearer_token, expires_at)
        except (KeyError, TypeError, ValueError, KubernetesEventsBackendError):
            raise KubernetesEventsConfigurationError(
                "kubernetes.events.credential.configuration.invalid"
            ) from None
        return cls(credentials)

    def resolve(self, request: CredentialLeaseRequest) -> CredentialLease:
        if request.provider != "kubernetes" or request.scopes != (
            "events:read",
            "resources:read",
        ):
            raise KubernetesEventsBackendError(
                "kubernetes.events.credential.unavailable"
            )
        lease = self._credentials.get(
            (request.tenant_id, request.integration_id, request.credential_ref)
        )
        if lease is None or (
            lease.expires_at is not None
            and _parse_time(
                lease.expires_at, "kubernetes.events.credential.unavailable"
            )
            < _parse_time(request.deadline, "kubernetes.events.credential.unavailable")
        ):
            raise KubernetesEventsBackendError(
                "kubernetes.events.credential.unavailable"
            )
        return lease


class KubernetesApiTransport(Protocol):
    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        ca_bundle_path: str | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        """Execute one bounded, no-redirect Kubernetes API GET."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None


class UrllibKubernetesApiTransport:
    """TLS-verifying standard-library transport for the Kubernetes API."""

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
                HTTPSHandler(context=context),
                _NoRedirectHandler(),
            )
            request = Request(url, headers=dict(headers), method="GET")
            with opener.open(request, timeout=timeout_seconds) as response:
                if response.status != 200:
                    raise KubernetesEventsBackendError(
                        "kubernetes.events.backend.unavailable"
                    )
                if response.headers.get_content_type() != "application/json":
                    raise KubernetesEventsBackendError(
                        "kubernetes.events.backend.response.invalid"
                    )
                content = response.read(max_response_bytes + 1)
        except KubernetesEventsBackendError:
            raise
        except Exception:
            raise KubernetesEventsBackendError(
                "kubernetes.events.backend.unavailable"
            ) from None
        if len(content) > max_response_bytes:
            raise KubernetesEventsBackendError(
                "kubernetes.events.backend.response.limited"
            )
        return content


@dataclass(frozen=True)
class _ResolvedResource:
    platform_uid: str
    provider_uid: str
    api_version: str
    kind: str
    namespace: str | None
    name: str


class KubernetesApiEventsBackend:
    """Resolve exact resources and list only their Kubernetes Events."""

    def __init__(
        self,
        registry: KubernetesEventsIntegrationRegistry,
        credentials: CredentialBroker,
        clock: Clock,
        transport: KubernetesApiTransport | None = None,
    ) -> None:
        self._registry = registry
        self._credentials = credentials
        self._clock = clock
        self._transport = transport or UrllibKubernetesApiTransport()

    def query_events(self, request: KubernetesEventQuery) -> KubernetesEventsResult:
        integration = self._registry.resolve(request.tenant_id, request.integration_id)
        timeout_seconds = self._timeout(request, integration)
        try:
            lease = self._credentials.resolve(
                CredentialLeaseRequest(
                    tenant_id=request.tenant_id,
                    actor_id=request.actor_id,
                    integration_id=request.integration_id,
                    credential_ref=integration.credential_ref,
                    provider="kubernetes",
                    scopes=("events:read", "resources:read"),
                    deadline=request.deadline,
                )
            )
        except Exception:
            raise KubernetesEventsBackendError(
                "kubernetes.events.credential.unavailable"
            ) from None
        if lease.scheme != "bearer" or not _safe_secret(lease.secret):
            raise KubernetesEventsBackendError(
                "kubernetes.events.credential.unavailable"
            )
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {lease.secret}",
            "User-Agent": "iip-kubernetes-events-adapter/0.1.0",
        }
        resolved = tuple(
            self._resolve_resource(
                request,
                resource,
                integration,
                headers,
                timeout_seconds,
            )
            for resource in request.resources
        )
        events: list[KubernetesEventRecord] = []
        warnings: set[str] = set()
        event_ids: set[str] = set()
        for resource in resolved:
            resource_events, resource_warnings = self._events_for_resource(
                request,
                resource,
                integration,
                headers,
                timeout_seconds,
            )
            for event in resource_events:
                if event.event_id in event_ids:
                    raise KubernetesEventsBackendError(
                        "kubernetes.events.backend.response.invalid"
                    )
                events.append(event)
                event_ids.add(event.event_id)
            warnings.update(resource_warnings)
        events.sort(key=lambda item: (item.last_observed_at, item.event_id))
        if len(events) > request.max_events:
            events = events[: request.max_events]
            warnings.add("event-limit")
        if not events and warnings:
            raise KubernetesEventsBackendError(
                "kubernetes.events.backend.response.limited"
            )
        executed_at = self._clock.now()
        if _parse_time(executed_at, "kubernetes.events.backend.time.invalid") > _parse_time(
            request.deadline, "kubernetes.events.backend.time.invalid"
        ):
            raise KubernetesEventsBackendError(
                "kubernetes.events.backend.deadline.exceeded"
            )
        status = "partial" if warnings else "complete" if events else "no-data"
        return KubernetesEventsResult(
            executed_at=executed_at,
            status=status,
            events=tuple(events),
            warnings=tuple(sorted(warnings)),
        )

    def _resolve_resource(
        self,
        request: KubernetesEventQuery,
        resource: KubernetesEventResourceRef,
        integration: KubernetesEventsIntegration,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> _ResolvedResource:
        binding = _RESOURCE_BINDINGS.get(resource.resource_type)
        if (
            resource.provider != "kubernetes"
            or binding is None
            or binding.resource_type not in integration.resource_types
        ):
            raise KubernetesEventsBackendError(
                "kubernetes.events.resource.unsupported"
            )
        parts = resource.external_id.split("/")
        expected_count = 3 if binding.namespaced else 2
        if (
            len(parts) != expected_count
            or parts[0] != integration.cluster_external_id
            or any(not _KUBERNETES_NAME.fullmatch(part) for part in parts[1:])
        ):
            raise KubernetesEventsBackendError(
                "kubernetes.events.resource.out-of-scope"
            )
        namespace = parts[1] if binding.namespaced else None
        name = parts[2] if binding.namespaced else parts[1]
        if namespace is not None and namespace not in integration.namespaces:
            raise KubernetesEventsBackendError(
                "kubernetes.events.resource.out-of-scope"
            )
        path = binding.api_prefix
        if namespace is not None:
            path += f"/namespaces/{quote(namespace, safe='')}"
        path += f"/{binding.plural}/{quote(name, safe='')}"
        document = self._get_json(
            integration,
            path,
            headers,
            timeout_seconds,
            request.deadline,
        )
        metadata = document.get("metadata")
        provider_uid = metadata.get("uid") if isinstance(metadata, dict) else None
        actual_namespace = (
            metadata.get("namespace") if isinstance(metadata, dict) else None
        )
        if (
            document.get("apiVersion") != binding.api_version
            or document.get("kind") != binding.kind
            or not isinstance(metadata, dict)
            or metadata.get("name") != name
            or actual_namespace != namespace
            or not _safe_text(provider_uid, 253)
        ):
            raise KubernetesEventsBackendError(
                "kubernetes.events.backend.response.invalid"
            )
        return _ResolvedResource(
            platform_uid=resource.platform_uid,
            provider_uid=provider_uid,
            api_version=binding.api_version,
            kind=binding.kind,
            namespace=namespace,
            name=name,
        )

    def _events_for_resource(
        self,
        request: KubernetesEventQuery,
        resource: _ResolvedResource,
        integration: KubernetesEventsIntegration,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> tuple[tuple[KubernetesEventRecord, ...], tuple[str, ...]]:
        namespace = resource.namespace or integration.cluster_event_namespace
        continuation: str | None = None
        events: list[KubernetesEventRecord] = []
        for page_number in range(integration.max_pages):
            parameters = {
                "fieldSelector": f"involvedObject.uid={resource.provider_uid}",
                "limit": str(integration.page_size),
            }
            if continuation is not None:
                parameters["continue"] = continuation
            path = (
                f"/api/v1/namespaces/{quote(namespace, safe='')}/events?"
                + urlencode(parameters)
            )
            document = self._get_json(
                integration,
                path,
                headers,
                timeout_seconds,
                request.deadline,
            )
            items = document.get("items")
            metadata = document.get("metadata")
            if (
                document.get("apiVersion") != "v1"
                or document.get("kind") != "EventList"
                or not isinstance(items, list)
                or not isinstance(metadata, dict)
            ):
                raise KubernetesEventsBackendError(
                    "kubernetes.events.backend.response.invalid"
                )
            for item in items:
                event = self._event(request, resource, integration, item)
                if event is not None:
                    events.append(event)
                    if len(events) > request.max_events:
                        return tuple(events), ("event-limit",)
            raw_continuation = metadata.get("continue", "")
            if not isinstance(raw_continuation, str) or len(raw_continuation) > 4096:
                raise KubernetesEventsBackendError(
                    "kubernetes.events.backend.response.invalid"
                )
            if not raw_continuation:
                return tuple(events), ()
            continuation = raw_continuation
            if page_number + 1 == integration.max_pages:
                return tuple(events), ("backend-partial",)
        raise AssertionError("unreachable")

    def _event(
        self,
        request: KubernetesEventQuery,
        resource: _ResolvedResource,
        integration: KubernetesEventsIntegration,
        value: object,
    ) -> KubernetesEventRecord | None:
        if not isinstance(value, dict):
            raise KubernetesEventsBackendError(
                "kubernetes.events.backend.response.invalid"
            )
        metadata = value.get("metadata")
        involved = value.get("involvedObject")
        series = value.get("series")
        source = value.get("source")
        if not isinstance(metadata, dict) or not isinstance(involved, dict):
            raise KubernetesEventsBackendError(
                "kubernetes.events.backend.response.invalid"
            )
        event_uid = metadata.get("uid")
        reason = value.get("reason")
        raw_type = value.get("type")
        if (
            value.get("apiVersion") not in (None, "v1")
            or value.get("kind") not in (None, "Event")
            or not _safe_text(event_uid, 253)
            or not isinstance(reason, str)
            or not _REASON.fullmatch(reason)
            or raw_type not in ("Normal", "Warning")
            or involved.get("uid") != resource.provider_uid
            or involved.get("apiVersion") != resource.api_version
            or involved.get("kind") != resource.kind
            or involved.get("name") != resource.name
            or involved.get("namespace") != resource.namespace
        ):
            raise KubernetesEventsBackendError(
                "kubernetes.events.backend.response.invalid"
            )
        severity = "normal" if raw_type == "Normal" else "warning"
        if request.severities and severity not in request.severities:
            return None
        if request.reasons and reason not in request.reasons:
            return None
        last_value = None
        if isinstance(series, dict):
            last_value = series.get("lastObservedTime")
        last_value = (
            last_value
            or value.get("eventTime")
            or value.get("lastTimestamp")
            or metadata.get("creationTimestamp")
        )
        last = _parse_time(last_value, "kubernetes.events.backend.response.invalid")
        start = _parse_time(request.start, "kubernetes.events.backend.time.invalid")
        end = _parse_time(request.end, "kubernetes.events.backend.time.invalid")
        if last < start or last > end:
            return None
        first_value = (
            value.get("firstTimestamp")
            or value.get("eventTime")
            or metadata.get("creationTimestamp")
            or last_value
        )
        first = _parse_time(first_value, "kubernetes.events.backend.response.invalid")
        if first > last:
            raise KubernetesEventsBackendError(
                "kubernetes.events.backend.response.invalid"
            )
        count = series.get("count") if isinstance(series, dict) else None
        if count is None:
            count = value.get("count", 1)
        if not _integer(count, 1, 2_147_483_647):
            raise KubernetesEventsBackendError(
                "kubernetes.events.backend.response.invalid"
            )
        if first < start:
            first = last
            count = 1
        controller = value.get("reportingComponent")
        if controller is None and isinstance(source, dict):
            controller = source.get("component")
        if not _safe_text(controller, 256):
            controller = None
        message = value.get("message")
        if not _safe_text(message, 2048, allow_newlines=True):
            message = None
        event_id = "kve_" + hashlib.sha256(
            "\x1f".join(
                (request.integration_id, resource.platform_uid, event_uid)
            ).encode("utf-8")
        ).hexdigest()[:32]
        return KubernetesEventRecord(
            event_id=event_id,
            resource_uid=resource.platform_uid,
            severity=severity,
            reason=reason,
            condition=integration.condition(reason),
            first_observed_at=_format_time(first),
            last_observed_at=_format_time(last),
            occurrence_count=count,
            reporting_controller=controller,
            message=message,
        )

    def _get_json(
        self,
        integration: KubernetesEventsIntegration,
        path: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
        deadline: str,
    ) -> dict[str, object]:
        remaining = (
            _parse_time(deadline, "kubernetes.events.backend.time.invalid")
            - _parse_time(self._clock.now(), "kubernetes.events.backend.time.invalid")
        ).total_seconds()
        if remaining <= 0:
            raise KubernetesEventsBackendError(
                "kubernetes.events.backend.deadline.exceeded"
            )
        content = self._transport.get(
            integration.endpoint + path,
            headers,
            ca_bundle_path=integration.ca_bundle_path,
            timeout_seconds=min(timeout_seconds, remaining),
            max_response_bytes=integration.max_response_bytes,
        )
        try:
            document = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise KubernetesEventsBackendError(
                "kubernetes.events.backend.response.invalid"
            ) from None
        if not isinstance(document, dict):
            raise KubernetesEventsBackendError(
                "kubernetes.events.backend.response.invalid"
            )
        return document

    def _timeout(
        self,
        request: KubernetesEventQuery,
        integration: KubernetesEventsIntegration,
    ) -> float:
        remaining = (
            _parse_time(request.deadline, "kubernetes.events.backend.time.invalid")
            - _parse_time(self._clock.now(), "kubernetes.events.backend.time.invalid")
        ).total_seconds()
        if remaining <= 0:
            raise KubernetesEventsBackendError(
                "kubernetes.events.backend.deadline.exceeded"
            )
        return min(float(integration.request_timeout_seconds), remaining)


def build_kubernetes_events_backend_from_environment(
    environment: Mapping[str, str],
    clock: Clock,
) -> KubernetesApiEventsBackend:
    """Build the live adapter from protected runtime configuration."""

    integrations = environment.get("IIP_KUBERNETES_EVENTS_INTEGRATIONS_JSON")
    credentials = environment.get("IIP_KUBERNETES_EVENTS_CREDENTIALS_JSON")
    if integrations is None or credentials is None:
        raise KubernetesEventsConfigurationError(
            "kubernetes.events.configuration.required"
        )
    return KubernetesApiEventsBackend(
        KubernetesEventsIntegrationRegistry.from_json(integrations),
        StaticKubernetesBearerCredentialBroker.from_json(credentials),
        clock,
    )


def _json_document(encoded: object, error_code: str) -> dict[str, object]:
    if (
        not isinstance(encoded, str)
        or not 1 <= len(encoded.encode("utf-8")) <= _MAX_CONFIGURATION_BYTES
    ):
        raise KubernetesEventsConfigurationError(error_code)
    try:
        document = json.loads(encoded)
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise KubernetesEventsConfigurationError(error_code) from None
    if not isinstance(document, dict):
        raise KubernetesEventsConfigurationError(error_code)
    return document


def _validate_endpoint(value: object) -> None:
    if not isinstance(value, str):
        raise TypeError
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise TypeError from None
    if (
        not 1 <= len(value) <= 2048
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65_535)
        or any(character.isspace() for character in value)
    ):
        raise TypeError


def _unique_names(value: object, maximum: int) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= maximum
        or any(not isinstance(item, str) or not _KUBERNETES_NAME.fullmatch(item) for item in value)
        or len(value) != len(set(value))
    ):
        raise TypeError
    return tuple(sorted(value))


def _integer(value: object, minimum: int, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _safe_text(value: object, maximum: int, *, allow_newlines: bool = False) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        return False
    allowed = {10, 13} if allow_newlines else set()
    return not any(
        (ord(character) < 32 and ord(character) not in allowed)
        or ord(character) == 127
        for character in value
    )


def _safe_secret(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and 16 <= len(value) <= 16_384
        and not any(ord(character) < 33 or ord(character) == 127 for character in value)
    )


def _parse_time(value: object, error_code: str) -> datetime:
    if not isinstance(value, str):
        raise KubernetesEventsBackendError(error_code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise KubernetesEventsBackendError(error_code) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KubernetesEventsBackendError(error_code)
    return parsed


def _format_time(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
