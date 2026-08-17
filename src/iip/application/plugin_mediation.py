"""Invocation-scoped, policy-checked plugin read mediation."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timezone

from iip.application.investigate import canonical_digest
from iip.application.ports import (
    ActorContext,
    AuditSink,
    PluginMediationBinding,
    PluginMediationBindingRegistry,
    PluginMediationGateway,
    PolicyDecisionPoint,
)


_GRANT_ID = re.compile(r"pmg_[a-f0-9]{32}")
_REQUEST_ID = re.compile(r"pmr_[a-f0-9]{32}")
_INVOCATION_ID = re.compile(r"pin_[a-f0-9]{32}")
_PATH = re.compile(r"/[A-Za-z0-9._~/-]{1,511}")
_QUERY_KEY = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}")
_TEMPLATE_PARAMETER = re.compile(r"\{[a-z][A-Za-z0-9]{0,31}\}")
_FAILURE_CODES = frozenset(
    {
        "plugin.mediation.audit-unavailable",
        "plugin.mediation.credential-unavailable",
        "plugin.mediation.deadline-exceeded",
        "plugin.mediation.denied",
        "plugin.mediation.limit-exceeded",
        "plugin.mediation.provider-unavailable",
        "plugin.mediation.request-invalid",
        "plugin.mediation.response-invalid",
        "plugin.mediation.response-too-large",
    }
)


class PluginMediationError(RuntimeError):
    """Fail-closed setup error carrying only a stable external code."""


class PluginMediationGatewayError(RuntimeError):
    """Provider-neutral gateway failure carrying a mediation response code."""


class BoundPluginMediator:
    """One invocation-local request handler with in-memory replay and count guards."""

    def __init__(
        self,
        actor: ActorContext,
        plugin_id: str,
        plugin_version: str,
        invocation_id: str,
        deadline: str,
        grants: Mapping[str, tuple[Mapping[str, object], PluginMediationBinding]],
        policy: PolicyDecisionPoint,
        audit: AuditSink,
        gateway: PluginMediationGateway,
        now: Callable[[], datetime],
    ) -> None:
        self._actor = actor
        self._plugin_id = plugin_id
        self._plugin_version = plugin_version
        self._invocation_id = invocation_id
        self._deadline = deadline
        self._grants = dict(grants)
        self._policy = policy
        self._audit = audit
        self._gateway = gateway
        self._now = now
        self._counts = {grant_id: 0 for grant_id in grants}
        self._request_ids: set[str] = set()
        self._lock = threading.Lock()

    def handle(self, request: Mapping[str, object]) -> Mapping[str, object]:
        request_id, grant_id, path, query = self._request(request)
        if request_id is None:
            return self._failure(_request_id_or_fallback(request), "plugin.mediation.request-invalid")
        grant_entry = self._grants.get(grant_id)
        if grant_entry is None:
            return self._failure(request_id, "plugin.mediation.denied")
        grant, binding = grant_entry
        grant_metadata = grant["metadata"]
        grant_spec = grant["spec"]
        assert isinstance(grant_metadata, Mapping)
        assert isinstance(grant_spec, Mapping)
        if not self._within_deadline(str(grant_metadata["expiresAt"])):
            return self._failure(request_id, "plugin.mediation.deadline-exceeded")
        if not _matches_any_path(path, binding.path_templates):
            return self._failure(request_id, "plugin.mediation.denied")
        if not set(query).issubset(binding.query_keys):
            return self._failure(request_id, "plugin.mediation.denied")
        with self._lock:
            if request_id in self._request_ids:
                return self._failure(request_id, "plugin.mediation.request-invalid")
            self._request_ids.add(request_id)
            if self._counts[grant_id] >= binding.max_requests:
                return self._failure(request_id, "plugin.mediation.limit-exceeded")
            self._counts[grant_id] += 1
        policy_resource = {
            "tenantId": self._actor.tenant_id,
            "pluginId": self._plugin_id,
            "pluginVersion": self._plugin_version,
            "invocationId": self._invocation_id,
            "grantId": grant_id,
            "integrationId": binding.integration_id,
            "provider": binding.provider,
            "destination": binding.destination,
            "pathDigest": _text_digest(path),
            "queryDigest": canonical_digest(query),
        }
        try:
            decision = self._policy.decide(
                self._actor, "plugin:mediate-read", policy_resource
            )
        except Exception:
            return self._failure(request_id, "plugin.mediation.denied")
        if not decision.allowed:
            return self._failure(request_id, "plugin.mediation.denied")
        audit_document = {
            "apiVersion": "iip.audit/v1alpha1",
            "kind": "PluginMediationReadIntent",
            "metadata": {
                "tenantId": self._actor.tenant_id,
                "actorId": self._actor.actor_id,
                "recordedAt": self._timestamp(),
            },
            "spec": {
                **policy_resource,
                "requestId": request_id,
                "policyReasonCode": decision.reason_code,
                "policySnapshotRef": decision.policy_snapshot_ref,
            },
        }
        try:
            self._audit.append_audit(
                self._actor, "plugin-mediation-read-intent", audit_document
            )
        except Exception:
            return self._failure(request_id, "plugin.mediation.audit-unavailable")
        try:
            body = self._gateway.fetch_plugin_json(
                self._actor,
                binding,
                path=path,
                query=query,
                deadline=_earlier_deadline(
                    self._deadline, str(grant_metadata["expiresAt"])
                ),
                max_response_bytes=binding.max_response_bytes,
            )
        except PluginMediationGatewayError as error:
            code = str(error)
            if code not in _FAILURE_CODES:
                code = "plugin.mediation.provider-unavailable"
            return self._failure(request_id, code)
        except Exception:
            return self._failure(request_id, "plugin.mediation.provider-unavailable")
        if not isinstance(body, (Mapping, list)):
            return self._failure(request_id, "plugin.mediation.response-invalid")
        try:
            normalized = json.loads(json.dumps(body, allow_nan=False))
            encoded = _canonical_bytes(normalized)
        except (TypeError, ValueError):
            return self._failure(request_id, "plugin.mediation.response-invalid")
        if len(encoded) > binding.max_response_bytes:
            return self._failure(request_id, "plugin.mediation.response-too-large")
        return {
            "apiVersion": "iip.plugin-runtime/v1alpha1",
            "kind": "PluginMediationResponse",
            "metadata": {
                "requestId": request_id,
                "invocationId": self._invocation_id,
                "completedAt": self._timestamp(),
            },
            "spec": {
                "status": "succeeded",
                "mediaType": "application/json",
                "body": normalized,
                "bodyDigest": "sha256:" + hashlib.sha256(encoded).hexdigest(),
                "bodyBytes": len(encoded),
            },
        }

    def _request(
        self, request: Mapping[str, object]
    ) -> tuple[str | None, str, str, Mapping[str, tuple[str, ...]]]:
        try:
            if (
                not isinstance(request, Mapping)
                or set(request) != {"apiVersion", "kind", "metadata", "spec"}
                or request.get("apiVersion") != "iip.plugin-runtime/v1alpha1"
                or request.get("kind") != "PluginMediationRequest"
            ):
                raise ValueError
            metadata = request["metadata"]
            spec = request["spec"]
            if (
                not isinstance(metadata, Mapping)
                or set(metadata) != {"id", "invocationId", "grantId"}
                or not isinstance(spec, Mapping)
                or set(spec) != {"method", "path", "query"}
            ):
                raise ValueError
            request_id = metadata["id"]
            invocation_id = metadata["invocationId"]
            grant_id = metadata["grantId"]
            path = spec["path"]
            raw_query = spec["query"]
            if (
                not isinstance(request_id, str)
                or _REQUEST_ID.fullmatch(request_id) is None
                or invocation_id != self._invocation_id
                or not isinstance(grant_id, str)
                or _GRANT_ID.fullmatch(grant_id) is None
                or spec["method"] != "GET"
                or not isinstance(path, str)
                or _PATH.fullmatch(path) is None
                or not isinstance(raw_query, Mapping)
                or len(raw_query) > 32
            ):
                raise ValueError
            query: dict[str, tuple[str, ...]] = {}
            for key, raw_value in raw_query.items():
                if not isinstance(key, str) or _QUERY_KEY.fullmatch(key) is None:
                    raise ValueError
                values = raw_value if isinstance(raw_value, list) else [raw_value]
                if not 1 <= len(values) <= 16:
                    raise ValueError
                if any(not _safe_query_value(value) for value in values):
                    raise ValueError
                query[key] = tuple(values)
            return request_id, grant_id, path, query
        except (KeyError, TypeError, ValueError):
            return None, "", "", {}

    def _within_deadline(self, grant_expiry: str) -> bool:
        current = self._now().astimezone(timezone.utc)
        return current <= _timestamp(self._deadline) and current <= _timestamp(grant_expiry)

    def _timestamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _failure(self, request_id: str, code: str) -> Mapping[str, object]:
        return {
            "apiVersion": "iip.plugin-runtime/v1alpha1",
            "kind": "PluginMediationResponse",
            "metadata": {
                "requestId": request_id,
                "invocationId": self._invocation_id,
                "completedAt": self._timestamp(),
            },
            "spec": {"status": "failed", "error": {"code": code}},
        }


class PluginMediationService:
    """Bind public grants to protected host authority before plugin execution."""

    def __init__(
        self,
        registry: PluginMediationBindingRegistry,
        policy: PolicyDecisionPoint,
        audit: AuditSink,
        gateway: PluginMediationGateway,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._audit = audit
        self._gateway = gateway
        self._now = now

    def bind(
        self,
        actor: ActorContext,
        manifest: Mapping[str, object],
        invocation: Mapping[str, object],
    ) -> BoundPluginMediator:
        try:
            manifest_metadata = manifest["metadata"]
            permissions = manifest["spec"]["permissions"]
            invocation_metadata = invocation["metadata"]
            invocation_spec = invocation["spec"]
            if not all(
                isinstance(value, Mapping)
                for value in (
                    manifest_metadata,
                    permissions,
                    invocation_metadata,
                    invocation_spec,
                )
            ):
                raise ValueError
            plugin_id = str(manifest_metadata["id"])
            plugin_version = str(manifest_metadata["version"])
            invocation_id = invocation_metadata["id"]
            created_at = invocation_metadata["createdAt"]
            deadline = invocation_metadata["deadline"]
            grants = invocation_spec.get("mediationGrants")
            network_permissions = permissions.get("network")
            secret_permissions = permissions.get("secrets")
            action_permissions = permissions.get("actions")
            if (
                not isinstance(invocation_id, str)
                or _INVOCATION_ID.fullmatch(invocation_id) is None
                or invocation_metadata.get("tenantId") != actor.tenant_id
                or invocation_metadata.get("actorId") != actor.actor_id
                or not isinstance(created_at, str)
                or not isinstance(deadline, str)
                or not isinstance(grants, list)
                or not 1 <= len(grants) <= 16
                or invocation_spec.get("manifestDigest") != canonical_digest(manifest)
                or not isinstance(network_permissions, list)
                or any(not isinstance(item, str) for item in network_permissions)
                or not isinstance(secret_permissions, list)
                or any(not isinstance(item, str) for item in secret_permissions)
                or not isinstance(action_permissions, list)
                or action_permissions
            ):
                raise ValueError
            resolved: dict[
                str, tuple[Mapping[str, object], PluginMediationBinding]
            ] = {}
            for grant in grants:
                grant_id, binding = self._resolve_grant(
                    actor,
                    plugin_id,
                    plugin_version,
                    invocation_id,
                    created_at,
                    deadline,
                    permissions,
                    grant,
                )
                if grant_id in resolved:
                    raise ValueError
                resolved[grant_id] = (grant, binding)
            return BoundPluginMediator(
                actor,
                plugin_id,
                plugin_version,
                invocation_id,
                deadline,
                resolved,
                self._policy,
                self._audit,
                self._gateway,
                self._now,
            )
        except PluginMediationError:
            raise
        except (KeyError, TypeError, ValueError):
            raise PluginMediationError("plugin.mediation.binding-invalid") from None

    def _resolve_grant(
        self,
        actor: ActorContext,
        plugin_id: str,
        plugin_version: str,
        invocation_id: str,
        invocation_created_at: str,
        invocation_deadline: str,
        permissions: Mapping[str, object],
        grant: object,
    ) -> tuple[str, PluginMediationBinding]:
        if not isinstance(grant, Mapping):
            raise ValueError
        metadata = grant.get("metadata")
        spec = grant.get("spec")
        if (
            set(grant) != {"apiVersion", "kind", "metadata", "spec"}
            or grant.get("apiVersion") != "iip.platform/v1alpha1"
            or grant.get("kind") != "PluginMediationGrant"
            or not isinstance(metadata, Mapping)
            or set(metadata)
            != {"id", "invocationId", "tenantId", "actorId", "issuedAt", "expiresAt"}
            or not isinstance(spec, Mapping)
            or set(spec)
            != {
                "integrationId",
                "provider",
                "destination",
                "credentialName",
                "operation",
                "pathTemplates",
                "queryKeys",
                "scopes",
                "limits",
            }
        ):
            raise ValueError
        grant_id = metadata.get("id")
        limits = spec.get("limits")
        if (
            not isinstance(grant_id, str)
            or _GRANT_ID.fullmatch(grant_id) is None
            or metadata.get("invocationId") != invocation_id
            or metadata.get("tenantId") != actor.tenant_id
            or metadata.get("actorId") != actor.actor_id
            or not isinstance(limits, Mapping)
            or spec.get("operation") != "http-json-read"
            or spec.get("destination") not in permissions.get("network", [])
            or spec.get("credentialName") not in permissions.get("secrets", [])
        ):
            raise ValueError
        issued = _timestamp(metadata.get("issuedAt"))
        expires = _timestamp(metadata.get("expiresAt"))
        invocation_created = _timestamp(invocation_created_at)
        invocation_expires = _timestamp(invocation_deadline)
        current = self._now().astimezone(timezone.utc)
        if (
            issued < invocation_created
            or issued > current
            or expires > invocation_expires
            or issued > expires
            or current > expires
        ):
            raise PluginMediationError("plugin.mediation.binding-expired")
        binding = self._registry.resolve_plugin_mediation_binding(
            actor, plugin_id, plugin_version, grant_id
        )
        if binding is None:
            raise PluginMediationError("plugin.mediation.binding-not-found")
        expected = {
            "tenantId": binding.tenant_id,
            "integrationId": binding.integration_id,
            "provider": binding.provider,
            "destination": binding.destination,
            "credentialName": binding.credential_name,
            "pathTemplates": list(binding.path_templates),
            "queryKeys": list(binding.query_keys),
            "scopes": list(binding.scopes),
            "limits": {
                "maxRequests": binding.max_requests,
                "maxResponseBytes": binding.max_response_bytes,
            },
        }
        actual = {
            "tenantId": actor.tenant_id,
            "integrationId": spec.get("integrationId"),
            "provider": spec.get("provider"),
            "destination": spec.get("destination"),
            "credentialName": spec.get("credentialName"),
            "pathTemplates": spec.get("pathTemplates"),
            "queryKeys": spec.get("queryKeys"),
            "scopes": spec.get("scopes"),
            "limits": limits,
        }
        if (
            actual != expected
            or binding.plugin_id != plugin_id
            or binding.plugin_version != plugin_version
            or binding.grant_id != grant_id
        ):
            raise PluginMediationError("plugin.mediation.binding-mismatch")
        return grant_id, binding


def _matches_any_path(path: str, templates: tuple[str, ...]) -> bool:
    path_segments = path.split("/")[1:]
    if any(segment in {"", ".", ".."} for segment in path_segments):
        return False
    for template in templates:
        template_segments = template.split("/")[1:]
        if len(path_segments) != len(template_segments):
            continue
        if all(
            template_segment == path_segment
            or (
                _TEMPLATE_PARAMETER.fullmatch(template_segment) is not None
                and _safe_path_segment(path_segment)
            )
            for template_segment, path_segment in zip(template_segments, path_segments)
        ):
            return True
    return False


def _safe_path_segment(value: str) -> bool:
    return bool(
        value not in {"", ".", ".."}
        and len(value) <= 128
        and re.fullmatch(r"[A-Za-z0-9._~-]+", value) is not None
    )


def _safe_query_value(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) <= 1024
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(timezone.utc)


def _earlier_deadline(left: str, right: str) -> str:
    selected = min(_timestamp(left), _timestamp(right))
    return selected.isoformat().replace("+00:00", "Z")


def _canonical_bytes(document: object) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request_id_or_fallback(request: Mapping[str, object]) -> str:
    metadata = request.get("metadata") if isinstance(request, Mapping) else None
    request_id = metadata.get("id") if isinstance(metadata, Mapping) else None
    if isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id):
        return request_id
    return "pmr_" + "0" * 32
