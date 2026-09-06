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
SDK = ROOT / "sdks/python/src"
for entry in (str(SCRIPTS), str(SDK)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import deployment_diagnostics  # noqa: E402
from infra_intelligence_sdk import DeploymentDiagnosticReport  # noqa: E402


REVISION = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64
IDENTITY = {
    "applicationVersion": "0.84.0",
    "chartVersion": "0.87.0",
    "imageDigest": IMAGE_DIGEST,
}


def deployment(
    component: str,
    container: str,
    *,
    desired: int = 2,
    current: int = 2,
    updated: int = 2,
    ready: int = 2,
    available: int = 2,
    image_digest: str = IMAGE_DIGEST,
) -> dict[str, object]:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": f"customer-private-{component}",
            "labels": {
                "app.kubernetes.io/part-of": "infrastructure-intelligence-platform",
                "app.kubernetes.io/version": "0.84.0",
                "helm.sh/chart": "infra-intelligence-0.87.0",
            },
        },
        "spec": {
            "replicas": desired,
            "template": {
                "metadata": {
                    "labels": {"app.kubernetes.io/component": component}
                },
                "spec": {
                    "containers": [
                        {
                            "name": container,
                            "image": f"private.registry.example/iip@{image_digest}",
                        }
                    ]
                },
            },
        },
        "status": {
            "replicas": current,
            "updatedReplicas": updated,
            "readyReplicas": ready,
            "availableReplicas": available,
        },
    }


def pod(
    component: str,
    *,
    ready: bool = True,
    restarts: int = 0,
    unschedulable: bool = False,
    waiting_reason: str | None = None,
) -> dict[str, object]:
    conditions: list[dict[str, str]] = [
        {"type": "Ready", "status": "True" if ready else "False"}
    ]
    if unschedulable:
        conditions.append(
            {
                "type": "PodScheduled",
                "status": "False",
                "reason": "Unschedulable",
            }
        )
    state: dict[str, object] = {}
    if waiting_reason is not None:
        state["waiting"] = {"reason": waiting_reason, "message": "private failure"}
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": f"customer-private-{component}-pod",
            "labels": {"app.kubernetes.io/component": component},
        },
        "status": {
            "conditions": conditions,
            "containerStatuses": [
                {
                    "name": "private-container",
                    "ready": ready,
                    "restartCount": restarts,
                    "state": state,
                }
            ],
        },
    }


def list_document(items: list[dict[str, object]]) -> dict[str, object]:
    return {"apiVersion": "v1", "kind": "List", "items": items}


def runner(
    deployments: list[dict[str, object]], pods: list[dict[str, object]]
):
    def run(command: object) -> tuple[bool, str]:
        arguments = tuple(command)
        if arguments[1:3] == ("version", "--client"):
            return True, json.dumps(
                {"clientVersion": {"gitVersion": "v1.36.1"}}
            )
        if "version" in arguments:
            return True, json.dumps(
                {
                    "clientVersion": {"gitVersion": "v1.36.1"},
                    "serverVersion": {"gitVersion": "v1.36.1"},
                }
            )
        if "deployments" in arguments:
            return True, json.dumps(list_document(deployments))
        if "pods" in arguments:
            return True, json.dumps(list_document(pods))
        raise AssertionError(arguments)

    return run


