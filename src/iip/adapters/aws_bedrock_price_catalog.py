"""Exact AWS Price List import adapter for protected Bedrock price catalogs."""

from __future__ import annotations

import hashlib
import json
import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from collections.abc import Set
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from iip.application.calculate_ai_cost import (
    MAX_SAFE_INTEGER,
    AiCostConfigurationError,
    validate_ai_price_catalog,
)


IMPORTER_VERSION = "0.1.0"
_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_POLICY_ID = re.compile(r"abp_[a-f0-9]{32}")
_VERSION = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[.][0-9]+")
_ENTRY_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_REGION = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
_REFERENCE = re.compile(r"[A-Z0-9]{8,32}")
_RATE_CODE = re.compile(r"[A-Z0-9]{8,32}[.][A-Z0-9]{8,32}[.][A-Z0-9]{8,32}")
_SOURCE_VERSION = re.compile(r"[0-9]{8,14}")
_LOCATOR_PATH = re.compile(
    r"/offers/v1[.]0/aws/AmazonBedrock/(?:current|[0-9]{8,14})/index[.]json"
)
_SAFE_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,256}")
_RATE_FIELDS = (
    "uncachedInputTokens",
    "cacheReadInputTokens",
    "cacheWriteInputTokens",
    "nonReasoningOutputTokens",
    "reasoningOutputTokens",
)
_SERVICE_TIERS = frozenset(
    {"default", "standard", "flex", "priority", "reserved", "unknown"}
)
_ROUTING_MODES = frozenset({"in-region", "geographic", "global", "unknown"})
_PURCHASE_MODES = frozenset(
    {"on-demand", "batch", "provisioned-throughput", "unknown"}
)
_EXPECTED_ATTRIBUTE_KEYS = frozenset(
    {"feature", "inferenceType", "model", "provider", "regionCode", "usagetype"}
)


class AwsBedrockPriceCatalogImportError(ValueError):
    """The protected mapping, provider snapshot, or derived output is invalid."""


@dataclass(frozen=True)
class AwsPriceRateReference:
    sku: str
    offer_term_code: str
    rate_code: str
    expected_unit: str
    expected_attributes: Mapping[str, str]


@dataclass(frozen=True)
class AwsBedrockPriceEntryMapping:
    document: Mapping[str, object]
    entry_id: str
    model_id: str
    region: str
    service_tier: str
    routing_mode: str
    purchase_mode: str
    effective_from: str
    effective_until: str | None
    rates: Mapping[str, AwsPriceRateReference]


@dataclass(frozen=True)
class ValidatedAwsBedrockPriceCatalogImportPolicy:
    document: Mapping[str, object]
    policy_id: str
    tenant_id: str
    version: str
    locator: str
    maximum_bytes: int
    catalog_version: str
    currency_scale: int
    entries: tuple[AwsBedrockPriceEntryMapping, ...]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, url):
        return None


def derive_aws_bedrock_price_catalog_import_policy_id(document: object) -> str:
    """Return the content-derived ID after validating the remaining policy."""

    try:
        copied = _json_copy(document)
        root = _closed(copied, {"apiVersion", "kind", "metadata", "spec"})
        metadata = _closed(root["metadata"], {"id", "tenantId", "version"})
        tenant_id = _matched(metadata["tenantId"], _TENANT_ID)
        version = _matched(metadata["version"], _VERSION)
        expected = "abp_" + _canonical_digest(
            {"tenantId": tenant_id, "version": version, "spec": root["spec"]}
        )[:32]
        mutable_metadata = dict(metadata)
        mutable_metadata["id"] = expected
        copied["metadata"] = mutable_metadata
        validate_aws_bedrock_price_catalog_import_policy(copied)
        return expected
    except (KeyError, TypeError, ValueError, OverflowError):
        raise AwsBedrockPriceCatalogImportError(
            "ai.price-import.policy.invalid"
        ) from None


