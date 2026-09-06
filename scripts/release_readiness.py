#!/usr/bin/env python3
"""Aggregate exact local release evidence without claiming production readiness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

from release_bundle import ReleaseBundleError, sha256_file, verify_bundle


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "contracts" / "schemas"
API_VERSION = "iip.dev/v1alpha1"
KIND = "ReleaseReadinessReport"


class ReleaseReadinessError(RuntimeError):
    """A stable release-readiness validation failure."""


def _fail(code: str) -> None:
    raise ReleaseReadinessError(code)


@dataclass(frozen=True)
class EvidenceRequirement:
    identifier: str
    filename: str | None
    schema: str
    api_version: str
    kind: str
    boundary: str
    status_path: tuple[str, ...]
    success_status: str
    expected_fields: tuple[tuple[tuple[str, ...], object], ...] = ()


REQUIREMENTS = (
    EvidenceRequirement(
        "packaged-release",
        None,
        "release-qualification-report.schema.json",
        "iip.dev/v1alpha1",
        "ReleaseQualificationReport",
        "packaged-local",
        ("spec", "status"),
        "qualified",
    ),
    EvidenceRequirement(
        "sbom-vulnerabilities",
        None,
        "release-vulnerability-qualification-report.schema.json",
        "iip.dev/v1alpha1",
        "ReleaseVulnerabilityQualificationReport",
        "security-local",
        ("spec", "status"),
        "qualified",
        ((("spec", "qualificationLevel"), "sbom-vulnerability-v1"),),
    ),
    EvidenceRequirement(
        "investigation-capacity",
        "investigation-capacity-report.json",
        "investigation-capacity-report.schema.json",
        "iip.platform/v1alpha1",
        "InvestigationCapacityReport",
        "local-runtime",
        ("spec", "status"),
        "certified",
        ((("spec", "profile", "name"), "postgresql-investigation-dispatch-v1"),),
    ),
    EvidenceRequirement(
        "credential-broker",
        "credential-broker-compatibility-report.json",
        "credential-broker-compatibility-report.schema.json",
        "iip.platform/v1alpha1",
        "CredentialBrokerCompatibilityReport",
        "local-integration",
        ("spec", "status"),
        "compatible",
        ((("spec", "profile", "name"), "local-tls-workload-identity-v1"),),
    ),
    EvidenceRequirement(
        "oidc-issuer",
        "oidc-issuer-compatibility-report.json",
        "oidc-issuer-compatibility-report.schema.json",
        "iip.platform/v1alpha1",
        "OidcIssuerCompatibilityReport",
        "local-integration",
        ("spec", "status"),
        "compatible",
        ((("spec", "profile", "name"), "local-oidc-rs256-jwks-v1"),),
    ),
    EvidenceRequirement(
        "oidc-browser",
        "oidc-browser-compatibility-report.json",
        "oidc-browser-compatibility-report.schema.json",
        "iip.platform/v1alpha1",
        "OidcBrowserCompatibilityReport",
        "local-integration",
        ("spec", "status"),
        "compatible",
        ((("spec", "profile", "name"), "local-oidc-browser-pkce-v1"),),
    ),
    EvidenceRequirement(
        "policy-engine",
        "policy-engine-compatibility-report.json",
        "policy-engine-compatibility-report.schema.json",
        "iip.platform/v1alpha1",
        "PolicyEngineCompatibilityReport",
        "local-integration",
        ("spec", "status"),
        "compatible",
        ((("spec", "profile", "name"), "local-external-http-policy-v1"),),
    ),
    EvidenceRequirement(
        "otlp-receiver",
        "otlp-receiver-compatibility-report.json",
        "otlp-receiver-compatibility-report.schema.json",
        "iip.platform/v1alpha1",
        "OtlpReceiverCompatibilityReport",
        "local-integration",
        ("spec", "status"),
        "compatible",
        ((("spec", "profile", "name"), "local-mutual-spiffe-otlp-http-v1"),),
    ),
    EvidenceRequirement(
        "bedrock-converse",
        "bedrock-instrumentation-offline-report.json",
        "bedrock-instrumentation-compatibility-report.schema.json",
        "iip.platform/v1alpha1",
        "BedrockInstrumentationCompatibilityReport",
        "offline-provider",
        ("spec", "status"),
        "compatible",
        (
            (("spec", "qualificationLevel"), "offline-sdk-interoperability"),
            (
                ("spec", "profile", "name"),
                "otel-python-botocore-converse-iip-usage-v1",
            ),
        ),
    ),
    EvidenceRequirement(
        "bedrock-converse-stream",
        "bedrock-converse-stream-instrumentation-offline-report.json",
        "bedrock-instrumentation-compatibility-report.schema.json",
        "iip.platform/v1alpha1",
        "BedrockInstrumentationCompatibilityReport",
        "offline-provider",
        ("spec", "status"),
        "compatible",
        (
            (("spec", "qualificationLevel"), "offline-sdk-interoperability"),
            (
                ("spec", "profile", "name"),
                "otel-python-botocore-converse-stream-iip-usage-v1",
            ),
        ),
    ),
    EvidenceRequirement(
        "openai-chat-completions",
        "openai-instrumentation-offline-report.json",
        "openai-instrumentation-compatibility-report.schema.json",
        "iip.platform/v1alpha1",
        "OpenAIInstrumentationCompatibilityReport",
        "offline-provider",
        ("spec", "status"),
        "compatible",
        (
            (("spec", "qualificationLevel"), "offline-sdk-interoperability"),
            (
                ("spec", "profile", "name"),
                "otel-python-openai-chat-completions-v1",
            ),
        ),
    ),
    EvidenceRequirement(
        "ai-finops-runtime",
        "ai-finops-runtime-compatibility-report.json",
        "ai-finops-runtime-compatibility-report.schema.json",
        "iip.dev/v1alpha1",
        "AiFinopsRuntimeCompatibilityReport",
        "local-runtime",
        ("spec", "status"),
        "compatible",
        (
            (
                ("spec", "qualificationLevel"),
                "local-multi-provider-ai-finops-v1",
            ),
        ),
    ),
    EvidenceRequirement(
        "postgresql-logical-recovery",
        "postgresql-recovery-qualification-report.json",
        "postgresql-recovery-qualification-report.schema.json",
        "iip.platform/v1alpha1",
        "PostgreSQLRecoveryQualificationReport",
        "local-recovery",
        ("spec", "status"),
        "qualified",
        ((("spec", "profile", "name"), "quiesced-logical-restore-v1"),),
    ),
    EvidenceRequirement(
        "postgresql-continuity",
        "postgresql-continuity-qualification-report.json",
        "postgresql-continuity-qualification-report.schema.json",
        "iip.platform/v1alpha1",
        "PostgreSQLContinuityQualificationReport",
        "local-recovery",
        ("spec", "status"),
        "qualified",
        ((("spec", "profile", "name"), "physical-streaming-pitr-v1"),),
    ),
    EvidenceRequirement(
        "kubernetes-planned-disruption",
        "kubernetes-availability-qualification-report.json",
        "kubernetes-availability-qualification-report.schema.json",
        "iip.platform/v1alpha1",
        "KubernetesAvailabilityQualificationReport",
        "local-availability",
        ("spec", "status"),
        "qualified",
        ((("spec", "qualificationLevel"), "local-multi-node-kind-v2"),),
    ),
    EvidenceRequirement(
        "github-context",
        "github-context-compatibility-report.json",
        "github-context-compatibility-report.schema.json",
        "iip.platform/v1alpha1",
        "GithubContextCompatibilityReport",
        "local-integration",
        ("spec", "status"),
        "compatible",
        ((("spec", "profile", "name"), "local-github-rest-contents-v1"),),
    ),
    EvidenceRequirement(
        "plugin-runtime",
        "plugin-compatibility-report.json",
        "plugin-compatibility-report.schema.json",
        "iip.platform/v1alpha1",
        "PluginCompatibilityReport",
        "local-integration",
        ("spec", "summary", "overallStatus"),
        "compatible",
    ),
    EvidenceRequirement(
        "production-core-configuration",
        "customer-deployment-preflight-report.json",
        "customer-deployment-preflight-report.schema.json",
        "iip.platform/v1alpha1",
        "CustomerDeploymentPreflightReport",
        "configuration-only",
        ("spec", "status"),
        "configuration-ready",
        (
            (("spec", "qualificationBoundary"), "pre-install-only"),
            (("spec", "profile", "name"), "production-core-v1"),
            (("spec", "environment", "mode"), "static"),
        ),
    ),
    EvidenceRequirement(
        "production-ai-finops-configuration",
        "customer-ai-finops-preflight-report.json",
        "customer-deployment-preflight-report.schema.json",
        "iip.platform/v1alpha1",
        "CustomerDeploymentPreflightReport",
        "configuration-only",
        ("spec", "status"),
        "configuration-ready",
        (
            (("spec", "qualificationBoundary"), "pre-install-only"),
            (("spec", "profile", "name"), "production-ai-finops-v0"),
            (("spec", "environment", "mode"), "static"),
        ),
    ),
)

EXTERNAL_GATES = (
    ("approved-registry-publication", "release-readiness.external.registry-publication"),
    (
        "organizational-release-signatures",
        "release-readiness.external.organizational-signatures",
    ),
    (
        "customer-install-preflight",
        "release-readiness.external.customer-install-preflight",
    ),
    (
        "customer-ingress-availability",
        "release-readiness.external.customer-ingress-availability",
    ),
    (
        "customer-integration-interoperability",
        "release-readiness.external.customer-integrations",
    ),
    (
        "live-ai-provider-and-price-authority",
        "release-readiness.external.live-ai-and-price",
    ),
    ("design-partner-acceptance", "release-readiness.external.design-partner"),
    (
        "legal-brand-and-governance",
        "release-readiness.external.legal-brand-governance",
    ),
)


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("release-readiness.document.unreadable")
    if not isinstance(value, dict):
        _fail("release-readiness.document.invalid")
    return value


def _path(value: object, parts: Sequence[str]) -> object:
    current = value
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        _fail("release-readiness.document.unreadable")
    return f"sha256:{digest.hexdigest()}"


def _schema(name: str) -> Mapping[str, Any]:
    return _read_json(SCHEMA_DIR / name)


def _schema_valid(document: Mapping[str, Any], schema_name: str) -> bool:
    validator = Draft202012Validator(
        _schema(schema_name), format_checker=FormatChecker()
    )
    return next(validator.iter_errors(document), None) is None


def _candidate(bundle: Path) -> dict[str, str]:
    try:
        manifest = verify_bundle(bundle)
    except ReleaseBundleError as exc:
        raise ReleaseReadinessError(str(exc)) from None
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        _fail("release-readiness.bundle.invalid")
    values = {
        "version": metadata.get("version"),
        "chartVersion": metadata.get("chartVersion"),
        "revision": metadata.get("revision"),
        "manifestDigest": f"sha256:{sha256_file(bundle / 'release-manifest.json')}",
        "signatureStatus": metadata.get("signatureStatus"),
    }
    if not all(isinstance(value, str) for value in values.values()):
        _fail("release-readiness.bundle.invalid")
    return values  # type: ignore[return-value]


def _git_state() -> tuple[str, bool]:
    def run(*arguments: str) -> str:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            _fail("release-readiness.source.unavailable")
        return completed.stdout.strip()

    return run("rev-parse", "HEAD"), bool(
        run("status", "--porcelain", "--untracked-files=normal")
    )


def _require_clean(candidate: Mapping[str, str]) -> None:
    revision, dirty = _git_state()
    if dirty:
        _fail("release-readiness.source.dirty")
    if revision != candidate["revision"]:
        _fail("release-readiness.source.revision-mismatch")


def _require_report_outside_bundle(bundle: Path, report: Path) -> None:
    bundle = bundle.expanduser().resolve()
    report = report.expanduser().resolve()
    if report == bundle or bundle in report.parents:
        _fail("release-readiness.report.inside-bundle")


def _reject(
    requirement: EvidenceRequirement,
    code: str,
    *,
    report_digest: str | None = None,
    observed_status: object = None,
    source_revision: object = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": requirement.identifier,
        "contractKind": requirement.kind,
        "qualificationBoundary": requirement.boundary,
        "status": "rejected",
        "errorCode": code,
    }
    if report_digest is not None:
        result["reportDigest"] = report_digest
    if isinstance(observed_status, str) and observed_status:
        result["observedStatus"] = observed_status
    if isinstance(source_revision, str) and source_revision:
        result["sourceRevision"] = source_revision
    return result


def _evidence_result(
    requirement: EvidenceRequirement,
    path: Path,
    candidate: Mapping[str, str],
) -> dict[str, Any]:
    base = {
        "id": requirement.identifier,
        "contractKind": requirement.kind,
        "qualificationBoundary": requirement.boundary,
    }
    if not path.is_file():
        return {
            **base,
            "status": "missing",
            "errorCode": "release-readiness.evidence.missing",
        }
    report_digest = _digest(path)
    try:
        document = _read_json(path)
    except ReleaseReadinessError:
        return _reject(
            requirement,
            "release-readiness.evidence.unreadable",
            report_digest=report_digest,
        )
    if not _schema_valid(document, requirement.schema):
        return _reject(
            requirement,
            "release-readiness.evidence.schema-invalid",
            report_digest=report_digest,
        )
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        return _reject(
            requirement,
            "release-readiness.evidence.schema-invalid",
            report_digest=report_digest,
        )
    revision = metadata.get("sourceRevision")
    observed_status = _path(document, requirement.status_path)
    if metadata.get("sourceDirty") is not False:
        return _reject(
            requirement,
            "release-readiness.evidence.source-dirty",
            report_digest=report_digest,
            observed_status=observed_status,
            source_revision=revision,
        )
    if revision != candidate["revision"]:
        return _reject(
            requirement,
            "release-readiness.evidence.revision-mismatch",
            report_digest=report_digest,
            observed_status=observed_status,
            source_revision=revision,
        )
    if document.get("apiVersion") != requirement.api_version or document.get(
        "kind"
    ) != requirement.kind:
        return _reject(
            requirement,
            "release-readiness.evidence.contract-mismatch",
            report_digest=report_digest,
            observed_status=observed_status,
            source_revision=revision,
        )
    if observed_status != requirement.success_status:
        return _reject(
            requirement,
            "release-readiness.evidence.status-not-passed",
            report_digest=report_digest,
            observed_status=observed_status,
            source_revision=revision,
        )
    for field_path, expected in requirement.expected_fields:
        if _path(document, field_path) != expected:
            return _reject(
                requirement,
                "release-readiness.evidence.profile-mismatch",
                report_digest=report_digest,
                observed_status=observed_status,
                source_revision=revision,
            )
    if requirement.identifier == "packaged-release":
        release = _path(document, ("spec", "candidate"))
        expected = {
            "version": candidate["version"],
            "chartVersion": candidate["chartVersion"],
            "revision": candidate["revision"],
            "releaseManifestDigest": candidate["manifestDigest"],
        }
        if not isinstance(release, dict) or any(
            release.get(key) != value for key, value in expected.items()
        ):
            return _reject(
                requirement,
                "release-readiness.evidence.release-mismatch",
                report_digest=report_digest,
                observed_status=observed_status,
                source_revision=revision,
            )
    elif requirement.identifier == "sbom-vulnerabilities":
        release = _path(document, ("spec", "release"))
        expected = {
            "version": candidate["version"],
            "revision": candidate["revision"],
            "manifestDigest": candidate["manifestDigest"],
        }
        if not isinstance(release, dict) or any(
            release.get(key) != value for key, value in expected.items()
        ):
            return _reject(
                requirement,
                "release-readiness.evidence.release-mismatch",
                report_digest=report_digest,
                observed_status=observed_status,
                source_revision=revision,
            )
    elif requirement.boundary == "configuration-only":
        profile = _path(document, ("spec", "profile"))
        if not isinstance(profile, dict) or (
            profile.get("applicationVersion") != candidate["version"]
            or profile.get("chartVersion") != candidate["chartVersion"]
        ):
            return _reject(
                requirement,
                "release-readiness.evidence.release-mismatch",
                report_digest=report_digest,
                observed_status=observed_status,
                source_revision=revision,
            )
    elif requirement.identifier == "ai-finops-runtime":
        if (
            _path(document, ("spec", "environment", "applicationVersion"))
            != candidate["version"]
        ):
            return _reject(
                requirement,
                "release-readiness.evidence.release-mismatch",
                report_digest=report_digest,
                observed_status=observed_status,
                source_revision=revision,
            )
    return {
        **base,
        "status": "passed",
        "reportDigest": report_digest,
        "observedStatus": observed_status,
        "sourceRevision": revision,
    }


def _paths(
    *,
    release_qualification: Path,
    vulnerability_qualification: Path,
    evidence_dir: Path,
) -> dict[str, Path]:
    result = {
        "packaged-release": release_qualification,
        "sbom-vulnerabilities": vulnerability_qualification,
    }
    for requirement in REQUIREMENTS:
        if requirement.filename is not None:
            result[requirement.identifier] = evidence_dir / requirement.filename
    return result


def _summary(evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    passed = sum(item.get("status") == "passed" for item in evidence)
    missing = sum(item.get("status") == "missing" for item in evidence)
    rejected = sum(item.get("status") == "rejected" for item in evidence)
    status = "locally-qualified" if passed == len(REQUIREMENTS) else "incomplete"
    return {
        "requiredEvidence": len(REQUIREMENTS),
        "passedEvidence": passed,
        "missingEvidence": missing,
        "rejectedEvidence": rejected,
        "externalGateCount": len(EXTERNAL_GATES),
        "overallStatus": status,
    }


def _external_gates() -> list[dict[str, str]]:
    return [
        {"id": identifier, "status": "external-required", "reasonCode": reason}
        for identifier, reason in EXTERNAL_GATES
    ]


def assess(
    *,
    bundle: Path,
    release_qualification: Path,
    vulnerability_qualification: Path,
    evidence_dir: Path,
    require_clean: bool = False,
    report_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    bundle = bundle.expanduser().resolve()
    candidate = _candidate(bundle)
    if require_clean:
        _require_clean(candidate)
    paths = _paths(
        release_qualification=release_qualification.expanduser().resolve(),
        vulnerability_qualification=vulnerability_qualification.expanduser().resolve(),
        evidence_dir=evidence_dir.expanduser().resolve(),
    )
    evidence = [
        _evidence_result(requirement, paths[requirement.identifier], candidate)
        for requirement in REQUIREMENTS
    ]
    summary = _summary(evidence)
    return {
        "apiVersion": API_VERSION,
        "kind": KIND,
        "metadata": {
            "id": report_id or f"rrr_{uuid.uuid4().hex}",
            "generatedAt": generated_at
            or datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "sourceRevision": candidate["revision"],
            "sourceDirty": False,
        },
        "spec": {
            "status": summary["overallStatus"],
            "qualificationBoundary": "local-candidate-only",
            "release": candidate,
            "evidence": evidence,
            "externalGates": _external_gates(),
            "summary": summary,
        },
    }


def _validate_report_shape(report: Mapping[str, Any]) -> None:
    if not _schema_valid(report, "release-readiness-report.schema.json"):
        _fail("release-readiness.report.schema-invalid")


def verify(
    *,
    bundle: Path,
    report_path: Path,
    release_qualification: Path,
    vulnerability_qualification: Path,
    evidence_dir: Path,
    require_clean: bool = False,
    require_locally_qualified: bool = False,
) -> Mapping[str, Any]:
    report = _read_json(report_path.expanduser().resolve())
    _validate_report_shape(report)
    expected = assess(
        bundle=bundle,
        release_qualification=release_qualification,
        vulnerability_qualification=vulnerability_qualification,
        evidence_dir=evidence_dir,
        require_clean=require_clean,
        report_id=str(_path(report, ("metadata", "id"))),
        generated_at=str(_path(report, ("metadata", "generatedAt"))),
    )
    if report != expected:
        _fail("release-readiness.report.evidence-mismatch")
    if require_locally_qualified and _path(
        report, ("spec", "status")
    ) != "locally-qualified":
        _fail("release-readiness.report.incomplete")
    return report


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


def _add_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--release-qualification", type=Path, required=True)
    parser.add_argument("--vulnerability-qualification", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--require-locally-qualified", action="store_true")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    _add_inputs(generate)
    generate.add_argument("--output", type=Path, required=True)
    verify_command = commands.add_parser("verify")
    _add_inputs(verify_command)
    verify_command.add_argument("--report", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "generate":
            _require_report_outside_bundle(args.bundle, args.output)
            report = assess(
                bundle=args.bundle,
                release_qualification=args.release_qualification,
                vulnerability_qualification=args.vulnerability_qualification,
                evidence_dir=args.evidence_dir,
                require_clean=args.require_clean,
            )
            _validate_report_shape(report)
            _write(args.output, report)
            status = report["spec"]["status"]
            print(f"release readiness assessment: {status}; report: {args.output}")
            if args.require_locally_qualified and status != "locally-qualified":
                _fail("release-readiness.report.incomplete")
        else:
            _require_report_outside_bundle(args.bundle, args.report)
            report = verify(
                bundle=args.bundle,
                report_path=args.report,
                release_qualification=args.release_qualification,
                vulnerability_qualification=args.vulnerability_qualification,
                evidence_dir=args.evidence_dir,
                require_clean=args.require_clean,
                require_locally_qualified=args.require_locally_qualified,
            )
            print(f"release readiness report verified: {report['spec']['status']}")
    except ReleaseReadinessError as exc:
        print(str(exc), file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
