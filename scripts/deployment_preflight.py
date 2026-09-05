#!/usr/bin/env python3
"""Evaluate a minimized, source-bound customer deployment preflight profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy" / "helm" / "infra-intelligence"
PROFILE_TEMPLATE = "templates/deployment-profile.yaml"
API_VERSION = "iip.platform/v1alpha1"
KIND = "CustomerDeploymentPreflightReport"
PROFILES = ("production-core-v1", "production-ai-finops-v0")
COMMON_CHECKS = (
    "helm-render",
    "immutable-image",
    "published-image-repository",
    "api-redundancy",
    "worker-enrollment",
    "worker-redundancy",
    "external-database",
    "controlled-migrations",
    "oidc-authentication",
    "external-policy",
    "workload-identity-broker",
    "tls-ingress",
    "network-isolation",
    "pod-disruption-budget",
    "scheduled-backup",
    "evidence-retention",
    "platform-telemetry",
    "evidence-backends",
    "service-account-isolation",
    "test-fixtures-denied",
)
AI_CHECKS = (
    "ai-usage-intake",
    "ai-receiver-mtls",
    "ai-receiver-redundancy",
    "ai-cost-allocation-savings",
    "collector-loss-objective",
)
LIVE_CHECKS = ("cluster-api", "referenced-dependencies")
FAILURE_ERROR_CODES = {
    "helm-render": "preflight.helm.render-failed",
    "immutable-image": "preflight.image.digest-required",
    "published-image-repository": "preflight.image.repository-placeholder",
    "api-redundancy": "preflight.api.replicas-insufficient",
    "worker-enrollment": "preflight.worker.enrollment-required",
    "worker-redundancy": "preflight.worker.replicas-insufficient",
    "external-database": "preflight.database.secret-required",
    "controlled-migrations": "preflight.database.migrations-required",
    "oidc-authentication": "preflight.auth.oidc-required",
    "external-policy": "preflight.policy.external-required",
    "workload-identity-broker": "preflight.credential-broker.external-required",
    "tls-ingress": "preflight.ingress.tls-required",
    "network-isolation": "preflight.network-policy.incomplete",
    "pod-disruption-budget": "preflight.pdb.required",
    "scheduled-backup": "preflight.backup.required",
    "evidence-retention": "preflight.evidence-retention.required",
    "platform-telemetry": "preflight.telemetry.required",
    "evidence-backends": "preflight.evidence-backends.required",
    "service-account-isolation": "preflight.security.workload-isolation-required",
    "test-fixtures-denied": "preflight.test-fixtures.forbidden",
    "ai-usage-intake": "preflight.ai.receiver-required",
    "ai-receiver-mtls": "preflight.ai.receiver-mtls-required",
    "ai-receiver-redundancy": "preflight.ai.receiver-replicas-insufficient",
    "ai-cost-allocation-savings": "preflight.ai.pipeline-required",
    "collector-loss-objective": "preflight.ai.collector-objective-required",
    "cluster-api": "preflight.cluster.unavailable",
    "referenced-dependencies": "preflight.dependencies.incomplete",
}
NOT_RUN_ERROR_CODES = {
    "cluster-api": "preflight.cluster.not-run",
    "referenced-dependencies": "preflight.dependencies.not-run",
}
COMMON_CUSTOMER_REQUIREMENTS = (
    "signed-published-release",
    "customer-oidc-browser-issuer",
    "customer-policy-bundle",
    "customer-workload-identity-broker",
    "customer-collector-pki",
    "customer-postgresql-ha-dr",
    "customer-workload-slo",
)
AI_CUSTOMER_REQUIREMENTS = (
    "live-bedrock-model-region-streaming",
    "authoritative-ai-price-catalog",
    "ai-workload-saving-validation",
)
EXPECTED_PROFILE_KEYS = frozenset(
    {
        "schemaVersion",
        "chart",
        "image",
        "api",
        "worker",
        "database",
        "authentication",
        "policy",
        "credentialBroker",
        "ingress",
        "backup",
        "evidenceRetention",
        "evidenceBackends",
        "telemetry",
        "networkPolicy",
        "podDisruptionBudget",
        "security",
        "receivers",
        "aiEconomics",
        "dependencies",
    }
)
REPORT_ID = re.compile(r"^cdp_[a-f0-9]{32}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")


class DeploymentPreflightError(RuntimeError):
    """Stable deployment-preflight failure."""


def _fail(code: str) -> None:
    raise DeploymentPreflightError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _timestamp(value: datetime | None = None) -> str:
    instant = value or datetime.now(timezone.utc)
    return instant.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _run(
    command: Sequence[str], *, timeout: int = 120, discard_stdout: bool = False
) -> str:
    try:
        completed = subprocess.run(
            list(command),
            cwd=ROOT,
            text=True,
            stdout=subprocess.DEVNULL if discard_stdout else subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("preflight.tool.unavailable")
    if completed.returncode != 0:
        _fail("preflight.command.failed")
    return "" if discard_stdout else completed.stdout


def _git_state() -> tuple[str, bool]:
    revision = _run(("git", "rev-parse", "HEAD")).strip()
    dirty = bool(
        _run(("git", "status", "--porcelain", "--untracked-files=normal")).strip()
    )
    if REVISION.fullmatch(revision) is None:
        _fail("preflight.source.invalid")
    return revision, dirty


def _require_values(values: Sequence[Path]) -> tuple[Path, ...]:
    if not values:
        _fail("preflight.values.required")
    resolved: list[Path] = []
    for candidate in values:
        path = candidate.expanduser().resolve()
        if not path.is_file() or path.stat().st_size < 1:
            _fail("preflight.values.invalid")
        resolved.append(path)
    return tuple(resolved)


def _values_digest(values: Sequence[Path]) -> str:
    return _digest(
        [
            {"position": index, "sizeBytes": path.stat().st_size, "sha256": _file_digest(path)}
            for index, path in enumerate(values)
        ]
    )


def _helm_command(
    helm: str,
    *,
    release_name: str,
    namespace: str,
    values: Sequence[Path],
) -> list[str]:
    command = [
        helm,
        "template",
        release_name,
        str(CHART),
        "--namespace",
        namespace,
    ]
    for path in values:
        command.extend(("--values", str(path)))
    return command


def _profile_from_helm(
    helm: str,
    *,
    release_name: str,
    namespace: str,
    values: Sequence[Path],
) -> Mapping[str, Any]:
    command = _helm_command(
        helm, release_name=release_name, namespace=namespace, values=values
    )
    _run(command, discard_stdout=True)
    rendered = _run((*command, "--show-only", PROFILE_TEMPLATE))
    match = re.search(r"^  profile\.json: (.+)$", rendered, re.MULTILINE)
    if match is None:
        _fail("preflight.profile.missing")
    try:
        document = json.loads(json.loads(match.group(1)))
    except (TypeError, json.JSONDecodeError):
        _fail("preflight.profile.invalid")
    if not isinstance(document, dict) or frozenset(document) != EXPECTED_PROFILE_KEYS:
        _fail("preflight.profile.invalid")
    _validate_rendered_profile(document)
    return document


def _object(parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        _fail("preflight.profile.invalid")
    return value


def _boolean(parent: Mapping[str, Any], key: str) -> bool:
    value = parent.get(key)
    if not isinstance(value, bool):
        _fail("preflight.profile.invalid")
    return value


def _integer(parent: Mapping[str, Any], key: str) -> int:
    value = parent.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        _fail("preflight.profile.invalid")
    return value


def _string(parent: Mapping[str, Any], key: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str):
        _fail("preflight.profile.invalid")
    return value


def _dependencies(profile: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = profile.get("dependencies")
    if not isinstance(raw, list) or len(raw) > 64:
        _fail("preflight.profile.invalid")
    dependencies: list[Mapping[str, Any]] = []
    purposes: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"purpose", "kind", "name", "keys"}:
            _fail("preflight.profile.invalid")
        purpose = item.get("purpose")
        kind = item.get("kind")
        name = item.get("name")
        keys = item.get("keys")
        if (
            not isinstance(purpose, str)
            or not purpose
            or purpose in purposes
            or kind not in {"Secret", "ConfigMap", "PersistentVolumeClaim"}
            or not isinstance(name, str)
            or len(name) > 253
            or NAME.fullmatch(name) is None
            or not isinstance(keys, list)
            or len(keys) > 16
            or len(keys) != len(set(keys))
            or any(not isinstance(key, str) or not key or len(key) > 253 for key in keys)
            or kind == "PersistentVolumeClaim" and keys
        ):
            _fail("preflight.profile.invalid")
        purposes.add(purpose)
        dependencies.append(item)
    return tuple(dependencies)


def _validate_rendered_profile(profile: Mapping[str, Any]) -> None:
    if profile.get("schemaVersion") != "1":
        _fail("preflight.profile.invalid")
    chart = _object(profile, "chart")
    if (
        set(chart) != {"name", "version", "applicationVersion"}
        or chart.get("name") != "infra-intelligence"
        or not isinstance(chart.get("version"), str)
        or SEMVER.fullmatch(chart["version"]) is None
        or not isinstance(chart.get("applicationVersion"), str)
        or SEMVER.fullmatch(chart["applicationVersion"]) is None
    ):
        _fail("preflight.profile.invalid")
    image = _object(profile, "image")
    if set(image) != {"repository", "digest"}:
        _fail("preflight.profile.invalid")
    _string(image, "repository")
    _string(image, "digest")
    _integer(_object(profile, "api"), "replicaCount")
    worker = _object(profile, "worker")
    _boolean(worker, "enabled")
    _integer(worker, "replicaCount")
    _integer(worker, "tenantCount")
    for section, booleans in (
        ("database", ("existingSecretConfigured", "migrationsEnabled")),
        ("ingress", ("enabled", "classConfigured", "hostConfigured", "tlsConfigured", "redirectConfigured")),
        ("backup", ("enabled", "destinationConfigured")),
        ("evidenceRetention", ("enabled",)),
        ("telemetry", ("metricsEnabled", "tracesEnabled", "endpointConfigured", "collectorQueueLossConfigured")),
        (
            "networkPolicy",
            (
                "enabled",
                "ingressController",
                "databaseEgress",
                "otlpEgress",
                "prometheusEgress",
                "lokiEgress",
                "opensearchEgress",
                "kubernetesApiEgress",
                "oidcEgress",
                "policyEgress",
                "credentialBrokerEgress",
                "otlpReceiverIngress",
            ),
        ),
        ("security", ("serviceAccountTokenAutomount", "runAsNonRoot", "readOnlyRootFilesystem", "allowPrivilegeEscalation")),
        ("aiEconomics", ("attributionEnabled", "costEngineEnabled", "savingsEngineEnabled", "allocationReportingEnabled", "attributionTestFixtures", "priceTestFixtures")),
    ):
        item = _object(profile, section)
        if set(item) != set(booleans):
            _fail("preflight.profile.invalid")
        for key in booleans:
            _boolean(item, key)
    auth = _object(profile, "authentication")
    policy = _object(profile, "policy")
    broker = _object(profile, "credentialBroker")
    if set(auth) != {"mode", "oidcConfigurationReviewed"}:
        _fail("preflight.profile.invalid")
    if set(policy) != {"mode", "configurationReviewed"} or set(broker) != {
        "mode",
        "configurationReviewed",
    }:
        _fail("preflight.profile.invalid")
    _string(auth, "mode")
    _boolean(auth, "oidcConfigurationReviewed")
    for item in (policy, broker):
        _string(item, "mode")
        _boolean(item, "configurationReviewed")
    evidence = _object(profile, "evidenceBackends")
    if set(evidence) != {"metrics", "logs", "kubernetesEvents", "context"}:
        _fail("preflight.profile.invalid")
    for key in evidence:
        _string(evidence, key)
    pdb = _object(profile, "podDisruptionBudget")
    if set(pdb) != {"enabled", "minAvailable"}:
        _fail("preflight.profile.invalid")
    _boolean(pdb, "enabled")
    _integer(pdb, "minAvailable")
    receivers = _object(profile, "receivers")
    if set(receivers) != {
        "metricsEnabled",
        "logsEnabled",
        "aiUsageEnabled",
        "replicaCount",
        "tlsMode",
        "crlConfigured",
    }:
        _fail("preflight.profile.invalid")
    for key in ("metricsEnabled", "logsEnabled", "aiUsageEnabled", "crlConfigured"):
        _boolean(receivers, key)
    _integer(receivers, "replicaCount")
    _string(receivers, "tlsMode")
    _dependencies(profile)


def _check(check_id: str, condition: bool, error_code: str) -> dict[str, str]:
    if condition:
        return {"id": check_id, "status": "passed"}
    return {"id": check_id, "status": "failed", "errorCode": error_code}


def _static_checks(
    profile_name: str, profile: Mapping[str, Any]
) -> list[dict[str, str]]:
    image = _object(profile, "image")
    api = _object(profile, "api")
    worker = _object(profile, "worker")
    database = _object(profile, "database")
    auth = _object(profile, "authentication")
    policy = _object(profile, "policy")
    broker = _object(profile, "credentialBroker")
    ingress = _object(profile, "ingress")
    network = _object(profile, "networkPolicy")
    pdb = _object(profile, "podDisruptionBudget")
    backup = _object(profile, "backup")
    retention = _object(profile, "evidenceRetention")
    telemetry = _object(profile, "telemetry")
    evidence = _object(profile, "evidenceBackends")
    security = _object(profile, "security")
    ai = _object(profile, "aiEconomics")
    receivers = _object(profile, "receivers")

    logs_network = (
        evidence["logs"] == "loki" and network["lokiEgress"] is True
    ) or (
        evidence["logs"] == "opensearch" and network["opensearchEgress"] is True
    )
    network_ready = all(
        network[key] is True
        for key in (
            "enabled",
            "ingressController",
            "databaseEgress",
            "otlpEgress",
            "prometheusEgress",
            "kubernetesApiEgress",
            "oidcEgress",
            "policyEgress",
            "credentialBrokerEgress",
        )
    ) and logs_network
    checks = [
        _check("helm-render", True, "preflight.helm.render-failed"),
        _check("immutable-image", DIGEST.fullmatch(image["digest"]) is not None, "preflight.image.digest-required"),
        _check("published-image-repository", image["repository"] != "ghcr.io/replace-me/infra-intelligence-platform", "preflight.image.repository-placeholder"),
        _check("api-redundancy", api["replicaCount"] >= 2, "preflight.api.replicas-insufficient"),
        _check("worker-enrollment", worker["enabled"] is True and worker["tenantCount"] >= 1, "preflight.worker.enrollment-required"),
        _check("worker-redundancy", worker["replicaCount"] >= 2, "preflight.worker.replicas-insufficient"),
        _check("external-database", database["existingSecretConfigured"] is True, "preflight.database.secret-required"),
        _check("controlled-migrations", database["migrationsEnabled"] is True, "preflight.database.migrations-required"),
        _check("oidc-authentication", auth["mode"] == "oidc" and auth["oidcConfigurationReviewed"] is True, "preflight.auth.oidc-required"),
        _check("external-policy", policy["mode"] == "external-http" and policy["configurationReviewed"] is True, "preflight.policy.external-required"),
        _check("workload-identity-broker", broker["mode"] == "external-http" and broker["configurationReviewed"] is True, "preflight.credential-broker.external-required"),
        _check("tls-ingress", all(ingress[key] is True for key in ("enabled", "classConfigured", "hostConfigured", "tlsConfigured", "redirectConfigured")), "preflight.ingress.tls-required"),
        _check("network-isolation", network_ready, "preflight.network-policy.incomplete"),
        _check("pod-disruption-budget", pdb["enabled"] is True and pdb["minAvailable"] >= 1, "preflight.pdb.required"),
        _check("scheduled-backup", backup["enabled"] is True and backup["destinationConfigured"] is True, "preflight.backup.required"),
        _check("evidence-retention", retention["enabled"] is True, "preflight.evidence-retention.required"),
        _check("platform-telemetry", telemetry["metricsEnabled"] is True and telemetry["tracesEnabled"] is True and telemetry["endpointConfigured"] is True, "preflight.telemetry.required"),
        _check("evidence-backends", evidence["metrics"] == "prometheus" and evidence["logs"] in {"loki", "opensearch"} and evidence["kubernetesEvents"] == "kubernetes-api", "preflight.evidence-backends.required"),
        _check("service-account-isolation", security == {"serviceAccountTokenAutomount": False, "runAsNonRoot": True, "readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False}, "preflight.security.workload-isolation-required"),
        _check("test-fixtures-denied", ai["attributionTestFixtures"] is False and ai["priceTestFixtures"] is False, "preflight.test-fixtures.forbidden"),
    ]
    if profile_name == "production-ai-finops-v0":
        checks.extend(
            (
                _check("ai-usage-intake", receivers["aiUsageEnabled"] is True and network["otlpReceiverIngress"] is True, "preflight.ai.receiver-required"),
                _check("ai-receiver-mtls", receivers["tlsMode"] == "mutual-spiffe" and receivers["crlConfigured"] is True, "preflight.ai.receiver-mtls-required"),
                _check("ai-receiver-redundancy", receivers["replicaCount"] >= 2, "preflight.ai.receiver-replicas-insufficient"),
                _check("ai-cost-allocation-savings", all(ai[key] is True for key in ("attributionEnabled", "costEngineEnabled", "savingsEngineEnabled", "allocationReportingEnabled")), "preflight.ai.pipeline-required"),
                _check("collector-loss-objective", telemetry["collectorQueueLossConfigured"] is True, "preflight.ai.collector-objective-required"),
            )
        )
    return checks


def _tool_versions(helm: str, kubectl: str, *, live: bool) -> dict[str, str]:
    helm_version = _run((helm, "version", "--template", "{{.Version}}")).strip()
    if not helm_version or len(helm_version) > 64:
        _fail("preflight.helm.version-invalid")
    versions = {"helmVersion": helm_version}
    if live:
        try:
            document = json.loads(_run((kubectl, "version", "--client=true", "-o", "json")))
            kubectl_version = document["clientVersion"]["gitVersion"]
        except (KeyError, TypeError, json.JSONDecodeError):
            _fail("preflight.kubectl.version-invalid")
        if not isinstance(kubectl_version, str) or not kubectl_version:
            _fail("preflight.kubectl.version-invalid")
        versions["kubectlVersion"] = kubectl_version
    return versions


def _kubectl_base(kubectl: str, context: str, namespace: str | None = None) -> list[str]:
    command = [kubectl, "--context", context]
    if namespace is not None:
        command.extend(("--namespace", namespace))
    return command


def _cluster_environment(
    kubectl: str, *, context: str, namespace: str
) -> tuple[Mapping[str, str], dict[str, str]]:
    if not context or not namespace:
        _fail("preflight.cluster.binding-required")
    try:
        version_document = json.loads(
            _run((*_kubectl_base(kubectl, context), "version", "-o", "json"))
        )
        server_version = version_document["serverVersion"]["gitVersion"]
        client_version = version_document["clientVersion"]["gitVersion"]
        namespace_uid = _run(
            (
                *_kubectl_base(kubectl, context),
                "get",
                "namespace",
                namespace,
                "-o",
                "jsonpath={.metadata.uid}",
            )
        ).strip()
    except (KeyError, TypeError, json.JSONDecodeError, DeploymentPreflightError):
        return {}, {
            "id": "cluster-api",
            "status": "failed",
            "errorCode": "preflight.cluster.unavailable",
        }
    if (
        not isinstance(server_version, str)
        or not server_version
        or not isinstance(client_version, str)
        or not client_version
        or not namespace_uid
    ):
        return {}, {
            "id": "cluster-api",
            "status": "failed",
            "errorCode": "preflight.cluster.unavailable",
        }
    binding = _digest(
        {
            "context": context,
            "namespace": namespace,
            "namespaceUid": namespace_uid,
            "serverVersion": server_version,
        }
    )
    return {
        "kubernetesVersion": server_version,
        "kubectlVersion": client_version,
        "clusterBindingDigest": binding,
        "namespaceDigest": _digest(namespace),
    }, {"id": "cluster-api", "status": "passed"}


def _observe_dependency(
    kubectl: str,
    *,
    context: str,
    namespace: str,
    dependency: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any] | None]:
    kind = str(dependency["kind"])
    name = str(dependency["name"])
    if kind == "PersistentVolumeClaim":
        template = '{{.metadata.uid}}{{"\\n"}}{{.metadata.resourceVersion}}{{"\\n"}}{{.status.phase}}{{"\\n"}}'
    else:
        template = '{{.metadata.uid}}{{"\\n"}}{{.metadata.resourceVersion}}{{"\\n"}}{{range $k,$v := .data}}{{$k}}{{"\\n"}}{{end}}'
    try:
        output = _run(
            (
                *_kubectl_base(kubectl, context, namespace),
                "get",
                kind.lower(),
                name,
                "--ignore-not-found",
                "-o",
                f"go-template={template}",
            ),
            timeout=30,
        )
    except DeploymentPreflightError:
        return "unavailable", None
    if not output.strip():
        return "missing", None
    lines = output.splitlines()
    if len(lines) < 2 or not lines[0] or not lines[1]:
        return "unavailable", None
    if kind == "PersistentVolumeClaim":
        if len(lines) < 3 or lines[2] != "Bound":
            return "invalid", {"kind": kind, "uid": lines[0], "resourceVersion": lines[1], "phase": lines[2] if len(lines) > 2 else ""}
        return "observed", {"kind": kind, "uid": lines[0], "resourceVersion": lines[1], "phase": "Bound"}
    observed_keys = frozenset(lines[2:])
    expected_keys = frozenset(dependency["keys"])
    observation = {
        "kind": kind,
        "uid": lines[0],
        "resourceVersion": lines[1],
        "expectedKeyDigest": _digest(sorted(expected_keys)),
        "observedExpectedKeyDigest": _digest(sorted(observed_keys & expected_keys)),
    }
    return ("observed" if expected_keys.issubset(observed_keys) else "invalid"), observation


def _dependency_measurement(
    profile: Mapping[str, Any],
    *,
    kubectl: str,
    context: str,
    namespace: str,
    live: bool,
    cluster_available: bool,
) -> tuple[dict[str, Any], dict[str, str]]:
    dependencies = _dependencies(profile)
    binding_digest = _digest(
        [
            {
                "purpose": item["purpose"],
                "kind": item["kind"],
                "name": item["name"],
                "keys": item["keys"],
            }
            for item in dependencies
        ]
    )
    base: dict[str, Any] = {
        "configuredCount": len(dependencies),
        "observedCount": 0,
        "missingCount": 0,
        "invalidCount": 0,
        "unavailableCount": 0,
        "bindingDigest": binding_digest,
    }
    if not live or not cluster_available:
        base["verificationStatus"] = "not-run"
        return base, {
            "id": "referenced-dependencies",
            "status": "not-run",
            "errorCode": "preflight.dependencies.not-run",
        }
    observations: list[Mapping[str, Any]] = []
    for dependency in dependencies:
        status, observation = _observe_dependency(
            kubectl,
            context=context,
            namespace=namespace,
            dependency=dependency,
        )
        if status == "missing":
            base["missingCount"] += 1
        elif status == "unavailable":
            base["unavailableCount"] += 1
        else:
            base["observedCount"] += 1
            if status == "invalid":
                base["invalidCount"] += 1
            if observation is not None:
                observations.append(
                    {"purpose": dependency["purpose"], **observation}
                )
    base["observationDigest"] = _digest(observations)
    if (
        base["missingCount"] == 0
        and base["invalidCount"] == 0
        and base["unavailableCount"] == 0
    ):
        base["verificationStatus"] = "passed"
        return base, {"id": "referenced-dependencies", "status": "passed"}
    base["verificationStatus"] = "failed"
    return base, {
        "id": "referenced-dependencies",
        "status": "failed",
        "errorCode": "preflight.dependencies.incomplete",
    }


def _expected_checks(profile_name: str) -> tuple[str, ...]:
    return COMMON_CHECKS + (AI_CHECKS if profile_name == "production-ai-finops-v0" else ()) + LIVE_CHECKS


def _expected_customer_requirements(profile_name: str) -> tuple[str, ...]:
    return COMMON_CUSTOMER_REQUIREMENTS + (
        AI_CUSTOMER_REQUIREMENTS if profile_name == "production-ai-finops-v0" else ()
    )


def _report_status(checks: Sequence[Mapping[str, Any]], live: bool) -> str:
    if any(check.get("status") == "failed" for check in checks):
        return "blocked"
    if live and all(check.get("status") == "passed" for check in checks):
        return "install-ready"
    return "configuration-ready"


def _report_identifier(
    *,
    source_revision: str,
    source_dirty: bool,
    profile: Mapping[str, Any],
    environment: Mapping[str, Any],
    dependencies: Mapping[str, Any],
    checks: Sequence[Mapping[str, Any]],
) -> str:
    evidence = {
        "sourceRevision": source_revision,
        "sourceDirty": source_dirty,
        "profile": profile,
        "environment": environment,
        "dependencies": dependencies,
        "checks": checks,
    }
    return "cdp_" + hashlib.sha256(_canonical(evidence)).hexdigest()[:32]


def _write_report(output: Path, report: Mapping[str, Any]) -> None:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def generate_report(
    *,
    profile_name: str,
    values: Sequence[Path],
    output: Path,
    namespace: str,
    release_name: str,
    helm: str = "helm",
    kubectl: str = "kubectl",
    context: str | None = None,
    live: bool = False,
) -> Mapping[str, Any]:
    if profile_name not in PROFILES:
        _fail("preflight.profile-name.invalid")
    if NAME.fullmatch(namespace) is None or NAME.fullmatch(release_name) is None:
        _fail("preflight.release-binding.invalid")
    if live and not context:
        _fail("preflight.cluster.binding-required")
    paths = _require_values(values)
    revision, source_dirty = _git_state()
    profile = _profile_from_helm(
        helm,
        release_name=release_name,
        namespace=namespace,
        values=paths,
    )
    checks = _static_checks(profile_name, profile)
    tools = _tool_versions(helm, kubectl, live=live)
    cluster: Mapping[str, str] = {}
    if live:
        assert context is not None
        cluster, cluster_check = _cluster_environment(
            kubectl, context=context, namespace=namespace
        )
    else:
        cluster_check = {
            "id": "cluster-api",
            "status": "not-run",
            "errorCode": "preflight.cluster.not-run",
        }
    checks.append(cluster_check)
    dependency, dependency_check = _dependency_measurement(
        profile,
        kubectl=kubectl,
        context=context or "",
        namespace=namespace,
        live=live,
        cluster_available=bool(cluster),
    )
    checks.append(dependency_check)
    status = _report_status(checks, live)
    values_digest = _values_digest(paths)
    configuration_digest = _digest(profile)
    environment: dict[str, Any] = {
        "mode": "cluster" if live else "static",
        "platform": f"{platform.system().lower()}/{platform.machine().lower()}",
        "pythonVersion": platform.python_version(),
        **tools,
    }
    if cluster:
        environment.update(cluster)
    report_profile = {
        "name": profile_name,
        "chartVersion": profile["chart"]["version"],
        "applicationVersion": profile["chart"]["applicationVersion"],
        "valuesDigest": values_digest,
        "configurationDigest": configuration_digest,
    }
    report: Mapping[str, Any] = {
        "apiVersion": API_VERSION,
        "kind": KIND,
        "metadata": {
            "id": _report_identifier(
                source_revision=revision,
                source_dirty=source_dirty,
                profile=report_profile,
                environment=environment,
                dependencies=dependency,
                checks=checks,
            ),
            "generatedAt": _timestamp(),
            "sourceRevision": revision,
            "sourceDirty": source_dirty,
        },
        "spec": {
            "status": status,
            "qualificationBoundary": "pre-install-only",
            "profile": report_profile,
            "environment": environment,
            "dependencies": dependency,
            "checks": checks,
            "customerQualificationRequired": list(
                _expected_customer_requirements(profile_name)
            ),
            "summary": {
                "totalChecks": len(checks),
                "passedChecks": sum(item["status"] == "passed" for item in checks),
                "failedChecks": sum(item["status"] == "failed" for item in checks),
                "notRunChecks": sum(item["status"] == "not-run" for item in checks),
                "overallStatus": status,
            },
        },
    }
    validate_report_document(report)
    _write_report(output, report)
    return report


def _json(path: Path) -> Mapping[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("preflight.report.invalid")
    if not isinstance(document, dict):
        _fail("preflight.report.invalid")
    return document


def validate_report_document(report: Mapping[str, Any]) -> None:
    if set(report) != {"apiVersion", "kind", "metadata", "spec"}:
        _fail("preflight.report.invalid")
    if report.get("apiVersion") != API_VERSION or report.get("kind") != KIND:
        _fail("preflight.report.invalid")
    metadata = _object(report, "metadata")
    spec = _object(report, "spec")
    if set(metadata) != {"id", "generatedAt", "sourceRevision", "sourceDirty"}:
        _fail("preflight.report.invalid")
    if (
        not isinstance(metadata.get("id"), str)
        or REPORT_ID.fullmatch(metadata["id"]) is None
        or not isinstance(metadata.get("generatedAt"), str)
        or not isinstance(metadata.get("sourceRevision"), str)
        or REVISION.fullmatch(metadata["sourceRevision"]) is None
        or not isinstance(metadata.get("sourceDirty"), bool)
    ):
        _fail("preflight.report.invalid")
    try:
        generated_at = datetime.fromisoformat(metadata["generatedAt"].replace("Z", "+00:00"))
    except ValueError:
        _fail("preflight.report.invalid")
    if generated_at.tzinfo is None:
        _fail("preflight.report.invalid")
    if set(spec) != {
        "status",
        "qualificationBoundary",
        "profile",
        "environment",
        "dependencies",
        "checks",
        "customerQualificationRequired",
        "summary",
    } or spec.get("qualificationBoundary") != "pre-install-only":
        _fail("preflight.report.invalid")
    profile = _object(spec, "profile")
    if set(profile) != {"name", "chartVersion", "applicationVersion", "valuesDigest", "configurationDigest"}:
        _fail("preflight.report.invalid")
    profile_name = profile.get("name")
    if profile_name not in PROFILES:
        _fail("preflight.report.invalid")
    for key in ("chartVersion", "applicationVersion"):
        if not isinstance(profile.get(key), str) or SEMVER.fullmatch(profile[key]) is None:
            _fail("preflight.report.invalid")
    for key in ("valuesDigest", "configurationDigest"):
        if not isinstance(profile.get(key), str) or DIGEST.fullmatch(profile[key]) is None:
            _fail("preflight.report.invalid")
    environment = _object(spec, "environment")
    common_environment = {"mode", "platform", "pythonVersion", "helmVersion"}
    if environment.get("mode") == "static":
        if set(environment) != common_environment:
            _fail("preflight.report.invalid")
    elif environment.get("mode") == "cluster":
        cluster_base = common_environment | {"kubectlVersion"}
        cluster_binding = {
            "kubernetesVersion",
            "clusterBindingDigest",
            "namespaceDigest",
        }
        environment_keys = frozenset(environment)
        if environment_keys not in {
            frozenset(cluster_base),
            frozenset(cluster_base | cluster_binding),
        }:
            _fail("preflight.report.invalid")
        if cluster_binding.issubset(environment):
            for key in ("clusterBindingDigest", "namespaceDigest"):
                if not isinstance(environment.get(key), str) or DIGEST.fullmatch(environment[key]) is None:
                    _fail("preflight.report.invalid")
    else:
        _fail("preflight.report.invalid")
    for key in common_environment - {"mode"}:
        if not isinstance(environment.get(key), str) or not environment[key] or len(environment[key]) > 128:
            _fail("preflight.report.invalid")
    if environment.get("mode") == "cluster":
        for key in ("kubectlVersion",):
            if not isinstance(environment.get(key), str) or not environment[key] or len(environment[key]) > 64:
                _fail("preflight.report.invalid")
        if "kubernetesVersion" in environment and (
            not isinstance(environment["kubernetesVersion"], str)
            or not environment["kubernetesVersion"]
            or len(environment["kubernetesVersion"]) > 64
        ):
            _fail("preflight.report.invalid")
    dependencies = _object(spec, "dependencies")
    allowed_dependency_keys = {"configuredCount", "observedCount", "missingCount", "invalidCount", "unavailableCount", "bindingDigest", "verificationStatus", "observationDigest"}
    if not set(dependencies).issubset(allowed_dependency_keys) or set(dependencies) - {"observationDigest"} != allowed_dependency_keys - {"observationDigest"}:
        _fail("preflight.report.invalid")
    for key in ("configuredCount", "observedCount", "missingCount", "invalidCount", "unavailableCount"):
        if not isinstance(dependencies.get(key), int) or isinstance(dependencies.get(key), bool) or dependencies[key] < 0 or dependencies[key] > 64:
            _fail("preflight.report.invalid")
    if dependencies["observedCount"] + dependencies["missingCount"] + dependencies["unavailableCount"] != dependencies["configuredCount"] and dependencies.get("verificationStatus") != "not-run":
        _fail("preflight.report.invalid")
    if dependencies["invalidCount"] > dependencies["observedCount"]:
        _fail("preflight.report.invalid")
    if not isinstance(dependencies.get("bindingDigest"), str) or DIGEST.fullmatch(dependencies["bindingDigest"]) is None:
        _fail("preflight.report.invalid")
    verification_status = dependencies.get("verificationStatus")
    if verification_status not in {"not-run", "passed", "failed"}:
        _fail("preflight.report.invalid")
    if verification_status == "not-run":
        if "observationDigest" in dependencies or any(dependencies[key] != 0 for key in ("observedCount", "missingCount", "invalidCount", "unavailableCount")):
            _fail("preflight.report.invalid")
    else:
        if not isinstance(dependencies.get("observationDigest"), str) or DIGEST.fullmatch(dependencies["observationDigest"]) is None:
            _fail("preflight.report.invalid")
        passed = dependencies["missingCount"] == 0 and dependencies["invalidCount"] == 0 and dependencies["unavailableCount"] == 0
        if (verification_status == "passed") != passed:
            _fail("preflight.report.invalid")
    checks = spec.get("checks")
    expected_checks = _expected_checks(str(profile_name))
    if not isinstance(checks, list) or len(checks) != len(expected_checks):
        _fail("preflight.report.invalid")
    for expected_id, check in zip(expected_checks, checks):
        if not isinstance(check, dict) or check.get("id") != expected_id or check.get("status") not in {"passed", "failed", "not-run"}:
            _fail("preflight.report.invalid")
        if check["status"] == "passed":
            if set(check) != {"id", "status"}:
                _fail("preflight.report.invalid")
        else:
            expected_error_codes = (
                FAILURE_ERROR_CODES
                if check["status"] == "failed"
                else NOT_RUN_ERROR_CODES
            )
            if (
                set(check) != {"id", "status", "errorCode"}
                or check.get("errorCode") != expected_error_codes.get(expected_id)
            ):
                _fail("preflight.report.invalid")
    cluster_check = checks[-2]
    bound_cluster = "clusterBindingDigest" in environment
    if environment["mode"] == "static":
        if cluster_check.get("status") != "not-run" or bound_cluster:
            _fail("preflight.report.invalid")
    elif (cluster_check.get("status") == "passed") != bound_cluster:
        _fail("preflight.report.invalid")
    requirements = spec.get("customerQualificationRequired")
    if requirements != list(_expected_customer_requirements(str(profile_name))):
        _fail("preflight.report.invalid")
    expected_status = _report_status(checks, environment["mode"] == "cluster")
    if spec.get("status") != expected_status:
        _fail("preflight.report.invalid")
    summary = _object(spec, "summary")
    expected_summary = {
        "totalChecks": len(checks),
        "passedChecks": sum(check["status"] == "passed" for check in checks),
        "failedChecks": sum(check["status"] == "failed" for check in checks),
        "notRunChecks": sum(check["status"] == "not-run" for check in checks),
        "overallStatus": expected_status,
    }
    if summary != expected_summary:
        _fail("preflight.report.invalid")
    expected_id = _report_identifier(
        source_revision=metadata["sourceRevision"],
        source_dirty=metadata["sourceDirty"],
        profile=profile,
        environment=environment,
        dependencies=dependencies,
        checks=checks,
    )
    if metadata["id"] != expected_id:
        _fail("preflight.report.invalid")


def verify_report(
    path: Path,
    *,
    values: Sequence[Path],
    namespace: str,
    release_name: str,
    helm: str = "helm",
    require_clean: bool = False,
    require_install_ready: bool = False,
) -> Mapping[str, Any]:
    report = _json(path.expanduser().resolve())
    validate_report_document(report)
    metadata = report["metadata"]
    spec = report["spec"]
    revision, source_dirty = _git_state()
    if metadata["sourceRevision"] != revision or metadata["sourceDirty"] != source_dirty:
        _fail("preflight.report.source-mismatch")
    if require_clean and (source_dirty or metadata["sourceDirty"]):
        _fail("preflight.report.dirty-source")
    paths = _require_values(values)
    profile = _profile_from_helm(
        helm,
        release_name=release_name,
        namespace=namespace,
        values=paths,
    )
    static_checks = _static_checks(spec["profile"]["name"], profile)
    if (
        spec["profile"]["valuesDigest"] != _values_digest(paths)
        or spec["profile"]["configurationDigest"] != _digest(profile)
        or spec["profile"]["chartVersion"] != profile["chart"]["version"]
        or spec["profile"]["applicationVersion"]
        != profile["chart"]["applicationVersion"]
        or spec["dependencies"]["bindingDigest"]
        != _digest(
            [
                {
                    "purpose": item["purpose"],
                    "kind": item["kind"],
                    "name": item["name"],
                    "keys": item["keys"],
                }
                for item in _dependencies(profile)
            ]
        )
        or spec["checks"][: len(static_checks)] != static_checks
    ):
        _fail("preflight.report.configuration-mismatch")
    if require_install_ready and spec["status"] != "install-ready":
        _fail("preflight.report.install-ready-required")
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("--profile", choices=PROFILES, required=True)
    generate.add_argument("--values", type=Path, action="append", required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--namespace", default="iip-system")
    generate.add_argument("--release-name", default="iip")
    generate.add_argument("--helm", default=os.environ.get("IIP_HELM_BIN", "helm"))
    generate.add_argument("--kubectl", default=os.environ.get("IIP_KUBECTL_BIN", "kubectl"))
    generate.add_argument("--context")
    generate.add_argument("--live", action="store_true")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--values", type=Path, action="append", required=True)
    verify.add_argument("--namespace", default="iip-system")
    verify.add_argument("--release-name", default="iip")
    verify.add_argument("--helm", default=os.environ.get("IIP_HELM_BIN", "helm"))
    verify.add_argument("--require-clean", action="store_true")
    verify.add_argument("--require-install-ready", action="store_true")
    arguments = parser.parse_args(tuple(argv) if argv is not None else None)
    try:
        if arguments.command == "generate":
            report = generate_report(
                profile_name=arguments.profile,
                values=arguments.values,
                output=arguments.output,
                namespace=arguments.namespace,
                release_name=arguments.release_name,
                helm=arguments.helm,
                kubectl=arguments.kubectl,
                context=arguments.context,
                live=arguments.live,
            )
            print(
                f"deployment preflight {report['spec']['status']}: {arguments.output}"
            )
            return 1 if report["spec"]["status"] == "blocked" else 0
        report = verify_report(
            arguments.report,
            values=arguments.values,
            namespace=arguments.namespace,
            release_name=arguments.release_name,
            helm=arguments.helm,
            require_clean=arguments.require_clean,
            require_install_ready=arguments.require_install_ready,
        )
        print(f"deployment preflight verified: {report['spec']['status']}")
        return 0
    except DeploymentPreflightError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
