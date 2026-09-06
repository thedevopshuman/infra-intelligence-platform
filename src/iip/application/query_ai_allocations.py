"""Tenant-safe, generation-bound AI usage and calculated-cost allocation reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Mapping

from iip.application.attribute_ai_usage import (
    ENGINE_VERSION as ATTRIBUTION_ENGINE_VERSION,
    InvalidAiAttributionInputError,
    ValidatedAiAttributionPolicy,
    validate_ai_attribution_policy,
    validate_ai_attribution_source_binding,
    validate_ai_usage_attribution_record,
)
from iip.application.calculate_ai_cost import (
    ENGINE_VERSION as COST_ENGINE_VERSION,
    InvalidAiCostInputError,
    ValidatedAiPriceCatalog,
    calculate_ai_cost_record,
    validate_ai_cost_record,
    validate_ai_price_catalog,
    validate_ai_usage_for_economics,
)
from iip.application.ports import (
    ActorContext,
    AiAllocationLedgerQuery,
    AiAllocationMeasurement,
    AiAllocationTelemetrySink,
    AiEconomicsLedger,
    Clock,
    PersistenceError,
    PolicyDecisionPoint,
)


MAX_SAFE_INTEGER = 9_007_199_254_740_991
DEFAULT_MAX_INTERVAL_SECONDS = 31 * 24 * 60 * 60
DEFAULT_SOURCE_RECORD_LIMIT = 10_000
_GROUPS = frozenset({"application", "team"})
_ACTOR_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/@-]{0,255}")
_TENANT_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}")
_ROLE = re.compile(r"[a-z][a-z0-9._:-]{0,63}")
_POLICY_ID = re.compile(r"aap_[a-f0-9]{32}")
_CATALOG_ID = re.compile(r"apc_[a-f0-9]{32}")


class AiAllocationConfigurationError(ValueError):
    """Protected allocation-report configuration is absent, unsafe, or ambiguous."""


class AiAllocationQueryError(ValueError):
    """A public allocation query violates a closed bound or source invariant."""


class AiAllocationAuthorizationError(PermissionError):
    """Policy denied an allocation report for the authenticated tenant."""


@dataclass(frozen=True)
class AiAllocationSources:
    policy: ValidatedAiAttributionPolicy
    catalog: ValidatedAiPriceCatalog
    policy_document_digest: str
    catalog_document_digest: str


@dataclass(frozen=True)
class AiAllocationProjectionPass:
    tenant_id: str
    usage_records: int
    groups: int


class AiAllocationProjectionService:
    """Project a rolling ledger snapshot to bounded backend-neutral telemetry."""

    def __init__(
        self,
        ledger: AiEconomicsLedger,
        clock: Clock,
        attribution_policies: tuple[Mapping[str, object], ...],
        price_catalogs: tuple[Mapping[str, object], ...],
        telemetry_sink: AiAllocationTelemetrySink,
        *,
        allow_test_fixtures: bool = False,
        source_record_limit: int = DEFAULT_SOURCE_RECORD_LIMIT,
        window_seconds: int = 24 * 60 * 60,
    ) -> None:
        if (
            isinstance(source_record_limit, bool)
            or not isinstance(source_record_limit, int)
            or not 1 <= source_record_limit <= DEFAULT_SOURCE_RECORD_LIMIT
            or isinstance(window_seconds, bool)
            or not isinstance(window_seconds, int)
            or not 60 <= window_seconds <= DEFAULT_MAX_INTERVAL_SECONDS
        ):
            raise AiAllocationConfigurationError(
                "ai.allocation.configuration.invalid"
            )
        self._ledger = ledger
        self._clock = clock
        self._sources = validate_ai_allocation_sources(
            attribution_policies,
            price_catalogs,
            allow_test_fixtures=allow_test_fixtures,
        )
        self._sink = telemetry_sink
        self._source_record_limit = source_record_limit
        self._window = timedelta(seconds=window_seconds)

    @property
    def tenant_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._sources))

    def run_once(self, tenant_id: str, worker_id: str) -> AiAllocationProjectionPass:
        source = self._sources.get(tenant_id)
        if source is None or not isinstance(worker_id, str) or not worker_id:
            raise AiAllocationConfigurationError(
                "ai.allocation.configuration.invalid"
            )
        now, end = canonical_ai_economics_timestamp(self._clock.now())
        start = _format_time(now - self._window)
        actor = ActorContext(
            actor_id=f"ai-allocation-worker:{worker_id}",
            tenant_id=tenant_id,
            roles=("ai-allocation-report",),
        )
        rows = self._ledger.list_ai_allocation_rows(
            actor,
            AiAllocationLedgerQuery(
                start=start,
                end=end,
                policy_id=source.policy.policy_id,
                attribution_engine_version=ATTRIBUTION_ENGINE_VERSION,
                catalog_id=source.catalog.catalog_id,
                cost_engine_version=COST_ENGINE_VERSION,
                limit=self._source_record_limit + 1,
            ),
        )
        if not isinstance(rows, tuple) or len(rows) > self._source_record_limit:
            raise AiAllocationQueryError("ai.allocation.source-limit-exceeded")
        measurements: list[AiAllocationMeasurement] = []
        for group_by in ("application", "team"):
            report = build_ai_allocation_report(
                tenant_id,
                start,
                end,
                group_by,
                source,
                rows,
                generated_at=end,
                source_record_limit=self._source_record_limit,
            )
            spec = report["spec"]
            assert isinstance(spec, Mapping)
            raw_groups = spec["groups"]
            assert isinstance(raw_groups, list)
            for group in raw_groups:
                assert isinstance(group, Mapping)
                dimension = group.get("dimension")
                money = group.get("pricedCost")
                measurements.append(
                    AiAllocationMeasurement(
                        tenant_id=tenant_id,
                        dimension=group_by,
                        allocation_status=str(group["allocationStatus"]),
                        dimension_id=(
                            str(dimension["id"])
                            if isinstance(dimension, Mapping)
                            else None
                        ),
                        request_count=int(group["usageRecords"]),
                        input_tokens=int(group["inputTokens"]),
                        input_token_records=int(group["inputTokenRecords"]),
                        output_tokens=int(group["outputTokens"]),
                        output_token_records=int(group["outputTokenRecords"]),
                        priced_requests=int(group["pricedRecords"]),
                        unpriced_requests=int(group["unpricedRecords"]),
                        ambiguous_requests=int(group["ambiguousRecords"]),
                        pending_cost_requests=int(group["pendingCostRecords"]),
                        calculated_cost_subunits=(
                            int(money["totalSubunits"])
                            if isinstance(money, Mapping)
                            else None
                        ),
                        currency=source.catalog.currency,
                        currency_scale=source.catalog.currency_scale,
                    )
                )
        try:
            self._sink.record_ai_allocation_snapshot(
                tenant_id,
                tuple(measurements),
            )
        except Exception:
            # Telemetry remains fail-open after the authoritative aggregate succeeds.
            pass
        return AiAllocationProjectionPass(tenant_id, len(rows), len(measurements))


class AiAllocationReportService:
    """Authorize and aggregate immutable AI economics records for one tenant."""

    def __init__(
        self,
        ledger: AiEconomicsLedger,
        policy: PolicyDecisionPoint,
        clock: Clock,
        attribution_policies: tuple[Mapping[str, object], ...],
        price_catalogs: tuple[Mapping[str, object], ...],
        *,
        allow_test_fixtures: bool = False,
        source_record_limit: int = DEFAULT_SOURCE_RECORD_LIMIT,
        max_interval_seconds: int = DEFAULT_MAX_INTERVAL_SECONDS,
    ) -> None:
        if (
            not isinstance(allow_test_fixtures, bool)
            or isinstance(source_record_limit, bool)
            or not isinstance(source_record_limit, int)
            or not 1 <= source_record_limit <= DEFAULT_SOURCE_RECORD_LIMIT
            or isinstance(max_interval_seconds, bool)
            or not isinstance(max_interval_seconds, int)
            or not 1 <= max_interval_seconds <= DEFAULT_MAX_INTERVAL_SECONDS
        ):
            raise AiAllocationConfigurationError(
                "ai.allocation.configuration.invalid"
            )
        sources = validate_ai_allocation_sources(
            attribution_policies,
            price_catalogs,
            allow_test_fixtures=allow_test_fixtures,
        )
        self._ledger = ledger
        self._policy = policy
        self._clock = clock
        self._sources = sources
        self._source_record_limit = source_record_limit
        self._max_interval = timedelta(seconds=max_interval_seconds)

    @property
    def tenant_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._sources))

    def get(
        self,
        actor: ActorContext,
        *,
        start: str,
        end: str,
        group_by: str,
    ) -> Mapping[str, object]:
        _validate_actor(actor)
        start_value, start_text = canonical_ai_economics_timestamp(start)
        end_value, end_text = canonical_ai_economics_timestamp(end)
        if (
            end_value <= start_value
            or end_value - start_value > self._max_interval
            or group_by not in _GROUPS
        ):
            raise AiAllocationQueryError("request.invalid")
        source = self._sources.get(actor.tenant_id)
        if source is None:
            raise AiAllocationQueryError("ai.allocation.not-configured")
        decision = self._policy.decide(
            actor,
            "ai-economics:read",
            {
                "tenantId": actor.tenant_id,
                "start": start_text,
                "end": end_text,
                "groupBy": group_by,
            },
        )
        if not decision.allowed:
            raise AiAllocationAuthorizationError(decision.reason_code)
        query = AiAllocationLedgerQuery(
            start=start_text,
            end=end_text,
            policy_id=source.policy.policy_id,
            attribution_engine_version=ATTRIBUTION_ENGINE_VERSION,
            catalog_id=source.catalog.catalog_id,
            cost_engine_version=COST_ENGINE_VERSION,
            limit=self._source_record_limit + 1,
        )
        rows = self._ledger.list_ai_allocation_rows(actor, query)
        if not isinstance(rows, tuple) or len(rows) > self._source_record_limit:
            raise AiAllocationQueryError("ai.allocation.source-limit-exceeded")
        generated_at = canonical_ai_economics_timestamp(self._clock.now())[1]
        return build_ai_allocation_report(
            actor.tenant_id,
            start_text,
            end_text,
            group_by,
            source,
            rows,
            generated_at=generated_at,
            source_record_limit=self._source_record_limit,
        )


def validate_ai_allocation_sources(
    policies: tuple[Mapping[str, object], ...],
    catalogs: tuple[Mapping[str, object], ...],
    *,
    allow_test_fixtures: bool,
) -> Mapping[str, AiAllocationSources]:
    try:
        if (
            not isinstance(policies, tuple)
            or not policies
            or len(policies) > 1000
            or not isinstance(catalogs, tuple)
            or not catalogs
            or len(catalogs) > 1000
        ):
            raise ValueError
        validated_policies = tuple(
            validate_ai_attribution_policy(item) for item in policies
        )
        validated_catalogs = tuple(
            validate_ai_price_catalog(item) for item in catalogs
        )
        policy_by_tenant = {item.tenant_id: item for item in validated_policies}
        catalog_by_tenant = {item.tenant_id: item for item in validated_catalogs}
        policy_document_by_tenant = {
            validated.tenant_id: document
            for validated, document in zip(validated_policies, policies)
        }
        catalog_document_by_tenant = {
            validated.tenant_id: document
            for validated, document in zip(validated_catalogs, catalogs)
        }
        if (
            len(policy_by_tenant) != len(validated_policies)
            or len(catalog_by_tenant) != len(validated_catalogs)
            or set(policy_by_tenant) != set(catalog_by_tenant)
            or (
                not allow_test_fixtures
                and any(
                    item.source_kind == "test-fixture"
                    for item in validated_policies + validated_catalogs
                )
            )
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise AiAllocationConfigurationError(
            "ai.allocation.configuration.invalid"
        ) from None
    return {
        tenant_id: AiAllocationSources(
            policy_by_tenant[tenant_id],
            catalog_by_tenant[tenant_id],
            _document_digest(policy_document_by_tenant[tenant_id]),
            _document_digest(catalog_document_by_tenant[tenant_id]),
        )
        for tenant_id in sorted(policy_by_tenant)
    }


def validate_ai_allocation_ledger_query(
    actor: ActorContext,
    query: AiAllocationLedgerQuery,
) -> tuple[datetime, datetime]:
    """Validate exact tenant-independent storage bounds before adapter I/O."""

    try:
        _validate_actor(actor)
        start, start_text = canonical_ai_economics_timestamp(query.start)
        end, end_text = canonical_ai_economics_timestamp(query.end)
        if (
            start_text != query.start
            or end_text != query.end
            or end <= start
            or end - start > timedelta(seconds=DEFAULT_MAX_INTERVAL_SECONDS)
            or _POLICY_ID.fullmatch(query.policy_id) is None
            or query.attribution_engine_version != ATTRIBUTION_ENGINE_VERSION
            or _CATALOG_ID.fullmatch(query.catalog_id) is None
            or query.cost_engine_version != COST_ENGINE_VERSION
            or isinstance(query.limit, bool)
            or not isinstance(query.limit, int)
            or not 1 <= query.limit <= DEFAULT_SOURCE_RECORD_LIMIT + 1
        ):
            raise ValueError
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise AiAllocationQueryError("request.invalid") from None
    return start, end


def _validate_actor(actor: ActorContext) -> None:
    if (
        not isinstance(actor, ActorContext)
        or not isinstance(actor.actor_id, str)
        or actor.actor_id == "anonymous"
        or _ACTOR_ID.fullmatch(actor.actor_id) is None
        or not isinstance(actor.tenant_id, str)
        or _TENANT_ID.fullmatch(actor.tenant_id) is None
        or not isinstance(actor.roles, tuple)
        or len(actor.roles) > 64
        or len(set(actor.roles)) != len(actor.roles)
        or any(
            not isinstance(role, str) or _ROLE.fullmatch(role) is None
            for role in actor.roles
        )
    ):
        raise AiAllocationQueryError("request.invalid")


def build_ai_allocation_report(
    tenant_id: str,
    start: str,
    end: str,
    group_by: str,
    sources: AiAllocationSources,
    rows: tuple[
        tuple[
            Mapping[str, object],
            Mapping[str, object] | None,
            Mapping[str, object] | None,
        ],
        ...,
    ],
    *,
    generated_at: str,
    source_record_limit: int,
) -> Mapping[str, object]:
    """Validate source bindings and build one complete bounded aggregate."""

    if group_by not in _GROUPS:
        raise AiAllocationQueryError("request.invalid")
    groups: dict[tuple[str, str, str], dict[str, object]] = {}
    coverage = {
        "usageRecords": 0,
        "allocatedRecords": 0,
        "unallocatedRecords": 0,
        "pendingAttributionRecords": 0,
        "pricedRecords": 0,
        "unpricedRecords": 0,
        "ambiguousRecords": 0,
        "pendingCostRecords": 0,
    }
    totals = _empty_totals()
    seen_usage_ids: set[str] = set()
    try:
        for row in rows:
            if not isinstance(row, tuple) or len(row) != 3:
                raise ValueError
            usage_document, attribution_document, cost_document = row
            usage = validate_ai_usage_for_economics(
                usage_document,
                expected_tenant=tenant_id,
            )
            usage_id = str(usage["usage_record_id"])
            if usage_id in seen_usage_ids:
                raise ValueError
            seen_usage_ids.add(usage_id)
            allocation_status = "pending"
            dimension_id = ""
            dimension_name = ""
            reason_code = "not-yet-attributed"
            if attribution_document is not None:
                attribution = validate_ai_usage_attribution_record(attribution_document)
                validate_ai_attribution_source_binding(
                    attribution,
                    sources.policy.document,
                    usage_document,
                )
                attr_metadata = attribution["metadata"]
                attr_spec = attribution["spec"]
                assert isinstance(attr_metadata, Mapping) and isinstance(attr_spec, Mapping)
                if attr_metadata["tenantId"] != tenant_id:
                    raise ValueError
                resolution = attr_spec["resolution"]
                assert isinstance(resolution, Mapping)
                allocation_status = str(resolution["status"])
                if allocation_status == "allocated":
                    dimension = resolution[group_by]
                    assert isinstance(dimension, Mapping)
                    dimension_id = str(dimension["id"])
                    dimension_name = str(dimension["name"])
                    reason_code = ""
                else:
                    reason_code = str(resolution["reasonCode"])
            coverage[_allocation_coverage_name(allocation_status)] = _safe_add(
                int(coverage[_allocation_coverage_name(allocation_status)]), 1
            )
            key = (allocation_status, dimension_id, dimension_name)
            group = groups.setdefault(
                key,
                _empty_group(
                    allocation_status,
                    dimension_id,
                    dimension_name,
                    reason_code,
                ),
            )
            _accumulate_usage(group, usage)
            _accumulate_usage(totals, usage)
            cost_status = "pending"
            cost_amount: int | None = None
            if cost_document is not None:
                cost = validate_ai_cost_record(cost_document)
                cost_metadata = cost["metadata"]
                cost_spec = cost["spec"]
                assert isinstance(cost_metadata, Mapping) and isinstance(cost_spec, Mapping)
                expected_cost, _event = calculate_ai_cost_record(
                    sources.catalog,
                    usage_document,
                    calculated_at=str(cost_metadata["calculatedAt"]),
                )
                if cost != expected_cost or cost_metadata["tenantId"] != tenant_id:
                    raise ValueError
                result = cost_spec["result"]
                assert isinstance(result, Mapping)
                cost_status = str(result["costStatus"])
                if cost_status == "priced":
                    if (
                        result["currency"] != sources.catalog.currency
                        or result["currencyScale"] != sources.catalog.currency_scale
                    ):
                        raise ValueError
                    cost_amount = int(result["totalSubunits"])
            coverage[_cost_coverage_name(cost_status)] = _safe_add(
                int(coverage[_cost_coverage_name(cost_status)]), 1
            )
            _accumulate_cost(group, cost_status, cost_amount, sources.catalog)
            _accumulate_cost(totals, cost_status, cost_amount, sources.catalog)
        coverage["usageRecords"] = len(rows)
    except (
        AssertionError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        InvalidAiAttributionInputError,
        InvalidAiCostInputError,
    ):
        raise PersistenceError("storage.state.invalid") from None

    ordered_groups = [
        groups[key]
        for key in sorted(
            groups,
            key=lambda value: (
                {"allocated": 0, "unallocated": 1, "pending": 2}[value[0]],
                value[1],
                value[2],
            ),
        )
    ]
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "AiAllocationReport",
        "metadata": {
            "tenantId": tenant_id,
            "generatedAt": canonical_ai_economics_timestamp(generated_at)[1],
        },
        "spec": {
            "scope": {
                "start": canonical_ai_economics_timestamp(start)[1],
                "end": canonical_ai_economics_timestamp(end)[1],
                "groupBy": group_by,
                "sourceRecordLimit": source_record_limit,
            },
            "sources": {
                "attribution": {
                    "id": sources.policy.policy_id,
                    "version": sources.policy.version,
                    "sourceHash": sources.policy.source_hash,
                    "engineVersion": ATTRIBUTION_ENGINE_VERSION,
                },
                "pricing": {
                    "id": sources.catalog.catalog_id,
                    "version": sources.catalog.version,
                    "sourceHash": sources.catalog.source_hash,
                    "engineVersion": COST_ENGINE_VERSION,
                    "currency": sources.catalog.currency,
                    "currencyScale": sources.catalog.currency_scale,
                    "costBasis": "calculated-estimate",
                },
            },
            "coverage": coverage,
            "totals": totals,
            "groups": ordered_groups,
        },
    }


def _empty_totals() -> dict[str, object]:
    return {
        "inputTokens": 0,
        "inputTokenRecords": 0,
        "outputTokens": 0,
        "outputTokenRecords": 0,
    }


def _empty_group(
    status: str,
    dimension_id: str,
    dimension_name: str,
    reason_code: str,
) -> dict[str, object]:
    group = {
        "allocationStatus": status,
        "usageRecords": 0,
        **_empty_totals(),
        "pricedRecords": 0,
        "unpricedRecords": 0,
        "ambiguousRecords": 0,
        "pendingCostRecords": 0,
    }
    if status == "allocated":
        group["dimension"] = {"id": dimension_id, "name": dimension_name}
    else:
        group["reasonCode"] = reason_code
    return group


def _accumulate_usage(target: dict[str, object], usage: Mapping[str, object]) -> None:
    if "usageRecords" in target:
        target["usageRecords"] = _safe_add(int(target["usageRecords"]), 1)
    for source_name, total_name, records_name in (
        ("inputTokens", "inputTokens", "inputTokenRecords"),
        ("outputTokens", "outputTokens", "outputTokenRecords"),
    ):
        quantity = usage[source_name]
        if quantity is not None:
            target[total_name] = _safe_add(int(target[total_name]), int(quantity))
            target[records_name] = _safe_add(int(target[records_name]), 1)


def _accumulate_cost(
    target: dict[str, object],
    status: str,
    amount: int | None,
    catalog: ValidatedAiPriceCatalog,
) -> None:
    if "usageRecords" in target:
        field = _cost_coverage_name(status)
        target[field] = _safe_add(int(target[field]), 1)
    if amount is None:
        return
    money = target.setdefault(
        "pricedCost",
        {
            "currency": catalog.currency,
            "currencyScale": catalog.currency_scale,
            "totalSubunits": 0,
            "costBasis": "calculated-estimate",
        },
    )
    assert isinstance(money, dict)
    money["totalSubunits"] = _safe_add(int(money["totalSubunits"]), amount)


def _allocation_coverage_name(status: str) -> str:
    return {
        "allocated": "allocatedRecords",
        "unallocated": "unallocatedRecords",
        "pending": "pendingAttributionRecords",
    }[status]


def _cost_coverage_name(status: str) -> str:
    return {
        "priced": "pricedRecords",
        "unpriced": "unpricedRecords",
        "ambiguous": "ambiguousRecords",
        "pending": "pendingCostRecords",
    }[status]


def _safe_add(left: int, right: int) -> int:
    result = left + right
    if left < 0 or right < 0 or result > MAX_SAFE_INTEGER:
        raise ValueError
    return result


def _document_digest(document: Mapping[str, object]) -> str:
    payload = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def canonical_ai_economics_timestamp(value: object) -> tuple[datetime, str]:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AiAllocationQueryError("request.invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise AiAllocationQueryError("request.invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise AiAllocationQueryError("request.invalid")
    parsed = parsed.astimezone(timezone.utc)
    canonical = parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
    canonical = canonical.replace(".000000Z", "Z")
    return parsed, canonical


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    ).replace(".000000Z", "Z")


__all__ = [
    "AiAllocationAuthorizationError",
    "AiAllocationConfigurationError",
    "AiAllocationQueryError",
    "AiAllocationProjectionPass",
    "AiAllocationProjectionService",
    "AiAllocationReportService",
    "AiAllocationSources",
    "build_ai_allocation_report",
    "canonical_ai_economics_timestamp",
    "validate_ai_allocation_sources",
    "validate_ai_allocation_ledger_query",
]
