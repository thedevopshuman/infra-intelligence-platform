"""Deterministic evidence-backed AI savings evaluation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from iip.application.calculate_ai_cost import (
    ENGINE_VERSION as COST_ENGINE_VERSION,
    MAX_SAFE_INTEGER,
    InvalidAiCostInputError,
    validate_ai_cost_record,
    validate_ai_usage_for_economics,
)
from iip.application.ports import (
    ActorContext,
    AiEconomicsLedger,
    AiSavingsCohortQuery,
    Clock,
)
from iip.domain.models import PlatformEvent


RULE_ID = "context-growth"
RULE_VERSION = "1.0.0"
MAX_COHORT_RECORDS = 100
_MILLION = 1_000_000
_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_WORKER_ID = re.compile(r"[A-Za-z0-9._:-]{1,256}")
_PROFILE_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_PROVIDER = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
_REGION = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
_CATALOG_ID = re.compile(r"apc_[a-f0-9]{32}")
_FINDING_ID = re.compile(r"aif_[a-f0-9]{32}")
_USAGE_ID = re.compile(r"aiu_[a-f0-9]{32}")
_COST_ID = re.compile(r"aic_[a-f0-9]{32}")
_SAFE_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,1024}")


class AiSavingsConfigurationError(ValueError):
    """A protected rule profile or worker setting is invalid."""


class InvalidAiSavingsInputError(ValueError):
    """Stored source facts or a proposed finding are invalid."""


@dataclass(frozen=True)
class ContextGrowthProfile:
    profile_id: str
    tenant_id: str
    catalog_id: str
    cost_engine_version: str
    provider: str
    model_id: str
    region: str
    service_name: str
    deployment_environment: str
    baseline_start: str
    baseline_end: str
    current_start: str
    current_end: str
    minimum_requests: int
    growth_threshold_basis_points: int
    max_records_per_window: int
    evaluation_grace_seconds: int


@dataclass(frozen=True)
class AiSavingsEvaluationPass:
    profiles: int
    qualified: int
    pending: int
    insufficient: int
    unresolved: int
    unsupported: int
    below_threshold: int
    failures: int


@dataclass(frozen=True)
class _CohortItem:
    usage_id: str
    cost_id: str
    started_at: datetime
    input_tokens: int
    currency: str
    currency_scale: int
    uncached_input_rate: int


class AiSavingsEvaluationService:
    """Evaluate fixed comparison windows outside the inference request path."""

    def __init__(
        self,
        ledger: AiEconomicsLedger,
        clock: Clock,
        profiles: tuple[Mapping[str, object], ...],
    ) -> None:
        if (
            not isinstance(profiles, tuple)
            or not profiles
            or len(profiles) > 1000
        ):
            raise AiSavingsConfigurationError(
                "ai.savings.configuration.invalid"
            )
        validated = tuple(validate_context_growth_profile(item) for item in profiles)
        identities = tuple((item.tenant_id, item.profile_id) for item in validated)
        if len(set(identities)) != len(identities):
            raise AiSavingsConfigurationError("ai.savings.profile.ambiguous")
        self._ledger = ledger
        self._clock = clock
        self._profiles = tuple(
            sorted(validated, key=lambda item: (item.tenant_id, item.profile_id))
        )

    @property
    def tenant_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.tenant_id for item in self._profiles}))

    def run_once(self, tenant_id: str, worker_id: str) -> AiSavingsEvaluationPass:
        actor = _savings_actor(tenant_id, worker_id)
        profiles = tuple(
            profile for profile in self._profiles if profile.tenant_id == tenant_id
        )
        if not profiles:
            raise AiSavingsConfigurationError("ai.savings.profile.missing")
        now = _parse_time(self._clock.now())
        counts = {
            "qualified": 0,
            "pending": 0,
            "insufficient": 0,
            "unresolved": 0,
            "unsupported": 0,
            "below-threshold": 0,
            "failures": 0,
        }
        for profile in profiles:
            try:
                status, finding, event = self._evaluate(profile, actor, now)
                counts[status] += 1
                if finding is not None and event is not None:
                    self._ledger.commit_ai_savings_batch(
                        actor,
                        (finding,),
                        (event,),
                    )
            except Exception:
                # Source identifiers, prices, quantities, and errors stay out of logs.
                counts["failures"] += 1
        return AiSavingsEvaluationPass(
            len(profiles),
            counts["qualified"],
            counts["pending"],
            counts["insufficient"],
            counts["unresolved"],
            counts["unsupported"],
            counts["below-threshold"],
            counts["failures"],
        )

    def _evaluate(
        self,
        profile: ContextGrowthProfile,
        actor: ActorContext,
        now: datetime,
    ) -> tuple[str, Mapping[str, object] | None, PlatformEvent | None]:
        current_end = _parse_time(profile.current_end)
        if now < current_end + timedelta(seconds=profile.evaluation_grace_seconds):
            return "pending", None, None
        baseline_rows = self._ledger.list_ai_savings_cohort(
            actor,
            _cohort_query(profile, baseline=True),
        )
        current_rows = self._ledger.list_ai_savings_cohort(
            actor,
            _cohort_query(profile, baseline=False),
        )
        if (
            len(baseline_rows) > profile.max_records_per_window
            or len(current_rows) > profile.max_records_per_window
        ):
            return "unsupported", None, None
        if (
            len(baseline_rows) < profile.minimum_requests
            or len(current_rows) < profile.minimum_requests
        ):
            return "insufficient", None, None
        if any(cost is None for _usage, cost in baseline_rows + current_rows):
            return "unresolved", None, None
        try:
            baseline = tuple(
                _cohort_item(profile, usage, cost)
                for usage, cost in baseline_rows
                if cost is not None
            )
            current = tuple(
                _cohort_item(profile, usage, cost)
                for usage, cost in current_rows
                if cost is not None
            )
        except _UnresolvedCohort:
            return "unresolved", None, None
        except _UnsupportedCohort:
            return "unsupported", None, None
        if not baseline or not current:
            return "insufficient", None, None
        if len({(item.currency, item.currency_scale) for item in baseline + current}) != 1:
            return "unresolved", None, None
        current_rates = {item.uncached_input_rate for item in current}
        if len(current_rates) != 1:
            return "unresolved", None, None

        baseline_mean = _half_up_divide(
            sum(item.input_tokens for item in baseline), len(baseline)
        )
        current_mean = _half_up_divide(
            sum(item.input_tokens for item in current), len(current)
        )
        if baseline_mean == 0 or current_mean <= baseline_mean:
            return "below-threshold", None, None
        change = _half_up_divide(
            (current_mean - baseline_mean) * 10_000,
            baseline_mean,
        )
        if change > 1_000_000_000:
            return "unsupported", None, None
        if change < profile.growth_threshold_basis_points:
            return "below-threshold", None, None
        excess = (current_mean - baseline_mean) * len(current)
        rate = next(iter(current_rates))
        amount = _half_up_divide(excess * rate, _MILLION)
        if excess > MAX_SAFE_INTEGER or amount > MAX_SAFE_INTEGER:
            return "unsupported", None, None

        finding, event = _build_finding(
            profile,
            baseline,
            current,
            evaluated_at=_format_time(now),
            baseline_mean=baseline_mean,
            current_mean=current_mean,
            change_basis_points=change,
            excess_quantity=excess,
            price_subunits_per_million_tokens=rate,
            amount_subunits=amount,
        )
        return "qualified", finding, event


class _UnsupportedCohort(ValueError):
    pass


class _UnresolvedCohort(ValueError):
    pass


def validate_context_growth_profile(document: object) -> ContextGrowthProfile:
    try:
        root = _closed(
            _json_copy(document),
            {
                "profileId",
                "tenantId",
                "catalogId",
                "costEngineVersion",
                "scope",
                "baselineWindow",
                "currentWindow",
                "minimumRequestsPerWindow",
                "growthThresholdBasisPoints",
                "maxRecordsPerWindow",
                "evaluationGraceSeconds",
            },
        )
        scope = _closed(
            root["scope"],
            {
                "provider",
                "modelId",
                "region",
                "serviceName",
                "deploymentEnvironment",
            },
        )
        baseline_start, baseline_end = _window(root["baselineWindow"])
        current_start, current_end = _window(root["currentWindow"])
        if (
            baseline_end != current_start
            or baseline_end - baseline_start != current_end - current_start
            or not timedelta(minutes=1)
            <= baseline_end - baseline_start
            <= timedelta(days=31)
        ):
            raise ValueError
        minimum = _integer(root["minimumRequestsPerWindow"], minimum=2, maximum=100)
        maximum = _integer(
            root["maxRecordsPerWindow"],
            minimum=minimum,
            maximum=MAX_COHORT_RECORDS,
        )
        threshold = _integer(
            root["growthThresholdBasisPoints"],
            minimum=1,
            maximum=1_000_000_000,
        )
        grace = _integer(
            root["evaluationGraceSeconds"], minimum=0, maximum=86_400
        )
        engine_version = _text(root["costEngineVersion"], maximum=64)
        if engine_version != COST_ENGINE_VERSION:
            raise ValueError
        return ContextGrowthProfile(
            _matched(root["profileId"], _PROFILE_ID),
            _matched(root["tenantId"], _TENANT_ID),
            _matched(root["catalogId"], _CATALOG_ID),
            engine_version,
            _matched(scope["provider"], _PROVIDER),
            _text(scope["modelId"], maximum=256),
            _matched(scope["region"], _REGION),
            _text(scope["serviceName"], maximum=256),
            _text(scope["deploymentEnvironment"], maximum=128),
            _format_time(baseline_start),
            _format_time(baseline_end),
            _format_time(current_start),
            _format_time(current_end),
            minimum,
            threshold,
            maximum,
            grace,
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        raise AiSavingsConfigurationError("ai.savings.profile.invalid") from None


def validate_ai_savings_finding(document: object) -> Mapping[str, object]:
    """Validate the closed finding shape and deterministic identity."""

    try:
        root = _closed(_json_copy(document), {"apiVersion", "kind", "metadata", "spec"})
        if root["apiVersion"] != "iip.platform/v1alpha1" or root["kind"] != "AiSavingsFinding":
            raise ValueError
        metadata = _closed(root["metadata"], {"id", "tenantId", "evaluatedAt"})
        spec = _closed(
            root["spec"],
            {
                "rule",
                "scope",
                "finding",
                "observations",
                "potentialSavings",
                "recommendation",
                "evidenceRefs",
            },
        )
        rule = _closed(spec["rule"], {"id", "version"})
        if rule != {"id": RULE_ID, "version": RULE_VERSION}:
            raise ValueError
        scope = _closed(
            spec["scope"],
            {
                "baselineWindow",
                "currentWindow",
                "provider",
                "modelId",
                "region",
                "serviceName",
                "deploymentEnvironment",
            },
        )
        baseline_start, baseline_end = _window(scope["baselineWindow"])
        current_start, current_end = _window(scope["currentWindow"])
        if (
            baseline_end != current_start
            or baseline_end - baseline_start != current_end - current_start
        ):
            raise ValueError
        _matched(scope["provider"], _PROVIDER)
        _text(scope["modelId"], maximum=256)
        _matched(scope["region"], _REGION)
        _text(scope["serviceName"], maximum=256)
        _text(scope["deploymentEnvironment"], maximum=128)
        finding = _closed(
            spec["finding"],
            {"category", "severity", "summary", "confidenceBasisPoints"},
        )
        if (
            finding["category"] != RULE_ID
            or finding["severity"] not in {"info", "low", "medium", "high"}
        ):
            raise ValueError
        _text(finding["summary"], maximum=1024)
        _integer(finding["confidenceBasisPoints"], minimum=0, maximum=10_000)
        observations = spec["observations"]
        if not isinstance(observations, list) or len(observations) != 1:
            raise ValueError
        observation = _closed(
            observations[0],
            {"metric", "unit", "baseline", "current", "changeBasisPoints"},
        )
        if (
            observation["metric"] != "input-tokens-per-request"
            or observation["unit"] != "tokens-per-request"
        ):
            raise ValueError
        for name in ("baseline", "current"):
            sample = _closed(observation[name], {"value", "sampleCount"})
            _integer(sample["value"], minimum=0, maximum=MAX_SAFE_INTEGER)
            _integer(sample["sampleCount"], minimum=1, maximum=MAX_COHORT_RECORDS)
        _integer(
            observation["changeBasisPoints"],
            minimum=-10_000,
            maximum=1_000_000_000,
        )
        savings = _closed(
            spec["potentialSavings"],
            {
                "status",
                "currency",
                "currencyScale",
                "amountSubunits",
                "period",
                "calculation",
                "costRecordRefs",
            },
        )
        if savings["status"] != "calculated" or savings["period"] != scope["currentWindow"]:
            raise ValueError
        currency = _text(savings["currency"], maximum=3)
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError
        if _integer(savings["currencyScale"], minimum=6, maximum=12) not in (6, 9, 12):
            raise ValueError
        _integer(savings["amountSubunits"], minimum=0, maximum=MAX_SAFE_INTEGER)
        calculation = _closed(
            savings["calculation"],
            {
                "method",
                "excessQuantity",
                "chargeCategory",
                "priceSubunitsPerMillionTokens",
            },
        )
        if (
            calculation["method"] != "avoidable-excess-at-observed-rate"
            or calculation["chargeCategory"] != "uncached-input-tokens"
        ):
            raise ValueError
        _integer(calculation["excessQuantity"], minimum=0, maximum=MAX_SAFE_INTEGER)
        _integer(
            calculation["priceSubunitsPerMillionTokens"],
            minimum=0,
            maximum=MAX_SAFE_INTEGER,
        )
        cost_refs = _id_list(savings["costRecordRefs"], _COST_ID, maximum=200)
        if cost_refs != sorted(cost_refs):
            raise ValueError
        recommendation = _closed(
            spec["recommendation"],
            {"actionCode", "summary", "requiresValidation"},
        )
        if (
            recommendation["actionCode"] != "review-context-retention"
            or recommendation["requiresValidation"] is not True
        ):
            raise ValueError
        _text(recommendation["summary"], maximum=1024)
        evidence = spec["evidenceRefs"]
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 256:
            raise ValueError
        pairs: list[tuple[str, str]] = []
        for raw_reference in evidence:
            reference = _closed(raw_reference, {"type", "id"})
            reference_type = reference["type"]
            pattern = {
                "ai-usage-record": _USAGE_ID,
                "ai-cost-record": _COST_ID,
            }.get(reference_type)
            if pattern is None:
                raise ValueError
            pairs.append((reference_type, _matched(reference["id"], pattern)))
        if len(set(pairs)) != len(pairs):
            raise ValueError
        finding_id = _matched(metadata["id"], _FINDING_ID)
        _matched(metadata["tenantId"], _TENANT_ID)
        evaluated_at, _canonical_evaluated_at = _timestamp(metadata["evaluatedAt"])
        if evaluated_at < current_end:
            raise ValueError
        if finding_id != "aif_" + _spec_digest(spec)[:32]:
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise InvalidAiSavingsInputError("ai.savings.finding.invalid") from None
    return root


def validate_context_growth_source_binding(
    finding_document: object,
    usage_documents: tuple[Mapping[str, object], ...],
    cost_documents: tuple[Mapping[str, object], ...],
) -> None:
    """Recalculate a finding from the exact immutable records it cites."""

    try:
        finding = validate_ai_savings_finding(finding_document)
        metadata = finding["metadata"]
        spec = finding["spec"]
        assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
        scope = spec["scope"]
        observation = spec["observations"][0]  # type: ignore[index]
        savings = spec["potentialSavings"]
        assert isinstance(scope, Mapping)
        assert isinstance(observation, Mapping)
        assert isinstance(savings, Mapping)
        tenant_id = metadata["tenantId"]
        usage_by_id: dict[str, Mapping[str, object]] = {}
        for document in usage_documents:
            validated = validate_ai_usage_for_economics(
                document, expected_tenant=str(tenant_id)
            )
            usage_id = validated["usage_record_id"]
            if not isinstance(usage_id, str) or usage_id in usage_by_id:
                raise ValueError
            usage_by_id[usage_id] = document
        cost_by_usage: dict[str, Mapping[str, object]] = {}
        cost_ids: list[str] = []
        for document in cost_documents:
            cost = validate_ai_cost_record(document)
            cost_metadata = cost["metadata"]
            cost_spec = cost["spec"]
            assert isinstance(cost_metadata, Mapping) and isinstance(cost_spec, Mapping)
            if cost_metadata["tenantId"] != tenant_id:
                raise ValueError
            usage_id = cost_spec["usageRecordId"]
            if not isinstance(usage_id, str) or usage_id in cost_by_usage:
                raise ValueError
            cost_by_usage[usage_id] = document
            cost_id = cost_metadata["id"]
            if not isinstance(cost_id, str):
                raise ValueError
            cost_ids.append(cost_id)
        expected_cost_refs = savings["costRecordRefs"]
        if sorted(cost_ids) != expected_cost_refs or set(cost_by_usage) != set(usage_by_id):
            raise ValueError

        catalog_ids: set[str] = set()
        engine_versions: set[str] = set()
        for document in cost_documents:
            cost_spec = document["spec"]
            if not isinstance(cost_spec, Mapping):
                raise ValueError
            calculation = cost_spec["calculation"]
            if not isinstance(calculation, Mapping):
                raise ValueError
            catalog_ids.add(str(calculation["catalogId"]))
            engine_versions.add(str(calculation["engineVersion"]))
        if len(catalog_ids) != 1 or len(engine_versions) != 1:
            raise ValueError
        profile = _profile_from_finding(
            finding,
            catalog_id=next(iter(catalog_ids)),
            cost_engine_version=next(iter(engine_versions)),
        )
        baseline: list[_CohortItem] = []
        current: list[_CohortItem] = []
        baseline_start = _parse_time(profile.baseline_start)
        baseline_end = _parse_time(profile.baseline_end)
        current_start = _parse_time(profile.current_start)
        current_end = _parse_time(profile.current_end)
        for usage_id, usage_document in usage_by_id.items():
            item = _cohort_item(profile, usage_document, cost_by_usage[usage_id])
            if baseline_start <= item.started_at < baseline_end:
                baseline.append(item)
            elif current_start <= item.started_at < current_end:
                current.append(item)
            else:
                raise ValueError
        baseline.sort(key=lambda item: (item.started_at, item.usage_id))
        current.sort(key=lambda item: (item.started_at, item.usage_id))
        baseline_sample = observation["baseline"]
        current_sample = observation["current"]
        assert isinstance(baseline_sample, Mapping) and isinstance(current_sample, Mapping)
        if (
            len(baseline) != baseline_sample["sampleCount"]
            or len(current) != current_sample["sampleCount"]
            or not baseline
            or not current
        ):
            raise ValueError
        baseline_mean = _half_up_divide(
            sum(item.input_tokens for item in baseline), len(baseline)
        )
        current_mean = _half_up_divide(
            sum(item.input_tokens for item in current), len(current)
        )
        if baseline_mean == 0 or current_mean <= baseline_mean:
            raise ValueError
        change = _half_up_divide(
            (current_mean - baseline_mean) * 10_000, baseline_mean
        )
        excess = (current_mean - baseline_mean) * len(current)
        rates = {item.uncached_input_rate for item in current}
        currencies = {(item.currency, item.currency_scale) for item in baseline + current}
        if len(rates) != 1 or len(currencies) != 1:
            raise ValueError
        rate = next(iter(rates))
        currency, currency_scale = next(iter(currencies))
        amount = _half_up_divide(excess * rate, _MILLION)
        if (
            baseline_sample["value"] != baseline_mean
            or current_sample["value"] != current_mean
            or observation["changeBasisPoints"] != change
            or savings["currency"] != currency
            or savings["currencyScale"] != currency_scale
            or savings["amountSubunits"] != amount
            or savings["calculation"]
            != {
                "method": "avoidable-excess-at-observed-rate",
                "excessQuantity": excess,
                "chargeCategory": "uncached-input-tokens",
                "priceSubunitsPerMillionTokens": rate,
            }
        ):
            raise ValueError
        expected_evidence = [
            {"type": "ai-usage-record", "id": item.usage_id}
            for item in baseline + current
        ] + [{"type": "ai-cost-record", "id": sorted(cost_ids)[0]}]
        if spec["evidenceRefs"] != expected_evidence:
            raise ValueError
        severity = _severity(change)
        confidence = _confidence(len(baseline), len(current))
        if spec["finding"] != _finding_summary(severity, confidence):
            raise ValueError
        if spec["recommendation"] != _recommendation():
            raise ValueError
    except (KeyError, TypeError, ValueError, InvalidAiCostInputError, InvalidAiSavingsInputError):
        raise InvalidAiSavingsInputError("ai.savings.sources.invalid") from None


def _cohort_item(
    profile: ContextGrowthProfile,
    usage_document: Mapping[str, object],
    cost_document: Mapping[str, object],
) -> _CohortItem:
    try:
        usage = validate_ai_usage_for_economics(
            usage_document, expected_tenant=profile.tenant_id
        )
        if (
            usage["provider"] != profile.provider
            or usage["model_id"] != profile.model_id
            or usage["region"] != profile.region
            or usage["service_name"] != profile.service_name
            or usage["deployment_environment"] != profile.deployment_environment
            or usage["outcome"] != "success"
            or usage["completeness"] != "complete"
            or usage["missing_fields"]
            or usage["cacheReadInputTokens"] != 0
            or usage["cacheWriteInputTokens"] != 0
            or not isinstance(usage["inputTokens"], int)
        ):
            raise _UnsupportedCohort
        cost = validate_ai_cost_record(cost_document)
        metadata = cost["metadata"]
        spec = cost["spec"]
        assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
        calculation = spec["calculation"]
        result = spec["result"]
        assert isinstance(calculation, Mapping) and isinstance(result, Mapping)
        if (
            metadata["tenantId"] != profile.tenant_id
            or spec["usageRecordId"] != usage["usage_record_id"]
            or calculation["catalogId"] != profile.catalog_id
            or calculation["engineVersion"] != profile.cost_engine_version
        ):
            raise ValueError
        if result["costStatus"] != "priced":
            raise _UnresolvedCohort
        lines = result["lines"]
        assert isinstance(lines, list)
        line = next(
            item
            for item in lines
            if isinstance(item, Mapping)
            and item.get("chargeCategory") == "uncached-input-tokens"
        )
        if (
            line["observedQuantity"] != usage["inputTokens"]
            or line["billableQuantity"] != usage["inputTokens"]
        ):
            raise ValueError
        started_at = usage["started_at"]
        if not isinstance(started_at, datetime):
            raise ValueError
        return _CohortItem(
            str(usage["usage_record_id"]),
            str(metadata["id"]),
            started_at,
            int(usage["inputTokens"]),
            str(result["currency"]),
            int(result["currencyScale"]),
            int(line["priceSubunitsPerMillionTokens"]),
        )
    except _UnsupportedCohort:
        raise
    except (KeyError, StopIteration, TypeError, ValueError, InvalidAiCostInputError):
        raise InvalidAiSavingsInputError("ai.savings.sources.invalid") from None


def _build_finding(
    profile: ContextGrowthProfile,
    baseline: tuple[_CohortItem, ...],
    current: tuple[_CohortItem, ...],
    *,
    evaluated_at: str,
    baseline_mean: int,
    current_mean: int,
    change_basis_points: int,
    excess_quantity: int,
    price_subunits_per_million_tokens: int,
    amount_subunits: int,
) -> tuple[Mapping[str, object], PlatformEvent]:
    currency = current[0].currency
    currency_scale = current[0].currency_scale
    severity = _severity(change_basis_points)
    confidence = _confidence(len(baseline), len(current))
    cost_refs = sorted(item.cost_id for item in baseline + current)
    evidence = [
        {"type": "ai-usage-record", "id": item.usage_id}
        for item in baseline + current
    ] + [{"type": "ai-cost-record", "id": cost_refs[0]}]
    scope = {
        "baselineWindow": {
            "start": profile.baseline_start,
            "end": profile.baseline_end,
        },
        "currentWindow": {
            "start": profile.current_start,
            "end": profile.current_end,
        },
        "provider": profile.provider,
        "modelId": profile.model_id,
        "region": profile.region,
        "serviceName": profile.service_name,
        "deploymentEnvironment": profile.deployment_environment,
    }
    spec: Mapping[str, object] = {
        "rule": {"id": RULE_ID, "version": RULE_VERSION},
        "scope": scope,
        "finding": _finding_summary(severity, confidence),
        "observations": [
            {
                "metric": "input-tokens-per-request",
                "unit": "tokens-per-request",
                "baseline": {
                    "value": baseline_mean,
                    "sampleCount": len(baseline),
                },
                "current": {
                    "value": current_mean,
                    "sampleCount": len(current),
                },
                "changeBasisPoints": change_basis_points,
            }
        ],
        "potentialSavings": {
            "status": "calculated",
            "currency": currency,
            "currencyScale": currency_scale,
            "amountSubunits": amount_subunits,
            "period": scope["currentWindow"],
            "calculation": {
                "method": "avoidable-excess-at-observed-rate",
                "excessQuantity": excess_quantity,
                "chargeCategory": "uncached-input-tokens",
                "priceSubunitsPerMillionTokens": price_subunits_per_million_tokens,
            },
            "costRecordRefs": cost_refs,
        },
        "recommendation": _recommendation(),
        "evidenceRefs": evidence,
    }
    digest = _spec_digest(spec)
    finding_id = "aif_" + digest[:32]
    document: Mapping[str, object] = {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "AiSavingsFinding",
        "metadata": {
            "id": finding_id,
            "tenantId": profile.tenant_id,
            "evaluatedAt": evaluated_at,
        },
        "spec": spec,
    }
    event = PlatformEvent(
        event_id="ai-savings-" + digest,
        event_type="io.iip.ai.savings-finding-recorded.v1",
        source=f"urn:iip:ai-savings:{RULE_ID}:{RULE_VERSION}",
        time=evaluated_at,
        subject=finding_id,
        tenant_id=profile.tenant_id,
        data={
            "findingId": finding_id,
            "ruleId": RULE_ID,
            "ruleVersion": RULE_VERSION,
            "category": RULE_ID,
            "severity": severity,
            "provider": profile.provider,
            "modelId": profile.model_id,
            "serviceName": profile.service_name,
        },
    )
    validate_ai_savings_finding(document)
    return document, event


def _profile_from_finding(
    finding: Mapping[str, object],
    *,
    catalog_id: str,
    cost_engine_version: str,
) -> ContextGrowthProfile:
    metadata = finding["metadata"]
    spec = finding["spec"]
    assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
    scope = spec["scope"]
    savings = spec["potentialSavings"]
    assert isinstance(scope, Mapping) and isinstance(savings, Mapping)
    baseline = scope["baselineWindow"]
    current = scope["currentWindow"]
    assert isinstance(baseline, Mapping) and isinstance(current, Mapping)
    return ContextGrowthProfile(
        "finding-source-validation",
        str(metadata["tenantId"]),
        catalog_id,
        cost_engine_version,
        str(scope["provider"]),
        str(scope["modelId"]),
        str(scope["region"]),
        str(scope["serviceName"]),
        str(scope["deploymentEnvironment"]),
        str(baseline["start"]),
        str(baseline["end"]),
        str(current["start"]),
        str(current["end"]),
        1,
        1,
        MAX_COHORT_RECORDS,
        0,
    )


def _cohort_query(
    profile: ContextGrowthProfile,
    *,
    baseline: bool,
) -> AiSavingsCohortQuery:
    return AiSavingsCohortQuery(
        provider=profile.provider,
        model_id=profile.model_id,
        region=profile.region,
        service_name=profile.service_name,
        deployment_environment=profile.deployment_environment,
        start=profile.baseline_start if baseline else profile.current_start,
        end=profile.baseline_end if baseline else profile.current_end,
        catalog_id=profile.catalog_id,
        engine_version=profile.cost_engine_version,
        limit=profile.max_records_per_window + 1,
    )


def _severity(change_basis_points: int) -> str:
    if change_basis_points >= 25_000:
        return "high"
    if change_basis_points >= 10_000:
        return "medium"
    return "low"


def _confidence(baseline_count: int, current_count: int) -> int:
    cohort = min(baseline_count, current_count)
    if cohort >= 100:
        return 9000
    if cohort >= 50:
        return 8000
    if cohort >= 20:
        return 7000
    return 6000


def _finding_summary(severity: str, confidence: int) -> Mapping[str, object]:
    return {
        "category": RULE_ID,
        "severity": severity,
        "summary": (
            "Mean input tokens per successful request exceeded the configured "
            "growth threshold against the preceding comparison window."
        ),
        "confidenceBasisPoints": confidence,
    }


def _recommendation() -> Mapping[str, object]:
    return {
        "actionCode": "review-context-retention",
        "summary": (
            "Review retained conversation context and retrieval payload size; "
            "validate quality before reducing either."
        ),
        "requiresValidation": True,
    }


def _savings_actor(tenant_id: object, worker_id: object) -> ActorContext:
    if (
        not isinstance(tenant_id, str)
        or not _TENANT_ID.fullmatch(tenant_id)
        or not isinstance(worker_id, str)
        or not _WORKER_ID.fullmatch(worker_id)
    ):
        raise AiSavingsConfigurationError("ai.savings.worker.invalid")
    return ActorContext(
        actor_id="ai-savings-worker:" + worker_id,
        tenant_id=tenant_id,
        roles=("ai-savings:evaluate",),
    )


def _spec_digest(spec: object) -> str:
    return hashlib.sha256(
        json.dumps(
            spec,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _window(value: object) -> tuple[datetime, datetime]:
    window = _closed(value, {"start", "end"})
    start = _parse_time(window["start"])
    end = _parse_time(window["end"])
    if start >= end:
        raise ValueError
    return start, end


def _closed(value: object, required: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != required:
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
    text = _text(value, maximum=1024)
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


def _id_list(value: object, pattern: re.Pattern[str], *, maximum: int) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError
    result = [_matched(item, pattern) for item in value]
    if len(set(result)) != len(result):
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


def _half_up_divide(numerator: int, denominator: int) -> int:
    if denominator <= 0 or numerator < 0:
        raise ValueError
    return (numerator + denominator // 2) // denominator


__all__ = [
    "AiSavingsConfigurationError",
    "AiSavingsEvaluationPass",
    "AiSavingsEvaluationService",
    "ContextGrowthProfile",
    "InvalidAiSavingsInputError",
    "MAX_COHORT_RECORDS",
    "RULE_ID",
    "RULE_VERSION",
    "validate_ai_savings_finding",
    "validate_context_growth_profile",
    "validate_context_growth_source_binding",
]
