#!/usr/bin/env python3
"""Qualify a pinned official Collector against one customer IIP OTLP receiver."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from jsonschema import Draft202012Validator, FormatChecker
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status

import qualify_ingress_availability as ingress


ROOT = Path(__file__).resolve().parents[1]
API_VERSION = "iip.platform/v1alpha1"
PROFILE_KIND = "CustomerOtlpReceiverQualificationProfile"
REPORT_KIND = "CustomerOtlpReceiverQualificationReport"
QUALIFICATION = "customer-pinned-collector-to-iip-receiver-v1"
PROFILE_SCHEMA = ROOT / "contracts/schemas/customer-otlp-receiver-qualification-profile.schema.json"
REPORT_SCHEMA = ROOT / "contracts/schemas/customer-otlp-receiver-qualification-report.schema.json"
COLLECTOR_IMAGE = "otel/opentelemetry-collector-contrib@sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5"
COLLECTOR_IMAGE_DIGEST = COLLECTOR_IMAGE.rsplit("@", 1)[1]
SIGNALS = ("metrics", "logs", "traces")
EXPORTERS = {
    "metrics": "otlphttp/iip-metrics",
    "logs": "otlphttp/iip-logs",
    "traces": "otlphttp/iip-traces",
}
SENT_METRICS = {
    "metrics": "otelcol_exporter_sent_metric_points",
    "logs": "otelcol_exporter_sent_log_records",
    "traces": "otelcol_exporter_sent_spans",
}
FAILED_METRICS = {
    "metrics": "otelcol_exporter_send_failed_metric_points",
    "logs": "otelcol_exporter_send_failed_log_records",
    "traces": "otelcol_exporter_send_failed_spans",
}
CHECK_IDS = (
    "profile-binding",
    "source-binding",
    "immutable-release",
    "api-runtime-identity",
    "receiver-endpoint-binding",
    "protected-input-files",
    "collector-image-pinned",
    "collector-config-validation",
    "verified-tls",
    "client-certificate-authentication",
    "separate-channel-credentials",
    "direct-no-proxy-no-redirect",
    "metrics-delivery",
    "logs-delivery",
    "metadata-only-genai-delivery",
    "receiver-durable-acknowledgement",
    "collector-zero-send-failure",
    "collector-queue-drained",
    "delivery-latency-objective",
    "minimized-output",
)
LIMITATIONS = (
    "customer-long-running-collector-configuration-not-qualified",
    "application-telemetry-fail-open-behavior-not-qualified",
    "sustained-throughput-queue-capacity-and-disk-recovery-not-qualified",
    "customer-pki-rotation-revocation-crl-distribution-and-ocsp-not-qualified",
    "node-zone-region-and-multi-collector-failure-not-qualified",
)
REPORT_ID = re.compile(r"^corq_[a-f0-9]{32}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")
SAFE_TOKEN = re.compile(r"[!-~]{16,8192}")
METRIC_LINE = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+"
    r"(?P<value>[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)$"
)
LABEL = re.compile(r'(?:^|,)\s*([A-Za-z_][A-Za-z0-9_]*)="((?:\\.|[^"\\])*)"')
MAX_RESPONSE_BYTES = 1024 * 1024
FORBIDDEN_RETAINED_KEYS = frozenset(
    {
        "endpoint",
        "receiverEndpoint",
        "url",
        "token",
        "credential",
        "secret",
        "privateKey",
        "serviceName",
        "metricName",
        "requestModel",
        "responseModel",
        "provider",
        "region",
        "instrumentationScope",
    }
)


class CustomerOtlpReceiverQualificationError(RuntimeError):
    """Stable failure for unsafe, incomplete, or crossed receiver evidence."""


@dataclass(frozen=True)
class ProbeResult:
    """Value-minimized observations from the ephemeral pinned Collector."""

    config_valid: bool
    collector_started: bool
    accepted: Mapping[str, bool]
    delivered: Mapping[str, int]
    send_failed: Mapping[str, int]
    queue_size: Mapping[str, int]
    latency_milliseconds: Mapping[str, int]


class CollectorProbe(Protocol):
    def run(
        self,
        payloads: Mapping[str, bytes],
        *,
        request_timeout_seconds: int,
        delivery_timeout_seconds: int,
        poll_interval_milliseconds: int,
    ) -> ProbeResult:
        """Run one isolated Collector and return only aggregate outcomes."""


def _fail(code: str) -> None:
    raise CustomerOtlpReceiverQualificationError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _digest_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        _fail("customer-otlp-receiver-qualification.time.invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, code: str) -> datetime:
    if not isinstance(value, str):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    return parsed.astimezone(timezone.utc)


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _integer(value: object, minimum: int, maximum: int, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail(code)
    return value


def _schema(path: Path, code: str) -> Mapping[str, Any]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), code)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)


def _validate_schema(document: Mapping[str, Any], path: Path, code: str) -> None:
    errors = list(
        Draft202012Validator(_schema(path, code), format_checker=FormatChecker()).iter_errors(document)
    )
    if errors:
        _fail(code)


def _read_file(
    path: Path,
    *,
    code: str,
    maximum_bytes: int,
    protected: bool,
) -> tuple[Path, bytes]:
    descriptor = -1
    candidate = path.expanduser()
    try:
        if candidate.is_symlink():
            _fail(code)
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or not 1 <= details.st_size <= maximum_bytes
            or (protected and stat.S_IMODE(details.st_mode) != 0o600)
        ):
            _fail(code)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            payload = handle.read(maximum_bytes + 1)
        if len(payload) > maximum_bytes:
            _fail(code)
        return candidate.absolute(), payload
    except CustomerOtlpReceiverQualificationError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _token(path: Path, code: str) -> str:
    _, payload = _read_file(path, code=code, maximum_bytes=8194, protected=True)
    try:
        raw = payload.decode("ascii")
    except UnicodeDecodeError:
        _fail(code)
    value = raw.rstrip("\r\n")
    if raw not in (value, value + "\n", value + "\r\n") or SAFE_TOKEN.fullmatch(value) is None:
        _fail(code)
    return value


def _root_https_url(value: object, code: str) -> str:
    if not isinstance(value, str):
        _fail(code)
    try:
        return ingress._checked_url("customer-ingress", value)[0]
    except ingress.IngressQualificationError:
        _fail(code)


def validate_profile(profile: Mapping[str, Any]) -> None:
    code = "customer-otlp-receiver-qualification.profile.invalid"
    _validate_schema(profile, PROFILE_SCHEMA, code)
    if profile.get("apiVersion") != API_VERSION or profile.get("kind") != PROFILE_KIND:
        _fail(code)
    spec = _mapping(profile.get("spec"), code)
    if _root_https_url(spec.get("receiverEndpoint"), code) != spec.get("receiverEndpoint"):
        _fail(code)
    objective = _mapping(spec.get("objective"), code)
    delivery_timeout = _integer(objective.get("deliveryTimeoutSeconds"), 10, 300, code)
    maximum_latency = _integer(objective.get("maximumDeliveryLatencyMilliseconds"), 1000, 300000, code)
    if maximum_latency > delivery_timeout * 1000:
        _fail(code)


def load_profile(path: Path) -> Mapping[str, Any]:
    _, payload = _read_file(
        path,
        code="customer-otlp-receiver-qualification.profile.invalid",
        maximum_bytes=262144,
        protected=True,
    )
    try:
        profile = _mapping(
            json.loads(payload.decode("utf-8")),
            "customer-otlp-receiver-qualification.profile.invalid",
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-otlp-receiver-qualification.profile.invalid")
    validate_profile(profile)
    return profile


def _source_identity() -> tuple[str, bool]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True, timeout=10
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=normal"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        _fail("customer-otlp-receiver-qualification.source.unavailable")
    if REVISION.fullmatch(revision) is None:
        _fail("customer-otlp-receiver-qualification.source.invalid")
    return revision, dirty


def _payloads(profile: Mapping[str, Any], instant: datetime) -> dict[str, bytes]:
    spec = _mapping(profile.get("spec"), "customer-otlp-receiver-qualification.profile.invalid")
    signals = _mapping(spec.get("signals"), "customer-otlp-receiver-qualification.profile.invalid")
    timestamp_ns = int(instant.timestamp() * 1_000_000_000)

    metric_profile = _mapping(signals.get("metrics"), "customer-otlp-receiver-qualification.profile.invalid")
    metrics = ExportMetricsServiceRequest()
    resource_metrics = metrics.resource_metrics.add()
    resource_metrics.resource.attributes.add(key="service.name").value.string_value = str(metric_profile["serviceName"])
    scope_metrics = resource_metrics.scope_metrics.add()
    scope_metrics.scope.name = "iip.customer-otlp-qualification"
    metric = scope_metrics.metrics.add(name=str(metric_profile["metricName"]), unit=str(metric_profile["unit"]))
    metric.sum.aggregation_temporality = 2
    metric.sum.is_monotonic = True
    point = metric.sum.data_points.add()
    point.time_unix_nano = timestamp_ns
    point.as_int = 1

    log_profile = _mapping(signals.get("logs"), "customer-otlp-receiver-qualification.profile.invalid")
    logs = ExportLogsServiceRequest()
    resource_logs = logs.resource_logs.add()
    for key, value in (
        ("service.name", str(log_profile["serviceName"])),
        ("deployment.environment.name", str(log_profile["deploymentEnvironment"])),
    ):
        resource_logs.resource.attributes.add(key=key).value.string_value = value
    scope_logs = resource_logs.scope_logs.add()
    scope_logs.scope.name = "iip.customer-otlp-qualification"
    record = scope_logs.log_records.add()
    record.time_unix_nano = timestamp_ns
    record.observed_time_unix_nano = timestamp_ns
    record.severity_number = 9
    record.severity_text = "INFO"
    record.body.string_value = "IIP customer Collector qualification record"

    trace_profile = _mapping(signals.get("genAiTraces"), "customer-otlp-receiver-qualification.profile.invalid")
    traces = ExportTraceServiceRequest()
    resource_spans = traces.resource_spans.add()
    for key, value in (
        ("service.name", str(trace_profile["serviceName"])),
        ("service.namespace", str(trace_profile["serviceNamespace"])),
        ("deployment.environment.name", str(trace_profile["deploymentEnvironment"])),
        ("cloud.region", str(trace_profile["region"])),
    ):
        resource_spans.resource.attributes.add(key=key).value.string_value = value
    scope_spans = resource_spans.scope_spans.add()
    scope_spans.scope.name = str(trace_profile["instrumentationScope"])
    scope_spans.scope.version = str(trace_profile["semanticConventionVersion"])
    span = scope_spans.spans.add()
    identity = hashlib.sha256(_canonical({"profile": profile, "time": _timestamp(instant)})).digest()
    span.trace_id = identity[:16]
    span.span_id = identity[16:24]
    span.name = f"{trace_profile['operation']} {trace_profile['requestModel']}"
    span.kind = Span.SPAN_KIND_CLIENT
    span.start_time_unix_nano = timestamp_ns
    span.end_time_unix_nano = timestamp_ns + 1_000_000
    span.status.code = Status.STATUS_CODE_OK
    for key, value in (
        ("gen_ai.provider.name", str(trace_profile["provider"])),
        ("gen_ai.operation.name", str(trace_profile["operation"])),
        ("gen_ai.request.model", str(trace_profile["requestModel"])),
        ("gen_ai.response.model", str(trace_profile["responseModel"])),
    ):
        span.attributes.add(key=key).value.string_value = value
    span.attributes.add(key="gen_ai.usage.input_tokens").value.int_value = 1
    span.attributes.add(key="gen_ai.usage.output_tokens").value.int_value = 1
    return {
        "metrics": metrics.SerializeToString(),
        "logs": logs.SerializeToString(),
        "traces": traces.SerializeToString(),
    }


def _collector_configuration() -> str:
    return """extensions:
  file_storage/iip:
    directory: /var/lib/otelcol/iip
    create_directory: true
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
exporters:
  otlphttp/iip-metrics:
    endpoint: ${env:IIP_QUAL_RECEIVER_ENDPOINT}
    headers:
      Authorization: Bearer ${env:IIP_QUAL_METRICS_TOKEN}
    tls: &iip_tls
      ca_file: /qualification/receiver-ca.pem
      cert_file: /qualification/client-cert.pem
      key_file: /qualification/client-key.pem
      min_version: \"1.2\"
    sending_queue: &iip_queue
      enabled: true
      num_consumers: 1
      queue_size: 100
      storage: file_storage/iip
    retry_on_failure: &iip_retry
      enabled: true
      initial_interval: 250ms
      max_interval: 1s
      max_elapsed_time: 10s
  otlphttp/iip-logs:
    endpoint: ${env:IIP_QUAL_RECEIVER_ENDPOINT}
    headers:
      Authorization: Bearer ${env:IIP_QUAL_LOGS_TOKEN}
    tls: *iip_tls
    sending_queue: *iip_queue
    retry_on_failure: *iip_retry
  otlphttp/iip-traces:
    endpoint: ${env:IIP_QUAL_RECEIVER_ENDPOINT}
    headers:
      Authorization: Bearer ${env:IIP_QUAL_TRACES_TOKEN}
    tls: *iip_tls
    sending_queue: *iip_queue
    retry_on_failure: *iip_retry