def validate_aws_bedrock_price_catalog_import_policy(
    document: object,
) -> ValidatedAwsBedrockPriceCatalogImportPolicy:
    """Validate a content-addressed protected mapping policy."""

    try:
        copied = _json_copy(document)
        root = _closed(copied, {"apiVersion", "kind", "metadata", "spec"})
        if (
            root["apiVersion"] != "iip.platform/v1alpha1"
            or root["kind"] != "AwsBedrockPriceCatalogImportPolicy"
        ):
            raise ValueError
        metadata = _closed(root["metadata"], {"id", "tenantId", "version"})
        spec = _closed(root["spec"], {"source", "catalog", "entries"})
        source = _closed(
            spec["source"],
            {"profile", "serviceCode", "locator", "maximumBytes"},
        )
        catalog = _closed(
            spec["catalog"], {"version", "currency", "currencyScale"}
        )
        policy_id = _matched(metadata["id"], _POLICY_ID)
        tenant_id = _matched(metadata["tenantId"], _TENANT_ID)
        version = _matched(metadata["version"], _VERSION)
        if source["profile"] != "aws-price-list-v1":
            raise ValueError
        if source["serviceCode"] != "AmazonBedrock":
            raise ValueError
        locator = _locator(source["locator"])
        maximum_bytes = _integer(
            source["maximumBytes"], minimum=1_048_576, maximum=67_108_864
        )
        catalog_version = _matched(catalog["version"], _VERSION)
        if catalog["currency"] != "USD":
            raise ValueError
        currency_scale = _integer(catalog["currencyScale"], minimum=6, maximum=12)
        if currency_scale not in {6, 9, 12}:
            raise ValueError
        raw_entries = spec["entries"]
        if not isinstance(raw_entries, list) or not 1 <= len(raw_entries) <= 10_000:
            raise ValueError
        entries = tuple(_entry(item) for item in raw_entries)
        entry_ids = tuple(item.entry_id for item in entries)
        if entry_ids != tuple(sorted(entry_ids)) or len(set(entry_ids)) != len(entries):
            raise ValueError
        identity = {"tenantId": tenant_id, "version": version, "spec": spec}
        if policy_id != "abp_" + _canonical_digest(identity)[:32]:
            raise ValueError
        return ValidatedAwsBedrockPriceCatalogImportPolicy(
            root,
            policy_id,
            tenant_id,
            version,
            locator,
            maximum_bytes,
            catalog_version,
            currency_scale,
            entries,
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        raise AwsBedrockPriceCatalogImportError(
            "ai.price-import.policy.invalid"
        ) from None


def download_aws_bedrock_price_list(
    policy_document: object,
    *,
    timeout_seconds: int = 30,
) -> bytes:
    """Fetch one policy-selected official snapshot without proxy or redirect use."""

    policy = validate_aws_bedrock_price_catalog_import_policy(policy_document)
    try:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 120
        ):
            raise ValueError
        request = Request(
            policy.locator,
            headers={
                "Accept": "application/json",
                "User-Agent": "iip-aws-price-import/0.1.0",
            },
            method="GET",
        )
        opener = build_opener(
            ProxyHandler({}),
            _NoRedirect(),
            HTTPSHandler(context=ssl.create_default_context()),
        )
        with opener.open(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise ValueError
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > policy.maximum_bytes:
                raise ValueError
            payload = response.read(policy.maximum_bytes + 1)
        if not 2 <= len(payload) <= policy.maximum_bytes:
            raise ValueError
        return payload
    except (HTTPError, URLError, OSError, TypeError, ValueError):
        raise AwsBedrockPriceCatalogImportError(
            "ai.price-import.download.invalid"
        ) from None


def import_aws_bedrock_price_catalog(
    source_bytes: bytes,
    policy_document: object,
    *,
    retrieved_at: str,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    """Derive one exact catalog and minimized reproducibility report."""

    policy = validate_aws_bedrock_price_catalog_import_policy(policy_document)
    try:
        if (
            not isinstance(source_bytes, bytes)
            or not 2 <= len(source_bytes) <= policy.maximum_bytes
        ):
            raise ValueError
        source = json.loads(source_bytes.decode("utf-8"))
        if not isinstance(source, dict):
            raise ValueError
        if source.get("formatVersion") != "aws_v1":
            raise ValueError
        if source.get("offerCode") != "AmazonBedrock":
            raise ValueError
        source_version = _matched(source.get("version"), _SOURCE_VERSION)
        locator_version = urlsplit(policy.locator).path.rsplit("/", 2)[-2]
        if locator_version != "current" and locator_version != source_version:
            raise ValueError
        catalog_locator = policy.locator.replace(
            f"/{locator_version}/index.json",
            f"/{source_version}/index.json",
        )
        publication_date = _timestamp(source.get("publicationDate"))
        retrieved = _timestamp(retrieved_at)
        if publication_date > retrieved:
            raise ValueError
        products = source.get("products")
        terms = source.get("terms")
        if not isinstance(products, dict) or not isinstance(terms, dict):
            raise ValueError
        on_demand = terms.get("OnDemand")
        if not isinstance(on_demand, dict):
            raise ValueError

        unique_dimensions: set[tuple[str, str, str]] = set()
        catalog_entries: list[Mapping[str, object]] = []
        for entry in policy.entries:
            rates: dict[str, Mapping[str, int]] = {}
            for meter in _RATE_FIELDS:
                reference = entry.rates[meter]
                price = _resolve_rate(
                    products,
                    on_demand,
                    entry,
                    meter,
                    reference,
                    currency_scale=policy.currency_scale,
                )
                rates[meter] = {"priceSubunitsPerMillionTokens": price}
                unique_dimensions.add(
                    (
                        reference.sku,
                        reference.offer_term_code,
                        reference.rate_code,
                    )
                )
            generated_entry: dict[str, object] = {
                "id": entry.entry_id,
                "provider": "aws.bedrock",
                "modelId": entry.model_id,
                "regions": [entry.region],
                "serviceTiers": [entry.service_tier],
                "routingModes": [entry.routing_mode],
                "purchaseModes": [entry.purchase_mode],
                "effectiveFrom": entry.effective_from,
                "rates": rates,
            }
            if entry.effective_until is not None:
                generated_entry["effectiveUntil"] = entry.effective_until
            catalog_entries.append(generated_entry)

        source_hash = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
        spec: dict[str, object] = {
            "currency": "USD",
            "currencyScale": policy.currency_scale,
            "source": {
                "kind": "provider-published",
                "locator": catalog_locator,
                "retrievedAt": retrieved,
                "contentHash": source_hash,
            },
            "entries": catalog_entries,
        }
        catalog_identity = {
            "tenantId": policy.tenant_id,
            "version": policy.catalog_version,
            "spec": spec,
        }
        catalog: Mapping[str, object] = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "AiPriceCatalog",
            "metadata": {
                "id": "apc_" + _canonical_digest(catalog_identity)[:32],
                "tenantId": policy.tenant_id,
                "version": policy.catalog_version,
                "publishedAt": retrieved,
            },
            "spec": spec,
        }
        validated_catalog = validate_ai_price_catalog(catalog).document
        report = _import_report(
            policy,
            validated_catalog,
            source_hash=source_hash,
            source_size=len(source_bytes),
            source_version=source_version,
            publication_date=publication_date,
            retrieved_at=retrieved,
            unique_dimension_count=len(unique_dimensions),
        )
        return _json_copy(validated_catalog), report
    except AiCostConfigurationError:
        raise AwsBedrockPriceCatalogImportError(
            "ai.price-import.catalog.invalid"
        ) from None
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        raise AwsBedrockPriceCatalogImportError(
            "ai.price-import.source.invalid"
        ) from None


def verify_aws_bedrock_price_catalog_import(
    source_bytes: bytes,
    policy_document: object,
    catalog_document: object,
    report_document: object,
) -> Mapping[str, object]:
    """Reproduce both outputs from the retained exact source and policy."""

    try:
        if not isinstance(report_document, Mapping):
            raise ValueError
        metadata = report_document.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError
        generated_at = metadata.get("generatedAt")
        if not isinstance(generated_at, str):
            raise ValueError
        expected_catalog, expected_report = import_aws_bedrock_price_catalog(
            source_bytes,
            policy_document,
            retrieved_at=generated_at,
        )
        if catalog_document != expected_catalog or report_document != expected_report:
            raise ValueError
        return _json_copy(expected_report)
    except (AwsBedrockPriceCatalogImportError, TypeError, ValueError):
        raise AwsBedrockPriceCatalogImportError(
            "ai.price-import.report.invalid"
        ) from None


def _entry(value: object) -> AwsBedrockPriceEntryMapping:
    item = _closed(
        value,
        {
            "id",
            "modelId",
            "region",
            "serviceTier",
            "routingMode",
            "purchaseMode",
            "effectiveFrom",
            "rates",
        },
        optional={"effectiveUntil"},
    )
    entry_id = _matched(item["id"], _ENTRY_ID)
    model_id = _text(item["modelId"])
    region = _matched(item["region"], _REGION)
    service_tier = _choice(item["serviceTier"], _SERVICE_TIERS)
    routing_mode = _choice(item["routingMode"], _ROUTING_MODES)
    purchase_mode = _choice(item["purchaseMode"], _PURCHASE_MODES)
    effective_from = _timestamp(item["effectiveFrom"])
    effective_until = (
        _timestamp(item["effectiveUntil"])
        if "effectiveUntil" in item
        else None
    )
    if effective_until is not None and effective_until <= effective_from:
        raise ValueError
    raw_rates = _closed(item["rates"], set(_RATE_FIELDS))
    rates = {field: _rate_reference(raw_rates[field]) for field in _RATE_FIELDS}
    expected_profiles = tuple(
        (
            reference.expected_attributes["model"],
            reference.expected_attributes["provider"],
            reference.expected_attributes["regionCode"],
        )
        for reference in rates.values()
    )
    if (
        len(set(expected_profiles)) != 1
        or expected_profiles[0][2] != region
        or any(
            not _meter_attribute_compatible(
                meter, reference.expected_attributes["inferenceType"]
            )
            for meter, reference in rates.items()
        )
    ):
        raise ValueError
    return AwsBedrockPriceEntryMapping(
        item,
        entry_id,
        model_id,
        region,
        service_tier,
        routing_mode,
        purchase_mode,
        effective_from,
        effective_until,
        rates,
    )


def _rate_reference(value: object) -> AwsPriceRateReference:
    item = _closed(
        value,
        {"sku", "offerTermCode", "rateCode", "expectedUnit", "expectedAttributes"},
    )
    sku = _matched(item["sku"], _REFERENCE)
    offer_term_code = _matched(item["offerTermCode"], _REFERENCE)
    rate_code = _matched(item["rateCode"], _RATE_CODE)
    if not rate_code.startswith(f"{sku}.{offer_term_code}."):
        raise ValueError
    expected_unit = _choice(item["expectedUnit"], frozenset({"1K tokens", "1M tokens"}))
    raw_attributes = _closed(item["expectedAttributes"], set(_EXPECTED_ATTRIBUTE_KEYS))
    attributes = {key: _text(raw_attributes[key]) for key in _EXPECTED_ATTRIBUTE_KEYS}
    _matched(attributes["regionCode"], _REGION)
    return AwsPriceRateReference(
        sku,
        offer_term_code,
        rate_code,
        expected_unit,
        attributes,
    )


def _resolve_rate(
    products: Mapping[str, object],
    on_demand: Mapping[str, object],
    entry: AwsBedrockPriceEntryMapping,
    meter: str,
    reference: AwsPriceRateReference,
    *,
    currency_scale: int,
) -> int:
    product = products.get(reference.sku)
    if not isinstance(product, Mapping):
        raise ValueError
    if product.get("sku") != reference.sku:
        raise ValueError
    attributes = product.get("attributes")
    if not isinstance(attributes, Mapping):
        raise ValueError
    if attributes.get("servicecode") != "AmazonBedrock":
        raise ValueError
    for key, expected in reference.expected_attributes.items():
        if attributes.get(key) != expected:
            raise ValueError
    if not _meter_attribute_compatible(meter, str(attributes["inferenceType"])):
        raise ValueError

    sku_terms = on_demand.get(reference.sku)
    if not isinstance(sku_terms, Mapping):
        raise ValueError
    term = sku_terms.get(f"{reference.sku}.{reference.offer_term_code}")
    if not isinstance(term, Mapping):
        raise ValueError
    if (
        term.get("sku") != reference.sku
        or term.get("offerTermCode") != reference.offer_term_code
        or _timestamp(term.get("effectiveDate")) != entry.effective_from
    ):
        raise ValueError
    dimensions = term.get("priceDimensions")
    if not isinstance(dimensions, Mapping):
        raise ValueError
    dimension = dimensions.get(reference.rate_code)
    if not isinstance(dimension, Mapping):
        raise ValueError
    if (
        dimension.get("rateCode") != reference.rate_code
        or dimension.get("beginRange") != "0"
        or dimension.get("endRange") != "Inf"
        or dimension.get("unit") != reference.expected_unit
        or dimension.get("appliesTo") != []
    ):
        raise ValueError
    price_per_unit = dimension.get("pricePerUnit")
    if not isinstance(price_per_unit, Mapping) or set(price_per_unit) != {"USD"}:
        raise ValueError
    return _price_subunits(
        price_per_unit["USD"],
        unit=reference.expected_unit,
        currency_scale=currency_scale,
    )


def _price_subunits(value: object, *, unit: str, currency_scale: int) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:[.][0-9]+)?", value):
        raise ValueError
    try:
        amount = Decimal(value)
        multiplier = Decimal(1000 if unit == "1K tokens" else 1)
        scaled = amount * multiplier * (Decimal(10) ** currency_scale)
        integral = scaled.to_integral_value()
    except (InvalidOperation, OverflowError):
        raise ValueError from None
    if scaled != integral:
        raise ValueError
    result = int(integral)
    if not 0 <= result <= MAX_SAFE_INTEGER:
        raise ValueError
    return result