class DeploymentDiagnosticTests(unittest.TestCase):
    def generate(
        self,
        deployments: list[dict[str, object]],
        pods: list[dict[str, object]],
        *,
        dirty: bool = False,
        command_runner=None,
    ) -> dict[str, object]:
        with patch.object(
            deployment_diagnostics,
            "_repository_identity",
            return_value={
                "applicationVersion": "0.84.0",
                "chartVersion": "0.87.0",
            },
        ), patch.object(
            deployment_diagnostics, "_git_state", return_value=(REVISION, dirty)
        ):
            return deployment_diagnostics.generate_report(
                context="customer-private-context",
                namespace="customer-private-namespace",
                release_name="customer-private-release",
                image_digest=IMAGE_DIGEST,
                runner=command_runner or runner(deployments, pods),
                report_id="ddr_" + "1" * 32,
                generated_at="2026-09-06T13:30:00Z",
            )

    def test_healthy_release_is_minimized_and_sdk_readable(self) -> None:
        deployments = [deployment("control-plane-api", "api")]
        pods = [pod("control-plane-api"), pod("control-plane-api")]

        report = self.generate(deployments, pods)

        self.assertEqual(report["spec"]["status"], "healthy")
        self.assertEqual(report["spec"]["summary"]["healthyComponents"], 1)
        self.assertEqual(report["spec"]["summary"]["notObservedComponents"], 2)
        self.assertEqual(report["spec"]["checks"][-1]["status"], "passed")
        self.assertEqual(
            DeploymentDiagnosticReport.from_dict(report).to_dict(), report
        )
        serialized = json.dumps(report, sort_keys=True)
        for forbidden in (
            "customer-private-context",
            "customer-private-namespace",
            "customer-private-release",
            "customer-private-control-plane-api",
            "private.registry.example",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_dirty_source_is_attention_required_without_hiding_runtime_health(self) -> None:
        report = self.generate(
            [deployment("control-plane-api", "api")],
            [pod("control-plane-api"), pod("control-plane-api")],
            dirty=True,
        )

        self.assertEqual(report["spec"]["status"], "attention-required")
        self.assertEqual(
            report["spec"]["checks"][0],
            {
                "id": "source-bound-tooling",
                "status": "warning",
                "errorCode": "deployment-diagnostic.source.dirty",
            },
        )

    def test_optional_worker_rollout_and_restart_are_actionable_warnings(self) -> None:
        deployments = [
            deployment("control-plane-api", "api"),
            deployment(
                "workflow-worker",
                "workflow-worker",
                current=2,
                updated=1,
                ready=1,
                available=1,
            ),
        ]
        pods = [
            pod("control-plane-api"),
            pod("control-plane-api"),
            pod("workflow-worker", restarts=3),
            pod("workflow-worker", ready=False),
        ]

        report = self.generate(deployments, pods)

        self.assertEqual(report["spec"]["status"], "attention-required")
        self.assertEqual(report["spec"]["components"][1]["state"], "progressing")
        self.assertEqual(report["spec"]["summary"]["totalRestarts"], 3)
        self.assertEqual(report["spec"]["checks"][6]["status"], "warning")
        self.assertEqual(report["spec"]["checks"][8]["status"], "warning")

    def test_unavailable_api_blocks_the_diagnostic(self) -> None:
        report = self.generate(
            [
                deployment(
                    "control-plane-api",
                    "api",
                    current=1,
                    updated=1,
                    ready=0,
                    available=0,
                )
            ],
            [pod("control-plane-api", ready=False)],
        )

        self.assertEqual(report["spec"]["status"], "blocked")
        self.assertEqual(report["spec"]["components"][0]["state"], "unavailable")
        self.assertEqual(
            report["spec"]["checks"][5]["errorCode"],
            "deployment-diagnostic.api.not-available",
        )

    def test_crossed_image_identity_blocks_the_diagnostic(self) -> None:
        wrong = "sha256:" + "c" * 64
        report = self.generate(
            [deployment("control-plane-api", "api", image_digest=wrong)],
            [pod("control-plane-api"), pod("control-plane-api")],
        )

        self.assertEqual(report["spec"]["status"], "blocked")
        self.assertEqual(report["spec"]["components"][0]["identityStatus"], "mismatch")
        self.assertEqual(report["spec"]["checks"][7]["status"], "failed")

    def test_unavailable_cluster_emits_a_minimized_blocked_report(self) -> None:
        def unavailable(command: object) -> tuple[bool, str]:
            arguments = tuple(command)
            if arguments[1:3] == ("version", "--client"):
                return True, json.dumps(
                    {"clientVersion": {"gitVersion": "v1.36.1"}}
                )
            return False, "private provider error"

        report = self.generate([], [], command_runner=unavailable)

        self.assertEqual(report["spec"]["status"], "blocked")
        self.assertEqual(
            report["spec"]["environment"]["clusterAccess"], "unavailable"
        )
        self.assertTrue(
            all(
                component["state"] == "not-observed"
                for component in report["spec"]["components"]
            )
        )
        self.assertNotIn("private provider error", json.dumps(report))

    def test_verifier_rejects_target_and_semantic_tampering(self) -> None:
        report = self.generate(
            [deployment("control-plane-api", "api")],
            [pod("control-plane-api"), pod("control-plane-api")],
        )
        with patch.object(
            deployment_diagnostics,
            "_repository_identity",
            return_value={
                "applicationVersion": "0.84.0",
                "chartVersion": "0.87.0",
            },
        ):
            with self.assertRaisesRegex(
                deployment_diagnostics.DeploymentDiagnosticError,
                "deployment-diagnostic.report.target-mismatch",
            ):
                deployment_diagnostics.verify_report(
                    report,
                    context="different-context",
                    namespace="customer-private-namespace",
                    release_name="customer-private-release",
                    image_digest=IMAGE_DIGEST,
                )
        changed = copy.deepcopy(report)
        changed["spec"]["summary"]["totalRestarts"] = 19
        with self.assertRaisesRegex(
            deployment_diagnostics.DeploymentDiagnosticError,
            "deployment-diagnostic.report.semantics-invalid",
        ):
            deployment_diagnostics.validate_report(changed)

        crossed_identity = copy.deepcopy(report)
        crossed_identity["spec"]["components"][0]["identity"]["imageDigest"] = (
            "sha256:" + "c" * 64
        )
        with self.assertRaisesRegex(
            deployment_diagnostics.DeploymentDiagnosticError,
            "deployment-diagnostic.report.semantics-invalid",
        ):
            deployment_diagnostics.validate_report(crossed_identity)

    def test_invalid_explicit_targets_fail_before_kubectl(self) -> None:
        cases = (
            {"context": "", "namespace": "valid", "release_name": "valid"},
            {"context": "valid", "namespace": "INVALID", "release_name": "valid"},
            {"context": "valid", "namespace": "valid", "release_name": "bad name"},
        )
        for values in cases:
            with self.subTest(values=values), patch.object(
                deployment_diagnostics,
                "_repository_identity",
                return_value={
                    "applicationVersion": "0.84.0",
                    "chartVersion": "0.87.0",
                },
            ):
                with self.assertRaises(deployment_diagnostics.DeploymentDiagnosticError):
                    deployment_diagnostics.generate_report(
                        **values,
                        image_digest=IMAGE_DIGEST,
                        runner=lambda command: self.fail(f"unexpected kubectl: {command}"),
                    )

    def test_contract_example_validates(self) -> None:
        schema = json.loads(
            (ROOT / "contracts/schemas/deployment-diagnostic-report.schema.json").read_text(
                encoding="utf-8"
            )
        )
        example = json.loads(
            (ROOT / "contracts/examples/deployment-diagnostic-report.json").read_text(
                encoding="utf-8"
            )
        )
        errors = list(
            Draft202012Validator(
                schema, format_checker=FormatChecker()
            ).iter_errors(example)
        )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
