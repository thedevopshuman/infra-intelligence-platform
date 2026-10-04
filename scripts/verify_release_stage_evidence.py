#!/usr/bin/env python3
"""Join saved CI evidence to one clean source revision and verified release bundle.

Expected file hashes must come from the producing job's outputs, not from the
downloaded artifact. This is an operational gate, not a new public contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__:
    from . import release_bundle, release_publication as publication
    from . import release_signature_verification as signatures
    from . import release_vulnerability_qualification as vulnerabilities
else:
    import release_bundle
    import release_publication as publication
    import release_signature_verification as signatures
    import release_vulnerability_qualification as vulnerabilities

ROOT = Path(__file__).resolve().parents[1]
GITHUB_REPOSITORY = "thedevopshuman/infra-intelligence-platform"
REPOSITORIES = {
    "control-plane-image": "docker.io/thedevopshuman/iip",
    "plugin-mediation-bridge-image": "docker.io/thedevopshuman/iip-bridge",
}
MAX_REPORT_BYTES = 4 * 1024 * 1024


class StageEvidenceError(RuntimeError):
    """Stable, non-provider-facing failure code."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise StageEvidenceError("release-stage.evidence." + code)


def trusted_json(path: Path, expected_sha256: str) -> dict:
    """Hash bounded regular files before interpreting their contents."""
    _require(bool(re.fullmatch(r"[0-9a-f]{64}", expected_sha256)), "hash-invalid")
    _require(not path.is_symlink() and path.is_file(), "file-invalid")
    with path.open("rb") as handle:
        raw = handle.read(MAX_REPORT_BYTES + 1)
    _require(len(raw) <= MAX_REPORT_BYTES, "file-invalid")
    _require(hashlib.sha256(raw).hexdigest() == expected_sha256, "hash-mismatch")
    document = json.loads(raw)
    _require(isinstance(document, dict), "file-invalid")
    return document


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(result.tzinfo is not None, "time-invalid")
    return result


def _source_and_time(report: dict, revision: str, now: datetime) -> None:
    metadata = report["metadata"]
    _require(metadata["sourceRevision"] == revision and not metadata["sourceDirty"], "source-mismatch")
    _require(_time(metadata["generatedAt"]) <= now + timedelta(minutes=5), "time-invalid")


