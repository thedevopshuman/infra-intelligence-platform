from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import assess_customer_pilot_readiness as pilot


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples"
REVISION = "a" * 40
MANIFEST = "sha256:" + "1" * 64
CONTROL_IMAGE = "sha256:" + "a" * 64
BRIDGE_IMAGE = "sha256:" + "b" * 64
POLICY = "sha256:" + "c" * 64
CLUSTER = "sha256:" + "d" * 64
ENVIRONMENT = "sha256:" + "e" * 64
TARGET = "sha256:" + "f" * 64
OTLP_TARGET = "sha256:" + "0" * 64
SUSTAINED_PROFILE = "sha256:" + "9" * 64


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _profile(now: datetime, targets: list[dict[str, str]]) -> dict:
    metadata = {
        "reviewedAt": _stamp(now - timedelta(hours=2)),
        "validUntil": _stamp(now + timedelta(days=2)),
    }
    spec = {
        "qualificationLevel": pilot.QUALIFICATION_LEVEL,
        "release": {
            "applicationVersion": "0.84.0",
            "chartVersion": "0.87.0",
            "sourceRevision": REVISION,
            "manifestDigest": MANIFEST,
            "controlPlaneImageDigest": CONTROL_IMAGE,
            "pluginMediationBridgeImageDigest": BRIDGE_IMAGE,
        },
        "bindings": {
            "signaturePolicyDigest": POLICY,
            "publicationTargetSetDigest": pilot._digest(targets),
            "clusterBindingDigest": CLUSTER,
            "environmentBindingDigest": ENVIRONMENT,
            "controlPlaneTargetDigest": TARGET,
            "otlpTargetDigest": OTLP_TARGET,
            "sustainedWorkloadProfileDigest": SUSTAINED_PROFILE,
        },
        "objective": {
            "maximumProfileAgeSeconds": 604800,
            "maximumFoundationEvidenceAgeSeconds": 2592000,
            "maximumCustomerEvidenceAgeSeconds": 86400,
            "maximumClockSkewSeconds": 300,
            "reportValiditySeconds": 86400,
        },
    }
    metadata["id"] = pilot._profile_id(metadata, spec)
    return {
        "apiVersion": pilot.API_VERSION,
        "kind": pilot.PROFILE_KIND,
        "metadata": metadata,
        "spec": spec,
    }


