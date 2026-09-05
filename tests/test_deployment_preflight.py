from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from deployment_preflight import (  # noqa: E402
    COMMON_CHECKS,
    LIVE_CHECKS,
    DeploymentPreflightError,
    _observe_dependency,
    _report_identifier,
    generate_report,
    validate_report_document,
    verify_report,
)
from infra_intelligence_sdk import CustomerDeploymentPreflightReport  # noqa: E402


REVISION = "a" * 40


def rendered_profile() -> dict[str, object]:
    return {
        "schemaVersion": "1",
        "chart": {
            "name": "infra-intelligence",
            "version": "0.86.0",
            "applicationVersion": "0.83.0",
        },
        "image": {
            "repository": "registry.example.test/iip/control-plane",
            "digest": "sha256:" + "1" * 64,
        },
        "api": {"replicaCount": 2},
        "worker": {"enabled": True, "replicaCount": 2, "tenantCount": 1},
        "database": {
            "existingSecretConfigured": True,
            "migrationsEnabled": True,
        },
        "authentication": {"mode": "oidc", "oidcConfigurationReviewed": True},
        "policy": {"mode": "external-http", "configurationReviewed": True},
        "credentialBroker": {
            "mode": "external-http",
            "configurationReviewed": True,
        },
        "ingress": {
            "enabled": True,
            "classConfigured": True,
            "hostConfigured": True,
            "tlsConfigured": True,
            "redirectConfigured": True,
        },
        "backup": {"enabled": True, "destinationConfigured": True},
        "evidenceRetention": {"enabled": True},
        "evidenceRedaction": {"customPoliciesConfigured": False},
        "evidenceBackends": {
            "metrics": "prometheus",
            "logs": "loki",
            "kubernetesEvents": "kubernetes-api",
            "context": "no-data",
        },
        "telemetry": {
            "metricsEnabled": True,
            "tracesEnabled": True,
            "endpointConfigured": True,
            "collectorQueueLossConfigured": False,
        },
        "networkPolicy": {
            "enabled": True,
            "ingressController": True,
            "databaseEgress": True,
            "otlpEgress": True,
            "prometheusEgress": True,
            "lokiEgress": True,
            "opensearchEgress": False,
            "contextEgress": False,
            "kubernetesApiEgress": True,
            "oidcEgress": True,
            "policyEgress": True,
            "credentialBrokerEgress": True,
            "otlpReceiverIngress": False,
        },
        "podDisruptionBudget": {
            "api": {"enabled": True, "minAvailable": 1},
            "worker": {"enabled": True, "minAvailable": 1},
            "receiver": {"enabled": False, "minAvailable": 1},
        },
        "topologySpread": {
            "enabled": True,
            "maxSkew": 1,
            "minDomains": 2,
            "hard": True,
        },
        "security": {
            "serviceAccountTokenAutomount": False,
            "runAsNonRoot": True,
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
        },
        "receivers": {
            "metricsEnabled": False,
            "logsEnabled": False,
            "aiUsageEnabled": False,
            "replicaCount": 1,
            "tlsMode": "disabled",
            "crlConfigured": False,
        },
        "aiEconomics": {
            "attributionEnabled": False,
            "costEngineEnabled": False,
            "savingsEngineEnabled": False,
            "allocationReportingEnabled": False,
            "attributionTestFixtures": False,
            "priceTestFixtures": False,
            "priceQualificationRequired": False,
            "savingsTestFixtures": False,
        },
        "dependencies": [
            {
                "purpose": "database",
                "kind": "Secret",
                "name": "customer-database-private",
                "keys": ["database-url"],
            },
            {
                "purpose": "backup-destination",
                "kind": "PersistentVolumeClaim",
                "name": "customer-backup-private",
                "keys": [],
            },
        ],
    }


class CustomerDeploymentPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.values = self.root / "customer-private.values.yaml"
        self.values.write_text(
            "credential: should-never-enter-the-report\n", encoding="utf-8"
        )
        self.output = self.root / "report.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def generate(
        self,
        profile: dict[str, object] | None = None,
        *,
        profile_name: str = "production-core-v1",
        live: bool = False,
        dependency_status: str = "observed",
        cluster_available: bool = True,
    ) -> dict:
        selected = copy.deepcopy(profile or rendered_profile())
        tools = {"helmVersion": "v4.1.3"}
        if live:
            tools["kubectlVersion"] = "v1.36.1"
        cluster = {
            "kubernetesVersion": "v1.36.1",
            "kubectlVersion": "v1.36.1",
            "clusterBindingDigest": "sha256:" + "2" * 64,
            "namespaceDigest": "sha256:" + "3" * 64,
        }
        cluster_result = (
            (cluster, {"id": "cluster-api", "status": "passed"})
            if cluster_available
            else (
                {},
                {
                    "id": "cluster-api",
                    "status": "failed",
                    "errorCode": "preflight.cluster.unavailable",
                },
            )
        )

        def observed(*args: object, **kwargs: object) -> tuple[str, dict | None]:
            dependency = kwargs["dependency"]
            assert isinstance(dependency, dict)
            if dependency_status == "missing":
                return "missing", None
            return dependency_status, {
                "kind": dependency["kind"],
                "uid": "f4a83430-97d3-4285-9d25-2bcb57485c64",
                "resourceVersion": "17",
                "expectedKeyDigest": "sha256:" + "4" * 64,
                "observedExpectedKeyDigest": "sha256:" + "4" * 64,
            }

        with patch(
            "deployment_preflight._git_state", return_value=(REVISION, False)
        ), patch(
            "deployment_preflight._profile_from_helm", return_value=selected
        ), patch(
            "deployment_preflight._tool_versions", return_value=tools
        ), patch(
            "deployment_preflight._cluster_environment",
            return_value=cluster_result,
        ), patch(
            "deployment_preflight._observe_dependency", side_effect=observed
        ):
            return dict(
                generate_report(
                    profile_name=profile_name,
                    values=(self.values,),
                    output=self.output,
                    namespace="iip-private",
                    release_name="iip",
                    context="customer-private-context" if live else None,
                    live=live,
                )
            )

    def test_static_core_profile_is_configuration_ready_and_minimized(self) -> None:
        report = self.generate()

        self.assertEqual(report["spec"]["status"], "configuration-ready")
        self.assertEqual(
            tuple(check["id"] for check in report["spec"]["checks"]),
            COMMON_CHECKS + LIVE_CHECKS,
        )
        self.assertEqual(report["spec"]["summary"]["notRunChecks"], 2)
        self.assertEqual(
            report["spec"]["dependencies"]["verificationStatus"], "not-run"
        )
        serialized = json.dumps(report, sort_keys=True)
        for forbidden in (
            "should-never-enter-the-report",
            "customer-private.values.yaml",
            "customer-private-context",
            "iip-private",
            "customer-database-private",
            "customer-backup-private",
            "database-url",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(
            CustomerDeploymentPreflightReport.from_dict(report).to_dict(), report
        )

    def test_live_profile_is_install_ready_only_after_exact_dependencies(self) -> None:
        report = self.generate(live=True)

        self.assertEqual(report["spec"]["status"], "install-ready")
        self.assertEqual(report["spec"]["environment"]["mode"], "cluster")
        self.assertEqual(report["spec"]["dependencies"]["observedCount"], 2)
        self.assertEqual(report["spec"]["dependencies"]["unavailableCount"], 0)
        self.assertEqual(
            report["spec"]["dependencies"]["verificationStatus"], "passed"
        )
        self.assertEqual(report["spec"]["summary"]["notRunChecks"], 0)

    def test_missing_dependency_blocks_cluster_profile_without_identity_leak(self) -> None:
        report = self.generate(live=True, dependency_status="missing")

        self.assertEqual(report["spec"]["status"], "blocked")
        self.assertEqual(report["spec"]["dependencies"]["missingCount"], 2)
        self.assertEqual(
            report["spec"]["checks"][-1],
            {
                "id": "referenced-dependencies",
                "status": "failed",
                "errorCode": "preflight.dependencies.incomplete",
            },
        )

    def test_inaccessible_dependency_is_separate_from_missing(self) -> None:
        report = self.generate(live=True, dependency_status="unavailable")

        self.assertEqual(report["spec"]["status"], "blocked")
        self.assertEqual(report["spec"]["dependencies"]["missingCount"], 0)
        self.assertEqual(report["spec"]["dependencies"]["unavailableCount"], 2)
        self.assertEqual(report["spec"]["dependencies"]["observedCount"], 0)

    def test_unavailable_cluster_writes_a_valid_minimized_blocked_report(self) -> None:
        report = self.generate(live=True, cluster_available=False)

        self.assertEqual(report["spec"]["status"], "blocked")
        self.assertEqual(
            report["spec"]["checks"][-2],
            {
                "id": "cluster-api",
                "status": "failed",
                "errorCode": "preflight.cluster.unavailable",
            },
        )
        self.assertEqual(
            report["spec"]["dependencies"]["verificationStatus"], "not-run"
        )
        self.assertNotIn("clusterBindingDigest", report["spec"]["environment"])
        self.assertEqual(
            json.loads(self.output.read_text(encoding="utf-8")), report
        )

    def test_ai_profile_requires_the_complete_receiver_and_cost_path(self) -> None:
        profile = rendered_profile()
        profile["receivers"] = {
            "metricsEnabled": False,
            "logsEnabled": False,
            "aiUsageEnabled": True,
            "replicaCount": 2,
            "tlsMode": "mutual-spiffe",
            "crlConfigured": True,
        }
        profile["podDisruptionBudget"]["receiver"]["enabled"] = True  # type: ignore[index]
        profile["aiEconomics"] = {
            "attributionEnabled": True,
            "costEngineEnabled": True,
            "savingsEngineEnabled": True,
            "allocationReportingEnabled": True,
            "attributionTestFixtures": False,
            "priceTestFixtures": False,
            "priceQualificationRequired": True,
            "savingsTestFixtures": False,
        }
        profile["telemetry"]["collectorQueueLossConfigured"] = True  # type: ignore[index]
        profile["networkPolicy"]["otlpReceiverIngress"] = True  # type: ignore[index]

        report = self.generate(profile, profile_name="production-ai-finops-v0")

        self.assertEqual(report["spec"]["status"], "configuration-ready")
        self.assertEqual(report["spec"]["summary"]["totalChecks"], 29)
        self.assertEqual(
            report["spec"]["customerQualificationRequired"][-3:],
            [
                "live-bedrock-model-region-streaming",
                "authoritative-ai-price-catalog",
                "ai-workload-saving-validation",
            ],
        )

    def test_static_unsafe_profile_is_blocked_with_stable_code(self) -> None:
        profile = rendered_profile()
        profile["image"]["digest"] = ""  # type: ignore[index]

        report = self.generate(profile)

        self.assertEqual(report["spec"]["status"], "blocked")
        self.assertEqual(
            report["spec"]["checks"][1],
            {
                "id": "immutable-image",
                "status": "failed",
                "errorCode": "preflight.image.digest-required",
            },
        )

    def test_every_enabled_workload_requires_a_usable_disruption_budget(self) -> None:
        for component in ("api", "worker"):
            with self.subTest(component=component):
                profile = rendered_profile()
                profile["podDisruptionBudget"][component]["enabled"] = False  # type: ignore[index]

                report = self.generate(profile)

                self.assertEqual(report["spec"]["status"], "blocked")
                self.assertEqual(
                    next(
                        check
                        for check in report["spec"]["checks"]
                        if check["id"] == "pod-disruption-budget"
                    ),
                    {
                        "id": "pod-disruption-budget",
                        "status": "failed",
                        "errorCode": "preflight.pdb.required",
                    },
                )

        profile = rendered_profile()
        profile["podDisruptionBudget"]["api"]["minAvailable"] = 2  # type: ignore[index]
        report = self.generate(profile)
        self.assertEqual(report["spec"]["status"], "blocked")

    def test_receiver_budget_is_required_only_when_intake_is_enabled(self) -> None:
        profile = rendered_profile()
        report = self.generate(profile)
        self.assertEqual(report["spec"]["status"], "configuration-ready")

        profile["receivers"]["metricsEnabled"] = True  # type: ignore[index]
        report = self.generate(profile)
        self.assertEqual(report["spec"]["status"], "blocked")
        failed = {
            check["id"]: check.get("errorCode")
            for check in report["spec"]["checks"]
            if check["status"] == "failed"
        }
        self.assertEqual(failed, {"pod-disruption-budget": "preflight.pdb.required"})

    def test_topology_spread_must_be_hard_and_single_skew(self) -> None:
        for topology in (
            {"enabled": False, "maxSkew": 1, "minDomains": 2, "hard": True},
            {"enabled": True, "maxSkew": 2, "minDomains": 2, "hard": True},
            {"enabled": True, "maxSkew": 1, "minDomains": 1, "hard": True},
            {"enabled": True, "maxSkew": 1, "minDomains": 2, "hard": False},
        ):
            with self.subTest(topology=topology):
                profile = rendered_profile()
                profile["topologySpread"] = topology

                report = self.generate(profile)

                self.assertEqual(report["spec"]["status"], "blocked")
                failed = next(
                    check
                    for check in report["spec"]["checks"]
                    if check["id"] == "hard-topology-spread"
                )
                self.assertEqual(
                    failed["errorCode"], "preflight.topology-spread.required"
                )

    def test_semantic_verifier_rejects_reordered_or_rewritten_report(self) -> None:
        report = self.generate()
        reordered = copy.deepcopy(report)
        reordered["spec"]["checks"][0], reordered["spec"]["checks"][1] = (
            reordered["spec"]["checks"][1],
            reordered["spec"]["checks"][0],
        )
        with self.assertRaisesRegex(
            DeploymentPreflightError, "preflight.report.invalid"
        ):
            validate_report_document(reordered)

        rewritten = copy.deepcopy(report)
        rewritten["spec"]["summary"]["passedChecks"] -= 1
        with self.assertRaisesRegex(
            DeploymentPreflightError, "preflight.report.invalid"
        ):
            validate_report_document(rewritten)

    def test_offline_verifier_binds_source_values_and_configuration(self) -> None:
        report = self.generate()
        profile = rendered_profile()
        with patch(
            "deployment_preflight._git_state", return_value=(REVISION, False)
        ), patch(
            "deployment_preflight._profile_from_helm", return_value=profile
        ):
            verified = verify_report(
                self.output,
                values=(self.values,),
                namespace="iip-private",
                release_name="iip",
                require_clean=True,
            )
            self.assertEqual(verified["metadata"]["sourceRevision"], REVISION)
            with self.assertRaisesRegex(
                DeploymentPreflightError, "preflight.report.install-ready-required"
            ):
                verify_report(
                    self.output,
                    values=(self.values,),
                    namespace="iip-private",
                    release_name="iip",
                    require_install_ready=True,
                )

        self.values.write_text("changed: true\n", encoding="utf-8")
        with patch(
            "deployment_preflight._git_state", return_value=(REVISION, False)
        ), patch(
            "deployment_preflight._profile_from_helm", return_value=profile
        ), self.assertRaisesRegex(
            DeploymentPreflightError, "preflight.report.configuration-mismatch"
        ):
            verify_report(
                self.output,
                values=(self.values,),
                namespace="iip-private",
                release_name="iip",
            )

    def test_offline_verifier_recomputes_static_profile_checks(self) -> None:
        profile = rendered_profile()
        profile["image"]["digest"] = ""  # type: ignore[index]
        report = self.generate(profile)
        forged = copy.deepcopy(report)
        forged["spec"]["checks"][1] = {
            "id": "immutable-image",
            "status": "passed",
        }
        forged["spec"]["status"] = "configuration-ready"
        forged["spec"]["summary"] = {
            "totalChecks": 23,
            "passedChecks": 21,
            "failedChecks": 0,
            "notRunChecks": 2,
            "overallStatus": "configuration-ready",
        }
        forged["metadata"]["id"] = _report_identifier(
            source_revision=forged["metadata"]["sourceRevision"],
            source_dirty=forged["metadata"]["sourceDirty"],
            profile=forged["spec"]["profile"],
            environment=forged["spec"]["environment"],
            dependencies=forged["spec"]["dependencies"],
            checks=forged["spec"]["checks"],
        )
        self.output.write_text(json.dumps(forged), encoding="utf-8")

        with patch(
            "deployment_preflight._git_state", return_value=(REVISION, False)
        ), patch(
            "deployment_preflight._profile_from_helm", return_value=profile
        ), self.assertRaisesRegex(
            DeploymentPreflightError,
            "preflight.report.configuration-mismatch",
        ):
            verify_report(
                self.output,
                values=(self.values,),
                namespace="iip-private",
                release_name="iip",
            )

    def test_secret_observation_projects_keys_not_values(self) -> None:
        dependency = {
            "purpose": "database",
            "kind": "Secret",
            "name": "database-private",
            "keys": ["database-url"],
        }
        observed_command: list[str] = []

        def fake_run(command: tuple[str, ...], **kwargs: object) -> str:
            observed_command.extend(command)
            return "uid-1\n19\ndatabase-url\nunrelated-key\n"

        with patch("deployment_preflight._run", side_effect=fake_run):
            status, observation = _observe_dependency(
                "kubectl",
                context="explicit-context",
                namespace="iip-private",
                dependency=dependency,
            )

        self.assertEqual(status, "observed")
        self.assertIsNotNone(observation)
        command = " ".join(observed_command)
        self.assertIn("go-template=", command)
        self.assertIn("--ignore-not-found", command)
        self.assertIn("{{$k}}", command)
        self.assertNotIn("{{$v}}", command)
        self.assertNotIn("-o json ", command)

    def test_dependency_access_failure_is_not_reported_as_missing(self) -> None:
        dependency = {
            "purpose": "database",
            "kind": "Secret",
            "name": "database-private",
            "keys": ["database-url"],
        }

        with patch(
            "deployment_preflight._run",
            side_effect=DeploymentPreflightError("preflight.command.failed"),
        ):
            status, observation = _observe_dependency(
                "kubectl",
                context="explicit-context",
                namespace="iip-private",
                dependency=dependency,
            )

        self.assertEqual(status, "unavailable")
        self.assertIsNone(observation)

    def test_contract_example_is_semantically_valid(self) -> None:
        report = json.loads(
            (
                ROOT
                / "contracts"
                / "examples"
                / "customer-deployment-preflight-report.json"
            ).read_text(encoding="utf-8")
        )
        validate_report_document(report)


if __name__ == "__main__":
    unittest.main()
