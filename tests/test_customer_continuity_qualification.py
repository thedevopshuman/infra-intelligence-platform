from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import qualify_customer_continuity as continuity  # noqa: E402
import qualify_ingress_availability as ingress  # noqa: E402
from infra_intelligence_sdk import CustomerContinuityQualificationReport  # noqa: E402


REVISION = "0123456789abcdef0123456789abcdef01234567"
IMAGE_DIGEST = "sha256:" + "a" * 64
REPOSITORY = {
    "applicationVersion": "0.84.0",
    "chartVersion": "0.87.0",
    "requiredMigration": "0023_ai_model_suitability.sql",
}
STARTED = datetime(2026, 9, 6, 12, 30, tzinfo=timezone.utc)
EVICTED = datetime(2026, 9, 6, 12, 31, tzinfo=timezone.utc)
RECOVERED = datetime(2026, 9, 6, 12, 31, 5, tzinfo=timezone.utc)
COMPLETED = datetime(2026, 9, 6, 12, 36, 5, tzinfo=timezone.utc)


def ingress_report(*, failures: int = 0) -> dict[str, object]:
    document = json.loads(
        (ROOT / "contracts/examples/ingress-availability-qualification-report.json").read_text(
            encoding="utf-8"
        )
    )
    objective = document["spec"]["objective"]
    objective.update(
        {
            "sampleCount": 721,
            "minimumAvailabilityBasisPoints": 9990,
            "maximumP95LatencyMilliseconds": 2000,
            "requestTimeoutMilliseconds": 2000,
            "intervalMilliseconds": 500,
        }
    )
    measurements = document["spec"]["measurements"]
    measurements.update(
        {
            "startedAt": "2026-09-06T12:30:00Z",
            "completedAt": "2026-09-06T12:36:05Z",
            "sampleCount": 721,
            "successfulSamples": 721 - failures,
            "failedSamples": failures,
            "availabilityBasisPoints": ((721 - failures) * 10_000) // 721,
            "p95CycleLatencyMilliseconds": 42,
        }
    )
    for index, path in enumerate(measurements["paths"]):
        path_failures = failures if index == 0 else 0
        path.update(
            {
                "attempts": 721,
                "successes": 721 - path_failures,
                "failures": path_failures,
            }
        )
    measurements["failureCategories"] = {
        "transport": failures,
        "http-status": 0,
        "contract": 0,
        "identity": 0,
    }
    if failures:
        document["spec"]["status"] = "not-qualified"
        document["spec"]["checks"][8] = {
            "id": "availability-objective",
            "status": "failed",
            "errorCode": "ingress-qualification.availability.objective-missed",
        }
        document["spec"]["summary"] = {
            "totalChecks": 10,
            "passedChecks": 9,
            "failedChecks": 1,
            "overallStatus": "not-qualified",
        }
    metadata = document["metadata"]
    metadata["generatedAt"] = "2026-09-06T12:36:05Z"
    metadata_without_id = dict(metadata)
    metadata_without_id.pop("id")
    metadata["id"] = ingress._report_identifier(metadata_without_id, document["spec"])
    ingress.validate_report_document(document)
    return document


def observation(*, pod_uid: str) -> continuity.DeploymentObservation:
    return continuity.DeploymentObservation(
        desired_replicas=2,
        ready_replicas=2,
        image_digest=IMAGE_DIGEST,
        max_unavailable=0,
        pdb_min_available=1,
        pdb_disruptions_allowed=1,
        pod_name="private-pod-name",
        pod_uid=pod_uid,
    )


class CustomerContinuityQualificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _build(self, *, failures: int = 0) -> tuple[dict[str, object], Path]:
        ingress_document = ingress_report(failures=failures)
        ingress_path = self.root / "ingress.json"
        ingress_path.write_text(
            json.dumps(ingress_document, indent=2) + "\n", encoding="utf-8"
        )
        report = continuity.build_report(
            revision=REVISION,
            repository=REPOSITORY,
            image_digest=IMAGE_DIGEST,
            ingress_report=ingress_document,
            ingress_report_digest=continuity._sha256_file(ingress_path),
            context="customer-context",
            namespace="private-namespace",
            deployment_name="private-deployment",
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
        return report, ingress_path

    def test_contract_example_is_schema_semantic_and_sdk_valid(self) -> None:
        schema = json.loads(
            (
                ROOT
                / "contracts/schemas/customer-continuity-qualification-report.schema.json"
            ).read_text(encoding="utf-8")
        )
        example = json.loads(
            (
                ROOT
                / "contracts/examples/customer-continuity-qualification-report.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(example)
        continuity.validate_report_document(example)
        self.assertEqual(
            CustomerContinuityQualificationReport.from_dict(example).to_dict(),
            example,
        )

    def test_build_report_retains_only_aggregates_and_digests(self) -> None:
        report, _ = self._build()
        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(report["spec"]["summary"]["passedChecks"], 16)
        self.assertEqual(report["spec"]["measurements"]["actualWindowSeconds"], 365)
        serialized = json.dumps(report, sort_keys=True)
        for forbidden in (
            "customer-context",
            "private-namespace",
            "private-deployment",
            "private-pod-name",
            "old-uid",
            "new-uid",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_failed_ingress_objective_cannot_be_promoted(self) -> None:
        report, _ = self._build(failures=2)
        self.assertEqual(report["spec"]["status"], "not-qualified")
        by_id = {item["id"]: item for item in report["spec"]["checks"]}
        self.assertEqual(by_id["ingress-probe-qualified"]["status"], "failed")
        self.assertEqual(by_id["availability-objective"]["status"], "failed")

    def test_semantic_validation_rejects_rehashed_arithmetic_tampering(self) -> None:
        report, _ = self._build()
        tampered = copy.deepcopy(report)
        tampered["spec"]["measurements"]["ingress"]["successfulSamples"] = 720
        metadata = dict(tampered["metadata"])
        metadata.pop("id")
        tampered["metadata"]["id"] = continuity._report_identifier(
            metadata, tampered["spec"]
        )
        with self.assertRaisesRegex(
            continuity.CustomerContinuityQualificationError,
            "customer-continuity.report.ingress-arithmetic-invalid",
        ):
            continuity.validate_report_document(tampered)

    def test_offline_verifier_requires_exact_ingress_and_current_source(self) -> None:
        report, ingress_path = self._build()
        report_path = self.root / "continuity.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        with (
            patch.object(continuity.ingress, "_git_state", return_value=(REVISION, False)),
            patch.object(
                continuity.ingress, "_repository_identity", return_value=REPOSITORY
            ),
        ):
            verified = continuity.verify_report(
                report_path=report_path,
                ingress_report_path=ingress_path,
                require_clean=True,
                require_qualified=True,
            )
        self.assertEqual(verified["metadata"]["id"], report["metadata"]["id"])

        crossed = ingress_report()
        crossed["spec"]["measurements"]["p95CycleLatencyMilliseconds"] = 43
        ingress_path.write_text(json.dumps(crossed) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(
            continuity.CustomerContinuityQualificationError,
            "customer-continuity.ingress-report",
        ):
            continuity.verify_report(
                report_path=report_path, ingress_report_path=ingress_path
            )

    def test_run_requires_explicit_disruption_and_sustained_window(self) -> None:
        common = {
            "base_url": "https://iip.example.test",
            "token_file": self.root / "token",
            "image_digest": IMAGE_DIGEST,
            "context": "customer-context",
            "namespace": "iip-system",
            "deployment_name": "iip-infra-intelligence",
            "ingress_report_path": self.root / "ingress.json",
            "output": self.root / "report.json",
        }
        with self.assertRaisesRegex(
            continuity.CustomerContinuityQualificationError,
            "customer-continuity.disruption.explicit-enable-required",
        ):
            continuity.qualify(allow_disruption=False, **common)
        with self.assertRaisesRegex(
            continuity.CustomerContinuityQualificationError,
            "customer-continuity.objective.window-insufficient",
        ):
            continuity.qualify(
                allow_disruption=True,
                sample_count=301,
                interval_milliseconds=1000,
                minimum_baseline_seconds=100,
                minimum_post_recovery_seconds=100,
                maximum_recovery_seconds=120,
                **common,
            )

    def test_kubectl_client_uses_explicit_scope_and_uid_precondition(self) -> None:
        deployment = {
            "metadata": {
                "name": "iip-infra-intelligence",
                "uid": "deployment-uid",
                "generation": 3,
            },
            "spec": {
                "replicas": 2,
                "strategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
                },
                "selector": {"matchLabels": {"app": "iip-api"}},
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "api",
                                "image": "registry.example/iip@" + IMAGE_DIGEST,
                            }
                        ]
                    }
                },
            },
            "status": {
                "observedGeneration": 3,
                "readyReplicas": 2,
                "availableReplicas": 2,
                "updatedReplicas": 2,
            },
        }
        pdb = {
            "metadata": {"name": "iip-infra-intelligence"},
            "spec": {
                "minAvailable": 1,
                "selector": {"matchLabels": {"app": "iip-api"}},
            },
            "status": {"disruptionsAllowed": 1},
        }
        replicasets = {
            "kind": "List",
            "items": [
                {
                    "metadata": {
                        "uid": "replicaset-uid",
                        "ownerReferences": [
                            {
                                "controller": True,
                                "kind": "Deployment",
                                "name": "iip-infra-intelligence",
                                "uid": "deployment-uid",
                            }
                        ],
                    }
                }
            ],
        }
        pods = {
            "kind": "List",
            "items": [
                {
                    "metadata": {
                        "name": f"pod-{index}",
                        "uid": f"pod-uid-{index}",
                        "labels": {"app": "iip-api"},
                        "ownerReferences": [
                            {
                                "controller": True,
                                "kind": "ReplicaSet",
                                "name": "rs",
                                "uid": "replicaset-uid",
                            }
                        ],
                    },
                    "status": {
                        "phase": "Running",
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
                }
                for index in range(2)
            ],
        }
        calls: list[tuple[list[str], str | None]] = []

        def run(command, **kwargs):
            calls.append((command, kwargs.get("input")))
            if "version" in command:
                payload = {"serverVersion": {"gitVersion": "v1.36.1"}}
            elif "deployment" in command:
                payload = deployment
            elif "poddisruptionbudget" in command:
                payload = pdb
            elif "replicaset" in command:
                payload = replicasets
            elif "pod" in command and "get" in command:
                payload = pods
            else:
                payload = {"kind": "Status", "status": "Success"}
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        client = continuity.KubectlClient(
            binary="kubectl",
            context="customer-context",
            namespace="iip-system",
            deployment="iip-infra-intelligence",
            expected_image_digest=IMAGE_DIGEST,
        )
        with patch.object(continuity.subprocess, "run", side_effect=run):
            self.assertEqual(client.version(), "v1.36.1")
            observed = client.observe()
            client.evict(observed)

        for command, _ in calls:
            self.assertEqual(command[1:5], ["--context", "customer-context", "--namespace", "iip-system"])
        eviction_call = calls[-1]
        self.assertIn("/eviction", eviction_call[0][-3])
        body = json.loads(eviction_call[1])
        self.assertEqual(
            body["deleteOptions"]["preconditions"]["uid"], observed.pod_uid
        )

    def test_qualification_orchestrates_probe_eviction_and_recovery(self) -> None:
        token = self.root / "token"
        token.write_text("x" * 32 + "\n", encoding="ascii")
        ingress_path = self.root / "ingress.json"
        output = self.root / "continuity.json"
        evicted = threading.Event()

        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def version(self):
                return "v1.36.1"

            def observe(self, *, original_pod_uid=None):
                return observation(
                    pod_uid="new-uid" if original_pod_uid else "old-uid"
                )

            def evict(self, selected):
                self.selected = selected
                evicted.set()

        def fake_probe(**kwargs):
            kwargs["started_callback"](STARTED)
            self.assertTrue(evicted.wait(timeout=2))
            document = ingress_report()
            kwargs["output"].write_text(
                json.dumps(document, indent=2) + "\n", encoding="utf-8"
            )
            return document

        times = iter((EVICTED, RECOVERED))
        monotonic_values = iter((0.0, 0.0, 1.0))
        with (
            patch.object(continuity.ingress, "_git_state", return_value=(REVISION, False)),
            patch.object(
                continuity.ingress, "_repository_identity", return_value=REPOSITORY
            ),
            patch.object(continuity.ingress, "generate_report", side_effect=fake_probe),
        ):
            report = continuity.qualify(
                base_url="https://iip.example.test",
                token_file=token,
                image_digest=IMAGE_DIGEST,
                context="customer-context",
                namespace="iip-system",
                deployment_name="iip-infra-intelligence",
                ingress_report_path=ingress_path,
                output=output,
                allow_disruption=True,
                client_factory=FakeClient,
                sleeper=lambda _: None,
                monotonic=lambda: next(monotonic_values),
                now=lambda: next(times),
            )
        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
