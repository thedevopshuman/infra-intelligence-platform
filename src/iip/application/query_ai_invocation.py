"""Privileged exact-correlation observation for the AI economics pipeline."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Mapping

from iip.application.attribute_ai_usage import (
    ENGINE_VERSION as ATTRIBUTION_ENGINE_VERSION,
    InvalidAiAttributionInputError,
    validate_ai_usage_attribution_record,
)
from iip.application.calculate_ai_cost import (
    ENGINE_VERSION as COST_ENGINE_VERSION,
    InvalidAiCostInputError,
    validate_ai_cost_record,
    validate_ai_usage_for_economics,
)
from iip.application.ports import (
    ActorContext,
    AiEconomicsLedger,
    AiInvocationEconomicsQuery,
    Clock,
    PersistenceError,
    PolicyDecisionPoint,
)
from iip.application.query_ai_allocations import (
    AiAllocationConfigurationError,
    AiAllocationSources,
    canonical_ai_economics_timestamp,
    validate_ai_allocation_sources,
)


_TRACE_ID = re.compile(r"^[a-f0-9]{32}$")
_SPAN_ID = re.compile(r"^[a-f0-9]{16}$")


class AiInvocationObservationConfigurationError(ValueError):
    """Active AI economics generations are absent or invalid."""


class AiInvocationObservationQueryError(ValueError):
    """An exact-correlation request or stored join is invalid."""


class AiInvocationObservationAuthorizationError(PermissionError):
    """The caller lacks exact AI economics qualification authority."""


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_ai_invocation_query(
    actor: ActorContext,
    query: AiInvocationEconomicsQuery,
) -> None:
    """Defensively validate the exact tenant query at every adapter boundary."""

    if (
        not isinstance(actor, ActorContext)
        or not actor.actor_id
        or actor.actor_id == "anonymous"
        or not actor.tenant_id
        or not isinstance(query, AiInvocationEconomicsQuery)
        or _TRACE_ID.fullmatch(query.trace_id) is None
        or _SPAN_ID.fullmatch(query.span_id) is None
        or not query.policy_id.startswith("aap_")
        or not query.catalog_id.startswith("apc_")
        or query.attribution_engine_version != ATTRIBUTION_ENGINE_VERSION
        or query.cost_engine_version != COST_ENGINE_VERSION
    ):
        raise AiInvocationObservationQueryError("ai.invocation-observation.request.invalid")


class AiInvocationObservationService:
    """Observe exact ledger progress without returning trace/span identifiers."""

    def __init__(
        self,
        ledger: AiEconomicsLedger,
        policy: PolicyDecisionPoint,
        clock: Clock,
        attribution_policies: tuple[Mapping[str, object], ...],
        price_catalogs: tuple[Mapping[str, object], ...],
        *,
        allow_test_fixtures: bool = False,
    ) -> None:
        try:
            self._sources = validate_ai_allocation_sources(
                attribution_policies,
                price_catalogs,
                allow_test_fixtures=allow_test_fixtures,
            )
        except (AiAllocationConfigurationError, TypeError, ValueError) as error:
            raise AiInvocationObservationConfigurationError(
                "ai.invocation-observation.configuration.invalid"
            ) from error
        self._ledger = ledger
        self._policy = policy
        self._clock = clock

    def observe(
        self,
        actor: ActorContext,
        request: Mapping[str, object],
    ) -> Mapping[str, object]:
        trace_id, span_id = self._request(request)
        if (
            not isinstance(actor, ActorContext)
            or not isinstance(actor.roles, tuple)
            or "platform-admin" not in actor.roles
        ):
            raise AiInvocationObservationAuthorizationError("role.required")
        sources = self._sources.get(actor.tenant_id)
        if sources is None:
            raise AiInvocationObservationConfigurationError(
                "ai.invocation-observation.not-configured"
            )
        decision = self._policy.decide(
            actor,
            "ai-economics:qualify",
            {"tenantId": actor.tenant_id, "correlationDigest": self._correlation_digest(actor, trace_id, span_id)},
        )
        if not decision.allowed:
            raise AiInvocationObservationAuthorizationError(decision.reason_code)
        query = AiInvocationEconomicsQuery(
            trace_id=trace_id,
            span_id=span_id,
            policy_id=sources.policy.policy_id,
            attribution_engine_version=ATTRIBUTION_ENGINE_VERSION,
            catalog_id=sources.catalog.catalog_id,
            cost_engine_version=COST_ENGINE_VERSION,
        )
        found = self._ledger.find_ai_invocation_economics(actor, query)
        generated_at = canonical_ai_economics_timestamp(self._clock.now())[1]
        document: dict[str, object] = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "AiEconomicsInvocationObservation",
            "metadata": {"tenantId": actor.tenant_id, "generatedAt": generated_at},
            "spec": {
                "status": "not-observed",
                "correlationDigest": self._correlation_digest(actor, trace_id, span_id),
                "sources": self._source_document(sources),
                "usage": {"status": "not-observed"},
                "attribution": {"status": "pending"},
                "cost": {"status": "pending"},
            },
        }
        if found is None:
            return document
        usage_document, attribution_document, cost_document = found
        usage = self._usage(actor, trace_id, span_id, usage_document)
        attribution = self._attribution(actor, usage, attribution_document, sources)
        cost = self._cost(actor, usage, cost_document, sources)
        spec = document["spec"]
        assert isinstance(spec, dict)
        spec.update(
            {
                "status": (
                    "complete"
                    if attribution["status"] != "pending" and cost["status"] != "pending"
                    else "processing"
                ),
                "usage": usage,
                "attribution": attribution,
                "cost": cost,
            }
        )
        return document

    @staticmethod
    def _request(request: Mapping[str, object]) -> tuple[str, str]:
        try:
            if set(request) != {"apiVersion", "kind", "spec"}:
                raise ValueError
            if (
                request["apiVersion"] != "iip.platform/v1alpha1"
                or request["kind"] != "AiEconomicsInvocationObservationRequest"
            ):
                raise ValueError
            spec = request["spec"]
            if not isinstance(spec, Mapping) or set(spec) != {"traceId", "spanId"}:
                raise ValueError
            trace_id = spec["traceId"]
            span_id = spec["spanId"]
            if (
                not isinstance(trace_id, str)
                or _TRACE_ID.fullmatch(trace_id) is None
                or not isinstance(span_id, str)
                or _SPAN_ID.fullmatch(span_id) is None
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise AiInvocationObservationQueryError(
                "ai.invocation-observation.request.invalid"
            ) from None
        return trace_id, span_id

    @staticmethod
    def _correlation_digest(actor: ActorContext, trace_id: str, span_id: str) -> str:
        return _canonical_digest(
            {"tenantId": actor.tenant_id, "traceId": trace_id, "spanId": span_id}
        )

    @staticmethod
    def _source_document(sources: AiAllocationSources) -> Mapping[str, object]:
        return {
            "attribution": {
                "id": sources.policy.policy_id,
                "version": sources.policy.version,
                "sourceHash": sources.policy.source_hash,
                "documentDigest": sources.policy_document_digest,
                "engineVersion": ATTRIBUTION_ENGINE_VERSION,
            },
            "pricing": {
                "id": sources.catalog.catalog_id,
                "version": sources.catalog.version,
                "sourceHash": sources.catalog.source_hash,
                "documentDigest": sources.catalog_document_digest,
                "engineVersion": COST_ENGINE_VERSION,
                "currency": sources.catalog.currency,
                "currencyScale": sources.catalog.currency_scale,
                "costBasis": "calculated-estimate",
            },
        }

    @staticmethod
    def _usage(
        actor: ActorContext,
        trace_id: str,
        span_id: str,
        document: Mapping[str, object],
    ) -> Mapping[str, object]:
        try:
            validated = validate_ai_usage_for_economics(
                document, expected_tenant=actor.tenant_id
            )
            metadata = document["metadata"]
            spec = document["spec"]
            invocation = spec["invocation"] if isinstance(spec, Mapping) else None
            if (
                validated["trace_id"] != trace_id
                or not isinstance(metadata, Mapping)
                or not isinstance(invocation, Mapping)
                or invocation.get("spanId") != span_id
            ):
                raise ValueError
            return {
                "status": "recorded",
                "recordId": metadata["id"],
                "recordDigest": _canonical_digest(document),
                "startedAt": invocation["startedAt"],
            }
        except (KeyError, TypeError, ValueError, InvalidAiCostInputError):
            raise PersistenceError("storage.state.invalid") from None

    @staticmethod
    def _attribution(
        actor: ActorContext,
        usage: Mapping[str, object],
        document: Mapping[str, object] | None,
        sources: AiAllocationSources,
    ) -> Mapping[str, object]:
        if document is None:
            return {"status": "pending"}
        try:
            validated = validate_ai_usage_attribution_record(document)
            metadata = validated["metadata"]
            spec = validated["spec"]
            policy = spec["policy"] if isinstance(spec, Mapping) else None
            resolution = spec["resolution"] if isinstance(spec, Mapping) else None
            if (
                not isinstance(metadata, Mapping)
                or metadata.get("tenantId") != actor.tenant_id
                or not isinstance(spec, Mapping)
                or spec.get("usageRecordId") != usage["recordId"]
                or spec.get("engineVersion") != ATTRIBUTION_ENGINE_VERSION
                or not isinstance(policy, Mapping)
                or policy.get("id") != sources.policy.policy_id
                or policy.get("version") != sources.policy.version
                or policy.get("sourceHash") != sources.policy.source_hash
                or not isinstance(resolution, Mapping)
            ):
                raise ValueError
            result: dict[str, object] = {
                "status": resolution["status"],
                "recordId": metadata["id"],
                "recordDigest": _canonical_digest(document),
            }
            if resolution["status"] == "allocated":
                application = resolution["application"]
                team = resolution["team"]
                if not isinstance(application, Mapping) or not isinstance(team, Mapping):
                    raise ValueError
                result.update(
                    {"applicationId": application["id"], "teamId": team["id"]}
                )
            else:
                result["reasonCode"] = resolution["reasonCode"]
            return result
        except (KeyError, TypeError, ValueError, InvalidAiAttributionInputError):
            raise PersistenceError("storage.state.invalid") from None

    @staticmethod
    def _cost(
        actor: ActorContext,
        usage: Mapping[str, object],
        document: Mapping[str, object] | None,
        sources: AiAllocationSources,
    ) -> Mapping[str, object]:
        if document is None:
            return {"status": "pending"}
        try:
            validated = validate_ai_cost_record(document)
            metadata = validated["metadata"]
            spec = validated["spec"]
            calculation = spec["calculation"] if isinstance(spec, Mapping) else None
            cost_result = spec["result"] if isinstance(spec, Mapping) else None
            if (
                not isinstance(metadata, Mapping)
                or metadata.get("tenantId") != actor.tenant_id
                or not isinstance(spec, Mapping)
                or spec.get("usageRecordId") != usage["recordId"]
                or not isinstance(calculation, Mapping)
                or calculation.get("catalogId") != sources.catalog.catalog_id
                or calculation.get("catalogVersion") != sources.catalog.version
                or calculation.get("catalogSourceHash") != sources.catalog.source_hash
                or calculation.get("engineVersion") != COST_ENGINE_VERSION
                or not isinstance(cost_result, Mapping)
            ):
                raise ValueError
            status = cost_result["costStatus"]
            result: dict[str, object] = {
                "status": status,
                "recordId": metadata["id"],
                "recordDigest": _canonical_digest(document),
            }
            if status == "priced":
                if (
                    cost_result.get("currency") != sources.catalog.currency
                    or cost_result.get("currencyScale") != sources.catalog.currency_scale
                ):
                    raise ValueError
                result["pricedCost"] = {
                    "currency": cost_result["currency"],
                    "currencyScale": cost_result["currencyScale"],
                    "totalSubunits": cost_result["totalSubunits"],
                    "costBasis": "calculated-estimate",
                }
            else:
                result["reasonCode"] = cost_result["reasonCode"]
            return result
        except (KeyError, TypeError, ValueError, InvalidAiCostInputError):
            raise PersistenceError("storage.state.invalid") from None


__all__ = [
    "AiInvocationObservationAuthorizationError",
    "AiInvocationObservationConfigurationError",
    "AiInvocationObservationQueryError",
    "AiInvocationObservationService",
    "validate_ai_invocation_query",
]
