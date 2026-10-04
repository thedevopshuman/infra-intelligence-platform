from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from iip.application.ports import (
    ActorContext,
    PolicyDecision,
    ProjectionRebuildResult,
)
from iip.application.rebuild_projections import (
    ProjectionRebuildAuthorizationError,
    ProjectionRebuildInputError,
    ProjectionRebuildService,
    RebuildProjectionsCommand,
)
from iip.surfaces.maintenance import main as maintenance_main


def rebuild_result(*, dry_run: bool) -> ProjectionRebuildResult:
    return ProjectionRebuildResult(
        tenant_id="local",
        dry_run=dry_run,
        drift_detected=True,
        rebuild_performed=not dry_run,
        resource_count=2,
        relationship_count=1,
        latest_observation_offset=7,
        before_digest="sha256:" + "1" * 64,
        expected_digest="sha256:" + "2" * 64,
        after_digest="sha256:" + ("1" * 64 if dry_run else "2" * 64),
    )


class RecordingMaintenance:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, int]] = []

    def rebuild_projections(
        self,
        tenant_id: str,
        *,
        dry_run: bool,
        max_resources: int,
    ) -> ProjectionRebuildResult:
        self.calls.append((tenant_id, dry_run, max_resources))
        return rebuild_result(dry_run=dry_run)


class RecordingPolicy:
    def __init__(self, decision: PolicyDecision) -> None:
        self.decision = decision
        self.calls: list[tuple[ActorContext, str, dict[str, object]]] = []

    def decide(
        self,
        actor: ActorContext,
        action: str,
        resource: dict[str, object],
    ) -> PolicyDecision:
        self.calls.append((actor, action, resource))
        return self.decision


class ProjectionRebuildServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.maintenance = RecordingMaintenance()
        self.policy = RecordingPolicy(PolicyDecision(True, "test.allow"))
        self.service = ProjectionRebuildService(self.maintenance, self.policy)
        self.actor = ActorContext(
            actor_id="operator",
            tenant_id="local",
            roles=("platform-admin",),
        )

    def test_privileged_dry_run_is_tenant_scoped_and_bounded(self) -> None:
        result = self.service.execute(
            RebuildProjectionsCommand(self.actor, dry_run=True, max_resources=250)
        )

        self.assertTrue(result.dry_run)
        self.assertEqual(self.maintenance.calls, [("local", True, 250)])
        self.assertEqual(self.policy.calls[0][1], "resource-projection:rebuild")
        self.assertEqual(
            self.policy.calls[0][2],
            {"tenantId": "local", "dryRun": True},
        )

    def test_platform_admin_role_and_policy_are_both_required(self) -> None:
        with self.assertRaisesRegex(
            ProjectionRebuildAuthorizationError,
            "projection.rebuild.role_required",
        ):
            self.service.execute(
                RebuildProjectionsCommand(ActorContext("developer", "local"))
            )
        self.assertEqual(self.policy.calls, [])
        self.assertEqual(self.maintenance.calls, [])

        denied = ProjectionRebuildService(
            self.maintenance,
            RecordingPolicy(PolicyDecision(False, "policy.denied")),
        )
        with self.assertRaisesRegex(
            ProjectionRebuildAuthorizationError,
            "policy.denied",
        ):
            denied.execute(RebuildProjectionsCommand(self.actor))
        self.assertEqual(self.maintenance.calls, [])

    def test_invalid_resource_limit_fails_before_policy_or_storage(self) -> None:
        with self.assertRaisesRegex(
            ProjectionRebuildInputError,
            "projection.rebuild.input_invalid",
        ):
            self.service.execute(
                RebuildProjectionsCommand(self.actor, max_resources=0)
            )
        self.assertEqual(self.policy.calls, [])
        self.assertEqual(self.maintenance.calls, [])


class MaintenanceCliTests(unittest.TestCase):
    def test_cli_defaults_to_dry_run_and_requires_apply_for_mutation(self) -> None:
        maintenance = RecordingMaintenance()
        service = ProjectionRebuildService(
            maintenance,
            RecordingPolicy(PolicyDecision(True, "test.allow")),
        )
        output = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {
                    "IIP_DATABASE_URL": "postgresql://unused",
                    "IIP_DATABASE_TRANSPORT_MODE": "insecure-local",
                },
            ),
            patch(
                "iip.surfaces.maintenance.build_projection_maintenance_from_env",
                return_value=service,
            ),
            redirect_stdout(output),
        ):
            status = maintenance_main(
                ["rebuild-projections", "--tenant", "local", "--actor", "operator"]
            )

        self.assertEqual(status, 0)
        self.assertEqual(maintenance.calls, [("local", True, 100_000)])
        self.assertTrue(json.loads(output.getvalue())["dryRun"])

        output = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {
                    "IIP_DATABASE_URL": "postgresql://unused",
                    "IIP_DATABASE_TRANSPORT_MODE": "insecure-local",
                },
            ),
            patch(
                "iip.surfaces.maintenance.build_projection_maintenance_from_env",
                return_value=service,
            ),
            redirect_stdout(output),
        ):
            status = maintenance_main(
                [
                    "rebuild-projections",
                    "--tenant",
                    "local",
                    "--actor",
                    "operator",
                    "--apply",
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(maintenance.calls[-1], ("local", False, 100_000))
        self.assertTrue(json.loads(output.getvalue())["rebuildPerformed"])


if __name__ == "__main__":
    unittest.main()
