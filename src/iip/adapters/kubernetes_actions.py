"""Request-scoped Kubernetes workload restart executor with verification and rollback."""

from __future__ import annotations

import json
import re
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping, Protocol
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from iip.application.ports import (
    ActionExecutionOutcome,
    ActorContext,
    Clock,
    CredentialBroker,
    CredentialLease,
    CredentialLeaseRequest,
)


_TENANT_ID = re.compile(r"[a-zA-Z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_CREDENTIAL_REF = re.compile(r"credential://[A-Za-z0-9._~:/-]{1,2020}")
_KUBERNETES_NAME = re.compile(
    r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?)*"
)
_PROVIDER_UID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,252}")
_RESOURCE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+/-]{0,511}")
_MAX_CONFIGURATION_BYTES = 1_048_576
_MAX_INTEGRATIONS = 128
_MAX_CREDENTIALS = 256
_RESTART_ANNOTATION = "iip.platform/restarted-at"
_KIND_BINDINGS = {
    "deployment": ("Deployment", "deployments"),
    "statefulset": ("StatefulSet", "statefulsets"),
    "daemonset": ("DaemonSet", "daemonsets"),
}
_ERROR_SUMMARIES = {
    "action.executor.configuration-unavailable": (
        "The protected Kubernetes action integration is unavailable."
    ),
    "action.executor.credential-unavailable": (
        "A request-scoped Kubernetes credential could not be issued."
    ),
    "action.executor.target-binding-required": (
        "The proposal does not contain an observed Kubernetes object UID."
    ),
    "action.executor.target-out-of-scope": (
        "The approved workload is outside the configured action allowlist."
    ),
    "action.executor.target-replaced": (
        "The Kubernetes object UID no longer matches the approved observation."
    ),
    "action.executor.preflight-rejected": (
        "Kubernetes rejected the server-side dry-run restart patch."
    ),
    "action.executor.apply-unknown": (
        "The live restart request did not return a trustworthy result; provider state must be checked."
    ),
    "action.executor.verification-failed": (
        "The restarted workload did not become ready within the approved verification window."
    ),
    "action.executor.rollback-failed": (
        "The prior restart annotation could not be verified after rollback."
    ),
    "action.executor.response-invalid": (
        "Kubernetes returned a response that failed identity or structure validation."
    ),
}


class KubernetesActionsConfigurationError(RuntimeError):
    """Protected Kubernetes action executor configuration is invalid."""


