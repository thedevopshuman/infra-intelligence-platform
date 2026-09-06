from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import deployment_diagnostics as diagnostics  # noqa: E402
import deployment_preflight as preflight  # noqa: E402
import qualify_customer_continuity as continuity  # noqa: E402
import qualify_customer_deployment as qualification  # noqa: E402
import qualify_customer_processing_continuity as processing  # noqa: E402
from infra_intelligence_sdk import CustomerDeploymentQualificationReport  # noqa: E402
from tests.test_customer_continuity_qualification import (  # noqa: E402
    COMPLETED,
    EVICTED,
    IMAGE_DIGEST,
    RECOVERED,
    REPOSITORY,
    REVISION,
    STARTED,
    ingress_report,
    observation,
)
from tests.test_customer_processing_continuity import (  # noqa: E402
    observation as processing_observation,
    phase as processing_phase,
)


CONTEXT = "customer-context"
NAMESPACE = "iip-system"
RELEASE = "iip"
DEPLOYMENT = "iip-infra-intelligence"
WORKER_DEPLOYMENT = "iip-infra-intelligence-worker"
RECEIVER_DEPLOYMENT = "iip-infra-intelligence-otlp-receiver"
PREFLIGHT_AT = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
HEALTH_AT = datetime(2026, 9, 6, 12, 37, tzinfo=timezone.utc)
QUALIFIED_AT = datetime(2026, 9, 6, 12, 38, tzinfo=timezone.utc)
CLUSTER = {
    "kubernetesVersion": "v1.36.1",
    "kubectlVersion": "v1.36.1",
    "clusterBindingDigest": "sha256:" + "9" * 64,
    "namespaceDigest": preflight._digest(NAMESPACE),
}


def _digest(document: object) -> str:
    return "sha256:" + hashlib.sha256(
        (json.dumps(document, indent=2) + "\n").encode("utf-8")
    ).hexdigest()


def live_preflight() -> dict[str, object]:
    report = json.loads(
        (
            ROOT
            / "contracts/examples/customer-deployment-preflight-report.json"
        ).read_text(encoding="utf-8")
    )
    metadata = report["metadata"]
    spec = report["spec"]
    metadata.update(
        {
            "generatedAt": "2026-09-06T12:00:00Z",
            "sourceRevision": REVISION,
            "sourceDirty": False,
        }
    )
    spec["environment"] = {
        "mode": "cluster",
        "platform": "linux/amd64",
        "pythonVersion": "3.12.10",
        "helmVersion": "v4.1.3",
        **CLUSTER,
    }
    dependencies = spec["dependencies"]
    dependencies.update(
        {
            "observedCount": dependencies["configuredCount"],
            "missingCount": 0,
            "invalidCount": 0,
            "unavailableCount": 0,
            "observationDigest": "sha256:" + "8" * 64,
            "verificationStatus": "passed",
        }
    )
    for check in spec["checks"]:
        check["status"] = "passed"
        check.pop("errorCode", None)
    spec["status"] = "install-ready"
    spec["summary"] = {
        "totalChecks": 23,
        "passedChecks": 23,
        "failedChecks": 0,
        "notRunChecks": 0,
        "overallStatus": "install-ready",
    }
    metadata["id"] = preflight._report_identifier(
        source_revision=REVISION,
        source_dirty=False,
        profile=spec["profile"],
        environment=spec["environment"],
        dependencies=spec["dependencies"],
        checks=spec["checks"],
    )
    preflight.validate_report_document(report)
    return report


def healthy_diagnostic() -> dict[str, object]:
    report = json.loads(
        (
            ROOT / "contracts/examples/deployment-diagnostic-report.json"
        ).read_text(encoding="utf-8")
    )
    binding, identity = diagnostics._target(
        context=CONTEXT,
        namespace=NAMESPACE,
        release_name=RELEASE,
        image_digest=IMAGE_DIGEST,
    )
    report["metadata"].update(
        {
            "generatedAt": "2026-09-06T12:37:00Z",
            "sourceRevision": REVISION,
            "sourceDirty": False,
        }
    )
    report["spec"]["targetBindingDigest"] = binding
    report["spec"]["expectedIdentity"] = identity
    report["spec"]["environment"]["observedAt"] = "2026-09-06T12:37:00Z"
    for component in report["spec"]["components"]:
        if component["state"] != "not-observed":
            component["identity"] = identity
            component["identityStatus"] = "match"
    diagnostics.validate_report(report)
    return report