def _sources(now: datetime) -> tuple[dict, dict[str, pilot.EvidenceDocument]]:
    foundation = now - timedelta(hours=4)
    deployment_at = now - timedelta(hours=1)
    load_started = now - timedelta(minutes=50)
    load_completed = now - timedelta(minutes=45)
    sustained_started = now - timedelta(minutes=40)
    sustained_completed = now - timedelta(minutes=25)
    prerequisite_at = now - timedelta(minutes=30)
    flow_at = now - timedelta(minutes=5)
    targets = [
        {
            "role": "control-plane-image",
            "repository": "registry.example.test/iip/control-plane",
            "tagReference": "registry.example.test/iip/control-plane:v0.84.0",
            "immutableReference": f"registry.example.test/iip/control-plane@{CONTROL_IMAGE}",
            "indexDigest": CONTROL_IMAGE,
        },
        {
            "role": "plugin-mediation-bridge-image",
            "repository": "registry.example.test/iip/plugin-bridge",
            "tagReference": "registry.example.test/iip/plugin-bridge:v0.84.0",
            "immutableReference": f"registry.example.test/iip/plugin-bridge@{BRIDGE_IMAGE}",
            "indexDigest": BRIDGE_IMAGE,
        },
    ]
    customer_subject = {
        "deploymentProfile": "production-ai-finops-v0",
        "applicationVersion": "0.84.0",
        "chartVersion": "0.87.0",
        "contractsApiVersion": pilot.API_VERSION,
        "sourceRevision": REVISION,
        "imageDigest": CONTROL_IMAGE,
    }
    deployment_subject = {
        "profile": "production-ai-finops-v0",
        "applicationVersion": "0.84.0",
        "chartVersion": "0.87.0",
        "contractsApiVersion": pilot.API_VERSION,
        "requiredMigration": "0023_ai_model_suitability.sql",
        "sourceRevision": REVISION,
        "imageDigest": CONTROL_IMAGE,
    }
    raw = {
        "release-readiness": {
            "metadata": {
                "id": "rrr_" + "1" * 32,
                "generatedAt": _stamp(foundation),
                "sourceRevision": REVISION,
                "sourceDirty": False,
            },
            "spec": {
                "status": "locally-qualified",
                "release": {
                    "version": "0.84.0",
                    "chartVersion": "0.87.0",
                    "revision": REVISION,
                    "manifestDigest": MANIFEST,
                    "signatureStatus": "unsigned",
                },
            },
        },
        "registry-publication": {
            "metadata": {
                "id": "rpr_" + "2" * 32,
                "generatedAt": _stamp(foundation + timedelta(minutes=5)),
                "sourceRevision": REVISION,
                "sourceDirty": False,
            },
            "spec": {
                "status": "published-unsigned",
                "release": {
                    "version": "0.84.0",
                    "manifestDigest": MANIFEST,
                },
                "targets": targets,
            },
        },
        "organizational-signatures": {
            "metadata": {
                "id": "rsv_" + "3" * 32,
                "generatedAt": _stamp(foundation + timedelta(minutes=10)),
                "sourceRevision": REVISION,
                "sourceDirty": False,
            },
            "spec": {
                "status": "verified",
                "release": {
                    "version": "0.84.0",
                    "revision": REVISION,
                    "manifestDigest": MANIFEST,
                },
                "policy": {"digest": POLICY},
                "artifacts": [
                    {
                        "role": "control-plane-image",
                        "indexDigest": CONTROL_IMAGE,
                    },
                    {
                        "role": "plugin-mediation-bridge-image",
                        "indexDigest": BRIDGE_IMAGE,
                    },
                ],
            },
        },
        "customer-deployment": {
            "metadata": {
                "id": "cdq_" + "4" * 32,
                "generatedAt": _stamp(deployment_at),
                "sourceRevision": REVISION,
                "sourceDirty": False,
            },
            "spec": {
                "status": "qualified",
                "subject": deployment_subject,
                "bindings": {
                    "clusterBindingDigest": CLUSTER,
                    "continuityTargetBindingDigest": TARGET,
                    "processingOtlpTargetBindingDigest": OTLP_TARGET,
                    "otlpReceiverEndpointBindingDigest": OTLP_TARGET,
                },
            },
        },
        "control-plane-load": {
            "metadata": {
                "id": "clq_" + "5" * 32,
                "generatedAt": _stamp(load_completed),
                "sourceRevision": REVISION,
                "sourceDirty": False,
            },
            "spec": {
                "status": "qualified",
                "targetBindingDigest": TARGET,
                "targetIdentity": {
                    "applicationVersion": "0.84.0",
                    "contractsApiVersion": pilot.API_VERSION,
                    "requiredMigration": "0023_ai_model_suitability.sql",
                    "buildMode": "release",
                    "sourceRevision": REVISION,
                    "chartVersion": "0.87.0",
                    "imageDigest": CONTROL_IMAGE,
                },
                "measurements": {
                    "startedAt": _stamp(load_started),
                    "completedAt": _stamp(load_completed),
                },
            },
        },
        "sustained-core-workload": {
            "metadata": {
                "id": "cswq_" + "6" * 32,
                "generatedAt": _stamp(sustained_completed),
                "validUntil": _stamp(now + timedelta(hours=18)),
                "sourceRevision": REVISION,
                "sourceDirty": False,
            },
            "spec": {
                "status": "qualified",
                "subject": {
                    "applicationVersion": "0.84.0",
                    "chartVersion": "0.87.0",
                    "contractsApiVersion": pilot.API_VERSION,
                    "requiredMigration": "0023_ai_model_suitability.sql",
                    "sourceRevision": REVISION,
                    "imageDigest": CONTROL_IMAGE,
                },
                "bindings": {
                    "profileDigest": SUSTAINED_PROFILE,
                    "apiTargetBindingDigest": TARGET,
                    "otlpTargetBindingDigest": OTLP_TARGET,
                },
                "measurements": {
                    "startedAt": _stamp(sustained_started),
                    "completedAt": _stamp(sustained_completed),
                },
            },
        },
        "ai-finops-prerequisites": {
            "metadata": {
                "id": "cafp_" + "7" * 32,
                "generatedAt": _stamp(prerequisite_at),
                "validUntil": _stamp(now + timedelta(days=1)),
                "sourceRevision": REVISION,
                "sourceDirty": False,
            },
            "spec": {
                "status": "prerequisites-ready",
                "subject": customer_subject,
                "bindings": {
                    "environmentBindingDigest": ENVIRONMENT,
                    "releaseReadinessReportDigest": _digest("1"),
                    "customerDeploymentReportDigest": _digest("4"),
                },
            },
        },
        "same-invocation-ai-finops": {
            "metadata": {
                "id": "caff_" + "8" * 32,
                "generatedAt": _stamp(flow_at),
                "validUntil": _stamp(now + timedelta(hours=20)),
                "sourceRevision": REVISION,
                "sourceDirty": False,
            },
            "spec": {
                "status": "qualified",
                "subject": customer_subject,
                "bindings": {
                    "environmentBindingDigest": ENVIRONMENT,
                    "controlPlaneTargetDigest": TARGET,
                    "prerequisiteReportDigest": _digest("7"),
                },
            },
        },
    }
    documents = {
        key: pilot.EvidenceDocument(
            document=value,
            file_digest=_digest(str(index + 1)),
            generated_at=datetime.fromisoformat(
                value["metadata"]["generatedAt"].replace("Z", "+00:00")
            ),
        )
        for index, (key, value) in enumerate(raw.items())
    }
    return _profile(now, targets), documents


class CustomerPilotReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
        self.profile, self.sources = _sources(self.now)

    def test_examples_are_closed_and_semantically_valid(self) -> None:
        profile = json.loads(
            (EXAMPLES / "customer-pilot-readiness-profile.json").read_text()
        )
        report = json.loads(
            (EXAMPLES / "customer-pilot-readiness-report.json").read_text()
        )
        pilot.validate_profile(profile)
        pilot.validate_report_document(report)

    def test_builds_minimized_design_partner_candidate(self) -> None:
        report = pilot.build_report(
            profile=self.profile, sources=self.sources, generated_at=self.now
        )
        self.assertEqual(report["spec"]["status"], "design-partner-candidate")
        self.assertEqual(report["spec"]["summary"]["passedEvidence"], 8)
        self.assertEqual(len(report["spec"]["externalGates"]), 3)
        self.assertEqual(
            report["metadata"]["validUntil"],
            _stamp(self.now + timedelta(hours=18)),
        )
        encoded = json.dumps(report)
        for forbidden in (
            "registry.example.test",
            "tenantId",
            "environmentId",
            "modelId",
            "traceId",
            "spanId",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_unsuccessful_or_stale_evidence_is_not_candidate(self) -> None:
        sources = dict(self.sources)
        signature = copy.deepcopy(sources["organizational-signatures"].document)
        signature["spec"]["status"] = "local-only"
        sources["organizational-signatures"] = pilot.EvidenceDocument(
            signature,
            sources["organizational-signatures"].file_digest,
            sources["organizational-signatures"].generated_at,
        )
        report = pilot.build_report(
            profile=self.profile, sources=sources, generated_at=self.now
        )
        self.assertEqual(report["spec"]["status"], "not-candidate")
        self.assertEqual(report["spec"]["summary"]["rejectedEvidence"], 1)
        freshness = next(
            item
            for item in report["spec"]["checks"]
            if item["id"] == "evidence-freshness"
        )
        self.assertEqual(freshness["status"], "passed")

        stale_profile = copy.deepcopy(self.profile)
        stale_profile["spec"]["objective"]["maximumFoundationEvidenceAgeSeconds"] = 3600
        metadata = stale_profile["metadata"]
        metadata["id"] = pilot._profile_id(metadata, stale_profile["spec"])
        stale = pilot.build_report(
            profile=stale_profile, sources=self.sources, generated_at=self.now
        )
        self.assertEqual(stale["spec"]["status"], "not-candidate")
        self.assertEqual(stale["spec"]["summary"]["rejectedEvidence"], 3)
        freshness = next(
            item
            for item in stale["spec"]["checks"]
            if item["id"] == "evidence-freshness"
        )
        self.assertEqual(freshness["status"], "failed")

    def test_expired_evidence_emits_a_bounded_not_candidate_report(self) -> None:
        assessed = self.now + timedelta(hours=21)
        report = pilot.build_report(
            profile=self.profile,
            sources=self.sources,
            generated_at=assessed,
        )
        self.assertEqual(report["spec"]["status"], "not-candidate")
        flow = next(
            item
            for item in report["spec"]["evidence"]
            if item["id"] == "same-invocation-ai-finops"
        )
        self.assertEqual(
            flow["errorCode"], "customer-pilot-readiness.evidence.expired"
        )
        self.assertGreater(
            datetime.fromisoformat(
                report["metadata"]["validUntil"].replace("Z", "+00:00")
            ),
            assessed,
        )

    def test_unsuccessful_sustained_workload_rejects_the_candidate(self) -> None:
        sources = dict(self.sources)
        sustained = copy.deepcopy(sources["sustained-core-workload"].document)
        sustained["spec"]["status"] = "not-qualified"
        sources["sustained-core-workload"] = pilot.EvidenceDocument(
            sustained,
            sources["sustained-core-workload"].file_digest,
            sources["sustained-core-workload"].generated_at,
        )
        report = pilot.build_report(
            profile=self.profile, sources=sources, generated_at=self.now
        )
        evidence = next(
            item
            for item in report["spec"]["evidence"]
            if item["id"] == "sustained-core-workload"
        )
        check = next(
            item
            for item in report["spec"]["checks"]
            if item["id"] == "sustained-core-workload"
        )
        self.assertEqual(report["spec"]["status"], "not-candidate")
        self.assertEqual(
            evidence["errorCode"],
            "customer-pilot-readiness.evidence.status-not-qualified",
        )
        self.assertEqual(check["status"], "failed")

    def test_dirty_source_is_rejected_and_crossed_revision_fails_closed(self) -> None:
        sources = dict(self.sources)
        publication = copy.deepcopy(sources["registry-publication"].document)
        publication["metadata"]["sourceDirty"] = True
        sources["registry-publication"] = pilot.EvidenceDocument(
            publication,
            sources["registry-publication"].file_digest,
            sources["registry-publication"].generated_at,
        )
        report = pilot.build_report(
            profile=self.profile, sources=sources, generated_at=self.now
        )
        item = report["spec"]["evidence"][1]
        self.assertEqual(item["status"], "rejected")
        self.assertEqual(
            item["errorCode"], "customer-pilot-readiness.evidence.source-dirty"
        )

        crossed = dict(self.sources)
        load = copy.deepcopy(crossed["control-plane-load"].document)
        load["metadata"]["sourceRevision"] = "b" * 40
        crossed["control-plane-load"] = pilot.EvidenceDocument(
            load,
            crossed["control-plane-load"].file_digest,
            crossed["control-plane-load"].generated_at,
        )
        with self.assertRaisesRegex(
            pilot.CustomerPilotReadinessError,
            "customer-pilot-readiness.evidence.crossed",
        ):
            pilot.build_report(
                profile=self.profile, sources=crossed, generated_at=self.now
            )

    def test_crossed_release_or_evidence_chain_fails_closed(self) -> None:
        sources = dict(self.sources)
        signature = copy.deepcopy(sources["organizational-signatures"].document)
        signature["spec"]["artifacts"][0]["indexDigest"] = _digest("9")
        sources["organizational-signatures"] = pilot.EvidenceDocument(
            signature,
            sources["organizational-signatures"].file_digest,
            sources["organizational-signatures"].generated_at,
        )
        with self.assertRaisesRegex(
            pilot.CustomerPilotReadinessError,
            "customer-pilot-readiness.evidence.crossed",
        ):
            pilot.build_report(
                profile=self.profile, sources=sources, generated_at=self.now
            )

        sources = dict(self.sources)
        sustained = copy.deepcopy(sources["sustained-core-workload"].document)
        sustained["spec"]["bindings"]["otlpTargetBindingDigest"] = _digest("8")
        sources["sustained-core-workload"] = pilot.EvidenceDocument(
            sustained,
            sources["sustained-core-workload"].file_digest,
            sources["sustained-core-workload"].generated_at,
        )
        with self.assertRaisesRegex(
            pilot.CustomerPilotReadinessError,
            "customer-pilot-readiness.evidence.crossed",
        ):
            pilot.build_report(
                profile=self.profile, sources=sources, generated_at=self.now
            )

        sources = dict(self.sources)
        prerequisite = copy.deepcopy(sources["ai-finops-prerequisites"].document)
        prerequisite["spec"]["bindings"]["customerDeploymentReportDigest"] = _digest("9")
        sources["ai-finops-prerequisites"] = pilot.EvidenceDocument(
            prerequisite,
            sources["ai-finops-prerequisites"].file_digest,
            sources["ai-finops-prerequisites"].generated_at,
        )
        with self.assertRaisesRegex(
            pilot.CustomerPilotReadinessError,
            "customer-pilot-readiness.evidence.crossed",
        ):
            pilot.build_report(
                profile=self.profile, sources=sources, generated_at=self.now
            )

    def test_load_must_follow_customer_deployment_qualification(self) -> None:
        sources = dict(self.sources)
        load = copy.deepcopy(sources["control-plane-load"].document)
        load["spec"]["measurements"]["startedAt"] = _stamp(
            self.now - timedelta(hours=2)
        )
        sources["control-plane-load"] = pilot.EvidenceDocument(
            load,
            sources["control-plane-load"].file_digest,
            sources["control-plane-load"].generated_at,
        )
        report = pilot.build_report(
            profile=self.profile, sources=sources, generated_at=self.now
        )
        self.assertEqual(report["spec"]["status"], "not-candidate")
        check = next(
            item
            for item in report["spec"]["checks"]
            if item["id"] == "post-deployment-load-window"
        )
        self.assertEqual(check["errorCode"], "customer-pilot-readiness.load.before-deployment")

    def test_sustained_workload_must_follow_deployment_qualification(self) -> None:
        sources = dict(self.sources)
        sustained = copy.deepcopy(sources["sustained-core-workload"].document)
        sustained["spec"]["measurements"]["startedAt"] = _stamp(
            self.now - timedelta(hours=2)
        )
        sources["sustained-core-workload"] = pilot.EvidenceDocument(
            sustained,
            sources["sustained-core-workload"].file_digest,
            sources["sustained-core-workload"].generated_at,
        )
        report = pilot.build_report(
            profile=self.profile, sources=sources, generated_at=self.now
        )
        self.assertEqual(report["spec"]["status"], "not-candidate")
        check = next(
            item
            for item in report["spec"]["checks"]
            if item["id"] == "post-deployment-sustained-workload-window"
        )
        self.assertEqual(
            check["errorCode"],
            "customer-pilot-readiness.sustained-workload.before-deployment",
        )

    def test_report_binding_digests_must_match_evidence_items(self) -> None:
        report = copy.deepcopy(
            pilot.build_report(
                profile=self.profile, sources=self.sources, generated_at=self.now
            )
        )
        report["spec"]["bindings"]["sustainedWorkloadReportDigest"] = _digest("0")
        metadata = dict(report["metadata"])
        metadata.pop("id")
        report["metadata"]["id"] = pilot._report_id(metadata, report["spec"])
        with self.assertRaisesRegex(
            pilot.CustomerPilotReadinessError,
            "customer-pilot-readiness.report.invalid",
        ):
            pilot.validate_report_document(report)

    def test_profile_file_is_owner_only_and_not_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile.json"
            profile.write_text(json.dumps(self.profile))
            os.chmod(profile, 0o644)
            with self.assertRaisesRegex(
                pilot.CustomerPilotReadinessError,
                "customer-pilot-readiness.profile.unreadable",
            ):
                pilot.load_profile(profile)
            os.chmod(profile, 0o400)
            with self.assertRaisesRegex(
                pilot.CustomerPilotReadinessError,
                "customer-pilot-readiness.profile.unreadable",
            ):
                pilot.load_profile(profile)
            os.chmod(profile, 0o600)
            self.assertEqual(pilot.load_profile(profile), self.profile)
            link = root / "profile-link.json"
            link.symlink_to(profile)
            with self.assertRaises(pilot.CustomerPilotReadinessError):
                pilot.load_profile(link)

    def test_verify_rebinds_current_inputs_and_expiry(self) -> None:
        report = pilot.build_report(
            profile=self.profile, sources=self.sources, generated_at=self.now
        )
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            report_path.write_text(json.dumps(report))
            with (
                patch.object(pilot, "_source_identity", return_value=(REVISION, False)),
                patch.object(pilot, "load_profile", return_value=self.profile),
                patch.object(pilot, "_load_sources", return_value=self.sources),
            ):
                verified = pilot.verify(
                    report_path=report_path,
                    profile_path=Path("profile.json"),
                    paths={},
                    require_candidate=True,
                    now=self.now,
                )
                self.assertEqual(verified, report)
                with self.assertRaisesRegex(
                    pilot.CustomerPilotReadinessError,
                    "customer-pilot-readiness.report.expired",
                ):
                    pilot.verify(
                        report_path=report_path,
                        profile_path=Path("profile.json"),
                        paths={},
                        now=datetime.fromisoformat(
                            report["metadata"]["validUntil"].replace("Z", "+00:00")
                        ),
                    )

    def test_report_validator_rejects_rederived_check_tampering(self) -> None:
        report = copy.deepcopy(
            pilot.build_report(
                profile=self.profile, sources=self.sources, generated_at=self.now
            )
        )
        report["spec"]["checks"][2] = {
            "id": "evidence-freshness",
            "status": "failed",
            "errorCode": "customer-pilot-readiness.evidence.not-current",
        }
        report["spec"]["summary"] = pilot._summary(
            report["spec"]["evidence"], report["spec"]["checks"]
        )
        report["spec"]["status"] = report["spec"]["summary"]["overallStatus"]
        metadata = dict(report["metadata"])
        metadata.pop("id")
        report["metadata"]["id"] = pilot._report_id(metadata, report["spec"])
        with self.assertRaisesRegex(
            pilot.CustomerPilotReadinessError,
            "customer-pilot-readiness.report.invalid",
        ):
            pilot.validate_report_document(report)

    def test_paths_cannot_overlap(self) -> None:
        path = Path("same.json")
        with self.assertRaisesRegex(
            pilot.CustomerPilotReadinessError,
            "customer-pilot-readiness.paths.overlap",
        ):
            pilot._ensure_distinct((path, path))

    def test_sdk_exposes_only_profile_and_transport_report(self) -> None:
        from infra_intelligence_sdk import (
            CustomerPilotReadinessProfile,
            CustomerPilotReadinessReport,
        )

        profile = CustomerPilotReadinessProfile.from_dict(self.profile)
        report = CustomerPilotReadinessReport.from_dict(
            pilot.build_report(
                profile=self.profile, sources=self.sources, generated_at=self.now
            )
        )
        self.assertEqual(profile.release["sourceRevision"], REVISION)
        self.assertEqual(report.status, "design-partner-candidate")
        self.assertNotIn("EvidenceDocument", dir(__import__("infra_intelligence_sdk")))


if __name__ == "__main__":
    unittest.main()
