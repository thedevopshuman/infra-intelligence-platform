from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from iip.application.observe_ingestion import (
    IngestionFreshnessReport,
    IngestionSourceNotFoundError,
    IngestionTelemetryAuthorizationError,
    IngestionTelemetryStateError,
)
from iip.application.sample_ingestion import (
    IngestionFreshnessSampler,
    IngestionMonitorTarget,
)
from iip.bootstrap import (
    build_ingestion_freshness_sampler_from_env,
    build_local_runtime,
    build_workflow_worker_runtime_from_env,
)
from iip.surfaces.worker import configured_tenants, monitor_interval_seconds


ROOT = Path(__file__).resolve().parents[1]


class _Service:
    def __init__(self) -> None:
        self.commands = []

    def get(self, command):
        self.commands.append(command)
        if command.source_id == "source-missing":
            raise IngestionSourceNotFoundError
        if command.source_id == "source-denied":
            raise IngestionTelemetryAuthorizationError
        if command.source_id == "source-failed":
            raise IngestionTelemetryStateError
        return IngestionFreshnessReport(
            {
                "spec": {
                    "status": (
                        "breached"
                        if command.source_id == "source-breached"
                        else "within-objective"
                    )
                }
            }
        )


class IngestionFreshnessSamplerTests(unittest.TestCase):
    def test_one_failure_cannot_block_other_explicit_targets(self) -> None:
        service = _Service()
        sampler = IngestionFreshnessSampler(
            service,
            tuple(
                IngestionMonitorTarget("tenant-a", source)
                for source in (
                    "source-healthy",
                    "source-breached",
                    "source-missing",
                    "source-denied",
                    "source-failed",
                )
            ),
        )

        summary = sampler.run_once()

        self.assertEqual(
            (
                summary.sampled,
                summary.breached,
                summary.missing,
                summary.denied,
                summary.failed,
            ),
            (2, 1, 1, 1, 1),
        )
        self.assertEqual(
            {command.actor.actor_id for command in service.commands},
            {"iip-ingestion-monitor"},
        )
        self.assertEqual(
            {command.actor.tenant_id for command in service.commands},
            {"tenant-a"},
        )
        self.assertEqual(
            {command.actor.roles for command in service.commands},
            {("system-monitor",)},
        )

    def test_targets_are_closed_bounded_and_unique(self) -> None:
        service = _Service()
        for targets in (
            (),
            (IngestionMonitorTarget("bad tenant", "source-ok"),),
            (IngestionMonitorTarget("tenant-a", "BAD"),),
            (
                IngestionMonitorTarget("tenant-a", "source-ok"),
                IngestionMonitorTarget("tenant-a", "source-ok"),
            ),
        ):
            with self.subTest(targets=targets):
                with self.assertRaisesRegex(
                    ValueError, "ingestion.monitor.configuration.invalid"
                ):
                    IngestionFreshnessSampler(service, targets)


class IngestionMonitorCompositionTests(unittest.TestCase):
    def test_monitor_configuration_must_stay_inside_worker_tenants(self) -> None:
        runtime = build_local_runtime()
        self.addCleanup(runtime.close)
        valid = {
            "targets": [
                {"tenantId": "tenant-a", "sourceId": "kubernetes-prod"}
            ]
        }
        with patch.dict(
            os.environ,
            {"IIP_INGESTION_MONITOR_TARGETS_JSON": json.dumps(valid)},
            clear=True,
        ):
            self.assertIsNotNone(
                build_ingestion_freshness_sampler_from_env(runtime, ("tenant-a",))
            )
            with self.assertRaisesRegex(
                ValueError, "ingestion.monitor.configuration.invalid"
            ):
                build_ingestion_freshness_sampler_from_env(runtime, ("tenant-b",))

    def test_unknown_fields_empty_targets_and_bad_json_fail_closed(self) -> None:
        runtime = build_local_runtime()
        self.addCleanup(runtime.close)
        invalid = (
            "not-json",
            '{"targets":[]}',
            '{"targets":[{"tenantId":"tenant-a","sourceId":"source-ok","extra":true}]}',
            '{"targets":[{"tenantId":"tenant-a","sourceId":"BAD"}]}',
        )
        for configuration in invalid:
            with self.subTest(configuration=configuration):
                with patch.dict(
                    os.environ,
                    {"IIP_INGESTION_MONITOR_TARGETS_JSON": configuration},
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        ValueError, "ingestion.monitor.configuration.invalid"
                    ):
                        build_ingestion_freshness_sampler_from_env(
                            runtime, ("tenant-a",)
                        )

    def test_worker_runtime_never_loads_interactive_authentication(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "iip.bootstrap._authenticator_from_env",
                side_effect=AssertionError("interactive auth must not load"),
            ),
        ):
            runtime = build_workflow_worker_runtime_from_env()
        self.addCleanup(runtime.close)

    def test_worker_tenants_and_interval_are_strict(self) -> None:
        with patch.dict(
            os.environ,
            {
                "IIP_WORKER_TENANTS": "tenant-a,tenant-b",
                "IIP_INGESTION_MONITOR_INTERVAL_SECONDS": "30",
            },
            clear=True,
        ):
            self.assertEqual(configured_tenants(), ("tenant-a", "tenant-b"))
            self.assertEqual(monitor_interval_seconds(), 30)
        for interval in ("bad", "4", "3601"):
            with patch.dict(
                os.environ,
                {"IIP_INGESTION_MONITOR_INTERVAL_SECONDS": interval},
                clear=True,
            ):
                with self.assertRaisesRegex(
                    ValueError, "ingestion.monitor.configuration.invalid"
                ):
                    monitor_interval_seconds()

    def test_worker_manifest_omits_interactive_identity_material(self) -> None:
        template = (
            ROOT
            / "deploy"
            / "helm"
            / "infra-intelligence"
            / "templates"
            / "worker-deployment.yaml"
        ).read_text(encoding="utf-8")

        self.assertNotIn("IIP_AUTH_IDENTITIES_JSON", template)
        self.assertNotIn("oidc-ca", template)
        self.assertIn("automountServiceAccountToken: false", template)


if __name__ == "__main__":
    unittest.main()
