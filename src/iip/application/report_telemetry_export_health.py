"""Internal deployment-wide telemetry exporter health reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from iip.application.ports import (
    Clock,
    TelemetryExportHealthReader,
    TelemetryExportHealthRepository,
    TelemetryExportInstanceState,
)
from iip.application.query_telemetry_export_health import (
    TelemetryExportHealthService,
    TelemetryExportHealthStateError,
)


_INSTANCE_ID = re.compile(r"sha256:[a-f0-9]{64}")
_COMPONENTS = frozenset({"api", "workflow-worker"})


@dataclass(frozen=True)
class TelemetryExportHealthReportingConfiguration:
    instance_id: str
    component: str
    interval_seconds: int = 30
    stale_after_seconds: int = 120
    retention_seconds: int = 600
    sample_retention_seconds: int = 604_800

    def validate(self) -> None:
        if (
            _INSTANCE_ID.fullmatch(self.instance_id) is None
            or self.component not in _COMPONENTS
            or isinstance(self.interval_seconds, bool)
            or isinstance(self.stale_after_seconds, bool)
            or isinstance(self.retention_seconds, bool)
            or isinstance(self.sample_retention_seconds, bool)
            or not isinstance(self.interval_seconds, int)
            or not isinstance(self.stale_after_seconds, int)
            or not isinstance(self.retention_seconds, int)
            or not isinstance(self.sample_retention_seconds, int)
            or not 5 <= self.interval_seconds <= 300
            or not self.interval_seconds * 2 <= self.stale_after_seconds <= 3_600
            or not self.stale_after_seconds * 2 <= self.retention_seconds <= 86_400
            or not self.retention_seconds
            <= self.sample_retention_seconds
            <= 2_592_000
        ):
            raise ValueError("telemetry.export-health.reporting.configuration.invalid")


class TelemetryExportHealthReporter:
    """Publish a bounded snapshot without making telemetry a serving dependency."""

    def __init__(
        self,
        reader: TelemetryExportHealthReader,
        repository: TelemetryExportHealthRepository,
        clock: Clock,
        configuration: TelemetryExportHealthReportingConfiguration,
    ) -> None:
        configuration.validate()
        self._reader = reader
        self._repository = repository
        self._clock = clock
        self.configuration = configuration
        self._started_at = self._normalized_now()

    def report_once(self) -> None:
        states = tuple(self._reader.read_export_health())
        TelemetryExportHealthService.validate_states(states)
        reported_at = self._normalized_now()
        self._repository.record_telemetry_export_health(
            TelemetryExportInstanceState(
                instance_id=self.configuration.instance_id,
                component=self.configuration.component,
                started_at=self._started_at,
                last_reported_at=reported_at,
                signals=states,
            ),
            expire_before=self._shift(
                reported_at, -self.configuration.retention_seconds
            ),
            sample_expire_before=self._shift(
                reported_at, -self.configuration.sample_retention_seconds
            ),
        )

    def retire(self) -> None:
        self._repository.retire_telemetry_export_health(
            self.configuration.instance_id
        )

    def _normalized_now(self) -> str:
        return self._format(self._parse(self._clock.now()))

    @classmethod
    def _shift(cls, value: str, seconds: int) -> str:
        return cls._format(cls._parse(value) + timedelta(seconds=seconds))

    @staticmethod
    def _parse(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
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
