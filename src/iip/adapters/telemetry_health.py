"""Failure-isolated periodic telemetry health reporting adapter."""

from __future__ import annotations

from threading import Event, Lock, Thread

from iip.application.report_telemetry_export_health import (
    TelemetryExportHealthReporter,
)


class PeriodicTelemetryExportHealthReporter:
    """Run internal exporter heartbeats without affecting serving readiness."""

    def __init__(self, reporter: TelemetryExportHealthReporter) -> None:
        self._reporter = reporter
        self._stopped = Event()
        self._lifecycle_lock = Lock()
        self._thread: Thread | None = None

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None:
                return
            self._report_safely()
            self._thread = Thread(
                target=self._run,
                name="iip-telemetry-health-reporter",
                daemon=True,
            )
            self._thread.start()

    def close(self) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            self._thread = None
            self._stopped.set()
        if thread is not None:
            thread.join(timeout=5)
        try:
            self._reporter.retire()
        except Exception:
            pass

    def _run(self) -> None:
        interval = self._reporter.configuration.interval_seconds
        while not self._stopped.wait(interval):
            self._report_safely()

    def _report_safely(self) -> None:
        try:
            self._reporter.report_once()
        except Exception:
            pass
