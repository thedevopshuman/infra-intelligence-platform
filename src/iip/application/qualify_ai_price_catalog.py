"""Deterministic, provider-neutral qualification of protected AI price catalogs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping
from urllib.parse import urlsplit

from iip.application.calculate_ai_cost import (
    AiCostConfigurationError,
    AiPriceEntry,
    ValidatedAiPriceCatalog,
    validate_ai_price_catalog,
)


QUALIFIER_VERSION = "0.1.0"
_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_POLICY_ID = re.compile(r"apqp_[a-f0-9]{32}")
_REPORT_ID = re.compile(r"apq_[a-f0-9]{32}")
_VERSION = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[.][0-9]+")
_PROVIDER = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
_REGION = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
_MODEL_ID = re.compile(r"[^\x00-\x1f\x7f]{1,256}")
_TIERS = frozenset({"default", "standard", "flex", "priority", "reserved", "unknown"})
_ROUTING = frozenset({"in-region", "geographic", "global", "unknown"})
_PURCHASE = frozenset({"on-demand", "batch", "provisioned-throughput", "unknown"})
_LEVELS = frozenset({"offline-static", "production-catalog"})


class AiPriceCatalogQualificationError(ValueError):
    """A qualification input or report cannot be trusted."""


@dataclass(frozen=True)
class AiPriceScopeRequirement:
    provider: str
    model_id: str
    region: str
    service_tier: str
    routing_mode: str
    purchase_mode: str
    effective_at: datetime


@dataclass(frozen=True)
class ValidatedAiPriceCatalogQualificationPolicy:
    document: Mapping[str, object]
    tenant_id: str
    policy_id: str
    version: str
    maximum_source_age_seconds: int
    report_validity_seconds: int
    maximum_future_skew_seconds: int
    required_scopes: tuple[AiPriceScopeRequirement, ...]


def validate_ai_price_catalog_qualification_policy(
    document: object,
) -> ValidatedAiPriceCatalogQualificationPolicy:
    """Validate a protected policy and its content-derived identity."""

    try:
        copied = _json_copy(document)
        root = _closed(copied, {"apiVersion", "kind", "metadata", "spec"})
        if (
            root["apiVersion"] != "iip.platform/v1alpha1"
            or root["kind"] != "AiPriceCatalogQualificationPolicy"
        ):
            raise ValueError
        metadata = _closed(root["metadata"], {"id", "tenantId", "version"})
        spec = _closed(
            root["spec"],
            {
                "maximumSourceAgeSeconds",
                "reportValiditySeconds",
                "maximumFutureSkewSeconds",
                "requiredScopes",
            },
        )
        tenant_id = _matched(metadata["tenantId"], _TENANT_ID)
        policy_id = _matched(metadata["id"], _POLICY_ID)
        version = _matched(metadata["version"], _VERSION)
        maximum_source_age = _integer(
            spec["maximumSourceAgeSeconds"], minimum=300, maximum=31_536_000
        )
        report_validity = _integer(
            spec["reportValiditySeconds"], minimum=60, maximum=2_592_000
        )
        maximum_future_skew = _integer(
            spec["maximumFutureSkewSeconds"], minimum=0, maximum=300
        )
        raw_scopes = spec["requiredScopes"]
        if not isinstance(raw_scopes, list) or not 1 <= len(raw_scopes) <= 10_000:
            raise ValueError
        scopes = tuple(_scope(item) for item in raw_scopes)
        scope_keys = tuple(_scope_key(item) for item in scopes)
        if len(set(scope_keys)) != len(scope_keys) or scope_keys != tuple(
            sorted(scope_keys)
        ):
            raise ValueError
        identity = {
            "tenantId": tenant_id,
            "version": version,
            "spec": spec,
        }
        if policy_id != "apqp_" + _canonical_digest(identity)[:32]:
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise AiPriceCatalogQualificationError(
            "ai.price-qualification.policy.invalid"
        ) from None
    return ValidatedAiPriceCatalogQualificationPolicy(
        root,
        tenant_id,
        policy_id,
        version,
        maximum_source_age,
        report_validity,
        maximum_future_skew,
        scopes,
    )


def qualify_ai_price_catalog(
    catalog_document: object,
    policy_document: object,
    *,
    generated_at: str,
    qualification_level: str,
) -> Mapping[str, object]:
    """Create minimized evidence for one exact catalog and protected policy."""

    try:
        catalog = validate_ai_price_catalog(catalog_document)
    except AiCostConfigurationError:
        raise AiPriceCatalogQualificationError(
            "ai.price-qualification.catalog.invalid"
        ) from None
    policy = validate_ai_price_catalog_qualification_policy(policy_document)
    try:
        if catalog.tenant_id != policy.tenant_id or qualification_level not in _LEVELS:
            raise ValueError
        generated = _timestamp(generated_at)
        valid_until = generated + timedelta(seconds=policy.report_validity_seconds)
        raw_catalog = _closed(
            catalog.document,
            {"apiVersion", "kind", "metadata", "spec"},
        )
        metadata = _closed(
            raw_catalog["metadata"], {"id", "tenantId", "version", "publishedAt"}
        )
        spec = _closed(raw_catalog["spec"], {"currency", "currencyScale", "source", "entries"})
        source = _closed(
            spec["source"], {"kind", "locator", "retrievedAt", "contentHash"}
        )
        published_at = _timestamp(metadata["publishedAt"])
        retrieved_at = _timestamp(source["retrievedAt"])
        skew = timedelta(seconds=policy.maximum_future_skew_seconds)
        source_profile_ok = _source_profile_is_allowed(
            str(source["kind"]),
            str(source["locator"]),
            qualification_level,
        )
        source_freshness_ok = (
            retrieved_at <= generated + skew
            and valid_until - retrieved_at
            <= timedelta(seconds=policy.maximum_source_age_seconds)
        )
        publication_order_ok = (
            retrieved_at <= published_at <= generated + skew
        )
        overlaps = _overlapping_pair_count(catalog.entries)
        covered = 0
        missing = 0
        ambiguous = 0
        for required in policy.required_scopes:
            count = sum(_entry_matches_scope(entry, required) for entry in catalog.entries)
            if count == 1:
                covered += 1
            elif count == 0:
                missing += 1
            else:
                ambiguous += 1
        checks = [
            _check(
                "source-profile",
                source_profile_ok,
                "ai.price-qualification.source-profile.invalid",
            ),
            _check(
                "source-freshness",
                source_freshness_ok,
                "ai.price-qualification.source.stale",
            ),
            _check(
                "publication-order",
                publication_order_ok,
                "ai.price-qualification.publication-order.invalid",
            ),
            _check(
                "non-overlapping-entries",
                overlaps == 0,
                "ai.price-qualification.entries.overlap",
            ),
            _check(
                "required-scope-coverage",
                missing == 0 and ambiguous == 0,
                "ai.price-qualification.coverage.incomplete",
            ),
        ]
        failed = sum(item["status"] == "failed" for item in checks)
        status = "qualified" if failed == 0 else "unqualified"
        report: dict[str, object] = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "AiPriceCatalogQualificationReport",
            "metadata": {
                "tenantId": catalog.tenant_id,
                "generatedAt": _format_time(generated),
                "validUntil": _format_time(valid_until),
            },
            "spec": {
                "status": status,
                "qualificationLevel": qualification_level,
                "qualifierVersion": QUALIFIER_VERSION,
                "catalog": {
                    "id": catalog.catalog_id,
                    "version": catalog.version,
                    "documentDigest": "sha256:" + _canonical_digest(catalog.document),
                    "sourceKind": catalog.source_kind,
                    "sourceHash": catalog.source_hash,
                    "publishedAt": catalog.published_at,
                    "retrievedAt": _format_time(retrieved_at),
                    "currency": catalog.currency,
                    "currencyScale": catalog.currency_scale,
                },
                "policy": {
                    "id": policy.policy_id,
                    "version": policy.version,
                    "documentDigest": "sha256:" + _canonical_digest(policy.document),
                    "maximumSourceAgeSeconds": policy.maximum_source_age_seconds,
                    "reportValiditySeconds": policy.report_validity_seconds,
                    "maximumFutureSkewSeconds": policy.maximum_future_skew_seconds,
                },
                "measurements": {
                    "entryCount": len(catalog.entries),
                    "requiredScopeCount": len(policy.required_scopes),
                    "coveredScopeCount": covered,
                    "missingScopeCount": missing,
                    "ambiguousScopeCount": ambiguous,
                    "overlappingEntryPairCount": overlaps,
                },
                "checks": checks,
                "summary": {
                    "totalChecks": len(checks),
                    "passedChecks": len(checks) - failed,
                    "failedChecks": failed,
                    "overallStatus": status,
                },
            },
        }
        identity = {
            "apiVersion": report["apiVersion"],
            "kind": report["kind"],
            "metadata": report["metadata"],
            "spec": report["spec"],
        }
        report_metadata = report["metadata"]
        assert isinstance(report_metadata, dict)
        report_metadata["id"] = "apq_" + _canonical_digest(identity)[:32]
        report_metadata_ordered = {
            "id": report_metadata["id"],
            "tenantId": report_metadata["tenantId"],
            "generatedAt": report_metadata["generatedAt"],
            "validUntil": report_metadata["validUntil"],
        }
        report["metadata"] = report_metadata_ordered
        return _json_copy(report)
    except (KeyError, TypeError, ValueError, OverflowError):
        raise AiPriceCatalogQualificationError(
            "ai.price-qualification.input.invalid"
        ) from None


def verify_ai_price_catalog_qualification_report(
    report_document: object,
    catalog_document: object,
    policy_document: object,
    *,
    evaluated_at: str | None = None,
    require_qualified: bool = True,
) -> Mapping[str, object]:
    """Recalculate the complete report and optionally require current qualification."""

    try:
        if not isinstance(require_qualified, bool):
            raise ValueError
        copied = _json_copy(report_document)
        root = _closed(copied, {"apiVersion", "kind", "metadata", "spec"})
        if (
            root["apiVersion"] != "iip.platform/v1alpha1"
            or root["kind"] != "AiPriceCatalogQualificationReport"
        ):
            raise ValueError
        metadata = _closed(
            root["metadata"], {"id", "tenantId", "generatedAt", "validUntil"}
        )
        spec = _closed(
            root["spec"],
            {
                "status",
                "qualificationLevel",
                "qualifierVersion",
                "catalog",
                "policy",
                "measurements",
                "checks",
                "summary",
            },
        )
        _matched(metadata["id"], _REPORT_ID)
        _matched(metadata["tenantId"], _TENANT_ID)
        generated = _timestamp(metadata["generatedAt"])
        valid_until = _timestamp(metadata["validUntil"])
        if generated >= valid_until or spec["qualifierVersion"] != QUALIFIER_VERSION:
            raise ValueError
        expected = qualify_ai_price_catalog(
            catalog_document,
            policy_document,
            generated_at=_format_time(generated),
            qualification_level=str(spec["qualificationLevel"]),
        )
        if root != expected:
            raise ValueError
        if evaluated_at is not None:
            evaluated = _timestamp(evaluated_at)
            if not generated <= evaluated < valid_until:
                raise ValueError
        if require_qualified and spec["status"] != "qualified":
            raise ValueError
        return root
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        AiPriceCatalogQualificationError,
    ):
        raise AiPriceCatalogQualificationError(
            "ai.price-qualification.report.invalid"
        ) from None


def _scope(value: object) -> AiPriceScopeRequirement:
    item = _closed(
        value,
        {
            "provider",
            "modelId",
            "region",
            "serviceTier",
            "routingMode",
            "purchaseMode",
            "effectiveAt",
        },
    )
    provider = _matched(item["provider"], _PROVIDER)
    model_id = _matched(item["modelId"], _MODEL_ID)
    region = _matched(item["region"], _REGION)
    service_tier = _choice(item["serviceTier"], _TIERS)
    routing_mode = _choice(item["routingMode"], _ROUTING)
    purchase_mode = _choice(item["purchaseMode"], _PURCHASE)
    effective_at = _timestamp(item["effectiveAt"])
    return AiPriceScopeRequirement(
        provider,
        model_id,
        region,
        service_tier,
        routing_mode,
        purchase_mode,
        effective_at,
    )


def _scope_key(scope: AiPriceScopeRequirement) -> tuple[str, ...]:
    return (
        scope.provider,
        scope.model_id,
        scope.region,
        scope.service_tier,
        scope.routing_mode,
        scope.purchase_mode,
        _format_time(scope.effective_at),
    )


def _entry_matches_scope(entry: AiPriceEntry, scope: AiPriceScopeRequirement) -> bool:
    return (
        entry.provider == scope.provider
        and entry.model_id == scope.model_id
        and scope.region in entry.regions
        and scope.service_tier in entry.service_tiers
        and scope.routing_mode in entry.routing_modes
        and scope.purchase_mode in entry.purchase_modes
        and entry.effective_from <= scope.effective_at
        and (entry.effective_until is None or scope.effective_at < entry.effective_until)
    )


def _overlapping_pair_count(entries: tuple[AiPriceEntry, ...]) -> int:
    count = 0
    for position, left in enumerate(entries):
        for right in entries[position + 1 :]:
            if (
                left.provider == right.provider
                and left.model_id == right.model_id
                and left.regions & right.regions
                and left.service_tiers & right.service_tiers
                and left.routing_modes & right.routing_modes
                and left.purchase_modes & right.purchase_modes
                and _intervals_overlap(left, right)
            ):
                count += 1
    return count


def _intervals_overlap(left: AiPriceEntry, right: AiPriceEntry) -> bool:
    left_ends_after_right_starts = (
        left.effective_until is None or right.effective_from < left.effective_until
    )
    right_ends_after_left_starts = (
        right.effective_until is None or left.effective_from < right.effective_until
    )
    return left_ends_after_right_starts and right_ends_after_left_starts


def _source_profile_is_allowed(kind: str, locator: str, level: str) -> bool:
    try:
        parsed = urlsplit(locator)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return False
        if parsed.scheme == "https":
            if not parsed.hostname or parsed.port not in (None, 443):
                return False
        elif parsed.scheme == "urn":
            if not parsed.path or ":" not in parsed.path:
                return False
        else:
            return False
        if level == "production-catalog":
            if kind == "test-fixture":
                return False
            if kind == "provider-published" and parsed.scheme != "https":
                return False
        return kind in {"provider-published", "operator-managed", "test-fixture"}
    except (TypeError, ValueError):
        return False


def _check(check_id: str, passed: bool, error_code: str) -> Mapping[str, object]:
    if passed:
        return {"id": check_id, "status": "passed"}
    return {"id": check_id, "status": "failed", "errorCode": error_code}


def _closed(value: object, keys: set[str]) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError
    return value


def _matched(value: object, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError
    return value


def _choice(value: object, choices: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError
    return value


def _integer(value: object, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError
    return value


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _json_copy(value: object) -> dict[str, object]:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    copied = json.loads(encoded)
    if not isinstance(copied, dict):
        raise ValueError
    return copied


__all__ = [
    "AiPriceCatalogQualificationError",
    "AiPriceScopeRequirement",
    "QUALIFIER_VERSION",
    "ValidatedAiPriceCatalogQualificationPolicy",
    "qualify_ai_price_catalog",
    "validate_ai_price_catalog_qualification_policy",
    "verify_ai_price_catalog_qualification_report",
]
