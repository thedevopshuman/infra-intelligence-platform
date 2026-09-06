#!/usr/bin/env python3
"""Qualify customer worker and receiver processing across bounded pod Evictions."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import secrets
import ssl
import stat
import tempfile
import time
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from jsonschema import Draft202012Validator, FormatChecker
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)

import qualify_customer_continuity as customer
import qualify_ingress_availability as ingress


ROOT = Path(__file__).resolve().parents[1]
PROFILE_SCHEMA = (
    ROOT / "contracts/schemas/customer-processing-qualification-profile.schema.json"
)
REPORT_SCHEMA = (
    ROOT
    / "contracts/schemas/customer-processing-continuity-qualification-report.schema.json"
)
API_VERSION = "iip.platform/v1alpha1"
KIND = "CustomerProcessingContinuityQualificationReport"
QUALIFICATION_LEVEL = "customer-worker-receiver-pod-eviction-v1"
PHASES = ("baseline", "worker-disruption", "receiver-disruption", "recovery")
COMPONENTS = ("workflow-worker", "otlp-receiver")
CHECK_IDS = (
    "source-binding",
    "minimized-output",
    "explicit-context",
    "immutable-image",
    "api-verified-https",
    "receiver-mutual-tls",
    "direct-no-proxy-no-redirect",
    "worker-redundant-capacity",
    "receiver-redundant-capacity",
    "zero-unavailable-rollouts",
    "component-pdbs",
    "uid-preconditioned-evictions",
    "worker-reduced-capacity-observed",
    "receiver-reduced-capacity-observed",
    "api-zero-failure",
    "receiver-zero-failure",
    "receiver-durable-intake",
    "workflow-baseline-completion",
    "workflow-worker-disruption-overlap",
    "workflow-receiver-disruption-overlap",
    "workflow-recovery-completion",
    "component-recovery",
)
LIMITATIONS = (
    "single-cluster",
    "sequential-single-pod-evictions",
    "synthetic-qualification-traffic",
    "shared-database-failure-not-qualified",
    "node-zone-region-failure-not-qualified",
    "sustained-customer-load-not-qualified",
)
REPORT_ID = re.compile(r"^cpcq_[a-f0-9]{32}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
MAX_RESPONSE_BYTES = 65_536
DEFAULT_ATTEMPTS_PER_PHASE = 20
DEFAULT_PROBE_INTERVAL_MILLISECONDS = 250
DEFAULT_WORKFLOW_TIMEOUT_MILLISECONDS = 60_000
DEFAULT_RECOVERY_TIMEOUT_MILLISECONDS = 120_000
DEFAULT_REQUEST_TIMEOUT_MILLISECONDS = 2_000
FORBIDDEN_RETAINED_KEYS = frozenset(
    {
        "url",
        "baseUrl",
        "hostname",
        "ipAddress",
        "context",
        "contextName",
        "namespace",
        "namespaceName",
        "deployment",
        "deploymentName",
        "pod",
        "podName",
        "podUid",
        "uid",
        "selector",
        "tenantId",
        "actorId",
        "resourceUid",
        "token",
        "credential",
        "certificate",
        "responseBody",
        "rawError",
        "metricName",
        "serviceName",
    }
)


class CustomerProcessingContinuityError(RuntimeError):
    """Stable customer processing-continuity failure."""


def _fail(code: str) -> None:
    raise CustomerProcessingContinuityError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _timestamp(value: datetime | None = None) -> str:
    instant = value or datetime.now(timezone.utc)
    return instant.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
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


def _integer(
    value: object, *, minimum: int, maximum: int, code: str
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        _fail(code)
    return value


def _load_schema(path: Path, code: str) -> Mapping[str, Any]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), code)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)


def _validate_schema(document: Mapping[str, Any], schema_path: Path, code: str) -> None:
    schema = _load_schema(schema_path, code)
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            document
        )
    )
    if errors:
        _fail(code)


def _regular_file(
    path: Path,
    *,
    code: str,
    maximum_bytes: int,
    protected: bool = False,
) -> Path:
    candidate = path.expanduser()
    try:
        if candidate.is_symlink():
            _fail(code)
        details = candidate.stat()
    except OSError:
        _fail(code)
    if (
        not stat.S_ISREG(details.st_mode)
        or not 1 <= details.st_size <= maximum_bytes
        or (protected and details.st_mode & 0o077)
    ):
        _fail(code)
    return candidate.absolute()


def _load_profile(path: Path) -> tuple[Mapping[str, Any], str]:
    source = _regular_file(
        path,
        code="customer-processing-continuity.profile.invalid",
        maximum_bytes=65_536,
        protected=True,
    )
    try:
        profile = _mapping(
            json.loads(source.read_text(encoding="utf-8")),
            "customer-processing-continuity.profile.invalid",
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-processing-continuity.profile.invalid")
    _validate_schema(
        profile,
        PROFILE_SCHEMA,
        "customer-processing-continuity.profile.invalid",
    )
    return profile, _digest_value(profile)


def _protected_token(path: Path, code: str) -> str:
    source = _regular_file(path, code=code, maximum_bytes=8194, protected=True)
    try:
        return ingress._load_token(source)
    except ingress.IngressQualificationError:
        _fail(code)


def _tls_file(path: Path, code: str, *, protected: bool = False) -> Path:
    return _regular_file(
        path, code=code, maximum_bytes=1024 * 1024, protected=protected
    )


def _https_target(raw: str) -> tuple[str, str]:
    try:
        base_url, _, target_digest = ingress._checked_url("customer-ingress", raw)
    except ingress.IngressQualificationError:
        _fail("customer-processing-continuity.target.invalid")
    return base_url, target_digest


class _DenyRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, url):
        del request, file_pointer, code, message, headers, url
        return None


def _opener(
    *,
    ca_file: Path | None,
    client_cert_file: Path | None = None,
    client_key_file: Path | None = None,
) -> tuple[Any, str]:
    resolved_ca = (
        _tls_file(ca_file, "customer-processing-continuity.tls.ca-invalid")
        if ca_file is not None
        else None
    )
    try:
        context = ssl.create_default_context(
            cafile=str(resolved_ca) if resolved_ca is not None else None
        )
        if client_cert_file is not None or client_key_file is not None:
            if client_cert_file is None or client_key_file is None:
                _fail("customer-processing-continuity.tls.client-identity-invalid")
            certificate = _tls_file(
                client_cert_file,
                "customer-processing-continuity.tls.client-identity-invalid",
            )
            key = _tls_file(
                client_key_file,
                "customer-processing-continuity.tls.client-identity-invalid",
                protected=True,
            )
            context.load_cert_chain(str(certificate), str(key))
    except (OSError, ssl.SSLError):
        _fail("customer-processing-continuity.tls.invalid")
    return (
        build_opener(
            ProxyHandler({}), _DenyRedirects(), HTTPSHandler(context=context)
        ),
        "custom" if resolved_ca is not None else "system",
    )


def _request(
    opener: Any,
    *,
    url: str,
    token: str,
    method: str,
    body: bytes | None,
    content_type: str,
    timeout_milliseconds: int,
) -> tuple[int, str, bytes] | None:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": content_type,
        "Connection": "close",
        "User-Agent": "iip-customer-processing-continuity/1",
    }
    if body is not None:
        headers["Content-Type"] = content_type
    request = Request(url, headers=headers, data=body, method=method)
    try:
        with opener.open(request, timeout=timeout_milliseconds / 1000) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                return None
            return (
                int(getattr(response, "status", 0)),
                response.headers.get_content_type(),
                payload,
            )
    except HTTPError as exc:
        exc.close()
        return None
    except (URLError, OSError, TimeoutError, ssl.SSLError, HTTPException, ValueError):
        return None


def _profile_values(profile: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    metadata = _mapping(
        profile.get("metadata"), "customer-processing-continuity.profile.invalid"
    )
    spec = _mapping(
        profile.get("spec"), "customer-processing-continuity.profile.invalid"
    )
    metric = _mapping(
        spec.get("metric"), "customer-processing-continuity.profile.invalid"
    )
    investigation = _mapping(
        spec.get("investigation"),
        "customer-processing-continuity.profile.invalid",
    )
    return metadata, metric, investigation


class ProcessingClient:
    """Direct API and mTLS receiver client with no ambient proxy or redirects."""

    def __init__(
        self,
        *,
        api_base_url: str,
        api_token_file: Path,
        otlp_base_url: str,
        otlp_token_file: Path,
        otlp_client_cert_file: Path,
        otlp_client_key_file: Path,
        profile: Mapping[str, Any],
        subject: Mapping[str, str],
        api_ca_file: Path | None,
        otlp_ca_file: Path | None,
        request_timeout_milliseconds: int,
        qualification_id: str = "customer-processing-continuity",
    ) -> None:
        self.api_base_url, self.api_target_digest = _https_target(api_base_url)
        self.otlp_base_url, self.otlp_target_digest = _https_target(otlp_base_url)
        self.api_token = _protected_token(
            api_token_file,
            "customer-processing-continuity.api-credential.invalid",
        )
        self.otlp_token = _protected_token(
            otlp_token_file,
            "customer-processing-continuity.otlp-credential.invalid",
        )
        self.api_opener, self.api_ca_source = _opener(ca_file=api_ca_file)
        self.otlp_opener, self.otlp_ca_source = _opener(
            ca_file=otlp_ca_file,
            client_cert_file=otlp_client_cert_file,
            client_key_file=otlp_client_key_file,
        )
        self.profile = profile
        self.subject = subject
        self.request_timeout_milliseconds = request_timeout_milliseconds
        if qualification_id not in {
            "customer-processing-continuity",
            "customer-postgresql-continuity",
        }:
            _fail("customer-processing-continuity.client.invalid")
        self.qualification_id = qualification_id

    def _runtime_identity_valid(self, payload: bytes) -> bool:
        try:
            document = json.loads(payload)
            spec = document["spec"]
            return (
                document["apiVersion"] == API_VERSION
                and document["kind"] == "RuntimeVersionReport"
                and spec["application"]["version"]
                == self.subject["applicationVersion"]
                and spec["contracts"]["apiVersion"] == API_VERSION
                and spec["storage"]["requiredMigration"]
                == self.subject["requiredMigration"]
                and spec["build"]
                == {"mode": "release", "revision": self.subject["sourceRevision"]}
                and spec["deployment"]
                == {
                    "helmChartVersion": self.subject["chartVersion"],
                    "imageDigest": self.subject["imageDigest"],
                }
            )
        except (KeyError, TypeError, json.JSONDecodeError):
            return False

    def _metric_payload(self) -> bytes:
        _, metric_profile, _ = _profile_values(self.profile)
        request = ExportMetricsServiceRequest()
        resource_metrics = request.resource_metrics.add()
        resource_metrics.resource.attributes.add(
            key="service.name"
        ).value.string_value = str(metric_profile["serviceName"])
        scope_metrics = resource_metrics.scope_metrics.add()
        scope_metrics.scope.name = "iip." + self.qualification_id
        metric = scope_metrics.metrics.add(
            name=str(metric_profile["name"]), unit=str(metric_profile["unit"])
        )
        metric.sum.aggregation_temporality = 2
        metric.sum.is_monotonic = True
        point = metric.sum.data_points.add()
        point.time_unix_nano = time.time_ns()
        point.as_int = 1
        return request.SerializeToString()

    def validate_resource(self) -> None:
        metadata, _, _ = _profile_values(self.profile)
        profile_spec = _mapping(
            self.profile.get("spec"),
            "customer-processing-continuity.profile.invalid",
        )
        resource_uid = str(profile_spec["resourceUid"])
        response = _request(
            self.api_opener,
            url=self.api_base_url + "/v1/resources/" + resource_uid,
            token=self.api_token,
            method="GET",
            body=None,
            content_type="application/json",
            timeout_milliseconds=self.request_timeout_milliseconds,
        )
        if response is None:
            _fail("customer-processing-continuity.resource.unavailable")
        status, content_type, body = response
        try:
            document = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            _fail("customer-processing-continuity.resource.invalid")
        resource_metadata = (
            document.get("metadata") if isinstance(document, Mapping) else None
        )
        if (
            status != 200
            or content_type != "application/json"
            or not isinstance(document, Mapping)
            or document.get("apiVersion") != API_VERSION
            or document.get("kind") != "Resource"
            or not isinstance(resource_metadata, Mapping)
            or resource_metadata.get("uid") != resource_uid
            or resource_metadata.get("tenantId") != metadata.get("tenantId")
        ):
            _fail("customer-processing-continuity.resource.invalid")

    def probe_cycle(self) -> tuple[bool, bool]:
        api_response = _request(
            self.api_opener,
            url=self.api_base_url + "/v1/system/version",
            token=self.api_token,
            method="GET",
            body=None,
            content_type="application/json",
            timeout_milliseconds=self.request_timeout_milliseconds,
        )
        api_ok = (
            api_response is not None
            and api_response[0] == 200
            and api_response[1] == "application/json"
            and self._runtime_identity_valid(api_response[2])
        )
        otlp_response = _request(
            self.otlp_opener,
            url=self.otlp_base_url + "/v1/metrics",
            token=self.otlp_token,
            method="POST",
            body=self._metric_payload(),
            content_type="application/x-protobuf",
            timeout_milliseconds=self.request_timeout_milliseconds,
        )
        otlp_ok = (
            otlp_response is not None
            and otlp_response[0] == 200
            and otlp_response[1] == "application/x-protobuf"
            and otlp_response[2] == b""
        )
        return api_ok, otlp_ok

    def start_workflow(self, phase: str) -> tuple[str, float]:
        metadata, _, investigation = _profile_values(self.profile)
        profile_spec = _mapping(
            self.profile.get("spec"),
            "customer-processing-continuity.profile.invalid",
        )
        investigation_id = "inv_" + secrets.token_hex(16)
        now = datetime.now(timezone.utc)
        request_document = {
            "apiVersion": API_VERSION,
            "kind": "InvestigationRequest",
            "metadata": {
                "id": investigation_id,
                "tenantId": metadata["tenantId"],
                "actorId": metadata["actorId"],
                "requestedAt": _timestamp(now),
                "correlationId": self.qualification_id + "-" + phase,
            },
            "spec": {
                "question": "Can the installed platform complete bounded queued work?",
                "trigger": {
                    "type": "scheduled",
                    "source": "urn:iip:qualification:" + self.qualification_id,
                    "summary": self.qualification_id.replace("-", " ").title(),
                },
                "scope": {
                    "resourceUids": [profile_spec["resourceUid"]],
                    "timeRange": {
                        "start": _timestamp(now.replace(microsecond=0) - timedelta(hours=1)),
                        "end": _timestamp(now),
                    },
                },
                "agentSelector": {"id": "incident-investigator", "version": "0.1.0"},
                "evidenceTypes": list(investigation["evidenceTypes"]),
                "allowedTools": list(investigation["allowedTools"]),
                "budgets": {
                    "maxToolCalls": investigation["maxToolCalls"],
                    "maxWallTimeSeconds": investigation["maxWallTimeSeconds"],
                    "maxModelTokens": 0,
                    "maxCostUsd": 0,
                    "maxEvidenceItems": investigation["maxEvidenceItems"],
                    "maxIterations": investigation["maxIterations"],
                },
                "maxAuthority": "propose",
                "priority": "normal",
            },
        }
        started = time.monotonic()
        response = _request(
            self.api_opener,
            url=self.api_base_url + "/v1/investigation-jobs",
            token=self.api_token,
            method="POST",
            body=_canonical(request_document),
            content_type="application/json",
            timeout_milliseconds=self.request_timeout_milliseconds,
        )
        if response is None:
            _fail("customer-processing-continuity.workflow.submission-failed")
        status, content_type, body = response
        try:
            document = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            _fail("customer-processing-continuity.workflow.submission-invalid")
        response_metadata = (
            document.get("metadata") if isinstance(document, Mapping) else None
        )
        if (
            status != 202
            or content_type != "application/json"
            or not isinstance(document, Mapping)
            or document.get("apiVersion") != API_VERSION
            or document.get("kind") != "InvestigationJobStatus"
            or not isinstance(response_metadata, Mapping)
            or response_metadata.get("id") != investigation_id
            or response_metadata.get("tenantId") != metadata.get("tenantId")
        ):
            _fail("customer-processing-continuity.workflow.submission-invalid")
        return investigation_id, started

    def finish_workflow(
        self,
        investigation_id: str,
        started: float,
        maximum_milliseconds: int,
        sleeper: Callable[[float], None],
    ) -> dict[str, int]:
        metadata, _, _ = _profile_values(self.profile)
        polls = 0
        deadline = started + maximum_milliseconds / 1000
        while time.monotonic() <= deadline:
            polls += 1
            response = _request(
                self.api_opener,
                url=self.api_base_url + "/v1/investigation-jobs/" + investigation_id,
                token=self.api_token,
                method="GET",
                body=None,
                content_type="application/json",
                timeout_milliseconds=self.request_timeout_milliseconds,
            )
            if response is None:
                sleeper(0.2)
                continue
            status, content_type, body = response
            try:
                document = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                _fail("customer-processing-continuity.workflow.status-invalid")
            response_metadata = (
                document.get("metadata") if isinstance(document, Mapping) else None
            )
            spec = document.get("spec") if isinstance(document, Mapping) else None
            state = spec.get("state") if isinstance(spec, Mapping) else None
            if (
                status != 200
                or content_type != "application/json"
                or not isinstance(document, Mapping)
                or document.get("apiVersion") != API_VERSION
                or document.get("kind") != "InvestigationJobStatus"
                or not isinstance(response_metadata, Mapping)
                or response_metadata.get("id") != investigation_id
                or response_metadata.get("tenantId") != metadata.get("tenantId")
                or state not in {"queued", "running", "completed", "failed", "cancelled"}
            ):
                _fail("customer-processing-continuity.workflow.status-invalid")
            if state in {"failed", "cancelled"}:
                elapsed = min(
                    maximum_milliseconds,
                    max(0, math.ceil((time.monotonic() - started) * 1000)),
                )
                return {
                    "workflowSubmitted": 1,
                    "workflowCompleted": 0,
                    "workflowFailures": 1,
                    "workflowPollAttempts": polls,
                    "workflowCompletionMilliseconds": elapsed,
                }
            if state == "completed":
                report_response = _request(
                    self.api_opener,
                    url=self.api_base_url + "/v1/investigations/" + investigation_id,
                    token=self.api_token,
                    method="GET",
                    body=None,
                    content_type="application/json",
                    timeout_milliseconds=self.request_timeout_milliseconds,
                )
                if report_response is None:
                    _fail("customer-processing-continuity.workflow.report-invalid")
                report_status, report_type, report_body = report_response
                try:
                    report = json.loads(report_body)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    _fail("customer-processing-continuity.workflow.report-invalid")
                report_metadata = (
                    report.get("metadata") if isinstance(report, Mapping) else None
                )
                report_spec = report.get("spec") if isinstance(report, Mapping) else None
                if (
                    report_status != 200
                    or report_type != "application/json"
                    or not isinstance(report, Mapping)
                    or report.get("apiVersion") != API_VERSION
                    or report.get("kind") != "InvestigationReport"
                    or not isinstance(report_metadata, Mapping)
                    or report_metadata.get("id") != investigation_id
                    or report_metadata.get("tenantId") != metadata.get("tenantId")
                    or not isinstance(report_spec, Mapping)
                    or report_spec.get("outcome") not in {"conclusive", "inconclusive"}
                ):
                    _fail("customer-processing-continuity.workflow.report-invalid")
                elapsed = min(
                    maximum_milliseconds,
                    max(0, math.ceil((time.monotonic() - started) * 1000)),
                )
                return {
                    "workflowSubmitted": 1,
                    "workflowCompleted": 1,
                    "workflowFailures": 0,
                    "workflowPollAttempts": polls,
                    "workflowCompletionMilliseconds": elapsed,
                }
            sleeper(0.2)
        return {
            "workflowSubmitted": 1,
            "workflowCompleted": 0,
            "workflowFailures": 1,
            "workflowPollAttempts": max(1, polls),
            "workflowCompletionMilliseconds": maximum_milliseconds,
        }


def _phase_measurement(
    client: ProcessingClient,
    *,
    phase: str,
    attempts: int,
    interval_milliseconds: int,
    maximum_workflow_milliseconds: int,
    submitted_during_reduced_capacity: bool,
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    investigation_id, workflow_started = client.start_workflow(phase)
    api_successes = 0
    receiver_successes = 0
    for index in range(attempts):
        api_ok, receiver_ok = client.probe_cycle()
        api_successes += int(api_ok)
        receiver_successes += int(receiver_ok)
        if index + 1 < attempts:
            sleeper(interval_milliseconds / 1000)
    workflow = client.finish_workflow(
        investigation_id,
        workflow_started,
        maximum_workflow_milliseconds,
        sleeper,
    )
    return {
        "id": phase,
        "apiAttempts": attempts,
        "apiSuccesses": api_successes,
        "apiFailures": attempts - api_successes,
        "receiverAttempts": attempts,
        "receiverSuccesses": receiver_successes,
        "receiverFailures": attempts - receiver_successes,
        **workflow,
        "submittedDuringReducedCapacity": submitted_during_reduced_capacity,
    }


def _wait_for_reduced_capacity(
    client: customer.KubectlClient,
    *,
    original_pod_uid: str,
    maximum_milliseconds: int,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> customer.DeploymentObservation:
    deadline = monotonic() + maximum_milliseconds / 1000
    while monotonic() <= deadline:
        try:
            return client.observe(
                original_pod_uid=original_pod_uid,
                require_reduced_capacity=True,
            )
        except customer.CustomerContinuityQualificationError as exc:
            if str(exc) != "customer-continuity.kubernetes.reduced-capacity-not-observed":
                _fail("customer-processing-continuity.kubernetes.state-invalid")
        sleeper(0.2)
    _fail("customer-processing-continuity.kubernetes.reduced-capacity-timeout")


def _wait_for_recovery(
    client: customer.KubectlClient,
    *,
    original_pod_uid: str,
    maximum_milliseconds: int,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> tuple[customer.DeploymentObservation, int]:
    started = monotonic()
    deadline = started + maximum_milliseconds / 1000
    while monotonic() <= deadline:
        try:
            observation = client.observe(original_pod_uid=original_pod_uid)
            elapsed = max(0, math.ceil((monotonic() - started) * 1000))
            return observation, elapsed
        except customer.CustomerContinuityQualificationError as exc:
            if str(exc) not in {
                "customer-continuity.kubernetes.capacity-not-ready",
                "customer-continuity.kubernetes.original-pod-present",
            }:
                _fail("customer-processing-continuity.kubernetes.state-invalid")
            sleeper(0.5)
    _fail("customer-processing-continuity.kubernetes.recovery-timeout")


def _component_measurement(
    component_id: str,
    before: customer.DeploymentObservation,
    during: customer.DeploymentObservation,
    after: customer.DeploymentObservation,
    recovery_milliseconds: int,
) -> dict[str, Any]:
    return {
        "id": component_id,
        "desiredReplicas": before.desired_replicas,
        "readyReplicasBefore": before.ready_replicas,
        "readyReplicasDuring": during.ready_replicas,
        "readyReplicasAfter": after.ready_replicas,
        "maxUnavailable": before.max_unavailable,
        "pdbMinAvailable": before.pdb_min_available,
        "pdbDisruptionsAllowedBefore": before.pdb_disruptions_allowed,
        "evictionAccepted": True,
        "reducedCapacityObserved": True,
        "originalPodReplaced": after.pod_uid != before.pod_uid,
        "recoveryMilliseconds": recovery_milliseconds,
    }


def _check(identifier: str, passed: bool, code: str) -> dict[str, str]:
    result = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        result["errorCode"] = code
    return result


def _derived_checks(
    *,
    subject: Mapping[str, Any],
    bindings: Mapping[str, Any],
    environment: Mapping[str, Any],
    receiver_intake: Mapping[str, Any],
    objective: Mapping[str, Any],
    phases: Sequence[Mapping[str, Any]],
    components: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    phase_by_id = {phase.get("id"): phase for phase in phases}
    component_by_id = {component.get("id"): component for component in components}
    if (
        list(phase_by_id) != list(PHASES)
        or list(component_by_id) != list(COMPONENTS)
    ):
        _fail("customer-processing-continuity.report.measurements-invalid")
    worker = component_by_id["workflow-worker"]
    receiver = component_by_id["otlp-receiver"]
    component_bindings = bindings.get("components")
    immutable_component_bindings = (
        isinstance(component_bindings, list)
        and [
            item.get("id")
            for item in component_bindings
            if isinstance(item, Mapping)
        ]
        == list(COMPONENTS)
        and all(
            isinstance(item, Mapping)
            and DIGEST.fullmatch(str(item.get("deploymentBindingDigest")))
            and DIGEST.fullmatch(str(item.get("evictedPodBindingDigest")))
            for item in component_bindings
        )
    )
    minimum_attempts = objective["minimumProbeAttemptsPerPhase"]
    max_workflow = objective["maximumWorkflowCompletionMilliseconds"]
    max_recovery = objective["maximumRecoveryMilliseconds"]
    total_api = sum(phase["apiAttempts"] for phase in phases)
    failed_api = sum(phase["apiFailures"] for phase in phases)
    total_receiver = sum(phase["receiverAttempts"] for phase in phases)
    failed_receiver = sum(phase["receiverFailures"] for phase in phases)
    maximum_recovery = max(
        component["recoveryMilliseconds"] for component in components
    )

    def workflow_passes(phase_id: str, *, reduced: bool) -> bool:
        phase = phase_by_id[phase_id]
        return (
            phase["workflowSubmitted"] == 1
            and phase["workflowCompleted"] == 1
            and phase["workflowFailures"] == 0
            and phase["workflowCompletionMilliseconds"] <= max_workflow
            and phase["submittedDuringReducedCapacity"] is reduced
        )

    return [
        _check(
            "source-binding",
            isinstance(subject.get("sourceRevision"), str),
            "customer-processing-continuity.source.invalid",
        ),
        _check(
            "minimized-output",
            True,
            "customer-processing-continuity.output.not-minimized",
        ),
        _check(
            "explicit-context",
            all(
                DIGEST.fullmatch(str(bindings.get(field)))
                for field in (
                    "apiTargetBindingDigest",
                    "otlpTargetBindingDigest",
                    "kubernetesContextBindingDigest",
                    "namespaceBindingDigest",
                    "profileDigest",
                )
            ),
            "customer-processing-continuity.context.invalid",
        ),
        _check(
            "immutable-image",
            DIGEST.fullmatch(str(subject.get("imageDigest"))) is not None,
            "customer-processing-continuity.image.invalid",
        ),
        _check(
            "api-verified-https",
            environment.get("apiTransport") == "verified-https",
            "customer-processing-continuity.api.transport-invalid",
        ),
        _check(
            "receiver-mutual-tls",
            environment.get("otlpTransport") == "mutual-tls-https",
            "customer-processing-continuity.receiver.transport-invalid",
        ),
        _check(
            "direct-no-proxy-no-redirect",
            environment.get("proxyMode") == "disabled"
            and environment.get("redirectMode") == "deny",
            "customer-processing-continuity.client.invalid",
        ),
        _check(
            "worker-redundant-capacity",
            worker["readyReplicasBefore"] == worker["desiredReplicas"] >= 2,
            "customer-processing-continuity.worker.capacity-invalid",
        ),
        _check(
            "receiver-redundant-capacity",
            receiver["readyReplicasBefore"] == receiver["desiredReplicas"] >= 2,
            "customer-processing-continuity.receiver.capacity-invalid",
        ),
        _check(
            "zero-unavailable-rollouts",
            worker["maxUnavailable"] == receiver["maxUnavailable"] == 0,
            "customer-processing-continuity.rollout.invalid",
        ),
        _check(
            "component-pdbs",
            worker["pdbMinAvailable"] >= 1
            and receiver["pdbMinAvailable"] >= 1
            and worker["pdbDisruptionsAllowedBefore"] >= 1
            and receiver["pdbDisruptionsAllowedBefore"] >= 1,
            "customer-processing-continuity.pdb.invalid",
        ),
        _check(
            "uid-preconditioned-evictions",
            environment.get("evictionApi") == "policy/v1"
            and immutable_component_bindings,
            "customer-processing-continuity.eviction.invalid",
        ),
        _check(
            "worker-reduced-capacity-observed",
            worker["reducedCapacityObserved"] is True
            and 1 <= worker["readyReplicasDuring"] < worker["desiredReplicas"],
            "customer-processing-continuity.worker.reduced-capacity-missing",
        ),
        _check(
            "receiver-reduced-capacity-observed",
            receiver["reducedCapacityObserved"] is True
            and 1
            <= receiver["readyReplicasDuring"]
            < receiver["desiredReplicas"],
            "customer-processing-continuity.receiver.reduced-capacity-missing",
        ),
        _check(
            "api-zero-failure",
            total_api >= minimum_attempts * len(PHASES) and failed_api == 0,
            "customer-processing-continuity.api.failure",
        ),
        _check(
            "receiver-zero-failure",
            total_receiver >= minimum_attempts * len(PHASES)
            and failed_receiver == 0,
            "customer-processing-continuity.receiver.failure",
        ),
        _check(
            "receiver-durable-intake",
            failed_receiver == 0
            and receiver_intake.get("signal") == "metric"
            and receiver_intake.get("payload") == "non-empty-otlp-protobuf"
            and receiver_intake.get("successBoundary")
            == "postgresql-commit-before-http-200",
            "customer-processing-continuity.receiver.persistence-failed",
        ),
        _check(
            "workflow-baseline-completion",
            workflow_passes("baseline", reduced=False),
            "customer-processing-continuity.workflow.baseline-failed",
        ),
        _check(
            "workflow-worker-disruption-overlap",
            workflow_passes("worker-disruption", reduced=True),
            "customer-processing-continuity.workflow.worker-disruption-failed",
        ),
        _check(
            "workflow-receiver-disruption-overlap",
            workflow_passes("receiver-disruption", reduced=True),
            "customer-processing-continuity.workflow.receiver-disruption-failed",
        ),
        _check(
            "workflow-recovery-completion",
            workflow_passes("recovery", reduced=False),
            "customer-processing-continuity.workflow.recovery-failed",
        ),
        _check(
            "component-recovery",
            worker["evictionAccepted"] is True
            and receiver["evictionAccepted"] is True
            and worker["originalPodReplaced"] is True
            and receiver["originalPodReplaced"] is True
            and worker["readyReplicasAfter"] == worker["desiredReplicas"]
            and receiver["readyReplicasAfter"] == receiver["desiredReplicas"]
            and maximum_recovery <= max_recovery,
            "customer-processing-continuity.recovery.objective-missed",
        ),
    ]


def _report_identifier(metadata: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    return "cpcq_" + hashlib.sha256(
        _canonical({"metadata": metadata, "spec": spec})
    ).hexdigest()[:32]


def _walk_keys(value: object) -> Sequence[str]:
    keys: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if isinstance(key, str):
                keys.append(key)
            keys.extend(_walk_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.extend(_walk_keys(nested))
    return keys


def build_report(
    *,
    revision: str,
    repository: Mapping[str, str],
    image_digest: str,
    api_target_digest: str,
    otlp_target_digest: str,
    context: str,
    namespace: str,
    profile_digest: str,
    worker_deployment: str,
    receiver_deployment: str,
    kubernetes_version: str,
    api_ca_source: str,
    otlp_ca_source: str,
    objective: Mapping[str, int],
    phases: Sequence[Mapping[str, Any]],
    worker_before: customer.DeploymentObservation,
    worker_during: customer.DeploymentObservation,
    worker_after: customer.DeploymentObservation,
    worker_recovery_milliseconds: int,
    receiver_before: customer.DeploymentObservation,
    receiver_during: customer.DeploymentObservation,
    receiver_after: customer.DeploymentObservation,
    receiver_recovery_milliseconds: int,
    started_at: datetime,
    completed_at: datetime,
) -> dict[str, Any]:
    subject = {
        "applicationVersion": repository["applicationVersion"],
        "chartVersion": repository["chartVersion"],
        "contractsApiVersion": API_VERSION,
        "requiredMigration": repository["requiredMigration"],
        "sourceRevision": revision,
        "imageDigest": image_digest,
    }
    component_measurements = [
        _component_measurement(
            "workflow-worker",
            worker_before,
            worker_during,
            worker_after,
            worker_recovery_milliseconds,
        ),
        _component_measurement(
            "otlp-receiver",
            receiver_before,
            receiver_during,
            receiver_after,
            receiver_recovery_milliseconds,
        ),
    ]
    phase_by_id = {phase.get("id"): phase for phase in phases}
    if set(phase_by_id) != set(PHASES) or len(phases) != len(PHASES):
        _fail("customer-processing-continuity.phases.invalid")
    ordered_phases = [dict(phase_by_id[phase]) for phase in PHASES]
    try:
        total_api = sum(phase["apiAttempts"] for phase in ordered_phases)
        failed_api = sum(phase["apiFailures"] for phase in ordered_phases)
        total_receiver = sum(phase["receiverAttempts"] for phase in ordered_phases)
        failed_receiver = sum(phase["receiverFailures"] for phase in ordered_phases)
        completed_workflows = sum(
            phase["workflowCompleted"] for phase in ordered_phases
        )
        failed_workflows = sum(
            phase["workflowFailures"] for phase in ordered_phases
        )
        max_workflow = max(
            phase["workflowCompletionMilliseconds"] for phase in ordered_phases
        )
    except (KeyError, TypeError):
        _fail("customer-processing-continuity.phases.invalid")
    max_recovery = max(worker_recovery_milliseconds, receiver_recovery_milliseconds)
    bindings = {
        "apiTargetBindingDigest": api_target_digest,
        "otlpTargetBindingDigest": otlp_target_digest,
        "kubernetesContextBindingDigest": _digest_value(context),
        "namespaceBindingDigest": _digest_value(namespace),
        "profileDigest": profile_digest,
        "components": [
            {
                "id": "workflow-worker",
                "deploymentBindingDigest": _digest_value(worker_deployment),
                "evictedPodBindingDigest": _digest_value(worker_before.pod_uid),
            },
            {
                "id": "otlp-receiver",
                "deploymentBindingDigest": _digest_value(receiver_deployment),
                "evictedPodBindingDigest": _digest_value(receiver_before.pod_uid),
            },
        ],
    }
    environment = {
        "platform": f"{platform.system().lower()}/{platform.machine().lower()}",
        "pythonVersion": platform.python_version(),
        "kubernetesVersion": kubernetes_version,
        "apiTransport": "verified-https",
        "otlpTransport": "mutual-tls-https",
        "apiCaSource": api_ca_source,
        "otlpCaSource": otlp_ca_source,
        "proxyMode": "disabled",
        "redirectMode": "deny",
        "evictionApi": "policy/v1",
    }
    receiver_intake = {
        "signal": "metric",
        "payload": "non-empty-otlp-protobuf",
        "successBoundary": "postgresql-commit-before-http-200",
    }
    checks = _derived_checks(
        subject=subject,
        bindings=bindings,
        environment=environment,
        receiver_intake=receiver_intake,
        objective=objective,
        phases=ordered_phases,
        components=component_measurements,
    )
    failed_checks = sum(item["status"] == "failed" for item in checks)
    status = "qualified" if failed_checks == 0 else "not-qualified"
    spec: dict[str, Any] = {
        "status": status,
        "qualificationLevel": QUALIFICATION_LEVEL,
        "subject": subject,
        "bindings": bindings,
        "objective": dict(objective),
        "environment": environment,
        "receiverIntake": receiver_intake,
        "measurements": {
            "startedAt": _timestamp(started_at),
            "completedAt": _timestamp(completed_at),
            "phases": ordered_phases,
            "components": component_measurements,
        },
        "checks": checks,
        "limitations": list(LIMITATIONS),
        "summary": {
            "totalChecks": len(CHECK_IDS),
            "passedChecks": len(CHECK_IDS) - failed_checks,
            "failedChecks": failed_checks,
            "totalApiAttempts": total_api,
            "failedApiAttempts": failed_api,
            "totalReceiverAttempts": total_receiver,
            "failedReceiverAttempts": failed_receiver,
            "totalWorkflowSubmissions": len(PHASES),
            "completedWorkflows": completed_workflows,
            "failedWorkflows": failed_workflows,
            "maximumWorkflowCompletionMilliseconds": max_workflow,
            "maximumRecoveryMilliseconds": max_recovery,
            "overallStatus": status,
        },
    }
    metadata_without_id = {
        "generatedAt": _timestamp(completed_at),
        "sourceRevision": revision,
        "sourceDirty": False,
    }
    report = {
        "apiVersion": API_VERSION,
        "kind": KIND,
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
        "customer-processing-continuity.report.schema-invalid",
    )
    if any(key in FORBIDDEN_RETAINED_KEYS for key in _walk_keys(report)):
        _fail("customer-processing-continuity.report.sensitive-field")
    metadata = _mapping(
        report.get("metadata"), "customer-processing-continuity.report.invalid"
    )
    spec = _mapping(
        report.get("spec"), "customer-processing-continuity.report.invalid"
    )
    report_id = metadata.get("id")
    metadata_without_id = dict(metadata)
    metadata_without_id.pop("id", None)
    if (
        report.get("apiVersion") != API_VERSION
        or report.get("kind") != KIND
        or not isinstance(report_id, str)
        or REPORT_ID.fullmatch(report_id) is None
        or report_id != _report_identifier(metadata_without_id, spec)
        or spec.get("qualificationLevel") != QUALIFICATION_LEVEL
    ):
        _fail("customer-processing-continuity.report.invalid")
    _parse_timestamp(
        metadata.get("generatedAt"),
        "customer-processing-continuity.report.time-invalid",
    )
    subject = _mapping(
        spec.get("subject"), "customer-processing-continuity.report.invalid"
    )
    if metadata.get("sourceRevision") != subject.get("sourceRevision"):
        _fail("customer-processing-continuity.report.source-mismatch")
    objective = _mapping(
        spec.get("objective"), "customer-processing-continuity.report.invalid"
    )
    minimum_attempts = _integer(
        objective.get("minimumProbeAttemptsPerPhase"),
        minimum=5,
        maximum=1000,
        code="customer-processing-continuity.report.objective-invalid",
    )
    _integer(
        objective.get("probeIntervalMilliseconds"),
        minimum=50,
        maximum=5000,
        code="customer-processing-continuity.report.objective-invalid",
    )
    max_workflow_objective = _integer(
        objective.get("maximumWorkflowCompletionMilliseconds"),
        minimum=1000,
        maximum=120000,
        code="customer-processing-continuity.report.objective-invalid",
    )
    max_recovery_objective = _integer(
        objective.get("maximumRecoveryMilliseconds"),
        minimum=10000,
        maximum=600000,
        code="customer-processing-continuity.report.objective-invalid",
    )
    measurements = _mapping(
        spec.get("measurements"), "customer-processing-continuity.report.invalid"
    )
    started_at = _parse_timestamp(
        measurements.get("startedAt"),
        "customer-processing-continuity.report.time-invalid",
    )
    completed_at = _parse_timestamp(
        measurements.get("completedAt"),
        "customer-processing-continuity.report.time-invalid",
    )
    if completed_at < started_at or _parse_timestamp(
        metadata.get("generatedAt"),
        "customer-processing-continuity.report.time-invalid",
    ) != completed_at:
        _fail("customer-processing-continuity.report.time-invalid")
    phases = measurements.get("phases")
    if (
        not isinstance(phases, list)
        or [phase.get("id") for phase in phases if isinstance(phase, Mapping)]
        != list(PHASES)
    ):
        _fail("customer-processing-continuity.report.phases-invalid")
    total_api = failed_api = total_receiver = failed_receiver = 0
    completed_workflows = failed_workflows = max_workflow = 0
    for index, phase in enumerate(phases):
        phase = _mapping(
            phase, "customer-processing-continuity.report.phases-invalid"
        )
        api_attempts = _integer(phase.get("apiAttempts"), minimum=minimum_attempts, maximum=1000, code="customer-processing-continuity.report.phases-invalid")
        api_successes = _integer(phase.get("apiSuccesses"), minimum=0, maximum=1000, code="customer-processing-continuity.report.phases-invalid")
        api_failures = _integer(phase.get("apiFailures"), minimum=0, maximum=1000, code="customer-processing-continuity.report.phases-invalid")
        receiver_attempts = _integer(phase.get("receiverAttempts"), minimum=minimum_attempts, maximum=1000, code="customer-processing-continuity.report.phases-invalid")
        receiver_successes = _integer(phase.get("receiverSuccesses"), minimum=0, maximum=1000, code="customer-processing-continuity.report.phases-invalid")
        receiver_failures = _integer(phase.get("receiverFailures"), minimum=0, maximum=1000, code="customer-processing-continuity.report.phases-invalid")
        workflow_completed = _integer(phase.get("workflowCompleted"), minimum=0, maximum=1, code="customer-processing-continuity.report.phases-invalid")
        workflow_failures = _integer(phase.get("workflowFailures"), minimum=0, maximum=1, code="customer-processing-continuity.report.phases-invalid")
        workflow_submitted = _integer(phase.get("workflowSubmitted"), minimum=1, maximum=1, code="customer-processing-continuity.report.phases-invalid")
        _integer(phase.get("workflowPollAttempts"), minimum=1, maximum=10000, code="customer-processing-continuity.report.phases-invalid")
        workflow_duration = _integer(phase.get("workflowCompletionMilliseconds"), minimum=0, maximum=120000, code="customer-processing-continuity.report.phases-invalid")
        expected_reduced_capacity = index in (1, 2)
        if (
            api_successes + api_failures != api_attempts
            or receiver_successes + receiver_failures != receiver_attempts
            or workflow_completed + workflow_failures != 1
            or workflow_submitted != 1
            or workflow_duration > max_workflow_objective
            or phase.get("submittedDuringReducedCapacity")
            is not expected_reduced_capacity
        ):
            _fail("customer-processing-continuity.report.phases-invalid")
        total_api += api_attempts
        failed_api += api_failures
        total_receiver += receiver_attempts
        failed_receiver += receiver_failures
        completed_workflows += workflow_completed
        failed_workflows += workflow_failures
        max_workflow = max(max_workflow, workflow_duration)
    components = measurements.get("components")
    if (
        not isinstance(components, list)
        or [component.get("id") for component in components if isinstance(component, Mapping)]
        != list(COMPONENTS)
    ):
        _fail("customer-processing-continuity.report.components-invalid")
    max_recovery = 0
    for component in components:
        component = _mapping(
            component, "customer-processing-continuity.report.components-invalid"
        )
        desired = _integer(component.get("desiredReplicas"), minimum=2, maximum=100, code="customer-processing-continuity.report.components-invalid")
        before = _integer(component.get("readyReplicasBefore"), minimum=2, maximum=100, code="customer-processing-continuity.report.components-invalid")
        during = _integer(component.get("readyReplicasDuring"), minimum=1, maximum=99, code="customer-processing-continuity.report.components-invalid")
        after = _integer(component.get("readyReplicasAfter"), minimum=2, maximum=100, code="customer-processing-continuity.report.components-invalid")
        _integer(component.get("maxUnavailable"), minimum=0, maximum=0, code="customer-processing-continuity.report.components-invalid")
        _integer(component.get("pdbMinAvailable"), minimum=1, maximum=99, code="customer-processing-continuity.report.components-invalid")
        _integer(component.get("pdbDisruptionsAllowedBefore"), minimum=1, maximum=100, code="customer-processing-continuity.report.components-invalid")
        recovery = _integer(component.get("recoveryMilliseconds"), minimum=0, maximum=600000, code="customer-processing-continuity.report.components-invalid")
        if (
            before != desired
            or after != desired
            or during >= desired
            or component.get("evictionAccepted") is not True
            or component.get("reducedCapacityObserved") is not True
            or component.get("originalPodReplaced") is not True
        ):
            _fail("customer-processing-continuity.report.components-invalid")
        max_recovery = max(max_recovery, recovery)
    checks = spec.get("checks")
    if not isinstance(checks, list) or [item.get("id") for item in checks if isinstance(item, Mapping)] != list(CHECK_IDS):
        _fail("customer-processing-continuity.report.checks-invalid")
    expected_checks = _derived_checks(
        subject=subject,
        bindings=_mapping(
            spec.get("bindings"),
            "customer-processing-continuity.report.bindings-invalid",
        ),
        environment=_mapping(
            spec.get("environment"),
            "customer-processing-continuity.report.environment-invalid",
        ),
        receiver_intake=_mapping(
            spec.get("receiverIntake"),
            "customer-processing-continuity.report.receiver-intake-invalid",
        ),
        objective=objective,
        phases=phases,
        components=components,
    )
    if checks != expected_checks:
        _fail("customer-processing-continuity.report.checks-invalid")
    failed_checks = sum(item.get("status") == "failed" for item in checks)
    status = "qualified" if failed_checks == 0 else "not-qualified"
    summary = _mapping(
        spec.get("summary"), "customer-processing-continuity.report.invalid"
    )
    expected_summary = {
        "totalChecks": len(CHECK_IDS),
        "passedChecks": len(CHECK_IDS) - failed_checks,
        "failedChecks": failed_checks,
        "totalApiAttempts": total_api,
        "failedApiAttempts": failed_api,
        "totalReceiverAttempts": total_receiver,
        "failedReceiverAttempts": failed_receiver,
        "totalWorkflowSubmissions": len(PHASES),
        "completedWorkflows": completed_workflows,
        "failedWorkflows": failed_workflows,
        "maximumWorkflowCompletionMilliseconds": max_workflow,
        "maximumRecoveryMilliseconds": max_recovery,
        "overallStatus": status,
    }
    if (
        dict(summary) != expected_summary
        or spec.get("status") != status
        or spec.get("limitations") != list(LIMITATIONS)
        or (status == "qualified" and (failed_api or failed_receiver or failed_workflows or max_recovery > max_recovery_objective))
    ):
        _fail("customer-processing-continuity.report.summary-invalid")


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    destination = path.expanduser()
    if destination.is_symlink():
        _fail("customer-processing-continuity.output.invalid")
    destination = destination.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, destination)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        _fail("customer-processing-continuity.output.invalid")


def qualify(
    *,
    api_base_url: str,
    api_token_file: Path,
    otlp_base_url: str,
    otlp_token_file: Path,
    otlp_client_cert_file: Path,
    otlp_client_key_file: Path,
    profile_path: Path,
    image_digest: str,
    context: str,
    namespace: str,
    worker_deployment: str,
    receiver_deployment: str,
    output: Path,
    allow_disruption: bool,
    api_ca_file: Path | None = None,
    otlp_ca_file: Path | None = None,
    kubectl_binary: str = "kubectl",
    attempts_per_phase: int = DEFAULT_ATTEMPTS_PER_PHASE,
    probe_interval_milliseconds: int = DEFAULT_PROBE_INTERVAL_MILLISECONDS,
    maximum_workflow_milliseconds: int = DEFAULT_WORKFLOW_TIMEOUT_MILLISECONDS,
    maximum_recovery_milliseconds: int = DEFAULT_RECOVERY_TIMEOUT_MILLISECONDS,
    request_timeout_milliseconds: int = DEFAULT_REQUEST_TIMEOUT_MILLISECONDS,
    client_factory: Callable[..., customer.KubectlClient] = customer.KubectlClient,
    processing_client_factory: Callable[..., ProcessingClient] = ProcessingClient,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    if not allow_disruption:
        _fail("customer-processing-continuity.disruption.explicit-enable-required")
    for value, minimum, maximum in (
        (attempts_per_phase, 5, 1000),
        (probe_interval_milliseconds, 50, 5000),
        (maximum_workflow_milliseconds, 1000, 120000),
        (maximum_recovery_milliseconds, 10000, 600000),
        (request_timeout_milliseconds, 100, 30000),
    ):
        _integer(
            value,
            minimum=minimum,
            maximum=maximum,
            code="customer-processing-continuity.objective.invalid",
        )
    if DIGEST.fullmatch(image_digest) is None:
        _fail("customer-processing-continuity.image.invalid")
    try:
        revision, dirty = ingress._git_state()
        repository = ingress._repository_identity()
    except ingress.IngressQualificationError:
        _fail("customer-processing-continuity.source.invalid")
    if dirty:
        _fail("customer-processing-continuity.source.dirty")
    profile, profile_digest = _load_profile(profile_path)
    subject = {
        **repository,
        "sourceRevision": revision,
        "imageDigest": image_digest,
    }
    processing = processing_client_factory(
        api_base_url=api_base_url,
        api_token_file=api_token_file,
        otlp_base_url=otlp_base_url,
        otlp_token_file=otlp_token_file,
        otlp_client_cert_file=otlp_client_cert_file,
        otlp_client_key_file=otlp_client_key_file,
        profile=profile,
        subject=subject,
        api_ca_file=api_ca_file,
        otlp_ca_file=otlp_ca_file,
        request_timeout_milliseconds=request_timeout_milliseconds,
    )
    worker = client_factory(
        binary=kubectl_binary,
        context=context,
        namespace=namespace,
        deployment=worker_deployment,
        container="workflow-worker",
        expected_image_digest=image_digest,
    )
    receiver = client_factory(
        binary=kubectl_binary,
        context=context,
        namespace=namespace,
        deployment=receiver_deployment,
        container="otlp-receiver",
        expected_image_digest=image_digest,
    )
    try:
        kubernetes_version = worker.version()
        if receiver.version() != kubernetes_version:
            _fail("customer-processing-continuity.kubernetes.version-mismatch")
        processing.validate_resource()
        worker_before = worker.observe()
        receiver.observe()
    except customer.CustomerContinuityQualificationError:
        _fail("customer-processing-continuity.kubernetes.state-invalid")
    clock = now or (lambda: datetime.now(timezone.utc))
    started_at = clock()
    objective = {
        "minimumProbeAttemptsPerPhase": attempts_per_phase,
        "probeIntervalMilliseconds": probe_interval_milliseconds,
        "maximumWorkflowCompletionMilliseconds": maximum_workflow_milliseconds,
        "maximumRecoveryMilliseconds": maximum_recovery_milliseconds,
        "requestTimeoutMilliseconds": request_timeout_milliseconds,
    }
    phases: list[dict[str, Any]] = [
        _phase_measurement(
            processing,
            phase="baseline",
            attempts=attempts_per_phase,
            interval_milliseconds=probe_interval_milliseconds,
            maximum_workflow_milliseconds=maximum_workflow_milliseconds,
            submitted_during_reduced_capacity=False,
            sleeper=sleeper,
        )
    ]
    try:
        worker.evict(worker_before)
    except customer.CustomerContinuityQualificationError:
        _fail("customer-processing-continuity.worker.eviction-failed")
    worker_during = _wait_for_reduced_capacity(
        worker,
        original_pod_uid=worker_before.pod_uid,
        maximum_milliseconds=maximum_recovery_milliseconds,
        monotonic=monotonic,
        sleeper=sleeper,
    )
    phases.append(
        _phase_measurement(
            processing,
            phase="worker-disruption",
            attempts=attempts_per_phase,
            interval_milliseconds=probe_interval_milliseconds,
            maximum_workflow_milliseconds=maximum_workflow_milliseconds,
            submitted_during_reduced_capacity=True,
            sleeper=sleeper,
        )
    )
    worker_after, worker_recovery = _wait_for_recovery(
        worker,
        original_pod_uid=worker_before.pod_uid,
        maximum_milliseconds=maximum_recovery_milliseconds,
        monotonic=monotonic,
        sleeper=sleeper,
    )
    try:
        receiver_before = receiver.observe()
        receiver.evict(receiver_before)
    except customer.CustomerContinuityQualificationError:
        _fail("customer-processing-continuity.receiver.eviction-failed")
    receiver_during = _wait_for_reduced_capacity(
        receiver,
        original_pod_uid=receiver_before.pod_uid,
        maximum_milliseconds=maximum_recovery_milliseconds,
        monotonic=monotonic,
        sleeper=sleeper,
    )
    phases.append(
        _phase_measurement(
            processing,
            phase="receiver-disruption",
            attempts=attempts_per_phase,
            interval_milliseconds=probe_interval_milliseconds,
            maximum_workflow_milliseconds=maximum_workflow_milliseconds,
            submitted_during_reduced_capacity=True,
            sleeper=sleeper,
        )
    )
    receiver_after, receiver_recovery = _wait_for_recovery(
        receiver,
        original_pod_uid=receiver_before.pod_uid,
        maximum_milliseconds=maximum_recovery_milliseconds,
        monotonic=monotonic,
        sleeper=sleeper,
    )
    phases.append(
        _phase_measurement(
            processing,
            phase="recovery",
            attempts=attempts_per_phase,
            interval_milliseconds=probe_interval_milliseconds,
            maximum_workflow_milliseconds=maximum_workflow_milliseconds,
            submitted_during_reduced_capacity=False,
            sleeper=sleeper,
        )
    )
    completed_at = clock()
    report = build_report(
        revision=revision,
        repository=repository,
        image_digest=image_digest,
        api_target_digest=processing.api_target_digest,
        otlp_target_digest=processing.otlp_target_digest,
        context=context,
        namespace=namespace,
        profile_digest=profile_digest,
        worker_deployment=worker_deployment,
        receiver_deployment=receiver_deployment,
        kubernetes_version=kubernetes_version,
        api_ca_source=processing.api_ca_source,
        otlp_ca_source=processing.otlp_ca_source,
        objective=objective,
        phases=phases,
        worker_before=worker_before,
        worker_during=worker_during,
        worker_after=worker_after,
        worker_recovery_milliseconds=worker_recovery,
        receiver_before=receiver_before,
        receiver_during=receiver_during,
        receiver_after=receiver_after,
        receiver_recovery_milliseconds=receiver_recovery,
        started_at=started_at,
        completed_at=completed_at,
    )
    _write_report(output, report)
    if report["spec"]["status"] != "qualified":
        _fail("customer-processing-continuity.report.not-qualified")
    return report


def verify_report(
    *,
    report_path: Path,
    profile_path: Path,
    api_base_url: str,
    otlp_base_url: str,
    image_digest: str,
    context: str,
    namespace: str,
    worker_deployment: str,
    receiver_deployment: str,
    require_clean: bool,
    require_qualified: bool,
) -> Mapping[str, Any]:
    try:
        report = _mapping(
            json.loads(report_path.read_text(encoding="utf-8")),
            "customer-processing-continuity.report.unreadable",
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-processing-continuity.report.unreadable")
    validate_report_document(report)
    profile, profile_digest = _load_profile(profile_path)
    del profile
    api_target, api_target_digest = _https_target(api_base_url)
    otlp_target, otlp_target_digest = _https_target(otlp_base_url)
    del api_target, otlp_target
    metadata = _mapping(
        report.get("metadata"), "customer-processing-continuity.report.invalid"
    )
    spec = _mapping(
        report.get("spec"), "customer-processing-continuity.report.invalid"
    )
    subject = _mapping(
        spec.get("subject"), "customer-processing-continuity.report.invalid"
    )
    bindings = _mapping(
        spec.get("bindings"), "customer-processing-continuity.report.invalid"
    )
    component_bindings = bindings.get("components")
    if not isinstance(component_bindings, list):
        _fail("customer-processing-continuity.report.bindings-invalid")
    expected_bindings = {
        "apiTargetBindingDigest": api_target_digest,
        "otlpTargetBindingDigest": otlp_target_digest,
        "kubernetesContextBindingDigest": _digest_value(context),
        "namespaceBindingDigest": _digest_value(namespace),
        "profileDigest": profile_digest,
    }
    if any(bindings.get(key) != value for key, value in expected_bindings.items()):
        _fail("customer-processing-continuity.report.bindings-mismatch")
    expected_deployments = (worker_deployment, receiver_deployment)
    if [item.get("deploymentBindingDigest") for item in component_bindings if isinstance(item, Mapping)] != [
        _digest_value(value) for value in expected_deployments
    ]:
        _fail("customer-processing-continuity.report.bindings-mismatch")
    try:
        revision, dirty = ingress._git_state()
        repository = ingress._repository_identity()
    except ingress.IngressQualificationError:
        _fail("customer-processing-continuity.source.invalid")
    if (
        metadata.get("sourceRevision") != revision
        or subject.get("sourceRevision") != revision
        or subject.get("applicationVersion") != repository["applicationVersion"]
        or subject.get("chartVersion") != repository["chartVersion"]
        or subject.get("requiredMigration") != repository["requiredMigration"]
        or subject.get("imageDigest") != image_digest
    ):
        _fail("customer-processing-continuity.source.identity-mismatch")
    if require_clean and (dirty or metadata.get("sourceDirty") is not False):
        _fail("customer-processing-continuity.source.dirty")
    if require_qualified and spec.get("status") != "qualified":
        _fail("customer-processing-continuity.report.not-qualified")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    for command in (run,):
        command.add_argument("--api-base-url", required=True)
        command.add_argument("--api-token-file", type=Path, required=True)
        command.add_argument("--otlp-base-url", required=True)
        command.add_argument("--otlp-token-file", type=Path, required=True)
        command.add_argument("--otlp-client-cert-file", type=Path, required=True)
        command.add_argument("--otlp-client-key-file", type=Path, required=True)
        command.add_argument("--api-ca-file", type=Path)
        command.add_argument("--otlp-ca-file", type=Path)
    common: list[argparse.ArgumentParser] = [run]
    verify = subparsers.add_parser("verify")
    common.append(verify)
    for command in common:
        command.add_argument("--profile", type=Path, required=True)
        command.add_argument("--image-digest", required=True)
        command.add_argument("--context", required=True)
        command.add_argument("--namespace", required=True)
        command.add_argument(
            "--worker-deployment", default="iip-infra-intelligence-worker"
        )
        command.add_argument(
            "--receiver-deployment", default="iip-infra-intelligence-otlp-receiver"
        )
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--kubectl", default="kubectl")
    run.add_argument("--attempts-per-phase", type=int, default=DEFAULT_ATTEMPTS_PER_PHASE)
    run.add_argument(
        "--probe-interval-milliseconds",
        type=int,
        default=DEFAULT_PROBE_INTERVAL_MILLISECONDS,
    )
    run.add_argument(
        "--maximum-workflow-milliseconds",
        type=int,
        default=DEFAULT_WORKFLOW_TIMEOUT_MILLISECONDS,
    )
    run.add_argument(
        "--maximum-recovery-milliseconds",
        type=int,
        default=DEFAULT_RECOVERY_TIMEOUT_MILLISECONDS,
    )
    run.add_argument(
        "--request-timeout-milliseconds",
        type=int,
        default=DEFAULT_REQUEST_TIMEOUT_MILLISECONDS,
    )
    run.add_argument("--allow-disruption", action="store_true")
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--api-base-url", required=True)
    verify.add_argument("--otlp-base-url", required=True)
    verify.add_argument("--require-clean", action="store_true")
    verify.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            report = qualify(
                api_base_url=args.api_base_url,
                api_token_file=args.api_token_file,
                otlp_base_url=args.otlp_base_url,
                otlp_token_file=args.otlp_token_file,
                otlp_client_cert_file=args.otlp_client_cert_file,
                otlp_client_key_file=args.otlp_client_key_file,
                api_ca_file=args.api_ca_file,
                otlp_ca_file=args.otlp_ca_file,
                profile_path=args.profile,
                image_digest=args.image_digest,
                context=args.context,
                namespace=args.namespace,
                worker_deployment=args.worker_deployment,
                receiver_deployment=args.receiver_deployment,
                output=args.output,
                allow_disruption=args.allow_disruption,
                kubectl_binary=args.kubectl,
                attempts_per_phase=args.attempts_per_phase,
                probe_interval_milliseconds=args.probe_interval_milliseconds,
                maximum_workflow_milliseconds=args.maximum_workflow_milliseconds,
                maximum_recovery_milliseconds=args.maximum_recovery_milliseconds,
                request_timeout_milliseconds=args.request_timeout_milliseconds,
            )
        else:
            report = verify_report(
                report_path=args.report,
                profile_path=args.profile,
                api_base_url=args.api_base_url,
                otlp_base_url=args.otlp_base_url,
                image_digest=args.image_digest,
                context=args.context,
                namespace=args.namespace,
                worker_deployment=args.worker_deployment,
                receiver_deployment=args.receiver_deployment,
                require_clean=args.require_clean,
                require_qualified=args.require_qualified,
            )
    except CustomerProcessingContinuityError as exc:
        print(str(exc), file=os.sys.stderr)
        return 1
    print("customer processing continuity qualification: " + str(report["spec"]["status"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