def _import_report(
    policy: ValidatedAwsBedrockPriceCatalogImportPolicy,
    catalog: Mapping[str, object],
    *,
    source_hash: str,
    source_size: int,
    source_version: str,
    publication_date: str,
    retrieved_at: str,
    unique_dimension_count: int,
) -> Mapping[str, object]:
    metadata = catalog["metadata"]
    spec = catalog["spec"]
    assert isinstance(metadata, Mapping)
    assert isinstance(spec, Mapping)
    checks = [
        {"id": "source-envelope", "status": "passed"},
        {"id": "policy-identity", "status": "passed"},
        {"id": "exact-rate-references", "status": "passed"},
        {"id": "decimal-conversion", "status": "passed"},
        {"id": "catalog-contract", "status": "passed"},
    ]
    report: dict[str, object] = {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "AiPriceCatalogImportReport",
        "metadata": {
            "tenantId": policy.tenant_id,
            "generatedAt": retrieved_at,
        },
        "spec": {
            "status": "imported",
            "sourceProfile": "aws-price-list-v1",
            "importerVersion": IMPORTER_VERSION,
            "source": {
                "serviceCode": "AmazonBedrock",
                "version": source_version,
                "contentHash": source_hash,
                "publicationDate": publication_date,
                "retrievedAt": retrieved_at,
                "sizeBytes": source_size,
            },
            "policy": {
                "id": policy.policy_id,
                "version": policy.version,
                "documentDigest": "sha256:" + _canonical_digest(policy.document),
            },
            "catalog": {
                "id": metadata["id"],
                "version": metadata["version"],
                "documentDigest": "sha256:" + _canonical_digest(catalog),
                "currency": spec["currency"],
                "currencyScale": spec["currencyScale"],
                "entryCount": len(policy.entries),
            },
            "measurements": {
                "rateReferenceCount": len(policy.entries) * len(_RATE_FIELDS),
                "uniquePriceDimensionCount": unique_dimension_count,
            },
            "checks": checks,
            "summary": {
                "totalChecks": len(checks),
                "passedChecks": len(checks),
                "failedChecks": 0,
                "overallStatus": "imported",
            },
        },
    }
    report_metadata = report["metadata"]
    assert isinstance(report_metadata, dict)
    identity = {
        "apiVersion": report["apiVersion"],
        "kind": report["kind"],
        "metadata": report_metadata,
        "spec": report["spec"],
    }
    report_metadata["id"] = "apir_" + _canonical_digest(identity)[:32]
    report["metadata"] = {
        "id": report_metadata["id"],
        "tenantId": report_metadata["tenantId"],
        "generatedAt": report_metadata["generatedAt"],
    }
    return _json_copy(report)


