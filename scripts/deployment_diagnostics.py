#!/usr/bin/env python3
"""Generate and verify a privacy-minimized Kubernetes deployment diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts/schemas/deployment-diagnostic-report.schema.json"
API_VERSION = "iip.platform/v1alpha1"
KIND = "DeploymentDiagnosticReport"
BOUNDARY = "point-in-time-support-only"
COMPONENTS = (
    ("control-plane-api", "api"),
    ("workflow-worker", "workflow-worker"),
    ("otlp-receiver", "otlp-receiver"),
)
CHECK_IDS = (
    "source-bound-tooling",
    "explicit-target",
    "minimized-output",
    "kubernetes-api-access",
    "release-selection",
    "control-plane-availability",
    "workload-rollouts",
    "immutable-image-identity",
    "pod-health-signals",
)
DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
CONTEXT = re.compile(r"^[A-Za-z0-9._:@/-]{1,253}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
KUBERNETES_VERSION = re.compile(
    r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$"
)
CRASH_REASONS = frozenset(
    {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerError"}
)
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024


class DeploymentDiagnosticError(RuntimeError):
    """A stable deployment-diagnostic failure."""


def _fail(code: str) -> None:
    raise DeploymentDiagnosticError(code)


def _timestamp(value: datetime | None = None) -> str:
    instant = value or datetime.now(timezone.utc)
    return instant.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        if not path.is_file() or path.stat().st_size > MAX_DOCUMENT_BYTES:
            _fail("deployment-diagnostic.report.unreadable")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("deployment-diagnostic.report.unreadable")
    if not isinstance(value, dict):
        _fail("deployment-diagnostic.report.invalid")
    return value


def _run(command: Sequence[str]) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            list(command),
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    if completed.returncode != 0 or len(completed.stdout.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        return False, ""
    return True, completed.stdout


def _git_state() -> tuple[str, bool]:
    revision_result, revision = _run(("git", "rev-parse", "HEAD"))
    status_result, status = _run(
        ("git", "status", "--porcelain", "--untracked-files=normal")
    )
    revision = revision.strip()
    if (
        not revision_result
        or not status_result
        or REVISION.fullmatch(revision) is None
    ):
        _fail("deployment-diagnostic.source.unavailable")
    return revision, bool(status.strip())


def _repository_identity() -> dict[str, str]:
    try:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        chart_text = (ROOT / "deploy/helm/infra-intelligence/Chart.yaml").read_text(
            encoding="utf-8"
        )
        application_version = project["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        _fail("deployment-diagnostic.source.identity-invalid")
    chart_match = re.search(r"^version:\s*([^\s]+)\s*$", chart_text, re.MULTILINE)
    if (
        not isinstance(application_version, str)
        or SEMVER.fullmatch(application_version) is None
        or chart_match is None
        or SEMVER.fullmatch(chart_match.group(1)) is None
    ):
        _fail("deployment-diagnostic.source.identity-invalid")
    return {
        "applicationVersion": application_version,
        "chartVersion": chart_match.group(1),
    }


def _target(
    *, context: str, namespace: str, release_name: str, image_digest: str
) -> tuple[str, dict[str, str]]:
    if CONTEXT.fullmatch(context) is None or context in (".", ".."):
        _fail("deployment-diagnostic.target.context-invalid")
    if DNS_LABEL.fullmatch(namespace) is None:
        _fail("deployment-diagnostic.target.namespace-invalid")
    if DNS_LABEL.fullmatch(release_name) is None:
        _fail("deployment-diagnostic.target.release-invalid")
    if DIGEST.fullmatch(image_digest) is None:
        _fail("deployment-diagnostic.target.image-digest-invalid")
    source = _repository_identity()
    identity = {**source, "imageDigest": image_digest}
    binding = _digest(
        {
            "context": context,
            "namespace": namespace,
            "releaseName": release_name,
            "expectedIdentity": identity,
        }
    )
    return binding, identity


def _document(raw: str) -> Mapping[str, Any] | None:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _items(document: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if document is None or document.get("kind") != "List":
        return []
    values = document.get("items")
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, dict)]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _component_id(resource: Mapping[str, Any]) -> str | None:
    spec = _mapping(resource.get("spec"))
    template = _mapping(spec.get("template"))
    metadata = _mapping(template.get("metadata"))
    labels = _mapping(metadata.get("labels"))
    component = labels.get("app.kubernetes.io/component")
    return component if isinstance(component, str) else None


def _pod_component(resource: Mapping[str, Any]) -> str | None:
    metadata = _mapping(resource.get("metadata"))
    labels = _mapping(metadata.get("labels"))
    component = labels.get("app.kubernetes.io/component")
    return component if isinstance(component, str) else None


def _pod_ready(pod: Mapping[str, Any]) -> bool:
    status = _mapping(pod.get("status"))
    conditions = status.get("conditions")
    if not isinstance(conditions, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("type") == "Ready"
        and item.get("status") == "True"
        for item in conditions
    )


def _pod_unschedulable(pod: Mapping[str, Any]) -> bool:
    status = _mapping(pod.get("status"))
    conditions = status.get("conditions")
    if not isinstance(conditions, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("type") == "PodScheduled"
        and item.get("status") == "False"
        and item.get("reason") == "Unschedulable"
        for item in conditions
    )


def _container_statuses(pod: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    status = _mapping(pod.get("status"))
    values = status.get("containerStatuses")
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, dict)]


def _pod_summary(pods: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    statuses = [status for pod in pods for status in _container_statuses(pod)]
    crash_looping = 0
    for status in statuses:
        state = _mapping(status.get("state"))
        waiting = _mapping(state.get("waiting"))
        if waiting.get("reason") in CRASH_REASONS:
            crash_looping += 1
    return {
        "observed": len(pods),
        "ready": sum(_pod_ready(pod) for pod in pods),
        "restarts": sum(_integer(status.get("restartCount")) for status in statuses),
        "unschedulable": sum(_pod_unschedulable(pod) for pod in pods),
        "crashLoopingContainers": crash_looping,
    }


def _deployment_identity(
    deployment: Mapping[str, Any], container_name: str
) -> tuple[str, dict[str, str] | None]:
    metadata = _mapping(deployment.get("metadata"))
    labels = _mapping(metadata.get("labels"))
    application_version = labels.get("app.kubernetes.io/version")
    chart_label = labels.get("helm.sh/chart")
    chart_prefix = "infra-intelligence-"
    chart_version = (
        chart_label.removeprefix(chart_prefix).replace("_", "+")
        if isinstance(chart_label, str) and chart_label.startswith(chart_prefix)
        else None
    )
    spec = _mapping(deployment.get("spec"))
    template = _mapping(spec.get("template"))
    pod_spec = _mapping(template.get("spec"))
    containers = pod_spec.get("containers")
    image = None
    if isinstance(containers, list):
        for container in containers:
            if isinstance(container, dict) and container.get("name") == container_name:
                image = container.get("image")
                break
    image_digest = (
        image.rsplit("@", 1)[1]
        if isinstance(image, str) and "@" in image
        else None
    )
    if (
        not isinstance(application_version, str)
        or SEMVER.fullmatch(application_version) is None
        or not isinstance(chart_version, str)
        or SEMVER.fullmatch(chart_version) is None
        or not isinstance(image_digest, str)
        or DIGEST.fullmatch(image_digest) is None
    ):
        return "invalid", None
    return "observed", {
        "applicationVersion": application_version,
        "chartVersion": chart_version,
        "imageDigest": image_digest,
    }


def _component(
    identifier: str,
    container_name: str,
    deployments: Sequence[Mapping[str, Any]],
    pods: Sequence[Mapping[str, Any]],
    expected_identity: Mapping[str, str],
) -> dict[str, Any]:
    if len(deployments) != 1:
        return {"id": identifier, "state": "not-observed"}
    deployment = deployments[0]
    status = _mapping(deployment.get("status"))
    spec = _mapping(deployment.get("spec"))
    rollout = {
        "desiredReplicas": _integer(spec.get("replicas")),
        "currentReplicas": _integer(status.get("replicas")),
        "updatedReplicas": _integer(status.get("updatedReplicas")),
        "readyReplicas": _integer(status.get("readyReplicas")),
        "availableReplicas": _integer(status.get("availableReplicas")),
    }
    pod_summary = _pod_summary(pods)
    identity_observation, identity = _deployment_identity(deployment, container_name)
    identity_status = (
        "invalid"
        if identity_observation == "invalid"
        else "match" if identity == expected_identity else "mismatch"
    )
    desired = rollout["desiredReplicas"]
    complete = (
        desired > 0
        and rollout["currentReplicas"] == desired
        and rollout["updatedReplicas"] == desired
        and rollout["readyReplicas"] == desired
        and rollout["availableReplicas"] == desired
        and pod_summary["observed"] >= desired
        and pod_summary["ready"] >= desired
        and pod_summary["unschedulable"] == 0
        and pod_summary["crashLoopingContainers"] == 0
    )
    if complete:
        state = "healthy"
    elif rollout["availableReplicas"] > 0:
        state = "progressing"
    else:
        state = "unavailable"
    result: dict[str, Any] = {
        "id": identifier,
        "state": state,
        "identityStatus": identity_status,
        "rollout": rollout,
        "pods": pod_summary,
    }
    if identity is not None:
        result["identity"] = identity
    return result


def _check(identifier: str, status: str, error_code: str | None = None) -> dict[str, str]:
    result = {"id": identifier, "status": status}
    if error_code is not None:
        result["errorCode"] = error_code
    return result


def _derive_checks(
    *,
    source_dirty: bool,
    expected_identity: Mapping[str, Any],
    environment: Mapping[str, Any],
    selection: Mapping[str, Any],
    components: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    by_id = {str(component.get("id")): component for component in components}
    api = by_id.get("control-plane-api", {})
    optional = [
        by_id.get("workflow-worker", {}),
        by_id.get("otlp-receiver", {}),
    ]
    cluster_access = environment.get("clusterAccess") == "observed"
    selection_valid = (
        cluster_access
        and selection.get("duplicateComponents") == 0
        and selection.get("unrecognizedDeployments") == 0
        and api.get("state") != "not-observed"
    )
    observed = [
        component
        for component in components
        if component.get("state") != "not-observed"
    ]
    identity_valid = bool(observed) and all(
        component.get("identityStatus") == "match"
        and component.get("identity") == expected_identity
        for component in observed
    )
    optional_problem = any(
        component.get("state") not in ("healthy", "not-observed")
        for component in optional
    )
    restarts = sum(
        _integer(_mapping(component.get("pods")).get("restarts"))
        for component in observed
    )
    unschedulable = sum(
        _integer(_mapping(component.get("pods")).get("unschedulable"))
        for component in observed
    )
    crash_looping = sum(
        _integer(_mapping(component.get("pods")).get("crashLoopingContainers"))
        for component in observed
    )
    return [
        _check(
            "source-bound-tooling",
            "warning" if source_dirty else "passed",
            "deployment-diagnostic.source.dirty" if source_dirty else None,
        ),
        _check("explicit-target", "passed"),
        _check("minimized-output", "passed"),
        _check(
            "kubernetes-api-access",
            "passed" if cluster_access else "failed",
            None if cluster_access else "deployment-diagnostic.cluster.unavailable",
        ),
        _check(
            "release-selection",
            "passed" if selection_valid else "failed",
            None
            if selection_valid
            else "deployment-diagnostic.release.selection-invalid",
        ),
        _check(
            "control-plane-availability",
            "passed" if api.get("state") == "healthy" else "failed",
            None
            if api.get("state") == "healthy"
            else "deployment-diagnostic.api.not-available",
        ),
        _check(
            "workload-rollouts",
            "warning" if optional_problem else "passed",
            "deployment-diagnostic.workload.not-healthy"
            if optional_problem
            else None,
        ),
        _check(
            "immutable-image-identity",
            "passed" if identity_valid else "failed",
            None
            if identity_valid
            else "deployment-diagnostic.identity.mismatch-or-invalid",
        ),
        _check(
            "pod-health-signals",
            "warning" if restarts or unschedulable or crash_looping else "passed",
            "deployment-diagnostic.pods.attention-required"
            if restarts or unschedulable or crash_looping
            else None,
        ),
    ]


def _summary(
    components: Sequence[Mapping[str, Any]], checks: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    states = [component.get("state") for component in components]
    observed = [component for component in components if component.get("state") != "not-observed"]
    pods = [_mapping(component.get("pods")) for component in observed]
    passed = sum(check.get("status") == "passed" for check in checks)
    warnings = sum(check.get("status") == "warning" for check in checks)
    failed = sum(check.get("status") == "failed" for check in checks)
    overall = "blocked" if failed else "attention-required" if warnings else "healthy"
    return {
        "observedComponents": len(observed),
        "healthyComponents": states.count("healthy"),
        "progressingComponents": states.count("progressing"),
        "unavailableComponents": states.count("unavailable"),
        "notObservedComponents": states.count("not-observed"),
        "totalRestarts": sum(_integer(value.get("restarts")) for value in pods),
        "unschedulablePods": sum(_integer(value.get("unschedulable")) for value in pods),
        "crashLoopingContainers": sum(
            _integer(value.get("crashLoopingContainers")) for value in pods
        ),
        "totalChecks": len(CHECK_IDS),
        "passedChecks": passed,
        "warningChecks": warnings,
        "failedChecks": failed,
        "overallStatus": overall,
    }


def _version(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    git_version = value.get("gitVersion")
    return (
        git_version
        if isinstance(git_version, str)
        and KUBERNETES_VERSION.fullmatch(git_version) is not None
        else None
    )


def _observe_kubernetes(
    *,
    kubectl: str,
    context: str,
    namespace: str,
    release_name: str,
    runner: Callable[[Sequence[str]], tuple[bool, str]] = _run,
) -> tuple[dict[str, Any], Mapping[str, Any] | None, Mapping[str, Any] | None]:
    client_ok, client_raw = runner((kubectl, "version", "--client", "-o", "json"))
    client_document = _document(client_raw) if client_ok else None
    kubectl_version = _version(
        _mapping(client_document).get("clientVersion") if client_document else None
    ) or "unavailable"
    version_ok, version_raw = runner(
        (kubectl, "--context", context, "version", "-o", "json")
    )
    version_document = _document(version_raw) if version_ok else None
    kubernetes_version = _version(
        _mapping(version_document).get("serverVersion") if version_document else None
    )
    selector = f"app.kubernetes.io/instance={release_name}"
    deployment_ok, deployments_raw = runner(
        (
            kubectl,
            "--context",
            context,
            "--namespace",
            namespace,
            "get",
            "deployments",
            "--selector",
            selector,
            "--output",
            "json",
        )
    )
    pod_ok, pods_raw = runner(
        (
            kubectl,
            "--context",
            context,
            "--namespace",
            namespace,
            "get",
            "pods",
            "--selector",
            selector,
            "--output",
            "json",
        )
    )
    deployments = _document(deployments_raw) if deployment_ok else None
    pods = _document(pods_raw) if pod_ok else None
    access = (
        version_document is not None
        and kubernetes_version is not None
        and deployments is not None
        and deployments.get("kind") == "List"
        and pods is not None
        and pods.get("kind") == "List"
    )
    environment = {
        "observedAt": _timestamp(),
        "platform": f"{platform.system().lower()}/{platform.machine().lower()}",
        "pythonVersion": platform.python_version(),
        "kubectlVersion": kubectl_version,
        "clusterAccess": "observed" if access else "unavailable",
        "kubernetesVersion": kubernetes_version if access else None,
    }
    return environment, deployments if access else None, pods if access else None


def generate_report(
    *,
    context: str,
    namespace: str,
    release_name: str,
    image_digest: str,
    kubectl: str = "kubectl",
    runner: Callable[[Sequence[str]], tuple[bool, str]] = _run,
    report_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    target_binding, expected_identity = _target(
        context=context,
        namespace=namespace,
        release_name=release_name,
        image_digest=image_digest,
    )
    revision, dirty = _git_state()
    environment, deployments_document, pods_document = _observe_kubernetes(
        kubectl=kubectl,
        context=context,
        namespace=namespace,
        release_name=release_name,
        runner=runner,
    )
    deployments = _items(deployments_document)
    pods = _items(pods_document)
    known = {identifier for identifier, _ in COMPONENTS}
    selected: dict[str, list[Mapping[str, Any]]] = {
        identifier: [] for identifier in known
    }
    unrecognized = 0
    for deployment in deployments:
        metadata = _mapping(deployment.get("metadata"))
        labels = _mapping(metadata.get("labels"))
        if labels.get("app.kubernetes.io/part-of") != "infrastructure-intelligence-platform":
            unrecognized += 1
            continue
        component_id = _component_id(deployment)
        if component_id in selected:
            selected[component_id].append(deployment)
        else:
            unrecognized += 1
    selected_pods = {
        identifier: [pod for pod in pods if _pod_component(pod) == identifier]
        for identifier in known
    }
    components = [
        _component(
            identifier,
            container_name,
            selected[identifier],
            selected_pods[identifier],
            expected_identity,
        )
        for identifier, container_name in COMPONENTS
    ]
    selection = {
        "selectedDeployments": sum(len(value) for value in selected.values()),
        "selectedPods": sum(len(value) for value in selected_pods.values()),
        "unrecognizedDeployments": unrecognized,
        "duplicateComponents": sum(len(value) > 1 for value in selected.values()),
    }
    checks = _derive_checks(
        source_dirty=dirty,
        expected_identity=expected_identity,
        environment=environment,
        selection=selection,
        components=components,
    )
    summary = _summary(components, checks)
    report = {
        "apiVersion": API_VERSION,
        "kind": KIND,
        "metadata": {
            "id": report_id or f"ddr_{uuid.uuid4().hex}",
            "generatedAt": generated_at or _timestamp(),
            "sourceRevision": revision,
            "sourceDirty": dirty,
        },
        "spec": {
            "status": summary["overallStatus"],
            "diagnosticBoundary": BOUNDARY,
            "targetBindingDigest": target_binding,
            "expectedIdentity": expected_identity,
            "environment": environment,
            "selection": selection,
            "components": components,
            "checks": checks,
            "summary": summary,
        },
    }
    validate_report(report)
    return report


def _schema() -> Mapping[str, Any]:
    return _read_json(SCHEMA)


def validate_report(report: Mapping[str, Any]) -> None:
    errors = list(
        Draft202012Validator(
            _schema(), format_checker=FormatChecker()
        ).iter_errors(report)
    )
    if errors:
        _fail("deployment-diagnostic.report.schema-invalid")
    metadata = _mapping(report.get("metadata"))
    spec = _mapping(report.get("spec"))
    components = spec.get("components")
    checks = spec.get("checks")
    if not isinstance(components, list) or not isinstance(checks, list):
        _fail("deployment-diagnostic.report.schema-invalid")
    expected_checks = _derive_checks(
        source_dirty=metadata.get("sourceDirty") is True,
        expected_identity=_mapping(spec.get("expectedIdentity")),
        environment=_mapping(spec.get("environment")),
        selection=_mapping(spec.get("selection")),
        components=[_mapping(value) for value in components],
    )
    expected_summary = _summary(
        [_mapping(value) for value in components], expected_checks
    )
    if checks != expected_checks or spec.get("summary") != expected_summary:
        _fail("deployment-diagnostic.report.semantics-invalid")
    if spec.get("status") != expected_summary["overallStatus"]:
        _fail("deployment-diagnostic.report.semantics-invalid")


def verify_report(
    report: Mapping[str, Any],
    *,
    context: str,
    namespace: str,
    release_name: str,
    image_digest: str,
    require_clean: bool = False,
    require_healthy: bool = False,
) -> None:
    validate_report(report)
    binding, identity = _target(
        context=context,
        namespace=namespace,
        release_name=release_name,
        image_digest=image_digest,
    )
    metadata = _mapping(report.get("metadata"))
    spec = _mapping(report.get("spec"))
    if spec.get("targetBindingDigest") != binding or spec.get("expectedIdentity") != identity:
        _fail("deployment-diagnostic.report.target-mismatch")
    if require_clean:
        revision, dirty = _git_state()
        if dirty or metadata.get("sourceDirty") is not False:
            _fail("deployment-diagnostic.source.dirty")
        if metadata.get("sourceRevision") != revision:
            _fail("deployment-diagnostic.source.revision-mismatch")
    if require_healthy and spec.get("status") != "healthy":
        _fail("deployment-diagnostic.report.not-healthy")


def _write(path: Path, report: Mapping[str, Any]) -> None:
    output = path.expanduser().resolve()
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


def _inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--release-name", default="iip")
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--require-healthy", action="store_true")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    _inputs(generate)
    generate.add_argument("--kubectl", default="kubectl")
    generate.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    _inputs(verify)
    verify.add_argument("--report", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "generate":
            report = generate_report(
                context=arguments.context,
                namespace=arguments.namespace,
                release_name=arguments.release_name,
                image_digest=arguments.image_digest,
                kubectl=arguments.kubectl,
            )
            _write(arguments.output, report)
            if arguments.require_clean and report["metadata"]["sourceDirty"]:
                _fail("deployment-diagnostic.source.dirty")
            if arguments.require_healthy and report["spec"]["status"] != "healthy":
                _fail("deployment-diagnostic.report.not-healthy")
            print(
                f"deployment diagnostic: {report['spec']['status']}; "
                f"report: {arguments.output}"
            )
        else:
            report = _read_json(arguments.report.expanduser().resolve())
            verify_report(
                report,
                context=arguments.context,
                namespace=arguments.namespace,
                release_name=arguments.release_name,
                image_digest=arguments.image_digest,
                require_clean=arguments.require_clean,
                require_healthy=arguments.require_healthy,
            )
            print(f"deployment diagnostic verified: {report['spec']['status']}")
    except DeploymentDiagnosticError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
