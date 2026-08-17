from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy" / "helm" / "infra-intelligence"


class HelmMigrationBoundaryTests(unittest.TestCase):
    def test_serving_pods_cannot_enable_automatic_migrations(self) -> None:
        config_map = (CHART / "templates" / "configmap.yaml").read_text(
            encoding="utf-8"
        )
        values = (CHART / "values.yaml").read_text(encoding="utf-8")

        self.assertIn('IIP_DATABASE_AUTO_MIGRATE: "false"', config_map)
        self.assertNotIn("autoMigrate:", values)

    def test_migration_hook_has_database_only_authority(self) -> None:
        template = (CHART / "templates" / "migration-job.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn('"helm.sh/hook": pre-install,pre-upgrade', template)
        self.assertIn('command: ["python", "-m", "iip.adapters.postgres"]', template)
        self.assertIn("automountServiceAccountToken: false", template)
        self.assertIn("IIP_DATABASE_URL", template)
        for forbidden in (
            "IIP_AUTH_",
            "IIP_POLICY_",
            "IIP_CREDENTIAL_BROKER_",
            "IIP_EVENT_PUBLISHER_",
            "IIP_OTEL_",
            "serviceAccountName:",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, template)

    def test_migration_network_policy_precedes_job_and_denies_ingress(self) -> None:
        template = (
            CHART / "templates" / "migration-networkpolicy.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn('"helm.sh/hook-weight": "-10"', template)
        self.assertIn("app.kubernetes.io/component: database-migration", template)
        self.assertIn("ingress: []", template)
        self.assertIn("networkPolicy.databaseEgress", template)

    def test_install_gate_is_pinned_to_a_disposable_kind_context(self) -> None:
        script = (ROOT / "scripts" / "test_helm_install.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("kind-*", script)
        self.assertIn("iip-helm-install-test", script)
        self.assertIn("--context \"$IIP_KUBE_CONTEXT\"", script)
        self.assertNotIn("current-context", script)
        self.assertNotIn("echo \"$IIP_DB_PASSWORD\"", script)
        self.assertIn('IIP_AUTH_VERIFIER="sha256:$(openssl rand -hex 32)"', script)
        self.assertIn('basename "$IIP_EXPECTED_MIGRATION"', script)
        self.assertNotIn('basename "$IIP_EXPECTED_MIGRATION" .sql', script)
        self.assertTrue((ROOT / "scripts" / "test_helm_install.sh").stat().st_mode & 0o111)


if __name__ == "__main__":
    unittest.main()
