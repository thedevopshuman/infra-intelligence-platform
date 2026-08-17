from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy" / "helm" / "infra-intelligence"


class HelmMigrationBoundaryTests(unittest.TestCase):
    def test_every_application_workload_uses_one_digest_aware_image_helper(self) -> None:
        for name in (
            "deployment.yaml",
            "worker-deployment.yaml",
            "otlp-receiver-deployment.yaml",
            "migration-job.yaml",
        ):
            with self.subTest(template=name):
                template = (CHART / "templates" / name).read_text(encoding="utf-8")
                self.assertIn('include "infra-intelligence.image"', template)
                self.assertNotIn(".Values.image.repository }}:", template)

        helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
        self.assertIn('printf "%s@%s" .Values.image.repository .Values.image.digest', helpers)
        self.assertIn(".Values.image.tag | default .Chart.AppVersion", helpers)

    def test_scheduled_backup_has_database_and_pvc_authority_only(self) -> None:
        cronjob = (CHART / "templates" / "backup-cronjob.yaml").read_text(
            encoding="utf-8"
        )
        script = (CHART / "templates" / "backup-configmap.yaml").read_text(
            encoding="utf-8"
        )
        network_policy = (
            CHART / "templates" / "backup-networkpolicy.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("kind: CronJob", cronjob)
        self.assertIn("concurrencyPolicy: Forbid", cronjob)
        self.assertIn("automountServiceAccountToken: false", cronjob)
        self.assertIn("runAsUser: 70", cronjob)
        self.assertIn("runAsGroup: 70", cronjob)
        self.assertIn("@{{ .Values.backup.image.digest }}", cronjob)
        self.assertIn("database.existingSecret", cronjob)
        self.assertIn("backup.destination.existingClaim", cronjob)
        self.assertIn("readOnly: true", cronjob)
        for forbidden in ("IIP_AUTH_", "IIP_POLICY_", "serviceAccountName:"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, cronjob)
        self.assertIn("umask 077", script)
        self.assertIn("unset IIP_DATABASE_URL", script)
        self.assertIn('pg_dump --dbname="$database_url" --format=custom', script)
        self.assertIn("pg_restore --list", script)
        self.assertLess(
            script.index('mv "$partial" "$base"'),
            script.index('mv ".${checksum}.partial" "$checksum"'),
        )
        self.assertIn("ingress: []", network_policy)
        self.assertIn("networkPolicy.databaseEgress", network_policy)

    def test_ingress_requires_tls_redirect_and_exact_controller_ingress(self) -> None:
        ingress = (CHART / "templates" / "ingress.yaml").read_text(encoding="utf-8")
        validation = (CHART / "templates" / "validation.yaml").read_text(
            encoding="utf-8"
        )
        network_policy = (CHART / "templates" / "networkpolicy.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("ingressClassName", ingress)
        self.assertIn("tls.existingSecret", ingress)
        self.assertIn("tlsRedirectAnnotation", ingress)
        self.assertIn("service.port", ingress)
        for required in (
            "ingress.tls.existingSecret is required",
            "ingress.tlsRedirectAnnotation is required",
            "networkPolicy.enabled must be true when ingress is enabled",
            "networkPolicy.ingressController.enabled must be true",
            "networkPolicy.ingressController selectors must be explicit",
        ):
            with self.subTest(required=required):
                self.assertIn(required, validation)
        self.assertIn("networkPolicy.ingressController.namespaceSelector", network_policy)
        self.assertIn("networkPolicy.ingressController.podSelector", network_policy)

    def test_serving_pods_cannot_enable_automatic_migrations(self) -> None:
        config_map = (CHART / "templates" / "configmap.yaml").read_text(
            encoding="utf-8"
        )
        values = (CHART / "values.yaml").read_text(encoding="utf-8")

        self.assertIn('IIP_DATABASE_AUTO_MIGRATE: "false"', config_map)
        self.assertNotIn("autoMigrate:", values)

    def test_oidc_console_profile_flows_only_through_reviewed_configuration(self) -> None:
        config_map = (CHART / "templates" / "configmap.yaml").read_text(
            encoding="utf-8"
        )
        deployment = (CHART / "templates" / "deployment.yaml").read_text(
            encoding="utf-8"
        )
        guide = (ROOT / "docs" / "operations" / "helm-deployment.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("IIP_AUTH_OIDC_CONFIG_JSON", config_map)
        self.assertIn("auth.oidc.configJson", config_map)
        self.assertIn("auth.oidc.caBundleExistingSecret", deployment)
        self.assertIn("Authorization Code", guide)
        self.assertIn("S256", guide)
        self.assertIn("CORS policy", guide)
        self.assertNotIn("clientSecret", guide)

    def test_protected_signal_catalog_is_secret_backed_for_api_and_worker(self) -> None:
        deployment = (CHART / "templates" / "deployment.yaml").read_text(
            encoding="utf-8"
        )
        worker = (CHART / "templates" / "worker-deployment.yaml").read_text(
            encoding="utf-8"
        )
        values = (CHART / "values.yaml").read_text(encoding="utf-8")

        self.assertIn("investigationSignalCatalog:", values)
        for template in (deployment, worker):
            self.assertIn("IIP_INVESTIGATION_SIGNAL_CATALOG_JSON", template)
            self.assertIn("investigationSignalCatalog.existingSecret", template)
            self.assertIn("investigationSignalCatalog.secretKey", template)

    def test_event_delivery_retry_budget_is_explicit(self) -> None:
        worker = (CHART / "templates" / "worker-deployment.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("IIP_OUTBOX_MAX_ATTEMPTS", worker)
        self.assertIn(".Values.eventPublisher.maxAttempts", worker)

    def test_worker_investigation_concurrency_is_explicit_and_bounded(self) -> None:
        worker = (CHART / "templates" / "worker-deployment.yaml").read_text(
            encoding="utf-8"
        )
        values = (CHART / "values.yaml").read_text(encoding="utf-8")

        self.assertIn("investigationConcurrency: 4", values)
        self.assertIn("maxTenantInvestigationConcurrency: 1", values)
        self.assertIn("IIP_WORKER_INVESTIGATION_CONCURRENCY", worker)
        self.assertIn("IIP_WORKER_MAX_TENANT_CONCURRENCY", worker)

    def test_event_delivery_slo_objective_is_explicit_and_api_visible(self) -> None:
        config_map = (CHART / "templates" / "configmap.yaml").read_text(
            encoding="utf-8"
        )
        values = (CHART / "values.yaml").read_text(encoding="utf-8")

        self.assertIn("eventDeliverySlo:", values)
        for variable in (
            "IIP_EVENT_DELIVERY_SLO_WINDOW_SECONDS",
            "IIP_EVENT_DELIVERY_SLO_MAXIMUM_LATENCY_SECONDS",
            "IIP_EVENT_DELIVERY_SLO_MINIMUM_ATTAINMENT_BASIS_POINTS",
            "IIP_EVENT_DELIVERY_SLO_MINIMUM_ELIGIBLE_EVENTS",
        ):
            with self.subTest(variable=variable):
                self.assertIn(variable, config_map)

    def test_investigation_completion_slo_is_explicit_and_api_visible(self) -> None:
        config_map = (CHART / "templates" / "configmap.yaml").read_text(
            encoding="utf-8"
        )
        values = (CHART / "values.yaml").read_text(encoding="utf-8")

        self.assertIn("investigationCompletionSlo:", values)
        for variable in (
            "IIP_INVESTIGATION_COMPLETION_SLO_WINDOW_SECONDS",
            "IIP_INVESTIGATION_COMPLETION_SLO_MAXIMUM_SECONDS",
            "IIP_INVESTIGATION_COMPLETION_SLO_MINIMUM_ATTAINMENT_BASIS_POINTS",
            "IIP_INVESTIGATION_COMPLETION_SLO_MINIMUM_ELIGIBLE_JOBS",
        ):
            with self.subTest(variable=variable):
                self.assertIn(variable, config_map)

    def test_investigation_queue_admission_limit_is_explicit(self) -> None:
        config_map = (CHART / "templates" / "configmap.yaml").read_text(
            encoding="utf-8"
        )
        values = (CHART / "values.yaml").read_text(encoding="utf-8")

        self.assertIn("investigationQueue:", values)
        self.assertIn("maxOutstandingJobsPerTenant: 1000", values)
        self.assertIn(
            "IIP_INVESTIGATION_MAX_OUTSTANDING_JOBS_PER_TENANT",
            config_map,
        )

    def test_evidence_retention_is_disabled_explicit_bounded_and_worker_owned(self) -> None:
        config_map = (CHART / "templates" / "configmap.yaml").read_text(
            encoding="utf-8"
        )
        values = (CHART / "values.yaml").read_text(encoding="utf-8")
        validation = (CHART / "templates" / "validation.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("evidenceRetention:", values)
        self.assertIn("  enabled: false", values)
        for variable in (
            "IIP_EVIDENCE_RETENTION_ENABLED",
            "IIP_EVIDENCE_RETENTION_INTERVAL_SECONDS",
            "IIP_EVIDENCE_RETENTION_EPHEMERAL_SECONDS",
            "IIP_EVIDENCE_RETENTION_STANDARD_SECONDS",
            "IIP_EVIDENCE_RETENTION_EXTENDED_SECONDS",
            "IIP_EVIDENCE_RETENTION_BATCH_SIZE",
        ):
            with self.subTest(variable=variable):
                self.assertIn(variable, config_map)
        for value in (
            "intervalSeconds",
            "ephemeralSeconds",
            "standardSeconds",
            "extendedSeconds",
            "batchSize",
        ):
            with self.subTest(value=value):
                self.assertIn(
                    f".Values.evidenceRetention.{value} | int64 | quote",
                    config_map,
                )
        self.assertIn(
            "worker.enabled must be true when evidenceRetention.enabled=true",
            validation,
        )

    def test_query_availability_objective_is_explicit_and_otel_visible(self) -> None:
        config_map = (CHART / "templates" / "configmap.yaml").read_text(
            encoding="utf-8"
        )
        values = (CHART / "values.yaml").read_text(encoding="utf-8")

        self.assertIn("queryAvailabilitySlo:", values)
        for variable in (
            "IIP_QUERY_AVAILABILITY_SLO_WINDOW_SECONDS",
            "IIP_QUERY_AVAILABILITY_SLO_MINIMUM_BASIS_POINTS",
            "IIP_QUERY_AVAILABILITY_SLO_MINIMUM_ELIGIBLE_REQUESTS",
        ):
            with self.subTest(variable=variable):
                self.assertIn(variable, config_map)

    def test_deployment_export_health_heartbeat_is_explicit_and_bounded(self) -> None:
        config_map = (CHART / "templates" / "configmap.yaml").read_text(
            encoding="utf-8"
        )
        values = (CHART / "values.yaml").read_text(encoding="utf-8")
        validation = (CHART / "templates" / "validation.yaml").read_text(
            encoding="utf-8"
        )

        for setting in (
            "healthReportIntervalSeconds: 30",
            "healthStaleAfterSeconds: 120",
            "healthRetentionSeconds: 600",
        ):
            self.assertIn(setting, values)
        for variable in (
            "IIP_TELEMETRY_HEALTH_INTERVAL_SECONDS",
            "IIP_TELEMETRY_HEALTH_STALE_AFTER_SECONDS",
            "IIP_TELEMETRY_HEALTH_RETENTION_SECONDS",
        ):
            self.assertIn(variable, config_map)
        self.assertIn(
            "telemetry.healthStaleAfterSeconds must be at least twice",
            validation,
        )
        self.assertIn(
            "telemetry.healthRetentionSeconds must be at least twice",
            validation,
        )

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
        self.assertIn("IIP_AUTH_BEARER_TOKEN=$(openssl rand -hex 32)", script)
        self.assertIn('\\"developer\\",\\"platform-admin\\"', script)
        self.assertIn("openssl dgst -sha256 -hex", script)
        self.assertIn("awk '{print $NF}'", script)
        self.assertIn("build --provenance=false", script)
        self.assertIn('IIP_IMAGE_VERSION=$IIP_APP_VERSION', script)
        self.assertIn("IIP_IMAGE_REVISION=development", script)
        self.assertIn("ctr -n k8s.io images tag --force", script)
        self.assertIn('basename "$IIP_EXPECTED_MIGRATION"', script)
        self.assertNotIn('basename "$IIP_EXPECTED_MIGRATION" .sql', script)
        self.assertIn("/v1/authentication/console", script)
        self.assertIn('"kind":"ConsoleAuthenticationConfiguration"', script)
        self.assertIn("/v1/system/version", script)
        self.assertIn(
            "/v1/operations/telemetry/deployment-export-health", script
        )
        self.assertIn(
            'document["kind"] == "TelemetryDeploymentExportHealthReport"',
            script,
        )
        self.assertIn("/v1/operations/events/delivery-health?limit=10", script)
        self.assertIn('document["kind"] == "EventDeliveryHealthReport"', script)
        self.assertIn("/v1/operations/events/delivery-slo", script)
        self.assertIn('document["kind"] == "EventDeliverySloReport"', script)
        self.assertIn(
            "/v1/operations/investigations/completion-slo",
            script,
        )
        self.assertIn(
            'document["kind"] == "InvestigationCompletionSloReport"',
            script,
        )
        self.assertIn('printf \'%s\' "$IIP_AUTH_BEARER_TOKEN"', script)
        self.assertIn('"helmChartVersion":chart,"imageDigest":digest', script)
        self.assertEqual(script.count('"$IIP_HELM_BIN" upgrade --install iip'), 2)
        self.assertIn("--set replicaCount=2", script)
        self.assertEqual(script.count('--set-string "image.digest=$IIP_TEST_IMAGE_DIGEST"'), 2)
        self.assertIn(
            '--from-file="investigation-signal-catalog-json=contracts/examples/investigation-signal-catalog.json"',
            script,
        )
        self.assertEqual(
            script.count(
                "--set investigationSignalCatalog.existingSecret="
                "iip-investigation-signal-catalog"
            ),
            2,
        )
        self.assertIn("iip-local-platform@$IIP_TEST_IMAGE_DIGEST", script)
        self.assertIn("--set ingress.tls.existingSecret=iip-tls", script)
        self.assertIn("--set backup.destination.existingClaim=iip-backups", script)
        self.assertIn("--from=cronjob/iip-infra-intelligence-backup", script)
        self.assertIn("pg_restore --no-owner --no-acl", script)
        self.assertIn("SELECT count(*) FROM iip.schema_migrations", script)
        self.assertIn('"$IIP_HELM_BIN" history iip', script)
        self.assertTrue((ROOT / "scripts" / "test_helm_install.sh").stat().st_mode & 0o111)


if __name__ == "__main__":
    unittest.main()
