from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SDK = ROOT / "sdks" / "python" / "src"
for entry in (str(SCRIPTS), str(SDK)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import qualify_customer_ai_finops as qualification  # noqa: E402
from infra_intelligence_sdk import (  # noqa: E402
    CustomerAiFinopsPrerequisiteProfile,
    CustomerAiFinopsPrerequisiteReport,
)


REVISION = "a" * 40
IMAGE = "sha256:" + "a" * 64
NOW = datetime(2026, 9, 8, 10, 10, tzinfo=timezone.utc)


def _example(name: str) -> dict[str, object]:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def _find(items: list[dict[str, object]], identifier: str) -> dict[str, object]:
    return next(item for item in items if item["id"] == identifier)


class CustomerAiFinopsPrerequisiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = _example("customer-ai-finops-prerequisite-profile.json")
        self.documents = self._documents()
        self.sources = self._sources(self.documents)

    def _documents(self) -> dict[str, dict[str, object]]:
        readiness = _example("release-readiness-report.json")
        runtime = _example("ai-finops-runtime-compatibility-report.json")
        deployment = _example("customer-deployment-qualification-report.json")
        receiver = _example("customer-otlp-receiver-qualification-report.json")
        bedrock = _example("customer-bedrock-qualification-report.json")
        price = _example("ai-price-catalog-qualification-report.json")

        readiness["metadata"]["generatedAt"] = "2026-09-08T10:06:00Z"
        runtime["metadata"].update(
            generatedAt="2026-09-08T10:05:00Z",
            sourceRevision=REVISION,
            sourceDirty=False,
        )
        deployment["metadata"].update(
            generatedAt="2026-09-08T10:05:00Z",
            sourceRevision=REVISION,
            sourceDirty=False,
        )
        deployment["spec"]["subject"].update(
            profile="production-ai-finops-v0",
            applicationVersion="0.84.0",
            chartVersion="0.87.0",
            sourceRevision=REVISION,
            imageDigest=IMAGE,
        )
        receiver["metadata"].update(
            generatedAt="2026-09-08T10:00:03Z",
            sourceRevision=REVISION,
            sourceDirty=False,
        )
        receiver["spec"]["subject"].update(
            applicationVersion="0.84.0",
            chartVersion="0.87.0",
            sourceRevision=REVISION,
            imageDigest=IMAGE,
        )
        bedrock["metadata"].update(
            generatedAt="2026-09-08T09:05:00Z",
            validUntil="2026-09-09T09:05:00Z",
            sourceRevision=REVISION,
            sourceDirty=False,
        )
        bedrock["spec"]["subject"].update(
            applicationVersion="0.84.0",
            sourceRevision=REVISION,
            imageDigest=IMAGE,
        )
        price["metadata"].update(
            tenantId="tenant-example",
            generatedAt="2026-09-08T09:50:00Z",
            validUntil="2026-09-09T09:50:00Z",
        )
        price["spec"].update(qualificationLevel="production-catalog")
        price["spec"]["catalog"].update(
            id="apc_11111111111111111111111111111111",
            version="2026-09-08.1",
            documentDigest="sha256:" + "1" * 64,
            sourceKind="provider-published",
            publishedAt="2026-09-08T09:30:00Z",
            retrievedAt="2026-09-08T09:35:00Z",
        )
        price["spec"]["policy"].update(
            id="apqp_22222222222222222222222222222222",
            version="2026-09-08.1",
        )
        return {
            "release-readiness": readiness,
            "local-ai-finops-runtime": runtime,
            "customer-deployment": deployment,
            "customer-otlp-receiver": receiver,
            "customer-bedrock": bedrock,
            "production-price-catalog": price,
        }

    def _sources(
        self, documents: dict[str, dict[str, object]]
    ) -> dict[str, qualification.EvidenceDocument]:
        runtime = qualification.evidence_from_document(
            documents["local-ai-finops-runtime"]
        )
        readiness_evidence = documents["release-readiness"]["spec"]["evidence"]
        _find(readiness_evidence, "ai-finops-runtime")["reportDigest"] = runtime.digest
        receiver = qualification.evidence_from_document(
            documents["customer-otlp-receiver"]
        )
        deployment_spec = documents["customer-deployment"]["spec"]
        deployment_item = _find(
            deployment_spec["evidence"], "customer-otlp-receiver"
        )
        deployment_item["reportId"] = documents["customer-otlp-receiver"][
            "metadata"
        ]["id"]
        deployment_item["reportDigest"] = receiver.digest
        pairs = (
            ("otlpReceiverApiTargetBindingDigest", "apiTargetBindingDigest"),
            ("otlpReceiverEndpointBindingDigest", "receiverEndpointBindingDigest"),
            ("otlpReceiverProfileDigest", "profileDigest"),
            ("otlpReceiverSignalSetDigest", "signalSetDigest"),
            ("otlpReceiverApiCaBundleDigest", "apiCaBundleDigest"),
            ("otlpReceiverCaBundleDigest", "receiverCaBundleDigest"),
            ("otlpReceiverClientCertificateDigest", "clientCertificateDigest"),
        )
        for deployment_key, receiver_key in pairs:
            deployment_spec["bindings"][deployment_key] = documents[
                "customer-otlp-receiver"
            ]["spec"]["bindings"][receiver_key]
        return {
            "release-readiness": qualification.evidence_from_document(
                documents["release-readiness"]
            ),
            "local-ai-finops-runtime": runtime,
            "customer-deployment": qualification.evidence_from_document(
                documents["customer-deployment"]
            ),
            "customer-otlp-receiver": receiver,
            "customer-bedrock": qualification.evidence_from_document(
                documents["customer-bedrock"]
            ),
            "production-price-catalog": qualification.evidence_from_document(
                documents["production-price-catalog"]
            ),
        }

    def _build(self) -> dict[str, object]:
        return qualification.build_report(
            profile=self.profile,
            sources=self.sources,
            generated_at=NOW,
        )

    def test_complete_prerequisites_are_ready_and_sdk_readable(self) -> None:
        report = self._build()

        self.assertEqual(report["spec"]["status"], "prerequisites-ready")
        self.assertEqual(report["spec"]["summary"]["passedEvidence"], 6)
        self.assertEqual(report["spec"]["summary"]["passedChecks"], 16)
        self.assertEqual(
            report["metadata"]["validUntil"], "2026-09-09T09:05:00Z"
        )
        self.assertIn(
            "same-live-invocation-end-to-end-path-not-qualified",
            report["spec"]["limitations"],
        )
        self.assertEqual(
            CustomerAiFinopsPrerequisiteProfile.from_dict(self.profile).to_dict(),
            self.profile,
        )
        self.assertEqual(
            CustomerAiFinopsPrerequisiteReport.from_dict(report).to_dict(), report
        )

    def test_report_retains_no_customer_or_provider_target_values(self) -> None:
        report = self._build()
        serialized = json.dumps(report, sort_keys=True)

        self.assertNotIn(self.profile["metadata"]["environmentId"], serialized)
        self.assertNotIn(self.profile["spec"]["pricing"]["tenantId"], serialized)
        self.assertNotIn("amazon.nova-lite-v1:0", serialized)
        self.assertNotIn("us-east-1", serialized)
        self.assertNotIn("credentialsProfile", serialized)

    def test_release_image_mismatch_fails_cross_binding(self) -> None:
        documents = copy.deepcopy(self.documents)
        documents["customer-bedrock"]["spec"]["subject"]["imageDigest"] = (
            "sha256:" + "b" * 64
        )
        sources = self._sources(documents)

        report = qualification.build_report(
            profile=self.profile, sources=sources, generated_at=NOW
        )
        checks = {item["id"]: item for item in report["spec"]["checks"]}

        self.assertEqual(report["spec"]["status"], "not-ready")
        self.assertEqual(checks["exact-release-identity"]["status"], "failed")

    def test_receiver_report_must_be_the_deployment_report_input(self) -> None:
        documents = copy.deepcopy(self.documents)
        sources = self._sources(documents)
        deployment = copy.deepcopy(documents["customer-deployment"])
        item = _find(deployment["spec"]["evidence"], "customer-otlp-receiver")
        item["reportDigest"] = "sha256:" + "f" * 64
        sources["customer-deployment"] = qualification.evidence_from_document(
            deployment
        )

        report = qualification.build_report(
            profile=self.profile, sources=sources, generated_at=NOW
        )
        checks = {item["id"]: item for item in report["spec"]["checks"]}

        self.assertEqual(checks["receiver-deployment-chain"]["status"], "failed")
        self.assertEqual(report["spec"]["status"], "not-ready")

    def test_runtime_report_must_be_the_release_readiness_input(self) -> None:
        documents = copy.deepcopy(self.documents)
        sources = self._sources(documents)
        readiness = copy.deepcopy(documents["release-readiness"])
        item = _find(readiness["spec"]["evidence"], "ai-finops-runtime")
        item["reportDigest"] = "sha256:" + "f" * 64
        sources["release-readiness"] = qualification.evidence_from_document(
            readiness
        )

        report = qualification.build_report(
            profile=self.profile, sources=sources, generated_at=NOW
        )
        checks = {item["id"]: item for item in report["spec"]["checks"]}

        self.assertEqual(checks["runtime-readiness-chain"]["status"], "failed")
        self.assertEqual(report["spec"]["status"], "not-ready")

    def test_test_fixture_or_crossed_catalog_is_not_ready(self) -> None:
        documents = copy.deepcopy(self.documents)
        documents["production-price-catalog"]["spec"]["catalog"][
            "sourceKind"
        ] = "test-fixture"
        sources = self._sources(documents)

        report = qualification.build_report(
            profile=self.profile, sources=sources, generated_at=NOW
        )
        checks = {item["id"]: item for item in report["spec"]["checks"]}

        self.assertEqual(checks["catalog-profile-binding"]["status"], "failed")
        self.assertEqual(report["spec"]["status"], "not-ready")

    def test_stale_and_missing_evidence_are_explicit(self) -> None:
        documents = copy.deepcopy(self.documents)
        documents["local-ai-finops-runtime"]["metadata"]["generatedAt"] = (
            "2026-09-01T00:00:00Z"
        )
        sources = self._sources(documents)
        sources["customer-bedrock"] = None

        report = qualification.build_report(
            profile=self.profile, sources=sources, generated_at=NOW
        )
        evidence = {item["id"]: item for item in report["spec"]["evidence"]}

        self.assertEqual(evidence["local-ai-finops-runtime"]["status"], "rejected")
        self.assertEqual(evidence["customer-bedrock"]["status"], "missing")
        self.assertEqual(report["spec"]["summary"]["rejectedEvidence"], 1)
        self.assertEqual(report["spec"]["summary"]["missingEvidence"], 1)

    def test_semantic_validator_rejects_identity_tampering(self) -> None:
        report = self._build()
        report["metadata"]["id"] = "cafp_" + "f" * 32

        with self.assertRaisesRegex(
            qualification.CustomerAiFinopsPrerequisiteError,
            "customer-ai-finops-prerequisite.report.id-invalid",
        ):
            qualification.validate_report_document(report)

    def test_naive_qualification_time_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            qualification.CustomerAiFinopsPrerequisiteError,
            "customer-ai-finops-prerequisite.time.invalid",
        ):
            qualification.build_report(
                profile=self.profile,
                sources=self.sources,
                generated_at=datetime(2026, 9, 8, 10, 10),
            )

    def test_verifier_rebinds_every_input_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            profile_path = directory / "profile.json"
            profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
            os.chmod(profile_path, 0o600)
            paths: dict[str, Path] = {}
            for identifier, document in self.documents.items():
                path = directory / f"{identifier}.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                paths[identifier] = path
            # Rebuild embedded raw-file digests after writing the two source reports.
            runtime_source = qualification.load_evidence(paths["local-ai-finops-runtime"])
            receiver_source = qualification.load_evidence(paths["customer-otlp-receiver"])
            readiness = json.loads(paths["release-readiness"].read_text())
            _find(readiness["spec"]["evidence"], "ai-finops-runtime")[
                "reportDigest"
            ] = runtime_source.digest
            paths["release-readiness"].write_text(json.dumps(readiness), encoding="utf-8")
            deployment = json.loads(paths["customer-deployment"].read_text())
            _find(deployment["spec"]["evidence"], "customer-otlp-receiver")[
                "reportDigest"
            ] = receiver_source.digest
            paths["customer-deployment"].write_text(json.dumps(deployment), encoding="utf-8")
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, False)
            ):
                report = qualification.qualify(
                    profile_path=profile_path, paths=paths, now=NOW
                )
                report_path = directory / "report.json"
                qualification._write(report_path, report)
                qualification.verify(
                    report_path=report_path,
                    profile_path=profile_path,
                    paths=paths,
                    require_ready=True,
                    now=NOW,
                )
                changed = json.loads(paths["production-price-catalog"].read_text())
                changed["spec"]["catalog"]["currency"] = "EUR"
                paths["production-price-catalog"].write_text(
                    json.dumps(changed), encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    qualification.CustomerAiFinopsPrerequisiteError,
                    "customer-ai-finops-prerequisite.report.evidence-mismatch",
                ):
                    qualification.verify(
                        report_path=report_path,
                        profile_path=profile_path,
                        paths=paths,
                        now=NOW,
                    )


if __name__ == "__main__":
    unittest.main()
