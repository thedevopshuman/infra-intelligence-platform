"""Privileged deployment-wide telemetry exporter delivery health query."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from iip.application.ports import (
    ActorContext,
    Clock,
    PolicyDecisionPoint,
    TelemetryExportHealthRepository,
    TelemetryExportInstanceState,
)
from iip.application.query_telemetry_export_health import (
    TelemetryExportHealthAuthorizationError,
    TelemetryExportHealthService,
    TelemetryExportHealthStateError,
)


_MAX_INSTANCES = 1_000
_INSTANCE_ID = re.compile(r"sha256:[a-f0-9]{64}")
_COMPONENTS = frozenset({"api", "workflow-worker", "otlp-receiver"})


@dataclass(frozen=True)
class GetTelemetryDeploymentHealthCommand:
    actor: ActorContext


@dataclass(frozen=True)
class TelemetryDeploymentHealthReport:
    document: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return dict(self.document)


class TelemetryDeploymentHealthService:
    """Render recent internal heartbeats as one backend-neutral health view."""

    def __init__(
        self,
        repository: TelemetryExportHealthRepository,
        policy: PolicyDecisionPoint,
        clock: Clock,
        *,
        stale_after_seconds: int = 120,
        retention_seconds: int = 600,
    ) -> None:
        if (
            isinstance(stale_after_seconds, bool)
            or isinstance(retention_seconds, bool)
            or not isinstance(stale_after_seconds, int)
            or not isinstance(retention_seconds, int)
            or not 10 <= stale_after_seconds <= 3_600
            or not stale_after_seconds * 2 <= retention_seconds <= 86_400
        ):
            raise ValueError("telemetry.export-health.reporting.configuration.invalid")
        self._repository = repository
        self._policy = policy
        self._clock = clock
        self._stale_after_seconds = stale_after_seconds
        self._retention_seconds = retention_seconds

    def get(
        self, command: GetTelemetryDeploymentHealthCommand
    ) -> TelemetryDeploymentHealthReport:
        if "platform-admin" not in command.actor.roles:
            raise TelemetryExportHealthAuthorizationError(
                "telemetry.export-health.role-required"
            )
        decision = self._policy.decide(
            command.actor,
            "telemetry-export-health:read",
            {"tenantId": command.actor.tenant_id},
        )
        if not decision.allowed:
            raise TelemetryExportHealthAuthorizationError(decision.reason_code)

        evaluated_at = self._parse(self._clock.now())
        retained_since = self._format(
            evaluated_at - timedelta(seconds=self._retention_seconds)
        )
        states = tuple(
            self._repository.list_telemetry_export_health(
                reported_since=retained_since,
                limit=_MAX_INSTANCES + 1,
            )
        )
        truncated = len(states) > _MAX_INSTANCES
        selected = states[:_MAX_INSTANCES]
        instances = [self._instance_document(state, evaluated_at) for state in selected]
        current = sum(1 for item in instances if item["freshness"] == "current")
        stale = len(instances) - current
        statuses = tuple(item["status"] for item in instances)
        if not instances:
            status = "disabled"
        elif stale or "degraded" in statuses or truncated:
            status = "degraded"
        elif "awaiting-first-attempt" in statuses:
            status = "awaiting-first-attempt"
        elif all(item == "disabled" for item in statuses):
            status = "disabled"
        else:
            status = "healthy"

        return TelemetryDeploymentHealthReport(
            {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "TelemetryDeploymentExportHealthReport",
                "metadata": {"evaluatedAt": self._format(evaluated_at)},
                "spec": {
                    "status": status,
                    "freshness": {
                        "staleAfterSeconds": self._stale_after_seconds,
                        "retentionSeconds": self._retention_seconds,
                    },
                    "summary": {
                        "includedInstances": len(instances),
                        "currentInstances": current,
                        "staleInstances": stale,
                        "truncated": truncated,
                    },
                    "instances": instances,
                },
            }
        )

    def _instance_document(
        self,
        state: TelemetryExportInstanceState,
        evaluated_at: datetime,
    ) -> dict[str, object]:
        if (
            _INSTANCE_ID.fullmatch(state.instance_id) is None
            or state.component not in _COMPONENTS
        ):
            raise TelemetryExportHealthStateError(
                "telemetry.export-health.state-invalid"
            )
        started_at = self._parse(state.started_at)
        reported_at = self._parse(state.last_reported_at)
        if started_at > reported_at or reported_at > evaluated_at + timedelta(seconds=30):
            raise TelemetryExportHealthStateError(
                "telemetry.export-health.state-invalid"
            )
        TelemetryExportHealthService.validate_states(state.signals)
        by_signal = {item.signal: item for item in state.signals}
        enabled = tuple(item for item in state.signals if item.enabled)
        status = (
            "disabled"
            if not enabled
            else "degraded"
            if any(item.status == "degraded" for item in enabled)
            else "awaiting-first-attempt"
            if any(item.status == "awaiting-first-attempt" for item in enabled)
            else "healthy"
        )
        return {
            "instanceId": state.instance_id,
            "component": state.component,
            "startedAt": self._format(started_at),
            "lastReportedAt": self._format(reported_at),
            "freshness": (
                "stale"
                if (evaluated_at - reported_at).total_seconds()
                > self._stale_after_seconds
                else "current"
            ),
            "status": status,
            "signals": [
                TelemetryExportHealthService._signal_document(by_signal[signal])
                for signal in ("metrics", "traces")
            ],
        }

    @staticmethod
    def _parse(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            raise TelemetryExportHealthStateError(
                "telemetry.export-health.state-invalid"
            ) from None
        if parsed.tzinfo is None:
            raise TelemetryExportHealthStateError(
                "telemetry.export-health.state-invalid"
            )
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")
