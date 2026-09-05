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
    AiEconomicsMeasurement,
    AiEconomicsLedger,
    AiEconomicsTelemetrySink,
    AiModelSavingsMeasurement,
    AiRetryMeasurement,
    AiSavingsCohortQuery,
    Clock,
)
from iip.application.validate_ai_model_suitability import (
    InvalidAiModelSuitabilityReportError,
    validate_ai_model_suitability_report,
)
from iip.domain.models import PlatformEvent


CONTEXT_GROWTH_RULE_ID = "context-growth"
RETRY_AMPLIFICATION_RULE_ID = "retry-amplification"
EXPENSIVE_MODEL_RULE_ID = "expensive-model-anomaly"
RULE_ID = CONTEXT_GROWTH_RULE_ID
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
_SUITABILITY_ID = re.compile(r"ams_[a-f0-9]{32}")
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
class RetryAmplificationProfile:
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
    retry_rate_increase_threshold_basis_points: int
    minimum_current_retry_rate_basis_points: int
    max_records_per_window: int
    evaluation_grace_seconds: int


@dataclass(frozen=True)
class ExpensiveModelProfile:
    profile_id: str
    tenant_id: str
    catalog_id: str
    cost_engine_version: str
    provider: str
    model_id: str
    candidate_model_id: str
    region: str
    service_name: str
    deployment_environment: str
    baseline_start: str
    baseline_end: str
    current_start: str
    current_end: str
    minimum_requests: int
    cost_increase_threshold_basis_points: int
    max_records_per_window: int
    evaluation_grace_seconds: int
    suitability_report_id: str
    suitability_evaluated_at: str
    suitability_valid_until: str
    suitability_report: Mapping[str, object]


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
class _EvaluationOutcome:
    status: str
    finding: Mapping[str, object] | None
    event: PlatformEvent | None
    measurement: (
        AiEconomicsMeasurement
        | AiRetryMeasurement
        | AiModelSavingsMeasurement
        | None
    )


@dataclass(frozen=True)
class _CohortItem:
    usage_id: str
    cost_id: str
    started_at: datetime
    input_tokens: int
    currency: str
    currency_scale: int
    uncached_input_rate: int


@dataclass(frozen=True)
class _RetryCohortItem:
    usage_id: str
    started_at: datetime
    retry_count: int


@dataclass(frozen=True)
class _ModelCostCohortItem:
    usage_id: str
    cost_id: str
    started_at: datetime
    total_cost_subunits: int
    currency: str
    currency_scale: int


class AiSavingsEvaluationService:
    """Evaluate fixed comparison windows outside the inference request path."""

    def __init__(
        self,
        ledger: AiEconomicsLedger,
        clock: Clock,
        profiles: tuple[Mapping[str, object], ...],
        telemetry_sink: AiEconomicsTelemetrySink | None = None,
        *,
        allow_test_fixtures: bool = False,
    ) -> None:
        if (
            not isinstance(profiles, tuple)
            or not profiles
            or len(profiles) > 1000
        ):
            raise AiSavingsConfigurationError(
                "ai.savings.configuration.invalid"
            )
        if not isinstance(allow_test_fixtures, bool):
            raise AiSavingsConfigurationError("ai.savings.configuration.invalid")
        validated = tuple(
            validate_ai_savings_profile(
                item,
                allow_test_fixtures=allow_test_fixtures,
            )
            for item in profiles
        )
        identities = tuple((item.tenant_id, item.profile_id) for item in validated)
        if len(set(identities)) != len(identities):
            raise AiSavingsConfigurationError("ai.savings.profile.ambiguous")
        self._ledger = ledger
        self._clock = clock
        self._telemetry_sink = telemetry_sink
        self._allow_test_fixtures = allow_test_fixtures
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
                if isinstance(profile, ExpensiveModelProfile):
                    self._ledger.register_ai_model_suitability_report(
                        actor,
                        profile.suitability_report,
                        allow_test_fixtures=self._allow_test_fixtures,
                    )
                outcome = self._evaluate(profile, actor, now)
                counts[outcome.status] += 1
                if outcome.finding is not None and outcome.event is not None:
                    self._ledger.commit_ai_savings_batch(
                        actor,
                        (outcome.finding,),
                        (outcome.event,),
                    )
                if outcome.measurement is not None:
                    self._record_telemetry(outcome.measurement)
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
        profile: (
            ContextGrowthProfile
            | RetryAmplificationProfile
            | ExpensiveModelProfile
        ),
        actor: ActorContext,
        now: datetime,
    ) -> _EvaluationOutcome:
        if isinstance(profile, RetryAmplificationProfile):
            return self._evaluate_retry_amplification(profile, actor, now)
        if isinstance(profile, ExpensiveModelProfile):
            return self._evaluate_expensive_model(profile, actor, now)
        return self._evaluate_context_growth(profile, actor, now)

    def _evaluate_context_growth(
        self,
        profile: ContextGrowthProfile,
        actor: ActorContext,
        now: datetime,
    ) -> _EvaluationOutcome:
        current_end = _parse_time(profile.current_end)
        if now < current_end + timedelta(seconds=profile.evaluation_grace_seconds):
            return _EvaluationOutcome("pending", None, None, None)
        baseline_rows = self._ledger.list_ai_savings_cohort(
            actor,
            _cohort_query(profile, baseline=True),
        )
        current_rows = self._ledger.list_ai_savings_cohort(
            actor,
            _cohort_query(profile, baseline=False),
        )

        def outcome(
            status: str,
            finding: Mapping[str, object] | None = None,
            event: PlatformEvent | None = None,
        ) -> _EvaluationOutcome:
            return _EvaluationOutcome(
                status,
                finding,
                event,
                _ai_economics_measurement(
                    profile,
                    baseline_rows,
                    current_rows,
                    evaluation_status=status,
                    finding=finding,
                ),
            )

        if (
            len(baseline_rows) > profile.max_records_per_window
            or len(current_rows) > profile.max_records_per_window
        ):
            # The repository deliberately returns max + 1 as an overflow
            # sentinel. Never export a partial aggregate as the window total.
            return _EvaluationOutcome("unsupported", None, None, None)
        if (
            len(baseline_rows) < profile.minimum_requests
            or len(current_rows) < profile.minimum_requests
        ):
            return outcome("insufficient")
        if any(cost is None for _usage, cost in baseline_rows + current_rows):
            return outcome("unresolved")
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
            return outcome("unresolved")
        except _UnsupportedCohort:
            return outcome("unsupported")
        if not baseline or not current:
            return outcome("insufficient")
        if len({(item.currency, item.currency_scale) for item in baseline + current}) != 1:
            return outcome("unresolved")
        current_rates = {item.uncached_input_rate for item in current}
        if len(current_rates) != 1:
            return outcome("unresolved")

        baseline_mean = _half_up_divide(
            sum(item.input_tokens for item in baseline), len(baseline)
        )
        current_mean = _half_up_divide(
            sum(item.input_tokens for item in current), len(current)
        )
        if baseline_mean == 0 or current_mean <= baseline_mean:
            return outcome("below-threshold")
        change = _half_up_divide(
            (current_mean - baseline_mean) * 10_000,
            baseline_mean,
        )
        if change > 1_000_000_000:
            return outcome("unsupported")
        if change < profile.growth_threshold_basis_points:
            return outcome("below-threshold")
        excess = (current_mean - baseline_mean) * len(current)
        rate = next(iter(current_rates))
        amount = _half_up_divide(excess * rate, _MILLION)
        if excess > MAX_SAFE_INTEGER or amount > MAX_SAFE_INTEGER:
            return outcome("unsupported")

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
        return outcome("qualified", finding, event)

    def _evaluate_retry_amplification(
        self,
        profile: RetryAmplificationProfile,
        actor: ActorContext,
        now: datetime,
    ) -> _EvaluationOutcome:
        current_end = _parse_time(profile.current_end)
        if now < current_end + timedelta(seconds=profile.evaluation_grace_seconds):
            return _EvaluationOutcome("pending", None, None, None)
        baseline_rows = self._ledger.list_ai_savings_cohort(
            actor,
            _cohort_query(profile, baseline=True),
        )
        current_rows = self._ledger.list_ai_savings_cohort(
            actor,
            _cohort_query(profile, baseline=False),
        )

        def outcome(
            status: str,
            finding: Mapping[str, object] | None = None,
            event: PlatformEvent | None = None,
        ) -> _EvaluationOutcome:
            return _EvaluationOutcome(
                status,
                finding,
                event,
                _retry_measurement(
                    profile,
                    baseline_rows,
                    current_rows,
                    evaluation_status=status,
                    finding=finding,
                ),
            )

        if (
            len(baseline_rows) > profile.max_records_per_window
            or len(current_rows) > profile.max_records_per_window
        ):
            return _EvaluationOutcome("unsupported", None, None, None)
        if (
            len(baseline_rows) < profile.minimum_requests
            or len(current_rows) < profile.minimum_requests
        ):
            return outcome("insufficient")
        try:
            baseline = tuple(
                _retry_cohort_item(profile, usage) for usage, _cost in baseline_rows
            )
            current = tuple(
                _retry_cohort_item(profile, usage) for usage, _cost in current_rows
            )
        except _UnsupportedCohort:
            return outcome("unsupported")
        if not baseline or not current:
            return outcome("insufficient")
        baseline_rate = _retry_rate_basis_points(baseline)
        current_rate = _retry_rate_basis_points(current)
        increase = current_rate - baseline_rate
        if (
            current_rate < profile.minimum_current_retry_rate_basis_points
            or increase < profile.retry_rate_increase_threshold_basis_points
        ):
            return outcome("below-threshold")
        finding, event = _build_retry_finding(
            profile,
            baseline,
            current,
            evaluated_at=_format_time(now),
            baseline_rate_basis_points=baseline_rate,
            current_rate_basis_points=current_rate,
            increase_basis_points=increase,
        )
        return outcome("qualified", finding, event)

    def _evaluate_expensive_model(
        self,
        profile: ExpensiveModelProfile,
        actor: ActorContext,
        now: datetime,
    ) -> _EvaluationOutcome:
        current_end = _parse_time(profile.current_end)
        if now < current_end + timedelta(seconds=profile.evaluation_grace_seconds):
            return _EvaluationOutcome("pending", None, None, None)
        if not (
            _parse_time(profile.suitability_evaluated_at)
            <= now
            < _parse_time(profile.suitability_valid_until)
        ):
            return _EvaluationOutcome(
                "unresolved",
                None,
                None,
                _model_savings_measurement(
                    profile,
                    (),
                    (),
                    evaluation_status="unresolved",
                    finding=None,
                ),
            )
        candidate_rows = self._ledger.list_ai_savings_cohort(
            actor,
            _cohort_query(profile, baseline=True),
        )
        reference_rows = self._ledger.list_ai_savings_cohort(
            actor,
            _cohort_query(profile, baseline=False),
        )

        def outcome(
            status: str,
            finding: Mapping[str, object] | None = None,
            event: PlatformEvent | None = None,
        ) -> _EvaluationOutcome:
            return _EvaluationOutcome(
                status,
                finding,
                event,
                _model_savings_measurement(
                    profile,
                    candidate_rows,
                    reference_rows,
                    evaluation_status=status,
                    finding=finding,
                ),
            )

        if (
            len(candidate_rows) > profile.max_records_per_window
            or len(reference_rows) > profile.max_records_per_window
        ):
            return _EvaluationOutcome("unsupported", None, None, None)
        if (
            len(candidate_rows) < profile.minimum_requests
            or len(reference_rows) < profile.minimum_requests
        ):
            return outcome("insufficient")
        if any(cost is None for _usage, cost in candidate_rows + reference_rows):
            return outcome("unresolved")
        try:
            candidate = tuple(
                _model_cost_cohort_item(
                    profile,
                    usage,
                    cost,
                    expected_model_id=profile.candidate_model_id,
                )
                for usage, cost in candidate_rows
                if cost is not None
            )
            reference = tuple(
                _model_cost_cohort_item(
                    profile,
                    usage,
                    cost,
                    expected_model_id=profile.model_id,
                )
                for usage, cost in reference_rows
                if cost is not None
            )
        except _UnresolvedCohort:
            return outcome("unresolved")
        except _UnsupportedCohort:
            return outcome("unsupported")
        if not candidate or not reference:
            return outcome("insufficient")
        if len(
            {
                (item.currency, item.currency_scale)
                for item in candidate + reference
            }
        ) != 1:
            return outcome("unresolved")
        candidate_mean = _half_up_divide(
            sum(item.total_cost_subunits for item in candidate),
            len(candidate),
        )
        reference_mean = _half_up_divide(
            sum(item.total_cost_subunits for item in reference),
            len(reference),
        )
        if candidate_mean == 0:
            return outcome("unsupported")
        if reference_mean <= candidate_mean:
            return outcome("below-threshold")
        increase = _half_up_divide(
            (reference_mean - candidate_mean) * 10_000,
            candidate_mean,
        )
        if increase > 1_000_000_000:
            return outcome("unsupported")
        if increase < profile.cost_increase_threshold_basis_points:
            return outcome("below-threshold")
        amount = (reference_mean - candidate_mean) * len(reference)
        if amount > MAX_SAFE_INTEGER:
            return outcome("unsupported")
        finding, event = _build_expensive_model_finding(
            profile,
            candidate,
            reference,
            evaluated_at=_format_time(now),
            candidate_cost_per_request=candidate_mean,
            reference_cost_per_request=reference_mean,
            increase_basis_points=increase,
            amount_subunits=amount,
        )
        return outcome("qualified", finding, event)

    def _record_telemetry(
        self,
        measurement: (
            AiEconomicsMeasurement
            | AiRetryMeasurement
            | AiModelSavingsMeasurement
        ),
    ) -> None:
        if self._telemetry_sink is None:
            return
        try:
            if isinstance(measurement, AiModelSavingsMeasurement):
                self._telemetry_sink.record_ai_model_savings(measurement)
            elif isinstance(measurement, AiRetryMeasurement):
                self._telemetry_sink.record_ai_retry(measurement)
            else:
                self._telemetry_sink.record_ai_economics(measurement)
        except Exception:
            # Observability cannot change persisted accounting or rule outcomes.
            return


