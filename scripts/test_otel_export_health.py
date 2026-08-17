#!/usr/bin/env python3
"""Prove real OTLP exporter failure detection and recovery with Docker Desktop."""

from __future__ import annotations

import os
import socket
import subprocess
import time

from iip.adapters.otel import (
    OtlpMetricsConfiguration,
    OtlpTracesConfiguration,
    build_otlp_metrics_runtime,
    build_otlp_traces_runtime,
)

from tests.test_otel_metrics import measurement
from tests.test_otel_traces import measurement as investigation_measurement


DOCKER = os.environ.get("IIP_DOCKER_BIN", "docker")
COMPOSE_FILE = "deploy/docker-compose.otel.yml"
PROJECT = "iip-otel-test"
PORT = int(os.environ.get("IIP_OTEL_TEST_PORT", "24318"))


def compose(*arguments: str) -> None:
    subprocess.run(
        [
            DOCKER,
            "compose",
            "--project-name",
            PROJECT,
            "-f",
            COMPOSE_FILE,
            *arguments,
        ],
        check=True,
    )


def wait_for_collector() -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("Collector did not recover")


def main() -> int:
    metrics = build_otlp_metrics_runtime(
        OtlpMetricsConfiguration(
            endpoint=f"http://127.0.0.1:{PORT}/v1/metrics",
            service_name="iip-export-health-conformance",
            export_interval_millis=300_000,
            export_timeout_millis=1_000,
        )
    )
    traces = build_otlp_traces_runtime(
        OtlpTracesConfiguration(
            endpoint=f"http://127.0.0.1:{PORT}/v1/traces",
            service_name="iip-export-health-conformance",
            schedule_delay_millis=300_000,
            export_timeout_millis=1_000,
            max_queue_size=32,
            max_export_batch_size=8,
        )
    )
    try:
        compose("stop", "collector")
        metrics.sink.record_ingestion_freshness(measurement())
        traces.sink.record_investigation_execution(investigation_measurement())
        metrics.force_flush(3_000)
        traces.force_flush(3_000)
        failed_metrics = metrics.read_export_health()[0]
        failed_traces = traces.read_export_health()[1]
        if failed_metrics.status != "degraded" or failed_traces.status != "degraded":
            raise RuntimeError("Collector outage was not reflected in exporter health")

        compose("start", "collector")
        wait_for_collector()
        metrics.sink.record_ingestion_freshness(measurement())
        traces.sink.record_investigation_execution(investigation_measurement())
        metrics.force_flush(5_000)
        traces.force_flush(5_000)
        recovered_metrics = metrics.read_export_health()[0]
        recovered_traces = traces.read_export_health()[1]
        if recovered_metrics.status != "healthy" or recovered_traces.status != "healthy":
            raise RuntimeError("Exporter health did not recover with the Collector")
        if recovered_metrics.failures < 1 or recovered_traces.failures < 1:
            raise RuntimeError("Exporter failure history was not retained")
        print(
            "OTLP exporter health detected Collector failure and recovered "
            "for metrics and traces"
        )
        return 0
    finally:
        metrics.shutdown(5_000)
        traces.shutdown(5_000)


if __name__ == "__main__":
    raise SystemExit(main())
