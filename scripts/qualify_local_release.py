#!/usr/bin/env python3
"""Run the complete repository-controlled local release qualification workflow."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from release_bundle import ReleaseBundleError, verify_bundle


ROOT = Path(__file__).resolve().parents[1]
REVISION = re.compile(r"^[a-f0-9]{7,64}$")


class LocalReleaseQualificationError(RuntimeError):
    """Stable local release orchestration failure."""


def _fail(code: str) -> None:
    raise LocalReleaseQualificationError(code)


@dataclass(frozen=True)
class Toolchain:
    make: str = "make"
    python: str = sys.executable
    docker: str = "docker"
    helm: str = "helm"
    kubectl: str = "kubectl"
    kind: str = "kind"
    npm: str = "npm"
    cosign: str = "cosign"


@dataclass(frozen=True)
class QualificationPaths:
    bundle: Path
    release_qualification: Path
    vulnerability_qualification: Path
    readiness: Path
    evidence_dir: Path


CommandRunner = Callable[[Sequence[str], Mapping[str, str], Path], int]


SOURCE_QUALITY_TARGETS = (
    "verify",
    "test-release-signatures",
    "test-release-vulnerabilities",
    "test-release-readiness",
)

ENVIRONMENT_EVIDENCE_TARGETS = (
    "test-postgres-tls",
    "test-capacity",
    "test-credential-broker",
    "test-oidc",
    "test-policy-engine",
    "test-external-secrets",
    "test-operational-alerts",
    "test-otlp-receiver",
    "test-bedrock-instrumentation",
    "test-openai-instrumentation",
    "test-ai-finops",
    "verify-ai-finops-runtime-report",
    "test-backup-restore",
    "verify-backup-restore-report",
    "test-postgres-continuity",
    "verify-postgres-continuity-report",
    "test-deployment-preflight",
    "qualify-github-context",
    "verify-github-context-report",
    "test-plugin-compatibility",
    "qualify-kubernetes-availability",
    "verify-kubernetes-availability-report",
)

STAGES = (
    ("source-quality", SOURCE_QUALITY_TARGETS),
    ("environment-evidence", ENVIRONMENT_EVIDENCE_TARGETS),
    ("release-bundle", ("release-bundle",)),
    ("packaged-qualification", ("qualify-release",)),
    ("vulnerability-qualification", ("qualify-release-vulnerabilities",)),
    (
        "readiness-aggregation",
        ("assess-release-readiness", "verify-release-readiness-report"),
    ),
)

SENSITIVE_ENVIRONMENT_MARKERS = (
    "API_KEY",
    "ACCESS_KEY",
    "CREDENTIAL",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
)


def _output(command: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        _fail("local-release.tool.unavailable")
    if result.returncode != 0:
        _fail("local-release.repository.unavailable")
    return result.stdout.strip()


def _repository_identity(upgrade_from_revision: str) -> tuple[str, str, str]:
    if not REVISION.fullmatch(upgrade_from_revision):
        _fail("local-release.upgrade-revision.invalid")
    revision = _output(("git", "rev-parse", "HEAD"))
    if not re.fullmatch(r"[a-f0-9]{40,64}", revision):
        _fail("local-release.repository.unavailable")
    if _output(("git", "status", "--porcelain", "--untracked-files=normal")):
        _fail("local-release.source.dirty")
    previous = _output(
        ("git", "rev-parse", "--verify", f"{upgrade_from_revision}^{{commit}}")
    )
    if previous == revision:
        _fail("local-release.upgrade-revision.not-ancestor")
    try:
        ancestry = subprocess.run(
            ("git", "merge-base", "--is-ancestor", previous, revision),
            cwd=ROOT,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        _fail("local-release.tool.unavailable")
    if ancestry.returncode == 1:
        _fail("local-release.upgrade-revision.not-ancestor")
    if ancestry.returncode != 0:
        _fail("local-release.repository.unavailable")
    try:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            version = tomllib.load(handle)["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        _fail("local-release.version.invalid")
    if not isinstance(version, str) or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", version
    ):
        _fail("local-release.version.invalid")
    return revision, previous, version


def qualification_paths(revision: str, version: str) -> QualificationPaths:
    evidence_dir = (ROOT / "dist").resolve()
    short_revision = revision[:12]
    return QualificationPaths(
        bundle=evidence_dir / f"iip-{version}-{short_revision}",
        release_qualification=evidence_dir
        / f"release-qualification-{short_revision}.json",
        vulnerability_qualification=evidence_dir
        / f"release-vulnerability-qualification-{short_revision}.json",
        readiness=evidence_dir / f"release-readiness-{short_revision}.json",
        evidence_dir=evidence_dir,
    )


def qualification_environment(
    *,
    paths: QualificationPaths,
    previous_revision: str,
    toolchain: Toolchain,
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("IIP_", "AWS_", "OPENAI_"))
        and key != "PYTHONPATH"
        and not any(marker in key.upper() for marker in SENSITIVE_ENVIRONMENT_MARKERS)
    }
    environment.update(
        {
            "IIP_RELEASE_OUTPUT_ROOT": str(paths.evidence_dir),
            "IIP_RELEASE_BUNDLE": str(paths.bundle),
            "IIP_RELEASE_QUALIFICATION_REPORT": str(paths.release_qualification),
            "IIP_UPGRADE_FROM_REVISION": previous_revision,
            "IIP_RELEASE_VULNERABILITY_POLICY": str(
                (ROOT / "contracts/examples/release-vulnerability-policy.json").resolve()
            ),
            "IIP_RELEASE_VULNERABILITY_REPORT": str(
                paths.vulnerability_qualification
            ),
            "IIP_RELEASE_EVIDENCE_DIR": str(paths.evidence_dir),
            "IIP_RELEASE_READINESS_REPORT": str(paths.readiness),
            "PYTHON": toolchain.python,
            "DOCKER": toolchain.docker,
            "HELM": toolchain.helm,
            "KUBECTL": toolchain.kubectl,
            "KIND": toolchain.kind,
            "NPM": toolchain.npm,
            "COSIGN": toolchain.cosign,
        }
    )
    return environment


def make_command(toolchain: Toolchain, targets: Sequence[str]) -> tuple[str, ...]:
    return (
        toolchain.make,
        "--no-print-directory",
        f"PYTHON={toolchain.python}",
        f"DOCKER={toolchain.docker}",
        f"HELM={toolchain.helm}",
        f"KUBECTL={toolchain.kubectl}",
        f"KIND={toolchain.kind}",
        f"NPM={toolchain.npm}",
        f"COSIGN={toolchain.cosign}",
        *targets,
    )


def _default_runner(
    command: Sequence[str], environment: Mapping[str, str], cwd: Path
) -> int:
    try:
        return subprocess.run(
            command, cwd=cwd, env=environment, check=False
        ).returncode
    except OSError:
        return 127


def _read_report(
    path: Path, kind: str, status: str, revision: str
) -> Mapping[str, object]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("local-release.output.missing-or-invalid")
    if not isinstance(report, dict):
        _fail("local-release.output.missing-or-invalid")
    metadata = report.get("metadata")
    spec = report.get("spec")
    if (
        report.get("kind") != kind
        or not isinstance(metadata, dict)
        or not isinstance(spec, dict)
        or metadata.get("sourceRevision") != revision
        or metadata.get("sourceDirty") is not False
        or spec.get("status") != status
    ):
        _fail("local-release.output.identity-mismatch")
    return report


def validate_outputs(
    paths: QualificationPaths, *, revision: str, version: str
) -> Mapping[str, object]:
    try:
        manifest = verify_bundle(paths.bundle)
    except ReleaseBundleError:
        _fail("local-release.output.bundle-invalid")
    metadata = manifest.get("metadata")
    if (
        not isinstance(metadata, dict)
        or metadata.get("revision") != revision
        or metadata.get("version") != version
    ):
        _fail("local-release.output.identity-mismatch")
    _read_report(
        paths.release_qualification,
        "ReleaseQualificationReport",
        "qualified",
        revision,
    )
    _read_report(
        paths.vulnerability_qualification,
        "ReleaseVulnerabilityQualificationReport",
        "qualified",
        revision,
    )
    readiness = _read_report(
        paths.readiness,
        "ReleaseReadinessReport",
        "locally-qualified",
        revision,
    )
    summary = readiness.get("spec", {}).get("summary")
    if not isinstance(summary, dict) or summary != {
        "requiredEvidence": 19,
        "passedEvidence": 19,
        "missingEvidence": 0,
        "rejectedEvidence": 0,
        "externalGateCount": 8,
        "overallStatus": "locally-qualified",
    }:
        _fail("local-release.output.readiness-incomplete")
    return readiness


def qualify_local_release(
    *,
    upgrade_from_revision: str,
    toolchain: Toolchain,
    runner: CommandRunner = _default_runner,
) -> QualificationPaths:
    revision, previous, version = _repository_identity(upgrade_from_revision)
    paths = qualification_paths(revision, version)
    if paths.bundle.exists():
        _fail("local-release.candidate.already-exists")
    environment = qualification_environment(
        paths=paths, previous_revision=previous, toolchain=toolchain
    )
    for stage, targets in STAGES:
        print(f"local release qualification stage: {stage}", flush=True)
        return_code = runner(make_command(toolchain, targets), environment, ROOT)
        if return_code != 0:
            _fail(f"local-release.stage.{stage}.failed")
    validate_outputs(paths, revision=revision, version=version)
    if _output(("git", "status", "--porcelain", "--untracked-files=normal")):
        _fail("local-release.source.changed-during-run")
    return paths


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upgrade-from-revision", required=True)
    parser.add_argument("--make-bin", default="make")
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument("--docker-bin", default="docker")
    parser.add_argument("--helm-bin", default="helm")
    parser.add_argument("--kubectl-bin", default="kubectl")
    parser.add_argument("--kind-bin", default="kind")
    parser.add_argument("--npm-bin", default="npm")
    parser.add_argument("--cosign-bin", default="cosign")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        paths = qualify_local_release(
            upgrade_from_revision=arguments.upgrade_from_revision,
            toolchain=Toolchain(
                make=arguments.make_bin,
                python=arguments.python_bin,
                docker=arguments.docker_bin,
                helm=arguments.helm_bin,
                kubectl=arguments.kubectl_bin,
                kind=arguments.kind_bin,
                npm=arguments.npm_bin,
                cosign=arguments.cosign_bin,
            ),
        )
    except LocalReleaseQualificationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("local release qualification: locally-qualified")
    print(f"bundle: {paths.bundle}")
    print(f"release qualification: {paths.release_qualification}")
    print(f"vulnerability qualification: {paths.vulnerability_qualification}")
    print(f"readiness: {paths.readiness}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