def continuity_inputs() -> tuple[dict[str, object], dict[str, object]]:
    ingress_document = ingress_report()
    continuity_document = continuity.build_report(
        revision=REVISION,
        repository=REPOSITORY,
        image_digest=IMAGE_DIGEST,
        ingress_report=ingress_document,
        ingress_report_digest="sha256:" + "7" * 64,
        context=CONTEXT,
        namespace=NAMESPACE,
        deployment_name=DEPLOYMENT,
        kubernetes_version="v1.36.1",
        before=observation(pod_uid="old-uid"),
        after=observation(pod_uid="new-uid"),
        started_at=STARTED,
        eviction_started_at=EVICTED,
        recovered_at=RECOVERED,
        completed_at=COMPLETED,
        recovery_seconds=5,
        sample_count=721,
        interval_milliseconds=500,
        minimum_window_seconds=300,
        minimum_baseline_seconds=60,
        minimum_post_recovery_seconds=60,
        minimum_availability_basis_points=9990,
        maximum_p95_latency_milliseconds=2000,
        request_timeout_milliseconds=2000,
        maximum_recovery_seconds=120,
    )
    return ingress_document, continuity_document


def processing_report(
    continuity_document: dict[str, object],
) -> dict[str, object]:
    return processing.build_report(
        revision=REVISION,
        repository=REPOSITORY,
        image_digest=IMAGE_DIGEST,
        api_target_digest=continuity_document["spec"]["bindings"][
            "targetBindingDigest"
        ],
        otlp_target_digest="sha256:" + "6" * 64,
        context=CONTEXT,
        namespace=NAMESPACE,
        profile_digest="sha256:" + "5" * 64,
        worker_deployment=WORKER_DEPLOYMENT,
        receiver_deployment=RECEIVER_DEPLOYMENT,
        kubernetes_version="v1.36.1",
        api_ca_source="custom",
        otlp_ca_source="custom",
        objective={
            "minimumProbeAttemptsPerPhase": 20,
            "probeIntervalMilliseconds": 250,
            "maximumWorkflowCompletionMilliseconds": 60_000,
            "maximumRecoveryMilliseconds": 120_000,
            "requestTimeoutMilliseconds": 2_000,
        },
        phases=[
            processing_phase("baseline", reduced=False),
            processing_phase("worker-disruption", reduced=True),
            processing_phase("receiver-disruption", reduced=True),
            processing_phase("recovery", reduced=False),
        ],
        worker_before=processing_observation("worker-old"),
        worker_during=processing_observation("worker-survivor", ready=1),
        worker_after=processing_observation("worker-new"),
        worker_recovery_milliseconds=4_000,
        receiver_before=processing_observation("receiver-old"),
        receiver_during=processing_observation("receiver-survivor", ready=1),
        receiver_after=processing_observation("receiver-new"),
        receiver_recovery_milliseconds=3_500,
        started_at=datetime(2026, 9, 6, 12, 36, 10, tzinfo=timezone.utc),
        completed_at=datetime(2026, 9, 6, 12, 36, 50, tzinfo=timezone.utc),
    )


def inputs() -> tuple[dict[str, object], ...]:
    ingress_document, continuity_document = continuity_inputs()
    return (
        live_preflight(),
        healthy_diagnostic(),
        ingress_document,
        continuity_document,
        processing_report(continuity_document),
    )


