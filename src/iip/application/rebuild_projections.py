"""Privileged resource-projection verification and recovery use case."""

from __future__ import annotations

from dataclasses import dataclass

from iip.application.ports import (
    ActorContext,
    PolicyDecisionPoint,
    ProjectionRebuildResult,
    ResourceProjectionMaintenance,
)


class ProjectionRebuildAuthorizationError(PermissionError):
    """Raised when an actor cannot inspect or rebuild tenant projections."""


class ProjectionRebuildInputError(ValueError):
    """Raised when a maintenance command is outside supported bounds."""


@dataclass(frozen=True)
class RebuildProjectionsCommand:
    """Explicit tenant-scoped projection recovery request."""

    actor: ActorContext
    dry_run: bool = True
    max_resources: int = 100_000


class ProjectionRebuildService:
    """Authorize and execute a bounded projection recovery operation."""

    def __init__(
        self,
        maintenance: ResourceProjectionMaintenance,
        policy: PolicyDecisionPoint,
    ) -> None:
        self._maintenance = maintenance
        self._policy = policy

    def execute(self, command: RebuildProjectionsCommand) -> ProjectionRebuildResult:
        if (
            not isinstance(command.dry_run, bool)
            or isinstance(command.max_resources, bool)
            or not isinstance(command.max_resources, int)
            or not 1 <= command.max_resources <= 1_000_000
            or not isinstance(command.actor.tenant_id, str)
            or not 1 <= len(command.actor.tenant_id) <= 128
        ):
            raise ProjectionRebuildInputError("projection.rebuild.input_invalid")
        if "platform-admin" not in command.actor.roles:
            raise ProjectionRebuildAuthorizationError(
                "projection.rebuild.role_required"
            )

        decision = self._policy.decide(
            actor=command.actor,
            action="resource-projection:rebuild",
            resource={
                "tenantId": command.actor.tenant_id,
                "dryRun": command.dry_run,
            },
        )
        if not decision.allowed:
            raise ProjectionRebuildAuthorizationError(decision.reason_code)

        return self._maintenance.rebuild_projections(
            command.actor.tenant_id,
            dry_run=command.dry_run,
            max_resources=command.max_resources,
        )
