from __future__ import annotations

import copy
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import kubernetes_availability_qualification as qualification  # noqa: E402
import kubernetes_availability_probe as probe  # noqa: E402
from infra_intelligence_sdk import (  # noqa: E402
    KubernetesAvailabilityQualificationReport,
)


REVISION = "a" * 40
IMAGE_DIGEST = "sha256:" + "1" * 64


def nodes(*, target_unschedulable: bool) -> dict[str, object]:
    def node(name: str, *, control_plane: bool, unschedulable: bool = False):
        labels = {"kubernetes.io/hostname": name}
        if control_plane:
            labels["node-role.kubernetes.io/control-plane"] = ""
        return {
            "apiVersion": "v1",
            "kind": "Node",
            "metadata": {"name": name, "labels": labels},
            "spec": {"unschedulable": unschedulable},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }

    return {
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            node("qualification-control-plane", control_plane=True),
            node(
                "qualification-worker-a",
                control_plane=False,
                unschedulable=target_unschedulable,
            ),
            node("qualification-worker-b", control_plane=False),
        ],
    }


def snapshot(phase: str) -> dict[str, object]:
    items: list[dict[str, object]] = []
    disrupted = phase == "disruption"
    for component_id, deployment_name, pdb_name in qualification.COMPONENTS:
        selector = {
            "app.kubernetes.io/name": "infra-intelligence",
            "app.kubernetes.io/instance": "iip",
            "app.kubernetes.io/component": component_id,
        }
        ready = 1 if disrupted else 2
        items.extend(
            [
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": deployment_name},
                    "spec": {
                        "replicas": 2,
                        "strategy": {
                            "type": "RollingUpdate",
                            "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
                        },
                        "selector": {"matchLabels": selector},
                        "template": {
                            "spec": {
                                "topologySpreadConstraints": [
                                    {
                                        "maxSkew": 1,
                                        "minDomains": 2,
                                        "topologyKey": "kubernetes.io/hostname",
                                        "whenUnsatisfiable": "DoNotSchedule",
                                        "labelSelector": {"matchLabels": selector},
                                    }
                                ]
                            }
                        },
                    },
                    "status": {
                        "readyReplicas": ready,
                        "unavailableReplicas": 1 if disrupted else 0,
                    },
                },
                {
                    "apiVersion": "policy/v1",
                    "kind": "PodDisruptionBudget",
                    "metadata": {"name": pdb_name},
                    "spec": {
                        "minAvailable": 1,
                        "selector": {"matchLabels": selector},
                    },
                    "status": {"disruptionsAllowed": 0 if disrupted else 1},
                },
            ]
        )
        ready_nodes = ["qualification-worker-b"] if disrupted else [
            "qualification-worker-a",
            "qualification-worker-b",
        ]
        for index, node_name in enumerate(ready_nodes):
            items.append(
                {
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {
                        "name": f"{deployment_name}-{phase}-{index}",
                        "labels": selector,
                    },
                    "spec": {"nodeName": node_name},
                    "status": {
                        "phase": "Running",
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
                }
            )
        if disrupted:
            items.append(
                {
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {
                        "name": f"{deployment_name}-{phase}-pending",
                        "labels": selector,
                    },
                    "spec": {},
                    "status": {"phase": "Pending", "conditions": []},
                }
            )
    return {"apiVersion": "v1", "kind": "List", "items": items}


def probe_state(*, failures: int = 0) -> dict[str, object]:
    phases: dict[str, object] = {}
    for phase in qualification.PHASES:
        phases[phase] = {
            probe_id: {
                "attempts": 20,
                "successes": 20 - failures,
                "failures": failures,
            }
            for probe_id, _, _ in qualification.PROBES
        }
    return {
        "seed": {"resourceAccepted": True, "metricAccepted": True},
        "phases": phases,
    }


def workflow_state(*, failed_phase: str | None = None) -> dict[str, object]:
    return {
        "phases": {
            phase: {
                "phase": phase,
                "submitted": 1,
                "completed": 0 if phase == failed_phase else 1,
                "failures": 1 if phase == failed_phase else 0,
                "pollAttempts": 2,
                "completionMilliseconds": 250,
            }
            for phase in qualification.PHASES
        }
    }


class KubernetesAvailabilityQualificationTests(unittest.TestCase):
    def report(self) -> dict[str, object]:
        with (
            patch.object(qualification, "_git_state", return_value=(REVISION, False)),
            patch.object(
                qualification,
                "_repository_identity",
                return_value={
                    "applicationVersion": "0.84.0",
                    "chartVersion": "0.87.0",
                    "requiredMigration": "0023_ai_model_suitability.sql",
                },
            ),
            patch.object(qualification, "_platform_name", return_value="linux/arm64"),
        ):
            return qualification.build_report(
                baseline_nodes=nodes(target_unschedulable=False),
                disruption_nodes=nodes(target_unschedulable=True),
                recovery_nodes=nodes(target_unschedulable=False),
                baseline_snapshot=snapshot("baseline"),
                disruption_snapshot=snapshot("disruption"),
                recovery_snapshot=snapshot("recovery"),
                probe_state=probe_state(),
                workflow_state=workflow_state(),
                cluster_name="iip-availability-test",
                namespace="iip-availability",
                target_node="qualification-worker-a",
                image_digest=IMAGE_DIGEST,
                kubernetes_version="v1.36.1",
                kind_version="v0.32.0",
                docker_version="29.7.2",
                containerd_version="v2.2.1",
                generated_at=datetime(2026, 9, 6, 12, 30, tzinfo=timezone.utc),
            )

    def test_builds_minimized_source_bound_qualified_report(self) -> None:
        report = self.report()
        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(report["spec"]["summary"]["totalProbeAttempts"], 120)
        self.assertEqual(report["spec"]["summary"]["completedWorkflows"], 3)
        self.assertEqual(
            report["spec"]["receiverIntake"]["successBoundary"],
            "postgresql-commit-before-http-200",
        )
        self.assertEqual(
            [item["id"] for item in report["spec"]["components"]],
            [item[0] for item in qualification.COMPONENTS],
        )
        serialized = json.dumps(report, sort_keys=True)
        for forbidden in (
            "iip-availability-test",
            "iip-availability",
            "qualification-worker-a",
            "qualification-worker-b",
            "qualification-control-plane",
        ):
            self.assertNotIn(forbidden, serialized)
        qualification.validate_report(report)
        parsed = KubernetesAvailabilityQualificationReport.from_dict(report)
        self.assertEqual(parsed.to_dict(), report)

    def test_rejects_probe_failure(self) -> None:
        with self.assertRaisesRegex(
            qualification.KubernetesAvailabilityQualificationError,
            "kubernetes-availability.probes.control-plane-api.baseline-failed",
        ):
            with patch.object(qualification, "_git_state", return_value=(REVISION, False)):
                qualification.build_report(
                    baseline_nodes=nodes(target_unschedulable=False),
                    disruption_nodes=nodes(target_unschedulable=True),
                    recovery_nodes=nodes(target_unschedulable=False),
                    baseline_snapshot=snapshot("baseline"),
                    disruption_snapshot=snapshot("disruption"),
                    recovery_snapshot=snapshot("recovery"),
                    probe_state=probe_state(failures=1),
                    workflow_state=workflow_state(),
                    cluster_name="iip-availability-test",
                    namespace="iip-availability",
                    target_node="qualification-worker-a",
                    image_digest=IMAGE_DIGEST,
                    kubernetes_version="v1.36.1",
                    kind_version="v0.32.0",
                    docker_version="29.7.2",
                    containerd_version="v2.2.1",
                )

    def test_rejects_component_remaining_on_drained_node(self) -> None:
        disrupted = snapshot("disruption")
        pod = next(item for item in disrupted["items"] if item["kind"] == "Pod")
        pod["spec"]["nodeName"] = "qualification-worker-a"
        with self.assertRaisesRegex(
            qualification.KubernetesAvailabilityQualificationError,
            "kubernetes-availability.disruption.target-not-drained",
        ):
            with patch.object(qualification, "_git_state", return_value=(REVISION, False)):
                qualification.build_report(
                    baseline_nodes=nodes(target_unschedulable=False),
                    disruption_nodes=nodes(target_unschedulable=True),
                    recovery_nodes=nodes(target_unschedulable=False),
                    baseline_snapshot=snapshot("baseline"),
                    disruption_snapshot=disrupted,
                    recovery_snapshot=snapshot("recovery"),
                    probe_state=probe_state(),
                    workflow_state=workflow_state(),
                    cluster_name="iip-availability-test",
                    namespace="iip-availability",
                    target_node="qualification-worker-a",
                    image_digest=IMAGE_DIGEST,
                    kubernetes_version="v1.36.1",
                    kind_version="v0.32.0",
                    docker_version="29.7.2",
                    containerd_version="v2.2.1",
                )

    def test_semantic_validation_rejects_rehashed_state_tampering(self) -> None:
        report = self.report()
        tampered = copy.deepcopy(report)
        tampered["spec"]["components"][0]["disruption"]["readyReplicas"] = 0
        tampered["metadata"]["id"] = qualification._report_id(tampered)
        with self.assertRaisesRegex(
            qualification.KubernetesAvailabilityQualificationError,
            "kubernetes-availability.report.components-invalid",
        ):
            qualification.validate_report(tampered)

    def test_rejects_disruption_workflow_failure(self) -> None:
        with self.assertRaisesRegex(
            qualification.KubernetesAvailabilityQualificationError,
            "kubernetes-availability.workflows.disruption-failed",
        ):
            with patch.object(qualification, "_git_state", return_value=(REVISION, False)):
                qualification.build_report(
                    baseline_nodes=nodes(target_unschedulable=False),
                    disruption_nodes=nodes(target_unschedulable=True),
                    recovery_nodes=nodes(target_unschedulable=False),
                    baseline_snapshot=snapshot("baseline"),
                    disruption_snapshot=snapshot("disruption"),
                    recovery_snapshot=snapshot("recovery"),
                    probe_state=probe_state(),
                    workflow_state=workflow_state(failed_phase="disruption"),
                    cluster_name="iip-availability-test",
                    namespace="iip-availability",
                    target_node="qualification-worker-a",
                    image_digest=IMAGE_DIGEST,
                    kubernetes_version="v1.36.1",
                    kind_version="v0.32.0",
                    docker_version="29.7.2",
                    containerd_version="v2.2.1",
                )

    def test_rejects_boolean_workflow_counters(self) -> None:
        state = workflow_state()
        state["phases"]["baseline"]["submitted"] = True
        with self.assertRaisesRegex(
            qualification.KubernetesAvailabilityQualificationError,
            "kubernetes-availability.workflows.baseline-failed",
        ):
            with patch.object(qualification, "_git_state", return_value=(REVISION, False)):
                qualification.build_report(
                    baseline_nodes=nodes(target_unschedulable=False),
                    disruption_nodes=nodes(target_unschedulable=True),
                    recovery_nodes=nodes(target_unschedulable=False),
                    baseline_snapshot=snapshot("baseline"),
                    disruption_snapshot=snapshot("disruption"),
                    recovery_snapshot=snapshot("recovery"),
                    probe_state=probe_state(),
                    workflow_state=state,
                    cluster_name="iip-availability-test",
                    namespace="iip-availability",
                    target_node="qualification-worker-a",
                    image_digest=IMAGE_DIGEST,
                    kubernetes_version="v1.36.1",
                    kind_version="v0.32.0",
                    docker_version="29.7.2",
                    containerd_version="v2.2.1",
                )

    def test_contract_example_is_schema_and_semantically_valid(self) -> None:
        schema = json.loads(
            (
                ROOT
                / "contracts/schemas/kubernetes-availability-qualification-report.schema.json"
            ).read_text(encoding="utf-8")
        )
        example = json.loads(
            (
                ROOT
                / "contracts/examples/kubernetes-availability-qualification-report.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(example)
        qualification.validate_report(example)

    def test_harness_never_targets_an_existing_context(self) -> None:
        source = (ROOT / "scripts/test_kubernetes_availability.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('"$IIP_KIND_BIN" create cluster', source)
        self.assertIn('"$IIP_KIND_BIN" delete cluster', source)
        self.assertIn("IIP_AVAILABILITY_CLUSTER_CREATED=false", source)
        self.assertIn('kind-"$IIP_AVAILABILITY_CLUSTER"', source)
        self.assertIn("    git \\\n", source)
        self.assertIn("    rg; do", source)
        self.assertIn(
            "printf 'postgresql://iip:%s@iip-postgres:5432/iip' ", source
        )
        self.assertNotIn(
            "printf 'postgresql://iip:%s@iip-postgres:5432/iip\\n' ", source
        )
        self.assertNotIn("kind-iip-dev", source)
        self.assertNotIn("--force --grace-period=0", source)
        self.assertIn(
            "--set database.transportSecurity.mode=insecure-local",
            source,
        )
        self.assertIn("run_workflow_phase disruption", source)
        self.assertIn("body=_metric_payload()", (ROOT / "scripts/kubernetes_availability_probe.py").read_text(encoding="utf-8"))

    def test_receiver_probe_sends_a_real_metric_on_every_attempt(self) -> None:
        calls: list[dict[str, object]] = []

        def request(**kwargs):
            calls.append(kwargs)
            return 200, "application/x-protobuf", b""

        with patch.object(probe, "_request", side_effect=request):
            self.assertTrue(
                probe._otlp_probe(
                    SimpleNamespace(otlp_url="http://receiver"), "secret-token"
                )
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["method"], "POST")
        self.assertGreater(len(calls[0]["body"]), 0)

    def test_workflow_probe_requires_completed_report_and_minimizes_output(self) -> None:
        investigation_id = "inv_" + "d" * 32
        responses = iter(
            (
                (
                    202,
                    "application/json",
                    json.dumps(
                        {
                            "apiVersion": "iip.platform/v1alpha1",
                            "kind": "InvestigationJobStatus",
                            "metadata": {
                                "id": investigation_id,
                                "tenantId": "availability",
                            },
                            "spec": {"state": "queued"},
                        }
                    ).encode(),
                ),
                (
                    200,
                    "application/json",
                    json.dumps(
                        {
                            "apiVersion": "iip.platform/v1alpha1",
                            "kind": "InvestigationJobStatus",
                            "metadata": {
                                "id": investigation_id,
                                "tenantId": "availability",
                            },
                            "spec": {"state": "queued"},
                        }
                    ).encode(),
                ),
                (
                    200,
                    "application/json",
                    json.dumps(
                        {
                            "apiVersion": "iip.platform/v1alpha1",
                            "kind": "InvestigationJobStatus",
                            "metadata": {
                                "id": investigation_id,
                                "tenantId": "availability",
                            },
                            "spec": {"state": "completed"},
                        }
                    ).encode(),
                ),
                (
                    200,
                    "application/json",
                    json.dumps(
                        {
                            "apiVersion": "iip.platform/v1alpha1",
                            "kind": "InvestigationReport",
                            "metadata": {
                                "id": investigation_id,
                                "tenantId": "availability",
                            },
                            "spec": {"outcome": "inconclusive"},
                        }
                    ).encode(),
                ),
            )
        )
        output = io.StringIO()
        args = SimpleNamespace(
            api_url="http://api",
            control_token_file=Path("unused"),
            tenant="availability",
            actor_id="availability-probe",
            phase="disruption",
            timeout_seconds=60,
        )
        with (
            patch.object(probe, "_token", return_value="secret-token"),
            patch.object(probe.secrets, "token_hex", return_value="d" * 32),
            patch.object(probe, "_request", side_effect=lambda **_kwargs: next(responses)),
            patch.object(probe.time, "monotonic", side_effect=(10, 10, 10.2, 10.25)),
            patch.object(probe.time, "sleep"),
            redirect_stdout(output),
        ):
            probe.workflow(args)
        result = json.loads(output.getvalue())
        self.assertEqual(result["phase"], "disruption")
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["failures"], 0)
        self.assertNotIn(investigation_id, output.getvalue())

    def test_probe_main_emits_only_a_stable_failure(self) -> None:
        output = io.StringIO()
        with (
            patch.object(probe, "workflow", side_effect=RuntimeError("secret detail")),
            redirect_stderr(output),
        ):
            result = probe.main(
                [
                    "workflow",
                    "--api-url",
                    "http://api",
                    "--control-token-file",
                    "unused",
                    "--phase",
                    "baseline",
                ]
            )
        self.assertEqual(result, 1)
        self.assertEqual(output.getvalue(), "availability probe failed\n")


if __name__ == "__main__":
    unittest.main()
