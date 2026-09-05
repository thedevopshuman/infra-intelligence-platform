#!/usr/bin/env python3
"""Build and verify minimized multi-node Kubernetes availability evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
API_VERSION = "iip.platform/v1alpha1"
KIND = "KubernetesAvailabilityQualificationReport"
PROFILE = "local-multi-node-kind-v1"
COMPONENTS = (
    ("control-plane-api", "iip-infra-intelligence", "iip-infra-intelligence"),
    ("workflow-worker", "iip-infra-intelligence-worker", "iip-infra-intelligence-worker"),
    ("otlp-receiver", "iip-infra-intelligence-otlp-receiver", "iip-infra-intelligence-otlp-receiver"),
)
PHASES = ("baseline", "disruption", "recovery")
PROBES = (
    ("control-plane-api", "http-json", "/v1/system/version"),
    ("otlp-metrics", "otlp-http-protobuf", "/v1/metrics"),
)
CHECK_IDS = (
    "source-binding",
    "isolated-cluster",
    "immutable-image",
    "zero-unavailable-rollout",
    "component-pdbs",
    "hard-topology-spread",
    "durable-intake-seed",
    "component-baseline",
    "voluntary-drain",
    "disruption-capacity",
    "api-zero-failure",
    "receiver-zero-failure",
    "component-recovery",
    "output-minimization",
)
LIMITATIONS = (
    "planned-disruption-only",
    "local-single-region",
    "shared-dependencies-not-qualified",
    "involuntary-failure-not-qualified",
)
REPORT_ID = re.compile(r"^kaq_[a-f0-9]{32}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
MIGRATION = re.compile(r"^[0-9]{4}_[a-z0-9_]+\.sql$")
TOOL_VERSION = re.compile(r"^v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
FORBIDDEN_RETAINED_KEYS = frozenset(
    {
        "clusterName",
        "namespace",
        "nodeName",
        "podName",
        "hostname",
        "endpoint",
        "url",
        "token",
        "ipAddress",
        "responseBody",
        "rawError",
    }
)


class KubernetesAvailabilityQualificationError(RuntimeError):
    """Stable qualification failure without environment details."""


def _fail(code: str) -> None:
    raise KubernetesAvailabilityQualificationError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _timestamp(value: datetime | None = None) -> str:
    instant = value or datetime.now(timezone.utc)
    return instant.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: object) -> None:
    if not isinstance(value, str):
        _fail("kubernetes-availability.report.timestamp-invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("kubernetes-availability.report.timestamp-invalid")
    if parsed.tzinfo is None:
        _fail("kubernetes-availability.report.timestamp-invalid")


def _run(command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            list(command),
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("kubernetes-availability.source.unavailable")
    if completed.returncode != 0:
        _fail("kubernetes-availability.source.unavailable")
    return completed.stdout


def _git_state() -> tuple[str, bool]:
    revision = _run(("git", "rev-parse", "HEAD")).strip()
    dirty = bool(
        _run(("git", "status", "--porcelain", "--untracked-files=normal")).strip()
    )
    if REVISION.fullmatch(revision) is None:
        _fail("kubernetes-availability.source.invalid")
    return revision, dirty


def _repository_identity() -> dict[str, str]:
    try:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        application_version = project["project"]["version"]
        chart_text = (ROOT / "deploy/helm/infra-intelligence/Chart.yaml").read_text(
            encoding="utf-8"
        )
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        _fail("kubernetes-availability.source.identity-invalid")
    chart_match = re.search(r"^version:\s*([^\s]+)\s*$", chart_text, re.MULTILINE)
    migrations = sorted((ROOT / "src/iip/adapters/postgres/migrations").glob("*.sql"))
    if (
        not isinstance(application_version, str)
        or SEMVER.fullmatch(application_version) is None
        or chart_match is None
        or SEMVER.fullmatch(chart_match.group(1)) is None
        or not migrations
        or MIGRATION.fullmatch(migrations[-1].name) is None
    ):
        _fail("kubernetes-availability.source.identity-invalid")
    return {
        "applicationVersion": application_version,
        "chartVersion": chart_match.group(1),
        "requiredMigration": migrations[-1].name,
    }


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _list_items(document: object, code: str) -> list[Mapping[str, Any]]:
    root = _mapping(document, code)
    items = root.get("items")
    if root.get("kind") != "List" or not isinstance(items, list):
        _fail(code)
    result: list[Mapping[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            _fail(code)
        result.append(item)
    return result


def _object(
    items: Sequence[Mapping[str, Any]], kind: str, name: str, code: str
) -> Mapping[str, Any]:
    matches = [
        item
        for item in items
        if item.get("kind") == kind
        and isinstance(item.get("metadata"), Mapping)
        and item["metadata"].get("name") == name
    ]
    if len(matches) != 1:
        _fail(code)
    return matches[0]


def _condition_ready(document: Mapping[str, Any]) -> bool:
    status = document.get("status")
    if not isinstance(status, Mapping):
        return False
    conditions = status.get("conditions", [])
    if not isinstance(conditions, list):
        return False
    return any(
        isinstance(item, Mapping)
        and item.get("type") == "Ready"
        and item.get("status") == "True"
        for item in conditions
    )


def _node_state(document: object, target_node: str, phase: str) -> dict[str, Any]:
    items = _list_items(document, "kubernetes-availability.nodes.invalid")
    nodes = [item for item in items if item.get("kind") == "Node"]
    if len(nodes) != 3 or any(not _condition_ready(item) for item in nodes):
        _fail("kubernetes-availability.nodes.topology-invalid")
    control_planes: list[str] = []
    workers: list[str] = []
    target: Mapping[str, Any] | None = None
    for node in nodes:
        metadata = _mapping(node.get("metadata"), "kubernetes-availability.nodes.invalid")
        spec = _mapping(node.get("spec", {}), "kubernetes-availability.nodes.invalid")
        name = metadata.get("name")
        labels = metadata.get("labels", {})
        if not isinstance(name, str) or not isinstance(labels, Mapping):
            _fail("kubernetes-availability.nodes.invalid")
        if "node-role.kubernetes.io/control-plane" in labels:
            control_planes.append(name)
        else:
            workers.append(name)
        if name == target_node:
            target = node
            target_unschedulable = spec.get("unschedulable") is True
    if len(control_planes) != 1 or len(workers) != 2 or target is None or target_node not in workers:
        _fail("kubernetes-availability.nodes.topology-invalid")
    expected_unschedulable = phase == "disruption"
    if target_unschedulable is not expected_unschedulable:
        _fail(f"kubernetes-availability.nodes.{phase}-cordon-invalid")
    return {
        "nodeCount": 3,
        "workerNodeCount": 2,
        "nodeNames": sorted(control_planes + workers),
        "workerNames": sorted(workers),
    }


def _selector(document: Mapping[str, Any], code: str) -> Mapping[str, Any]:
    spec = _mapping(document.get("spec"), code)
    selector = _mapping(spec.get("selector"), code)
    labels = _mapping(selector.get("matchLabels"), code)
    if set(selector) != {"matchLabels"}:
        _fail(code)
    return labels


def _component_state(
    snapshot: object,
    *,
    component_id: str,
    deployment_name: str,
    pdb_name: str,
    phase: str,
    target_node: str,
) -> dict[str, int]:
    code = f"kubernetes-availability.component.{component_id}.{phase}-invalid"
    items = _list_items(snapshot, code)
    deployment = _object(items, "Deployment", deployment_name, code)
    pdb = _object(items, "PodDisruptionBudget", pdb_name, code)
    deployment_spec = _mapping(deployment.get("spec"), code)
    deployment_status = _mapping(deployment.get("status", {}), code)
    selector = _selector(deployment, code)
    strategy = _mapping(deployment_spec.get("strategy"), code)
    rolling = _mapping(strategy.get("rollingUpdate"), code)
    template = _mapping(deployment_spec.get("template"), code)
    template_spec = _mapping(template.get("spec"), code)
    constraints = template_spec.get("topologySpreadConstraints")
    if (
        deployment_spec.get("replicas") != 2
        or strategy.get("type") != "RollingUpdate"
        or rolling.get("maxUnavailable") != 0
        or rolling.get("maxSurge") != 1
        or selector.get("app.kubernetes.io/component") != component_id
        or not isinstance(constraints, list)
        or len(constraints) != 1
    ):
        _fail(code)
    constraint = _mapping(constraints[0], code)
    constraint_selector = _mapping(constraint.get("labelSelector"), code)
    if (
        constraint.get("topologyKey") != "kubernetes.io/hostname"
        or constraint.get("maxSkew") != 1
        or constraint.get("minDomains") != 2
        or constraint.get("whenUnsatisfiable") != "DoNotSchedule"
        or constraint_selector.get("matchLabels") != selector
    ):
        _fail(code)
    pdb_spec = _mapping(pdb.get("spec"), code)
    pdb_status = _mapping(pdb.get("status", {}), code)
    if pdb_spec.get("minAvailable") != 1 or _selector(pdb, code) != selector:
        _fail(code)
    pods: list[Mapping[str, Any]] = []
    for item in items:
        if item.get("kind") != "Pod":
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, Mapping) or metadata.get("deletionTimestamp") is not None:
            continue
        labels = metadata.get("labels", {})
        if isinstance(labels, Mapping) and all(labels.get(key) == value for key, value in selector.items()):
            pods.append(item)
    ready_pods = [item for item in pods if _condition_ready(item)]
    nodes: set[str] = set()
    for pod in ready_pods:
        pod_spec = _mapping(pod.get("spec", {}), code)
        node_name = pod_spec.get("nodeName")
        if not isinstance(node_name, str) or not node_name:
            _fail(code)
        nodes.add(node_name)
    if phase == "disruption" and any(
        _mapping(pod.get("spec", {}), code).get("nodeName") == target_node for pod in pods
    ):
        _fail("kubernetes-availability.disruption.target-not-drained")
    ready = deployment_status.get("readyReplicas", 0)
    unavailable = deployment_status.get("unavailableReplicas", 0)
    disruptions_allowed = pdb_status.get("disruptionsAllowed")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (ready, unavailable, disruptions_allowed)):
        _fail(code)
    expected = (2, 0, 2, 1) if phase != "disruption" else (1, 1, 1, 0)
    actual = (ready, unavailable, len(nodes), disruptions_allowed)
    if actual != expected or len(ready_pods) != ready:
        _fail(code)
    return {
        "readyReplicas": ready,
        "unavailableReplicas": unavailable,
        "domainCount": len(nodes),
        "pdbDisruptionsAllowed": disruptions_allowed,
    }


def _probe_measurements(
    document: object,
) -> tuple[dict[str, bool], list[dict[str, Any]], int]:
    root = _mapping(document, "kubernetes-availability.probes.invalid")
    if set(root) != {"seed", "phases"}:
        _fail("kubernetes-availability.probes.invalid")
    seed = root.get("seed")
    if seed != {"resourceAccepted": True, "metricAccepted": True}:
        _fail("kubernetes-availability.probes.seed-failed")
    raw_phases = root.get("phases")
    if not isinstance(raw_phases, Mapping) or set(raw_phases) != set(PHASES):
        _fail("kubernetes-availability.probes.invalid")
    probes: list[dict[str, Any]] = []
    total = 0
    for probe_id, protocol, path in PROBES:
        phases: list[dict[str, Any]] = []
        for phase in PHASES:
            phase_value = _mapping(
                raw_phases[phase], "kubernetes-availability.probes.invalid"
            )
            value = _mapping(
                phase_value.get(probe_id), "kubernetes-availability.probes.invalid"
            )
            if set(value) != {"attempts", "successes", "failures"}:
                _fail("kubernetes-availability.probes.invalid")
            attempts = value.get("attempts")
            successes = value.get("successes")
            failures = value.get("failures")
            if (
                any(
                    isinstance(item, bool) or not isinstance(item, int)
                    for item in (attempts, successes, failures)
                )
                or attempts < 20
                or successes != attempts
                or failures != 0
            ):
                _fail(f"kubernetes-availability.probes.{probe_id}.{phase}-failed")
            total += attempts
            phases.append(
                {
                    "id": phase,
                    "attempts": attempts,
                    "successes": successes,
                    "failures": failures,
                }
            )
        probes.append(
            {"id": probe_id, "protocol": protocol, "path": path, "phases": phases}
        )
    return dict(seed), probes, total


def _platform_name() -> str:
    machine = platform.machine().lower()
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine)
    return f"linux/{architecture}"


def _checked_version(value: str, code: str) -> str:
    normalized = value.strip()
    if TOOL_VERSION.fullmatch(normalized) is None:
        _fail(code)
    return normalized


def build_report(
    *,
    baseline_nodes: object,
    disruption_nodes: object,
    recovery_nodes: object,
    baseline_snapshot: object,
    disruption_snapshot: object,
    recovery_snapshot: object,
    probe_state: object,
    cluster_name: str,
    namespace: str,
    target_node: str,
    image_digest: str,
    kubernetes_version: str,
    kind_version: str,
    docker_version: str,
    containerd_version: str,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Derive a qualified report from raw, ephemeral Kubernetes evidence."""

    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", cluster_name)
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", namespace)
        or not target_node
        or DIGEST.fullmatch(image_digest) is None
    ):
        _fail("kubernetes-availability.input.invalid")
    revision, dirty = _git_state()
    identity = _repository_identity()
    node_states = {
        "baseline": _node_state(baseline_nodes, target_node, "baseline"),
        "disruption": _node_state(disruption_nodes, target_node, "disruption"),
        "recovery": _node_state(recovery_nodes, target_node, "recovery"),
    }
    if any(
        value["nodeNames"] != node_states["baseline"]["nodeNames"]
        for value in node_states.values()
    ):
        _fail("kubernetes-availability.nodes.membership-changed")
    snapshots = {
        "baseline": baseline_snapshot,
        "disruption": disruption_snapshot,
        "recovery": recovery_snapshot,
    }
    components: list[dict[str, Any]] = []
    for component_id, deployment_name, pdb_name in COMPONENTS:
        states = {
            phase: _component_state(
                snapshots[phase],
                component_id=component_id,
                deployment_name=deployment_name,
                pdb_name=pdb_name,
                phase=phase,
                target_node=target_node,
            )
            for phase in PHASES
        }
        components.append(
            {
                "id": component_id,
                "desiredReplicas": 2,
                "baseline": states["baseline"],
                "disruption": states["disruption"],
                "recovery": states["recovery"],
            }
        )
    intake_seed, probes, total_probe_attempts = _probe_measurements(probe_state)
    subject = {
        **identity,
        "sourceRevision": revision,
        "imageDigest": image_digest,
    }
    spec: dict[str, Any] = {
        "status": "qualified",
        "qualificationLevel": PROFILE,
        "subject": subject,
        "environment": {
            "platform": _platform_name(),
            "kubernetesVersion": _checked_version(
                kubernetes_version, "kubernetes-availability.environment.kubernetes-version-invalid"
            ),
            "kindVersion": _checked_version(
                kind_version, "kubernetes-availability.environment.kind-version-invalid"
            ),
            "dockerVersion": _checked_version(
                docker_version, "kubernetes-availability.environment.docker-version-invalid"
            ),
            "containerdVersion": _checked_version(
                containerd_version,
                "kubernetes-availability.environment.containerd-version-invalid",
            ),
            "nodeCount": 3,
            "workerNodeCount": 2,
            "clusterBindingDigest": _digest(
                {
                    "cluster": cluster_name,
                    "namespace": namespace,
                    "nodes": node_states["baseline"]["nodeNames"],
                    "imageDigest": image_digest,
                }
            ),
        },
        "policy": {
            "rollout": {"maxUnavailable": 0, "maxSurge": 1},
            "podDisruptionBudget": {"minAvailable": 1},
            "topologySpread": {
                "topologyKey": "kubernetes.io/hostname",
                "maxSkew": 1,
                "minDomains": 2,
                "whenUnsatisfiable": "DoNotSchedule",
            },
        },
        "components": components,
        "intakeSeed": intake_seed,
        "disruption": {
            "method": "kubectl-drain",
            "targetNodeDigest": _digest(target_node),
            "nodeCordoned": True,
            "nodeDrained": True,
            "nodeUncordoned": True,
            "evictedComponentCount": 3,
        },
        "probes": probes,
        "summary": {
            "totalChecks": len(CHECK_IDS),
            "passedChecks": len(CHECK_IDS),
            "failedChecks": 0,
            "totalProbeAttempts": total_probe_attempts,
            "failedProbeAttempts": 0,
            "overallStatus": "qualified",
        },
        "checks": [{"id": check_id, "status": "passed"} for check_id in CHECK_IDS],
        "limitations": list(LIMITATIONS),
    }
    report: dict[str, Any] = {
        "apiVersion": API_VERSION,
        "kind": KIND,
        "metadata": {
            "id": "",
            "generatedAt": _timestamp(generated_at),
            "sourceRevision": revision,
            "sourceDirty": dirty,
        },
        "spec": spec,
    }
    report["metadata"]["id"] = _report_id(report)
    validate_report(report)
    return report