def _meter_attribute_compatible(meter: str, inference_type: str) -> bool:
    normalized = " ".join(inference_type.lower().split())
    if meter == "cacheReadInputTokens":
        return "cache read" in normalized and "input" in normalized and "token" in normalized
    if meter == "cacheWriteInputTokens":
        return "cache write" in normalized and "input" in normalized and "token" in normalized
    if meter == "uncachedInputTokens":
        return "cache" not in normalized and "input" in normalized and "token" in normalized
    return "output" in normalized and "token" in normalized


def _locator(value: object) -> str:
    locator = _text(value)
    parsed = urlsplit(locator)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "pricing.us-east-1.amazonaws.com"
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or _LOCATOR_PATH.fullmatch(parsed.path) is None
    ):
        raise ValueError
    return locator


def _closed(
    value: object,
    required: Set[str],
    *,
    optional: Set[str] = frozenset(),
) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != required | optional.intersection(value):
        raise ValueError
    return value


def _choice(value: object, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or _SAFE_TEXT.fullmatch(value) is None:
        raise ValueError
    return value


def _matched(value: object, pattern: re.Pattern[str]) -> str:
    result = _text(value)
    if pattern.fullmatch(result) is None:
        raise ValueError
    return result


def _integer(value: object, *, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError
    return value


def _timestamp(value: object) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _json_copy(value: object) -> dict[str, object]:
    copied = json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))
    if not isinstance(copied, dict):
        raise ValueError
    return copied


__all__ = [
    "AwsBedrockPriceCatalogImportError",
    "IMPORTER_VERSION",
    "ValidatedAwsBedrockPriceCatalogImportPolicy",
    "derive_aws_bedrock_price_catalog_import_policy_id",
    "download_aws_bedrock_price_list",
    "import_aws_bedrock_price_catalog",
    "validate_aws_bedrock_price_catalog_import_policy",
    "verify_aws_bedrock_price_catalog_import",
]
