#!/usr/bin/env python3
"""Qualify one customer control-plane pod Eviction under sustained ingress probes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote

from jsonschema import Draft202012Validator, FormatChecker

import qualify_ingress_availability as ingress


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts/schemas/customer-continuity-qualification-report.schema.json"
API_VERSION = "iip.platform/v1alpha1"
KIND = "CustomerContinuityQualificationReport"
PROFILE = "customer-control-plane-pod-eviction-v1"
CHECK_IDS = (
    "source-binding",
    "minimized-output",
    "direct-no-redirect-probe",
    "explicit-context",
    "exact-release-identity",
    "redundant-capacity",
    "zero-unavailable-rollout",
    "pdb-protected",
    "sustained-probe-window",
    "probe-eviction-overlap",
    "eviction-admitted",
    "replacement-observed",
    "recovery-objective",
    "ingress-probe-qualified",
    "availability-objective",
    "latency-objective",
)
LIMITATIONS = (
    "single-api-pod-eviction",
    "single-cluster",
    "read-only-synthetic-traffic",
    "database-failure-not-qualified",
    "worker-receiver-continuity-not-qualified",
    "regional-slo-not-qualified",
)
REPORT_ID = re.compile(r"^ccq_[a-f0-9]{32}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")
KUBERNETES_VERSION = re.compile(
    r"^v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$"
)
SAFE_CONTEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,252}$")
DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
MAX_KUBECTL_OUTPUT_BYTES = 4 * 1024 * 1024
DEFAULT_SAMPLE_COUNT = 721
DEFAULT_INTERVAL_MILLISECONDS = 500
DEFAULT_MINIMUM_WINDOW_SECONDS = 300
DEFAULT_BASELINE_SECONDS = 60
DEFAULT_POST_RECOVERY_SECONDS = 60
DEFAULT_MINIMUM_AVAILABILITY_BASIS_POINTS = 9990
DEFAULT_MAXIMUM_P95_LATENCY_MILLISECONDS = 2000
DEFAULT_REQUEST_TIMEOUT_MILLISECONDS = 2000
DEFAULT_MAXIMUM_RECOVERY_SECONDS = 120
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
        "deploymentName",
        "pod",
        "podName",
        "podUid",
        "uid",
        "selector",
        "tenantId",
        "actorId",
        "token",
        "credential",
        "certificate",
        "responseBody",
        "rawError",
    }
)


class CustomerContinuityQualificationError(RuntimeError):
    """Stable customer-continuity failure without customer environment details."""


def _fail(code: str) -> None:
    raise CustomerContinuityQualificationError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        _fail("customer-continuity.ingress-report.unreadable")
    return "sha256:" + digest.hexdigest()


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
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


def _integer(value: object, *, minimum: int = 0, maximum: int = 100_000) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        _fail("customer-continuity.kubernetes.state-invalid")
    return value


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _list(value: object, code: str) -> list[Mapping[str, Any]]:
    document = _mapping(value, code)
    items = document.get("items")
    if document.get("kind") != "List" or not isinstance(items, list):
        _fail(code)
    if len(items) > 500:
        _fail(code)
    result: list[Mapping[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            _fail(code)
        result.append(item)
    return result


def _safe_context(value: str) -> str:
    if SAFE_CONTEXT.fullmatch(value) is None:
        _fail("customer-continuity.kubernetes.context-invalid")
    return value


def _dns_label(value: str, code: str) -> str:
    if len(value) > 253 or DNS_LABEL.fullmatch(value) is None:
        _fail(code)
    return value


def _load_json(path: Path, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    return _mapping(value, code)


def _write_report(path: Path, report: Mapping[str, object]) -> None:
    candidate = path.expanduser()
    if candidate.is_symlink():
        _fail("customer-continuity.output.invalid")
    destination = candidate.absolute()
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
        _fail("customer-continuity.output.invalid")


def _report_identifier(metadata: Mapping[str, object], spec: Mapping[str, object]) -> str:
    return "ccq_" + hashlib.sha256(
        _canonical({"metadata": metadata, "spec": spec})
    ).hexdigest()[:32]


def _check(identifier: str, passed: bool, error_code: str) -> dict[str, str]:
    result = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        result["errorCode"] = error_code
    return result


def _ready(pod: Mapping[str, Any]) -> bool:
    metadata = _mapping(pod.get("metadata", {}), "customer-continuity.kubernetes.pods-invalid")
    status = _mapping(pod.get("status", {}), "customer-continuity.kubernetes.pods-invalid")
    conditions = status.get("conditions", [])
    return (
        metadata.get("deletionTimestamp") is None
        and status.get("phase") == "Running"
        and isinstance(conditions, list)
        and any(
            isinstance(item, Mapping)
            and item.get("type") == "Ready"
            and item.get("status") == "True"
            for item in conditions
        )
    )


def _controller_reference(
    document: Mapping[str, Any], kind: str
) -> tuple[str, str] | None:
    metadata = _mapping(
        document.get("metadata", {}), "customer-continuity.kubernetes.owner-invalid"
    )
    references = metadata.get("ownerReferences", [])
    if not isinstance(references, list):
        return None
    matches = [
        item
        for item in references
        if isinstance(item, Mapping)
        and item.get("controller") is True
        and item.get("kind") == kind
        and isinstance(item.get("name"), str)
        and isinstance(item.get("uid"), str)
    ]
    if len(matches) != 1:
        return None
    return str(matches[0]["name"]), str(matches[0]["uid"])


def _selector_text(labels: Mapping[str, Any]) -> str:
    pairs: list[str] = []
    for key in sorted(labels):
        value = labels[key]
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            or not value
            or "," in key
            or "," in value
            or "=" in key
            or "=" in value
        ):
            _fail("customer-continuity.kubernetes.selector-invalid")
        pairs.append(f"{key}={value}")
    if not pairs:
        _fail("customer-continuity.kubernetes.selector-invalid")
    return ",".join(pairs)


@dataclass(frozen=True)
class DeploymentObservation:
    desired_replicas: int
    ready_replicas: int
    image_digest: str
    max_unavailable: int
    pdb_min_available: int
    pdb_disruptions_allowed: int
    pod_name: str
    pod_uid: str


class KubectlClient:
    """Small explicit-context client with one closed mutation."""

    def __init__(
        self,
        *,
        binary: str,
        context: str,
        namespace: str,
        deployment: str,
        expected_image_digest: str,
        timeout_seconds: int = 30,
    ) -> None:
        self.binary = binary
        self.context = _safe_context(context)
        self.namespace = _dns_label(
            namespace, "customer-continuity.kubernetes.namespace-invalid"
        )
        self.deployment = _dns_label(
            deployment, "customer-continuity.kubernetes.deployment-invalid"
        )
        if DIGEST.fullmatch(expected_image_digest) is None:
            _fail("customer-continuity.image.invalid")
        self.expected_image_digest = expected_image_digest
        self.timeout_seconds = timeout_seconds

    def _run(self, args: Sequence[str], *, input_text: str | None = None) -> str:
        command = [
            self.binary,
            "--context",
            self.context,
            "--namespace",
            self.namespace,
            *args,
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                input=input_text,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            _fail("customer-continuity.kubernetes.unavailable")
        if completed.returncode != 0:
            _fail("customer-continuity.kubernetes.command-failed")
        if len(completed.stdout.encode("utf-8")) > MAX_KUBECTL_OUTPUT_BYTES:
            _fail("customer-continuity.kubernetes.output-too-large")
        return completed.stdout

    def _json(self, args: Sequence[str], code: str) -> Mapping[str, Any]:
        try:
            value = json.loads(self._run(args))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _fail(code)
        return _mapping(value, code)

    def version(self) -> str:
        document = self._json(("version", "-o", "json"), "customer-continuity.kubernetes.version-invalid")
        server = _mapping(
            document.get("serverVersion"),
            "customer-continuity.kubernetes.version-invalid",
        )
        version = server.get("gitVersion")
        if (
            not isinstance(version, str)
            or KUBERNETES_VERSION.fullmatch(version) is None
        ):
            _fail("customer-continuity.kubernetes.version-invalid")
        return version

    def observe(self, *, original_pod_uid: str | None = None) -> DeploymentObservation:
        deployment = self._json(
            ("get", "deployment", self.deployment, "-o", "json"),
            "customer-continuity.kubernetes.deployment-invalid",
        )
        metadata = _mapping(
            deployment.get("metadata"),
            "customer-continuity.kubernetes.deployment-invalid",
        )
        deployment_uid = metadata.get("uid")
        if metadata.get("name") != self.deployment or not isinstance(deployment_uid, str):
            _fail("customer-continuity.kubernetes.deployment-invalid")
        spec = _mapping(
            deployment.get("spec"),
            "customer-continuity.kubernetes.deployment-invalid",
        )
        status = _mapping(
            deployment.get("status", {}),
            "customer-continuity.kubernetes.deployment-invalid",
        )
        desired = _integer(spec.get("replicas"), minimum=2, maximum=100)
        ready = _integer(status.get("readyReplicas", 0), maximum=100)
        available = _integer(status.get("availableReplicas", 0), maximum=100)
        updated = _integer(status.get("updatedReplicas", 0), maximum=100)
        generation = _integer(metadata.get("generation"), minimum=1)
        observed_generation = _integer(status.get("observedGeneration"), minimum=1)
        strategy = _mapping(
            spec.get("strategy"), "customer-continuity.kubernetes.rollout-invalid"
        )
        rolling = _mapping(
            strategy.get("rollingUpdate"),
            "customer-continuity.kubernetes.rollout-invalid",
        )
        if strategy.get("type") != "RollingUpdate" or rolling.get("maxUnavailable") != 0:
            _fail("customer-continuity.kubernetes.rollout-invalid")
        selector = _mapping(
            spec.get("selector"), "customer-continuity.kubernetes.selector-invalid"
        )
        labels = _mapping(
            selector.get("matchLabels"),
            "customer-continuity.kubernetes.selector-invalid",
        )
        if set(selector) != {"matchLabels"}:
            _fail("customer-continuity.kubernetes.selector-invalid")
        selector_text = _selector_text(labels)
        template = _mapping(
            spec.get("template"), "customer-continuity.kubernetes.image-invalid"
        )
        pod_spec = _mapping(
            template.get("spec"), "customer-continuity.kubernetes.image-invalid"
        )
        containers = pod_spec.get("containers")
        api_images = [
            item.get("image")
            for item in containers
            if isinstance(containers, list)
            and isinstance(item, Mapping)
            and item.get("name") == "api"
        ] if isinstance(containers, list) else []
        expected_suffix = "@" + self.expected_image_digest
        if (
            len(api_images) != 1
            or not isinstance(api_images[0], str)
            or not api_images[0].endswith(expected_suffix)
        ):
            _fail("customer-continuity.kubernetes.image-invalid")

        pdb = self._json(
            ("get", "poddisruptionbudget", self.deployment, "-o", "json"),
            "customer-continuity.kubernetes.pdb-invalid",
        )
        pdb_metadata = _mapping(
            pdb.get("metadata"), "customer-continuity.kubernetes.pdb-invalid"
        )
        if pdb_metadata.get("name") != self.deployment:
            _fail("customer-continuity.kubernetes.pdb-invalid")
        pdb_spec = _mapping(
            pdb.get("spec"), "customer-continuity.kubernetes.pdb-invalid"
        )
        pdb_status = _mapping(
            pdb.get("status"), "customer-continuity.kubernetes.pdb-invalid"
        )
        pdb_selector = _mapping(
            pdb_spec.get("selector"), "customer-continuity.kubernetes.pdb-invalid"
        )
        pdb_labels = _mapping(
            pdb_selector.get("matchLabels"),
            "customer-continuity.kubernetes.pdb-invalid",
        )
        min_available = _integer(
            pdb_spec.get("minAvailable"), minimum=1, maximum=99
        )
        disruptions_allowed = _integer(
            pdb_status.get("disruptionsAllowed", 0), maximum=99
        )
        if pdb_labels != labels or min_available >= desired:
            _fail("customer-continuity.kubernetes.pdb-invalid")

        replicasets = _list(
            self._json(
                ("get", "replicaset", "-l", selector_text, "-o", "json"),
                "customer-continuity.kubernetes.replicasets-invalid",
            ),
            "customer-continuity.kubernetes.replicasets-invalid",
        )
        owned_rs_uids = {
            str(_mapping(item.get("metadata", {}), "customer-continuity.kubernetes.replicasets-invalid").get("uid"))
            for item in replicasets
            if _controller_reference(item, "Deployment")
            == (self.deployment, deployment_uid)
        }
        owned_rs_uids.discard("None")
        if not owned_rs_uids:
            _fail("customer-continuity.kubernetes.replicasets-invalid")
        pods = _list(
            self._json(
                ("get", "pod", "-l", selector_text, "-o", "json"),
                "customer-continuity.kubernetes.pods-invalid",
            ),
            "customer-continuity.kubernetes.pods-invalid",
        )
        ready_pods: list[tuple[str, str]] = []
        for pod in pods:
            owner = _controller_reference(pod, "ReplicaSet")
            pod_metadata = _mapping(
                pod.get("metadata"), "customer-continuity.kubernetes.pods-invalid"
            )
            name = pod_metadata.get("name")
            uid = pod_metadata.get("uid")
            pod_labels = _mapping(
                pod_metadata.get("labels", {}),
                "customer-continuity.kubernetes.pods-invalid",
            )
            if (
                owner is not None
                and owner[1] in owned_rs_uids
                and all(pod_labels.get(key) == value for key, value in labels.items())
                and isinstance(name, str)
                and isinstance(uid, str)
                and _ready(pod)
            ):
                ready_pods.append((name, uid))
        ready_pods.sort(key=lambda item: (item[1], item[0]))
        if (
            observed_generation != generation
            or ready != desired
            or available != desired
            or updated != desired
            or len(ready_pods) < desired
        ):
            _fail("customer-continuity.kubernetes.capacity-not-ready")
        if original_pod_uid is not None:
            if any(uid == original_pod_uid for _, uid in ready_pods):
                _fail("customer-continuity.kubernetes.original-pod-present")
            pod_name, pod_uid = ready_pods[0]
        else:
            if disruptions_allowed < 1:
                _fail("customer-continuity.kubernetes.disruption-not-allowed")
            pod_name, pod_uid = ready_pods[0]
        return DeploymentObservation(
            desired_replicas=desired,
            ready_replicas=ready,
            image_digest=self.expected_image_digest,
            max_unavailable=0,
            pdb_min_available=min_available,
            pdb_disruptions_allowed=disruptions_allowed,
            pod_name=pod_name,
            pod_uid=pod_uid,
        )

    def evict(self, observation: DeploymentObservation) -> None:
        body = {
            "apiVersion": "policy/v1",
            "kind": "Eviction",
            "metadata": {
                "name": observation.pod_name,
                "namespace": self.namespace,
            },
            "deleteOptions": {"preconditions": {"uid": observation.pod_uid}},
        }
        path = (
            "/api/v1/namespaces/"
            + quote(self.namespace, safe="")
            + "/pods/"
            + quote(observation.pod_name, safe="")
            + "/eviction"
        )
        self._run(("create", "--raw", path, "-f", "-"), input_text=json.dumps(body))


def _wait_for_replacement(
    client: KubectlClient,
    *,
    original_pod_uid: str,
    maximum_recovery_seconds: int,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> tuple[DeploymentObservation, int]:
    started = monotonic()
    deadline = started + maximum_recovery_seconds
    while monotonic() <= deadline:
        try:
            observation = client.observe(original_pod_uid=original_pod_uid)
        except CustomerContinuityQualificationError as exc:
            if str(exc) not in {
                "customer-continuity.kubernetes.capacity-not-ready",
                "customer-continuity.kubernetes.original-pod-present",
            }:
                raise
        else:
            return observation, math.ceil(monotonic() - started)
        sleeper(2)
    _fail("customer-continuity.kubernetes.recovery-timeout")


def _subject(
    *, revision: str, repository: Mapping[str, str], image_digest: str
) -> dict[str, str]:
    return {
        "applicationVersion": repository["applicationVersion"],
        "chartVersion": repository["chartVersion"],
        "contractsApiVersion": API_VERSION,
        "requiredMigration": repository["requiredMigration"],
        "sourceRevision": revision,
        "imageDigest": image_digest,
    }


def _expected_ingress_identity(subject: Mapping[str, str]) -> dict[str, str]:
    return {
        "applicationVersion": subject["applicationVersion"],
        "contractsApiVersion": subject["contractsApiVersion"],
        "requiredMigration": subject["requiredMigration"],
        "buildMode": "release",
        "sourceRevision": subject["sourceRevision"],
        "chartVersion": subject["chartVersion"],
        "imageDigest": subject["imageDigest"],
    }


def _ingress_evidence(
    report: Mapping[str, Any], *, report_digest: str
) -> dict[str, Any]:
    metadata = _mapping(
        report.get("metadata"), "customer-continuity.ingress-report.invalid"
    )
    spec = _mapping(report.get("spec"), "customer-continuity.ingress-report.invalid")
    measurements = _mapping(
        spec.get("measurements"), "customer-continuity.ingress-report.invalid"
    )
    return {
        "reportId": metadata.get("id"),
        "reportDigest": report_digest,
        "status": spec.get("status"),
        "sampleCount": measurements.get("sampleCount"),
        "successfulSamples": measurements.get("successfulSamples"),
        "failedSamples": measurements.get("failedSamples"),
        "availabilityBasisPoints": measurements.get("availabilityBasisPoints"),
        "p95CycleLatencyMilliseconds": measurements.get(
            "p95CycleLatencyMilliseconds"
        ),
        "failureCategories": measurements.get("failureCategories"),
    }


def _derive_checks(
    *,
    metadata: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> list[dict[str, str]]:
    subject = _mapping(spec.get("subject"), "customer-continuity.report.invalid")
    bindings = _mapping(spec.get("bindings"), "customer-continuity.report.invalid")
    objective = _mapping(spec.get("objective"), "customer-continuity.report.invalid")
    environment = _mapping(
        spec.get("environment"), "customer-continuity.report.invalid"
    )
    measurements = _mapping(
        spec.get("measurements"), "customer-continuity.report.invalid"
    )
    ingress_result = _mapping(
        measurements.get("ingress"), "customer-continuity.report.invalid"
    )
    deployment = _mapping(
        measurements.get("deployment"), "customer-continuity.report.invalid"
    )
    p95 = ingress_result.get("p95CycleLatencyMilliseconds")
    binding_values = list(bindings.values())
    return [
        _check(
            "source-binding",
            metadata.get("sourceDirty") is False
            and metadata.get("sourceRevision") == subject.get("sourceRevision"),
            "customer-continuity.source.mismatch",
        ),
        _check(
            "minimized-output",
            not _forbidden_key_present({"metadata": metadata, "spec": spec}),
            "customer-continuity.output.not-minimized",
        ),
        _check(
            "direct-no-redirect-probe",
            environment.get("proxyMode") == "disabled"
            and environment.get("redirectMode") == "deny"
            and environment.get("transport") == "verified-https",
            "customer-continuity.probe.transport-invalid",
        ),
        _check(
            "explicit-context",
            len(binding_values) == 5
            and all(isinstance(value, str) and DIGEST.fullmatch(value) for value in binding_values),
            "customer-continuity.kubernetes.binding-invalid",
        ),
        _check(
            "exact-release-identity",
            isinstance(subject.get("imageDigest"), str)
            and DIGEST.fullmatch(str(subject.get("imageDigest"))) is not None,
            "customer-continuity.release.identity-invalid",
        ),
        _check(
            "redundant-capacity",
            isinstance(deployment.get("desiredReplicas"), int)
            and deployment.get("desiredReplicas", 0) >= 2
            and deployment.get("readyReplicasBefore")
            == deployment.get("desiredReplicas")
            and deployment.get("readyReplicasAfter")
            == deployment.get("desiredReplicas"),
            "customer-continuity.kubernetes.capacity-invalid",
        ),
        _check(
            "zero-unavailable-rollout",
            deployment.get("maxUnavailable") == 0,
            "customer-continuity.kubernetes.rollout-invalid",
        ),
        _check(
            "pdb-protected",
            isinstance(deployment.get("pdbMinAvailable"), int)
            and deployment.get("pdbMinAvailable", 0) >= 1
            and deployment.get("pdbMinAvailable", 0)
            < deployment.get("desiredReplicas", 0)
            and deployment.get("pdbDisruptionsAllowedBefore", 0) >= 1,
            "customer-continuity.kubernetes.pdb-invalid",
        ),
        _check(
            "sustained-probe-window",
            measurements.get("scheduledWindowSeconds", 0)
            >= objective.get("minimumWindowSeconds", 0)
            and measurements.get("actualWindowSeconds", 0)
            >= objective.get("minimumWindowSeconds", 0),
            "customer-continuity.probe.window-insufficient",
        ),
        _check(
            "probe-eviction-overlap",
            measurements.get("baselineSeconds", 0)
            >= objective.get("minimumBaselineSeconds", 0)
            and measurements.get("postRecoverySeconds", 0)
            >= objective.get("minimumPostRecoverySeconds", 0),
            "customer-continuity.probe.overlap-insufficient",
        ),
        _check(
            "eviction-admitted",
            deployment.get("evictionAccepted") is True,
            "customer-continuity.kubernetes.eviction-not-admitted",
        ),
        _check(
            "replacement-observed",
            deployment.get("originalPodReplaced") is True
            and deployment.get("readyReplicasAfter")
            == deployment.get("desiredReplicas"),
            "customer-continuity.kubernetes.replacement-not-observed",
        ),
        _check(
            "recovery-objective",
            measurements.get("recoverySeconds", 100_001)
            <= objective.get("maximumRecoverySeconds", -1),
            "customer-continuity.recovery.objective-missed",
        ),
        _check(
            "ingress-probe-qualified",
            ingress_result.get("status") == "qualified",
            "customer-continuity.ingress.not-qualified",
        ),
        _check(
            "availability-objective",
            ingress_result.get("availabilityBasisPoints", 0)
            >= objective.get("minimumAvailabilityBasisPoints", 10_001),
            "customer-continuity.availability.objective-missed",
        ),
        _check(
            "latency-objective",
            isinstance(p95, int)
            and p95 <= objective.get("maximumP95LatencyMilliseconds", -1),
            "customer-continuity.latency.objective-missed",
        ),
    ]


def _forbidden_key_present(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_RETAINED_KEYS or _forbidden_key_present(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_forbidden_key_present(item) for item in value)
    return False


def build_report(
    *,
    revision: str,
    repository: Mapping[str, str],
    image_digest: str,
    ingress_report: Mapping[str, Any],
    ingress_report_digest: str,
    context: str,
    namespace: str,
    deployment_name: str,
    kubernetes_version: str,
    before: DeploymentObservation,
    after: DeploymentObservation,
    started_at: datetime,
    eviction_started_at: datetime,
    recovered_at: datetime,
    completed_at: datetime,
    recovery_seconds: int,
    sample_count: int,
    interval_milliseconds: int,
    minimum_window_seconds: int,
    minimum_baseline_seconds: int,
    minimum_post_recovery_seconds: int,
    minimum_availability_basis_points: int,
    maximum_p95_latency_milliseconds: int,
    request_timeout_milliseconds: int,
    maximum_recovery_seconds: int,
) -> dict[str, Any]:
    try:
        ingress.validate_report_document(ingress_report)
    except ingress.IngressQualificationError:
        _fail("customer-continuity.ingress-report.invalid")
    subject = _subject(
        revision=revision, repository=repository, image_digest=image_digest
    )
    ingress_metadata = _mapping(
        ingress_report.get("metadata"), "customer-continuity.ingress-report.invalid"
    )
    ingress_spec = _mapping(
        ingress_report.get("spec"), "customer-continuity.ingress-report.invalid"
    )
    ingress_objective = _mapping(
        ingress_spec.get("objective"), "customer-continuity.ingress-report.invalid"
    )
    ingress_environment = _mapping(
        ingress_spec.get("environment"), "customer-continuity.ingress-report.invalid"
    )
    if (
        ingress_metadata.get("sourceRevision") != revision
        or ingress_metadata.get("sourceDirty") is not False
        or ingress_spec.get("qualificationLevel") != "customer-ingress"
        or ingress_spec.get("targetIdentity") != _expected_ingress_identity(subject)
        or ingress_objective
        != {
            "sampleCount": sample_count,
            "minimumAvailabilityBasisPoints": minimum_availability_basis_points,
            "maximumP95LatencyMilliseconds": maximum_p95_latency_milliseconds,
            "requestTimeoutMilliseconds": request_timeout_milliseconds,
            "intervalMilliseconds": interval_milliseconds,
        }
    ):
        _fail("customer-continuity.ingress-report.binding-invalid")
    scheduled_window = ((sample_count - 1) * interval_milliseconds) // 1000
    actual_window = math.floor((completed_at - started_at).total_seconds())
    baseline = math.floor((eviction_started_at - started_at).total_seconds())
    post_recovery = math.floor((completed_at - recovered_at).total_seconds())
    objective = {
        "sampleCount": sample_count,
        "intervalMilliseconds": interval_milliseconds,
        "minimumWindowSeconds": minimum_window_seconds,
        "minimumBaselineSeconds": minimum_baseline_seconds,
        "minimumPostRecoverySeconds": minimum_post_recovery_seconds,
        "minimumAvailabilityBasisPoints": minimum_availability_basis_points,
        "maximumP95LatencyMilliseconds": maximum_p95_latency_milliseconds,
        "requestTimeoutMilliseconds": request_timeout_milliseconds,
        "maximumRecoverySeconds": maximum_recovery_seconds,
    }
    measurements = {
        "startedAt": _timestamp(started_at),
        "evictionStartedAt": _timestamp(eviction_started_at),
        "recoveredAt": _timestamp(recovered_at),
        "completedAt": _timestamp(completed_at),
        "scheduledWindowSeconds": scheduled_window,
        "actualWindowSeconds": actual_window,
        "baselineSeconds": baseline,
        "recoverySeconds": recovery_seconds,
        "postRecoverySeconds": post_recovery,
        "ingress": _ingress_evidence(
            ingress_report, report_digest=ingress_report_digest
        ),
        "deployment": {
            "desiredReplicas": before.desired_replicas,
            "readyReplicasBefore": before.ready_replicas,
            "readyReplicasAfter": after.ready_replicas,
            "maxUnavailable": before.max_unavailable,
            "pdbMinAvailable": before.pdb_min_available,
            "pdbDisruptionsAllowedBefore": before.pdb_disruptions_allowed,
            "evictionAccepted": True,
            "originalPodReplaced": before.pod_uid != after.pod_uid,
        },
    }
    spec_without_checks: dict[str, Any] = {
        "status": "not-qualified",
        "qualificationLevel": PROFILE,
        "subject": subject,
        "bindings": {
            "targetBindingDigest": ingress_spec.get("targetBindingDigest"),
            "kubernetesContextBindingDigest": _digest_value(
                {"kubernetesContext": context}
            ),
            "namespaceBindingDigest": _digest_value({"namespace": namespace}),
            "deploymentBindingDigest": _digest_value(
                {"context": context, "namespace": namespace, "deployment": deployment_name}
            ),
            "evictedPodBindingDigest": _digest_value(
                {
                    "context": context,
                    "namespace": namespace,
                    "deployment": deployment_name,
                    "podName": before.pod_name,
                    "podUid": before.pod_uid,
                }
            ),
        },
        "objective": objective,
        "environment": {
            "platform": f"{platform.system().lower()}/{platform.machine().lower()}",
            "pythonVersion": platform.python_version(),
            "kubernetesVersion": kubernetes_version,
            "transport": "verified-https",
            "caSource": ingress_environment.get("caSource"),
            "proxyMode": "disabled",
            "redirectMode": "deny",
            "evictionApi": "policy/v1",
        },
        "measurements": measurements,
        "limitations": list(LIMITATIONS),
    }
    metadata_without_id: dict[str, Any] = {
        "generatedAt": _timestamp(completed_at),
        "sourceRevision": revision,
        "sourceDirty": False,
    }
    checks = _derive_checks(metadata=metadata_without_id, spec=spec_without_checks)
    failed_checks = sum(item["status"] == "failed" for item in checks)
    status = "qualified" if failed_checks == 0 else "not-qualified"
    spec_without_checks["status"] = status
    spec_without_checks["checks"] = checks
    spec_without_checks["summary"] = {
        "totalChecks": len(checks),
        "passedChecks": len(checks) - failed_checks,
        "failedChecks": failed_checks,
        "overallStatus": status,
    }
    report = {
        "apiVersion": API_VERSION,
        "kind": KIND,
        "metadata": {
            "id": _report_identifier(metadata_without_id, spec_without_checks),
            **metadata_without_id,
        },
        "spec": spec_without_checks,
    }
    validate_report_document(report)
    return report


def validate_report_document(report: Mapping[str, Any]) -> None:
    try:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)
    except Exception as exc:
        if isinstance(exc, CustomerContinuityQualificationError):
            raise
        _fail("customer-continuity.report.schema-invalid")
    if _forbidden_key_present(report):
        _fail("customer-continuity.report.output-not-minimized")
    metadata = _mapping(report.get("metadata"), "customer-continuity.report.invalid")
    spec = _mapping(report.get("spec"), "customer-continuity.report.invalid")
    measurements = _mapping(
        spec.get("measurements"), "customer-continuity.report.invalid"
    )
    objective = _mapping(spec.get("objective"), "customer-continuity.report.invalid")
    ingress_result = _mapping(
        measurements.get("ingress"), "customer-continuity.report.invalid"
    )
    started = _parse_timestamp(
        measurements.get("startedAt"), "customer-continuity.report.time-invalid"
    )
    eviction = _parse_timestamp(
        measurements.get("evictionStartedAt"),
        "customer-continuity.report.time-invalid",
    )
    recovered = _parse_timestamp(
        measurements.get("recoveredAt"), "customer-continuity.report.time-invalid"
    )
    completed = _parse_timestamp(
        measurements.get("completedAt"), "customer-continuity.report.time-invalid"
    )
    generated = _parse_timestamp(
        metadata.get("generatedAt"), "customer-continuity.report.time-invalid"
    )
    if not started < eviction < recovered < completed or generated != completed:
        _fail("customer-continuity.report.time-invalid")
    expected_durations = {
        "actualWindowSeconds": math.floor((completed - started).total_seconds()),
        "baselineSeconds": math.floor((eviction - started).total_seconds()),
        "postRecoverySeconds": math.floor((completed - recovered).total_seconds()),
    }
    if any(measurements.get(key) != value for key, value in expected_durations.items()):
        _fail("customer-continuity.report.duration-invalid")
    expected_scheduled = (
        (objective.get("sampleCount") - 1) * objective.get("intervalMilliseconds")
    ) // 1000
    if measurements.get("scheduledWindowSeconds") != expected_scheduled:
        _fail("customer-continuity.report.duration-invalid")
    sample_count = ingress_result.get("sampleCount")
    successes = ingress_result.get("successfulSamples")
    failures = ingress_result.get("failedSamples")
    if (
        sample_count != objective.get("sampleCount")
        or not isinstance(successes, int)
        or not isinstance(failures, int)
        or successes + failures != sample_count
        or ingress_result.get("availabilityBasisPoints")
        != (successes * 10_000) // sample_count
    ):
        _fail("customer-continuity.report.ingress-arithmetic-invalid")
    expected_checks = _derive_checks(metadata=metadata, spec=spec)
    if spec.get("checks") != expected_checks or [item["id"] for item in expected_checks] != list(CHECK_IDS):
        _fail("customer-continuity.report.checks-invalid")
    failed = sum(item["status"] == "failed" for item in expected_checks)
    status = "qualified" if failed == 0 else "not-qualified"
    if spec.get("status") != status or spec.get("summary") != {
        "totalChecks": len(CHECK_IDS),
        "passedChecks": len(CHECK_IDS) - failed,
        "failedChecks": failed,
        "overallStatus": status,
    }:
        _fail("customer-continuity.report.summary-invalid")
    metadata_without_id = dict(metadata)
    report_id = metadata_without_id.pop("id", None)
    if (
        not isinstance(report_id, str)
        or REPORT_ID.fullmatch(report_id) is None
        or report_id != _report_identifier(metadata_without_id, spec)
    ):
        _fail("customer-continuity.report.id-invalid")


def _cross_check_ingress(
    report: Mapping[str, Any], ingress_report: Mapping[str, Any], ingress_path: Path
) -> None:
    try:
        ingress.validate_report_document(ingress_report)
    except ingress.IngressQualificationError:
        _fail("customer-continuity.ingress-report.invalid")
    spec = _mapping(report.get("spec"), "customer-continuity.report.invalid")
    subject = _mapping(spec.get("subject"), "customer-continuity.report.invalid")
    objective = _mapping(spec.get("objective"), "customer-continuity.report.invalid")
    measurements = _mapping(
        spec.get("measurements"), "customer-continuity.report.invalid"
    )
    retained = _mapping(
        measurements.get("ingress"), "customer-continuity.report.invalid"
    )
    ingress_metadata = _mapping(
        ingress_report.get("metadata"), "customer-continuity.ingress-report.invalid"
    )
    ingress_spec = _mapping(
        ingress_report.get("spec"), "customer-continuity.ingress-report.invalid"
    )
    ingress_objective = _mapping(
        ingress_spec.get("objective"), "customer-continuity.ingress-report.invalid"
    )
    expected_objective = {
        "sampleCount": objective.get("sampleCount"),
        "minimumAvailabilityBasisPoints": objective.get(
            "minimumAvailabilityBasisPoints"
        ),
        "maximumP95LatencyMilliseconds": objective.get(
            "maximumP95LatencyMilliseconds"
        ),
        "requestTimeoutMilliseconds": objective.get("requestTimeoutMilliseconds"),
        "intervalMilliseconds": objective.get("intervalMilliseconds"),
    }
    if (
        retained != _ingress_evidence(ingress_report, report_digest=_sha256_file(ingress_path))
        or ingress_metadata.get("sourceRevision") != subject.get("sourceRevision")
        or ingress_metadata.get("sourceDirty") is not False
        or ingress_spec.get("qualificationLevel") != "customer-ingress"
        or ingress_spec.get("targetBindingDigest")
        != _mapping(spec.get("bindings"), "customer-continuity.report.invalid").get(
            "targetBindingDigest"
        )
        or ingress_spec.get("targetIdentity")
        != _expected_ingress_identity(subject)
        or ingress_objective != expected_objective
    ):
        _fail("customer-continuity.ingress-report.binding-invalid")
    ingress_measurements = _mapping(
        ingress_spec.get("measurements"),
        "customer-continuity.ingress-report.invalid",
    )
    ingress_started = _parse_timestamp(
        ingress_measurements.get("startedAt"),
        "customer-continuity.ingress-report.time-invalid",
    )
    ingress_completed = _parse_timestamp(
        ingress_measurements.get("completedAt"),
        "customer-continuity.ingress-report.time-invalid",
    )
    report_started = _parse_timestamp(
        measurements.get("startedAt"), "customer-continuity.report.time-invalid"
    )
    report_completed = _parse_timestamp(
        measurements.get("completedAt"), "customer-continuity.report.time-invalid"
    )
    if ingress_started != report_started or ingress_completed != report_completed:
        _fail("customer-continuity.ingress-report.time-invalid")


def verify_report(
    *,
    report_path: Path,
    ingress_report_path: Path,
    require_clean: bool = False,
    require_qualified: bool = False,
) -> Mapping[str, Any]:
    report = _load_json(report_path, "customer-continuity.report.unreadable")
    validate_report_document(report)
    ingress_report = _load_json(
        ingress_report_path, "customer-continuity.ingress-report.unreadable"
    )
    _cross_check_ingress(report, ingress_report, ingress_report_path)
    metadata = _mapping(report.get("metadata"), "customer-continuity.report.invalid")
    spec = _mapping(report.get("spec"), "customer-continuity.report.invalid")
    subject = _mapping(spec.get("subject"), "customer-continuity.report.invalid")
    try:
        revision, dirty = ingress._git_state()
        repository = ingress._repository_identity()
    except ingress.IngressQualificationError:
        _fail("customer-continuity.source.unavailable")
    if require_clean and (
        dirty
        or metadata.get("sourceDirty") is not False
        or revision != metadata.get("sourceRevision")
    ):
        _fail("customer-continuity.source.mismatch")
    if subject != _subject(
        revision=str(metadata.get("sourceRevision")),
        repository=repository,
        image_digest=str(subject.get("imageDigest")),
    ):
        _fail("customer-continuity.source.identity-mismatch")
    if require_qualified and spec.get("status") != "qualified":
        _fail("customer-continuity.report.not-qualified")
    return report


def _validate_objective(
    *,
    sample_count: int,
    interval_milliseconds: int,
    minimum_window_seconds: int,
    minimum_baseline_seconds: int,
    minimum_post_recovery_seconds: int,
    minimum_availability_basis_points: int,
    maximum_p95_latency_milliseconds: int,
    request_timeout_milliseconds: int,
    maximum_recovery_seconds: int,
) -> None:
    values = (
        (sample_count, 301, 10_000),
        (interval_milliseconds, 30, 10_000),
        (minimum_window_seconds, 300, 3_600),
        (minimum_baseline_seconds, 30, 900),
        (minimum_post_recovery_seconds, 30, 900),
        (minimum_availability_basis_points, 9_000, 10_000),
        (maximum_p95_latency_milliseconds, 1, 60_000),
        (request_timeout_milliseconds, 100, 30_000),
        (maximum_recovery_seconds, 10, 600),
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high
        for value, low, high in values
    ):
        _fail("customer-continuity.objective.invalid")
    scheduled_milliseconds = (sample_count - 1) * interval_milliseconds
    required_seconds = max(
        minimum_window_seconds,
        minimum_baseline_seconds
        + maximum_recovery_seconds
        + minimum_post_recovery_seconds,
    )
    if scheduled_milliseconds < required_seconds * 1000:
        _fail("customer-continuity.objective.window-insufficient")


def qualify(
    *,
    base_url: str,
    token_file: Path,
    image_digest: str,
    context: str,
    namespace: str,
    deployment_name: str,
    ingress_report_path: Path,
    output: Path,
    allow_disruption: bool,
    ca_file: Path | None = None,
    kubectl_binary: str = "kubectl",
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    interval_milliseconds: int = DEFAULT_INTERVAL_MILLISECONDS,
    minimum_window_seconds: int = DEFAULT_MINIMUM_WINDOW_SECONDS,
    minimum_baseline_seconds: int = DEFAULT_BASELINE_SECONDS,
    minimum_post_recovery_seconds: int = DEFAULT_POST_RECOVERY_SECONDS,
    minimum_availability_basis_points: int = DEFAULT_MINIMUM_AVAILABILITY_BASIS_POINTS,
    maximum_p95_latency_milliseconds: int = DEFAULT_MAXIMUM_P95_LATENCY_MILLISECONDS,
    request_timeout_milliseconds: int = DEFAULT_REQUEST_TIMEOUT_MILLISECONDS,
    maximum_recovery_seconds: int = DEFAULT_MAXIMUM_RECOVERY_SECONDS,
    client_factory: Callable[..., KubectlClient] = KubectlClient,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    if not allow_disruption:
        _fail("customer-continuity.disruption.explicit-enable-required")
    if DIGEST.fullmatch(image_digest) is None:
        _fail("customer-continuity.image.invalid")
    _validate_objective(
        sample_count=sample_count,
        interval_milliseconds=interval_milliseconds,
        minimum_window_seconds=minimum_window_seconds,
        minimum_baseline_seconds=minimum_baseline_seconds,
        minimum_post_recovery_seconds=minimum_post_recovery_seconds,
        minimum_availability_basis_points=minimum_availability_basis_points,
        maximum_p95_latency_milliseconds=maximum_p95_latency_milliseconds,
        request_timeout_milliseconds=request_timeout_milliseconds,
        maximum_recovery_seconds=maximum_recovery_seconds,
    )
    try:
        ingress._checked_url("customer-ingress", base_url)
        revision, dirty = ingress._git_state()
        repository = ingress._repository_identity()
    except ingress.IngressQualificationError:
        _fail("customer-continuity.source-or-target.invalid")
    if dirty:
        _fail("customer-continuity.source.dirty")
    client = client_factory(
        binary=kubectl_binary,
        context=context,
        namespace=namespace,
        deployment=deployment_name,
        expected_image_digest=image_digest,
    )
    kubernetes_version = client.version()
    before = client.observe()
    if before.pdb_disruptions_allowed < 1:
        _fail("customer-continuity.kubernetes.disruption-not-allowed")

    clock = now or (lambda: datetime.now(timezone.utc))
    probe_started = threading.Event()
    stop_probe = threading.Event()
    probe_started_at: list[datetime] = []

    def mark_started(value: datetime) -> None:
        probe_started_at.append(value)
        probe_started.set()

    def run_probe() -> dict[str, Any]:
        return ingress.generate_report(
            profile="customer-ingress",
            base_url=base_url,
            token_file=token_file,
            output=ingress_report_path,
            ca_file=ca_file,
            image_digest=image_digest,
            sample_count=sample_count,
            minimum_availability_basis_points=minimum_availability_basis_points,
            maximum_p95_latency_milliseconds=maximum_p95_latency_milliseconds,
            request_timeout_milliseconds=request_timeout_milliseconds,
            interval_milliseconds=interval_milliseconds,
            started_callback=mark_started,
            stop_requested=stop_probe.is_set,
        )

    future: Future[dict[str, Any]]
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="iip-continuity")
    future = executor.submit(run_probe)
    try:
        if not probe_started.wait(timeout=15):
            _fail("customer-continuity.probe.start-timeout")
        sleeper(minimum_baseline_seconds)
        if future.done():
            _fail("customer-continuity.probe.ended-before-eviction")
        eviction_started_at = clock()
        client.evict(before)
        after, recovery_seconds = _wait_for_replacement(
            client,
            original_pod_uid=before.pod_uid,
            maximum_recovery_seconds=maximum_recovery_seconds,
            monotonic=monotonic,
            sleeper=sleeper,
        )
        recovered_at = clock()
        try:
            scheduled_window_seconds = (
                (sample_count - 1) * interval_milliseconds
            ) / 1000
            probe_watchdog_seconds = min(
                7200,
                max(
                    600,
                    scheduled_window_seconds * 3
                    + request_timeout_milliseconds * 3 / 1000
                    + 30,
                ),
            )
            ingress_report = future.result(
                timeout=probe_watchdog_seconds
            )
        except Exception:
            _fail("customer-continuity.probe.failed")
    except BaseException:
        stop_probe.set()
        try:
            future.result(timeout=request_timeout_milliseconds / 1000 + 5)
        except Exception:
            pass
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    if len(probe_started_at) != 1:
        _fail("customer-continuity.probe.start-invalid")
    ingress_spec = _mapping(
        ingress_report.get("spec"), "customer-continuity.ingress-report.invalid"
    )
    ingress_measurements = _mapping(
        ingress_spec.get("measurements"),
        "customer-continuity.ingress-report.invalid",
    )
    started_at = _parse_timestamp(
        ingress_measurements.get("startedAt"),
        "customer-continuity.ingress-report.time-invalid",
    )
    completed_at = _parse_timestamp(
        ingress_measurements.get("completedAt"),
        "customer-continuity.ingress-report.time-invalid",
    )
    report = build_report(
        revision=revision,
        repository=repository,
        image_digest=image_digest,
        ingress_report=ingress_report,
        ingress_report_digest=_sha256_file(ingress_report_path),
        context=context,
        namespace=namespace,
        deployment_name=deployment_name,
        kubernetes_version=kubernetes_version,
        before=before,
        after=after,
        started_at=started_at,
        eviction_started_at=eviction_started_at,
        recovered_at=recovered_at,
        completed_at=completed_at,
        recovery_seconds=recovery_seconds,
        sample_count=sample_count,
        interval_milliseconds=interval_milliseconds,
        minimum_window_seconds=minimum_window_seconds,
        minimum_baseline_seconds=minimum_baseline_seconds,
        minimum_post_recovery_seconds=minimum_post_recovery_seconds,
        minimum_availability_basis_points=minimum_availability_basis_points,
        maximum_p95_latency_milliseconds=maximum_p95_latency_milliseconds,
        request_timeout_milliseconds=request_timeout_milliseconds,
        maximum_recovery_seconds=maximum_recovery_seconds,
    )
    _write_report(output, report)
    if report["spec"]["status"] != "qualified":
        _fail("customer-continuity.report.not-qualified")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--base-url", required=True)
    run.add_argument("--token-file", type=Path, required=True)
    run.add_argument("--image-digest", required=True)
    run.add_argument("--context", required=True)
    run.add_argument("--namespace", required=True)
    run.add_argument("--deployment", required=True)
    run.add_argument("--ingress-report", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--ca-file", type=Path)
    run.add_argument("--kubectl", default="kubectl")
    run.add_argument("--samples", type=int, default=DEFAULT_SAMPLE_COUNT)
    run.add_argument(
        "--interval-milliseconds", type=int, default=DEFAULT_INTERVAL_MILLISECONDS
    )
    run.add_argument(
        "--minimum-window-seconds", type=int, default=DEFAULT_MINIMUM_WINDOW_SECONDS
    )
    run.add_argument(
        "--minimum-baseline-seconds", type=int, default=DEFAULT_BASELINE_SECONDS
    )
    run.add_argument(
        "--minimum-post-recovery-seconds",
        type=int,
        default=DEFAULT_POST_RECOVERY_SECONDS,
    )
    run.add_argument(
        "--minimum-availability-basis-points",
        type=int,
        default=DEFAULT_MINIMUM_AVAILABILITY_BASIS_POINTS,
    )
    run.add_argument(
        "--maximum-p95-latency-milliseconds",
        type=int,
        default=DEFAULT_MAXIMUM_P95_LATENCY_MILLISECONDS,
    )
    run.add_argument(
        "--request-timeout-milliseconds",
        type=int,
        default=DEFAULT_REQUEST_TIMEOUT_MILLISECONDS,
    )
    run.add_argument(
        "--maximum-recovery-seconds",
        type=int,
        default=DEFAULT_MAXIMUM_RECOVERY_SECONDS,
    )
    run.add_argument("--allow-disruption", action="store_true")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--ingress-report", type=Path, required=True)
    verify.add_argument("--require-clean", action="store_true")
    verify.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            report = qualify(
                base_url=args.base_url,
                token_file=args.token_file,
                image_digest=args.image_digest,
                context=args.context,
                namespace=args.namespace,
                deployment_name=args.deployment,
                ingress_report_path=args.ingress_report,
                output=args.output,
                allow_disruption=args.allow_disruption,
                ca_file=args.ca_file,
                kubectl_binary=args.kubectl,
                sample_count=args.samples,
                interval_milliseconds=args.interval_milliseconds,
                minimum_window_seconds=args.minimum_window_seconds,
                minimum_baseline_seconds=args.minimum_baseline_seconds,
                minimum_post_recovery_seconds=args.minimum_post_recovery_seconds,
                minimum_availability_basis_points=args.minimum_availability_basis_points,
                maximum_p95_latency_milliseconds=args.maximum_p95_latency_milliseconds,
                request_timeout_milliseconds=args.request_timeout_milliseconds,
                maximum_recovery_seconds=args.maximum_recovery_seconds,
            )
        else:
            report = verify_report(
                report_path=args.report,
                ingress_report_path=args.ingress_report,
                require_clean=args.require_clean,
                require_qualified=args.require_qualified,
            )
    except CustomerContinuityQualificationError as exc:
        print(str(exc), file=os.sys.stderr)
        return 1
    print(
        "customer continuity qualification: " + str(report["spec"]["status"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