def _report_id(report: Mapping[str, Any]) -> str:
    content = copy.deepcopy(dict(report))
    metadata = _mapping(content.get("metadata"), "kubernetes-availability.report.invalid")
    metadata = dict(metadata)
    metadata.pop("id", None)
    content["metadata"] = metadata
    return "kaq_" + hashlib.sha256(_canonical(content)).hexdigest()[:32]


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


def _validate_schema(report: Mapping[str, Any]) -> None:
    try:
        from jsonschema import Draft202012Validator, FormatChecker

        schema = json.loads(
            (ROOT / "contracts/schemas/kubernetes-availability-qualification-report.schema.json").read_text(
                encoding="utf-8"
            )
        )
        errors = list(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(report)
        )
    except (ImportError, OSError, json.JSONDecodeError):
        _fail("kubernetes-availability.report.schema-unavailable")
    if errors:
        _fail("kubernetes-availability.report.schema-invalid")


def validate_report(
    report: Mapping[str, Any], *, require_clean: bool = False, require_current: bool = False
) -> None:
    """Validate schema, arithmetic, closed ordering, content ID, and source binding."""

    _validate_schema(report)
    if any(key in FORBIDDEN_RETAINED_KEYS for key in _walk_keys(report)):
        _fail("kubernetes-availability.report.sensitive-field")
    metadata = _mapping(report.get("metadata"), "kubernetes-availability.report.invalid")
    spec = _mapping(report.get("spec"), "kubernetes-availability.report.invalid")
    report_id = metadata.get("id")
    _parse_timestamp(metadata.get("generatedAt"))
    if (
        report.get("apiVersion") != API_VERSION
        or report.get("kind") != KIND
        or not isinstance(report_id, str)
        or REPORT_ID.fullmatch(report_id) is None
        or report_id != _report_id(report)
        or spec.get("status") != "qualified"
        or spec.get("qualificationLevel") != PROFILE
    ):
        _fail("kubernetes-availability.report.invalid")
    subject = _mapping(spec.get("subject"), "kubernetes-availability.report.invalid")
    if metadata.get("sourceRevision") != subject.get("sourceRevision"):
        _fail("kubernetes-availability.report.source-mismatch")
    if require_clean and metadata.get("sourceDirty") is not False:
        _fail("kubernetes-availability.report.source-dirty")
    components = spec.get("components")
    if not isinstance(components, list) or [item.get("id") for item in components if isinstance(item, Mapping)] != [item[0] for item in COMPONENTS]:
        _fail("kubernetes-availability.report.components-invalid")
    baseline_expected = {
        "readyReplicas": 2,
        "unavailableReplicas": 0,
        "domainCount": 2,
        "pdbDisruptionsAllowed": 1,
    }
    disruption_expected = {
        "readyReplicas": 1,
        "unavailableReplicas": 1,
        "domainCount": 1,
        "pdbDisruptionsAllowed": 0,
    }
    for component in components:
        component = _mapping(component, "kubernetes-availability.report.components-invalid")
        if (
            component.get("desiredReplicas") != 2
            or component.get("baseline") != baseline_expected
            or component.get("disruption") != disruption_expected
            or component.get("recovery") != baseline_expected
        ):
            _fail("kubernetes-availability.report.components-invalid")
    if spec.get("intakeSeed") != {
        "resourceAccepted": True,
        "metricAccepted": True,
    }:
        _fail("kubernetes-availability.report.intake-seed-invalid")
    probes = spec.get("probes")
    if not isinstance(probes, list) or len(probes) != len(PROBES):
        _fail("kubernetes-availability.report.probes-invalid")
    total = 0
    for probe, expected in zip(probes, PROBES):
        probe = _mapping(probe, "kubernetes-availability.report.probes-invalid")
        if (probe.get("id"), probe.get("protocol"), probe.get("path")) != expected:
            _fail("kubernetes-availability.report.probes-invalid")
        phases = probe.get("phases")
        if not isinstance(phases, list) or [phase.get("id") for phase in phases if isinstance(phase, Mapping)] != list(PHASES):
            _fail("kubernetes-availability.report.probes-invalid")
        for phase in phases:
            phase = _mapping(phase, "kubernetes-availability.report.probes-invalid")
            attempts = phase.get("attempts")
            if (
                not isinstance(attempts, int)
                or isinstance(attempts, bool)
                or attempts < 20
                or phase.get("successes") != attempts
                or phase.get("failures") != 0
            ):
                _fail("kubernetes-availability.report.probes-invalid")
            total += attempts
    checks = spec.get("checks")
    if not isinstance(checks, list) or checks != [
        {"id": check_id, "status": "passed"} for check_id in CHECK_IDS
    ]:
        _fail("kubernetes-availability.report.checks-invalid")
    summary = _mapping(spec.get("summary"), "kubernetes-availability.report.invalid")
    if summary != {
        "totalChecks": len(CHECK_IDS),
        "passedChecks": len(CHECK_IDS),
        "failedChecks": 0,
        "totalProbeAttempts": total,
        "failedProbeAttempts": 0,
        "overallStatus": "qualified",
    } or spec.get("limitations") != list(LIMITATIONS):
        _fail("kubernetes-availability.report.summary-invalid")
    if require_current:
        revision, dirty = _git_state()
        identity = _repository_identity()
        if (
            metadata.get("sourceRevision") != revision
            or metadata.get("sourceDirty") != dirty
            or subject.get("applicationVersion") != identity["applicationVersion"]
            or subject.get("chartVersion") != identity["chartVersion"]
            or subject.get("requiredMigration") != identity["requiredMigration"]
        ):
            _fail("kubernetes-availability.report.source-stale")


