"""Automatic, explicitly enrolled ingestion-freshness sampling."""

from __future__ import annotations

import re
from dataclasses import dataclass

from iip.application.observe_ingestion import (
    GetIngestionFreshnessCommand,
    IngestionFreshnessService,
    IngestionSourceNotFoundError,
    IngestionTelemetryAuthorizationError,
    IngestionTelemetryInputError,
    IngestionTelemetryStateError,
)
from iip.application.ports import ActorContext, PersistenceError


_TENANT_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}")
_SOURCE_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")


@dataclass(frozen=True)
class IngestionMonitorTarget:
    tenant_id: str
    source_id: str

    def validate(self) -> None:
        if (
            not isinstance(self.tenant_id, str)
            or _TENANT_ID.fullmatch(self.tenant_id) is None
            or not isinstance(self.source_id, str)
            or _SOURCE_ID.fullmatch(self.source_id) is None
        ):
            raise ValueError("ingestion.monitor.configuration.invalid")


@dataclass(frozen=True)
class IngestionSamplingSummary:
    sampled: int = 0
    breached: int = 0
    missing: int = 0
    denied: int = 0
    failed: int = 0


class IngestionFreshnessSampler:
    """Evaluate enrolled tenant/source pairs without widening their authority."""

    def __init__(
        self,
        service: IngestionFreshnessService,
        targets: tuple[IngestionMonitorTarget, ...],
    ) -> None:
        if not targets or len(targets) > 1000 or len(set(targets)) != len(targets):
            raise ValueError("ingestion.monitor.configuration.invalid")
        for target in targets:
            target.validate()
        self._service = service
        self._targets = targets

    def run_once(self) -> IngestionSamplingSummary:
        sampled = breached = missing = denied = failed = 0
        for target in self._targets:
            actor = ActorContext(
                actor_id="iip-ingestion-monitor",
                tenant_id=target.tenant_id,
                roles=("system-monitor",),
            )
            try:
                report = self._service.get(
                    GetIngestionFreshnessCommand(actor, target.source_id)
                ).to_dict()
                sampled += 1
                spec = report.get("spec")
                if isinstance(spec, dict) and spec.get("status") == "breached":
                    breached += 1
            except IngestionSourceNotFoundError:
                missing += 1
            except IngestionTelemetryAuthorizationError:
                denied += 1
            except (
                IngestionTelemetryInputError,
                IngestionTelemetryStateError,
                PersistenceError,
            ):
                failed += 1
        return IngestionSamplingSummary(
            sampled=sampled,
            breached=breached,
            missing=missing,
            denied=denied,
            failed=failed,
        )