class KubernetesActionExecutorError(RuntimeError):
    """Stable adapter failure that never includes provider response text."""

    def __init__(self, code: str, *, mutation_possible: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.mutation_possible = mutation_possible


@dataclass(frozen=True)
class KubernetesActionIntegration:
    tenant_id: str
    integration_id: str
    endpoint: str
    credential_ref: str
    ca_bundle_path: str | None
    cluster_external_id: str
    namespaces: tuple[str, ...]
    workload_kinds: tuple[str, ...]
    request_timeout_seconds: int
    max_response_bytes: int
    verification_timeout_seconds: int
    poll_interval_milliseconds: int
    live_execution_enabled: bool
    enabled: bool


class KubernetesActionIntegrationRegistry:
    """Closed tenant/integration action allowlist containing no credentials."""

    def __init__(self, integrations: tuple[KubernetesActionIntegration, ...]) -> None:
        self._integrations = {
            (item.tenant_id, item.integration_id): item for item in integrations
        }

    @classmethod
    def from_json(cls, encoded: str) -> "KubernetesActionIntegrationRegistry":
        document = _json_document(encoded, "kubernetes.actions.configuration.invalid")
        try:
            if set(document) != {"integrations"}:
                raise TypeError
            values = document["integrations"]
            if not isinstance(values, list) or not 1 <= len(values) <= _MAX_INTEGRATIONS:
                raise TypeError
            integrations = tuple(cls._integration(value) for value in values)
        except (KeyError, TypeError, ValueError):
            raise cls._invalid() from None
        keys = {(item.tenant_id, item.integration_id) for item in integrations}
        if len(keys) != len(integrations):
            raise cls._invalid()
        return cls(integrations)

    @classmethod
    def _integration(cls, value: object) -> KubernetesActionIntegration:
        expected = {
            "tenantId",
            "integrationId",
            "provider",
            "endpoint",
            "credentialRef",
            "caBundlePath",
            "clusterExternalId",
            "namespaces",
            "workloadKinds",
            "requestTimeoutSeconds",
            "maxResponseBytes",
            "verificationTimeoutSeconds",
            "pollIntervalMilliseconds",
            "liveExecutionEnabled",
            "enabled",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise TypeError
        tenant_id = value["tenantId"]
        integration_id = value["integrationId"]
        credential_ref = value["credentialRef"]
        ca_bundle_path = value["caBundlePath"]
        cluster_external_id = value["clusterExternalId"]
        namespaces = _unique_names(value["namespaces"], 128)
        workload_kinds = value["workloadKinds"]
        if (
            not isinstance(tenant_id, str)
            or _TENANT_ID.fullmatch(tenant_id) is None
            or not isinstance(integration_id, str)
            or _INTEGRATION_ID.fullmatch(integration_id) is None
            or value["provider"] != "kubernetes"
            or not isinstance(credential_ref, str)
            or _CREDENTIAL_REF.fullmatch(credential_ref) is None
            or not isinstance(cluster_external_id, str)
            or _KUBERNETES_NAME.fullmatch(cluster_external_id) is None
            or not isinstance(value["liveExecutionEnabled"], bool)
            or not isinstance(value["enabled"], bool)
            or not _integer(value["requestTimeoutSeconds"], 1, 120)
            or not _integer(value["maxResponseBytes"], 1_024, 16_777_216)
            or not _integer(value["verificationTimeoutSeconds"], 5, 900)
            or not _integer(value["pollIntervalMilliseconds"], 100, 5_000)
            or not isinstance(workload_kinds, list)
            or not 1 <= len(workload_kinds) <= len(_KIND_BINDINGS)
            or any(item not in _KIND_BINDINGS for item in workload_kinds)
            or len(workload_kinds) != len(set(workload_kinds))
        ):
            raise TypeError
        _validate_endpoint(value["endpoint"])
        if ca_bundle_path is not None and (
            not isinstance(ca_bundle_path, str)
            or not Path(ca_bundle_path).is_absolute()
            or not 1 <= len(ca_bundle_path) <= 4096
            or any(ord(character) < 32 for character in ca_bundle_path)
        ):
            raise TypeError
        return KubernetesActionIntegration(
            tenant_id=tenant_id,
            integration_id=integration_id,
            endpoint=str(value["endpoint"]).rstrip("/"),
            credential_ref=credential_ref,
            ca_bundle_path=ca_bundle_path,
            cluster_external_id=cluster_external_id,
            namespaces=namespaces,
            workload_kinds=tuple(sorted(workload_kinds)),
            request_timeout_seconds=value["requestTimeoutSeconds"],
            max_response_bytes=value["maxResponseBytes"],
            verification_timeout_seconds=value["verificationTimeoutSeconds"],
            poll_interval_milliseconds=value["pollIntervalMilliseconds"],
            live_execution_enabled=value["liveExecutionEnabled"],
            enabled=value["enabled"],
        )

    def resolve(self, tenant_id: str, integration_id: str) -> KubernetesActionIntegration:
        integration = self._integrations.get((tenant_id, integration_id))
        if integration is None or not integration.enabled:
            raise KubernetesActionExecutorError(
                "action.executor.configuration-unavailable"
            )
        return integration

    @staticmethod
    def _invalid() -> KubernetesActionsConfigurationError:
        return KubernetesActionsConfigurationError(
            "kubernetes.actions.configuration.invalid"
        )


class StaticKubernetesActionCredentialBroker:
    """Local-only exact-scope Bearer broker for executor conformance."""

    def __init__(self, credentials: Mapping[tuple[str, str, str], CredentialLease]) -> None:
        self._credentials = dict(credentials)

    def __repr__(self) -> str:
        return f"StaticKubernetesActionCredentialBroker(credentials={len(self._credentials)})"

    @classmethod
    def from_json(cls, encoded: str) -> "StaticKubernetesActionCredentialBroker":
        document = _json_document(
            encoded, "kubernetes.actions.credential.configuration.invalid"
        )
        try:
            if set(document) != {"credentials"}:
                raise TypeError
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
                key = (
                    value["tenantId"],
                    value["integrationId"],
                    value["credentialRef"],
                )
                if (
                    not isinstance(key[0], str)
                    or _TENANT_ID.fullmatch(key[0]) is None
                    or not isinstance(key[1], str)
                    or _INTEGRATION_ID.fullmatch(key[1]) is None
                    or not isinstance(key[2], str)
                    or _CREDENTIAL_REF.fullmatch(key[2]) is None
                    or not _safe_secret(value["bearerToken"])
                    or (
                        value["expiresAt"] is not None
                        and not isinstance(value["expiresAt"], str)
                    )
                    or key in credentials
                ):
                    raise TypeError
                if value["expiresAt"] is not None:
                    _parse_time(value["expiresAt"])
                credentials[key] = CredentialLease(
                    "bearer", value["bearerToken"], value["expiresAt"]
                )
        except (KeyError, TypeError, ValueError):
            raise KubernetesActionsConfigurationError(
                "kubernetes.actions.credential.configuration.invalid"
            ) from None
        return cls(credentials)

    def resolve(self, request: CredentialLeaseRequest) -> CredentialLease:
        if request.provider != "kubernetes" or request.scopes != (
            "resources:read",
            "workloads:patch",
        ):
            raise KubernetesActionExecutorError(
                "action.executor.credential-unavailable"
            )
        lease = self._credentials.get(
            (request.tenant_id, request.integration_id, request.credential_ref)
        )
        if lease is None or (
            lease.expires_at is not None
            and _parse_time(lease.expires_at) < _parse_time(request.deadline)
        ):
            raise KubernetesActionExecutorError(
                "action.executor.credential-unavailable"
            )
        return lease


class KubernetesActionTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        *,
        ca_bundle_path: str | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        """Execute one bounded, TLS-verified, no-redirect API request."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None


class UrllibKubernetesActionTransport:
    """TLS-verifying Kubernetes API transport that never forwards redirects."""

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None,
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
            request = Request(
                url,
                data=body,
                headers=dict(headers),
                method=method,
            )
            with opener.open(request, timeout=timeout_seconds) as response:
                if response.status != 200:
                    raise KubernetesActionExecutorError(
                        "action.executor.response-invalid"
                    )
                if response.headers.get_content_type() != "application/json":
                    raise KubernetesActionExecutorError(
                        "action.executor.response-invalid"
                    )
                content = response.read(max_response_bytes + 1)
        except KubernetesActionExecutorError:
            raise
        except Exception:
            raise KubernetesActionExecutorError(
                "action.executor.apply-unknown",
                mutation_possible=method == "PATCH" and "dryRun=All" not in url,
            ) from None
        if len(content) > max_response_bytes:
            raise KubernetesActionExecutorError(
                "action.executor.response-invalid",
                mutation_possible=method == "PATCH" and "dryRun=All" not in url,
            )
        return content


class KubernetesRestartExecutor:
    """Restart one exact workload using server dry-run, verification, and rollback."""

    def __init__(
        self,
        registry: KubernetesActionIntegrationRegistry,
        credentials: CredentialBroker,
        clock: Clock,
        *,
        transport: KubernetesActionTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._registry = registry
        self._credentials = credentials
        self._clock = clock
        self._transport = transport or UrllibKubernetesActionTransport()
        self._sleeper = sleeper

    def execute(
        self,
        actor: ActorContext,
        proposal: Mapping[str, object],
        *,
        approval_id: str,
    ) -> ActionExecutionOutcome:
        del approval_id
        operation_ref = "kubernetes://unresolved/action"
        try:
            spec, parameters = self._proposal(proposal)
            integration = self._registry.resolve(
                actor.tenant_id, str(spec["integrationId"])
            )
            operation_ref = self._operation_ref(integration, parameters)
            return self._execute(actor, spec, parameters, integration, operation_ref)
        except KubernetesActionExecutorError as error:
            summary = _ERROR_SUMMARIES.get(
                error.code, "The Kubernetes action failed closed."
            )
            return ActionExecutionOutcome(
                outcome="failed",
                provider="kubernetes",
                operation_ref=operation_ref,
                verification_status=("failed" if error.mutation_possible else "not-run"),
                verification_summary=summary,
                error_code=error.code,
            )
        except Exception:
            return ActionExecutionOutcome(
                outcome="failed",
                provider="kubernetes",
                operation_ref=operation_ref,
                verification_status="failed",
                verification_summary=_ERROR_SUMMARIES[
                    "action.executor.apply-unknown"
                ],
                error_code="action.executor.apply-unknown",
            )

    def _execute(
        self,
        actor: ActorContext,
        spec: Mapping[str, object],
        parameters: Mapping[str, str],
        integration: KubernetesActionIntegration,
        operation_ref: str,
    ) -> ActionExecutionOutcome:
        dry_run = spec["dryRun"]
        if dry_run is False and not integration.live_execution_enabled:
            raise KubernetesActionExecutorError(
                "action.executor.configuration-unavailable"
            )
        provider_uid = spec.get("providerObjectUid")
        if (
            not isinstance(provider_uid, str)
            or _PROVIDER_UID.fullmatch(provider_uid) is None
        ):
            raise KubernetesActionExecutorError(
                "action.executor.target-binding-required"
            )
        now = _parse_time(self._clock.now())
        expires_at = _parse_time(str(spec["expiresAt"]))
        deadline = min(
            expires_at,
            now
            + timedelta(
                seconds=(
                    (2 * integration.verification_timeout_seconds)
                    + (6 * integration.request_timeout_seconds)
                )
            ),
        )
        if deadline <= now:
            raise KubernetesActionExecutorError(
                "action.executor.configuration-unavailable"
            )
        try:
            lease = self._credentials.resolve(
                CredentialLeaseRequest(
                    tenant_id=actor.tenant_id,
                    actor_id=actor.actor_id,
                    integration_id=integration.integration_id,
                    credential_ref=integration.credential_ref,
                    provider="kubernetes",
                    scopes=("resources:read", "workloads:patch"),
                    deadline=_format_time(deadline),
                )
            )
        except Exception:
            raise KubernetesActionExecutorError(
                "action.executor.credential-unavailable"
            ) from None
        if lease.scheme != "bearer" or not _safe_secret(lease.secret):
            raise KubernetesActionExecutorError(
                "action.executor.credential-unavailable"
            )
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {lease.secret}",
            "User-Agent": "iip-kubernetes-action-adapter/0.1.0",
        }
        path = self._path(integration, parameters)
        original = self._get_json(integration, path, headers, deadline)
        original_state = self._validate_workload(
            original, parameters, provider_uid
        )
        original_annotation = original_state["restartAnnotation"]
        restart_value = self._clock.now()
        patch = self._patch(
            parameters,
            str(original_state["resourceVersion"]),
            restart_value,
        )
        try:
            preflight = self._patch_json(
                integration,
                path,
                patch,
                headers,
                deadline,
                dry_run=True,
            )
            self._validate_patch_response(
                preflight, parameters, provider_uid, restart_value
            )
        except KubernetesActionExecutorError:
            raise KubernetesActionExecutorError(
                "action.executor.preflight-rejected"
            ) from None
        if dry_run is True:
            return ActionExecutionOutcome(
                outcome="dry-run",
                provider="kubernetes",
                operation_ref=operation_ref,
                verification_status="not-run",
                verification_summary=(
                    "Kubernetes accepted the exact restart patch with dryRun=All; "
                    "no mutation was persisted."
                ),
            )
        try:
            applied = self._patch_json(
                integration,
                path,
                patch,
                headers,
                deadline,
                dry_run=False,
            )
            applied_state = self._validate_patch_response(
                applied, parameters, provider_uid, restart_value
            )
        except KubernetesActionExecutorError:
            raise KubernetesActionExecutorError(
                "action.executor.apply-unknown", mutation_possible=True
            ) from None
        verification_deadline = min(
            deadline,
            _parse_time(self._clock.now())
            + timedelta(seconds=integration.verification_timeout_seconds),
        )
        verified, latest = self._verify_ready(
            integration,
            path,
            headers,
            parameters,
            provider_uid,
            int(applied_state["generation"]),
            verification_deadline,
            applied,
        )
        if verified:
            return ActionExecutionOutcome(
                outcome="succeeded",
                provider="kubernetes",
                operation_ref=operation_ref,
                verification_status="passed",
                verification_summary=(
                    "The workload controller observed the restarted generation and "
                    "all desired replicas became ready."
                ),
            )
        rollback = self._rollback(
            integration,
            path,
            headers,
            parameters,
            provider_uid,
            original_annotation,
            latest,
            deadline,
        )
        if rollback:
            return ActionExecutionOutcome(
                outcome="rolled-back",
                provider="kubernetes",
                operation_ref=operation_ref,
                verification_status="failed",
                verification_summary=_ERROR_SUMMARIES[
                    "action.executor.verification-failed"
                ],
                error_code="action.executor.verification-failed",
                rollback_status="succeeded",
                rollback_summary=(
                    "Kubernetes accepted the rollback and the prior restart annotation "
                    "was observed on the exact workload UID."
                ),
            )
        return ActionExecutionOutcome(
            outcome="failed",
            provider="kubernetes",
            operation_ref=operation_ref,
            verification_status="failed",
            verification_summary=_ERROR_SUMMARIES[
                "action.executor.rollback-failed"
            ],
            error_code="action.executor.rollback-failed",
            rollback_status="failed",
            rollback_summary=(
                "The executor could not verify restoration of the prior restart annotation."
            ),
        )

    @staticmethod
    def _proposal(
        proposal: Mapping[str, object],
    ) -> tuple[Mapping[str, object], Mapping[str, str]]:
        spec = proposal.get("spec")
        parameters = spec.get("parameters") if isinstance(spec, Mapping) else None
        if (
            proposal.get("kind") != "ActionProposal"
            or not isinstance(spec, Mapping)
            or spec.get("actionType") != "kubernetes.restart-workload"
            or not isinstance(spec.get("dryRun"), bool)
            or not isinstance(spec.get("expiresAt"), str)
            or not isinstance(spec.get("integrationId"), str)
            or not isinstance(parameters, Mapping)
            or set(parameters) != {"namespace", "workloadKind", "workloadName"}
            or any(not isinstance(value, str) for value in parameters.values())
        ):
            raise KubernetesActionExecutorError(
                "action.executor.configuration-unavailable"
            )
        return spec, parameters  # type: ignore[return-value]

    @staticmethod
    def _operation_ref(
        integration: KubernetesActionIntegration,
        parameters: Mapping[str, str],
    ) -> str:
        _, plural = _KIND_BINDINGS[parameters["workloadKind"]]
        return (
            f"kubernetes://{integration.cluster_external_id}/namespaces/"
            f"{parameters['namespace']}/{plural}/{parameters['workloadName']}"
        )

    @staticmethod
    def _path(
        integration: KubernetesActionIntegration,
        parameters: Mapping[str, str],
    ) -> str:
        namespace = parameters["namespace"]
        workload_kind = parameters["workloadKind"]
        name = parameters["workloadName"]
        if (
            namespace not in integration.namespaces
            or workload_kind not in integration.workload_kinds
            or _KUBERNETES_NAME.fullmatch(namespace) is None
            or _KUBERNETES_NAME.fullmatch(name) is None
        ):
            raise KubernetesActionExecutorError(
                "action.executor.target-out-of-scope"
            )
        _, plural = _KIND_BINDINGS[workload_kind]
        return (
            f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/"
            f"{plural}/{quote(name, safe='')}"
        )

    @staticmethod
    def _patch(
        parameters: Mapping[str, str],
        resource_version: str,
        annotation_value: str | None,
    ) -> dict[str, object]:
        return {
            "apiVersion": "apps/v1",
            "kind": _KIND_BINDINGS[parameters["workloadKind"]][0],
            "metadata": {
                "name": parameters["workloadName"],
                "namespace": parameters["namespace"],
                "resourceVersion": resource_version,
            },
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {_RESTART_ANNOTATION: annotation_value}
                    }
                }
            },
        }

    def _verify_ready(
        self,
        integration: KubernetesActionIntegration,
        path: str,
        headers: Mapping[str, str],
        parameters: Mapping[str, str],
        provider_uid: str,
        generation: int,
        deadline: datetime,
        initial: dict[str, object],
    ) -> tuple[bool, dict[str, object]]:
        latest = initial
        while True:
            self._validate_workload(latest, parameters, provider_uid)
            if self._ready(latest, generation):
                return True, latest
            remaining = (deadline - _parse_time(self._clock.now())).total_seconds()
            if remaining <= 0:
                return False, latest
            self._sleeper(
                min(integration.poll_interval_milliseconds / 1000.0, remaining)
            )
            if _parse_time(self._clock.now()) >= deadline:
                return False, latest
            latest = self._get_json(integration, path, headers, deadline)

    def _rollback(
        self,
        integration: KubernetesActionIntegration,
        path: str,
        headers: Mapping[str, str],
        parameters: Mapping[str, str],
        provider_uid: str,
        original_annotation: str | None,
        latest: Mapping[str, object],
        deadline: datetime,
    ) -> bool:
        try:
            state = self._validate_workload(latest, parameters, provider_uid)
            patch = self._patch(
                parameters,
                str(state["resourceVersion"]),
                original_annotation,
            )
            preflight = self._patch_json(
                integration, path, patch, headers, deadline, dry_run=True
            )
            self._validate_patch_response(
                preflight, parameters, provider_uid, original_annotation
            )
            applied = self._patch_json(
                integration, path, patch, headers, deadline, dry_run=False
            )
            restored = self._validate_patch_response(
                applied, parameters, provider_uid, original_annotation
            )
            generation = int(restored["generation"])
            while True:
                current = self._get_json(integration, path, headers, deadline)
                current_state = self._validate_workload(
                    current, parameters, provider_uid
                )
                if (
                    current_state["restartAnnotation"] == original_annotation
                    and int(current_state["observedGeneration"]) >= generation
                ):
                    return True
                remaining = (
                    deadline - _parse_time(self._clock.now())
                ).total_seconds()
                if remaining <= 0:
                    return False
                self._sleeper(
                    min(integration.poll_interval_milliseconds / 1000.0, remaining)
                )
        except Exception:
            return False

    def _get_json(
        self,
        integration: KubernetesActionIntegration,
        path: str,
        headers: Mapping[str, str],
        deadline: datetime,
    ) -> dict[str, object]:
        return self._request_json(
            integration, "GET", path, None, headers, deadline
        )

    def _patch_json(
        self,
        integration: KubernetesActionIntegration,
        path: str,
        patch: Mapping[str, object],
        headers: Mapping[str, str],
        deadline: datetime,
        *,
        dry_run: bool,
    ) -> dict[str, object]:
        parameters = {"fieldManager": "iip-action-executor"}
        if dry_run:
            parameters["dryRun"] = "All"
        patch_headers = dict(headers)
        patch_headers["Content-Type"] = "application/strategic-merge-patch+json"
        body = json.dumps(
            patch, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return self._request_json(
            integration,
            "PATCH",
            path + "?" + urlencode(parameters),
            body,
            patch_headers,
            deadline,
        )

    def _request_json(
        self,
        integration: KubernetesActionIntegration,
        method: str,
        path: str,
        body: bytes | None,
        headers: Mapping[str, str],
        deadline: datetime,
    ) -> dict[str, object]:
        remaining = (deadline - _parse_time(self._clock.now())).total_seconds()
        if remaining <= 0:
            raise KubernetesActionExecutorError(
                "action.executor.verification-failed",
                mutation_possible=method == "PATCH" and "dryRun=All" not in path,
            )
        content = self._transport.request(
            method,
            integration.endpoint + path,
            body,
            headers,
            ca_bundle_path=integration.ca_bundle_path,
            timeout_seconds=min(float(integration.request_timeout_seconds), remaining),
            max_response_bytes=integration.max_response_bytes,
        )
        try:
            document = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise KubernetesActionExecutorError(
                "action.executor.response-invalid",
                mutation_possible=method == "PATCH" and "dryRun=All" not in path,
            ) from None
        if not isinstance(document, dict):
            raise KubernetesActionExecutorError(
                "action.executor.response-invalid",
                mutation_possible=method == "PATCH" and "dryRun=All" not in path,
            )
        return document

    @staticmethod
    def _validate_workload(
        document: Mapping[str, object],
        parameters: Mapping[str, str],
        provider_uid: str,
    ) -> dict[str, object]:
        expected_kind, _ = _KIND_BINDINGS[parameters["workloadKind"]]
        metadata = document.get("metadata")
        spec = document.get("spec")
        status = document.get("status", {})
        template = spec.get("template") if isinstance(spec, dict) else None
        template_metadata = (
            template.get("metadata") if isinstance(template, dict) else None
        )
        annotations = (
            template_metadata.get("annotations")
            if isinstance(template_metadata, dict)
            else None
        )
        annotations = annotations if isinstance(annotations, dict) else {}
        if (
            document.get("apiVersion") != "apps/v1"
            or document.get("kind") != expected_kind
            or not isinstance(metadata, dict)
            or metadata.get("name") != parameters["workloadName"]
            or metadata.get("namespace") != parameters["namespace"]
            or metadata.get("uid") != provider_uid
            or not isinstance(metadata.get("resourceVersion"), str)
            or _RESOURCE_VERSION.fullmatch(metadata["resourceVersion"]) is None
            or not _integer(metadata.get("generation"), 1, 9_007_199_254_740_991)
            or not isinstance(spec, dict)
            or not isinstance(status, dict)
            or any(not isinstance(key, str) for key in annotations)
            or any(value is not None and not isinstance(value, str) for value in annotations.values())
        ):
            if isinstance(metadata, dict) and metadata.get("uid") != provider_uid:
                raise KubernetesActionExecutorError(
                    "action.executor.target-replaced"
                )
            raise KubernetesActionExecutorError(
                "action.executor.response-invalid"
            )
        observed = status.get("observedGeneration", 0)
        if not _integer(observed, 0, 9_007_199_254_740_991):
            raise KubernetesActionExecutorError(
                "action.executor.response-invalid"
            )
        annotation = annotations.get(_RESTART_ANNOTATION)
        return {
            "resourceVersion": metadata["resourceVersion"],
            "generation": metadata["generation"],
            "observedGeneration": observed,
            "restartAnnotation": annotation,
        }

    @classmethod
    def _validate_patch_response(
        cls,
        document: Mapping[str, object],
        parameters: Mapping[str, str],
        provider_uid: str,
        expected_annotation: str | None,
    ) -> dict[str, object]:
        state = cls._validate_workload(document, parameters, provider_uid)
        if state["restartAnnotation"] != expected_annotation:
            raise KubernetesActionExecutorError(
                "action.executor.response-invalid"
            )
        return state

    @staticmethod
    def _ready(document: Mapping[str, object], generation: int) -> bool:
        metadata = document.get("metadata")
        spec = document.get("spec")
        status = document.get("status")
        if not isinstance(metadata, dict) or not isinstance(spec, dict) or not isinstance(status, dict):
            return False
        observed = status.get("observedGeneration")
        if not _integer(observed, generation, 9_007_199_254_740_991):
            return False
        kind = document.get("kind")
        if kind == "DaemonSet":
            desired = status.get("desiredNumberScheduled")
            return (
                _integer(desired, 0, 1_000_000)
                and status.get("updatedNumberScheduled") == desired
                and status.get("numberReady") == desired
                and status.get("numberUnavailable", 0) == 0
            )
        desired = spec.get("replicas", 1)
        if not _integer(desired, 0, 1_000_000):
            return False
        if kind == "StatefulSet":
            revisions_match = (
                not status.get("currentRevision")
                or not status.get("updateRevision")
                or status.get("currentRevision") == status.get("updateRevision")
            )
            return (
                status.get("updatedReplicas", 0) == desired
                and status.get("readyReplicas", 0) == desired
                and status.get("currentReplicas", 0) == desired
                and revisions_match
            )
        return (
            status.get("updatedReplicas", 0) == desired
            and status.get("readyReplicas", 0) == desired
            and status.get("availableReplicas", 0) == desired
            and status.get("unavailableReplicas", 0) == 0
        )


def build_kubernetes_action_executor_from_environment(
    environment: Mapping[str, str],
    clock: Clock,
    credential_broker: CredentialBroker | None = None,
) -> KubernetesRestartExecutor:
    """Build the explicit live-capable executor from protected configuration."""

    integrations = environment.get("IIP_KUBERNETES_ACTIONS_INTEGRATIONS_JSON")
    credentials = environment.get("IIP_KUBERNETES_ACTIONS_CREDENTIALS_JSON")
    if integrations is None or (credential_broker is None and credentials is None):
        raise KubernetesActionsConfigurationError(
            "kubernetes.actions.configuration.required"
        )
    return KubernetesRestartExecutor(
        KubernetesActionIntegrationRegistry.from_json(integrations),
        credential_broker
        if credential_broker is not None
        else StaticKubernetesActionCredentialBroker.from_json(credentials),
        clock,
    )


def _json_document(encoded: object, error_code: str) -> dict[str, object]:
    if (
        not isinstance(encoded, str)
        or not 1 <= len(encoded.encode("utf-8")) <= _MAX_CONFIGURATION_BYTES
    ):
        raise KubernetesActionsConfigurationError(error_code)
    try:
        document = json.loads(encoded)
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise KubernetesActionsConfigurationError(error_code) from None
    if not isinstance(document, dict):
        raise KubernetesActionsConfigurationError(error_code)
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
        or any(
            not isinstance(item, str)
            or _KUBERNETES_NAME.fullmatch(item) is None
            for item in value
        )
        or len(value) != len(set(value))
    ):
        raise TypeError
    return tuple(sorted(value))


def _safe_secret(value: object) -> bool:
    return (
        isinstance(value, str)
        and 16 <= len(value) <= 16_384
        and value.isascii()
        and not any(character.isspace() for character in value)
    )


def _integer(value: object, minimum: int, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise KubernetesActionExecutorError(
            "action.executor.configuration-unavailable"
        ) from None
    if parsed.tzinfo is None:
        raise KubernetesActionExecutorError(
            "action.executor.configuration-unavailable"
        )
    return parsed


def _format_time(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