def verify_evidence(
    *, bundle: Path, revision: str,
    vulnerability_report: Path, vulnerability_sha256: str,
    publication_report: Path | None = None, publication_sha256: str | None = None,
    signature_policy: Path | None = None, signature_policy_sha256: str | None = None,
    signature_report: Path | None = None, signature_sha256: str | None = None,
    now: datetime | None = None,
) -> None:
    for path, digest in (
        (publication_report, publication_sha256),
        (signature_policy, signature_policy_sha256),
        (signature_report, signature_sha256),
    ):
        _require((path is None) == (digest is None), "arguments-invalid")
    _require((signature_policy is None) == (signature_report is None), "arguments-invalid")
    _require(signature_report is None or publication_report is not None, "arguments-invalid")
    current, dirty = publication.source_identity()
    _require(current == revision and not dirty, "source-mismatch")
    manifest = release_bundle.verify_bundle(bundle)
    _require(manifest["metadata"]["revision"] == revision, "source-mismatch")
    _require(manifest["metadata"]["version"] == publication.project_version(), "source-mismatch")
    now = now or datetime.now(timezone.utc)
    _require(now.tzinfo is not None, "time-invalid")
    identity = {
        "version": manifest["metadata"]["version"], "revision": revision,
        "manifestDigest": "sha256:" + release_bundle.sha256_file(bundle / "release-manifest.json"),
    }
    images = dict(zip(publication.ROLES, (
        manifest["spec"]["image"], manifest["spec"]["pluginMediationBridgeImage"],
    )))
    for image in images.values():
        _require(sorted(p["name"] for p in image["platforms"]) == ["linux/amd64", "linux/arm64"], "coverage-mismatch")

    report = trusted_json(vulnerability_report, vulnerability_sha256)
    vulnerabilities.validate_report_document(report)
    _source_and_time(report, revision, now)
    spec = report["spec"]
    _require(spec["status"] == "qualified" and spec["release"] == identity, "vulnerability-mismatch")
    policy = vulnerabilities.load_policy(ROOT / "contracts/examples/release-vulnerability-policy.json")
    scanner = policy["spec"]["scanner"]
    expected_policy = {
        **policy["metadata"], "digest": vulnerabilities.canonical_digest(policy),
        "scannerVersion": scanner["version"], "scannerImageDigest": scanner["imageDigest"],
        "maximumFindings": policy["spec"]["maximumFindings"],
        "unfixedHandling": policy["spec"]["unfixedHandling"],
        "exceptionCount": len(policy["spec"]["exceptions"]),
    }
    _require(spec["policy"] == expected_policy, "policy-mismatch")
    _require(spec["qualificationLevel"] == policy["spec"]["profile"], "policy-mismatch")
    database = spec["scanner"]["database"]
    maximum_age = policy["spec"]["database"]["maximumAgeHours"]
    updated = _time(database["updatedAt"])
    _require(database["maximumAgeHours"] == maximum_age, "policy-mismatch")
    _require(updated <= now + timedelta(minutes=5) and now - updated <= timedelta(hours=maximum_age), "scan-stale")
    # Re-evaluate expiry on delayed approval/rerun, not just the scan timestamp.
    for exception in policy["spec"]["exceptions"]:
        _require(_time(exception["expiresAt"]) > now, "exception-expired")
    expected_documents = []
    for role, image in images.items():
        for document in vulnerabilities.extract_spdx_documents(
            bundle / image["path"], artifact_role=role, declared_platforms=image["platforms"],
        ):
            expected_documents.append((role, document.platform, document.subject_digest, document.sbom_digest))
    actual_documents = [(d["artifactRole"], d["platform"], d["subjectDigest"], d["sbomDigest"]) for d in spec["documents"]]
    _require(sorted(actual_documents) == sorted(expected_documents), "sbom-mismatch")
    if publication_report is None:
        return
    published = trusted_json(publication_report, publication_sha256)
    publication.validate_report(published)
    _source_and_time(published, revision, now)
    spec = published["spec"]
    _require(spec["release"] == {
        "version": identity["version"], "tag": "v" + identity["version"],
        "manifestDigest": identity["manifestDigest"],
    }, "publication-mismatch")
    _require(spec["status"] == "published-unsigned" and all(c["status"] == "passed" for c in spec["checks"]), "publication-mismatch")
    for target in spec["targets"]:
        role = target["role"]
        _require(target["repository"] == REPOSITORIES[role] and target["indexDigest"] == images[role]["indexDigest"], "publication-mismatch")
    if signature_report is None:
        return
    policy = trusted_json(signature_policy, signature_policy_sha256)
    signatures.validate_policy_document(policy, promotion=True)
    expected_policy = publication.github_signature_policy(
        published, github_repository=GITHUB_REPOSITORY,
        generation=policy["metadata"]["generation"], effective_at=policy["metadata"]["effectiveAt"],
    )
    _require(policy == expected_policy, "signer-mismatch")
    _require(_time(policy["metadata"]["effectiveAt"]) <= now + timedelta(minutes=5), "time-invalid")
    signed = trusted_json(signature_report, signature_sha256)
    signatures.validate_report_document(signed)
    _source_and_time(signed, revision, now)
    spec = signed["spec"]
    _require(spec["release"] == identity and spec["status"] == "verified" and spec["qualificationLevel"] == "sigstore-keyless-v1", "signature-mismatch")
    _require(spec["policy"] == {
        "id": policy["metadata"]["id"], "generation": policy["metadata"]["generation"],
        "digest": signatures.canonical_digest(policy), "cosignVersion": policy["spec"]["cosignVersion"],
    }, "policy-mismatch")
    for artifact in spec["artifacts"]:
        _require(artifact["indexDigest"] == images[artifact["role"]]["indexDigest"] and artifact["trustId"] == "github-release-workflow", "signature-mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--vulnerability-report", type=Path, required=True)
    parser.add_argument("--vulnerability-sha256", required=True)
    for prefix in ("publication", "signature"):
        parser.add_argument(f"--{prefix}-report", type=Path)
        parser.add_argument(f"--{prefix}-sha256")
    parser.add_argument("--signature-policy", type=Path)
    parser.add_argument("--signature-policy-sha256")
    try:
        verify_evidence(**vars(parser.parse_args()))
    except (StageEvidenceError, release_bundle.ReleaseBundleError,
            publication.ReleasePublicationError, signatures.ReleaseSignatureError,
            vulnerabilities.ReleaseVulnerabilityError) as exc:
        print(str(exc))
        return 2
    except (OSError, ValueError, KeyError, TypeError):
        print("release-stage.evidence.invalid")
        return 2
    print("Release stage evidence matches the clean source and bundle; scan is current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
