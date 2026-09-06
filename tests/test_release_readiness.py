from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SDK = ROOT / "sdks" / "python" / "src"
for entry in (str(SCRIPTS), str(SDK)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import release_readiness  # noqa: E402
from infra_intelligence_sdk import ReleaseReadinessReport  # noqa: E402


REVISION = "a" * 40
CANDIDATE = {
    "version": "0.84.0",
    "chartVersion": "0.87.0",
    "revision": REVISION,
    "manifestDigest": "sha256:" + "b" * 64,
    "signatureStatus": "unsigned",
}


def _assign(document: dict[str, object], path: tuple[str, ...], value: object) -> None:
    current = document
    for part in path[:-1]:
        child = current.setdefault(part, {})
        assert isinstance(child, dict)
        current = child
    current[path[-1]] = value


class ReleaseReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.evidence_dir = self.root / "evidence"
        self.evidence_dir.mkdir()
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.release_report = self.root / "release.json"
        self.vulnerability_report = self.root / "vulnerabilities.json"
        self.paths = release_readiness._paths(
            release_qualification=self.release_report,
            vulnerability_qualification=self.vulnerability_report,
            evidence_dir=self.evidence_dir,
        )
        self.documents: dict[str, dict[str, object]] = {}
        for requirement in release_readiness.REQUIREMENTS:
            document: dict[str, object] = {
                "apiVersion": requirement.api_version,
                "kind": requirement.kind,
                "metadata": {
                    "id": "fixture",
                    "generatedAt": "2026-09-06T15:00:00Z",
                    "sourceRevision": REVISION,
                    "sourceDirty": False,
                },
                "spec": {},
            }
            _assign(document, requirement.status_path, requirement.success_status)
            for field_path, value in requirement.expected_fields:
                _assign(document, field_path, value)
            if requirement.identifier == "packaged-release":
                _assign(
                    document,
                    ("spec", "candidate"),
                    {
                        "version": CANDIDATE["version"],
                        "chartVersion": CANDIDATE["chartVersion"],
                        "revision": REVISION,
                        "releaseManifestDigest": CANDIDATE["manifestDigest"],
                    },
                )
            if requirement.identifier == "sbom-vulnerabilities":
                _assign(
                    document,
                    ("spec", "release"),
                    {
                        "version": CANDIDATE["version"],
                        "revision": REVISION,
                        "manifestDigest": CANDIDATE["manifestDigest"],
                    },
                )
            if requirement.boundary == "configuration-only":
                _assign(
                    document,
                    ("spec", "profile", "applicationVersion"),
                    CANDIDATE["version"],
                )
                _assign(
                    document,
                    ("spec", "profile", "chartVersion"),
                    CANDIDATE["chartVersion"],
                )
            self.documents[requirement.identifier] = document
            self.paths[requirement.identifier].write_text(
                json.dumps(document), encoding="utf-8"
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _assess(self) -> dict[str, object]:
        with patch.object(
            release_readiness, "_candidate", return_value=dict(CANDIDATE)
        ), patch.object(release_readiness, "_schema_valid", return_value=True):
            return release_readiness.assess(
                bundle=self.bundle,
                release_qualification=self.release_report,
                vulnerability_qualification=self.vulnerability_report,
                evidence_dir=self.evidence_dir,
                report_id="rrr_" + "1" * 32,
                generated_at="2026-09-06T15:00:00Z",
            )

    def _rewrite(self, identifier: str, document: dict[str, object]) -> None:
        self.documents[identifier] = document
        self.paths[identifier].write_text(json.dumps(document), encoding="utf-8")

    def test_complete_set_is_locally_qualified_and_sdk_readable(self) -> None:
        report = self._assess()
        release_readiness._validate_report_shape(report)

        self.assertEqual(report["spec"]["status"], "locally-qualified")
        self.assertEqual(report["spec"]["summary"]["passedEvidence"], 18)
        self.assertEqual(len(report["spec"]["externalGates"]), 8)
        self.assertTrue(
            all(
                gate["status"] == "external-required"
                for gate in report["spec"]["externalGates"]
            )
        )
        self.assertEqual(ReleaseReadinessReport.from_dict(report).to_dict(), report)

    def test_missing_document_keeps_report_incomplete(self) -> None:
        self.paths["credential-broker"].unlink()

        report = self._assess()
        release_readiness._validate_report_shape(report)

        self.assertEqual(report["spec"]["status"], "incomplete")
        self.assertEqual(report["spec"]["summary"]["missingEvidence"], 1)
        item = next(
            value
            for value in report["spec"]["evidence"]
            if value["id"] == "credential-broker"
        )
        self.assertEqual(item["status"], "missing")
        self.assertNotIn("reportDigest", item)

    def test_dirty_stale_and_crossed_profile_evidence_are_rejected(self) -> None:
        dirty = copy.deepcopy(self.documents["oidc-issuer"])
        dirty["metadata"]["sourceDirty"] = True
        self._rewrite("oidc-issuer", dirty)
        stale = copy.deepcopy(self.documents["policy-engine"])
        stale["metadata"]["sourceRevision"] = "c" * 40
        self._rewrite("policy-engine", stale)
        crossed = copy.deepcopy(self.documents["bedrock-converse-stream"])
        crossed["spec"]["profile"]["name"] = (
            "otel-python-botocore-converse-iip-usage-v1"
        )
        self._rewrite("bedrock-converse-stream", crossed)

        report = self._assess()
        release_readiness._validate_report_shape(report)
        items = {item["id"]: item for item in report["spec"]["evidence"]}

        self.assertEqual(report["spec"]["summary"]["rejectedEvidence"], 3)
        self.assertEqual(
            items["oidc-issuer"]["errorCode"],
            "release-readiness.evidence.source-dirty",
        )
        self.assertEqual(
            items["policy-engine"]["errorCode"],
            "release-readiness.evidence.revision-mismatch",
        )
        self.assertEqual(
            items["bedrock-converse-stream"]["errorCode"],
            "release-readiness.evidence.profile-mismatch",
        )

    def test_release_manifest_cross_binding_is_required(self) -> None:
        document = copy.deepcopy(self.documents["sbom-vulnerabilities"])
        document["spec"]["release"]["manifestDigest"] = "sha256:" + "d" * 64
        self._rewrite("sbom-vulnerabilities", document)

        report = self._assess()
        item = report["spec"]["evidence"][1]

        self.assertEqual(item["status"], "rejected")
        self.assertEqual(
            item["errorCode"], "release-readiness.evidence.release-mismatch"
        )

    def test_readiness_report_must_remain_outside_the_bundle(self) -> None:
        with self.assertRaisesRegex(
            release_readiness.ReleaseReadinessError,
            "release-readiness.report.inside-bundle",
        ):
            release_readiness._require_report_outside_bundle(
                self.bundle, self.bundle / "readiness.json"
            )

    def test_verifier_recomputes_evidence_digests_and_summary(self) -> None:
        report = self._assess()
        report_path = self.root / "readiness.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        changed = copy.deepcopy(self.documents["github-context"])
        changed["spec"]["status"] = "incompatible"
        self._rewrite("github-context", changed)

        with patch.object(
            release_readiness, "_candidate", return_value=dict(CANDIDATE)
        ), patch.object(release_readiness, "_schema_valid", return_value=True):
            with self.assertRaisesRegex(
                release_readiness.ReleaseReadinessError,
                "release-readiness.report.evidence-mismatch",
            ):
                release_readiness.verify(
                    bundle=self.bundle,
                    report_path=report_path,
                    release_qualification=self.release_report,
                    vulnerability_qualification=self.vulnerability_report,
                    evidence_dir=self.evidence_dir,
                )

    def test_contract_example_validates_with_draft_2020_12(self) -> None:
        schema = json.loads(
            (ROOT / "contracts/schemas/release-readiness-report.schema.json").read_text(
                encoding="utf-8"
            )
        )
        example = json.loads(
            (ROOT / "contracts/examples/release-readiness-report.json").read_text(
                encoding="utf-8"
            )
        )

        errors = list(
            Draft202012Validator(
                schema, format_checker=FormatChecker()
            ).iter_errors(example)
        )
        self.assertEqual(errors, [])

    def test_sdk_rejects_crossed_kind_and_api_version(self) -> None:
        report = self._assess()
        crossed = copy.deepcopy(report)
        crossed["kind"] = "ReleaseQualificationReport"
        with self.assertRaises(ValueError):
            ReleaseReadinessReport.from_dict(crossed)
        crossed = copy.deepcopy(report)
        crossed["apiVersion"] = "iip.platform/v1alpha1"
        with self.assertRaises(ValueError):
            ReleaseReadinessReport.from_dict(crossed)


if __name__ == "__main__":
    unittest.main()