class _UnsupportedCohort(ValueError):
    pass


class _UnresolvedCohort(ValueError):
    pass


def validate_context_growth_profile(document: object) -> ContextGrowthProfile:
    try:
        copied = _json_copy(document)
        root = _closed(
            copied,
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
            {"ruleId"},
        )
        if root.get("ruleId", CONTEXT_GROWTH_RULE_ID) != CONTEXT_GROWTH_RULE_ID:
            raise ValueError
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


def validate_retry_amplification_profile(
    document: object,
) -> RetryAmplificationProfile:
    try:
        root = _closed(
            _json_copy(document),
            {
                "profileId",
                "ruleId",
                "tenantId",
                "catalogId",
                "costEngineVersion",
                "scope",
                "baselineWindow",
                "currentWindow",
                "minimumRequestsPerWindow",
                "retryRateIncreaseThresholdBasisPoints",
                "minimumCurrentRetryRateBasisPoints",
                "maxRecordsPerWindow",
                "evaluationGraceSeconds",
            },
        )
        if root["ruleId"] != RETRY_AMPLIFICATION_RULE_ID:
            raise ValueError
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
        increase_threshold = _integer(
            root["retryRateIncreaseThresholdBasisPoints"],
            minimum=1,
            maximum=10_000,
        )
        minimum_current_rate = _integer(
            root["minimumCurrentRetryRateBasisPoints"],
            minimum=1,
            maximum=10_000,
        )
        grace = _integer(
            root["evaluationGraceSeconds"], minimum=0, maximum=86_400
        )
        engine_version = _text(root["costEngineVersion"], maximum=64)
        if engine_version != COST_ENGINE_VERSION:
            raise ValueError
        return RetryAmplificationProfile(
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
            increase_threshold,
            minimum_current_rate,
            maximum,
            grace,
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        raise AiSavingsConfigurationError("ai.savings.profile.invalid") from None


def validate_expensive_model_profile(
    document: object,
    *,
    allow_test_fixtures: bool = False,
) -> ExpensiveModelProfile:
    try:
        root = _closed(
            _json_copy(document),
            {
                "profileId",
                "ruleId",
                "tenantId",
                "catalogId",
                "costEngineVersion",
                "scope",
                "baselineWindow",
                "currentWindow",
                "minimumRequestsPerWindow",
                "costIncreaseThresholdBasisPoints",
                "maxRecordsPerWindow",
                "evaluationGraceSeconds",
                "modelSuitabilityReport",
            },
        )
        if root["ruleId"] != EXPENSIVE_MODEL_RULE_ID:
            raise ValueError
        scope = _closed(
            root["scope"],
            {
                "provider",
                "modelId",
                "candidateModelId",
                "region",
                "serviceName",
                "deploymentEnvironment",
            },
        )
        tenant_id = _matched(root["tenantId"], _TENANT_ID)
        provider = _matched(scope["provider"], _PROVIDER)
        model_id = _text(scope["modelId"], maximum=256)
        candidate_model_id = _text(scope["candidateModelId"], maximum=256)
        if model_id == candidate_model_id:
            raise ValueError
        region = _matched(scope["region"], _REGION)
        service_name = _text(scope["serviceName"], maximum=256)
        deployment_environment = _text(
            scope["deploymentEnvironment"],
            maximum=128,
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
            root["costIncreaseThresholdBasisPoints"],
            minimum=1,
            maximum=1_000_000_000,
        )
        grace = _integer(
            root["evaluationGraceSeconds"],
            minimum=0,
            maximum=86_400,
        )
        engine_version = _text(root["costEngineVersion"], maximum=64)
        if engine_version != COST_ENGINE_VERSION:
            raise ValueError
        report = validate_ai_model_suitability_report(
            root["modelSuitabilityReport"],
            allow_test_fixtures=allow_test_fixtures,
        )
        if (
            report.tenant_id != tenant_id
            or report.provider != provider
            or report.reference_model_id != model_id
            or report.candidate_model_id != candidate_model_id
            or report.region != region
            or report.service_name != service_name
            or report.deployment_environment != deployment_environment
            or report.valid_until
            <= current_end + timedelta(seconds=grace)
        ):
            raise ValueError
        return ExpensiveModelProfile(
            _matched(root["profileId"], _PROFILE_ID),
            tenant_id,
            _matched(root["catalogId"], _CATALOG_ID),
            engine_version,
            provider,
            model_id,
            candidate_model_id,
            region,
            service_name,
            deployment_environment,
            _format_time(baseline_start),
            _format_time(baseline_end),
            _format_time(current_start),
            _format_time(current_end),
            minimum,
            threshold,
            maximum,
            grace,
            report.report_id,
            _format_time(report.evaluated_at),
            _format_time(report.valid_until),
            report.document,
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        InvalidAiModelSuitabilityReportError,
    ):
        raise AiSavingsConfigurationError("ai.savings.profile.invalid") from None


def validate_ai_savings_profile(
    document: object,
    *,
    allow_test_fixtures: bool = False,
) -> ContextGrowthProfile | RetryAmplificationProfile | ExpensiveModelProfile:
    if (
        isinstance(document, Mapping)
        and document.get("ruleId") == RETRY_AMPLIFICATION_RULE_ID
    ):
        return validate_retry_amplification_profile(document)
    if (
        isinstance(document, Mapping)
        and document.get("ruleId") == EXPENSIVE_MODEL_RULE_ID
    ):
        return validate_expensive_model_profile(
            document,
            allow_test_fixtures=allow_test_fixtures,
        )
    return validate_context_growth_profile(document)


def _validate_context_growth_finding(document: object) -> Mapping[str, object]:
    """Validate the closed context-growth finding shape and identity."""

    try:
        root = _closed(
            _json_copy(document),
            {"apiVersion", "kind", "metadata", "spec"},
        )
        if (
            root["apiVersion"] != "iip.platform/v1alpha1"
            or root["kind"] != "AiSavingsFinding"
        ):
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
        if (
            savings["status"] != "calculated"
            or savings["period"] != scope["currentWindow"]
        ):
            raise ValueError
        currency = _text(savings["currency"], maximum=3)
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError
        if _integer(
            savings["currencyScale"],
            minimum=6,
            maximum=12,
        ) not in (6, 9, 12):
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


def _validate_retry_amplification_finding(
    document: object,
) -> Mapping[str, object]:
    try:
        root = _closed(
            _json_copy(document),
            {"apiVersion", "kind", "metadata", "spec"},
        )
        if (
            root["apiVersion"] != "iip.platform/v1alpha1"
            or root["kind"] != "AiSavingsFinding"
        ):
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
        if rule != {"id": RETRY_AMPLIFICATION_RULE_ID, "version": RULE_VERSION}:
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
            finding["category"] != RETRY_AMPLIFICATION_RULE_ID
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
            observation["metric"] != "retrying-operations-rate"
            or observation["unit"] != "basis-points"
        ):
            raise ValueError
        for name in ("baseline", "current"):
            sample = _closed(observation[name], {"value", "sampleCount"})
            _integer(sample["value"], minimum=0, maximum=10_000)
            _integer(sample["sampleCount"], minimum=1, maximum=MAX_COHORT_RECORDS)
        _integer(
            observation["changeBasisPoints"],
            minimum=-10_000,
            maximum=10_000,
        )
        savings = _closed(
            spec["potentialSavings"],
            {"status", "reasonCode", "period"},
        )
        if (
            savings["status"] != "unresolved"
            or savings["reasonCode"] != "retry-billing-unproven"
            or savings["period"] != scope["currentWindow"]
        ):
            raise ValueError
        _window(savings["period"])
        recommendation = _closed(
            spec["recommendation"],
            {"actionCode", "summary", "requiresValidation"},
        )
        if (
            recommendation["actionCode"] != "review-retry-policy"
            or recommendation["requiresValidation"] is not True
        ):
            raise ValueError
        _text(recommendation["summary"], maximum=1024)
        evidence = spec["evidenceRefs"]
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 200:
            raise ValueError
        usage_ids: list[str] = []
        for raw_reference in evidence:
            reference = _closed(raw_reference, {"type", "id"})
            if reference["type"] != "ai-usage-record":
                raise ValueError
            usage_ids.append(_matched(reference["id"], _USAGE_ID))
        if usage_ids != sorted(usage_ids) or len(set(usage_ids)) != len(usage_ids):
            raise ValueError
        finding_id = _matched(metadata["id"], _FINDING_ID)
        _matched(metadata["tenantId"], _TENANT_ID)
        evaluated_at, _canonical_evaluated_at = _timestamp(metadata["evaluatedAt"])
        if (
            evaluated_at < current_end
            or finding_id != "aif_" + _spec_digest(spec)[:32]
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise InvalidAiSavingsInputError("ai.savings.finding.invalid") from None
    return root


def _validate_expensive_model_finding(
    document: object,
) -> Mapping[str, object]:
    try:
        root = _closed(
            _json_copy(document),
            {"apiVersion", "kind", "metadata", "spec"},
        )
        if (
            root["apiVersion"] != "iip.platform/v1alpha1"
            or root["kind"] != "AiSavingsFinding"
        ):
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
        if rule != {"id": EXPENSIVE_MODEL_RULE_ID, "version": RULE_VERSION}:
            raise ValueError
        scope = _closed(
            spec["scope"],
            {
                "baselineWindow",
                "currentWindow",
                "provider",
                "modelId",
                "candidateModelId",
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
        model_id = _text(scope["modelId"], maximum=256)
        candidate_model_id = _text(scope["candidateModelId"], maximum=256)
        if model_id == candidate_model_id:
            raise ValueError
        _matched(scope["region"], _REGION)
        _text(scope["serviceName"], maximum=256)
        _text(scope["deploymentEnvironment"], maximum=128)
        finding = _closed(
            spec["finding"],
            {"category", "severity", "summary", "confidenceBasisPoints"},
        )
        if (
            finding["category"] != EXPENSIVE_MODEL_RULE_ID
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
            observation["metric"] != "calculated-cost-per-request"
            or observation["unit"] != "currency-subunits-per-request"
        ):
            raise ValueError
        for name in ("baseline", "current"):
            sample = _closed(observation[name], {"value", "sampleCount"})
            _integer(sample["value"], minimum=0, maximum=MAX_SAFE_INTEGER)
            _integer(sample["sampleCount"], minimum=1, maximum=MAX_COHORT_RECORDS)
        _integer(
            observation["changeBasisPoints"],
            minimum=1,
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
        if (
            savings["status"] != "calculated"
            or savings["period"] != scope["currentWindow"]
        ):
            raise ValueError
        currency = _text(savings["currency"], maximum=3)
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError
        if _integer(savings["currencyScale"], minimum=6, maximum=12) not in (
            6,
            9,
            12,
        ):
            raise ValueError
        _integer(savings["amountSubunits"], minimum=0, maximum=MAX_SAFE_INTEGER)
        calculation = _closed(
            savings["calculation"],
            {
                "method",
                "candidateCostPerRequestSubunits",
                "referenceCostPerRequestSubunits",
                "referenceRequestCount",
                "suitabilityReportId",
            },
        )
        if calculation["method"] != "qualified-model-cost-difference":
            raise ValueError
        _integer(
            calculation["candidateCostPerRequestSubunits"],
            minimum=0,
            maximum=MAX_SAFE_INTEGER,
        )
        _integer(
            calculation["referenceCostPerRequestSubunits"],
            minimum=0,
            maximum=MAX_SAFE_INTEGER,
        )
        _integer(
            calculation["referenceRequestCount"],
            minimum=1,
            maximum=MAX_COHORT_RECORDS,
        )
        report_id = _matched(calculation["suitabilityReportId"], _SUITABILITY_ID)
        cost_refs = _id_list(savings["costRecordRefs"], _COST_ID, maximum=200)
        if cost_refs != sorted(cost_refs):
            raise ValueError
        recommendation = _closed(
            spec["recommendation"],
            {"actionCode", "summary", "requiresValidation"},
        )
        if (
            recommendation["actionCode"] != "evaluate-lower-cost-model"
            or recommendation["requiresValidation"] is not True
        ):
            raise ValueError
        _text(recommendation["summary"], maximum=1024)
        evidence = spec["evidenceRefs"]
        if not isinstance(evidence, list) or not 2 <= len(evidence) <= 201:
            raise ValueError
        pairs: list[tuple[str, str]] = []
        for raw_reference in evidence:
            reference = _closed(raw_reference, {"type", "id"})
            reference_type = reference["type"]
            pattern = {
                "ai-usage-record": _USAGE_ID,
                "ai-model-suitability-report": _SUITABILITY_ID,
            }.get(reference_type)
            if pattern is None:
                raise ValueError
            pairs.append((str(reference_type), _matched(reference["id"], pattern)))
        if (
            len(set(pairs)) != len(pairs)
            or [item for item in pairs if item[0] == "ai-model-suitability-report"]
            != [("ai-model-suitability-report", report_id)]
        ):
            raise ValueError
        usage_pairs = [item for item in pairs if item[0] == "ai-usage-record"]
        if usage_pairs != sorted(usage_pairs):
            raise ValueError
        finding_id = _matched(metadata["id"], _FINDING_ID)
        _matched(metadata["tenantId"], _TENANT_ID)
        evaluated_at, _canonical_evaluated_at = _timestamp(metadata["evaluatedAt"])
        if (
            evaluated_at < current_end
            or finding_id != "aif_" + _spec_digest(spec)[:32]
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise InvalidAiSavingsInputError("ai.savings.finding.invalid") from None
    return root


def validate_ai_savings_finding(document: object) -> Mapping[str, object]:
    """Validate a supported closed finding shape and deterministic identity."""

    try:
        if not isinstance(document, Mapping):
            raise ValueError
        spec = document.get("spec")
        rule = spec.get("rule") if isinstance(spec, Mapping) else None
        rule_id = rule.get("id") if isinstance(rule, Mapping) else None
    except (TypeError, ValueError):
        raise InvalidAiSavingsInputError("ai.savings.finding.invalid") from None
    if rule_id == CONTEXT_GROWTH_RULE_ID:
        return _validate_context_growth_finding(document)
    if rule_id == RETRY_AMPLIFICATION_RULE_ID:
        return _validate_retry_amplification_finding(document)
    if rule_id == EXPENSIVE_MODEL_RULE_ID:
        return _validate_expensive_model_finding(document)
    raise InvalidAiSavingsInputError("ai.savings.finding.invalid")


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
            assert isinstance(cost_metadata, Mapping) and isinstance(
                cost_spec,
                Mapping,
            )
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
        if sorted(cost_ids) != expected_cost_refs or set(cost_by_usage) != set(
            usage_by_id
        ):
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
        assert isinstance(baseline_sample, Mapping) and isinstance(
            current_sample,
            Mapping,
        )
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
        currencies = {
            (item.currency, item.currency_scale) for item in baseline + current
        }
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
    except (
        KeyError,
        TypeError,
        ValueError,
        InvalidAiCostInputError,
        InvalidAiSavingsInputError,
    ):
        raise InvalidAiSavingsInputError("ai.savings.sources.invalid") from None


def validate_retry_amplification_source_binding(
    finding_document: object,
    usage_documents: tuple[Mapping[str, object], ...],
    cost_documents: tuple[Mapping[str, object], ...],
) -> None:
    """Recalculate retry evidence without inferring a monetary amount."""

    try:
        finding = _validate_retry_amplification_finding(finding_document)
        if cost_documents:
            raise ValueError
        metadata = finding["metadata"]
        spec = finding["spec"]
        assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
        scope = spec["scope"]
        observation = spec["observations"][0]  # type: ignore[index]
        assert isinstance(scope, Mapping) and isinstance(observation, Mapping)
        profile = _retry_profile_from_finding(finding)
        usage_by_id: dict[str, Mapping[str, object]] = {}
        for document in usage_documents:
            usage = validate_ai_usage_for_economics(
                document,
                expected_tenant=str(metadata["tenantId"]),
            )
            usage_id = usage["usage_record_id"]
            if not isinstance(usage_id, str) or usage_id in usage_by_id:
                raise ValueError
            usage_by_id[usage_id] = document
        evidence_ids = [
            reference["id"]
            for reference in spec["evidenceRefs"]  # type: ignore[union-attr]
            if isinstance(reference, Mapping)
        ]
        if sorted(usage_by_id) != evidence_ids:
            raise ValueError
        baseline: list[_RetryCohortItem] = []
        current: list[_RetryCohortItem] = []
        baseline_start = _parse_time(profile.baseline_start)
        baseline_end = _parse_time(profile.baseline_end)
        current_start = _parse_time(profile.current_start)
        current_end = _parse_time(profile.current_end)
        for document in usage_by_id.values():
            item = _retry_cohort_item(profile, document)
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
        assert isinstance(baseline_sample, Mapping) and isinstance(
            current_sample,
            Mapping,
        )
        if not baseline or not current:
            raise ValueError
        baseline_rate = _retry_rate_basis_points(tuple(baseline))
        current_rate = _retry_rate_basis_points(tuple(current))
        increase = current_rate - baseline_rate
        if (
            increase <= 0
            or current_rate <= 0
            or baseline_sample
            != {"value": baseline_rate, "sampleCount": len(baseline)}
            or current_sample
            != {"value": current_rate, "sampleCount": len(current)}
            or observation["changeBasisPoints"] != increase
            or spec["finding"]
            != _retry_finding_summary(
                _retry_severity(increase),
                _confidence(len(baseline), len(current)),
            )
            or spec["recommendation"] != _retry_recommendation()
        ):
            raise ValueError
        expected_savings = {
            "status": "unresolved",
            "reasonCode": "retry-billing-unproven",
            "period": scope["currentWindow"],
        }
        if spec["potentialSavings"] != expected_savings:
            raise ValueError
    except (
        AssertionError,
        KeyError,
        TypeError,
        ValueError,
        InvalidAiCostInputError,
        InvalidAiSavingsInputError,
    ):
        raise InvalidAiSavingsInputError("ai.savings.sources.invalid") from None


def validate_expensive_model_source_binding(
    finding_document: object,
    usage_documents: tuple[Mapping[str, object], ...],
    cost_documents: tuple[Mapping[str, object], ...],
    suitability_documents: tuple[Mapping[str, object], ...],
) -> None:
    """Recalculate a model recommendation from every immutable cited fact."""

    try:
        finding = _validate_expensive_model_finding(finding_document)
        if len(suitability_documents) != 1:
            raise ValueError
        metadata = finding["metadata"]
        spec = finding["spec"]
        assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
        scope = spec["scope"]
        observation = spec["observations"][0]  # type: ignore[index]
        savings = spec["potentialSavings"]
        assert isinstance(scope, Mapping)
        assert isinstance(observation, Mapping)
        assert isinstance(savings, Mapping)
        report = validate_ai_model_suitability_report(
            suitability_documents[0],
            allow_test_fixtures=True,
        )
        evaluated_at = _parse_time(metadata["evaluatedAt"])
        calculation = savings["calculation"]
        assert isinstance(calculation, Mapping)
        if (
            report.report_id != calculation["suitabilityReportId"]
            or report.tenant_id != metadata["tenantId"]
            or report.provider != scope["provider"]
            or report.reference_model_id != scope["modelId"]
            or report.candidate_model_id != scope["candidateModelId"]
            or report.region != scope["region"]
            or report.service_name != scope["serviceName"]
            or report.deployment_environment != scope["deploymentEnvironment"]
            or not report.evaluated_at <= evaluated_at < report.valid_until
        ):
            raise ValueError
        usage_by_id: dict[str, Mapping[str, object]] = {}
        for document in usage_documents:
            usage = validate_ai_usage_for_economics(
                document,
                expected_tenant=str(metadata["tenantId"]),
            )
            usage_id = usage["usage_record_id"]
            if not isinstance(usage_id, str) or usage_id in usage_by_id:
                raise ValueError
            usage_by_id[usage_id] = document
        cost_by_usage: dict[str, Mapping[str, object]] = {}
        cost_ids: list[str] = []
        catalog_ids: set[str] = set()
        engine_versions: set[str] = set()
        for document in cost_documents:
            cost = validate_ai_cost_record(document)
            cost_metadata = cost["metadata"]
            cost_spec = cost["spec"]
            assert isinstance(cost_metadata, Mapping) and isinstance(
                cost_spec,
                Mapping,
            )
            calculation_value = cost_spec["calculation"]
            assert isinstance(calculation_value, Mapping)
            usage_id = cost_spec["usageRecordId"]
            if (
                cost_metadata["tenantId"] != metadata["tenantId"]
                or not isinstance(usage_id, str)
                or usage_id in cost_by_usage
            ):
                raise ValueError
            cost_by_usage[usage_id] = document
            cost_ids.append(str(cost_metadata["id"]))
            catalog_ids.add(str(calculation_value["catalogId"]))
            engine_versions.add(str(calculation_value["engineVersion"]))
        if (
            set(cost_by_usage) != set(usage_by_id)
            or sorted(cost_ids) != savings["costRecordRefs"]
            or len(catalog_ids) != 1
            or len(engine_versions) != 1
        ):
            raise ValueError
        profile = _model_profile_from_finding(
            finding,
            report.document,
            catalog_id=next(iter(catalog_ids)),
            cost_engine_version=next(iter(engine_versions)),
        )
        candidate: list[_ModelCostCohortItem] = []
        reference: list[_ModelCostCohortItem] = []
        baseline_start = _parse_time(profile.baseline_start)
        baseline_end = _parse_time(profile.baseline_end)
        current_start = _parse_time(profile.current_start)
        current_end = _parse_time(profile.current_end)
        for usage_id, usage_document in usage_by_id.items():
            usage = validate_ai_usage_for_economics(
                usage_document,
                expected_tenant=profile.tenant_id,
            )
            started_at = usage["started_at"]
            if not isinstance(started_at, datetime):
                raise ValueError
            if baseline_start <= started_at < baseline_end:
                target = candidate
                expected_model_id = profile.candidate_model_id
            elif current_start <= started_at < current_end:
                target = reference
                expected_model_id = profile.model_id
            else:
                raise ValueError
            target.append(
                _model_cost_cohort_item(
                    profile,
                    usage_document,
                    cost_by_usage[usage_id],
                    expected_model_id=expected_model_id,
                )
            )
        candidate.sort(key=lambda item: (item.started_at, item.usage_id))
        reference.sort(key=lambda item: (item.started_at, item.usage_id))
        if not candidate or not reference:
            raise ValueError
        candidate_mean = _half_up_divide(
            sum(item.total_cost_subunits for item in candidate),
            len(candidate),
        )
        reference_mean = _half_up_divide(
            sum(item.total_cost_subunits for item in reference),
            len(reference),
        )
        if candidate_mean == 0 or reference_mean <= candidate_mean:
            raise ValueError
        increase = _half_up_divide(
            (reference_mean - candidate_mean) * 10_000,
            candidate_mean,
        )
        amount = (reference_mean - candidate_mean) * len(reference)
        currencies = {
            (item.currency, item.currency_scale)
            for item in candidate + reference
        }
        if len(currencies) != 1:
            raise ValueError
        currency, currency_scale = next(iter(currencies))
        if (
            observation["baseline"]
            != {"value": candidate_mean, "sampleCount": len(candidate)}
            or observation["current"]
            != {"value": reference_mean, "sampleCount": len(reference)}
            or observation["changeBasisPoints"] != increase
            or savings["currency"] != currency
            or savings["currencyScale"] != currency_scale
            or savings["amountSubunits"] != amount
            or calculation
            != {
                "method": "qualified-model-cost-difference",
                "candidateCostPerRequestSubunits": candidate_mean,
                "referenceCostPerRequestSubunits": reference_mean,
                "referenceRequestCount": len(reference),
                "suitabilityReportId": report.report_id,
            }
        ):
            raise ValueError
        usage_ids = sorted(usage_by_id)
        expected_evidence = [
            {"type": "ai-usage-record", "id": usage_id}
            for usage_id in usage_ids
        ] + [
            {
                "type": "ai-model-suitability-report",
                "id": report.report_id,
            }
        ]
        if (
            spec["evidenceRefs"] != expected_evidence
            or spec["finding"]
            != _model_finding_summary(
                _severity(increase),
                _confidence(len(candidate), len(reference)),
            )
            or spec["recommendation"] != _model_recommendation()
        ):
            raise ValueError
    except (
        AssertionError,
        KeyError,
        TypeError,
        ValueError,
        InvalidAiCostInputError,
        InvalidAiSavingsInputError,
        InvalidAiModelSuitabilityReportError,
    ):
        raise InvalidAiSavingsInputError("ai.savings.sources.invalid") from None


def validate_ai_savings_source_binding(
    finding_document: object,
    usage_documents: tuple[Mapping[str, object], ...],
    cost_documents: tuple[Mapping[str, object], ...],
    suitability_documents: tuple[Mapping[str, object], ...] = (),
) -> None:
    finding = validate_ai_savings_finding(finding_document)
    spec = finding["spec"]
    assert isinstance(spec, Mapping)
    rule = spec["rule"]
    assert isinstance(rule, Mapping)
    if rule["id"] == CONTEXT_GROWTH_RULE_ID:
        if suitability_documents:
            raise InvalidAiSavingsInputError("ai.savings.sources.invalid")
        validate_context_growth_source_binding(
            finding,
            usage_documents,
            cost_documents,
        )
        return
    if rule["id"] == EXPENSIVE_MODEL_RULE_ID:
        validate_expensive_model_source_binding(
            finding,
            usage_documents,
            cost_documents,
            suitability_documents,
        )
        return
    if suitability_documents:
        raise InvalidAiSavingsInputError("ai.savings.sources.invalid")
    validate_retry_amplification_source_binding(
        finding,
        usage_documents,
        cost_documents,
    )


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
    except (_UnsupportedCohort, _UnresolvedCohort):
        raise
    except (KeyError, StopIteration, TypeError, ValueError, InvalidAiCostInputError):
        raise InvalidAiSavingsInputError("ai.savings.sources.invalid") from None


def _retry_cohort_item(
    profile: RetryAmplificationProfile,
    usage_document: Mapping[str, object],
) -> _RetryCohortItem:
    try:
        usage = validate_ai_usage_for_economics(
            usage_document,
            expected_tenant=profile.tenant_id,
        )
        retry_count = usage["retry_count"]
        started_at = usage["started_at"]
        if (
            usage["provider"] != profile.provider
            or usage["model_id"] != profile.model_id
            or usage["region"] != profile.region
            or usage["service_name"] != profile.service_name
            or usage["deployment_environment"] != profile.deployment_environment
            or usage["outcome"] != "success"
            or isinstance(retry_count, bool)
            or not isinstance(retry_count, int)
            or not 0 <= retry_count <= 100
            or not isinstance(started_at, datetime)
        ):
            raise _UnsupportedCohort
        return _RetryCohortItem(
            str(usage["usage_record_id"]),
            started_at,
            retry_count,
        )
    except _UnsupportedCohort:
        raise
    except (KeyError, TypeError, ValueError, InvalidAiCostInputError):
        raise InvalidAiSavingsInputError("ai.savings.sources.invalid") from None


def _model_cost_cohort_item(
    profile: ExpensiveModelProfile,
    usage_document: Mapping[str, object],
    cost_document: Mapping[str, object],
    *,
    expected_model_id: str,
) -> _ModelCostCohortItem:
    try:
        usage = validate_ai_usage_for_economics(
            usage_document,
            expected_tenant=profile.tenant_id,
        )
        if (
            usage["provider"] != profile.provider
            or usage["model_id"] != expected_model_id
            or usage["region"] != profile.region
            or usage["service_name"] != profile.service_name
            or usage["deployment_environment"] != profile.deployment_environment
            or usage["outcome"] != "success"
            or usage["completeness"] != "complete"
            or usage["missing_fields"]
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
        total = result["totalSubunits"]
        currency = result["currency"]
        scale = result["currencyScale"]
        started_at = usage["started_at"]
        if (
            isinstance(total, bool)
            or not isinstance(total, int)
            or total < 0
            or not isinstance(currency, str)
            or isinstance(scale, bool)
            or not isinstance(scale, int)
            or not isinstance(started_at, datetime)
        ):
            raise ValueError
        return _ModelCostCohortItem(
            str(usage["usage_record_id"]),
            str(metadata["id"]),
            started_at,
            total,
            currency,
            scale,
        )
    except (_UnsupportedCohort, _UnresolvedCohort):
        raise
    except (KeyError, TypeError, ValueError, InvalidAiCostInputError):
        raise InvalidAiSavingsInputError("ai.savings.sources.invalid") from None


def _retry_rate_basis_points(items: tuple[_RetryCohortItem, ...]) -> int:
    if not items:
        raise ValueError
    return _half_up_divide(
        sum(item.retry_count > 0 for item in items) * 10_000,
        len(items),
    )


def _ai_economics_measurement(
    profile: ContextGrowthProfile,
    baseline_rows: tuple[
        tuple[Mapping[str, object], Mapping[str, object] | None], ...
    ],
    current_rows: tuple[
        tuple[Mapping[str, object], Mapping[str, object] | None], ...
    ],
    *,
    evaluation_status: str,
    finding: Mapping[str, object] | None,
) -> AiEconomicsMeasurement:
    """Build one bounded snapshot from the same facts used by the rule."""

    try:
        if evaluation_status not in {
            "qualified",
            "insufficient",
            "unresolved",
            "unsupported",
            "below-threshold",
        }:
            raise ValueError
        baseline_usage = tuple(
            _measurement_usage(profile, document) for document, _cost in baseline_rows
        )
        current_usage = tuple(
            _measurement_usage(profile, document) for document, _cost in current_rows
        )
        input_values = tuple(
            value
            for value in (item["inputTokens"] for item in current_usage)
            if isinstance(value, int) and not isinstance(value, bool)
        )
        output_values = tuple(
            value
            for value in (item["outputTokens"] for item in current_usage)
            if isinstance(value, int) and not isinstance(value, bool)
        )
        input_total = _bounded_total(input_values)
        output_total = _bounded_total(output_values)
        incomplete = sum(
            item["completeness"] != "complete" for item in current_usage
        )

        cost_counts = {"priced": 0, "unpriced": 0, "ambiguous": 0, "pending": 0}
        priced_amounts: list[int] = []
        money_units: set[tuple[str, int]] = set()
        for (_usage_document, cost_document), usage in zip(
            current_rows, current_usage
        ):
            if cost_document is None:
                cost_counts["pending"] += 1
                continue
            cost = validate_ai_cost_record(cost_document)
            metadata = cost["metadata"]
            spec = cost["spec"]
            assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
            calculation = spec["calculation"]
            result = spec["result"]
            assert isinstance(calculation, Mapping) and isinstance(result, Mapping)
            status = result["costStatus"]
            if (
                metadata["tenantId"] != profile.tenant_id
                or spec["usageRecordId"] != usage["usage_record_id"]
                or calculation["catalogId"] != profile.catalog_id
                or calculation["engineVersion"] != profile.cost_engine_version
                or status not in cost_counts
                or status == "pending"
            ):
                raise ValueError
            cost_counts[str(status)] += 1
            if status == "priced":
                amount = result["totalSubunits"]
                currency = result["currency"]
                scale = result["currencyScale"]
                if (
                    isinstance(amount, bool)
                    or not isinstance(amount, int)
                    or not isinstance(currency, str)
                    or isinstance(scale, bool)
                    or not isinstance(scale, int)
                ):
                    raise ValueError
                priced_amounts.append(amount)
                money_units.add((currency, scale))
        if len(money_units) > 1:
            raise ValueError
        calculated_cost = (
            _bounded_total(tuple(priced_amounts)) if priced_amounts else None
        )
        currency, currency_scale = (
            next(iter(money_units)) if money_units else (None, None)
        )

        baseline_mean: int | None = None
        current_mean: int | None = None
        change: int | None = None
        baseline_inputs = tuple(item["inputTokens"] for item in baseline_usage)
        current_inputs = tuple(item["inputTokens"] for item in current_usage)
        if (
            baseline_inputs
            and current_inputs
            and all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in baseline_inputs + current_inputs
            )
        ):
            baseline_mean = _half_up_divide(
                _bounded_total(tuple(int(value) for value in baseline_inputs)),
                len(baseline_inputs),
            )
            current_mean = _half_up_divide(
                _bounded_total(tuple(int(value) for value in current_inputs)),
                len(current_inputs),
            )
            if baseline_mean > 0:
                change = _signed_half_up_divide(
                    (current_mean - baseline_mean) * 10_000,
                    baseline_mean,
                )
                if not -MAX_SAFE_INTEGER <= change <= MAX_SAFE_INTEGER:
                    raise ValueError

        finding_count = 0
        finding_severity: str | None = None
        potential_savings: int | None = None
        if finding is not None:
            validated_finding = validate_ai_savings_finding(finding)
            finding_spec = validated_finding["spec"]
            assert isinstance(finding_spec, Mapping)
            finding_value = finding_spec["finding"]
            savings = finding_spec["potentialSavings"]
            assert isinstance(finding_value, Mapping) and isinstance(savings, Mapping)
            finding_count = 1
            finding_severity = str(finding_value["severity"])
            potential_savings = int(savings["amountSubunits"])
            if (
                savings["currency"] != currency
                or savings["currencyScale"] != currency_scale
            ):
                raise ValueError

        return AiEconomicsMeasurement(
            tenant_id=profile.tenant_id,
            profile_id=profile.profile_id,
            provider=profile.provider,
            model_id=profile.model_id,
            region=profile.region,
            service_name=profile.service_name,
            deployment_environment=profile.deployment_environment,
            request_count=len(current_usage),
            input_tokens=input_total,
            input_token_requests=len(input_values),
            output_tokens=output_total,
            output_token_requests=len(output_values),
            incomplete_requests=incomplete,
            priced_requests=cost_counts["priced"],
            unpriced_requests=cost_counts["unpriced"],
            ambiguous_requests=cost_counts["ambiguous"],
            pending_cost_requests=cost_counts["pending"],
            calculated_cost_subunits=calculated_cost,
            currency=currency,
            currency_scale=currency_scale,
            baseline_input_tokens_per_request=baseline_mean,
            current_input_tokens_per_request=current_mean,
            context_growth_change_basis_points=change,
            evaluation_status=evaluation_status,
            finding_count=finding_count,
            finding_severity=finding_severity,
            potential_savings_subunits=potential_savings,
        )
    except (
        AssertionError,
        KeyError,
        TypeError,
        ValueError,
        InvalidAiCostInputError,
        InvalidAiSavingsInputError,
    ):
        raise InvalidAiSavingsInputError("ai.savings.measurement.invalid") from None


def _measurement_usage(
    profile: ContextGrowthProfile,
    document: Mapping[str, object],
) -> Mapping[str, object]:
    usage = validate_ai_usage_for_economics(
        document,
        expected_tenant=profile.tenant_id,
    )
    if (
        usage["provider"] != profile.provider
        or usage["model_id"] != profile.model_id
        or usage["region"] != profile.region
        or usage["service_name"] != profile.service_name
        or usage["deployment_environment"] != profile.deployment_environment
        or usage["outcome"] != "success"
    ):
        raise InvalidAiSavingsInputError("ai.savings.measurement.invalid")
    return usage


def _bounded_total(values: tuple[int, ...]) -> int:
    total = sum(values)
    if total < 0 or total > MAX_SAFE_INTEGER:
        raise ValueError
    return total


def _signed_half_up_divide(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ValueError
    sign = -1 if numerator < 0 else 1
    return sign * _half_up_divide(abs(numerator), denominator)


def _retry_measurement(
    profile: RetryAmplificationProfile,
    baseline_rows: tuple[
        tuple[Mapping[str, object], Mapping[str, object] | None], ...
    ],
    current_rows: tuple[
        tuple[Mapping[str, object], Mapping[str, object] | None], ...
    ],
    *,
    evaluation_status: str,
    finding: Mapping[str, object] | None,
) -> AiRetryMeasurement:
    try:
        if evaluation_status not in {
            "qualified",
            "insufficient",
            "unresolved",
            "unsupported",
            "below-threshold",
        }:
            raise ValueError

        def facts(
            rows: tuple[
                tuple[Mapping[str, object], Mapping[str, object] | None], ...
            ],
        ) -> tuple[int | None, ...]:
            values: list[int | None] = []
            for document, _cost in rows:
                usage = validate_ai_usage_for_economics(
                    document,
                    expected_tenant=profile.tenant_id,
                )
                if (
                    usage["provider"] != profile.provider
                    or usage["model_id"] != profile.model_id
                    or usage["region"] != profile.region
                    or usage["service_name"] != profile.service_name
                    or usage["deployment_environment"]
                    != profile.deployment_environment
                    or usage["outcome"] != "success"
                ):
                    raise ValueError
                value = usage["retry_count"]
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 0 <= value <= 100
                ):
                    raise ValueError
                values.append(value)
            return tuple(values)

        baseline = facts(baseline_rows)
        current = facts(current_rows)
        baseline_rate = None
        current_rate = None
        increase = None
        if baseline and current and None not in baseline and None not in current:
            baseline_rate = _half_up_divide(
                sum(int(value) > 0 for value in baseline) * 10_000,
                len(baseline),
            )
            current_rate = _half_up_divide(
                sum(int(value) > 0 for value in current) * 10_000,
                len(current),
            )
            increase = current_rate - baseline_rate
        finding_count = 0
        severity = None
        if finding is not None:
            validated = _validate_retry_amplification_finding(finding)
            spec = validated["spec"]
            assert isinstance(spec, Mapping)
            finding_value = spec["finding"]
            assert isinstance(finding_value, Mapping)
            finding_count = 1
            severity = str(finding_value["severity"])
        present = tuple(int(value) for value in current if value is not None)
        return AiRetryMeasurement(
            tenant_id=profile.tenant_id,
            profile_id=profile.profile_id,
            provider=profile.provider,
            model_id=profile.model_id,
            region=profile.region,
            service_name=profile.service_name,
            deployment_environment=profile.deployment_environment,
            current_operations=len(current),
            current_retry_fact_operations=len(present),
            current_retrying_operations=sum(value > 0 for value in present),
            current_excess_attempts=sum(present),
            baseline_retry_rate_basis_points=baseline_rate,
            current_retry_rate_basis_points=current_rate,
            retry_rate_increase_basis_points=increase,
            evaluation_status=evaluation_status,
            finding_count=finding_count,
            finding_severity=severity,
        )
    except (
        AssertionError,
        KeyError,
        TypeError,
        ValueError,
        InvalidAiCostInputError,
        InvalidAiSavingsInputError,
    ):
        raise InvalidAiSavingsInputError("ai.savings.measurement.invalid") from None


def _model_savings_measurement(
    profile: ExpensiveModelProfile,
    candidate_rows: tuple[
        tuple[Mapping[str, object], Mapping[str, object] | None], ...
    ],
    reference_rows: tuple[
        tuple[Mapping[str, object], Mapping[str, object] | None], ...
    ],
    *,
    evaluation_status: str,
    finding: Mapping[str, object] | None,
) -> AiModelSavingsMeasurement:
    try:
        if evaluation_status not in {
            "qualified",
            "insufficient",
            "unresolved",
            "unsupported",
            "below-threshold",
        }:
            raise ValueError

        def items(
            rows: tuple[
                tuple[Mapping[str, object], Mapping[str, object] | None], ...
            ],
            model_id: str,
        ) -> tuple[tuple[_ModelCostCohortItem, ...], bool]:
            result: list[_ModelCostCohortItem] = []
            complete = True
            for usage_document, cost_document in rows:
                usage = validate_ai_usage_for_economics(
                    usage_document,
                    expected_tenant=profile.tenant_id,
                )
                if (
                    usage["provider"] != profile.provider
                    or usage["model_id"] != model_id
                    or usage["region"] != profile.region
                    or usage["service_name"] != profile.service_name
                    or usage["deployment_environment"]
                    != profile.deployment_environment
                    or usage["outcome"] != "success"
                ):
                    raise ValueError
                if cost_document is None:
                    complete = False
                    continue
                try:
                    result.append(
                        _model_cost_cohort_item(
                            profile,
                            usage_document,
                            cost_document,
                            expected_model_id=model_id,
                        )
                    )
                except (_UnresolvedCohort, _UnsupportedCohort):
                    complete = False
            return tuple(result), complete and len(result) == len(rows)

        candidate, candidate_complete = items(
            candidate_rows,
            profile.candidate_model_id,
        )
        reference, reference_complete = items(reference_rows, profile.model_id)
        candidate_mean: int | None = None
        reference_mean: int | None = None
        increase: int | None = None
        currency: str | None = None
        currency_scale: int | None = None
        if candidate and reference and candidate_complete and reference_complete:
            money = {
                (item.currency, item.currency_scale)
                for item in candidate + reference
            }
            if len(money) == 1:
                currency, currency_scale = next(iter(money))
                candidate_mean = _half_up_divide(
                    sum(item.total_cost_subunits for item in candidate),
                    len(candidate),
                )
                reference_mean = _half_up_divide(
                    sum(item.total_cost_subunits for item in reference),
                    len(reference),
                )
                if candidate_mean > 0:
                    increase = _signed_half_up_divide(
                        (reference_mean - candidate_mean) * 10_000,
                        candidate_mean,
                    )
        finding_count = 0
        finding_severity = None
        potential_savings = None
        if finding is not None:
            validated = _validate_expensive_model_finding(finding)
            finding_spec = validated["spec"]
            assert isinstance(finding_spec, Mapping)
            finding_value = finding_spec["finding"]
            savings = finding_spec["potentialSavings"]
            assert isinstance(finding_value, Mapping) and isinstance(
                savings,
                Mapping,
            )
            finding_count = 1
            finding_severity = str(finding_value["severity"])
            potential_savings = int(savings["amountSubunits"])
        return AiModelSavingsMeasurement(
            tenant_id=profile.tenant_id,
            profile_id=profile.profile_id,
            provider=profile.provider,
            reference_model_id=profile.model_id,
            candidate_model_id=profile.candidate_model_id,
            region=profile.region,
            service_name=profile.service_name,
            deployment_environment=profile.deployment_environment,
            reference_request_count=len(reference_rows),
            candidate_request_count=len(candidate_rows),
            reference_cost_per_request_subunits=reference_mean,
            candidate_cost_per_request_subunits=candidate_mean,
            cost_increase_basis_points=increase,
            currency=currency,
            currency_scale=currency_scale,
            evaluation_status=evaluation_status,
            finding_count=finding_count,
            finding_severity=finding_severity,
            potential_savings_subunits=potential_savings,
        )
    except (
        AssertionError,
        KeyError,
        TypeError,
        ValueError,
        InvalidAiCostInputError,
        InvalidAiSavingsInputError,
    ):
        raise InvalidAiSavingsInputError("ai.savings.measurement.invalid") from None


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


def _build_retry_finding(
    profile: RetryAmplificationProfile,
    baseline: tuple[_RetryCohortItem, ...],
    current: tuple[_RetryCohortItem, ...],
    *,
    evaluated_at: str,
    baseline_rate_basis_points: int,
    current_rate_basis_points: int,
    increase_basis_points: int,
) -> tuple[Mapping[str, object], PlatformEvent]:
    severity = _retry_severity(increase_basis_points)
    confidence = _confidence(len(baseline), len(current))
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
    evidence = [
        {"type": "ai-usage-record", "id": usage_id}
        for usage_id in sorted(item.usage_id for item in baseline + current)
    ]
    spec: Mapping[str, object] = {
        "rule": {"id": RETRY_AMPLIFICATION_RULE_ID, "version": RULE_VERSION},
        "scope": scope,
        "finding": _retry_finding_summary(severity, confidence),
        "observations": [
            {
                "metric": "retrying-operations-rate",
                "unit": "basis-points",
                "baseline": {
                    "value": baseline_rate_basis_points,
                    "sampleCount": len(baseline),
                },
                "current": {
                    "value": current_rate_basis_points,
                    "sampleCount": len(current),
                },
                "changeBasisPoints": increase_basis_points,
            }
        ],
        "potentialSavings": {
            "status": "unresolved",
            "reasonCode": "retry-billing-unproven",
            "period": scope["currentWindow"],
        },
        "recommendation": _retry_recommendation(),
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
        source=(
            f"urn:iip:ai-savings:{RETRY_AMPLIFICATION_RULE_ID}:{RULE_VERSION}"
        ),
        time=evaluated_at,
        subject=finding_id,
        tenant_id=profile.tenant_id,
        data={
            "findingId": finding_id,
            "ruleId": RETRY_AMPLIFICATION_RULE_ID,
            "ruleVersion": RULE_VERSION,
            "category": RETRY_AMPLIFICATION_RULE_ID,
            "severity": severity,
            "provider": profile.provider,
            "modelId": profile.model_id,
            "serviceName": profile.service_name,
        },
    )
    validate_ai_savings_finding(document)
    return document, event


def _build_expensive_model_finding(
    profile: ExpensiveModelProfile,
    candidate: tuple[_ModelCostCohortItem, ...],
    reference: tuple[_ModelCostCohortItem, ...],
    *,
    evaluated_at: str,
    candidate_cost_per_request: int,
    reference_cost_per_request: int,
    increase_basis_points: int,
    amount_subunits: int,
) -> tuple[Mapping[str, object], PlatformEvent]:
    currency = reference[0].currency
    currency_scale = reference[0].currency_scale
    severity = _severity(increase_basis_points)
    confidence = _confidence(len(candidate), len(reference))
    cost_refs = sorted(item.cost_id for item in candidate + reference)
    usage_refs = sorted(item.usage_id for item in candidate + reference)
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
        "candidateModelId": profile.candidate_model_id,
        "region": profile.region,
        "serviceName": profile.service_name,
        "deploymentEnvironment": profile.deployment_environment,
    }
    spec: Mapping[str, object] = {
        "rule": {"id": EXPENSIVE_MODEL_RULE_ID, "version": RULE_VERSION},
        "scope": scope,
        "finding": _model_finding_summary(severity, confidence),
        "observations": [
            {
                "metric": "calculated-cost-per-request",
                "unit": "currency-subunits-per-request",
                "baseline": {
                    "value": candidate_cost_per_request,
                    "sampleCount": len(candidate),
                },
                "current": {
                    "value": reference_cost_per_request,
                    "sampleCount": len(reference),
                },
                "changeBasisPoints": increase_basis_points,
            }
        ],
        "potentialSavings": {
            "status": "calculated",
            "currency": currency,
            "currencyScale": currency_scale,
            "amountSubunits": amount_subunits,
            "period": scope["currentWindow"],
            "calculation": {
                "method": "qualified-model-cost-difference",
                "candidateCostPerRequestSubunits": candidate_cost_per_request,
                "referenceCostPerRequestSubunits": reference_cost_per_request,
                "referenceRequestCount": len(reference),
                "suitabilityReportId": profile.suitability_report_id,
            },
            "costRecordRefs": cost_refs,
        },
        "recommendation": _model_recommendation(),
        "evidenceRefs": [
            {"type": "ai-usage-record", "id": usage_id}
            for usage_id in usage_refs
        ]
        + [
            {
                "type": "ai-model-suitability-report",
                "id": profile.suitability_report_id,
            }
        ],
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
        source=(
            f"urn:iip:ai-savings:{EXPENSIVE_MODEL_RULE_ID}:{RULE_VERSION}"
        ),
        time=evaluated_at,
        subject=finding_id,
        tenant_id=profile.tenant_id,
        data={
            "findingId": finding_id,
            "ruleId": EXPENSIVE_MODEL_RULE_ID,
            "ruleVersion": RULE_VERSION,
            "category": EXPENSIVE_MODEL_RULE_ID,
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


def _retry_profile_from_finding(
    finding: Mapping[str, object],
) -> RetryAmplificationProfile:
    metadata = finding["metadata"]
    spec = finding["spec"]
    assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
    scope = spec["scope"]
    assert isinstance(scope, Mapping)
    baseline = scope["baselineWindow"]
    current = scope["currentWindow"]
    assert isinstance(baseline, Mapping) and isinstance(current, Mapping)
    return RetryAmplificationProfile(
        "finding-source-validation",
        str(metadata["tenantId"]),
        "apc_" + "0" * 32,
        COST_ENGINE_VERSION,
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
        1,
        MAX_COHORT_RECORDS,
        0,
    )


def _model_profile_from_finding(
    finding: Mapping[str, object],
    suitability_report: Mapping[str, object],
    *,
    catalog_id: str,
    cost_engine_version: str,
) -> ExpensiveModelProfile:
    metadata = finding["metadata"]
    spec = finding["spec"]
    assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
    scope = spec["scope"]
    savings = spec["potentialSavings"]
    assert isinstance(scope, Mapping) and isinstance(savings, Mapping)
    baseline = scope["baselineWindow"]
    current = scope["currentWindow"]
    calculation = savings["calculation"]
    assert isinstance(baseline, Mapping) and isinstance(current, Mapping)
    assert isinstance(calculation, Mapping)
    report = validate_ai_model_suitability_report(
        suitability_report,
        allow_test_fixtures=True,
    )
    return ExpensiveModelProfile(
        "finding-source-validation",
        str(metadata["tenantId"]),
        catalog_id,
        cost_engine_version,
        str(scope["provider"]),
        str(scope["modelId"]),
        str(scope["candidateModelId"]),
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
        str(calculation["suitabilityReportId"]),
        _format_time(report.evaluated_at),
        _format_time(report.valid_until),
        report.document,
    )


def _cohort_query(
    profile: (
        ContextGrowthProfile
        | RetryAmplificationProfile
        | ExpensiveModelProfile
    ),
    *,
    baseline: bool,
) -> AiSavingsCohortQuery:
    return AiSavingsCohortQuery(
        provider=profile.provider,
        model_id=(
            profile.candidate_model_id
            if isinstance(profile, ExpensiveModelProfile) and baseline
            else profile.model_id
        ),
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


def _retry_severity(increase_basis_points: int) -> str:
    if increase_basis_points >= 5000:
        return "high"
    if increase_basis_points >= 2500:
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


def _retry_finding_summary(
    severity: str,
    confidence: int,
) -> Mapping[str, object]:
    return {
        "category": RETRY_AMPLIFICATION_RULE_ID,
        "severity": severity,
        "summary": (
            "The share of successful operations reporting one or more retries "
            "increased against the preceding comparison window."
        ),
        "confidenceBasisPoints": confidence,
    }


def _model_finding_summary(
    severity: str,
    confidence: int,
) -> Mapping[str, object]:
    return {
        "category": EXPENSIVE_MODEL_RULE_ID,
        "severity": severity,
        "summary": (
            "Calculated cost per request for the reference model exceeded the "
            "qualified candidate-model cohort by the configured threshold."
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


def _retry_recommendation() -> Mapping[str, object]:
    return {
        "actionCode": "review-retry-policy",
        "summary": (
            "Review provider throttling, timeout, and retry-policy evidence; "
            "validate reliability before changing retry behavior."
        ),
        "requiresValidation": True,
    }


def _model_recommendation() -> Mapping[str, object]:
    return {
        "actionCode": "evaluate-lower-cost-model",
        "summary": (
            "Review the cited workload suitability report and validate the "
            "candidate model against current traffic before changing models."
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


def _closed(
    value: object,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, object]:
    allowed = required | (optional or set())
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or not set(value).issubset(allowed)
    ):
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
    "CONTEXT_GROWTH_RULE_ID",
    "ContextGrowthProfile",
    "EXPENSIVE_MODEL_RULE_ID",
    "ExpensiveModelProfile",
    "InvalidAiSavingsInputError",
    "MAX_COHORT_RECORDS",
    "RULE_ID",
    "RULE_VERSION",
    "RETRY_AMPLIFICATION_RULE_ID",
    "RetryAmplificationProfile",
    "validate_ai_savings_finding",
    "validate_ai_savings_profile",
    "validate_ai_savings_source_binding",
    "validate_context_growth_profile",
    "validate_context_growth_source_binding",
    "validate_expensive_model_profile",
    "validate_expensive_model_source_binding",
    "validate_retry_amplification_profile",
    "validate_retry_amplification_source_binding",
]