service:
  extensions: [file_storage/iip]
  telemetry:
    logs:
      level: error
    metrics:
      readers:
        - pull:
            exporter:
              prometheus:
                host: 0.0.0.0
                port: 8888
  pipelines:
    metrics:
      receivers: [otlp]
      exporters: [otlphttp/iip-metrics]
    logs:
      receivers: [otlp]
      exporters: [otlphttp/iip-logs]
    traces:
      receivers: [otlp]
      exporters: [otlphttp/iip-traces]
"""


def _parse_labels(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    return {match.group(1): bytes(match.group(2), "utf-8").decode("unicode_escape") for match in LABEL.finditer(value)}


def _metric_value(document: str, name: str, exporter: str) -> int:
    total = 0.0
    for raw in document.splitlines():
        match = METRIC_LINE.fullmatch(raw.strip())
        if match is None or match.group("name") != name:
            continue
        if _parse_labels(match.group("labels")).get("exporter") != exporter:
            continue
        try:
            total += float(match.group("value"))
        except ValueError:
            continue
    if not math.isfinite(total) or total < 0:
        return 0
    return int(total)


class _DenyRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None


def _loopback_request(url: str, *, method: str = "GET", body: bytes | None = None, timeout: float = 5) -> bytes:
    opener = build_opener(ProxyHandler({}), _DenyRedirects())
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/x-protobuf", "Connection": "close"},
    )
    with opener.open(request, timeout=timeout) as response:
        if int(getattr(response, "status", 0)) != 200:
            raise OSError
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise OSError
    return payload


class DockerCollectorProbe:
    """Run the pinned Collector without a shell or ambient proxy configuration."""

    def __init__(
        self,
        *,
        docker: str,
        receiver_endpoint: str,
        tokens: Mapping[str, str],
        receiver_ca: bytes,
        client_certificate: bytes,
        client_key: bytes,
    ) -> None:
        self.docker = docker
        self.receiver_endpoint = receiver_endpoint
        self.tokens = dict(tokens)
        self.receiver_ca = receiver_ca
        self.client_certificate = client_certificate
        self.client_key = client_key

    def _command(self, arguments: Sequence[str], *, environment: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        selected = dict(os.environ)
        selected.update(environment or {})
        try:
            return subprocess.run(
                [self.docker, *arguments],
                cwd=ROOT,
                env=selected,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            _fail("customer-otlp-receiver-qualification.collector.unavailable")

    @staticmethod
    def _environment() -> dict[str, str]:
        return {
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "ALL_PROXY": "",
            "NO_PROXY": "*",
            "http_proxy": "",
            "https_proxy": "",
            "all_proxy": "",
            "no_proxy": "*",
        }

    def _runtime_environment(self) -> dict[str, str]:
        return {
            **self._environment(),
            "IIP_QUAL_RECEIVER_ENDPOINT": self.receiver_endpoint,
            "IIP_QUAL_METRICS_TOKEN": self.tokens["metrics"],
            "IIP_QUAL_LOGS_TOKEN": self.tokens["logs"],
            "IIP_QUAL_TRACES_TOKEN": self.tokens["traces"],
        }

    @staticmethod
    def _container_arguments(name: str, queue_directory: Path, directory: Path) -> list[str]:
        arguments = [
            "run", "--detach", "--name", name, "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--pids-limit", "128", "--memory", "256m", "--cpus", "1",
            "--publish", "127.0.0.1::4318", "--publish", "127.0.0.1::8888",
            "--mount", f"type=bind,src={directory},dst=/qualification,readonly",
            "--mount", f"type=bind,src={queue_directory},dst=/var/lib/otelcol",
            "--env", "HTTP_PROXY", "--env", "HTTPS_PROXY", "--env", "ALL_PROXY", "--env", "NO_PROXY",
            "--env", "http_proxy", "--env", "https_proxy", "--env", "all_proxy", "--env", "no_proxy",
            "--env", "IIP_QUAL_RECEIVER_ENDPOINT", "--env", "IIP_QUAL_METRICS_TOKEN",
            "--env", "IIP_QUAL_LOGS_TOKEN", "--env", "IIP_QUAL_TRACES_TOKEN",
            COLLECTOR_IMAGE, "--config=/qualification/config.yaml",
        ]
        return arguments

    def _port(self, name: str, container_port: int) -> int:
        result = self._command(["port", name, f"{container_port}/tcp"])
        if result.returncode != 0:
            raise OSError
        for line in result.stdout.splitlines():
            try:
                port = int(line.rsplit(":", 1)[1])
            except (IndexError, ValueError):
                continue
            if 1 <= port <= 65535:
                return port
        raise OSError

    @staticmethod
    def _write_inputs(directory: Path, ca: bytes, certificate: bytes, key: bytes) -> None:
        (directory / "config.yaml").write_text(_collector_configuration(), encoding="utf-8")
        (directory / "receiver-ca.pem").write_bytes(ca)
        (directory / "client-cert.pem").write_bytes(certificate)
        (directory / "client-key.pem").write_bytes(key)
        os.chmod(directory / "client-key.pem", 0o600)

    def _snapshot(self, metrics_url: str, timeout: float) -> dict[str, dict[str, int]]:
        document = _loopback_request(metrics_url, timeout=timeout).decode("utf-8")
        return {
            signal: {
                "sent": _metric_value(document, SENT_METRICS[signal], EXPORTERS[signal]),
                "failed": _metric_value(document, FAILED_METRICS[signal], EXPORTERS[signal]),
                "queue": _metric_value(document, "otelcol_exporter_queue_size", EXPORTERS[signal]),
            }
            for signal in SIGNALS
        }

    def run(
        self,
        payloads: Mapping[str, bytes],
        *,
        request_timeout_seconds: int,
        delivery_timeout_seconds: int,
        poll_interval_milliseconds: int,
    ) -> ProbeResult:
        name = "iip-customer-otlp-qualification-" + os.urandom(6).hex()
        accepted = {signal: False for signal in SIGNALS}
        zeros = {signal: 0 for signal in SIGNALS}
        config_valid = False
        started = False
        final = {signal: {"sent": 0, "failed": 0, "queue": 0} for signal in SIGNALS}
        baseline = final
        latency = dict(zeros)
        with tempfile.TemporaryDirectory(prefix="iip-customer-otlp-") as temporary:
            directory = Path(temporary)
            queue_directory = directory / "queue"
            queue_directory.mkdir(mode=0o777)
            os.chmod(queue_directory, 0o777)
            self._write_inputs(directory, self.receiver_ca, self.client_certificate, self.client_key)
            validate = self._command(
                [
                    "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/var/lib/otelcol:rw,nosuid,nodev,size=16m",
                    "--mount", f"type=bind,src={directory},dst=/qualification,readonly",
                    *sum((["--env", key] for key in self._runtime_environment()), []),
                    COLLECTOR_IMAGE, "validate", "--config=/qualification/config.yaml",
                ],
                environment=self._runtime_environment(),
            )
            config_valid = validate.returncode == 0
            if not config_valid:
                return ProbeResult(False, False, accepted, zeros, zeros, zeros, zeros)
            try:
                run = self._command(
                    self._container_arguments(name, queue_directory, directory),
                    environment=self._runtime_environment(),
                )
                started = run.returncode == 0
                if not started:
                    return ProbeResult(True, False, accepted, zeros, zeros, zeros, zeros)
                deadline = time.monotonic() + min(30, delivery_timeout_seconds)
                ports: tuple[int, int] | None = None
                while time.monotonic() <= deadline:
                    try:
                        ports = (self._port(name, 4318), self._port(name, 8888))
                        baseline = self._snapshot(
                            f"http://127.0.0.1:{ports[1]}/metrics",
                            request_timeout_seconds,
                        )
                        break
                    except (OSError, UnicodeDecodeError, URLError, HTTPError):
                        time.sleep(0.2)
                if ports is None:
                    return ProbeResult(True, True, accepted, zeros, zeros, zeros, zeros)
                submitted_at: dict[str, float] = {}
                for signal in SIGNALS:
                    submitted_at[signal] = time.monotonic()
                    try:
                        _loopback_request(
                            f"http://127.0.0.1:{ports[0]}/v1/{signal}",
                            method="POST",
                            body=payloads[signal],
                            timeout=request_timeout_seconds,
                        )
                        accepted[signal] = True
                    except (OSError, URLError, HTTPError, TimeoutError):
                        accepted[signal] = False
                delivery_deadline = time.monotonic() + delivery_timeout_seconds
                delivered_at: dict[str, float] = {}
                while time.monotonic() <= delivery_deadline:
                    try:
                        final = self._snapshot(
                            f"http://127.0.0.1:{ports[1]}/metrics",
                            request_timeout_seconds,
                        )
                    except (OSError, UnicodeDecodeError, URLError, HTTPError):
                        time.sleep(poll_interval_milliseconds / 1000)
                        continue
                    for signal in SIGNALS:
                        if final[signal]["sent"] - baseline[signal]["sent"] >= 1 and signal not in delivered_at:
                            delivered_at[signal] = time.monotonic()
                    if len(delivered_at) == len(SIGNALS) and all(final[signal]["queue"] == 0 for signal in SIGNALS):
                        break
                    time.sleep(poll_interval_milliseconds / 1000)
                for signal in SIGNALS:
                    if signal in delivered_at:
                        latency[signal] = max(0, math.ceil((delivered_at[signal] - submitted_at[signal]) * 1000))
            finally:
                self._command(["rm", "--force", name])
        return ProbeResult(
            config_valid=config_valid,
            collector_started=started,
            accepted=accepted,
            delivered={signal: max(0, final[signal]["sent"] - baseline[signal]["sent"]) for signal in SIGNALS},
            send_failed={signal: max(0, final[signal]["failed"] - baseline[signal]["failed"]) for signal in SIGNALS},
            queue_size={signal: max(0, final[signal]["queue"]) for signal in SIGNALS},
            latency_milliseconds=latency,
        )


def _api_identity(
    *,
    base_url: str,
    token: str,
    ca_payload: bytes | None,
    expected: Mapping[str, str],
    timeout_seconds: int,
) -> bool:
    with tempfile.TemporaryDirectory(prefix="iip-api-ca-") as temporary:
        ca_path: Path | None = None
        if ca_payload is not None:
            ca_path = Path(temporary) / "ca.pem"
            ca_path.write_bytes(ca_payload)
        try:
            context = ssl.create_default_context(cafile=str(ca_path) if ca_path is not None else None)
            opener = build_opener(ProxyHandler({}), _DenyRedirects(), HTTPSHandler(context=context))
            request = Request(
                base_url + "/v1/system/version",
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}", "Connection": "close"},
                method="GET",
            )
            with opener.open(request, timeout=timeout_seconds) as response:
                if response.status != 200 or response.headers.get_content_type() != "application/json":
                    return False
                payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                return False
            identity = ingress._runtime_identity(json.loads(payload))
            return identity == dict(expected)
        except HTTPError as error:
            error.close()
            return False
        except (OSError, URLError, TimeoutError, ssl.SSLError, HTTPException, ValueError, json.JSONDecodeError):
            return False


def _direct_receiver_delivery(
    *,
    receiver_endpoint: str,
    tokens: Mapping[str, str],
    receiver_ca_path: Path,
    client_certificate_path: Path,
    client_key_path: Path,
    timeout_seconds: int,
) -> int:
    """Return the number of exact receiver paths reached without a redirect."""

    try:
        context = ssl.create_default_context(cafile=str(receiver_ca_path))
        context.load_cert_chain(
            certfile=str(client_certificate_path),
            keyfile=str(client_key_path),
        )
        opener = build_opener(
            ProxyHandler({}),
            _DenyRedirects(),
            HTTPSHandler(context=context),
        )
    except (OSError, ssl.SSLError, ValueError):
        return 0
    accepted = 0
    for signal in SIGNALS:
        request = Request(
            f"{receiver_endpoint}/v1/{signal}",
            data=b"",
            method="POST",
            headers={
                "Accept": "application/x-protobuf",
                "Authorization": f"Bearer {tokens[signal]}",
                "Connection": "close",
                "Content-Type": "application/x-protobuf",
            },
        )
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if (
                    response.status == 200
                    and response.headers.get_content_type()
                    == "application/x-protobuf"
                    and body == b""
                ):
                    accepted += 1
        except HTTPError as error:
            error.close()
        except (OSError, URLError, TimeoutError, ssl.SSLError, HTTPException):
            continue
    return accepted


def _check(identifier: str, passed: bool) -> dict[str, str]:
    result = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        result["errorCode"] = f"customer-otlp-receiver-qualification.{identifier}.failed"
    return result


def _summary(checks: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    passed = sum(item.get("status") == "passed" for item in checks)
    total = len(CHECK_IDS)
    return {
        "totalChecks": total,
        "passedChecks": passed,
        "failedChecks": total - passed,
        "overallStatus": "qualified" if passed == total else "not-qualified",
    }


def _has_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(key in FORBIDDEN_RETAINED_KEYS or _has_forbidden_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_has_forbidden_key(item) for item in value)
    return False


def _report_identifier(metadata: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    return "corq_" + hashlib.sha256(_canonical({"metadata": metadata, "spec": spec})).hexdigest()[:32]


def _probe_values(result: ProbeResult) -> tuple[int, int, int, int]:
    if type(result.config_valid) is not bool or type(result.collector_started) is not bool:
        _fail("customer-otlp-receiver-qualification.observation.invalid")
    if any(set(mapping) != set(SIGNALS) for mapping in (result.accepted, result.delivered, result.send_failed, result.queue_size, result.latency_milliseconds)):
        _fail("customer-otlp-receiver-qualification.observation.invalid")
    if any(type(result.accepted[signal]) is not bool for signal in SIGNALS):
        _fail("customer-otlp-receiver-qualification.observation.invalid")
    accepted = sum(bool(result.accepted[signal]) for signal in SIGNALS)
    delivered = sum(min(1, _integer(result.delivered[signal], 0, 1000000, "customer-otlp-receiver-qualification.observation.invalid")) for signal in SIGNALS)
    failed = sum(_integer(result.send_failed[signal], 0, 1000000, "customer-otlp-receiver-qualification.observation.invalid") for signal in SIGNALS)
    queued = sum(_integer(result.queue_size[signal], 0, 1000000, "customer-otlp-receiver-qualification.observation.invalid") for signal in SIGNALS)
    for signal in SIGNALS:
        _integer(result.latency_milliseconds[signal], 0, 300000, "customer-otlp-receiver-qualification.observation.invalid")
    return accepted, delivered, failed, queued


def build_report(
    *,
    revision: str,
    repository: Mapping[str, str],
    image_digest: str,
    api_target_digest: str,
    profile: Mapping[str, Any],
    api_ca_digest: str,
    receiver_ca_digest: str,
    client_certificate_digest: str,
    started_at: datetime,
    completed_at: datetime,
    api_identity_valid: bool,
    credentials_separate: bool,
    direct_receiver_accepted_count: int,
    result: ProbeResult,
) -> dict[str, Any]:
    code = "customer-otlp-receiver-qualification.report.invalid"
    validate_profile(profile)
    if (
        started_at.tzinfo is None
        or started_at.utcoffset() is None
        or completed_at.tzinfo is None
        or completed_at.utcoffset() is None
    ):
        _fail("customer-otlp-receiver-qualification.time.invalid")
    if REVISION.fullmatch(revision) is None or DIGEST.fullmatch(image_digest) is None:
        _fail(code)
    if any(DIGEST.fullmatch(value) is None for value in (api_target_digest, api_ca_digest, receiver_ca_digest, client_certificate_digest)):
        _fail(code)
    if set(repository) != {"applicationVersion", "chartVersion", "requiredMigration"}:
        _fail(code)
    accepted, delivered, failed, queued = _probe_values(result)
    direct_accepted = _integer(
        direct_receiver_accepted_count,
        0,
        3,
        "customer-otlp-receiver-qualification.observation.invalid",
    )
    spec_profile = _mapping(profile.get("spec"), code)
    metadata_profile = _mapping(profile.get("metadata"), code)
    objective = _mapping(spec_profile.get("objective"), code)
    signals = _mapping(spec_profile.get("signals"), code)
    maximum_latency = max(result.latency_milliseconds.values())
    delivery_ok = {signal: result.accepted[signal] and result.delivered[signal] >= 1 for signal in SIGNALS}
    observations = {
        "profile-binding": 0 <= (started_at - _parse_timestamp(metadata_profile["reviewedAt"], code)).total_seconds() <= objective["maximumProfileAgeSeconds"],
        "source-binding": True,
        "immutable-release": True,
        "api-runtime-identity": api_identity_valid,
        "receiver-endpoint-binding": True,
        "protected-input-files": True,
        "collector-image-pinned": True,
        "collector-config-validation": result.config_valid and result.collector_started,
        "verified-tls": all(delivery_ok.values()),
        "client-certificate-authentication": all(delivery_ok.values()),
        "separate-channel-credentials": credentials_separate,
        "direct-no-proxy-no-redirect": direct_accepted == 3,
        "metrics-delivery": delivery_ok["metrics"],
        "logs-delivery": delivery_ok["logs"],
        "metadata-only-genai-delivery": delivery_ok["traces"],
        "receiver-durable-acknowledgement": delivered == 3,
        "collector-zero-send-failure": failed == 0,
        "collector-queue-drained": queued == 0 and delivered == 3,
        "delivery-latency-objective": maximum_latency <= objective["maximumDeliveryLatencyMilliseconds"],
        "minimized-output": True,
    }
    checks = [_check(identifier, observations[identifier]) for identifier in CHECK_IDS]
    summary = _summary(checks)
    report_spec: dict[str, Any] = {
        "status": summary["overallStatus"],
        "qualification": QUALIFICATION,
        "subject": {
            "applicationVersion": repository["applicationVersion"],
            "contractsApiVersion": API_VERSION,
            "sourceRevision": revision,
            "chartVersion": repository["chartVersion"],
            "requiredMigration": repository["requiredMigration"],
            "imageDigest": image_digest,
        },
        "bindings": {
            "apiTargetBindingDigest": api_target_digest,
            "receiverEndpointBindingDigest": _digest_value(spec_profile["receiverEndpoint"]),
            "profileDigest": _digest_value(profile),
            "signalSetDigest": _digest_value(signals),
            "apiCaBundleDigest": api_ca_digest,
            "receiverCaBundleDigest": receiver_ca_digest,
            "clientCertificateDigest": client_certificate_digest,
        },
        "profile": {
            "name": QUALIFICATION,
            "collectorDistribution": "opentelemetry-collector-contrib",
            "collectorVersion": "0.158.0",
            "collectorImageDigest": COLLECTOR_IMAGE_DIGEST,
            "inputTransport": "loopback-otlp-http-protobuf",
            "outputTransport": "ca-verified-https-otlp-http-protobuf",
            "workloadIdentity": "x509-client-certificate",
            "channelCredential": "separate-per-signal-bearer",
            "queueMode": "file-storage-persistent-sending-queue",
            "redirectMode": "denied",
            "proxyMode": "disabled",
        },
        "objective": dict(objective),
        "measurements": {
            "profileReviewedAt": metadata_profile["reviewedAt"],
            "startedAt": _timestamp(started_at),
            "completedAt": _timestamp(completed_at),
            "signalCount": 3,
            "collectorSubmittedItemCount": 3,
            "directReceiverAcceptedPathCount": direct_accepted,
            "collectorAcceptedItemCount": accepted,
            "receiverDeliveredItemCount": delivered,
            "collectorSendFailureCount": failed,
            "finalQueueItemCount": queued,
            "maximumDeliveryLatencyMilliseconds": maximum_latency,
        },
        "checks": checks,
        "limitations": list(LIMITATIONS),
        "summary": summary,
    }
    metadata_without_id = {"generatedAt": _timestamp(completed_at), "sourceRevision": revision, "sourceDirty": False}
    report = {
        "apiVersion": API_VERSION,
        "kind": REPORT_KIND,
        "metadata": {"id": _report_identifier(metadata_without_id, report_spec), **metadata_without_id},
        "spec": report_spec,
    }
    if _has_forbidden_key(report):
        _fail("customer-otlp-receiver-qualification.report.not-minimized")
    validate_report_document(report)
    return report


def validate_report_document(report: Mapping[str, Any]) -> None:
    _validate_schema(report, REPORT_SCHEMA, "customer-otlp-receiver-qualification.report.schema-invalid")
    code = "customer-otlp-receiver-qualification.report.invalid"
    metadata = _mapping(report.get("metadata"), code)
    spec = _mapping(report.get("spec"), code)
    measurements = _mapping(spec.get("measurements"), code)
    objective = _mapping(spec.get("objective"), code)
    checks = spec.get("checks")
    if not isinstance(checks, list) or [item.get("id") for item in checks if isinstance(item, Mapping)] != list(CHECK_IDS):
        _fail("customer-otlp-receiver-qualification.report.checks-invalid")
    for identifier, item in zip(CHECK_IDS, checks):
        current = _mapping(item, "customer-otlp-receiver-qualification.report.checks-invalid")
        if current != _check(identifier, current.get("status") == "passed"):
            _fail("customer-otlp-receiver-qualification.report.checks-invalid")
    reviewed = _parse_timestamp(measurements.get("profileReviewedAt"), code)
    started = _parse_timestamp(measurements.get("startedAt"), code)
    completed = _parse_timestamp(measurements.get("completedAt"), code)
    accepted = _integer(measurements.get("collectorAcceptedItemCount"), 0, 3, code)
    direct_accepted = _integer(
        measurements.get("directReceiverAcceptedPathCount"), 0, 3, code
    )
    delivered = _integer(measurements.get("receiverDeliveredItemCount"), 0, 3, code)
    failed = _integer(measurements.get("collectorSendFailureCount"), 0, 1000000, code)
    queued = _integer(measurements.get("finalQueueItemCount"), 0, 1000000, code)
    latency = _integer(measurements.get("maximumDeliveryLatencyMilliseconds"), 0, 300000, code)
    by_id = {item["id"]: item["status"] == "passed" for item in checks}
    if (
        not reviewed <= started <= completed
        or completed != _parse_timestamp(metadata.get("generatedAt"), code)
        or metadata.get("sourceRevision") != _mapping(spec.get("subject"), code).get("sourceRevision")
        or measurements.get("signalCount") != 3
        or measurements.get("collectorSubmittedItemCount") != 3
        or spec.get("limitations") != list(LIMITATIONS)
        or _has_forbidden_key(report)
    ):
        _fail(code)
    expected_relations = {
        "profile-binding": 0 <= (started - reviewed).total_seconds() <= objective.get("maximumProfileAgeSeconds"),
        "receiver-durable-acknowledgement": delivered == 3,
        "collector-zero-send-failure": failed == 0,
        "collector-queue-drained": queued == 0 and delivered == 3,
        "delivery-latency-objective": latency <= objective.get("maximumDeliveryLatencyMilliseconds"),
        "direct-no-proxy-no-redirect": direct_accepted == 3,
    }
    if any(by_id.get(identifier) != expected for identifier, expected in expected_relations.items()):
        _fail("customer-otlp-receiver-qualification.report.checks-invalid")
    delivery_checks = ("metrics-delivery", "logs-delivery", "metadata-only-genai-delivery")
    if sum(by_id.get(identifier) is True for identifier in delivery_checks) != delivered:
        _fail("customer-otlp-receiver-qualification.report.checks-invalid")
    if delivered > accepted or (by_id.get("collector-config-validation") is False and accepted > 0):
        _fail("customer-otlp-receiver-qualification.report.checks-invalid")
    if by_id.get("verified-tls") != (delivered == 3) or by_id.get("client-certificate-authentication") != (delivered == 3):
        _fail("customer-otlp-receiver-qualification.report.checks-invalid")
    for identifier in (
        "source-binding", "immutable-release", "receiver-endpoint-binding", "protected-input-files",
        "collector-image-pinned", "minimized-output",
    ):
        if by_id.get(identifier) is not True:
            _fail("customer-otlp-receiver-qualification.report.checks-invalid")
    summary = _summary(checks)
    if spec.get("summary") != summary or spec.get("status") != summary["overallStatus"]:
        _fail("customer-otlp-receiver-qualification.report.summary-invalid")
    metadata_without_id = dict(metadata)
    identifier = metadata_without_id.pop("id", None)
    if not isinstance(identifier, str) or REPORT_ID.fullmatch(identifier) is None or identifier != _report_identifier(metadata_without_id, spec):
        _fail("customer-otlp-receiver-qualification.report.id-invalid")


def qualify(
    *,
    profile: Mapping[str, Any],
    api_base_url: str,
    receiver_endpoint: str,
    api_token_path: Path,
    metrics_token_path: Path,
    logs_token_path: Path,
    traces_token_path: Path,
    receiver_ca_path: Path,
    client_certificate_path: Path,
    client_key_path: Path,
    image_digest: str,
    allow_observation: bool,
    api_ca_path: Path | None = None,
    docker: str = "docker",
    probe: CollectorProbe | None = None,
    now: datetime | None = None,
    api_identity_valid: bool | None = None,
    direct_receiver_accepted_count: int | None = None,
) -> dict[str, Any]:
    if not allow_observation:
        _fail("customer-otlp-receiver-qualification.enable.required")
    validate_profile(profile)
    if DIGEST.fullmatch(image_digest) is None:
        _fail("customer-otlp-receiver-qualification.image.invalid")
    canonical_api = _root_https_url(api_base_url, "customer-otlp-receiver-qualification.api-target.invalid")
    canonical_receiver = _root_https_url(receiver_endpoint, "customer-otlp-receiver-qualification.receiver-target.invalid")
    spec = _mapping(profile.get("spec"), "customer-otlp-receiver-qualification.profile.invalid")
    if spec.get("receiverEndpoint") != canonical_receiver:
        _fail("customer-otlp-receiver-qualification.receiver-target.crossed")
    api_token = _token(api_token_path, "customer-otlp-receiver-qualification.api-credential.invalid")
    tokens = {
        "metrics": _token(metrics_token_path, "customer-otlp-receiver-qualification.channel-credential.invalid"),
        "logs": _token(logs_token_path, "customer-otlp-receiver-qualification.channel-credential.invalid"),
        "traces": _token(traces_token_path, "customer-otlp-receiver-qualification.channel-credential.invalid"),
    }
    credentials_separate = len({api_token, *tokens.values()}) == 4
    receiver_ca_file, receiver_ca = _read_file(receiver_ca_path, code="customer-otlp-receiver-qualification.receiver-ca.invalid", maximum_bytes=2 * 1024 * 1024, protected=False)
    certificate_file, certificate = _read_file(client_certificate_path, code="customer-otlp-receiver-qualification.client-certificate.invalid", maximum_bytes=2 * 1024 * 1024, protected=False)
    key_file, key = _read_file(client_key_path, code="customer-otlp-receiver-qualification.client-key.invalid", maximum_bytes=65536, protected=True)
    api_ca = None
    if api_ca_path is not None:
        _, api_ca = _read_file(api_ca_path, code="customer-otlp-receiver-qualification.api-ca.invalid", maximum_bytes=2 * 1024 * 1024, protected=False)
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-otlp-receiver-qualification.source.dirty")
    try:
        repository = ingress._repository_identity()
    except ingress.IngressQualificationError:
        _fail("customer-otlp-receiver-qualification.source.identity-invalid")
    expected = {
        "applicationVersion": repository["applicationVersion"],
        "contractsApiVersion": API_VERSION,
        "requiredMigration": repository["requiredMigration"],
        "buildMode": "release",
        "sourceRevision": revision,
        "chartVersion": repository["chartVersion"],
        "imageDigest": image_digest,
    }
    objective = _mapping(spec.get("objective"), "customer-otlp-receiver-qualification.profile.invalid")
    observed_now = now or datetime.now(timezone.utc)
    if observed_now.tzinfo is None or observed_now.utcoffset() is None:
        _fail("customer-otlp-receiver-qualification.time.invalid")
    started = observed_now.astimezone(timezone.utc)
    api_valid = api_identity_valid if api_identity_valid is not None else _api_identity(
        base_url=canonical_api,
        token=api_token,
        ca_payload=api_ca,
        expected=expected,
        timeout_seconds=int(objective["requestTimeoutSeconds"]),
    )
    payloads = _payloads(profile, started)
    direct_accepted = (
        direct_receiver_accepted_count
        if direct_receiver_accepted_count is not None
        else _direct_receiver_delivery(
            receiver_endpoint=canonical_receiver,
            tokens=tokens,
            receiver_ca_path=receiver_ca_file,
            client_certificate_path=certificate_file,
            client_key_path=key_file,
            timeout_seconds=int(objective["requestTimeoutSeconds"]),
        )
    )
    selected_probe = probe or DockerCollectorProbe(
        docker=docker,
        receiver_endpoint=canonical_receiver,
        tokens=tokens,
        receiver_ca=receiver_ca,
        client_certificate=certificate,
        client_key=key,
    )
    result = selected_probe.run(
        payloads,
        request_timeout_seconds=int(objective["requestTimeoutSeconds"]),
        delivery_timeout_seconds=int(objective["deliveryTimeoutSeconds"]),
        poll_interval_milliseconds=int(objective["pollIntervalMilliseconds"]),
    )
    completed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return build_report(
        revision=revision,
        repository=repository,
        image_digest=image_digest,
        api_target_digest=_digest_value(canonical_api),
        profile=profile,
        api_ca_digest=_digest_bytes(api_ca) if api_ca is not None else _digest_value("system-trust"),
        receiver_ca_digest=_digest_bytes(receiver_ca),
        client_certificate_digest=_digest_bytes(certificate),
        started_at=started,
        completed_at=completed,
        api_identity_valid=api_valid,
        credentials_separate=credentials_separate,
        direct_receiver_accepted_count=direct_accepted,
        result=result,
    )


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        _fail("customer-otlp-receiver-qualification.output.invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except OSError:
        _fail("customer-otlp-receiver-qualification.output.invalid")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def verify_report(
    *,
    report_path: Path,
    profile_path: Path,
    api_base_url: str,
    receiver_endpoint: str,
    receiver_ca_path: Path,
    client_certificate_path: Path,
    image_digest: str,
    api_ca_path: Path | None = None,
    require_qualified: bool = False,
) -> Mapping[str, Any]:
    _, raw = _read_file(report_path, code="customer-otlp-receiver-qualification.report.unreadable", maximum_bytes=1024 * 1024, protected=False)
    try:
        report = _mapping(json.loads(raw.decode("utf-8")), "customer-otlp-receiver-qualification.report.unreadable")
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-otlp-receiver-qualification.report.unreadable")
    validate_report_document(report)
    profile = load_profile(profile_path)
    _, receiver_ca = _read_file(receiver_ca_path, code="customer-otlp-receiver-qualification.receiver-ca.invalid", maximum_bytes=2 * 1024 * 1024, protected=False)
    _, certificate = _read_file(client_certificate_path, code="customer-otlp-receiver-qualification.client-certificate.invalid", maximum_bytes=2 * 1024 * 1024, protected=False)
    api_ca = None
    if api_ca_path is not None:
        _, api_ca = _read_file(api_ca_path, code="customer-otlp-receiver-qualification.api-ca.invalid", maximum_bytes=2 * 1024 * 1024, protected=False)
    revision, dirty = _source_identity()
    try:
        repository = ingress._repository_identity()
    except ingress.IngressQualificationError:
        _fail("customer-otlp-receiver-qualification.source.identity-invalid")
    canonical_api = _root_https_url(api_base_url, "customer-otlp-receiver-qualification.api-target.invalid")
    canonical_receiver = _root_https_url(receiver_endpoint, "customer-otlp-receiver-qualification.receiver-target.invalid")
    spec = _mapping(report.get("spec"), "customer-otlp-receiver-qualification.report.invalid")
    subject = _mapping(spec.get("subject"), "customer-otlp-receiver-qualification.report.invalid")
    bindings = _mapping(spec.get("bindings"), "customer-otlp-receiver-qualification.report.invalid")
    profile_spec = _mapping(profile.get("spec"), "customer-otlp-receiver-qualification.profile.invalid")
    if (
        dirty
        or subject != {
            "applicationVersion": repository["applicationVersion"],
            "contractsApiVersion": API_VERSION,
            "sourceRevision": revision,
            "chartVersion": repository["chartVersion"],
            "requiredMigration": repository["requiredMigration"],
            "imageDigest": image_digest,
        }
        or profile_spec.get("receiverEndpoint") != canonical_receiver
        or bindings.get("apiTargetBindingDigest") != _digest_value(canonical_api)
        or bindings.get("receiverEndpointBindingDigest") != _digest_value(canonical_receiver)
        or bindings.get("profileDigest") != _digest_value(profile)
        or bindings.get("signalSetDigest") != _digest_value(profile_spec.get("signals"))
        or bindings.get("apiCaBundleDigest") != (_digest_bytes(api_ca) if api_ca is not None else _digest_value("system-trust"))
        or bindings.get("receiverCaBundleDigest") != _digest_bytes(receiver_ca)
        or bindings.get("clientCertificateDigest") != _digest_bytes(certificate)
    ):
        _fail("customer-otlp-receiver-qualification.report.crossed")
    if require_qualified and spec.get("status") != "qualified":
        _fail("customer-otlp-receiver-qualification.report.not-qualified")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    qualify_parser = subparsers.add_parser("qualify")
    qualify_parser.add_argument("--profile", type=Path, required=True)
    qualify_parser.add_argument("--api-base-url", required=True)
    qualify_parser.add_argument("--receiver-endpoint", required=True)
    qualify_parser.add_argument("--api-token-file", type=Path, required=True)
    qualify_parser.add_argument("--api-ca-file", type=Path)
    qualify_parser.add_argument("--metrics-token-file", type=Path, required=True)
    qualify_parser.add_argument("--logs-token-file", type=Path, required=True)
    qualify_parser.add_argument("--traces-token-file", type=Path, required=True)
    qualify_parser.add_argument("--receiver-ca-file", type=Path, required=True)
    qualify_parser.add_argument("--client-certificate-file", type=Path, required=True)
    qualify_parser.add_argument("--client-key-file", type=Path, required=True)
    qualify_parser.add_argument("--image-digest", required=True)
    qualify_parser.add_argument("--docker", default=os.environ.get("IIP_DOCKER_BIN", "docker"))
    qualify_parser.add_argument("--output", type=Path, required=True)
    qualify_parser.add_argument("--allow-observation", action="store_true")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--report", type=Path, required=True)
    verify_parser.add_argument("--profile", type=Path, required=True)
    verify_parser.add_argument("--api-base-url", required=True)
    verify_parser.add_argument("--receiver-endpoint", required=True)
    verify_parser.add_argument("--api-ca-file", type=Path)
    verify_parser.add_argument("--receiver-ca-file", type=Path, required=True)
    verify_parser.add_argument("--client-certificate-file", type=Path, required=True)
    verify_parser.add_argument("--image-digest", required=True)
    verify_parser.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "qualify":
            report = qualify(
                profile=load_profile(arguments.profile),
                api_base_url=arguments.api_base_url,
                receiver_endpoint=arguments.receiver_endpoint,
                api_token_path=arguments.api_token_file,
                api_ca_path=arguments.api_ca_file,
                metrics_token_path=arguments.metrics_token_file,
                logs_token_path=arguments.logs_token_file,
                traces_token_path=arguments.traces_token_file,
                receiver_ca_path=arguments.receiver_ca_file,
                client_certificate_path=arguments.client_certificate_file,
                client_key_path=arguments.client_key_file,
                image_digest=arguments.image_digest,
                allow_observation=arguments.allow_observation,
                docker=arguments.docker,
            )
            _write_report(arguments.output, report)
            print(f"customer OTLP receiver qualification {report['spec']['status']}: {arguments.output}")
            return 0 if report["spec"]["status"] == "qualified" else 1
        verify_report(
            report_path=arguments.report,
            profile_path=arguments.profile,
            api_base_url=arguments.api_base_url,
            receiver_endpoint=arguments.receiver_endpoint,
            api_ca_path=arguments.api_ca_file,
            receiver_ca_path=arguments.receiver_ca_file,
            client_certificate_path=arguments.client_certificate_file,
            image_digest=arguments.image_digest,
            require_qualified=arguments.require_qualified,
        )
        print("customer OTLP receiver qualification report verified")
    except CustomerOtlpReceiverQualificationError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