def build(
    *,
    preflight_document: dict[str, object] | None = None,
    diagnostic_document: dict[str, object] | None = None,
    ingress_document: dict[str, object] | None = None,
    continuity_document: dict[str, object] | None = None,
    processing_document: dict[str, object] | None = None,
) -> dict[str, object]:
    documents = inputs()
    selected = (
        preflight_document or documents[0],
        diagnostic_document or documents[1],
        ingress_document or documents[2],
        continuity_document or documents[3],
        processing_document or documents[4],
    )
    return qualification.build_report(
        preflight_report=selected[0],
        preflight_digest=_digest(selected[0]),
        diagnostic_report=selected[1],
        diagnostic_digest=_digest(selected[1]),
        ingress_report=selected[2],
        ingress_digest=_digest(selected[2]),
        continuity_report=selected[3],
        continuity_digest=_digest(selected[3]),
        processing_report=selected[4],
        processing_digest=_digest(selected[4]),
        context=CONTEXT,
        namespace=NAMESPACE,
        release_name=RELEASE,
        deployment_name=DEPLOYMENT,
        worker_deployment_name=WORKER_DEPLOYMENT,
        receiver_deployment_name=RECEIVER_DEPLOYMENT,
        image_digest=IMAGE_DIGEST,
        current_cluster_environment=CLUSTER,
        now=QUALIFIED_AT,
    )


