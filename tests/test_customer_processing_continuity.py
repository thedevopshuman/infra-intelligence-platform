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

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import qualify_customer_continuity as customer  # noqa: E402
import qualify_customer_processing_continuity as continuity  # noqa: E402
from infra_intelligence_sdk import (  # noqa: E402
    CustomerProcessingContinuityQualificationReport,
    CustomerProcessingQualificationProfile,
)


REVISION = "0123456789abcdef0123456789abcdef01234567"
IMAGE_DIGEST = "sha256:" + "a" * 64
REPOSITORY = {
    "applicationVersion": "0.84.0",
    "chartVersion": "0.87.0",
    "requiredMigration": "0023_ai_model_suitability.sql",
}


def observation(uid: str, *, ready: int = 2) -> customer.DeploymentObservation:
    return customer.DeploymentObservation(
        desired_replicas=2,
        ready_replicas=ready,
        image_digest=IMAGE_DIGEST,
        max_unavailable=0,
        pdb_min_available=1,
        pdb_disruptions_allowed=1 if ready == 2 else 0,
        pod_name="private-pod-name",
        pod_uid=uid,
    )


def phase(phase_id: str, *, reduced: bool) -> dict[str, object]:
    return {
        "id": phase_id,
        "apiAttempts": 20,
        "apiSuccesses": 20,
        "apiFailures": 0,
        "receiverAttempts": 20,
        "receiverSuccesses": 20,
        "receiverFailures": 0,
        "workflowSubmitted": 1,
        "workflowCompleted": 1,
        "workflowFailures": 0,
        "workflowPollAttempts": 2,
        "workflowCompletionMilliseconds": 250,
        "submittedDuringReducedCapacity": reduced,
    }


class CustomerProcessingContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.profile = self.root / "profile.json"
        self.profile.write_text(
            (
                ROOT
                / "contracts/examples/customer-processing-qualification-profile.json"
            ).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        os.chmod(self.profile, 0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def build_report(self) -> dict[str, object]:
        return continuity.build_report(
            revision=REVISION,
            repository=REPOSITORY,
            image_digest=IMAGE_DIGEST,
            api_target_digest="sha256:" + "1" * 64,
            otlp_target_digest="sha256:" + "2" * 64,
            context="private-customer-context",
            namespace="private-customer-namespace",
            profile_digest="sha256:" + "3" * 64,
            worker_deployment="private-worker",
            receiver_deployment="private-receiver",
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
                phase("baseline", reduced=False),
                phase("worker-disruption", reduced=True),
                phase("receiver-disruption", reduced=True),
                phase("recovery", reduced=False),
            ],
            worker_before=observation("worker-old"),
            worker_during=observation("worker-survivor", ready=1),
            worker_after=observation("worker-new"),
            worker_recovery_milliseconds=4_000,
            receiver_before=observation("receiver-old"),
            receiver_during=observation("receiver-survivor", ready=1),
            receiver_after=observation("receiver-new"),
            receiver_recovery_milliseconds=3_500,
            started_at=datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 9, 6, 13, 10, tzinfo=timezone.utc),
        )

    def test_contract_examples_are_schema_semantic_and_sdk_valid(self) -> None:
        for stem in (
            "customer-processing-qualification-profile",
            "customer-processing-continuity-qualification-report",
        ):
            schema = json.loads(
                (ROOT / f"contracts/schemas/{stem}.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            example = json.loads(
                (ROOT / f"contracts/examples/{stem}.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(
                example
            )
        report = json.loads(
            (
                ROOT
                / "contracts/examples/customer-processing-continuity-qualification-report.json"
            ).read_text(encoding="utf-8")
        )
        profile = json.loads(
            (
                ROOT / "contracts/examples/customer-processing-qualification-profile.json"
            ).read_text(encoding="utf-8")
        )
        continuity.validate_report_document(report)
        self.assertEqual(
            CustomerProcessingContinuityQualificationReport.from_dict(
                report
            ).to_dict(),
            report,
        )
        self.assertEqual(
            CustomerProcessingQualificationProfile.from_dict(profile).to_dict(),
            profile,
        )

    def test_report_is_qualified_and_retains_only_aggregates_and_digests(self) -> None:
        report = self.build_report()
        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(report["spec"]["summary"]["totalApiAttempts"], 80)
        self.assertEqual(report["spec"]["summary"]["completedWorkflows"], 4)
        serialized = json.dumps(report, sort_keys=True)
        for forbidden in (
            "private-customer-context",
            "private-customer-namespace",
            "private-worker",
            "private-receiver",
            "worker-old",
            "receiver-old",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_semantic_validation_rejects_rehashed_probe_arithmetic(self) -> None:
        report = self.build_report()
        tampered = copy.deepcopy(report)
        first = tampered["spec"]["measurements"]["phases"][0]
        first["apiSuccesses"] = 19
        first["apiFailures"] = 0
        metadata = dict(tampered["metadata"])
        metadata.pop("id")
        tampered["metadata"]["id"] = continuity._report_identifier(
            metadata, tampered["spec"]
        )
        with self.assertRaisesRegex(
            continuity.CustomerProcessingContinuityError,
            "customer-processing-continuity.report.phases-invalid",
        ):
            continuity.validate_report_document(tampered)

    def test_semantic_validation_rejects_rehashed_check_status(self) -> None:
        report = self.build_report()
        tampered = copy.deepcopy(report)
        check = tampered["spec"]["checks"][14]
        check["status"] = "failed"
        check["errorCode"] = "customer-processing-continuity.api.failure"
        metadata = dict(tampered["metadata"])
        metadata.pop("id")
        tampered["metadata"]["id"] = continuity._report_identifier(
            metadata, tampered["spec"]
        )
        with self.assertRaisesRegex(
            continuity.CustomerProcessingContinuityError,
            "customer-processing-continuity.report.checks-invalid",
        ):
            continuity.validate_report_document(tampered)

    def test_recovery_does_not_mask_immutable_state_error(self) -> None:
        class BrokenClient:
            @staticmethod
            def observe(**_kwargs):
                raise customer.CustomerContinuityQualificationError(
                    "customer-continuity.kubernetes.image-invalid"
                )

        with self.assertRaisesRegex(
            continuity.CustomerProcessingContinuityError,
            "customer-processing-continuity.kubernetes.state-invalid",
        ):
            continuity._wait_for_recovery(
                BrokenClient(),
                original_pod_uid="old-uid",
                maximum_milliseconds=10_000,
                monotonic=lambda: 0.0,
                sleeper=lambda _seconds: None,
            )

    def test_profile_and_credentials_must_be_protected_regular_files(self) -> None:
        os.chmod(self.profile, 0o644)
        with self.assertRaisesRegex(
            continuity.CustomerProcessingContinuityError,
            "customer-processing-continuity.profile.invalid",
        ):
            continuity._load_profile(self.profile)

        token = self.root / "token"
        token.write_text("x" * 32 + "\n", encoding="ascii")
        os.chmod(token, 0o600)
        self.assertEqual(
            continuity._protected_token(token, "credential.invalid"), "x" * 32
        )

    def test_generic_kubectl_observes_reduced_worker_capacity(self) -> None:
        labels = {"app": "worker"}
        deployment = {
            "metadata": {"name": "worker", "uid": "deployment-uid", "generation": 1},
            "spec": {
                "replicas": 2,
                "strategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
                },
                "selector": {"matchLabels": labels},
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "workflow-worker",
                                "image": "registry.example/iip@" + IMAGE_DIGEST,
                            }
                        ]
                    }
                },
            },
            "status": {
                "observedGeneration": 1,
                "readyReplicas": 1,
                "availableReplicas": 1,
                "updatedReplicas": 2,
            },
        }
        pdb = {
            "metadata": {"name": "worker"},
            "spec": {"minAvailable": 1, "selector": {"matchLabels": labels}},
            "status": {"disruptionsAllowed": 0},
        }
        replicasets = {
            "kind": "List",
            "items": [
                {
                    "metadata": {
                        "uid": "rs-uid",
                        "ownerReferences": [
                            {
                                "controller": True,
                                "kind": "Deployment",
                                "name": "worker",
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
                        "name": "survivor",
                        "uid": "survivor-uid",
                        "labels": labels,
                        "ownerReferences": [
                            {
                                "controller": True,
                                "kind": "ReplicaSet",
                                "name": "rs",
                                "uid": "rs-uid",
                            }
                        ],
                    },
                    "status": {
                        "phase": "Running",
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
                }
            ],
        }

        def response(args, _code):
            if args[1] == "deployment":
                return deployment
            if args[1] == "poddisruptionbudget":
                return pdb
            if args[1] == "replicaset":
                return replicasets
            return pods

        client = customer.KubectlClient(
            binary="kubectl",
            context="customer-context",
            namespace="iip-system",
            deployment="worker",
            container="workflow-worker",
            expected_image_digest=IMAGE_DIGEST,
        )
        with patch.object(client, "_json", side_effect=response):
            result = client.observe(
                original_pod_uid="old-uid", require_reduced_capacity=True
            )
        self.assertEqual(result.ready_replicas, 1)
        self.assertEqual(result.pod_uid, "survivor-uid")

    def test_qualification_requires_explicit_disruption(self) -> None:
        with self.assertRaisesRegex(
            continuity.CustomerProcessingContinuityError,
            "customer-processing-continuity.disruption.explicit-enable-required",
        ):
            continuity.qualify(
                api_base_url="https://api.example.test",
                api_token_file=self.root / "api-token",
                otlp_base_url="https://otlp.example.test:4318",
                otlp_token_file=self.root / "otlp-token",
                otlp_client_cert_file=self.root / "client.crt",
                otlp_client_key_file=self.root / "client.key",
                profile_path=self.profile,
                image_digest=IMAGE_DIGEST,
                context="customer-context",
                namespace="iip-system",
                worker_deployment="worker",
                receiver_deployment="receiver",
                output=self.root / "report.json",
                allow_disruption=False,
            )

    def test_qualification_orchestrates_sequential_component_evictions(self) -> None:
        created: dict[str, object] = {}

        class FakeKubernetesClient:
            def __init__(self, **kwargs):
                self.component = kwargs["container"]
                self.evicted = False
                self.reduced = False
                created[self.component] = self

            def version(self):
                return "v1.36.1"

            def observe(self, *, original_pod_uid=None, require_reduced_capacity=False):
                if require_reduced_capacity:
                    self.reduced = True
                    return observation(self.component + "-survivor", ready=1)
                if original_pod_uid is not None:
                    return observation(self.component + "-replacement")
                return observation(self.component + "-original")

            def evict(self, selected):
                self.evicted = True
                self.selected = selected

        class FakeProcessingClient:
            api_target_digest = "sha256:" + "1" * 64
            otlp_target_digest = "sha256:" + "2" * 64
            api_ca_source = "custom"
            otlp_ca_source = "custom"

            def __init__(self, **_kwargs):
                self.phases: list[str] = []

            def validate_resource(self):
                return None

            def start_workflow(self, selected_phase):
                self.phases.append(selected_phase)
                if selected_phase == "worker-disruption":
                    self.assert_reduced("workflow-worker")
                if selected_phase == "receiver-disruption":
                    self.assert_reduced("otlp-receiver")
                return "inv_" + "a" * 32, 0.0

            @staticmethod
            def assert_reduced(component):
                assert created[component].reduced

            def probe_cycle(self):
                return True, True

            def finish_workflow(self, *_args):
                return {
                    "workflowSubmitted": 1,
                    "workflowCompleted": 1,
                    "workflowFailures": 0,
                    "workflowPollAttempts": 1,
                    "workflowCompletionMilliseconds": 100,
                }

        ticks = iter(index / 10 for index in range(100))
        clock = iter(
            (
                datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc),
                datetime(2026, 9, 6, 13, 1, tzinfo=timezone.utc),
            )
        )
        output = self.root / "report.json"
        with (
            patch.object(continuity.ingress, "_git_state", return_value=(REVISION, False)),
            patch.object(
                continuity.ingress, "_repository_identity", return_value=REPOSITORY
            ),
        ):
            report = continuity.qualify(
                api_base_url="https://api.example.test",
                api_token_file=self.root / "unused-api-token",
                otlp_base_url="https://otlp.example.test:4318",
                otlp_token_file=self.root / "unused-otlp-token",
                otlp_client_cert_file=self.root / "unused-client.crt",
                otlp_client_key_file=self.root / "unused-client.key",
                profile_path=self.profile,
                image_digest=IMAGE_DIGEST,
                context="customer-context",
                namespace="iip-system",
                worker_deployment="worker",
                receiver_deployment="receiver",
                output=output,
                allow_disruption=True,
                attempts_per_phase=5,
                probe_interval_milliseconds=50,
                maximum_workflow_milliseconds=1_000,
                maximum_recovery_milliseconds=10_000,
                request_timeout_milliseconds=100,
                client_factory=FakeKubernetesClient,
                processing_client_factory=FakeProcessingClient,
                monotonic=lambda: next(ticks),
                sleeper=lambda _seconds: None,
                now=lambda: next(clock),
            )
        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertTrue(created["workflow-worker"].evicted)
        self.assertTrue(created["otlp-receiver"].evicted)
        self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
