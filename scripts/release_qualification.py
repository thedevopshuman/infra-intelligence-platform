#!/usr/bin/env python3
"""Record and verify environment-scoped packaged release qualification evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from release_bundle import ReleaseBundleError, sha256_file, verify_bundle


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PROFILES = ("packaged-install", "n-minus-one-upgrade")
INSTALL_CHECKS = (
    "bundle-integrity",
    "source-identity",
    "immutable-deployment",
    "runtime-identity",
    "schema-migrations",
    "protected-operations",
    "tls-ingress",
    "backup-checksum",
    "isolated-restore",
    "helm-upgrade",
)
UPGRADE_CHECKS = (
    "bundle-integrity",
    "source-identity",
    "strict-ancestry",
    "base-runtime-identity",
    "target-runtime-identity",
    "tenant-data-preservation",
    "non-regressing-migration",
    "forward-schema-rollback",
    "idempotent-reupgrade",
    "zero-failure-service-availability",
    "in-flight-request-drain",
    "helm-history",
)
REVISION = re.compile(r"^[0-9a-f]{40,64}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
MIGRATION = re.compile(r"^[0-9]{4}_[A-Za-z0-9_]+\.sql$")
PLATFORM = re.compile(r"^linux/[a-z0-9_]+(?:/[a-z0-9.]+)?$")
REPORT_ID = re.compile(r"^rqr_[0-9a-f]{32}$")


class ReleaseQualificationError(RuntimeError):
    """A stable release-qualification validation failure."""


def _fail(code: str) -> None:
    raise ReleaseQualificationError(code)


def _json(path: Path, code: str) -> Mapping[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(document, dict):
        _fail(code)
    return document


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        _fail("qualification.timestamp.invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("qualification.timestamp.invalid")
    if parsed.tzinfo is None:
        _fail("qualification.timestamp.invalid")
    return parsed


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _exact_keys(value: object, keys: Sequence[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        _fail(code)
    return value


def _positive_integer(value: object, code: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(code)
    return value


def _checked_string(value: object, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(code)
    return value


def _candidate(bundle: Path) -> tuple[Mapping[str, Any], dict[str, Any]]:
    try:
        manifest = verify_bundle(bundle)
    except ReleaseBundleError as exc:
        raise ReleaseQualificationError(str(exc)) from None
    metadata = manifest.get("metadata")
    spec = manifest.get("spec")
    image = spec.get("image") if isinstance(spec, dict) else None
    platforms = image.get("platforms") if isinstance(image, dict) else None
    if (
        not isinstance(metadata, dict)
        or not isinstance(image, dict)
        or not isinstance(platforms, list)
    ):
        _fail("qualification.bundle.invalid")
    manifest_path = bundle / "release-manifest.json"
    candidate = {
        "version": metadata.get("version"),
        "chartVersion": metadata.get("chartVersion"),
        "revision": metadata.get("revision"),
        "releaseManifestDigest": f"sha256:{sha256_file(manifest_path)}",
        "controlPlaneImageDigest": image.get("indexDigest"),
        "platforms": [
            item.get("name") for item in platforms if isinstance(item, dict)
        ],
        "signatureStatus": metadata.get("signatureStatus"),
    }
    return manifest, candidate


def _environment(
    *, platform: str, kubernetes_version: str, container_runtime_version: str
) -> dict[str, Any]:
    _checked_string(platform, PLATFORM, "qualification.environment.platform.invalid")
    if not kubernetes_version or len(kubernetes_version) > 64:
        _fail("qualification.environment.kubernetes-version.invalid")
    if not container_runtime_version or len(container_runtime_version) > 64:
        _fail("qualification.environment.container-runtime-version.invalid")
    return {
        "profile": "local-kind",
        "platform": platform,
        "kubernetesVersion": kubernetes_version,
        "containerRuntime": {
            "name": "docker",
            "version": container_runtime_version,
        },
    }


def _run_git(*arguments: str) -> str:
    process = subprocess.run(
        ("git", *arguments),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        _fail("qualification.source.unavailable")
    return process.stdout.strip()


def _require_clean_source(candidate: Mapping[str, Any]) -> None:
    revision = _run_git("rev-parse", "HEAD")
    dirty = _run_git("status", "--porcelain", "--untracked-files=normal")
    if revision != candidate.get("revision"):
        _fail("qualification.source.revision-mismatch")
    if dirty:
        _fail("qualification.source.dirty")


def _require_output_outside_bundle(bundle: Path, output: Path) -> None:
    bundle_resolved = bundle.resolve()
    output_resolved = output.resolve()
    if output_resolved == bundle_resolved or bundle_resolved in output_resolved.parents:
        _fail("qualification.output.inside-bundle")


def _checks(check_ids: Sequence[str]) -> list[dict[str, str]]:
    return [{"id": identifier, "status": "passed"} for identifier in check_ids]


def _summary(profiles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    names = [profile.get("name") for profile in profiles]
    passed = sum(
        1
        for name in REQUIRED_PROFILES
        if names.count(name) == 1
        and profiles[names.index(name)].get("result") == "passed"
    )
    status = "qualified" if passed == len(REQUIRED_PROFILES) else "incomplete"
    return {
        "requiredProfiles": len(REQUIRED_PROFILES),
        "passedProfiles": passed,
        "overallStatus": status,
    }


def _report(
    *,
    candidate: Mapping[str, Any],
    environment: Mapping[str, Any],
    profiles: Sequence[Mapping[str, Any]],
    report_id: str | None = None,
) -> dict[str, Any]:
    summary = _summary(profiles)
    return {
        "apiVersion": "iip.dev/v1alpha1",
        "kind": "ReleaseQualificationReport",
        "metadata": {
            "id": report_id or f"rqr_{uuid.uuid4().hex}",
            "generatedAt": _now(),
            "sourceRevision": candidate["revision"],
            "sourceDirty": False,
        },
        "spec": {
            "status": summary["overallStatus"],
            "candidate": dict(candidate),
            "environment": dict(environment),
            "profiles": [dict(profile) for profile in profiles],
            "summary": summary,
        },
    }


def _write_report(output: Path, report: Mapping[str, Any]) -> None:
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


def _runtime_identity(
    path: Path, candidate: Mapping[str, Any], required_migration: str
) -> dict[str, str]:
    document = _json(path, "qualification.runtime-report.invalid")
    if (
        document.get("apiVersion") != "iip.platform/v1alpha1"
        or document.get("kind") != "RuntimeVersionReport"
    ):
        _fail("qualification.runtime-report.invalid")
    spec = document.get("spec")
    if not isinstance(spec, dict):
        _fail("qualification.runtime-report.invalid")
    application = spec.get("application")
    storage = spec.get("storage")
    build = spec.get("build")
    deployment = spec.get("deployment")
    if not all(isinstance(item, dict) for item in (application, storage, build, deployment)):
        _fail("qualification.runtime-report.invalid")
    expected = {
        "version": candidate.get("version"),
        "chartVersion": candidate.get("chartVersion"),
        "revision": candidate.get("revision"),
        "imageDigest": candidate.get("controlPlaneImageDigest"),
    }
    actual = {
        "version": application.get("version"),
        "chartVersion": deployment.get("helmChartVersion"),
        "revision": build.get("revision"),
        "imageDigest": deployment.get("imageDigest"),
    }
    if (
        actual != expected
        or build.get("mode") != "release"
        or storage.get("requiredMigration") != required_migration
    ):
        _fail("qualification.runtime-report.identity-mismatch")
    return actual


def record_install(
    *,
    bundle: Path,
    output: Path,
    runtime_report: Path,
    required_migration: str,
    applied_migration_count: int,
    final_helm_revision: int,
    platform: str,
    kubernetes_version: str,
    container_runtime_version: str,
) -> Mapping[str, Any]:
    _require_output_outside_bundle(bundle, output)
    _, candidate = _candidate(bundle)
    _require_clean_source(candidate)
    _checked_string(required_migration, MIGRATION, "qualification.migration.invalid")
    count = _positive_integer(
        applied_migration_count, "qualification.migration-count.invalid"
    )
    if final_helm_revision != 2:
        _fail("qualification.install.helm-revision.invalid")
    environment = _environment(
        platform=platform,
        kubernetes_version=kubernetes_version,
        container_runtime_version=container_runtime_version,
    )
    if platform not in candidate["platforms"]:
        _fail("qualification.environment.platform-not-packaged")
    runtime = _runtime_identity(runtime_report, candidate, required_migration)
    profile = {
        "name": "packaged-install",
        "observedAt": _now(),
        "result": "passed",
        "checks": _checks(INSTALL_CHECKS),
        "measurement": {
            "runtime": runtime,
            "requiredMigration": required_migration,
            "appliedMigrationCount": count,
            "finalHelmRevision": final_helm_revision,
            "backupChecksumVerified": True,
            "isolatedRestoreVerified": True,
        },
    }
    report = _report(
        candidate=candidate, environment=environment, profiles=[profile]
    )
    verify_report_document(report, candidate=candidate, require_complete=False)
    _write_report(output, report)
    return report


def _upgrade_revision(
    *, version: str, chart_version: str, revision: str, migration: str, image_digest: str
) -> dict[str, str]:
    _checked_string(version, SEMVER, "qualification.upgrade.version.invalid")
    _checked_string(
        chart_version, SEMVER, "qualification.upgrade.chart-version.invalid"
    )
    _checked_string(revision, REVISION, "qualification.upgrade.revision.invalid")
    _checked_string(migration, MIGRATION, "qualification.upgrade.migration.invalid")
    _checked_string(image_digest, DIGEST, "qualification.upgrade.digest.invalid")
    return {
        "version": version,
        "chartVersion": chart_version,
        "revision": revision,
        "migration": migration,
        "imageDigest": image_digest,
    }


def _availability(path: Path, base_version: str, target_version: str) -> dict[str, int]:
    state = _json(path, "qualification.availability.invalid")
    version_counts = state.get("versionCounts")
    failure_kinds = state.get("failureKinds")
    if (
        state.get("stopped") is not True
        or not isinstance(version_counts, dict)
        or failure_kinds != {}
    ):
        _fail("qualification.availability.invalid")
    values = {
        "attemptCount": state.get("attemptCount"),
        "requestCount": state.get("requestCount"),
        "successCount": state.get("successCount"),
        "failureCount": state.get("failureCount"),
        "baseSuccessCount": version_counts.get(base_version),
        "targetSuccessCount": version_counts.get(target_version),
    }
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values.values()):
        _fail("qualification.availability.invalid")
    return values  # type: ignore[return-value]


def record_upgrade(
    *,
    bundle: Path,
    output: Path,
    availability_state: Path,
    base_version: str,
    base_chart_version: str,
    base_revision: str,
    base_migration: str,
    base_image_digest: str,
    target_migration: str,
    applied_migration_count: int,
    final_helm_revision: int,
    platform: str,
    kubernetes_version: str,
    container_runtime_version: str,
) -> Mapping[str, Any]:
    _require_output_outside_bundle(bundle, output)
    _, candidate = _candidate(bundle)
    _require_clean_source(candidate)
    environment = _environment(
        platform=platform,
        kubernetes_version=kubernetes_version,
        container_runtime_version=container_runtime_version,
    )
    if platform not in candidate["platforms"]:
        _fail("qualification.environment.platform-not-packaged")
    base = _upgrade_revision(
        version=base_version,
        chart_version=base_chart_version,
        revision=base_revision,
        migration=base_migration,
        image_digest=base_image_digest,
    )
    target = _upgrade_revision(
        version=str(candidate["version"]),
        chart_version=str(candidate["chartVersion"]),
        revision=str(candidate["revision"]),
        migration=target_migration,
        image_digest=str(candidate["controlPlaneImageDigest"]),
    )
    if base_revision == target["revision"] or base_version == target["version"]:
        _fail("qualification.upgrade.strict-ancestor.required")
    ancestry = subprocess.run(
        ("git", "merge-base", "--is-ancestor", base_revision, target["revision"]),
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if ancestry.returncode != 0:
        _fail("qualification.upgrade.strict-ancestor.required")
    relation = "retained" if base_migration == target_migration else "advanced"
    if target_migration < base_migration:
        _fail("qualification.upgrade.migration-regression")
    count = _positive_integer(
        applied_migration_count, "qualification.migration-count.invalid"
    )
    if final_helm_revision != 4:
        _fail("qualification.upgrade.helm-revision.invalid")
    availability = _availability(availability_state, base_version, str(candidate["version"]))
    profile = {
        "name": "n-minus-one-upgrade",
        "observedAt": _now(),
        "result": "passed",
        "checks": _checks(UPGRADE_CHECKS),
        "measurement": {
            "base": base,
            "target": target,
            "migrationRelation": relation,
            "appliedMigrationCount": count,
            "finalHelmRevision": final_helm_revision,
            "availability": availability,
            "inFlightDrain": {
                "blockedReadObserved": True,
                "podTerminationRequested": True,
                "requestCompleted": True,
            },
        },
    }
    install_profiles: list[Mapping[str, Any]] = []
    report_id = None
    if output.is_file():
        existing = _json(output, "qualification.report.invalid")
        verify_report_document(existing, candidate=candidate, require_complete=False)
        existing_spec = existing["spec"]
        if existing_spec["environment"] != environment:
            _fail("qualification.environment.mismatch")
        install_profiles = [
            item
            for item in existing_spec["profiles"]
            if item.get("name") == "packaged-install"
        ]
        report_id = existing["metadata"]["id"]
    profiles = [*install_profiles, profile]
    report = _report(
        candidate=candidate,
        environment=environment,
        profiles=profiles,
        report_id=report_id,
    )
    verify_report_document(report, candidate=candidate, require_complete=False)
    _write_report(output, report)
    return report


def _validate_check_profile(profile: Mapping[str, Any], expected: Sequence[str]) -> None:
    checks = profile.get("checks")
    if not isinstance(checks, list):
        _fail("qualification.profile.checks.invalid")
    ids = tuple(
        item.get("id") if isinstance(item, dict) else None for item in checks
    )
    if ids != tuple(expected) or any(
        not isinstance(item, dict)
        or set(item) != {"id", "status"}
        or item.get("status") != "passed"
        for item in checks
    ):
        _fail("qualification.profile.checks.invalid")


def _validate_runtime_identity(
    value: object, candidate: Mapping[str, Any], *, migration: str | None = None
) -> Mapping[str, Any]:
    runtime = _exact_keys(
        value,
        ("version", "chartVersion", "revision", "imageDigest"),
        "qualification.runtime.invalid",
    )
    expected = {
        "version": candidate.get("version"),
        "chartVersion": candidate.get("chartVersion"),
        "revision": candidate.get("revision"),
        "imageDigest": candidate.get("controlPlaneImageDigest"),
    }
    if runtime != expected:
        _fail("qualification.runtime.identity-mismatch")
    if migration is not None:
        _checked_string(migration, MIGRATION, "qualification.migration.invalid")
    return runtime


def _validate_install_profile(
    profile: Mapping[str, Any], candidate: Mapping[str, Any]
) -> None:
    _validate_check_profile(profile, INSTALL_CHECKS)
    measurement = _exact_keys(
        profile.get("measurement"),
        (
            "runtime",
            "requiredMigration",
            "appliedMigrationCount",
            "finalHelmRevision",
            "backupChecksumVerified",
            "isolatedRestoreVerified",
        ),
        "qualification.install.measurement.invalid",
    )
    migration = _checked_string(
        measurement.get("requiredMigration"),
        MIGRATION,
        "qualification.migration.invalid",
    )
    _validate_runtime_identity(measurement.get("runtime"), candidate, migration=migration)
    _positive_integer(
        measurement.get("appliedMigrationCount"),
        "qualification.migration-count.invalid",
    )
    if (
        measurement.get("finalHelmRevision") != 2
        or measurement.get("backupChecksumVerified") is not True
        or measurement.get("isolatedRestoreVerified") is not True
    ):
        _fail("qualification.install.measurement.invalid")


def _validate_upgrade_revision(value: object) -> Mapping[str, Any]:
    revision = _exact_keys(
        value,
        ("version", "chartVersion", "revision", "migration", "imageDigest"),
        "qualification.upgrade.revision.invalid",
    )
    _checked_string(revision.get("version"), SEMVER, "qualification.upgrade.version.invalid")
    _checked_string(
        revision.get("chartVersion"),
        SEMVER,
        "qualification.upgrade.chart-version.invalid",
    )
    _checked_string(
        revision.get("revision"), REVISION, "qualification.upgrade.revision.invalid"
    )
    _checked_string(
        revision.get("migration"), MIGRATION, "qualification.upgrade.migration.invalid"
    )
    _checked_string(
        revision.get("imageDigest"), DIGEST, "qualification.upgrade.digest.invalid"
    )
    return revision


def _validate_upgrade_profile(
    profile: Mapping[str, Any], candidate: Mapping[str, Any]
) -> None:
    _validate_check_profile(profile, UPGRADE_CHECKS)
    measurement = _exact_keys(
        profile.get("measurement"),
        (
            "base",
            "target",
            "migrationRelation",
            "appliedMigrationCount",
            "finalHelmRevision",
            "availability",
            "inFlightDrain",
        ),
        "qualification.upgrade.measurement.invalid",
    )
    base = _validate_upgrade_revision(measurement.get("base"))
    target = _validate_upgrade_revision(measurement.get("target"))
    expected_target = {
        "version": candidate.get("version"),
        "chartVersion": candidate.get("chartVersion"),
        "revision": candidate.get("revision"),
        "migration": target.get("migration"),
        "imageDigest": candidate.get("controlPlaneImageDigest"),
    }
    if target != expected_target or base.get("revision") == target.get("revision"):
        _fail("qualification.upgrade.identity-mismatch")
    base_migration = str(base["migration"])
    target_migration = str(target["migration"])
    relation = "retained" if base_migration == target_migration else "advanced"
    if target_migration < base_migration or measurement.get("migrationRelation") != relation:
        _fail("qualification.upgrade.migration-relation.invalid")
    _positive_integer(
        measurement.get("appliedMigrationCount"),
        "qualification.migration-count.invalid",
    )
    if measurement.get("finalHelmRevision") != 4:
        _fail("qualification.upgrade.helm-revision.invalid")
    availability = _exact_keys(
        measurement.get("availability"),
        (
            "attemptCount",
            "requestCount",
            "successCount",
            "failureCount",
            "baseSuccessCount",
            "targetSuccessCount",
        ),
        "qualification.availability.invalid",
    )
    values = {
        key: availability.get(key)
        for key in (
            "attemptCount",
            "requestCount",
            "successCount",
            "failureCount",
            "baseSuccessCount",
            "targetSuccessCount",
        )
    }
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values.values()):
        _fail("qualification.availability.invalid")
    if (
        values["failureCount"] != 0
        or values["attemptCount"] != values["successCount"]
        or values["requestCount"] != values["successCount"] * 2
        or values["successCount"]
        != values["baseSuccessCount"] + values["targetSuccessCount"]
        or values["baseSuccessCount"] < 10
        or values["targetSuccessCount"] < 10
    ):
        _fail("qualification.availability.invalid")
    drain = _exact_keys(
        measurement.get("inFlightDrain"),
        ("blockedReadObserved", "podTerminationRequested", "requestCompleted"),
        "qualification.in-flight-drain.invalid",
    )
    if any(value is not True for value in drain.values()):
        _fail("qualification.in-flight-drain.invalid")


def verify_report_document(
    report: Mapping[str, Any], *, candidate: Mapping[str, Any], require_complete: bool
) -> Mapping[str, Any]:
    _exact_keys(
        report,
        ("apiVersion", "kind", "metadata", "spec"),
        "qualification.report.invalid",
    )
    if (
        report.get("apiVersion") != "iip.dev/v1alpha1"
        or report.get("kind") != "ReleaseQualificationReport"
    ):
        _fail("qualification.report.invalid")
    metadata = _exact_keys(
        report.get("metadata"),
        ("id", "generatedAt", "sourceRevision", "sourceDirty"),
        "qualification.metadata.invalid",
    )
    _checked_string(metadata.get("id"), REPORT_ID, "qualification.metadata.id.invalid")
    generated = _timestamp(metadata.get("generatedAt"))
    if (
        metadata.get("sourceRevision") != candidate.get("revision")
        or metadata.get("sourceDirty") is not False
    ):
        _fail("qualification.metadata.source.invalid")
    spec = _exact_keys(
        report.get("spec"),
        ("status", "candidate", "environment", "profiles", "summary"),
        "qualification.spec.invalid",
    )
    if spec.get("candidate") != candidate:
        _fail("qualification.candidate.mismatch")
    environment = _exact_keys(
        spec.get("environment"),
        ("profile", "platform", "kubernetesVersion", "containerRuntime"),
        "qualification.environment.invalid",
    )
    runtime = _exact_keys(
        environment.get("containerRuntime"),
        ("name", "version"),
        "qualification.environment.invalid",
    )
    if (
        environment.get("profile") != "local-kind"
        or runtime.get("name") != "docker"
        or not isinstance(environment.get("kubernetesVersion"), str)
        or not environment.get("kubernetesVersion")
        or not isinstance(runtime.get("version"), str)
        or not runtime.get("version")
    ):
        _fail("qualification.environment.invalid")
    platform = _checked_string(
        environment.get("platform"),
        PLATFORM,
        "qualification.environment.platform.invalid",
    )
    if platform not in candidate.get("platforms", []):
        _fail("qualification.environment.platform-not-packaged")
    profiles = spec.get("profiles")
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 2:
        _fail("qualification.profiles.invalid")
    names = [item.get("name") if isinstance(item, dict) else None for item in profiles]
    if len(set(names)) != len(names) or any(name not in REQUIRED_PROFILES for name in names):
        _fail("qualification.profiles.invalid")
    if names != [name for name in REQUIRED_PROFILES if name in names]:
        _fail("qualification.profiles.order.invalid")
    observed: list[datetime] = []
    for profile in profiles:
        normalized = _exact_keys(
            profile,
            ("name", "observedAt", "result", "checks", "measurement"),
            "qualification.profile.invalid",
        )
        if normalized.get("result") != "passed":
            _fail("qualification.profile.result.invalid")
        observed.append(_timestamp(normalized.get("observedAt")))
        if normalized["name"] == "packaged-install":
            _validate_install_profile(normalized, candidate)
        else:
            _validate_upgrade_profile(normalized, candidate)
    if generated < max(observed):
        _fail("qualification.timestamp.order.invalid")
    expected_summary = _summary(profiles)
    if spec.get("summary") != expected_summary or spec.get("status") != expected_summary[
        "overallStatus"
    ]:
        _fail("qualification.summary.invalid")
    if require_complete and expected_summary["overallStatus"] != "qualified":
        _fail("qualification.required-profiles.incomplete")
    return report


def verify_report(
    report_path: Path, bundle: Path, *, require_complete: bool = False
) -> Mapping[str, Any]:
    _require_output_outside_bundle(bundle, report_path)
    _, candidate = _candidate(bundle)
    report = _json(report_path, "qualification.report.invalid")
    return verify_report_document(
        report, candidate=candidate, require_complete=require_complete
    )


def _common_environment(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--platform", required=True)
    parser.add_argument("--kubernetes-version", required=True)
    parser.add_argument("--container-runtime-version", required=True)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("record-install")
    install.add_argument("bundle", type=Path)
    install.add_argument("output", type=Path)
    install.add_argument("--runtime-report", type=Path, required=True)
    install.add_argument("--required-migration", required=True)
    install.add_argument("--applied-migration-count", type=int, required=True)
    install.add_argument("--final-helm-revision", type=int, required=True)
    _common_environment(install)

    upgrade = subparsers.add_parser("record-upgrade")
    upgrade.add_argument("bundle", type=Path)
    upgrade.add_argument("output", type=Path)
    upgrade.add_argument("--availability-state", type=Path, required=True)
    upgrade.add_argument("--base-version", required=True)
    upgrade.add_argument("--base-chart-version", required=True)
    upgrade.add_argument("--base-revision", required=True)
    upgrade.add_argument("--base-migration", required=True)
    upgrade.add_argument("--base-image-digest", required=True)
    upgrade.add_argument("--target-migration", required=True)
    upgrade.add_argument("--applied-migration-count", type=int, required=True)
    upgrade.add_argument("--final-helm-revision", type=int, required=True)
    _common_environment(upgrade)

    verify = subparsers.add_parser("verify")
    verify.add_argument("bundle", type=Path)
    verify.add_argument("report", type=Path)
    verify.add_argument("--require-complete", action="store_true")

    arguments = parser.parse_args(tuple(argv) if argv is not None else None)
    try:
        if arguments.command == "record-install":
            record_install(
                bundle=arguments.bundle,
                output=arguments.output,
                runtime_report=arguments.runtime_report,
                required_migration=arguments.required_migration,
                applied_migration_count=arguments.applied_migration_count,
                final_helm_revision=arguments.final_helm_revision,
                platform=arguments.platform,
                kubernetes_version=arguments.kubernetes_version,
                container_runtime_version=arguments.container_runtime_version,
            )
        elif arguments.command == "record-upgrade":
            record_upgrade(
                bundle=arguments.bundle,
                output=arguments.output,
                availability_state=arguments.availability_state,
                base_version=arguments.base_version,
                base_chart_version=arguments.base_chart_version,
                base_revision=arguments.base_revision,
                base_migration=arguments.base_migration,
                base_image_digest=arguments.base_image_digest,
                target_migration=arguments.target_migration,
                applied_migration_count=arguments.applied_migration_count,
                final_helm_revision=arguments.final_helm_revision,
                platform=arguments.platform,
                kubernetes_version=arguments.kubernetes_version,
                container_runtime_version=arguments.container_runtime_version,
            )
        else:
            verify_report(
                arguments.report,
                arguments.bundle,
                require_complete=arguments.require_complete,
            )
    except ReleaseQualificationError as exc:
        parser.exit(1, f"{exc}\n")
    print(
        f"release qualification {arguments.command} passed: "
        f"{getattr(arguments, 'output', getattr(arguments, 'report', ''))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
