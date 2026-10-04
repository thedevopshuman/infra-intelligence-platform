from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_release_stage_evidence as stage


class StageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bundle = Path(self.temp.name)
        self.manifest = json.loads((ROOT / "contracts/examples/release-manifest.json").read_text())
        self.revision = self.manifest["metadata"]["revision"]
        self.version = self.manifest["metadata"]["version"]
        self.now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
        self.generated = "2026-10-05T12:00:00Z"
        raw = json.dumps(self.manifest).encode()
        (self.bundle / "release-manifest.json").write_bytes(raw)
        self.digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        self.documents = []
        for role, key in zip(stage.publication.ROLES, ("image", "pluginMediationBridgeImage")):
            for platform in self.manifest["spec"][key]["platforms"]:
                self.documents.append(stage.vulnerabilities.SbomDocument(
                    artifact_role=role, platform=platform["name"],
                    subject_digest=platform["manifestDigest"],
                    sbom_digest="sha256:" + "8" * 64, document={},
                ))
        policy = stage.vulnerabilities.load_policy(ROOT / "contracts/examples/release-vulnerability-policy.json")
        self.vulnerability = stage.vulnerabilities.build_report(
            manifest=self.manifest, manifest_digest=self.digest, policy=policy,
            documents=self.documents, scan_results=[{"SchemaVersion": 2, "Results": []}] * 4,
            scanner_version=policy["spec"]["scanner"]["version"], docker_version="28.1.1",
            database={"Version": 2, "UpdatedAt": "2026-10-05T11:00:00Z", "NextUpdate": "2026-10-05T17:00:00Z"},
            source_revision=self.revision, source_dirty=False, generated_at=self.generated,
            platform_name="linux/amd64", python_version="3.12.10",
        )
        self.published = stage.publication.build_report(
            version=self.version, tag="v" + self.version, manifest_digest=self.digest,
            source_revision=self.revision, source_dirty=False, repositories=stage.REPOSITORIES,
            index_digests={role: self.manifest["spec"][key]["indexDigest"] for role, key in zip(stage.publication.ROLES, ("image", "pluginMediationBridgeImage"))},
            buildx_version="0.37.2", generated_at=self.generated, platform_name="linux/amd64", python_version="3.12.10",
        )
        self.policy = stage.publication.github_signature_policy(
            self.published, github_repository=stage.GITHUB_REPOSITORY, generation=1, effective_at=self.generated,
        )
        runner = SimpleNamespace(
            version=lambda: self.policy["spec"]["cosignVersion"],
            verify=lambda **_: stage.signatures.VerifiedSignature("github-release-workflow", 1),
        )
        self.signed = stage.signatures.build_report(
            manifest=self.manifest, manifest_digest=self.digest, policy=self.policy, runner=runner,
            source_revision=self.revision, source_dirty=False, generated_at=self.generated,
            platform_name="linux/amd64", python_version="3.12.10",
        )
        for target, value in (
            ("publication.source_identity", (self.revision, False)),
            ("publication.project_version", self.version),
            ("release_bundle.verify_bundle", self.manifest),
        ):
            self.enterContext(patch.object(getattr(stage, target.split(".")[0]), target.split(".")[1], return_value=value))
        self.enterContext(patch.object(stage.vulnerabilities, "extract_spdx_documents", side_effect=lambda _path, artifact_role, declared_platforms: [d for d in self.documents if d.artifact_role == artifact_role]))

    def save(self, name, value):
        path = self.bundle / (name + ".json")
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        return path, hashlib.sha256(raw).hexdigest()

    def arguments(self, level=3):
        args = {"bundle": self.bundle, "revision": self.revision, "now": self.now}
        for prefix, value in (("vulnerability", self.vulnerability), ("publication", self.published), ("signature", self.signed))[:level]:
            args[prefix + "_report"], args[prefix + "_sha256"] = self.save(prefix, value)
        if level == 3:
            args["signature_policy"], args["signature_policy_sha256"] = self.save("policy", self.policy)
        return args

    def test_all_saved_stage_levels_accept_bound_evidence(self):
        for level in (1, 2, 3):
            with self.subTest(level=level):
                stage.verify_evidence(**self.arguments(level))

    def test_every_external_digest_is_required_and_checked(self):
        for key in ("vulnerability_sha256", "publication_sha256", "signature_sha256", "signature_policy_sha256"):
            args = self.arguments()
            args[key] = "0" * 64
            with self.subTest(key=key), self.assertRaisesRegex(stage.StageEvidenceError, "hash-mismatch"):
                stage.verify_evidence(**args)

    def test_hash_is_checked_before_json_parse(self):
        path = self.bundle / "bad.json"
        path.write_text("not-json")
        with self.assertRaisesRegex(stage.StageEvidenceError, "hash-mismatch"):
            stage.trusted_json(path, "0" * 64)

    def test_symlink_and_oversized_report_rejected(self):
        path, digest = self.save("report", {})
        link = self.bundle / "link"
        link.symlink_to(path)
        with self.assertRaisesRegex(stage.StageEvidenceError, "file-invalid"):
            stage.trusted_json(link, digest)
        with patch.object(stage, "MAX_REPORT_BYTES", 1), self.assertRaisesRegex(stage.StageEvidenceError, "file-invalid"):
            stage.trusted_json(path, digest)

    def test_delayed_publication_must_refresh_scan(self):
        args = self.arguments()
        args["now"] += timedelta(days=1)
        with self.assertRaisesRegex(stage.StageEvidenceError, "scan-stale"):
            stage.verify_evidence(**args)

    def test_source_revision_and_clean_checkout_required(self):
        for value in ((self.revision, True), ("f" * 40, False)):
            with patch.object(stage.publication, "source_identity", return_value=value), self.assertRaisesRegex(stage.StageEvidenceError, "source-mismatch"):
                stage.verify_evidence(**self.arguments())

    def test_current_project_version_required(self):
        with patch.object(stage.publication, "project_version", return_value="99.0.0"), self.assertRaisesRegex(stage.StageEvidenceError, "source-mismatch"):
            stage.verify_evidence(**self.arguments())

    def test_schema_valid_wrong_report_manifest_rejected(self):
        for report, module in ((self.vulnerability, stage.vulnerabilities), (self.published, stage.publication), (self.signed, stage.signatures)):
            original = deepcopy(report)
            report["spec"]["release"]["manifestDigest"] = "sha256:" + "0" * 64
            report["metadata"]["id"] = module.report_id(report)
            with self.assertRaises(stage.StageEvidenceError):
                stage.verify_evidence(**self.arguments())
            report.clear()
            report.update(original)

    def test_schema_valid_wrong_sbom_or_subject_rejected(self):
        for key in ("sbomDigest", "subjectDigest"):
            original = deepcopy(self.vulnerability)
            self.vulnerability["spec"]["documents"][0][key] = "sha256:" + "0" * 64
            self.vulnerability["metadata"]["id"] = stage.vulnerabilities.report_id(self.vulnerability)
            with self.assertRaisesRegex(stage.StageEvidenceError, "sbom-mismatch"):
                stage.verify_evidence(**self.arguments())
            self.vulnerability = original

    def test_changed_policy_digest_rejected(self):
        self.vulnerability["spec"]["policy"]["digest"] = "sha256:" + "0" * 64
        self.vulnerability["metadata"]["id"] = stage.vulnerabilities.report_id(self.vulnerability)
        with self.assertRaisesRegex(stage.StageEvidenceError, "policy-mismatch"):
            stage.verify_evidence(**self.arguments())

    def test_schema_valid_wrong_registry_rejected(self):
        target = self.published["spec"]["targets"][0]
        target["repository"] = "docker.io/another-owner/iip"
        target["tagReference"] = target["repository"] + ":v" + self.version
        target["immutableReference"] = target["repository"] + "@" + target["indexDigest"]
        self.published["metadata"]["id"] = stage.publication.report_id(self.published)
        with self.assertRaisesRegex(stage.StageEvidenceError, "publication-mismatch"):
            stage.verify_evidence(**self.arguments())

    def test_different_workflow_signer_rejected(self):
        self.policy["spec"]["artifacts"][0]["trust"]["identities"][0]["certificateIdentity"] = "https://github.com/other/repo/.github/workflows/release.yml@refs/tags/v0.84.0"
        with self.assertRaisesRegex(stage.StageEvidenceError, "signer-mismatch"):
            stage.verify_evidence(**self.arguments())

    def test_missing_sha_or_partial_signature_inputs_rejected(self):
        for key in ("publication_sha256", "signature_policy", "signature_report"):
            args = self.arguments()
            del args[key]
            with self.subTest(key=key), self.assertRaisesRegex(stage.StageEvidenceError, "arguments-invalid"):
                stage.verify_evidence(**args)

    def test_missing_architecture_rejected(self):
        self.manifest["spec"]["image"]["platforms"].pop()
        with self.assertRaisesRegex(stage.StageEvidenceError, "coverage-mismatch"):
            stage.verify_evidence(**self.arguments())


if __name__ == "__main__":
    unittest.main()
