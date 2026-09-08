#!/usr/bin/env python3
"""Qualify one customer Prometheus and notification route for IIP alerts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import ssl
import stat
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
API_VERSION = "iip.platform/v1alpha1"
PROFILE_KIND = "CustomerOperationalAlertQualificationProfile"
REPORT_KIND = "CustomerOperationalAlertQualificationReport"
QUALIFICATION = "customer-operational-alert-routing-v1"
METRIC_NAME_PROFILE = "otel-prometheus-underscore-no-suffix-v1"
PROFILE_SCHEMA = ROOT / "contracts/schemas/customer-operational-alert-qualification-profile.schema.json"
REPORT_SCHEMA = ROOT / "contracts/schemas/customer-operational-alert-qualification-report.schema.json"
CORE_RULES = (
    "IIPApiTelemetryAbsent",
    "IIPWorkflowWorkerTelemetryAbsent",
    "IIPQueryAvailabilityBelowObjective",
    "IIPIngestionFreshnessObjectiveViolated",
    "IIPTelemetryRecordingFailures",
)
AI_RULES = CORE_RULES + (
    "IIPOtlpReceiverTelemetryAbsent",
    "IIPOtlpReceiverAvailabilityBelowObjective",
    "IIPAiUsageCoverageIncomplete",
    "IIPAiCostCoverageUnresolved",
)
SERVICE_COMPONENTS = {
    "core-v1": ("api", "workflow-worker"),
    "ai-finops-v0": ("api", "workflow-worker", "otlp-receiver"),
}
RULES = {"core-v1": CORE_RULES, "ai-finops-v0": AI_RULES}
CHECK_IDS = (
    "profile-binding",
    "source-binding",
    "immutable-release",
    "protected-input-files",
    "verified-https",
    "no-proxy-no-redirect",
    "prometheus-ready",
    "production-rule-group-loaded",
    "production-rules-complete",
    "production-rules-healthy",
    "component-heartbeats-observed",
    "synthetic-rule-loaded",
    "synthetic-rule-healthy",
    "synthetic-rule-inactive",
    "alertmanager-ready",
    "receipt-evidence-current",
    "firing-notification-delivered",
    "recovery-notification-delivered",
    "notification-latency-objective",
    "minimized-output",
)
LIMITATIONS = (
    "customer-receipt-service-authenticity-and-retention-not-qualified",
    "additional-routes-receivers-silences-and-inhibition-not-qualified",
    "human-on-call-acknowledgement-and-escalation-not-qualified",
    "collector-backend-and-alertmanager-ha-not-qualified",
    "long-window-regional-slo-and-disaster-recovery-not-qualified",
    "real-component-failure-and-customer-workload-impact-not-qualified",
)
REPORT_ID = re.compile(r"^coar_[a-f0-9]{32}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
MIGRATION = re.compile(r"^[0-9]{4}_[a-z0-9_]+\.sql$")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}$")
SAFE_TOKEN = re.compile(r"^[!-~]{16,8192}$")
MAX_FILE_BYTES = 1024 * 1024
FORBIDDEN_REPORT_KEYS = frozenset(
    {
        "url",
        "endpoint",
        "target",
        "token",
        "credential",
        "secret",
        "certificate",
        "serviceName",
        "routeId",
        "probeId",
        "labels",
    }
)


class CustomerOperationalAlertQualificationError(RuntimeError):
    """Stable failure for unsafe, incomplete, or crossed alert evidence."""


@dataclass(frozen=True)
class ProbeResult:
    """Only bounded aggregate observations from the three customer endpoints."""

    prometheus_ready: bool
    verified_https: bool
    production_group_loaded: bool
    loaded_expected_rule_count: int
    unhealthy_expected_rule_count: int
    observed_component_count: int
    synthetic_rule_loaded: bool
    synthetic_rule_healthy: bool
    synthetic_rule_inactive: bool
    alertmanager_ready: bool
    receipt_evidence_current: bool
    firing_notification_count: int
    recovery_notification_count: int
    maximum_notification_latency_milliseconds: int


def _fail(code: str) -> None:
    raise CustomerOperationalAlertQualificationError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _integer(value: object, minimum: int, maximum: int, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail(code)
    return value


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        _fail("customer-operational-alert-qualification.time.invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _normalized_instant(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        _fail("customer-operational-alert-qualification.time.invalid")
    return value.astimezone(timezone.utc)


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


def _schema(path: Path, code: str) -> Mapping[str, Any]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), code)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)


def _schema_validate(document: Mapping[str, Any], path: Path, code: str) -> None:
    errors = list(
        Draft202012Validator(
            _schema(path, code), format_checker=FormatChecker()
        ).iter_errors(document)
    )
    if errors:
        _fail(code)


def _strict_https_url(value: object, code: str, *, root_only: bool) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 2048
        or re.search(r"[\x00-\x20\x7f]", value) is not None
    ):
        _fail(code)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        _fail(code)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or not parsed.hostname.isascii()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port == 0
        or (root_only and parsed.path not in ("", "/"))
        or (not root_only and parsed.path in ("", "/"))
    ):
        _fail(code)
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port is not None:
        host = f"{host}:{port}"
    path = "" if root_only else parsed.path
    return urlunsplit(("https", host, path, "", ""))


def _read_file(
    path: Path, *, code: str, protected: bool, maximum_bytes: int = MAX_FILE_BYTES
) -> bytes:
    candidate = path.expanduser()
    descriptor = -1
    try:
        if candidate.is_symlink() or not candidate.is_file():
            _fail(code)
        mode = stat.S_IMODE(candidate.stat().st_mode)
        if protected and mode & 0o077:
            _fail(code)
        size = candidate.stat().st_size
        if size < 1 or size > maximum_bytes:
            _fail(code)
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        payload = os.read(descriptor, maximum_bytes + 1)
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not 1 <= len(payload) <= maximum_bytes:
        _fail(code)
    return payload


def load_profile(path: Path) -> Mapping[str, Any]:
    payload = _read_file(
        path,
        code="customer-operational-alert-qualification.profile.unreadable",
        protected=True,
    )
    try:
        profile = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-operational-alert-qualification.profile.unreadable")
    profile = _mapping(
        profile, "customer-operational-alert-qualification.profile.invalid"
    )
    validate_profile(profile)
    return profile


def load_token(path: Path) -> str:
    payload = _read_file(
        path,
        code="customer-operational-alert-qualification.credential.invalid",
        protected=True,
        maximum_bytes=8193,
    )
    try:
        value = payload.decode("ascii").strip()
    except UnicodeDecodeError:
        _fail("customer-operational-alert-qualification.credential.invalid")
    if SAFE_TOKEN.fullmatch(value) is None:
        _fail("customer-operational-alert-qualification.credential.invalid")
    return value


def validate_profile(profile: Mapping[str, Any]) -> None:
    code = "customer-operational-alert-qualification.profile.invalid"
    _schema_validate(profile, PROFILE_SCHEMA, code)
    if profile.get("apiVersion") != API_VERSION or profile.get("kind") != PROFILE_KIND:
        _fail(code)
    metadata = _mapping(profile.get("metadata"), code)
    spec = _mapping(profile.get("spec"), code)
    deployment = _mapping(spec.get("deployment"), code)
    monitoring = _mapping(spec.get("monitoring"), code)
    objective = _mapping(spec.get("objective"), code)
    reviewed_at = _parse_timestamp(metadata.get("reviewedAt"), code)
    if reviewed_at.microsecond:
        _fail(code)
    rule_set = spec.get("ruleSet")
    if rule_set not in RULES:
        _fail(code)
    for key in ("clusterBindingDigest", "namespaceBindingDigest"):
        value = deployment.get(key)
        if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
            _fail(code)
    _strict_https_url(monitoring.get("prometheusBaseUrl"), code, root_only=True)
    _strict_https_url(monitoring.get("alertmanagerBaseUrl"), code, root_only=True)
    _strict_https_url(monitoring.get("receiptUrl"), code, root_only=False)
    for key in ("productionRuleGroup", "syntheticRuleGroup", "syntheticAlertName"):
        value = monitoring.get(key)
        if not isinstance(value, str) or SAFE_VALUE.fullmatch(value) is None:
            _fail(code)
    if monitoring.get("productionRuleGroup") != "iip.platform.availability":
        _fail(code)
    if monitoring.get("syntheticRuleGroup") == monitoring.get("productionRuleGroup"):
        _fail(code)
    if monitoring.get("syntheticAlertName") != "IIPQualificationSynthetic":
        _fail(code)
    services = monitoring.get("services")
    if not isinstance(services, list):
        _fail(code)
    components: list[str] = []
    names: list[str] = []
    for item in services:
        service = _mapping(item, code)
        component = service.get("component")
        name = service.get("serviceName")
        if not isinstance(component, str) or not isinstance(name, str):
            _fail(code)
        if SAFE_VALUE.fullmatch(name) is None:
            _fail(code)
        components.append(component)
        names.append(name)
    if tuple(components) != SERVICE_COMPONENTS[rule_set] or len(set(names)) != len(names):
        _fail(code)
    for key, minimum, maximum in (
        ("requestTimeoutMilliseconds", 100, 30_000),
        ("maximumResponseBytes", 4_096, MAX_FILE_BYTES),
        ("maximumObservationAgeSeconds", 30, 3_600),
        ("maximumNotificationLatencyMilliseconds", 100, 300_000),
        ("maximumClockSkewSeconds", 0, 300),
        ("maximumProfileAgeSeconds", 3_600, 7_776_000),
        ("reportValiditySeconds", 300, 604_800),
    ):
        _integer(objective.get(key), minimum, maximum, code)


def _source_identity() -> tuple[str, bool]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        _fail("customer-operational-alert-qualification.source.unavailable")
    if REVISION.fullmatch(revision) is None:
        _fail("customer-operational-alert-qualification.source.invalid")
    return revision, dirty


def _repository_identity() -> Mapping[str, str]:
    try:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        application_version = project["project"]["version"]
        chart = (ROOT / "deploy/helm/infra-intelligence/Chart.yaml").read_text(
            encoding="utf-8"
        )
        migrations = sorted(
            (ROOT / "src/iip/adapters/postgres/migrations").glob("*.sql")
        )
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        _fail("customer-operational-alert-qualification.source.identity-invalid")
    chart_match = re.search(r"^version:\s*([^\s]+)\s*$", chart, re.MULTILINE)
    if (
        not isinstance(application_version, str)
        or SEMVER.fullmatch(application_version) is None
        or chart_match is None
        or SEMVER.fullmatch(chart_match.group(1)) is None
        or not migrations
        or MIGRATION.fullmatch(migrations[-1].name) is None
    ):
        _fail("customer-operational-alert-qualification.source.identity-invalid")
    return {
        "applicationVersion": application_version,
        "chartVersion": chart_match.group(1),
        "requiredMigration": migrations[-1].name,
    }


class _DenyRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, url):
        del request, file_pointer, code, message, headers, url
        return None


def _request_json(
    url: str,
    *,
    token: str,
    ca_file: Path,
    timeout_seconds: float,
    maximum_bytes: int,
) -> Mapping[str, Any] | None:
    try:
        context = ssl.create_default_context(cafile=str(ca_file.expanduser()))
        opener = build_opener(
            ProxyHandler({}), HTTPSHandler(context=context), _DenyRedirects()
        )
        response = opener.open(
            Request(
                url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "iip-customer-operational-alert-qualification/1",
                },
                method="GET",
            ),
            timeout=timeout_seconds,
        )
        try:
            if response.status != 200:
                return None
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > maximum_bytes:
                return None
            payload = response.read(maximum_bytes + 1)
        finally:
            response.close()
        if len(payload) > maximum_bytes:
            return None
        value = json.loads(payload.decode("utf-8"))
    except (
        OSError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        HTTPError,
        URLError,
    ):
        return None
    return value if isinstance(value, Mapping) else None


def _api_url(base: str, path: str, query: Mapping[str, str] | None = None) -> str:
    parsed = urlsplit(base)
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            urlencode(query or {}, safe="()_"),
            "",
        )
    )


def _prometheus_success(value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if value is None or value.get("status") != "success":
        return None
    data = value.get("data")
    return data if isinstance(data, Mapping) else None


def _receipt_result(
    value: Mapping[str, Any] | None,
    *,
    probe_id: str,
    route_id: str,
    alert_name: str,
    now: datetime,
    maximum_age_seconds: int,
    maximum_future_skew_seconds: int,
) -> tuple[bool, int, int, int]:
    if value is None or set(value) != {"apiVersion", "kind", "spec"}:
        return False, 0, 0, 0
    if value.get("apiVersion") != "iip.qualification/v1" or value.get("kind") != "NotificationReceipt":
        return False, 0, 0, 0
    spec = value.get("spec")
    if not isinstance(spec, Mapping) or set(spec) != {
        "probeId",
        "alertName",
        "routeId",
        "events",
    }:
        return False, 0, 0, 0
    if (
        spec.get("probeId") != probe_id
        or spec.get("alertName") != alert_name
        or spec.get("routeId") != route_id
    ):
        return False, 0, 0, 0
    events = spec.get("events")
    if not isinstance(events, list) or len(events) != 2:
        return False, 0, 0, 0
    expected_states = ("firing", "resolved")
    latencies: list[int] = []
    source_times: list[datetime] = []
    received_times: list[datetime] = []
    for event, expected_state in zip(events, expected_states, strict=True):
        if not isinstance(event, Mapping) or set(event) != {
            "state",
            "sourceAt",
            "receivedAt",
        }:
            return False, 0, 0, 0
        if event.get("state") != expected_state:
            return False, 0, 0, 0
        try:
            source_at = _parse_timestamp(
                event.get("sourceAt"),
                "customer-operational-alert-qualification.receipt.invalid",
            )
            received_at = _parse_timestamp(
                event.get("receivedAt"),
                "customer-operational-alert-qualification.receipt.invalid",
            )
        except CustomerOperationalAlertQualificationError:
            return False, 0, 0, 0
        if (
            received_at < source_at
            or received_at
            > now + timedelta(seconds=maximum_future_skew_seconds)
            or now - source_at > timedelta(seconds=maximum_age_seconds)
            or now - received_at > timedelta(seconds=maximum_age_seconds)
        ):
            return False, 0, 0, 0
        received_times.append(received_at)
        source_times.append(source_at)
        latencies.append(int((received_at - source_at).total_seconds() * 1000))
    if source_times[1] < source_times[0] or received_times[1] < received_times[0]:
        return False, 0, 0, 0
    return True, 1, 1, max(latencies)


class LiveAlertProbe:
    """Read only bounded monitoring APIs selected by a protected profile."""

    def __init__(
        self,
        *,
        prometheus_token: str,
        alertmanager_token: str,
        receipt_token: str,
        prometheus_ca_file: Path,
        alertmanager_ca_file: Path,
        receipt_ca_file: Path,
    ) -> None:
        self._prometheus_token = prometheus_token
        self._alertmanager_token = alertmanager_token
        self._receipt_token = receipt_token
        self._prometheus_ca_file = prometheus_ca_file
        self._alertmanager_ca_file = alertmanager_ca_file
        self._receipt_ca_file = receipt_ca_file

    def run(self, profile: Mapping[str, Any], *, now: datetime) -> ProbeResult:
        validate_profile(profile)
        now = _normalized_instant(now)
        spec = _mapping(
            profile.get("spec"),
            "customer-operational-alert-qualification.profile.invalid",
        )
        monitoring = _mapping(
            spec.get("monitoring"),
            "customer-operational-alert-qualification.profile.invalid",
        )
        objective = _mapping(
            spec.get("objective"),
            "customer-operational-alert-qualification.profile.invalid",
        )
        timeout = int(objective["requestTimeoutMilliseconds"]) / 1000
        maximum_bytes = int(objective["maximumResponseBytes"])
        prometheus = _strict_https_url(
            monitoring["prometheusBaseUrl"],
            "customer-operational-alert-qualification.profile.invalid",
            root_only=True,
        )
        alertmanager = _strict_https_url(
            monitoring["alertmanagerBaseUrl"],
            "customer-operational-alert-qualification.profile.invalid",
            root_only=True,
        )
        receipt = _strict_https_url(
            monitoring["receiptUrl"],
            "customer-operational-alert-qualification.profile.invalid",
            root_only=False,
        )
        build = _prometheus_success(
            _request_json(
                _api_url(prometheus, "/api/v1/status/buildinfo"),
                token=self._prometheus_token,
                ca_file=self._prometheus_ca_file,
                timeout_seconds=timeout,
                maximum_bytes=maximum_bytes,
            )
        )
        rules_data = _prometheus_success(
            _request_json(
                _api_url(prometheus, "/api/v1/rules", {"type": "alert"}),
                token=self._prometheus_token,
                ca_file=self._prometheus_ca_file,
                timeout_seconds=timeout,
                maximum_bytes=maximum_bytes,
            )
        )
        rule_set = str(spec["ruleSet"])
        expected_rules = set(RULES[rule_set])
        loaded: set[str] = set()
        unhealthy: set[str] = set()
        production_group_loaded = False
        synthetic_rule_loaded = False
        synthetic_rule_healthy = False
        synthetic_rule_inactive = False
        groups = rules_data.get("groups") if rules_data is not None else None
        if isinstance(groups, list):
            for raw_group in groups:
                if not isinstance(raw_group, Mapping):
                    continue
                group_name = raw_group.get("name")
                raw_rules = raw_group.get("rules")
                if not isinstance(raw_rules, list):
                    continue
                if group_name == monitoring["productionRuleGroup"]:
                    production_group_loaded = True
                    for raw_rule in raw_rules:
                        if not isinstance(raw_rule, Mapping):
                            continue
                        name = raw_rule.get("name")
                        if name in expected_rules:
                            loaded.add(str(name))
                            if raw_rule.get("health") != "ok":
                                unhealthy.add(str(name))
                if group_name == monitoring["syntheticRuleGroup"]:
                    for raw_rule in raw_rules:
                        if (
                            isinstance(raw_rule, Mapping)
                            and raw_rule.get("name") == monitoring["syntheticAlertName"]
                        ):
                            synthetic_rule_loaded = True
                            synthetic_rule_healthy = raw_rule.get("health") == "ok"
                            synthetic_rule_inactive = raw_rule.get("state") == "inactive"
        heartbeat_data = _prometheus_success(
            _request_json(
                _api_url(
                    prometheus,
                    "/api/v1/query",
                    {"query": "count by(job) (iip_telemetry_heartbeat)"},
                ),
                token=self._prometheus_token,
                ca_file=self._prometheus_ca_file,
                timeout_seconds=timeout,
                maximum_bytes=maximum_bytes,
            )
        )
        expected_services = {
            str(item["serviceName"]) for item in monitoring["services"]
        }
        observed_services: set[str] = set()
        result = (
            heartbeat_data.get("result")
            if heartbeat_data is not None
            and heartbeat_data.get("resultType") == "vector"
            else None
        )
        if isinstance(result, list):
            for item in result:
                metric = item.get("metric") if isinstance(item, Mapping) else None
                value = item.get("value") if isinstance(item, Mapping) else None
                job = metric.get("job") if isinstance(metric, Mapping) else None
                try:
                    positive = (
                        isinstance(value, list)
                        and len(value) == 2
                        and float(value[1]) > 0
                    )
                except (TypeError, ValueError):
                    positive = False
                if job in expected_services and positive:
                    observed_services.add(str(job))
        alertmanager_status = _request_json(
            _api_url(alertmanager, "/api/v2/status"),
            token=self._alertmanager_token,
            ca_file=self._alertmanager_ca_file,
            timeout_seconds=timeout,
            maximum_bytes=maximum_bytes,
        )
        cluster = (
            alertmanager_status.get("cluster")
            if isinstance(alertmanager_status, Mapping)
            else None
        )
        alertmanager_ready = (
            isinstance(cluster, Mapping) and cluster.get("status") in {"ready", "disabled"}
        )
        receipt_value = _request_json(
            receipt + "?" + urlencode({"probeId": monitoring["probeId"]}),
            token=self._receipt_token,
            ca_file=self._receipt_ca_file,
            timeout_seconds=timeout,
            maximum_bytes=maximum_bytes,
        )
        receipt_current, firing_count, recovery_count, maximum_latency = _receipt_result(
            receipt_value,
            probe_id=str(monitoring["probeId"]),
            route_id=str(monitoring["routeId"]),
            alert_name=str(monitoring["syntheticAlertName"]),
            now=now,
            maximum_age_seconds=int(objective["maximumObservationAgeSeconds"]),
            maximum_future_skew_seconds=int(
                objective["maximumClockSkewSeconds"]
            ),
        )
        return ProbeResult(
            prometheus_ready=build is not None,
            verified_https=(
                build is not None
                and rules_data is not None
                and heartbeat_data is not None
                and alertmanager_status is not None
                and receipt_value is not None
            ),
            production_group_loaded=production_group_loaded,
            loaded_expected_rule_count=len(loaded),
            unhealthy_expected_rule_count=len(unhealthy),
            observed_component_count=len(observed_services),
            synthetic_rule_loaded=synthetic_rule_loaded,
            synthetic_rule_healthy=synthetic_rule_healthy,
            synthetic_rule_inactive=synthetic_rule_inactive,
            alertmanager_ready=alertmanager_ready,
            receipt_evidence_current=receipt_current,
            firing_notification_count=firing_count,
            recovery_notification_count=recovery_count,
            maximum_notification_latency_milliseconds=maximum_latency,
        )


def _check(identifier: str, passed: bool) -> Mapping[str, str]:
    if passed:
        return {"id": identifier, "status": "passed"}
    return {
        "id": identifier,
        "status": "failed",
        "errorCode": f"customer-operational-alert-qualification.{identifier}.failed",
    }


def _summary(checks: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
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
        return any(
            key in FORBIDDEN_REPORT_KEYS
            or _has_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(child) for child in value)
    return False


def _report_identifier(metadata: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    return "coar_" + hashlib.sha256(_canonical({"metadata": metadata, "spec": spec})).hexdigest()[:32]


def build_report(
    *,
    revision: str,
    repository: Mapping[str, str],
    image_digest: str,
    profile: Mapping[str, Any],
    prometheus_ca_digest: str,
    alertmanager_ca_digest: str,
    receipt_ca_digest: str,
    started_at: datetime,
    completed_at: datetime,
    protected_inputs: bool,
    result: ProbeResult,
) -> Mapping[str, Any]:
    validate_profile(profile)
    if REVISION.fullmatch(revision) is None or DIGEST.fullmatch(image_digest) is None:
        _fail("customer-operational-alert-qualification.source.invalid")
    if (
        set(repository)
        != {"applicationVersion", "chartVersion", "requiredMigration"}
        or SEMVER.fullmatch(repository.get("applicationVersion", "")) is None
        or SEMVER.fullmatch(repository.get("chartVersion", "")) is None
        or MIGRATION.fullmatch(repository.get("requiredMigration", "")) is None
    ):
        _fail("customer-operational-alert-qualification.source.identity-invalid")
    for digest in (prometheus_ca_digest, alertmanager_ca_digest, receipt_ca_digest):
        if DIGEST.fullmatch(digest) is None:
            _fail("customer-operational-alert-qualification.binding.invalid")
    started_at = _normalized_instant(started_at)
    completed_at = _normalized_instant(completed_at)
    if completed_at < started_at:
        _fail("customer-operational-alert-qualification.time.invalid")
    spec_profile = _mapping(
        profile.get("spec"), "customer-operational-alert-qualification.profile.invalid"
    )
    metadata_profile = _mapping(
        profile.get("metadata"), "customer-operational-alert-qualification.profile.invalid"
    )
    monitoring = _mapping(
        spec_profile.get("monitoring"),
        "customer-operational-alert-qualification.profile.invalid",
    )
    deployment = _mapping(
        spec_profile.get("deployment"),
        "customer-operational-alert-qualification.profile.invalid",
    )
    objective = dict(
        _mapping(
            spec_profile.get("objective"),
            "customer-operational-alert-qualification.profile.invalid",
        )
    )
    rule_set = str(spec_profile["ruleSet"])
    expected_rule_count = len(RULES[rule_set])
    expected_component_count = len(SERVICE_COMPONENTS[rule_set])
    profile_age = int(
        (completed_at - _parse_timestamp(metadata_profile["reviewedAt"], "customer-operational-alert-qualification.time.invalid")).total_seconds()
    )
    subject = {
        "applicationVersion": repository["applicationVersion"],
        "chartVersion": repository["chartVersion"],
        "contractsApiVersion": API_VERSION,
        "requiredMigration": repository["requiredMigration"],
        "sourceRevision": revision,
        "imageDigest": image_digest,
    }
    measurements = {
        "profileReviewedAt": metadata_profile["reviewedAt"],
        "startedAt": _timestamp(started_at),
        "completedAt": _timestamp(completed_at),
        "profileAgeSeconds": max(0, profile_age),
        "expectedRuleCount": expected_rule_count,
        "loadedExpectedRuleCount": result.loaded_expected_rule_count,
        "unhealthyExpectedRuleCount": result.unhealthy_expected_rule_count,
        "expectedComponentCount": expected_component_count,
        "observedComponentCount": result.observed_component_count,
        "receiptEventCount": result.firing_notification_count
        + result.recovery_notification_count,
        "firingNotificationCount": result.firing_notification_count,
        "recoveryNotificationCount": result.recovery_notification_count,
        "maximumNotificationLatencyMilliseconds": result.maximum_notification_latency_milliseconds,
    }
    observations = {
        "verifiedHttps": result.verified_https,
        "protectedInputs": protected_inputs,
        "prometheusReady": result.prometheus_ready,
        "productionGroupLoaded": result.production_group_loaded,
        "syntheticRuleLoaded": result.synthetic_rule_loaded,
        "syntheticRuleHealthy": result.synthetic_rule_healthy,
        "syntheticRuleInactive": result.synthetic_rule_inactive,
        "alertmanagerReady": result.alertmanager_ready,
        "receiptEvidenceCurrent": result.receipt_evidence_current,
    }
    conditions = {
        "profile-binding": profile_age >= 0
        and profile_age <= int(objective["maximumProfileAgeSeconds"]),
        "source-binding": True,
        "immutable-release": True,
        "protected-input-files": observations["protectedInputs"],
        "verified-https": observations["verifiedHttps"],
        "no-proxy-no-redirect": True,
        "prometheus-ready": observations["prometheusReady"],
        "production-rule-group-loaded": observations["productionGroupLoaded"],
        "production-rules-complete": result.loaded_expected_rule_count
        == expected_rule_count,
        "production-rules-healthy": result.loaded_expected_rule_count
        == expected_rule_count
        and result.unhealthy_expected_rule_count == 0,
        "component-heartbeats-observed": result.observed_component_count
        == expected_component_count,
        "synthetic-rule-loaded": observations["syntheticRuleLoaded"],
        "synthetic-rule-healthy": observations["syntheticRuleHealthy"],
        "synthetic-rule-inactive": observations["syntheticRuleInactive"],
        "alertmanager-ready": observations["alertmanagerReady"],
        "receipt-evidence-current": observations["receiptEvidenceCurrent"],
        "firing-notification-delivered": result.firing_notification_count == 1,
        "recovery-notification-delivered": result.recovery_notification_count == 1,
        "notification-latency-objective": result.receipt_evidence_current
        and result.maximum_notification_latency_milliseconds
        <= int(objective["maximumNotificationLatencyMilliseconds"]),
        "minimized-output": True,
    }
    checks = [_check(identifier, conditions[identifier]) for identifier in CHECK_IDS]
    summary = _summary(checks)
    report_spec: dict[str, Any] = {
        "status": summary["overallStatus"],
        "qualification": QUALIFICATION,
        "qualificationBoundary": "customer-rule-evaluation-and-notification-route",
        "subject": subject,
        "bindings": {
            "profileDigest": _digest_value(profile),
            "clusterBindingDigest": deployment["clusterBindingDigest"],
            "namespaceBindingDigest": deployment["namespaceBindingDigest"],
            "prometheusTargetBindingDigest": _digest_value(
                _strict_https_url(
                    monitoring["prometheusBaseUrl"],
                    "customer-operational-alert-qualification.profile.invalid",
                    root_only=True,
                )
            ),
            "alertmanagerTargetBindingDigest": _digest_value(
                _strict_https_url(
                    monitoring["alertmanagerBaseUrl"],
                    "customer-operational-alert-qualification.profile.invalid",
                    root_only=True,
                )
            ),
            "receiptTargetBindingDigest": _digest_value(
                _strict_https_url(
                    monitoring["receiptUrl"],
                    "customer-operational-alert-qualification.profile.invalid",
                    root_only=False,
                )
            ),
            "probeRouteBindingDigest": _digest_value(
                {
                    "probeId": monitoring["probeId"],
                    "routeId": monitoring["routeId"],
                }
            ),
            "prometheusCaBundleDigest": prometheus_ca_digest,
            "alertmanagerCaBundleDigest": alertmanager_ca_digest,
            "receiptCaBundleDigest": receipt_ca_digest,
            "releaseBindingDigest": _digest_value(subject),
        },
        "profile": {
            "name": QUALIFICATION,
            "ruleSet": rule_set,
            "metricNameProfile": METRIC_NAME_PROFILE,
            "prometheusApi": "v1",
            "alertmanagerApi": "v2",
            "receiptApi": "iip.qualification/v1",
            "transport": "ca-verified-https",
            "proxyMode": "disabled",
            "redirectMode": "denied",
        },
        "objective": objective,
        "measurements": measurements,
        "observations": observations,
        "checks": checks,
        "limitations": list(LIMITATIONS),
        "summary": summary,
    }
    profile_valid_until = _parse_timestamp(
        metadata_profile["reviewedAt"],
        "customer-operational-alert-qualification.time.invalid",
    ) + timedelta(seconds=int(objective["maximumProfileAgeSeconds"]))
    report_valid_until = completed_at + timedelta(
        seconds=int(objective["reportValiditySeconds"])
    )
    metadata_without_id = {
        "generatedAt": _timestamp(completed_at),
        "validUntil": _timestamp(min(profile_valid_until, report_valid_until)),
        "sourceRevision": revision,
        "sourceDirty": False,
    }
    report = {
        "apiVersion": API_VERSION,
        "kind": REPORT_KIND,
        "metadata": {
            "id": _report_identifier(metadata_without_id, report_spec),
            **metadata_without_id,
        },
        "spec": report_spec,
    }
    validate_report_document(report)
    return report


def validate_report_document(report: Mapping[str, Any]) -> None:
    code = "customer-operational-alert-qualification.report.invalid"
    _schema_validate(report, REPORT_SCHEMA, code)
    if report.get("apiVersion") != API_VERSION or report.get("kind") != REPORT_KIND:
        _fail(code)
    metadata = _mapping(report.get("metadata"), code)
    spec = _mapping(report.get("spec"), code)
    subject = _mapping(spec.get("subject"), code)
    bindings = _mapping(spec.get("bindings"), code)
    profile = _mapping(spec.get("profile"), code)
    objective = _mapping(spec.get("objective"), code)
    measurements = _mapping(spec.get("measurements"), code)
    observations = _mapping(spec.get("observations"), code)
    checks = spec.get("checks")
    if not isinstance(checks, list) or tuple(item.get("id") for item in checks if isinstance(item, Mapping)) != CHECK_IDS:
        _fail(code)
    started = _parse_timestamp(measurements.get("startedAt"), code)
    completed = _parse_timestamp(measurements.get("completedAt"), code)
    reviewed = _parse_timestamp(measurements.get("profileReviewedAt"), code)
    generated = _parse_timestamp(metadata.get("generatedAt"), code)
    valid_until = _parse_timestamp(metadata.get("validUntil"), code)
    if (
        completed < started
        or reviewed > completed
        or generated != completed
        or valid_until
        != min(
            completed + timedelta(seconds=int(objective["reportValiditySeconds"])),
            reviewed + timedelta(seconds=int(objective["maximumProfileAgeSeconds"])),
        )
        or measurements.get("profileAgeSeconds")
        != int((completed - reviewed).total_seconds())
        or metadata.get("sourceRevision") != subject.get("sourceRevision")
        or bindings.get("releaseBindingDigest") != _digest_value(subject)
        or spec.get("limitations") != list(LIMITATIONS)
        or profile.get("name") != QUALIFICATION
        or _has_forbidden_key(report)
    ):
        _fail(code)
    rule_set = profile.get("ruleSet")
    if rule_set not in RULES:
        _fail(code)
    expected_rules = len(RULES[str(rule_set)])
    expected_components = len(SERVICE_COMPONENTS[str(rule_set)])
    conditions = {
        "profile-binding": measurements["profileAgeSeconds"]
        <= objective["maximumProfileAgeSeconds"],
        "source-binding": True,
        "immutable-release": True,
        "protected-input-files": observations["protectedInputs"],
        "verified-https": observations["verifiedHttps"],
        "no-proxy-no-redirect": True,
        "prometheus-ready": observations["prometheusReady"],
        "production-rule-group-loaded": observations["productionGroupLoaded"],
        "production-rules-complete": measurements["loadedExpectedRuleCount"]
        == expected_rules,
        "production-rules-healthy": measurements["loadedExpectedRuleCount"]
        == expected_rules
        and measurements["unhealthyExpectedRuleCount"] == 0,
        "component-heartbeats-observed": measurements["observedComponentCount"]
        == expected_components,
        "synthetic-rule-loaded": observations["syntheticRuleLoaded"],
        "synthetic-rule-healthy": observations["syntheticRuleHealthy"],
        "synthetic-rule-inactive": observations["syntheticRuleInactive"],
        "alertmanager-ready": observations["alertmanagerReady"],
        "receipt-evidence-current": observations["receiptEvidenceCurrent"],
        "firing-notification-delivered": measurements["firingNotificationCount"]
        == 1,
        "recovery-notification-delivered": measurements[
            "recoveryNotificationCount"
        ]
        == 1,
        "notification-latency-objective": observations["receiptEvidenceCurrent"]
        and measurements["maximumNotificationLatencyMilliseconds"]
        <= objective["maximumNotificationLatencyMilliseconds"],
        "minimized-output": True,
    }
    expected_checks = [
        _check(identifier, conditions[identifier]) for identifier in CHECK_IDS
    ]
    if checks != expected_checks:
        _fail(code)
    if (
        measurements.get("expectedRuleCount") != expected_rules
        or measurements.get("expectedComponentCount") != expected_components
        or measurements.get("receiptEventCount")
        != measurements.get("firingNotificationCount")
        + measurements.get("recoveryNotificationCount")
    ):
        _fail(code)
    expected_summary = _summary(checks)
    if spec.get("summary") != expected_summary or spec.get("status") != expected_summary["overallStatus"]:
        _fail(code)
    metadata_without_id = dict(metadata)
    identifier = metadata_without_id.pop("id", None)
    if (
        not isinstance(identifier, str)
        or REPORT_ID.fullmatch(identifier) is None
        or identifier != _report_identifier(metadata_without_id, spec)
    ):
        _fail("customer-operational-alert-qualification.report.id-invalid")


def qualify(
    *,
    profile: Mapping[str, Any],
    image_digest: str,
    prometheus_token: str,
    alertmanager_token: str,
    receipt_token: str,
    prometheus_ca_file: Path,
    alertmanager_ca_file: Path,
    receipt_ca_file: Path,
    allow_monitoring_observation: bool = False,
    now: datetime | None = None,
    probe: LiveAlertProbe | None = None,
) -> Mapping[str, Any]:
    validate_profile(profile)
    if allow_monitoring_observation is not True:
        _fail("customer-operational-alert-qualification.observation.not-authorized")
    if len({prometheus_token, alertmanager_token, receipt_token}) != 3:
        _fail("customer-operational-alert-qualification.credential.reused")
    instant = _normalized_instant(now)
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-operational-alert-qualification.source.dirty")
    ca_payloads = [
        _read_file(
            path,
            code="customer-operational-alert-qualification.ca.invalid",
            protected=False,
        )
        for path in (prometheus_ca_file, alertmanager_ca_file, receipt_ca_file)
    ]
    runner = probe or LiveAlertProbe(
        prometheus_token=prometheus_token,
        alertmanager_token=alertmanager_token,
        receipt_token=receipt_token,
        prometheus_ca_file=prometheus_ca_file,
        alertmanager_ca_file=alertmanager_ca_file,
        receipt_ca_file=receipt_ca_file,
    )
    result = runner.run(profile, now=instant)
    completed = _normalized_instant(None) if now is None else instant
    return build_report(
        revision=revision,
        repository=_repository_identity(),
        image_digest=image_digest,
        profile=profile,
        prometheus_ca_digest=_digest_bytes(ca_payloads[0]),
        alertmanager_ca_digest=_digest_bytes(ca_payloads[1]),
        receipt_ca_digest=_digest_bytes(ca_payloads[2]),
        started_at=instant,
        completed_at=completed,
        protected_inputs=True,
        result=result,
    )


def verify_report(
    *,
    report_path: Path,
    profile_path: Path,
    image_digest: str,
    prometheus_ca_file: Path,
    alertmanager_ca_file: Path,
    receipt_ca_file: Path,
    require_qualified: bool = False,
    now: datetime | None = None,
) -> Mapping[str, Any]:
    candidate = report_path.expanduser()
    try:
        if candidate.is_symlink() or not candidate.is_file():
            _fail("customer-operational-alert-qualification.report.unreadable")
        payload = candidate.read_bytes()
        if not 1 <= len(payload) <= MAX_FILE_BYTES:
            _fail("customer-operational-alert-qualification.report.unreadable")
        report = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-operational-alert-qualification.report.unreadable")
    report = _mapping(
        report, "customer-operational-alert-qualification.report.unreadable"
    )
    validate_report_document(report)
    profile = load_profile(profile_path)
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-operational-alert-qualification.source.dirty")
    metadata = _mapping(
        report.get("metadata"),
        "customer-operational-alert-qualification.report.invalid",
    )
    spec = _mapping(
        report.get("spec"), "customer-operational-alert-qualification.report.invalid"
    )
    subject = _mapping(
        spec.get("subject"), "customer-operational-alert-qualification.report.invalid"
    )
    bindings = _mapping(
        spec.get("bindings"), "customer-operational-alert-qualification.report.invalid"
    )
    report_profile = _mapping(
        spec.get("profile"), "customer-operational-alert-qualification.report.invalid"
    )
    report_objective = _mapping(
        spec.get("objective"), "customer-operational-alert-qualification.report.invalid"
    )
    measurements = _mapping(
        spec.get("measurements"),
        "customer-operational-alert-qualification.report.invalid",
    )
    protected_metadata = _mapping(
        profile.get("metadata"),
        "customer-operational-alert-qualification.profile.invalid",
    )
    protected_spec = _mapping(
        profile.get("spec"),
        "customer-operational-alert-qualification.profile.invalid",
    )
    monitoring = _mapping(
        protected_spec.get("monitoring"),
        "customer-operational-alert-qualification.profile.invalid",
    )
    protected_objective = _mapping(
        protected_spec.get("objective"),
        "customer-operational-alert-qualification.profile.invalid",
    )
    ca_digests = [
        _digest_bytes(
            _read_file(
                path,
                code="customer-operational-alert-qualification.ca.invalid",
                protected=False,
            )
        )
        for path in (prometheus_ca_file, alertmanager_ca_file, receipt_ca_file)
    ]
    repository = _repository_identity()
    expected_subject = {
        "applicationVersion": repository["applicationVersion"],
        "chartVersion": repository["chartVersion"],
        "contractsApiVersion": API_VERSION,
        "requiredMigration": repository["requiredMigration"],
        "sourceRevision": revision,
        "imageDigest": image_digest,
    }
    expected_bindings = {
        "profileDigest": _digest_value(profile),
        "clusterBindingDigest": protected_spec["deployment"][
            "clusterBindingDigest"
        ],
        "namespaceBindingDigest": protected_spec["deployment"][
            "namespaceBindingDigest"
        ],
        "prometheusTargetBindingDigest": _digest_value(
            _strict_https_url(
                monitoring["prometheusBaseUrl"],
                "customer-operational-alert-qualification.profile.invalid",
                root_only=True,
            )
        ),
        "alertmanagerTargetBindingDigest": _digest_value(
            _strict_https_url(
                monitoring["alertmanagerBaseUrl"],
                "customer-operational-alert-qualification.profile.invalid",
                root_only=True,
            )
        ),
        "receiptTargetBindingDigest": _digest_value(
            _strict_https_url(
                monitoring["receiptUrl"],
                "customer-operational-alert-qualification.profile.invalid",
                root_only=False,
            )
        ),
        "probeRouteBindingDigest": _digest_value(
            {"probeId": monitoring["probeId"], "routeId": monitoring["routeId"]}
        ),
        "prometheusCaBundleDigest": ca_digests[0],
        "alertmanagerCaBundleDigest": ca_digests[1],
        "receiptCaBundleDigest": ca_digests[2],
        "releaseBindingDigest": _digest_value(expected_subject),
    }
    expected_report_profile = {
        "name": QUALIFICATION,
        "ruleSet": protected_spec["ruleSet"],
        "metricNameProfile": METRIC_NAME_PROFILE,
        "prometheusApi": "v1",
        "alertmanagerApi": "v2",
        "receiptApi": "iip.qualification/v1",
        "transport": "ca-verified-https",
        "proxyMode": "disabled",
        "redirectMode": "denied",
    }
    instant = _normalized_instant(now)
    generated_at = _parse_timestamp(
        metadata.get("generatedAt"),
        "customer-operational-alert-qualification.report.invalid",
    )
    if (
        metadata.get("sourceRevision") != revision
        or dict(subject) != expected_subject
        or dict(bindings) != expected_bindings
        or dict(report_profile) != expected_report_profile
        or dict(report_objective) != dict(protected_objective)
        or measurements.get("profileReviewedAt")
        != protected_metadata.get("reviewedAt")
        or generated_at
        > instant
        + timedelta(seconds=int(protected_objective["maximumClockSkewSeconds"]))
        or instant > _parse_timestamp(metadata.get("validUntil"), "customer-operational-alert-qualification.report.invalid")
    ):
        _fail("customer-operational-alert-qualification.report.crossed")
    if require_qualified and spec.get("status") != "qualified":
        _fail("customer-operational-alert-qualification.report.not-qualified")
    return report


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        _fail("customer-operational-alert-qualification.output.invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        _fail("customer-operational-alert-qualification.output.invalid")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    qualify_parser = subparsers.add_parser("qualify")
    qualify_parser.add_argument("--profile", type=Path, required=True)
    qualify_parser.add_argument("--image-digest", required=True)
    qualify_parser.add_argument("--prometheus-token-file", type=Path, required=True)
    qualify_parser.add_argument("--alertmanager-token-file", type=Path, required=True)
    qualify_parser.add_argument("--receipt-token-file", type=Path, required=True)
    qualify_parser.add_argument("--prometheus-ca-file", type=Path, required=True)
    qualify_parser.add_argument("--alertmanager-ca-file", type=Path, required=True)
    qualify_parser.add_argument("--receipt-ca-file", type=Path, required=True)
    qualify_parser.add_argument("--output", type=Path, required=True)
    qualify_parser.add_argument("--allow-monitoring-observation", action="store_true")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--report", type=Path, required=True)
    verify_parser.add_argument("--profile", type=Path, required=True)
    verify_parser.add_argument("--image-digest", required=True)
    verify_parser.add_argument("--prometheus-ca-file", type=Path, required=True)
    verify_parser.add_argument("--alertmanager-ca-file", type=Path, required=True)
    verify_parser.add_argument("--receipt-ca-file", type=Path, required=True)
    verify_parser.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "qualify":
            report = qualify(
                profile=load_profile(arguments.profile),
                image_digest=arguments.image_digest,
                prometheus_token=load_token(arguments.prometheus_token_file),
                alertmanager_token=load_token(arguments.alertmanager_token_file),
                receipt_token=load_token(arguments.receipt_token_file),
                prometheus_ca_file=arguments.prometheus_ca_file,
                alertmanager_ca_file=arguments.alertmanager_ca_file,
                receipt_ca_file=arguments.receipt_ca_file,
                allow_monitoring_observation=arguments.allow_monitoring_observation,
            )
            _write_report(arguments.output, report)
            print(
                f"customer operational alert qualification {report['spec']['status']}: "
                f"{arguments.output}"
            )
            return 0 if report["spec"]["status"] == "qualified" else 1
        verify_report(
            report_path=arguments.report,
            profile_path=arguments.profile,
            image_digest=arguments.image_digest,
            prometheus_ca_file=arguments.prometheus_ca_file,
            alertmanager_ca_file=arguments.alertmanager_ca_file,
            receipt_ca_file=arguments.receipt_ca_file,
            require_qualified=arguments.require_qualified,
        )
        print("customer operational alert qualification report verified")
    except CustomerOperationalAlertQualificationError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