class CustomerDeploymentQualificationTests(unittest.TestCase):
    def test_contract_example_is_schema_semantic_and_sdk_valid(self) -> None:
        schema = json.loads(
            (
                ROOT
                / "contracts/schemas/customer-deployment-qualification-report.schema.json"
            ).read_text(encoding="utf-8")
        )
        example = json.loads(
            (
                ROOT
                / "contracts/examples/customer-deployment-qualification-report.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(example)
        qualification.validate_report_document(example)
        self.assertEqual(
            CustomerDeploymentQualificationReport.from_dict(example).to_dict(),
            example,
        )

    def test_exact_bound_inputs_produce_minimized_qualified_report(self) -> None:
        report = build()
        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(report["spec"]["summary"]["passedEvidence"], 5)
        serialized = json.dumps(report, sort_keys=True)
        for forbidden in (
            CONTEXT,
            NAMESPACE,
            DEPLOYMENT,
            WORKER_DEPLOYMENT,
            RECEIVER_DEPLOYMENT,
            "old-uid",
            "new-uid",
            "worker-old",
            "receiver-old",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_crossed_processing_target_is_rejected(self) -> None:
        documents = inputs()
        crossed = copy.deepcopy(documents[4])
        crossed["spec"]["bindings"]["apiTargetBindingDigest"] = (
            "sha256:" + "f" * 64
        )
        with self.assertRaisesRegex(
            qualification.CustomerDeploymentQualificationError,
            "customer-deployment-qualification.processing.crossed",
        ):
            build(processing_document=crossed)

    def test_crossed_source_and_current_cluster_are_rejected(self) -> None:
        crossed = healthy_diagnostic()
        crossed["metadata"]["sourceRevision"] = "f" * 40
        with self.assertRaisesRegex(
            qualification.CustomerDeploymentQualificationError,
            "customer-deployment-qualification.source.crossed",
        ):
            build(diagnostic_document=crossed)
        with self.assertRaisesRegex(
            qualification.CustomerDeploymentQualificationError,
            "customer-deployment-qualification.cluster.changed",
        ):
            documents = inputs()
            qualification.build_report(
                preflight_report=documents[0],
                preflight_digest=_digest(documents[0]),
                diagnostic_report=documents[1],
                diagnostic_digest=_digest(documents[1]),
                ingress_report=documents[2],
                ingress_digest=_digest(documents[2]),
                continuity_report=documents[3],
                continuity_digest=_digest(documents[3]),
                processing_report=documents[4],
                processing_digest=_digest(documents[4]),
                context=CONTEXT,
                namespace=NAMESPACE,
                release_name=RELEASE,
                deployment_name=DEPLOYMENT,
                worker_deployment_name=WORKER_DEPLOYMENT,
                receiver_deployment_name=RECEIVER_DEPLOYMENT,
                image_digest=IMAGE_DIGEST,
                current_cluster_environment={**CLUSTER, "clusterBindingDigest": "sha256:" + "6" * 64},
                now=QUALIFIED_AT,
            )

    def test_stale_or_non_post_continuity_evidence_cannot_qualify(self) -> None:
        stale = build()
        stale["spec"]["measurements"]["qualifiedAt"] = "2026-09-09T12:38:00Z"
        stale["metadata"]["generatedAt"] = "2026-09-09T12:38:00Z"
        stale["spec"]["measurements"]["oldestEvidenceAgeSeconds"] = 261480
        expected = qualification._expected_checks(stale)
        stale["spec"]["checks"] = expected
        failed = sum(item["status"] == "failed" for item in expected)
        stale["spec"]["status"] = "not-qualified"
        stale["spec"]["summary"].update(
            {
                "passedChecks": len(qualification.CHECK_IDS) - failed,
                "failedChecks": failed,
                "overallStatus": "not-qualified",
            }
        )
        metadata = dict(stale["metadata"])
        metadata.pop("id")
        stale["metadata"]["id"] = qualification._report_identifier(
            metadata, stale["spec"]
        )
        qualification.validate_report_document(stale)
        by_id = {item["id"]: item for item in stale["spec"]["checks"]}
        self.assertEqual(by_id["evidence-freshness"]["status"], "failed")

        before = healthy_diagnostic()
        before["metadata"]["generatedAt"] = "2026-09-06T12:29:00Z"
        before["spec"]["environment"]["observedAt"] = "2026-09-06T12:29:00Z"
        report = build(diagnostic_document=before)
        self.assertEqual(report["spec"]["status"], "not-qualified")
        by_id = {item["id"]: item for item in report["spec"]["checks"]}
        self.assertEqual(by_id["post-continuity-health"]["status"], "failed")

    def test_rehashed_arithmetic_or_status_tampering_is_rejected(self) -> None:
        report = build()
        tampered = copy.deepcopy(report)
        tampered["spec"]["summary"]["passedChecks"] = 13
        metadata = dict(tampered["metadata"])
        metadata.pop("id")
        tampered["metadata"]["id"] = qualification._report_identifier(
            metadata, tampered["spec"]
        )
        with self.assertRaisesRegex(
            qualification.CustomerDeploymentQualificationError,
            "customer-deployment-qualification.report.summary-invalid",
        ):
            qualification.validate_report_document(tampered)

    def test_qualify_observes_current_cluster_and_retains_failed_status(self) -> None:
        documents = inputs()
        rejected = copy.deepcopy(documents[1])
        rejected["spec"]["status"] = "attention-required"
        selected = (
            documents[0],
            _digest(documents[0]),
            rejected,
            _digest(rejected),
            documents[2],
            _digest(documents[2]),
            documents[3],
            _digest(documents[3]),
            documents[4],
            _digest(documents[4]),
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.json"
            with (
                patch.object(qualification, "_validate_inputs", return_value=selected),
                patch.object(
                    qualification.preflight,
                    "_cluster_environment",
                    return_value=(CLUSTER, {"id": "cluster-api", "status": "passed"}),
                ),
            ):
                report = qualification.qualify(
                    preflight_path=Path("preflight.json"),
                    diagnostic_path=Path("diagnostic.json"),
                    ingress_path=Path("ingress.json"),
                    continuity_path=Path("continuity.json"),
                    processing_path=Path("processing.json"),
                    processing_profile_path=Path("processing-profile.json"),
                    processing_api_base_url="https://api.example.test",
                    processing_otlp_base_url="https://otlp.example.test:4318",
                    values=[Path("values.yaml")],
                    context=CONTEXT,
                    namespace=NAMESPACE,
                    release_name=RELEASE,
                    deployment_name=DEPLOYMENT,
                    worker_deployment_name=WORKER_DEPLOYMENT,
                    receiver_deployment_name=RECEIVER_DEPLOYMENT,
                    image_digest=IMAGE_DIGEST,
                    output=output,
                    now=lambda: QUALIFIED_AT,
                )
            self.assertEqual(report["spec"]["status"], "not-qualified")
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
