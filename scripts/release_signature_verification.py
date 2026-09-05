#!/usr/bin/env python3
"""Verify published release OCI indexes against an exact trust policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_bundle  # noqa: E402
import validate_schemas  # noqa: E402


POLICY_SCHEMA = (
    ROOT / "contracts" / "schemas" / "release-signature-policy.schema.json"
)
REPORT_SCHEMA = (
    ROOT
    / "contracts"
    / "schemas"
    / "release-signature-verification-report.schema.json"
)
ROLES = ("control-plane-image", "plugin-mediation-bridge-image")
CHECK_IDS = (
    "release-bundle-integrity",
    "policy-binding",
    "immutable-references",
    "tool-version",
    "control-plane-signature",
    "bridge-signature",
    "exact-trust",
    "transparency-verification",
    "secret-minimization",
)
DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
STRICT_SEMVER = re.compile(r"(?:v)?([0-9]+)\.([0-9]+)\.([0-9]+)")
MAX_JSON_BYTES = 2_097_152
MAX_SIGNATURES = 16
SIGNATURE_TYPE = "https://sigstore.dev/cosign/sign/v1"


class ReleaseSignatureError(RuntimeError):
    """Release signature verification failed with a stable error code."""


@dataclass(frozen=True)
class VerifiedSignature:
    trust_id: str
    signature_count: int


class CosignRunner(Protocol):
    def version(self) -> str:
        """Return the normalized Cosign semantic version."""

    def verify(
        self,
        *,
        reference: str,
        repository: str,
        digest: str,
        trust: Mapping[str, object],
        transparency_mode: str,
    ) -> VerifiedSignature:
        """Verify one exact OCI reference and return minimized proof."""


class SubprocessCosignRunner:
    """Cosign CLI adapter that never exposes captured provider output."""

    def __init__(self, executable: str = "cosign") -> None:
        if (
            not executable
            or len(executable) > 2048
            or any(ord(character) < 32 or character.isspace() for character in executable)
        ):
            raise ReleaseSignatureError("release-signature.tool.invalid")
        self._executable = executable

    def version(self) -> str:
        completed = self._run((self._executable, "version", "--json"), timeout=30)
        if completed.returncode != 0 or len(completed.stdout) > 65_536:
            raise ReleaseSignatureError("release-signature.tool.unavailable")
        try:
            document = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ReleaseSignatureError("release-signature.tool.output-invalid") from None
        if not isinstance(document, dict):
            raise ReleaseSignatureError("release-signature.tool.output-invalid")
        raw_version = document.get("gitVersion", document.get("version"))
        if not isinstance(raw_version, str):
            raise ReleaseSignatureError("release-signature.tool.output-invalid")
        return _normalized_version(raw_version)

    def verify(
        self,
        *,
        reference: str,
        repository: str,
        digest: str,
        trust: Mapping[str, object],
        transparency_mode: str,
    ) -> VerifiedSignature:
        mode = trust.get("mode")
        raw_candidates = trust.get("identities" if mode == "keyless" else "keys")
        if not isinstance(raw_candidates, list):
            raise ReleaseSignatureError("release-signature.policy.invalid")
        invalid_output = False
        for candidate in raw_candidates:
            if not isinstance(candidate, dict):
                raise ReleaseSignatureError("release-signature.policy.invalid")
            command = [self._executable, "verify", "--output", "json"]
            if mode == "keyless":
                command.extend(
                    (
                        "--certificate-identity",
                        str(candidate["certificateIdentity"]),
                        "--certificate-oidc-issuer",
                        str(candidate["certificateOidcIssuer"]),
                    )
                )
            elif mode == "public-key":
                _verify_public_key(candidate)
                command.extend(("--key", str(candidate["path"])))
                if transparency_mode == "disabled-local-only":
                    command.append("--insecure-ignore-tlog=true")
            else:
                raise ReleaseSignatureError("release-signature.policy.invalid")
            command.append(reference)
            completed = self._run(tuple(command), timeout=180)
            if completed.returncode != 0:
                continue
            try:
                count = _validated_cosign_output(
                    completed.stdout,
                    repository=repository,
                    reference=reference,
                    digest=digest,
                )
            except ReleaseSignatureError:
                invalid_output = True
                continue
            return VerifiedSignature(str(candidate["id"]), count)
        code = (
            "release-signature.tool.output-invalid"
            if invalid_output
            else "release-signature.signature.rejected"
        )
        raise ReleaseSignatureError(code)

    @staticmethod
    def _run(command: Sequence[str], *, timeout: int) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                tuple(command),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
                env=os.environ.copy(),
            )
        except (OSError, subprocess.SubprocessError):
            raise ReleaseSignatureError("release-signature.tool.unavailable") from None


def _validated_cosign_output(
    encoded: bytes,
    *,
    repository: str,
    reference: str,
    digest: str,
) -> int:
    if not encoded or len(encoded) > MAX_JSON_BYTES:
        raise ReleaseSignatureError("release-signature.tool.output-invalid")
    try:
        document = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ReleaseSignatureError("release-signature.tool.output-invalid") from None
    if not isinstance(document, list) or not 1 <= len(document) <= MAX_SIGNATURES:
        raise ReleaseSignatureError("release-signature.tool.output-invalid")
    for item in document:
        if not isinstance(item, dict):
            raise ReleaseSignatureError("release-signature.tool.output-invalid")
        critical = item.get("critical")
        identity = critical.get("identity") if isinstance(critical, dict) else None
        image = critical.get("image") if isinstance(critical, dict) else None
        if (
            not isinstance(identity, dict)
            or identity.get("docker-reference") not in (repository, reference)
            or not isinstance(image, dict)
            or image.get("docker-manifest-digest") != digest
            or critical.get("type") != SIGNATURE_TYPE
        ):
            raise ReleaseSignatureError("release-signature.tool.output-invalid")
    return len(document)


def _verify_public_key(candidate: Mapping[str, object]) -> None:
    path = candidate.get("path")
    expected = candidate.get("sha256")
    if not isinstance(path, str) or not isinstance(expected, str):
        raise ReleaseSignatureError("release-signature.policy.invalid")
    selected = Path(path)
    try:
        content = selected.read_bytes()
    except OSError:
        raise ReleaseSignatureError("release-signature.public-key.unavailable") from None
    if len(content) > 1_048_576 or "sha256:" + hashlib.sha256(content).hexdigest() != expected:
        raise ReleaseSignatureError("release-signature.public-key.mismatch")


def _normalized_version(value: str) -> str:
    matched = STRICT_SEMVER.fullmatch(value.strip())
    if matched is None:
        raise ReleaseSignatureError("release-signature.tool.output-invalid")
    return ".".join(matched.groups())


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def canonical_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def report_id(report: Mapping[str, object]) -> str:
    metadata = dict(report["metadata"])  # type: ignore[arg-type]
    metadata.pop("id", None)
    return "rsv_" + hashlib.sha256(
        canonical_bytes({"metadata": metadata, "spec": report["spec"]})
    ).hexdigest()[:32]


def load_policy(path: Path, *, promotion: bool) -> dict[str, object]:
    policy = _read_json(path, "release-signature.policy.invalid")
    validate_policy_document(policy, promotion=promotion)
    return policy


def validate_policy_document(policy: object, *, promotion: bool = False) -> None:
    schema = _read_json(POLICY_SCHEMA, "release-signature.policy.invalid")
    errors = validate_schemas.instance_validation_errors(
        schema, policy, label="release signature policy"
    )
    if errors or not isinstance(policy, dict):
        raise ReleaseSignatureError("release-signature.policy.invalid")
    spec = policy["spec"]
    assert isinstance(spec, dict)
    version = _version_tuple(str(spec["cosignVersion"]))
    if version[:2] != (3, 1) or version[2] < 2:
        raise ReleaseSignatureError("release-signature.policy.tool-unsupported")
    artifacts = spec["artifacts"]
    assert isinstance(artifacts, list)
    repositories: set[str] = set()
    for expected_role, artifact in zip(ROLES, artifacts):
        assert isinstance(artifact, dict)
        repository = str(artifact["repository"])
        if artifact["role"] != expected_role or not _valid_repository(repository):
            raise ReleaseSignatureError("release-signature.policy.invalid")
        if repository in repositories:
            raise ReleaseSignatureError("release-signature.policy.invalid")
        repositories.add(repository)
        trust = artifact["trust"]
        assert isinstance(trust, dict)
        candidates = trust.get(
            "identities" if trust.get("mode") == "keyless" else "keys"
        )
        assert isinstance(candidates, list)
        ids = [candidate.get("id") for candidate in candidates if isinstance(candidate, dict)]
        if len(ids) != len(candidates) or len(ids) != len(set(ids)):
            raise ReleaseSignatureError("release-signature.policy.invalid")
        if trust.get("mode") == "keyless":
            for candidate in candidates:
                assert isinstance(candidate, dict)
                _validate_https_origin(str(candidate["certificateOidcIssuer"]))
    if promotion:
        if spec["profile"] != "sigstore-keyless-v1":
            raise ReleaseSignatureError("release-signature.policy.not-promotable")
        protected_values = canonical_bytes(policy).decode("utf-8")
        if "replace-me" in protected_values or "example." in protected_values:
            raise ReleaseSignatureError("release-signature.policy.placeholder")


def _valid_repository(value: str) -> bool:
    if "@" in value or "://" in value or value.endswith("/") or "//" in value:
        return False
    host = value.split("/", 1)[0]
    if ":" in host:
        _, port_text = host.rsplit(":", 1)
        try:
            port = int(port_text)
        except ValueError:
            return False
        if not 1 <= port <= 65_535:
            return False
    return True


def _validate_https_origin(value: str) -> None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise ReleaseSignatureError("release-signature.policy.invalid") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65_535)
    ):
        raise ReleaseSignatureError("release-signature.policy.invalid")


def _version_tuple(value: str) -> tuple[int, int, int]:
    matched = STRICT_SEMVER.fullmatch(value)
    if matched is None:
        raise ReleaseSignatureError("release-signature.policy.invalid")
    return tuple(int(part) for part in matched.groups())  # type: ignore[return-value]


def _read_json(path: Path, code: str) -> dict[str, object]:
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise OSError
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ReleaseSignatureError(code) from None
    if not isinstance(document, dict):
        raise ReleaseSignatureError(code)
    return document


def release_identity(manifest: Mapping[str, object]) -> dict[str, object]:
    metadata = manifest.get("metadata")
    spec = manifest.get("spec")
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        raise ReleaseSignatureError("release-signature.bundle.invalid")
    control = spec.get("image")
    bridge = spec.get("pluginMediationBridgeImage")
    if not isinstance(control, dict) or not isinstance(bridge, dict):
        raise ReleaseSignatureError("release-signature.bundle.invalid")
    values = {
        "version": metadata.get("version"),
        "revision": metadata.get("revision"),
        "control-plane-image": control.get("indexDigest"),
        "plugin-mediation-bridge-image": bridge.get("indexDigest"),
    }
    if (
        not isinstance(values["version"], str)
        or not isinstance(values["revision"], str)
        or any(
            not isinstance(values[role], str)
            or DIGEST.fullmatch(str(values[role])) is None
            for role in ROLES
        )
    ):
        raise ReleaseSignatureError("release-signature.bundle.invalid")
    return values


def build_report(
    *,
    manifest: Mapping[str, object],
    manifest_digest: str,
    policy: Mapping[str, object],
    runner: CosignRunner,
    source_revision: str,
    source_dirty: bool,
    generated_at: str,
    platform_name: str,
    python_version: str,
) -> dict[str, object]:
    identity = release_identity(manifest)
    if identity["revision"] != source_revision:
        raise ReleaseSignatureError("release-signature.source.mismatch")
    validate_policy_document(policy)
    spec = policy["spec"]
    metadata = policy["metadata"]
    assert isinstance(spec, dict) and isinstance(metadata, dict)
    configured_version = str(spec["cosignVersion"])
    tool_version_error: str | None = None
    try:
        tool_version = runner.version()
        if tool_version != configured_version:
            tool_version_error = "release-signature.tool.version-mismatch"
    except ReleaseSignatureError as exc:
        tool_version = configured_version
        tool_version_error = str(exc)

    artifact_results: list[dict[str, object]] = []
    for artifact in spec["artifacts"]:  # type: ignore[union-attr]
        assert isinstance(artifact, dict)
        role = str(artifact["role"])
        digest = str(identity[role])
        repository = str(artifact["repository"])
        trust = artifact["trust"]
        assert isinstance(trust, dict)
        candidates = trust[
            "identities" if trust["mode"] == "keyless" else "keys"
        ]
        assert isinstance(candidates, list) and isinstance(candidates[0], dict)
        if tool_version_error is not None:
            result: dict[str, object] = {
                "role": role,
                "indexDigest": digest,
                "trustId": candidates[0]["id"],
                "signatureCount": 0,
                "status": "rejected",
                "errorCode": tool_version_error,
            }
        else:
            try:
                verified = runner.verify(
                    reference=f"{repository}@{digest}",
                    repository=repository,
                    digest=digest,
                    trust=trust,
                    transparency_mode=str(spec["transparencyMode"]),
                )
                result = {
                    "role": role,
                    "indexDigest": digest,
                    "trustId": verified.trust_id,
                    "signatureCount": verified.signature_count,
                    "status": "verified",
                }
            except ReleaseSignatureError as exc:
                result = {
                    "role": role,
                    "indexDigest": digest,
                    "trustId": candidates[0]["id"],
                    "signatureCount": 0,
                    "status": "rejected",
                    "errorCode": str(exc),
                }
        artifact_results.append(result)

    control_ok = artifact_results[0]["status"] == "verified"
    bridge_ok = artifact_results[1]["status"] == "verified"
    exact_trust = control_ok and bridge_ok
    local_only = spec["profile"] == "local-public-key-v1"
    checks = [
        _check("release-bundle-integrity", True, "release-signature.bundle.invalid"),
        _check("policy-binding", True, "release-signature.policy.invalid"),
        _check("immutable-references", True, "release-signature.reference.mutable"),
        _check(
            "tool-version",
            tool_version_error is None,
            tool_version_error or "release-signature.tool.version-mismatch",
        ),
        _check(
            "control-plane-signature",
            control_ok,
            str(artifact_results[0].get("errorCode", "release-signature.signature.rejected")),
        ),
        _check(
            "bridge-signature",
            bridge_ok,
            str(artifact_results[1].get("errorCode", "release-signature.signature.rejected")),
        ),
        _check("exact-trust", exact_trust, "release-signature.trust.rejected"),
        (
            {
                "id": "transparency-verification",
                "status": "not-run",
                "errorCode": "release-signature.transparency.local-only",
            }
            if local_only
            else _check(
                "transparency-verification",
                exact_trust,
                "release-signature.transparency.rejected",
            )
        ),
        _check("secret-minimization", True, "release-signature.output.not-minimized"),
    ]
    failed = sum(check["status"] == "failed" for check in checks)
    not_run = sum(check["status"] == "not-run" for check in checks)
    status = "rejected" if failed else "local-only" if local_only else "verified"
    report_spec: dict[str, object] = {
        "status": status,
        "qualificationLevel": spec["profile"],
        "release": {
            "version": identity["version"],
            "revision": identity["revision"],
            "manifestDigest": manifest_digest,
        },
        "policy": {
            "id": metadata["id"],
            "generation": metadata["generation"],
            "digest": canonical_digest(policy),
            "cosignVersion": configured_version,
        },
        "environment": {
            "platform": platform_name,
            "pythonVersion": python_version,
            "cosignVersion": tool_version,
        },
        "artifacts": artifact_results,
        "checks": checks,
        "summary": {
            "totalChecks": len(CHECK_IDS),
            "passedChecks": len(CHECK_IDS) - failed - not_run,
            "failedChecks": failed,
            "notRunChecks": not_run,
            "overallStatus": status,
        },
    }
    report: dict[str, object] = {
        "apiVersion": "iip.dev/v1alpha1",
        "kind": "ReleaseSignatureVerificationReport",
        "metadata": {
            "id": "rsv_" + ("0" * 32),
            "generatedAt": generated_at,
            "sourceRevision": source_revision,
            "sourceDirty": source_dirty,
        },
        "spec": report_spec,
    }
    report["metadata"]["id"] = report_id(report)  # type: ignore[index]
    _require_minimized(report, policy)
    validate_report_document(report)
    return report


def _check(identifier: str, passed: bool, error_code: str) -> dict[str, str]:
    if passed:
        return {"id": identifier, "status": "passed"}
    return {"id": identifier, "status": "failed", "errorCode": error_code}


def _require_minimized(
    report: Mapping[str, object], policy: Mapping[str, object]
) -> None:
    encoded = canonical_bytes(report).decode("utf-8")
    spec = policy["spec"]
    assert isinstance(spec, dict)
    for artifact in spec["artifacts"]:  # type: ignore[union-attr]
        assert isinstance(artifact, dict)
        forbidden = [str(artifact["repository"])]
        trust = artifact["trust"]
        assert isinstance(trust, dict)
        candidates = trust.get(
            "identities" if trust.get("mode") == "keyless" else "keys"
        )
        assert isinstance(candidates, list)
        for candidate in candidates:
            assert isinstance(candidate, dict)
            forbidden.extend(
                str(candidate[key])
                for key in ("certificateIdentity", "certificateOidcIssuer", "path")
                if key in candidate
            )
        if any(value and value in encoded for value in forbidden):
            raise ReleaseSignatureError("release-signature.output.not-minimized")


def validate_report_document(report: object) -> None:
    schema = _read_json(REPORT_SCHEMA, "release-signature.report.invalid")
    errors = validate_schemas.instance_validation_errors(
        schema, report, label="release signature verification report"
    )
    if errors or not isinstance(report, dict):
        raise ReleaseSignatureError("release-signature.report.invalid")
    metadata = report["metadata"]
    spec = report["spec"]
    assert isinstance(metadata, dict) and isinstance(spec, dict)
    checks = spec["checks"]
    artifacts = spec["artifacts"]
    assert isinstance(checks, list) and isinstance(artifacts, list)
    release = spec["release"]
    policy = spec["policy"]
    environment = spec["environment"]
    assert (
        isinstance(release, dict)
        and isinstance(policy, dict)
        and isinstance(environment, dict)
    )
    if metadata["sourceRevision"] != release["revision"]:
        raise ReleaseSignatureError("release-signature.report.invalid")
    if tuple(check["id"] for check in checks) != CHECK_IDS:
        raise ReleaseSignatureError("release-signature.report.invalid")
    if tuple(artifact["role"] for artifact in artifacts) != ROLES:
        raise ReleaseSignatureError("release-signature.report.invalid")
    expected_signature_passes = tuple(
        artifact["status"] == "verified" for artifact in artifacts
    )
    if tuple(checks[index]["status"] == "passed" for index in (4, 5)) != expected_signature_passes:
        raise ReleaseSignatureError("release-signature.report.invalid")
    if (checks[6]["status"] == "passed") != all(expected_signature_passes):
        raise ReleaseSignatureError("release-signature.report.invalid")
    if (checks[3]["status"] == "passed") != (
        policy["cosignVersion"] == environment["cosignVersion"]
    ):
        raise ReleaseSignatureError("release-signature.report.invalid")
    failed = sum(check["status"] == "failed" for check in checks)
    not_run = sum(check["status"] == "not-run" for check in checks)
    expected_status = (
        "rejected"
        if failed
        else "local-only"
        if spec["qualificationLevel"] == "local-public-key-v1"
        else "verified"
    )
    if spec["qualificationLevel"] == "sigstore-keyless-v1":
        expected_transparency_pass = all(expected_signature_passes)
        if (
            (checks[7]["status"] == "passed") != expected_transparency_pass
            or not_run
        ):
            raise ReleaseSignatureError("release-signature.report.invalid")
    elif checks[7] != {
        "id": "transparency-verification",
        "status": "not-run",
        "errorCode": "release-signature.transparency.local-only",
    }:
        raise ReleaseSignatureError("release-signature.report.invalid")
    expected_summary = {
        "totalChecks": len(CHECK_IDS),
        "passedChecks": len(CHECK_IDS) - failed - not_run,
        "failedChecks": failed,
        "notRunChecks": not_run,
        "overallStatus": expected_status,
    }
    if spec["status"] != expected_status or spec["summary"] != expected_summary:
        raise ReleaseSignatureError("release-signature.report.invalid")
    if metadata["id"] != report_id(report):
        raise ReleaseSignatureError("release-signature.report.invalid")


def source_identity() -> tuple[str, bool]:
    try:
        revision = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ("git", "status", "--porcelain", "--untracked-files=normal"),
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=10,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        raise ReleaseSignatureError("release-signature.source.unavailable") from None
    return revision, dirty


def qualify(
    *,
    bundle: Path,
    policy_path: Path,
    output: Path,
    cosign: str,
    require_clean: bool,
    require_promotable: bool,
) -> dict[str, object]:
    try:
        manifest = release_bundle.verify_bundle(bundle.expanduser().resolve())
    except release_bundle.ReleaseBundleError:
        raise ReleaseSignatureError("release-signature.bundle.invalid") from None
    manifest_path = bundle.expanduser().resolve() / "release-manifest.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError:
        raise ReleaseSignatureError("release-signature.bundle.invalid") from None
    policy = load_policy(policy_path.expanduser().resolve(), promotion=require_promotable)
    revision, dirty = source_identity()
    if require_clean and dirty:
        raise ReleaseSignatureError("release-signature.source.dirty")
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    report = build_report(
        manifest=manifest,
        manifest_digest="sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
        policy=policy,
        runner=SubprocessCosignRunner(cosign),
        source_revision=revision,
        source_dirty=dirty,
        generated_at=generated_at,
        platform_name=f"{platform.system().lower()}/{platform.machine() or 'unknown'}",
        python_version=platform.python_version(),
    )
    _write_report(output, report)
    if require_promotable and report["spec"]["status"] != "verified":  # type: ignore[index]
        raise ReleaseSignatureError("release-signature.report.not-promotable")
    return report


def verify_report(path: Path, *, require_clean: bool) -> dict[str, object]:
    report = _read_json(path.expanduser().resolve(), "release-signature.report.invalid")
    validate_report_document(report)
    metadata = report["metadata"]
    assert isinstance(metadata, dict)
    if require_clean:
        revision, dirty = source_identity()
        if dirty or metadata["sourceDirty"] or metadata["sourceRevision"] != revision:
            raise ReleaseSignatureError("release-signature.source.mismatch")
    return report


def _write_report(path: Path, report: Mapping[str, object]) -> None:
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        raise ReleaseSignatureError("release-signature.output.invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temporary_path = Path(handle.name)
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, destination)
    except OSError:
        raise ReleaseSignatureError("release-signature.output.invalid") from None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--bundle", type=Path, required=True)
    run.add_argument("--policy", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--cosign", default="cosign")
    run.add_argument("--require-clean", action="store_true")
    run.add_argument("--require-promotable", action="store_true")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--require-clean", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.command == "run":
            report = qualify(
                bundle=arguments.bundle,
                policy_path=arguments.policy,
                output=arguments.output,
                cosign=arguments.cosign,
                require_clean=arguments.require_clean,
                require_promotable=arguments.require_promotable,
            )
        else:
            report = verify_report(
                arguments.report,
                require_clean=arguments.require_clean,
            )
    except ReleaseSignatureError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    label = "qualification" if arguments.command == "run" else "report validation"
    print(f"release signature {label}: {report['spec']['status']}")  # type: ignore[index]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
