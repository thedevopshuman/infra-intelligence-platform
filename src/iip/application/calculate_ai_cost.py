"""Deterministic, tenant-bound calculation of AI usage cost."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping
from urllib.parse import urlsplit

from iip.application.ports import ActorContext, AiEconomicsLedger, Clock
from iip.domain.models import PlatformEvent


ENGINE_VERSION = "0.2.0"
MAX_SAFE_INTEGER = 9_007_199_254_740_991
_MILLION = 1_000_000
_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_WORKER_ID = re.compile(r"[A-Za-z0-9._:-]{1,256}")
_USAGE_ID = re.compile(r"aiu_[a-f0-9]{32}")
_CATALOG_ID = re.compile(r"apc_[a-f0-9]{32}")
_COST_ID = re.compile(r"aic_[a-f0-9]{32}")
_TRACE_ID = re.compile(r"[a-f0-9]{32}")
_VERSION = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[.][0-9]+")
_ENTRY_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_PROVIDER = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
_REGION = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
_SHA256 = re.compile(r"sha256:[a-f0-9]{64}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_SAFE_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,2048}")
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:authorization|password|passwd|token|secret|api[_-]?key|"
    r"access[_-]?key)\s*[:=]\s*\S+|bearer\s+\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_USAGE_FIELDS = (
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheWriteInputTokens",
    "reasoningOutputTokens",
)
_RATE_FIELDS = (
    "uncachedInputTokens",
    "cacheReadInputTokens",
    "cacheWriteInputTokens",
    "nonReasoningOutputTokens",
    "reasoningOutputTokens",
)
_DETAILED_LINE_CATEGORIES = (
    "uncached-input-tokens",
    "cache-read-input-tokens",
    "cache-write-input-tokens",
    "non-reasoning-output-tokens",
    "reasoning-output-tokens",
)
_AGGREGATE_LINE_CATEGORIES = (
    "uncached-input-tokens",
    "cache-read-input-tokens",
    "cache-write-input-tokens",
    "aggregate-output-tokens",
)
_TIERS = frozenset({"default", "standard", "flex", "priority", "reserved", "unknown"})
_ROUTING = frozenset({"in-region", "geographic", "global", "unknown"})
_PURCHASE = frozenset({"on-demand", "batch", "provisioned-throughput", "unknown"})


class AiCostConfigurationError(ValueError):
    """Protected catalog or worker configuration is invalid."""


class InvalidAiCostInputError(ValueError):
    """Stored usage or cost state cannot be safely evaluated."""


@dataclass(frozen=True)
class AiPriceEntry:
    entry_id: str
    provider: str
    model_id: str
    regions: frozenset[str]
    service_tiers: frozenset[str]
    routing_modes: frozenset[str]
    purchase_modes: frozenset[str]
    effective_from: datetime
    effective_until: datetime | None
    rates: Mapping[str, int]


@dataclass(frozen=True)
class ValidatedAiPriceCatalog:
    document: Mapping[str, object]
    tenant_id: str
    catalog_id: str
    version: str
    published_at: str
    source_kind: str
    source_hash: str
    currency: str
    currency_scale: int
    entries: tuple[AiPriceEntry, ...]


@dataclass(frozen=True)
class AiCostCalculationPass:
    processed: int
    priced: int
    unpriced: int
    ambiguous: int
    catalog_id: str
    catalog_version: str


class AiCostCalculationService:
    """Poll immutable usage and emit independently reproducible cost records."""

    def __init__(
        self,
        ledger: AiEconomicsLedger,
        clock: Clock,
        catalogs: tuple[Mapping[str, object], ...],
        *,
        allow_test_fixtures: bool = False,
        qualification_policies: tuple[Mapping[str, object], ...] | None = None,
        qualification_reports: tuple[Mapping[str, object], ...] | None = None,
        require_production_qualification: bool = False,
        batch_size: int = 100,
    ) -> None:
        if (
            not isinstance(catalogs, tuple)
            or not catalogs
            or len(catalogs) > 1000
            or not isinstance(allow_test_fixtures, bool)
            or not isinstance(require_production_qualification, bool)
            or isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or not 1 <= batch_size <= 1000
        ):
            raise AiCostConfigurationError("ai.cost.configuration.invalid")
        validated = tuple(validate_ai_price_catalog(item) for item in catalogs)
        if (
            not allow_test_fixtures
            and any(item.source_kind == "test-fixture" for item in validated)
        ):
            raise AiCostConfigurationError("ai.cost.test-fixture.prohibited")
        if len({item.tenant_id for item in validated}) != len(validated):
            raise AiCostConfigurationError("ai.cost.catalog.ambiguous")
        qualifications = self._validate_qualifications(
            validated,
            qualification_policies,
            qualification_reports,
            allow_test_fixtures=allow_test_fixtures,
            required=require_production_qualification,
        )
        self._ledger = ledger
        self._clock = clock
        self._catalogs = {item.tenant_id: item for item in validated}
        self._qualifications = qualifications
        self._require_production_qualification = require_production_qualification
        self._batch_size = batch_size

    @staticmethod
    def _validate_qualifications(
        catalogs: tuple[ValidatedAiPriceCatalog, ...],
        policies: tuple[Mapping[str, object], ...] | None,
        reports: tuple[Mapping[str, object], ...] | None,
        *,
        allow_test_fixtures: bool,
        required: bool,
    ) -> Mapping[str, tuple[Mapping[str, object], Mapping[str, object]]]:
        if not required:
            if policies is not None or reports is not None:
                raise AiCostConfigurationError("ai.cost.configuration.invalid")
            return {}
        if (
            allow_test_fixtures
            or not isinstance(policies, tuple)
            or not isinstance(reports, tuple)
            or not policies
            or not reports
            or len(policies) != len(catalogs)
            or len(reports) != len(catalogs)
        ):
            raise AiCostConfigurationError("ai.cost.catalog-qualification.invalid")

        from iip.application.qualify_ai_price_catalog import (
            AiPriceCatalogQualificationError,
            validate_ai_price_catalog_qualification_policy,
            verify_ai_price_catalog_qualification_report,
        )

        try:
            validated_policies = tuple(
                validate_ai_price_catalog_qualification_policy(item)
                for item in policies
            )
            policy_by_tenant = {
                item.tenant_id: item.document for item in validated_policies
            }
            report_by_tenant: dict[str, Mapping[str, object]] = {}
            for report in reports:
                if not isinstance(report, Mapping):
                    raise ValueError
                metadata = report.get("metadata")
                if not isinstance(metadata, Mapping):
                    raise ValueError
                tenant_id = metadata.get("tenantId")
                if not isinstance(tenant_id, str) or tenant_id in report_by_tenant:
                    raise ValueError
                report_by_tenant[tenant_id] = report
            catalog_by_tenant = {item.tenant_id: item for item in catalogs}
            if (
                len(policy_by_tenant) != len(validated_policies)
                or set(policy_by_tenant) != set(catalog_by_tenant)
                or set(report_by_tenant) != set(catalog_by_tenant)
            ):
                raise ValueError
            result: dict[
                str, tuple[Mapping[str, object], Mapping[str, object]]
            ] = {}
            for tenant_id, catalog in catalog_by_tenant.items():
                policy = policy_by_tenant[tenant_id]
                verified = verify_ai_price_catalog_qualification_report(
                    report_by_tenant[tenant_id],
                    catalog.document,
                    policy,
                )
                spec = verified.get("spec")
                if (
                    not isinstance(spec, Mapping)
                    or spec.get("qualificationLevel") != "production-catalog"
                ):
                    raise ValueError
                result[tenant_id] = (policy, verified)
            return result
        except (
            AiPriceCatalogQualificationError,
            KeyError,
            TypeError,
            ValueError,
        ):
            raise AiCostConfigurationError(
                "ai.cost.catalog-qualification.invalid"
            ) from None

    @property
    def tenant_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._catalogs))

    def run_once(self, tenant_id: str, worker_id: str) -> AiCostCalculationPass:
        actor = _cost_actor(tenant_id, worker_id)
        catalog = self._catalogs.get(tenant_id)
        if catalog is None:
            raise AiCostConfigurationError("ai.cost.catalog.missing")
        now = _format_time(_parse_time(self._clock.now()))
        if self._require_production_qualification:
            policy, report = self._qualifications[tenant_id]
            from iip.application.qualify_ai_price_catalog import (
                AiPriceCatalogQualificationError,
                verify_ai_price_catalog_qualification_report,
            )

            try:
                verify_ai_price_catalog_qualification_report(
                    report,
                    catalog.document,
                    policy,
                    evaluated_at=now,
                )
            except AiPriceCatalogQualificationError:
                raise AiCostConfigurationError(
                    "ai.cost.catalog-qualification.invalid"
                ) from None
        self._ledger.register_price_catalog(actor, catalog.document)
        usage_records = self._ledger.list_usage_without_cost(
            actor,
            catalog.catalog_id,
            ENGINE_VERSION,
            limit=self._batch_size,
        )
        if not isinstance(usage_records, tuple) or len(usage_records) > self._batch_size:
            raise InvalidAiCostInputError("ai.cost.storage.invalid")
        if not usage_records:
            return AiCostCalculationPass(
                0,
                0,
                0,
                0,
                catalog.catalog_id,
                catalog.version,
            )

        calculated_at = now
        records: list[Mapping[str, object]] = []
        events: list[PlatformEvent] = []
        counts = {"priced": 0, "unpriced": 0, "ambiguous": 0}
        for usage_record in usage_records:
            record, event = calculate_ai_cost_record(
                catalog,
                usage_record,
                calculated_at=calculated_at,
            )
            result = record["spec"]["result"]  # type: ignore[index]
            status = result["costStatus"]  # type: ignore[index]
            counts[status] += 1  # type: ignore[index]
            records.append(record)
            events.append(event)
        self._ledger.commit_cost_batch(actor, tuple(records), tuple(events))
        return AiCostCalculationPass(
            len(records),
            counts["priced"],
            counts["unpriced"],
            counts["ambiguous"],
            catalog.catalog_id,
            catalog.version,
        )


def validate_ai_price_catalog(document: object) -> ValidatedAiPriceCatalog:
    """Return a copied and semantically validated price snapshot."""

    try:
        copied = _json_copy(document)
        root = _closed(copied, {"apiVersion", "kind", "metadata", "spec"})
        if root["apiVersion"] != "iip.platform/v1alpha1" or root["kind"] != "AiPriceCatalog":
            raise ValueError
        metadata = _closed(
            root["metadata"],
            {"id", "tenantId", "version", "publishedAt"},
        )
        spec = _closed(root["spec"], {"currency", "currencyScale", "source", "entries"})
        source = _closed(
            spec["source"],
            {"kind", "locator", "retrievedAt", "contentHash"},
        )
        tenant_id = _matched(metadata["tenantId"], _TENANT_ID)
        catalog_id = _matched(metadata["id"], _CATALOG_ID)
        version = _matched(metadata["version"], _VERSION)
        published_at = _timestamp(metadata["publishedAt"])[1]
        source_kind = source["kind"]
        if source_kind not in {"provider-published", "operator-managed", "test-fixture"}:
            raise ValueError
        locator = _text(source["locator"], maximum=2048)
        parsed_locator = urlsplit(locator)
        if (
            _SENSITIVE_TEXT.search(locator)
            or parsed_locator.username is not None
            or parsed_locator.password is not None
        ):
            raise ValueError
        _timestamp(source["retrievedAt"])
        source_hash = _matched(source["contentHash"], _SHA256)
        currency = _matched(spec["currency"], _CURRENCY)
        currency_scale = _safe_integer(spec["currencyScale"])
        if currency_scale not in (6, 9, 12):
            raise ValueError
        raw_entries = spec["entries"]
        if not isinstance(raw_entries, list) or not 1 <= len(raw_entries) <= 10_000:
            raise ValueError
        entries = tuple(_price_entry(item) for item in raw_entries)
        if len({item.entry_id for item in entries}) != len(entries):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise AiCostConfigurationError("ai.cost.catalog.invalid") from None
    return ValidatedAiPriceCatalog(
        root,
        tenant_id,
        catalog_id,
        version,
        published_at,
        source_kind,
        source_hash,
        currency,
        currency_scale,
        entries,
    )


def calculate_ai_cost_record(
    catalog: ValidatedAiPriceCatalog,
    usage_document: object,
    *,
    calculated_at: str,
) -> tuple[Mapping[str, object], PlatformEvent]:
    """Calculate one immutable result using integer half-up arithmetic."""

    usage = _usage_input(usage_document, expected_tenant=catalog.tenant_id)
    try:
        calculated_value, calculated_time = _timestamp(calculated_at)
        started_at = usage["started_at"]
        if not isinstance(started_at, datetime) or calculated_value < started_at:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise InvalidAiCostInputError("ai.cost.time.invalid") from None
    matches = tuple(
        entry for entry in catalog.entries if _entry_matches(entry, usage)
    )
    warnings = ["calculated-cost-not-invoice"]
    if catalog.source_kind == "test-fixture":
        warnings.append("test-fixture-pricing")
    if not matches:
        result: Mapping[str, object] = {
            "costStatus": "unpriced",
            "coverage": "none",
            "reasonCode": "no-catalog-match",
            "warnings": sorted(warnings + ["cost-unresolved"]),
        }
    elif len(matches) > 1:
        result = {
            "costStatus": "ambiguous",
            "coverage": "none",
            "reasonCode": "multiple-catalog-matches",
            "warnings": sorted(warnings + ["cost-unresolved"]),
        }
    elif usage["completeness"] != "complete" or usage["missing_fields"]:
        if _aggregate_output_pricing_is_exact(matches[0], usage):
            result = _priced_result(catalog, matches[0], usage, warnings)
        else:
            result = {
                "costStatus": "unpriced",
                "coverage": "partial",
                "reasonCode": "missing-usage",
                "warnings": sorted(warnings + ["cost-unresolved"]),
            }
    else:
        result = _priced_result(catalog, matches[0], usage, warnings)

    identity = {
        "tenantId": catalog.tenant_id,
        "usageRecordId": usage["usage_record_id"],
        "catalogId": catalog.catalog_id,
        "catalogVersion": catalog.version,
        "engineVersion": ENGINE_VERSION,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    cost_record_id = "aic_" + digest[:32]
    record: Mapping[str, object] = {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "AiCostRecord",
        "metadata": {
            "id": cost_record_id,
            "tenantId": catalog.tenant_id,
            "calculatedAt": calculated_time,
        },
        "spec": {
            "usageRecordId": usage["usage_record_id"],
            "calculation": {
                "engineVersion": ENGINE_VERSION,
                "costBasis": "calculated-estimate",
                "catalogId": catalog.catalog_id,
                "catalogVersion": catalog.version,
                "catalogSourceHash": catalog.source_hash,
            },
            "result": result,
        },
    }
    event = PlatformEvent(
        event_id="ai-cost-" + digest,
        event_type="io.iip.ai.cost-calculated.v1",
        source="urn:iip:ai-cost:" + ENGINE_VERSION,
        time=calculated_time,
        subject=cost_record_id,
        tenant_id=catalog.tenant_id,
        correlation_id=usage["trace_id"],
        causation_id=usage["usage_record_id"],
        data={
            "costRecordId": cost_record_id,
            "usageRecordId": usage["usage_record_id"],
            "catalogId": catalog.catalog_id,
            "catalogVersion": catalog.version,
            "costStatus": result["costStatus"],
            "provider": usage["provider"],
            "modelId": usage["model_id"],
            "serviceName": usage["service_name"],
        },
    )
    return record, event


def validate_ai_cost_record(document: object) -> Mapping[str, object]:
    """Validate the closed cost result shape at persistence boundaries."""

    try:
        root = _closed(_json_copy(document), {"apiVersion", "kind", "metadata", "spec"})
        if root["apiVersion"] != "iip.platform/v1alpha1" or root["kind"] != "AiCostRecord":
            raise ValueError
        metadata = _closed(root["metadata"], {"id", "tenantId", "calculatedAt"})
        spec = _closed(root["spec"], {"usageRecordId", "calculation", "result"})
        calculation = _closed(
            spec["calculation"],
            {"engineVersion", "costBasis", "catalogId", "catalogVersion", "catalogSourceHash"},
        )
        _matched(metadata["id"], _COST_ID)
        _matched(metadata["tenantId"], _TENANT_ID)
        _timestamp(metadata["calculatedAt"])
        _matched(spec["usageRecordId"], _USAGE_ID)
        if calculation["engineVersion"] != ENGINE_VERSION:
            raise ValueError
        if calculation["costBasis"] != "calculated-estimate":
            raise ValueError
        _matched(calculation["catalogId"], _CATALOG_ID)
        _matched(calculation["catalogVersion"], _VERSION)
        _matched(calculation["catalogSourceHash"], _SHA256)
        _validate_cost_result(spec["result"])
    except (KeyError, TypeError, ValueError, OverflowError):
        raise InvalidAiCostInputError("ai.cost.record.invalid") from None
    return root


def validate_ai_usage_for_economics(
    document: object,
    *,
    expected_tenant: str,
) -> Mapping[str, object]:
    """Validate one stored usage fact for a downstream economics use case."""

    return _usage_input(document, expected_tenant=expected_tenant)


def _price_entry(value: object) -> AiPriceEntry:
    entry = _closed(
        value,
        {
            "id",
            "provider",
            "modelId",
            "regions",
            "serviceTiers",
            "routingModes",
            "purchaseModes",
            "effectiveFrom",
            "rates",
        },
        {"effectiveUntil"},
    )
    entry_id = _matched(entry["id"], _ENTRY_ID)
    provider = _matched(entry["provider"], _PROVIDER)
    model_id = _text(entry["modelId"], maximum=256)
    regions = _string_set(entry["regions"], 64, pattern=_REGION)
    service_tiers = _string_set(entry["serviceTiers"], 6, allowed=_TIERS)
    routing_modes = _string_set(entry["routingModes"], 4, allowed=_ROUTING)
    purchase_modes = _string_set(entry["purchaseModes"], 4, allowed=_PURCHASE)
    effective_from, _ = _timestamp(entry["effectiveFrom"])
    effective_until = None
    if "effectiveUntil" in entry:
        effective_until, _ = _timestamp(entry["effectiveUntil"])
        if effective_until <= effective_from:
            raise ValueError
    rates_document = _closed(entry["rates"], set(_RATE_FIELDS))
    rates: dict[str, int] = {}
    for name in _RATE_FIELDS:
        price = _closed(rates_document[name], {"priceSubunitsPerMillionTokens"})
        rates[name] = _safe_integer(price["priceSubunitsPerMillionTokens"])
    return AiPriceEntry(
        entry_id,
        provider,
        model_id,
        frozenset(regions),
        frozenset(service_tiers),
        frozenset(routing_modes),
        frozenset(purchase_modes),
        effective_from,
        effective_until,
        rates,
    )


def _usage_input(value: object, *, expected_tenant: str) -> Mapping[str, object]:
    try:
        root = _closed(_json_copy(value), {"apiVersion", "kind", "metadata", "spec"})
        if root["apiVersion"] != "iip.platform/v1alpha1" or root["kind"] != "AiUsageRecord":
            raise ValueError
        metadata = _closed(root["metadata"], {"id", "tenantId", "recordedAt"})
        spec = _closed(
            root["spec"],
            {"source", "invocation", "attribution", "usage", "privacy", "deduplicationKey"},
        )
        invocation = _closed(
            spec["invocation"],
            {
                "provider",
                "operationName",
                "requestModel",
                "region",
                "serviceTier",
                "routingMode",
                "purchaseMode",
                "startedAt",
                "durationMillis",
                "outcome",
                "traceId",
                "spanId",
            },
            {"responseModel", "errorType", "requestIdHash", "retryCount"},
        )
        attribution = _closed(
            spec["attribution"],
            {"serviceName", "resourceRefs"},
            {"serviceNamespace", "deploymentEnvironment"},
        )
        usage = _closed(
            spec["usage"],
            {"reportedBy", "completeness", "missingFields"},
            set(_USAGE_FIELDS),
        )
        privacy = _closed(
            spec["privacy"],
            {"contentPolicy", "contentCaptured", "rawPayloadPersisted", "droppedAttributeCount"},
        )
        usage_record_id = _matched(metadata["id"], _USAGE_ID)
        tenant_id = _matched(metadata["tenantId"], _TENANT_ID)
        if tenant_id != expected_tenant:
            raise ValueError
        provider = _matched(invocation["provider"], _PROVIDER)
        model_id = _text(
            invocation.get("responseModel", invocation["requestModel"]),
            maximum=256,
        )
        region = _matched(invocation["region"], _REGION)
        service_tier = _text(invocation["serviceTier"], maximum=32)
        routing_mode = _text(invocation["routingMode"], maximum=32)
        purchase_mode = _text(invocation["purchaseMode"], maximum=32)
        if (
            service_tier not in _TIERS
            or routing_mode not in _ROUTING
            or purchase_mode not in _PURCHASE
        ):
            raise ValueError
        started_at, _ = _timestamp(invocation["startedAt"])
        trace_id = _matched(invocation["traceId"], _TRACE_ID)
        retry_count = invocation.get("retryCount")
        if retry_count is not None and (
            isinstance(retry_count, bool)
            or not isinstance(retry_count, int)
            or not 0 <= retry_count <= 100
        ):
            raise ValueError
        service_name = _text(attribution["serviceName"], maximum=256)
        missing = usage["missingFields"]
        if (
            usage["reportedBy"] not in {"provider", "instrumentation", "derived"}
            or usage["completeness"] not in {"complete", "partial"}
            or not isinstance(missing, list)
            or any(item not in _USAGE_FIELDS for item in missing)
            or missing != sorted(missing)
            or len(set(missing)) != len(missing)
            or privacy
            != {
                "contentPolicy": "metadata-only",
                "contentCaptured": False,
                "rawPayloadPersisted": False,
                "droppedAttributeCount": privacy["droppedAttributeCount"],
            }
            or isinstance(privacy["droppedAttributeCount"], bool)
            or not isinstance(privacy["droppedAttributeCount"], int)
            or not 0 <= privacy["droppedAttributeCount"] <= 100_000
        ):
            raise ValueError
        quantities: dict[str, int | None] = {}
        for name in _USAGE_FIELDS:
            raw = usage.get(name)
            quantities[name] = None if raw is None else _safe_integer(raw)
        absent = sorted(name for name, quantity in quantities.items() if quantity is None)
        if missing != absent:
            raise ValueError
        if quantities["inputTokens"] is None and quantities["outputTokens"] is None:
            raise ValueError
        if usage["completeness"] == "partial" and not missing:
            raise ValueError
        if usage["completeness"] == "complete" and (
            missing or quantities["inputTokens"] is None or quantities["outputTokens"] is None
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise InvalidAiCostInputError("ai.cost.usage.invalid") from None
    return {
        "usage_record_id": usage_record_id,
        "provider": provider,
        "model_id": model_id,
        "region": region,
        "service_tier": service_tier,
        "routing_mode": routing_mode,
        "purchase_mode": purchase_mode,
        "started_at": started_at,
        "trace_id": trace_id,
        "retry_count": retry_count,
        "service_name": service_name,
        "deployment_environment": attribution.get("deploymentEnvironment"),
        "outcome": invocation["outcome"],
        "completeness": usage["completeness"],
        "missing_fields": tuple(missing),
        **quantities,
    }


def _entry_matches(entry: AiPriceEntry, usage: Mapping[str, object]) -> bool:
    started_at = usage["started_at"]
    assert isinstance(started_at, datetime)
    return (
        entry.provider == usage["provider"]
        and entry.model_id == usage["model_id"]
        and usage["region"] in entry.regions
        and usage["service_tier"] in entry.service_tiers
        and usage["routing_mode"] in entry.routing_modes
        and usage["purchase_mode"] in entry.purchase_modes
        and entry.effective_from <= started_at
        and (entry.effective_until is None or started_at < entry.effective_until)
    )


def _priced_result(
    catalog: ValidatedAiPriceCatalog,
    entry: AiPriceEntry,
    usage: Mapping[str, object],
    warnings: list[str],
) -> Mapping[str, object]:
    input_tokens = usage["inputTokens"]
    output_tokens = usage["outputTokens"]
    cache_read = usage["cacheReadInputTokens"]
    cache_write = usage["cacheWriteInputTokens"]
    reasoning = usage["reasoningOutputTokens"]
    assert all(
        isinstance(item, int)
        for item in (input_tokens, output_tokens, cache_read, cache_write)
    )
    uncached = input_tokens - cache_read - cache_write  # type: ignore[operator]
    if uncached < 0:
        return {
            "costStatus": "unpriced",
            "coverage": "none",
            "reasonCode": "invalid-breakdown",
            "warnings": sorted(warnings + ["cost-unresolved"]),
        }
    if reasoning is None:
        assert _aggregate_output_pricing_is_exact(entry, usage)
        categories = _AGGREGATE_LINE_CATEGORIES
        rate_names = (
            "uncachedInputTokens",
            "cacheReadInputTokens",
            "cacheWriteInputTokens",
            "nonReasoningOutputTokens",
        )
        observed = (input_tokens, cache_read, cache_write, output_tokens)
        billable = (uncached, cache_read, cache_write, output_tokens)
        warnings = warnings + ["aggregate-output-priced-at-equivalent-rates"]
    else:
        assert isinstance(reasoning, int)
        non_reasoning = output_tokens - reasoning  # type: ignore[operator]
        if non_reasoning < 0:
            return {
                "costStatus": "unpriced",
                "coverage": "none",
                "reasonCode": "invalid-breakdown",
                "warnings": sorted(warnings + ["cost-unresolved"]),
            }
        categories = _DETAILED_LINE_CATEGORIES
        rate_names = _RATE_FIELDS
        observed = (input_tokens, cache_read, cache_write, output_tokens, reasoning)
        billable = (uncached, cache_read, cache_write, non_reasoning, reasoning)
    lines: list[Mapping[str, object]] = []
    total = 0
    for category, rate_name, seen, quantity in zip(
        categories,
        rate_names,
        observed,
        billable,
    ):
        rate = entry.rates[rate_name]
        amount = (quantity * rate + (_MILLION // 2)) // _MILLION  # type: ignore[operator]
        if amount > MAX_SAFE_INTEGER or total + amount > MAX_SAFE_INTEGER:
            return {
                "costStatus": "unpriced",
                "coverage": "none",
                "reasonCode": "unsupported-meter",
                "warnings": sorted(warnings + ["cost-overflow", "cost-unresolved"]),
            }
        total += amount
        lines.append(
            {
                "chargeCategory": category,
                "observedQuantity": seen,
                "billableQuantity": quantity,
                "catalogEntryId": entry.entry_id,
                "priceSubunitsPerMillionTokens": rate,
                "amountSubunits": amount,
            }
        )
    return {
        "costStatus": "priced",
        "coverage": "complete",
        "currency": catalog.currency,
        "currencyScale": catalog.currency_scale,
        "totalSubunits": total,
        "lines": lines,
        "warnings": sorted(warnings),
    }


def _aggregate_output_pricing_is_exact(
    entry: AiPriceEntry,
    usage: Mapping[str, object],
) -> bool:
    """Return true only when one absent output subset cannot change cost."""

    return (
        usage["completeness"] == "partial"
        and usage["missing_fields"] == ("reasoningOutputTokens",)
        and all(
            isinstance(usage[name], int)
            for name in (
                "inputTokens",
                "outputTokens",
                "cacheReadInputTokens",
                "cacheWriteInputTokens",
            )
        )
        and entry.rates["nonReasoningOutputTokens"]
        == entry.rates["reasoningOutputTokens"]
    )


def _validate_cost_result(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError
    status = value.get("costStatus")
    if status == "priced":
        result = _closed(
            value,
            {
                "costStatus",
                "coverage",
                "currency",
                "currencyScale",
                "totalSubunits",
                "lines",
                "warnings",
            },
        )
        if result["coverage"] != "complete":
            raise ValueError
        _matched(result["currency"], _CURRENCY)
        if _safe_integer(result["currencyScale"]) not in (6, 9, 12):
            raise ValueError
        total = _safe_integer(result["totalSubunits"])
        lines = result["lines"]
        if not isinstance(lines, list) or len(lines) not in (4, 5):
            raise ValueError
        categories: list[str] = []
        amount_sum = 0
        for line_value in lines:
            line = _closed(
                line_value,
                {
                    "chargeCategory",
                    "observedQuantity",
                    "billableQuantity",
                    "catalogEntryId",
                    "priceSubunitsPerMillionTokens",
                    "amountSubunits",
                },
            )
            categories.append(_text(line["chargeCategory"], maximum=64))
            _safe_integer(line["observedQuantity"])
            _safe_integer(line["billableQuantity"])
            _matched(line["catalogEntryId"], _ENTRY_ID)
            _safe_integer(line["priceSubunitsPerMillionTokens"])
            amount_sum += _safe_integer(line["amountSubunits"])
        category_tuple = tuple(categories)
        if category_tuple not in {
            _DETAILED_LINE_CATEGORIES,
            _AGGREGATE_LINE_CATEGORIES,
        } or amount_sum != total:
            raise ValueError
    elif status in {"unpriced", "ambiguous"}:
        result = _closed(
            value,
            {"costStatus", "coverage", "reasonCode", "warnings"},
        )
        if status == "ambiguous" and result["reasonCode"] != "multiple-catalog-matches":
            raise ValueError
        if status == "unpriced" and result["reasonCode"] not in {
            "no-catalog-match",
            "missing-usage",
            "unsupported-meter",
            "invalid-breakdown",
        }:
            raise ValueError
        if result["coverage"] not in {"none", "partial"}:
            raise ValueError
    else:
        raise ValueError
    warnings_value = result["warnings"]
    if (
        not isinstance(warnings_value, list)
        or len(warnings_value) > 32
        or warnings_value != sorted(warnings_value)
        or len(set(warnings_value)) != len(warnings_value)
        or any(not _ENTRY_ID.fullmatch(item) for item in warnings_value)
    ):
        raise ValueError
    warning_set = set(warnings_value)
    if "calculated-cost-not-invoice" not in warning_set:
        raise ValueError
    if status == "priced" and "cost-unresolved" in warning_set:
        raise ValueError
    aggregate_warning = "aggregate-output-priced-at-equivalent-rates"
    if status == "priced" and (
        (tuple(categories) == _AGGREGATE_LINE_CATEGORIES)
        != (aggregate_warning in warning_set)
    ):
        raise ValueError
    if status != "priced" and "cost-unresolved" not in warning_set:
        raise ValueError
    if (
        status == "unpriced"
        and result["reasonCode"] == "unsupported-meter"
        and "cost-overflow" not in warning_set
    ):
        raise ValueError


def _cost_actor(tenant_id: object, worker_id: object) -> ActorContext:
    if (
        not isinstance(tenant_id, str)
        or not _TENANT_ID.fullmatch(tenant_id)
        or not isinstance(worker_id, str)
        or not _WORKER_ID.fullmatch(worker_id)
    ):
        raise AiCostConfigurationError("ai.cost.worker.invalid")
    return ActorContext(
        actor_id="ai-cost-worker:" + worker_id,
        tenant_id=tenant_id,
        roles=("ai-cost:calculate",),
    )


def _closed(
    value: object,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError
    allowed = required | (optional or set())
    if not required.issubset(value) or not set(value).issubset(allowed):
        raise ValueError
    return value

def _json_copy(value: object) -> Mapping[str, object]:
    copied = json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    )
    if not isinstance(copied, dict):
        raise ValueError
    return copied


def _text(value: object, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or not _SAFE_TEXT.fullmatch(value)
    ):
        raise ValueError
    return value


def _matched(value: object, pattern: re.Pattern[str]) -> str:
    result = _text(value, maximum=2048)
    if not pattern.fullmatch(result):
        raise ValueError
    return result


def _safe_integer(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_SAFE_INTEGER
    ):
        raise ValueError
    return value


def _string_set(
    value: object,
    maximum: int,
    *,
    pattern: re.Pattern[str] | None = None,
    allowed: frozenset[str] | None = None,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= maximum
        or value != sorted(value)
        or len(set(value)) != len(value)
    ):
        raise ValueError
    result = tuple(_text(item, maximum=256) for item in value)
    if pattern is not None and any(not pattern.fullmatch(item) for item in result):
        raise ValueError
    if allowed is not None and not set(result).issubset(allowed):
        raise ValueError
    return result


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(timezone.utc)


def _timestamp(value: object) -> tuple[datetime, str]:
    parsed = _parse_time(value)
    return parsed, _format_time(parsed)


def _format_time(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


__all__ = [
    "AiCostCalculationPass",
    "AiCostCalculationService",
    "AiCostConfigurationError",
    "AiPriceEntry",
    "ENGINE_VERSION",
    "InvalidAiCostInputError",
    "ValidatedAiPriceCatalog",
    "calculate_ai_cost_record",
    "validate_ai_cost_record",
    "validate_ai_usage_for_economics",
    "validate_ai_price_catalog",
]