def _load(path: Path, code: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _fail(code)


def _write(path: Path, document: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    except OSError:
        _fail("kubernetes-availability.report.write-failed")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="build a report from raw cluster evidence")
    for argument in (
        "baseline-nodes",
        "disruption-nodes",
        "recovery-nodes",
        "baseline-snapshot",
        "disruption-snapshot",
        "recovery-snapshot",
        "probe-state",
    ):
        build.add_argument(f"--{argument}", required=True, type=Path)
    for argument in (
        "cluster-name",
        "namespace",
        "target-node",
        "image-digest",
        "kubernetes-version",
        "kind-version",
        "docker-version",
        "containerd-version",
    ):
        build.add_argument(f"--{argument}", required=True)
    build.add_argument("--output", required=True, type=Path)
    build.add_argument("--require-clean", action="store_true")
    verify = subparsers.add_parser("verify", help="verify retained evidence offline")
    verify.add_argument("--report", required=True, type=Path)
    verify.add_argument("--require-clean", action="store_true")
    verify.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            report = build_report(
                baseline_nodes=_load(args.baseline_nodes, "kubernetes-availability.nodes.unavailable"),
                disruption_nodes=_load(args.disruption_nodes, "kubernetes-availability.nodes.unavailable"),
                recovery_nodes=_load(args.recovery_nodes, "kubernetes-availability.nodes.unavailable"),
                baseline_snapshot=_load(args.baseline_snapshot, "kubernetes-availability.snapshot.unavailable"),
                disruption_snapshot=_load(args.disruption_snapshot, "kubernetes-availability.snapshot.unavailable"),
                recovery_snapshot=_load(args.recovery_snapshot, "kubernetes-availability.snapshot.unavailable"),
                probe_state=_load(args.probe_state, "kubernetes-availability.probes.unavailable"),
                cluster_name=args.cluster_name,
                namespace=args.namespace,
                target_node=args.target_node,
                image_digest=args.image_digest,
                kubernetes_version=args.kubernetes_version,
                kind_version=args.kind_version,
                docker_version=args.docker_version,
                containerd_version=args.containerd_version,
            )
            if args.require_clean and report["metadata"]["sourceDirty"]:
                _fail("kubernetes-availability.report.source-dirty")
            _write(args.output, report)
            print(f"wrote qualified Kubernetes availability report to {args.output}")
        else:
            report = _load(args.report, "kubernetes-availability.report.unavailable")
            if not isinstance(report, Mapping):
                _fail("kubernetes-availability.report.invalid")
            validate_report(
                report,
                require_clean=args.require_clean,
                require_current=args.require_clean or args.require_qualified,
            )
            print(f"verified qualified Kubernetes availability report {report['metadata']['id']}")
    except KubernetesAvailabilityQualificationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
