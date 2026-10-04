from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping
from unittest.mock import patch

import assess_customer_failure_overlap as overlap
from infra_intelligence_sdk import (
    CustomerFailureOverlapProfile,
    CustomerFailureOverlapQualificationReport,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples"
REVISION = "a" * 40
IMAGE = "sha256:" + "a" * 64
CLUSTER = "sha256:" + "1" * 64
CONTEXT = "sha256:" + "2" * 64
NAMESPACE = "sha256:" + "3" * 64
API_TARGET = "sha256:" + "4" * 64
OTLP_TARGET = "sha256:" + "5" * 64
DATABASE_TARGET = "sha256:" + "6" * 64
PROCESSING_PROFILE = "sha256:" + "7" * 64
POSTGRESQL_PROFILE = "sha256:" + "8" * 64


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _release() -> dict:
    return {
        "applicationVersion": "0.84.0",
        "chartVersion": "0.87.0",
        "contractsApiVersion": overlap.API_VERSION,
        "requiredMigration": "0023_ai_model_suitability.sql",
        "sourceRevision": REVISION,
        "imageDigest": IMAGE,
    }


def _sustained_profile() -> dict:
    return json.loads(
        (EXAMPLES / "customer-sustained-workload-profile.json").read_text()
    )


def _source(
    identifier: str,
    report_id: str,
    generated: datetime,
    *,
    status: str = "qualified",
    bindings: dict,
    started: datetime | None = None,
    completed: datetime | None = None,
    valid_until: datetime | None = None,
) -> overlap.EvidenceDocument:
    metadata = {
        "id": report_id,
        "generatedAt": _stamp(generated),
        "sourceRevision": REVISION,
        "sourceDirty": False,
    }
    if valid_until is not None:
        metadata["validUntil"] = _stamp(valid_until)
    subject = _release()
    if identifier == "customer-deployment":
        subject = {"profile": "production-ai-finops-v1", **subject}
    document = {
        "apiVersion": overlap.API_VERSION,
        "kind": next(
            item.kind for item in overlap.REQUIREMENTS if item.identifier == identifier
        ),
        "metadata": metadata,
        "spec": {
            "status": status,
            "subject": subject,
            "bindings": bindings,
        },
    }
    if started is not None and completed is not None:
        document["spec"]["measurements"] = {
            "startedAt": _stamp(started),
            "completedAt": _stamp(completed),
        }
    return overlap.EvidenceDocument(
        document=document,
        file_digest=_digest(report_id[-1]),
        generated_at=generated,
    )


def _fixtures(
    now: datetime,
) -> tuple[dict, dict, str, dict[str, overlap.EvidenceDocument]]:
    sustained_profile = _sustained_profile()
    sustained_profile_digest = overlap._digest(sustained_profile)
    deployment_time = now - timedelta(hours=2)
    sustained_start = now - timedelta(minutes=80)
    sustained_end = now - timedelta(minutes=10)
    sources = {
        "customer-deployment": _source(
            "customer-deployment",
            "cdq_" + "1" * 32,
            deployment_time,
            bindings={
                "clusterBindingDigest": CLUSTER,
                "namespaceBindingDigest": NAMESPACE,
                "continuityTargetBindingDigest": API_TARGET,
                "processingOtlpTargetBindingDigest": OTLP_TARGET,
                "otlpReceiverEndpointBindingDigest": OTLP_TARGET,
                "databaseTargetBindingDigest": DATABASE_TARGET,
                "processingProfileDigest": PROCESSING_PROFILE,
                "databaseProfileDigest": POSTGRESQL_PROFILE,
            },
        ),
        "sustained-core-workload": _source(
            "sustained-core-workload",
            "cswq_" + "2" * 32,
            sustained_end,
            bindings={
                "profileDigest": sustained_profile_digest,
                "apiTargetBindingDigest": API_TARGET,
                "otlpTargetBindingDigest": OTLP_TARGET,
            },
            started=sustained_start,
            completed=sustained_end,
            valid_until=now + timedelta(days=1),
        ),
        "control-plane-continuity": _source(
            "control-plane-continuity",
            "ccq_" + "3" * 32,
            now - timedelta(minutes=65),
            bindings={
                "targetBindingDigest": API_TARGET,
                "kubernetesContextBindingDigest": CONTEXT,
                "namespaceBindingDigest": NAMESPACE,
            },
            started=now - timedelta(minutes=75),
            completed=now - timedelta(minutes=65),
        ),
        "worker-receiver-continuity": _source(
            "worker-receiver-continuity",
            "cpcq_" + "4" * 32,
            now - timedelta(minutes=45),
            bindings={
                "apiTargetBindingDigest": API_TARGET,
                "otlpTargetBindingDigest": OTLP_TARGET,
                "kubernetesContextBindingDigest": CONTEXT,
                "namespaceBindingDigest": NAMESPACE,
                "profileDigest": PROCESSING_PROFILE,
            },
            started=now - timedelta(minutes=64),
            completed=now - timedelta(minutes=45),
        ),
        "postgresql-primary-promotion": _source(
            "postgresql-primary-promotion",
            "cpgq_" + "5" * 32,
            now - timedelta(minutes=20),
            bindings={
                "apiTargetBindingDigest": API_TARGET,
                "otlpTargetBindingDigest": OTLP_TARGET,
                "databaseTargetBindingDigest": DATABASE_TARGET,
                "kubernetesContextBindingDigest": CONTEXT,
                "namespaceBindingDigest": NAMESPACE,
                "profileDigest": POSTGRESQL_PROFILE,
            },
            started=now - timedelta(minutes=44),
            completed=now - timedelta(minutes=20),
        ),
    }
    metadata = {
        "reviewedAt": _stamp(now - timedelta(days=1)),
        "validUntil": _stamp(now + timedelta(days=6)),
    }
    spec = {
        "qualificationLevel": overlap.QUALIFICATION_LEVEL,
        "release": _release(),
        "bindings": {
            "deploymentReportDigest": sources["customer-deployment"].file_digest,
            "sustainedWorkloadProfileDigest": sustained_profile_digest,
            "clusterBindingDigest": CLUSTER,
            "kubernetesContextBindingDigest": CONTEXT,
            "namespaceBindingDigest": NAMESPACE,
            "apiTargetBindingDigest": API_TARGET,
            "otlpTargetBindingDigest": OTLP_TARGET,
            "databaseTargetBindingDigest": DATABASE_TARGET,
            "processingProfileDigest": PROCESSING_PROFILE,
            "postgresqlProfileDigest": POSTGRESQL_PROFILE,
        },
        "review": {
            "basis": "customer-approved-private-pilot-core-proxy",
            "approvalRecordDigest": _digest("9"),
            "approvedScenarios": [
                "api-pod-eviction",
                "workflow-worker-pod-eviction",
                "otlp-receiver-pod-eviction",
                "postgresql-primary-promotion",
            ],
            "dataHandlingReviewed": True,
            "recoveryObjectivesReviewed": True,
        },
        "objective": {
            "minimumPreFailureLoadSeconds": 60,
            "minimumPostFailureLoadSeconds": 60,
            "maximumProfileAgeSeconds": 2592000,
            "maximumEvidenceAgeSeconds": 604800,
            "maximumClockSkewSeconds": 5,
            "reportValiditySeconds": 86400,
        },
    }
    metadata["id"] = overlap._profile_id(metadata, spec)
    profile = {
        "apiVersion": overlap.API_VERSION,
        "kind": overlap.PROFILE_KIND,
        "metadata": metadata,
        "spec": spec,
    }
    return profile, sustained_profile, sustained_profile_digest, sources


class CustomerFailureOverlapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
        (
            self.profile,
            self.sustained_profile,
            self.sustained_profile_digest,
            self.sources,
        ) = _fixtures(self.now)

    def _build(self, **changes: object) -> Mapping[str, object]:
        arguments = {
            "profile": self.profile,
            "sustained_profile": self.sustained_profile,
            "sustained_profile_digest": self.sustained_profile_digest,
            "sources": self.sources,
            "generated_at": self.now,
        }
        arguments.update(changes)
        return overlap.build_report(**arguments)

    def test_examples_are_closed_and_semantically_valid(self) -> None:
        profile = json.loads(
            (EXAMPLES / "customer-failure-overlap-profile.json").read_text()
        )
        report = json.loads(
            (
                EXAMPLES
                / "customer-failure-overlap-qualification-report.json"
            ).read_text()
        )
        overlap.validate_profile(profile)
        overlap.validate_report_document(report)
        self.assertEqual(
            CustomerFailureOverlapProfile.from_dict(profile).release,
            profile["spec"]["release"],
        )
        self.assertEqual(
            CustomerFailureOverlapQualificationReport.from_dict(report).status,
            "qualified",
        )

    def test_builds_minimized_qualified_report(self) -> None:
        report = self._build()
        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(report["spec"]["summary"]["passedEvidence"], 5)
        self.assertEqual(report["spec"]["summary"]["passedChecks"], 21)
        self.assertEqual(report["spec"]["measurements"]["preFailureLoadSeconds"], 300)
        self.assertEqual(report["spec"]["measurements"]["postFailureLoadSeconds"], 600)
        encoded = json.dumps(report)
        for forbidden in ("tenantId", "resourceUid", "apiBaseUrl", "customer.example"):
            self.assertNotIn(forbidden, encoded)

    def test_crossed_release_or_target_fails_closed(self) -> None:
        sources = dict(self.sources)
        crossed = copy.deepcopy(sources["worker-receiver-continuity"].document)
        crossed["spec"]["bindings"]["otlpTargetBindingDigest"] = _digest("f")
        sources["worker-receiver-continuity"] = overlap.EvidenceDocument(
            crossed,
            sources["worker-receiver-continuity"].file_digest,
            sources["worker-receiver-continuity"].generated_at,
        )
        with self.assertRaisesRegex(
            overlap.CustomerFailureOverlapError,
            "customer-failure-overlap.evidence.crossed",
        ):
            self._build(sources=sources)

        sources = dict(self.sources)
        crossed = copy.deepcopy(sources["control-plane-continuity"].document)
        crossed["spec"]["subject"]["sourceRevision"] = "b" * 40
        sources["control-plane-continuity"] = overlap.EvidenceDocument(
            crossed,
            sources["control-plane-continuity"].file_digest,
            sources["control-plane-continuity"].generated_at,
        )
        with self.assertRaises(overlap.CustomerFailureOverlapError):
            self._build(sources=sources)

    def test_failure_qualification_must_be_enclosed_by_sustained_window(self) -> None:
        sources = dict(self.sources)
        late = copy.deepcopy(sources["postgresql-primary-promotion"].document)
        late["spec"]["measurements"]["completedAt"] = _stamp(self.now)
        sources["postgresql-primary-promotion"] = overlap.EvidenceDocument(
            late,
            sources["postgresql-primary-promotion"].file_digest,
            sources["postgresql-primary-promotion"].generated_at,
        )
        report = self._build(sources=sources)
        self.assertEqual(report["spec"]["status"], "not-qualified")
        check = next(
            item
            for item in report["spec"]["checks"]
            if item["id"] == "sustained-failure-enclosure"
        )
        self.assertEqual(check["status"], "failed")

    def test_pre_and_post_failure_load_are_independent_checks(self) -> None:
        profile = copy.deepcopy(self.profile)
        profile["spec"]["objective"]["minimumPreFailureLoadSeconds"] = 600
        profile["metadata"]["id"] = overlap._profile_id(
            profile["metadata"], profile["spec"]
        )
        report = self._build(profile=profile)
        self.assertEqual(report["spec"]["status"], "not-qualified")
        failed = {
            item["id"] for item in report["spec"]["checks"] if item["status"] == "failed"
        }
        self.assertEqual(failed, {"pre-failure-load-window"})

    def test_stale_and_unsuccessful_inputs_are_explicit(self) -> None:
        sources = dict(self.sources)
        deployment = sources["customer-deployment"]
        sources["customer-deployment"] = overlap.EvidenceDocument(
            deployment.document,
            deployment.file_digest,
            self.now - timedelta(days=8),
        )
        sources["customer-deployment"].document["metadata"]["generatedAt"] = _stamp(
            self.now - timedelta(days=8)
        )
        profile = copy.deepcopy(self.profile)
        profile["spec"]["bindings"]["deploymentReportDigest"] = deployment.file_digest
        profile["metadata"]["id"] = overlap._profile_id(
            profile["metadata"], profile["spec"]
        )
        report = self._build(profile=profile, sources=sources)
        self.assertEqual(report["spec"]["status"], "not-qualified")
        evidence = report["spec"]["evidence"][0]
        self.assertEqual(
            evidence["errorCode"], "customer-failure-overlap.evidence.stale"
        )

        sources = dict(self.sources)
        failed = copy.deepcopy(sources["control-plane-continuity"].document)
        failed["spec"]["status"] = "not-qualified"
        sources["control-plane-continuity"] = overlap.EvidenceDocument(
            failed,
            sources["control-plane-continuity"].file_digest,
            sources["control-plane-continuity"].generated_at,
        )
        report = self._build(sources=sources)
        self.assertEqual(report["spec"]["status"], "not-qualified")
        self.assertEqual(
            report["spec"]["evidence"][2]["errorCode"],
            "customer-failure-overlap.evidence.status-not-qualified",
        )

    def test_report_binding_and_measurement_tampering_is_rejected(self) -> None:
        report = copy.deepcopy(self._build())
        report["spec"]["bindings"]["processingContinuityReportDigest"] = _digest("0")
        metadata = dict(report["metadata"])
        metadata.pop("id")
        report["metadata"]["id"] = overlap._report_id(metadata, report["spec"])
        with self.assertRaises(overlap.CustomerFailureOverlapError):
            overlap.validate_report_document(report)

        report = copy.deepcopy(self._build())
        report["spec"]["measurements"]["postFailureLoadSeconds"] += 1
        metadata = dict(report["metadata"])
        metadata.pop("id")
        report["metadata"]["id"] = overlap._report_id(metadata, report["spec"])
        with self.assertRaises(overlap.CustomerFailureOverlapError):
            overlap.validate_report_document(report)

    def test_profile_is_owner_only_and_non_symlinked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps(self.profile))
            os.chmod(profile_path, 0o644)
            with self.assertRaises(overlap.CustomerFailureOverlapError):
                overlap.load_profile(profile_path)
            os.chmod(profile_path, 0o600)
            self.assertEqual(overlap.load_profile(profile_path), self.profile)
            link = root / "profile-link.json"
            link.symlink_to(profile_path)
            with self.assertRaises(overlap.CustomerFailureOverlapError):
                overlap.load_profile(link)

    def test_verify_rebuilds_all_exact_inputs_and_expiry(self) -> None:
        report = self._build()
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            report_path.write_text(json.dumps(report))
            with (
                patch.object(overlap, "load_profile", return_value=self.profile),
                patch.object(
                    overlap,
                    "_load_sustained_profile",
                    return_value=(
                        self.sustained_profile,
                        self.sustained_profile_digest,
                    ),
                ),
                patch.object(overlap, "_load_sources", return_value=self.sources),
                patch.object(overlap, "_source_identity", return_value=(REVISION, False)),
            ):
                verified = overlap.verify(
                    report_path=report_path,
                    profile_path=Path("profile.json"),
                    sustained_profile_path=Path("sustained.json"),
                    paths={},
                    now=self.now,
                )
                self.assertEqual(verified, report)
                with self.assertRaisesRegex(
                    overlap.CustomerFailureOverlapError,
                    "customer-failure-overlap.report.expired",
                ):
                    overlap.verify(
                        report_path=report_path,
                        profile_path=Path("profile.json"),
                        sustained_profile_path=Path("sustained.json"),
                        paths={},
                        now=self.now + timedelta(days=2),
                    )


if __name__ == "__main__":
    unittest.main()
