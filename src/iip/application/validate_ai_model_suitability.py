"""Validation for protected workload-specific AI model suitability reports."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping
from urllib.parse import urlsplit


EVALUATOR_VERSION = "1.0.0"
MAX_VALIDITY = timedelta(days=90)
GATE_IDS = ("quality", "latency", "safety", "compliance")
LIMITATIONS = ("workload-specific", "time-bounded", "advisory-only")
_REPORT_ID = re.compile(r"ams_[a-f0-9]{32}")
_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_PROFILE_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_PROVIDER = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
_REGION = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
_SHA256 = re.compile(r"sha256:[a-f0-9]{64}")
_SAFE_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,2048}")
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:authorization|password|passwd|token|secret|api[_-]?key|"
    r"access[_-]?key)\s*[:=]\s*\S+|bearer\s+\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


class InvalidAiModelSuitabilityReportError(ValueError):
    """A suitability report is malformed, unsafe, or internally inconsistent."""


@dataclass(frozen=True)
class ValidatedAiModelSuitabilityReport:
    document: Mapping[str, object]
    report_id: str
    tenant_id: str
    evaluated_at: datetime
    valid_until: datetime
    source_kind: str
    source_hash: str
    provider: str
    reference_model_id: str
    candidate_model_id: str
    region: str
    service_name: str
    deployment_environment: str
    workload_profile_id: str


def validate_ai_model_suitability_report(
    document: object,
    *,
    allow_test_fixtures: bool = False,
) -> ValidatedAiModelSuitabilityReport:
    """Return a copied, semantically validated suitability report."""

    try:
        if not isinstance(allow_test_fixtures, bool):
            raise ValueError
        copied = _json_copy(document)
        root = _closed(copied, {"apiVersion", "kind", "metadata", "spec"})
        if (
            root["apiVersion"] != "iip.platform/v1alpha1"
            or root["kind"] != "AiModelSuitabilityReport"
        ):
            raise ValueError
        metadata = _closed(
            root["metadata"], {"id", "tenantId", "evaluatedAt", "validUntil"}
        )
        spec = _closed(
            root["spec"],
            {
                "status",
                "evaluatorVersion",
                "source",
                "scope",
                "workload",
                "gates",
                "contentHandling",
                "limitations",
            },
        )
        if (
            spec["status"] != "qualified"
            or spec["evaluatorVersion"] != EVALUATOR_VERSION
            or spec["limitations"] != list(LIMITATIONS)
        ):
            raise ValueError
        report_id = _matched(metadata["id"], _REPORT_ID)
        tenant_id = _matched(metadata["tenantId"], _TENANT_ID)
        evaluated_at = _timestamp(metadata["evaluatedAt"])
        valid_until = _timestamp(metadata["validUntil"])
        if (
            not evaluated_at < valid_until
            or valid_until - evaluated_at > MAX_VALIDITY
        ):
            raise ValueError
        source = _closed(
            spec["source"], {"kind", "locator", "retrievedAt", "contentHash"}
        )
        source_kind = source["kind"]
        if source_kind not in {"operator-attested", "test-fixture"}:
            raise ValueError
        if source_kind == "test-fixture" and not allow_test_fixtures:
            raise ValueError
        locator = _text(source["locator"], maximum=2048)
        parsed_locator = urlsplit(locator)
        locator_port = parsed_locator.port
        if (
            _SENSITIVE_TEXT.search(locator)
            or parsed_locator.scheme not in {"https", "urn"}
            or (
                parsed_locator.scheme == "https"
                and (
                    not parsed_locator.hostname
                    or locator_port is not None
                    and not 1 <= locator_port <= 65_535
                )
            )
            or (
                parsed_locator.scheme == "urn"
                and (parsed_locator.netloc or not parsed_locator.path)
            )
            or parsed_locator.username is not None
            or parsed_locator.password is not None
            or parsed_locator.query
            or parsed_locator.fragment
            or _timestamp(source["retrievedAt"]) > evaluated_at
        ):
            raise ValueError
        source_hash = _matched(source["contentHash"], _SHA256)
        scope = _closed(
            spec["scope"],
            {
                "provider",
                "referenceModelId",
                "candidateModelId",
                "region",
                "serviceName",
                "deploymentEnvironment",
            },
        )
        provider = _matched(scope["provider"], _PROVIDER)
        reference_model_id = _text(scope["referenceModelId"], maximum=256)
        candidate_model_id = _text(scope["candidateModelId"], maximum=256)
        if candidate_model_id == reference_model_id:
            raise ValueError
        region = _matched(scope["region"], _REGION)
        service_name = _text(scope["serviceName"], maximum=256)
        deployment_environment = _text(
            scope["deploymentEnvironment"], maximum=128
        )
        workload = _closed(spec["workload"], {"profileId", "criteriaDigest"})
        workload_profile_id = _matched(workload["profileId"], _PROFILE_ID)
        _matched(workload["criteriaDigest"], _SHA256)
        gates = spec["gates"]
        if not isinstance(gates, list) or len(gates) != len(GATE_IDS):
            raise ValueError
        for raw_gate, expected_id in zip(gates, GATE_IDS):
            gate = _closed(
                raw_gate, {"id", "status", "sampleCount", "resultDigest"}
            )
            if gate["id"] != expected_id or gate["status"] != "passed":
                raise ValueError
            _integer(gate["sampleCount"], minimum=1, maximum=1_000_000)
            _matched(gate["resultDigest"], _SHA256)
        content = _closed(
            spec["contentHandling"],
            {
                "promptContentPersisted",
                "responseContentPersisted",
                "toolContentPersisted",
                "artifactContainsContent",
            },
        )
        if any(value is not False for value in content.values()):
            raise ValueError
        if report_id != model_suitability_report_id(copied):
            raise ValueError
        return ValidatedAiModelSuitabilityReport(
            copied,
            report_id,
            tenant_id,
            evaluated_at,
            valid_until,
            str(source_kind),
            source_hash,
            provider,
            reference_model_id,
            candidate_model_id,
            region,
            service_name,
            deployment_environment,
            workload_profile_id,
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        raise InvalidAiModelSuitabilityReportError(
            "ai.model-suitability.report.invalid"
        ) from None


def model_suitability_report_id(document: Mapping[str, object]) -> str:
    """Derive the immutable report identifier from all fields except its ID."""

    copied = _json_copy(document)
    metadata = copied.get("metadata")
    if not isinstance(metadata, dict):
        raise InvalidAiModelSuitabilityReportError(
            "ai.model-suitability.report.invalid"
        )
    metadata.pop("id", None)
    return "ams_" + hashlib.sha256(_canonical(copied)).hexdigest()[:32]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _json_copy(value: object) -> dict[str, object]:
    copied = json.loads(_canonical(value))
    if not isinstance(copied, dict):
        raise ValueError
    return copied


def _closed(value: object, required: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError
    return value


def _text(value: object, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or not _SAFE_TEXT.fullmatch(value)
    ):
        raise ValueError
    return value


def _matched(value: object, pattern: re.Pattern[str]) -> str:
    text = _text(value, maximum=2048)
    if not pattern.fullmatch(text):
        raise ValueError
    return text


def _integer(value: object, *, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError
    return value


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(timezone.utc)


__all__ = [
    "EVALUATOR_VERSION",
    "GATE_IDS",
    "InvalidAiModelSuitabilityReportError",
    "LIMITATIONS",
    "MAX_VALIDITY",
    "ValidatedAiModelSuitabilityReport",
    "model_suitability_report_id",
    "validate_ai_model_suitability_report",
]
