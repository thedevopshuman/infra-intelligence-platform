"""Protected, effective-time application and team attribution for AI usage."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping
from urllib.parse import urlsplit

from iip.application.ports import ActorContext, AiAttributionLedger, Clock
from iip.domain.models import PlatformEvent


ENGINE_VERSION = "0.1.0"
_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_WORKER_ID = re.compile(r"[A-Za-z0-9._:-]{1,256}")
_POLICY_ID = re.compile(r"aap_[a-f0-9]{32}")
_ATTRIBUTION_ID = re.compile(r"aia_[a-f0-9]{32}")
_USAGE_ID = re.compile(r"aiu_[a-f0-9]{32}")
_VERSION = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[.][0-9]+")
_SAFE_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_TRACE_ID = re.compile(r"[a-f0-9]{32}")
_SHA256 = re.compile(r"sha256:[a-f0-9]{64}")
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:authorization|password|passwd|token|secret|api[_-]?key|"
    r"access[_-]?key)\s*[:=]\s*\S+|bearer\s+\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


class AiAttributionConfigurationError(ValueError):
    """Protected attribution configuration is unsafe or ambiguous."""


class InvalidAiAttributionInputError(ValueError):
    """Stored source facts or an attribution result are invalid."""


@dataclass(frozen=True)
class AiAttributionRule:
    rule_id: str
    priority: int
    service_name: str
    service_namespace: str | None
    deployment_environment: str | None
    resource_ref: str | None
    application_id: str
    application_name: str
    team_id: str
    team_name: str
    effective_from: datetime
    effective_until: datetime | None


@dataclass(frozen=True)
class ValidatedAiAttributionPolicy:
    document: Mapping[str, object]
    tenant_id: str
    policy_id: str
    version: str
    published_at: str
    source_kind: str
    source_hash: str
    rules: tuple[AiAttributionRule, ...]


@dataclass(frozen=True)
class AiAttributionPass:
    processed: int
    allocated: int
    unallocated: int
    policy_id: str
    policy_version: str


class AiAttributionService:
    """Resolve immutable usage against one protected policy per tenant."""

    def __init__(
        self,
        ledger: AiAttributionLedger,
        clock: Clock,
        policies: tuple[Mapping[str, object], ...],
        *,
        allow_test_fixtures: bool = False,
        batch_size: int = 100,
    ) -> None:
        if (
            not isinstance(policies, tuple)
            or not policies
            or len(policies) > 1000
            or not isinstance(allow_test_fixtures, bool)
            or isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or not 1 <= batch_size <= 1000
        ):
            raise AiAttributionConfigurationError(
                "ai.attribution.configuration.invalid"
            )
        validated = tuple(validate_ai_attribution_policy(item) for item in policies)
        if len({item.tenant_id for item in validated}) != len(validated):
            raise AiAttributionConfigurationError("ai.attribution.policy.ambiguous")
        if not allow_test_fixtures and any(
            item.source_kind == "test-fixture" for item in validated
        ):
            raise AiAttributionConfigurationError(
                "ai.attribution.test-fixture.prohibited"
            )
        self._ledger = ledger
        self._clock = clock
        self._policies = {item.tenant_id: item for item in validated}
        self._batch_size = batch_size

    @property
    def tenant_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._policies))

    def run_once(self, tenant_id: str, worker_id: str) -> AiAttributionPass:
        actor = _attribution_actor(tenant_id, worker_id)
        policy = self._policies.get(tenant_id)
        if policy is None:
            raise AiAttributionConfigurationError("ai.attribution.policy.missing")
        self._ledger.register_attribution_policy(actor, policy.document)
        usage_records = self._ledger.list_usage_without_attribution(
            actor,
            policy.policy_id,
            ENGINE_VERSION,
            limit=self._batch_size,
        )
        if (
            not isinstance(usage_records, tuple)
            or len(usage_records) > self._batch_size
        ):
            raise InvalidAiAttributionInputError("ai.attribution.storage.invalid")
        if not usage_records:
            return AiAttributionPass(0, 0, 0, policy.policy_id, policy.version)
        resolved_at = _timestamp(self._clock.now())[1]
        records: list[Mapping[str, object]] = []
        events: list[PlatformEvent] = []
        allocated = 0
        for usage in usage_records:
            record, event = resolve_ai_usage_attribution(
                policy,
                usage,
                resolved_at=resolved_at,
            )
            spec = record.get("spec")
            resolution = spec.get("resolution") if isinstance(spec, Mapping) else None
            if not isinstance(resolution, Mapping):
                raise InvalidAiAttributionInputError(
                    "ai.attribution.storage.invalid"
                )
            if resolution.get("status") == "allocated":
                allocated += 1
            records.append(record)
            events.append(event)
        self._ledger.commit_usage_attribution_batch(
            actor,
            tuple(records),
            tuple(events),
        )
        return AiAttributionPass(
            len(records),
            allocated,
            len(records) - allocated,
            policy.policy_id,
            policy.version,
        )


def validate_ai_attribution_policy(
    document: object,
) -> ValidatedAiAttributionPolicy:
    """Copy and semantically validate one protected attribution snapshot."""

    try:
        root = _closed(
            _json_copy(document),
            {"apiVersion", "kind", "metadata", "spec"},
        )
        if (
            root["apiVersion"] != "iip.platform/v1alpha1"
            or root["kind"] != "AiAttributionPolicy"
        ):
            raise ValueError
        metadata = _closed(
            root["metadata"],
            {"id", "tenantId", "version", "publishedAt"},
        )
        spec = _closed(root["spec"], {"source", "rules"})
        source = _closed(
            spec["source"],
            {"kind", "locator", "retrievedAt", "contentHash"},
        )
        tenant_id = _matched(metadata["tenantId"], _TENANT_ID)
        policy_id = _matched(metadata["id"], _POLICY_ID)
        version = _matched(metadata["version"], _VERSION)
        published, published_at = _timestamp(metadata["publishedAt"])
        source_kind = source["kind"]
        if source_kind not in {"operator-managed", "test-fixture"}:
            raise ValueError
        locator = _text(source["locator"], maximum=2048)
        parsed = urlsplit(locator)
        if (
            _SENSITIVE_TEXT.search(locator)
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError
        retrieved, _ = _timestamp(source["retrievedAt"])
        if retrieved > published:
            raise ValueError
        source_hash = _matched(source["contentHash"], _SHA256)
        raw_rules = spec["rules"]
        if not isinstance(raw_rules, list) or not 1 <= len(raw_rules) <= 1000:
            raise ValueError
        rules = tuple(_rule(item) for item in raw_rules)
        if (
            len({item.rule_id for item in rules}) != len(rules)
            or len({item.priority for item in rules}) != len(rules)
            or tuple((-item.priority, item.rule_id) for item in rules)
            != tuple(sorted((-item.priority, item.rule_id) for item in rules))
            or len({item.application_id for item in rules}) > 1000
            or len({item.team_id for item in rules}) > 1000
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise AiAttributionConfigurationError(
            "ai.attribution.policy.invalid"
        ) from None
    return ValidatedAiAttributionPolicy(
        root,
        tenant_id,
        policy_id,
        version,
        published_at,
        str(source_kind),
        source_hash,
        rules,
    )


def resolve_ai_usage_attribution(
    policy: ValidatedAiAttributionPolicy,
    usage_document: object,
    *,
    resolved_at: str,
) -> tuple[Mapping[str, object], PlatformEvent]:
    """Resolve one invocation using its effective time, never processing time."""

    usage = _usage_identity(usage_document, expected_tenant=policy.tenant_id)
    resolved_time, canonical_resolved_at = _timestamp(resolved_at)
    effective_at = usage["effective_at"]
    assert isinstance(effective_at, datetime)
    if resolved_time < effective_at:
        raise InvalidAiAttributionInputError("ai.attribution.time.invalid")
    matched = next(
        (rule for rule in policy.rules if _rule_matches(rule, usage)),
        None,
    )
    resolution: Mapping[str, object]
    if matched is None:
        resolution = {
            "status": "unallocated",
            "reasonCode": "no-matching-rule",
        }
    else:
        resolution = {
            "status": "allocated",
            "ruleId": matched.rule_id,
            "application": {
                "id": matched.application_id,
                "name": matched.application_name,
            },
            "team": {"id": matched.team_id, "name": matched.team_name},
        }
    identity = {
        "tenantId": policy.tenant_id,
        "usageRecordId": usage["usage_record_id"],
        "policyId": policy.policy_id,
        "policyVersion": policy.version,
        "policySourceHash": policy.source_hash,
        "engineVersion": ENGINE_VERSION,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    observed_identity: dict[str, object] = {
        "serviceName": usage["service_name"],
        "resourceRefs": list(usage["resource_refs"]),
    }
    if usage["service_namespace"] is not None:
        observed_identity["serviceNamespace"] = usage["service_namespace"]
    if usage["deployment_environment"] is not None:
        observed_identity["deploymentEnvironment"] = usage[
            "deployment_environment"
        ]
    attribution_id = "aia_" + digest[:32]
    record: Mapping[str, object] = {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "AiUsageAttributionRecord",
        "metadata": {
            "id": attribution_id,
            "tenantId": policy.tenant_id,
            "resolvedAt": canonical_resolved_at,
        },
        "spec": {
            "usageRecordId": usage["usage_record_id"],
            "effectiveAt": usage["effective_at_text"],
            "engineVersion": ENGINE_VERSION,
            "policy": {
                "id": policy.policy_id,
                "version": policy.version,
                "sourceHash": policy.source_hash,
            },
            "observedIdentity": observed_identity,
            "resolution": resolution,
        },
    }
    event_data: dict[str, object] = {
        "attributionRecordId": attribution_id,
        "usageRecordId": usage["usage_record_id"],
        "policyId": policy.policy_id,
        "policyVersion": policy.version,
        "status": resolution["status"],
    }
    if matched is not None:
        event_data.update(
            {
                "applicationId": matched.application_id,
                "teamId": matched.team_id,
            }
        )
    event = PlatformEvent(
        event_id="ai-attribution-" + digest,
        event_type="io.iip.ai.usage-attributed.v1",
        source="urn:iip:ai-attribution:" + ENGINE_VERSION,
        time=canonical_resolved_at,
        subject=attribution_id,
        tenant_id=policy.tenant_id,
        correlation_id=str(usage["trace_id"]),
        causation_id=str(usage["usage_record_id"]),
        data=event_data,
    )
    return record, event


def validate_ai_usage_attribution_record(
    document: object,
) -> Mapping[str, object]:
    """Validate a closed attribution result and its deterministic identity."""

    try:
        root = _closed(
            _json_copy(document),
            {"apiVersion", "kind", "metadata", "spec"},
        )
        if (
            root["apiVersion"] != "iip.platform/v1alpha1"
            or root["kind"] != "AiUsageAttributionRecord"
        ):
            raise ValueError
        metadata = _closed(root["metadata"], {"id", "tenantId", "resolvedAt"})
        spec = _closed(
            root["spec"],
            {
                "usageRecordId",
                "effectiveAt",
                "engineVersion",
                "policy",
                "observedIdentity",
                "resolution",
            },
        )
        policy = _closed(spec["policy"], {"id", "version", "sourceHash"})
        observed = _closed(
            spec["observedIdentity"],
            {"serviceName", "resourceRefs"},
            {"serviceNamespace", "deploymentEnvironment"},
        )
        tenant_id = _matched(metadata["tenantId"], _TENANT_ID)
        attribution_id = _matched(metadata["id"], _ATTRIBUTION_ID)
        resolved_at, _ = _timestamp(metadata["resolvedAt"])
        effective_at, _ = _timestamp(spec["effectiveAt"])
        if resolved_at < effective_at or spec["engineVersion"] != ENGINE_VERSION:
            raise ValueError
        usage_id = _matched(spec["usageRecordId"], _USAGE_ID)
        policy_id = _matched(policy["id"], _POLICY_ID)
        policy_version = _matched(policy["version"], _VERSION)
        source_hash = _matched(policy["sourceHash"], _SHA256)
        _text(observed["serviceName"], maximum=256)
        if "serviceNamespace" in observed:
            _text(observed["serviceNamespace"], maximum=256)
        if "deploymentEnvironment" in observed:
            _text(observed["deploymentEnvironment"], maximum=128)
        refs = observed["resourceRefs"]
        if (
            not isinstance(refs, list)
            or len(refs) > 64
            or refs != sorted(refs)
            or len(set(refs)) != len(refs)
            or any(
                not isinstance(item, str) or not _RESOURCE_UID.fullmatch(item)
                for item in refs
            )
        ):
            raise ValueError
        _resolution(spec["resolution"])
        identity = {
            "tenantId": tenant_id,
            "usageRecordId": usage_id,
            "policyId": policy_id,
            "policyVersion": policy_version,
            "policySourceHash": source_hash,
            "engineVersion": ENGINE_VERSION,
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if attribution_id != "aia_" + digest[:32]:
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise InvalidAiAttributionInputError(
            "ai.attribution.record.invalid"
        ) from None
    return root


def validate_ai_attribution_source_binding(
    record_document: object,
    policy_document: object,
    usage_document: object,
) -> None:
    """Re-resolve a record from the exact immutable sources it cites."""

    try:
        record = validate_ai_usage_attribution_record(record_document)
        policy = validate_ai_attribution_policy(policy_document)
        metadata = record["metadata"]
        assert isinstance(metadata, Mapping)
        expected, _event = resolve_ai_usage_attribution(
            policy,
            usage_document,
            resolved_at=str(metadata["resolvedAt"]),
        )
        if expected != record:
            raise ValueError
    except (
        AiAttributionConfigurationError,
        InvalidAiAttributionInputError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise InvalidAiAttributionInputError(
            "ai.attribution.binding.invalid"
        ) from None


def _rule(value: object) -> AiAttributionRule:
    item = _closed(
        value,
        {"id", "priority", "match", "allocation", "effectiveFrom"},
        {"effectiveUntil"},
    )
    match = _closed(
        item["match"],
        {"serviceName"},
        {"serviceNamespace", "deploymentEnvironment", "resourceRef"},
    )
    allocation = _closed(item["allocation"], {"application", "team"})
    application = _organizational_unit(allocation["application"])
    team = _organizational_unit(allocation["team"])
    priority = _integer(item["priority"], minimum=0, maximum=1_000_000)
    effective_from, _ = _timestamp(item["effectiveFrom"])
    effective_until = None
    if "effectiveUntil" in item:
        effective_until, _ = _timestamp(item["effectiveUntil"])
        if effective_until <= effective_from:
            raise ValueError
    resource_ref = match.get("resourceRef")
    if resource_ref is not None:
        resource_ref = _matched(resource_ref, _RESOURCE_UID)
    return AiAttributionRule(
        _matched(item["id"], _SAFE_ID),
        priority,
        _text(match["serviceName"], maximum=256),
        _optional_text(match.get("serviceNamespace"), maximum=256),
        _optional_text(match.get("deploymentEnvironment"), maximum=128),
        resource_ref,
        application[0],
        application[1],
        team[0],
        team[1],
        effective_from,
        effective_until,
    )


def _organizational_unit(value: object) -> tuple[str, str]:
    item = _closed(value, {"id", "name"})
    return _matched(item["id"], _SAFE_ID), _text(item["name"], maximum=256)


def _usage_identity(value: object, *, expected_tenant: str) -> Mapping[str, object]:
    try:
        root = _closed(
            _json_copy(value),
            {"apiVersion", "kind", "metadata", "spec"},
        )
        if (
            root["apiVersion"] != "iip.platform/v1alpha1"
            or root["kind"] != "AiUsageRecord"
        ):
            raise ValueError
        metadata = _closed(root["metadata"], {"id", "tenantId", "recordedAt"})
        spec = _closed(
            root["spec"],
            {
                "source",
                "invocation",
                "attribution",
                "usage",
                "privacy",
                "deduplicationKey",
            },
        )
        invocation = spec["invocation"]
        if not isinstance(invocation, dict):
            raise ValueError
        attribution = _closed(
            spec["attribution"],
            {"serviceName", "resourceRefs"},
            {"serviceNamespace", "deploymentEnvironment"},
        )
        tenant_id = _matched(metadata["tenantId"], _TENANT_ID)
        if tenant_id != expected_tenant:
            raise ValueError
        usage_id = _matched(metadata["id"], _USAGE_ID)
        effective_at, effective_text = _timestamp(invocation["startedAt"])
        trace_id = _matched(invocation["traceId"], _TRACE_ID)
        service_name = _text(attribution["serviceName"], maximum=256)
        service_namespace = _optional_text(
            attribution.get("serviceNamespace"), maximum=256
        )
        deployment_environment = _optional_text(
            attribution.get("deploymentEnvironment"), maximum=128
        )
        raw_refs = attribution["resourceRefs"]
        if (
            not isinstance(raw_refs, list)
            or len(raw_refs) > 64
            or len(set(raw_refs)) != len(raw_refs)
            or any(
                not isinstance(item, str) or not _RESOURCE_UID.fullmatch(item)
                for item in raw_refs
            )
        ):
            raise ValueError
        resource_refs = tuple(sorted(raw_refs))
    except (KeyError, TypeError, ValueError, OverflowError):
        raise InvalidAiAttributionInputError(
            "ai.attribution.usage.invalid"
        ) from None
    return {
        "usage_record_id": usage_id,
        "effective_at": effective_at,
        "effective_at_text": effective_text,
        "trace_id": trace_id,
        "service_name": service_name,
        "service_namespace": service_namespace,
        "deployment_environment": deployment_environment,
        "resource_refs": resource_refs,
    }


def _rule_matches(rule: AiAttributionRule, usage: Mapping[str, object]) -> bool:
    effective_at = usage["effective_at"]
    resource_refs = usage["resource_refs"]
    assert isinstance(effective_at, datetime)
    assert isinstance(resource_refs, tuple)
    return (
        rule.service_name == usage["service_name"]
        and (
            rule.service_namespace is None
            or rule.service_namespace == usage["service_namespace"]
        )
        and (
            rule.deployment_environment is None
            or rule.deployment_environment == usage["deployment_environment"]
        )
        and (
            rule.resource_ref is None
            or rule.resource_ref in resource_refs
        )
        and rule.effective_from <= effective_at
        and (rule.effective_until is None or effective_at < rule.effective_until)
    )


def _resolution(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError
    status = value.get("status")
    if status == "allocated":
        result = _closed(value, {"status", "ruleId", "application", "team"})
        _matched(result["ruleId"], _SAFE_ID)
        _organizational_unit(result["application"])
        _organizational_unit(result["team"])
    elif status == "unallocated":
        result = _closed(value, {"status", "reasonCode"})
        if result["reasonCode"] != "no-matching-rule":
            raise ValueError
    else:
        raise ValueError


def _attribution_actor(tenant_id: object, worker_id: object) -> ActorContext:
    if (
        not isinstance(tenant_id, str)
        or not _TENANT_ID.fullmatch(tenant_id)
        or not isinstance(worker_id, str)
        or not _WORKER_ID.fullmatch(worker_id)
    ):
        raise AiAttributionConfigurationError("ai.attribution.worker.invalid")
    return ActorContext(
        actor_id="ai-attribution-worker:" + worker_id,
        tenant_id=tenant_id,
        roles=("ai-attribution:resolve",),
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


def _matched(value: object, pattern: re.Pattern[str]) -> str:
    result = _text(value, maximum=256)
    if not pattern.fullmatch(result):
        raise ValueError
    return result


def _optional_text(value: object, *, maximum: int) -> str | None:
    return None if value is None else _text(value, maximum=maximum)


def _text(value: object, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or _SENSITIVE_TEXT.search(value)
    ):
        raise ValueError
    return value


def _integer(value: object, *, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError
    return value


def _timestamp(value: object) -> tuple[datetime, str]:
    text = _text(value, maximum=64)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    parsed = parsed.astimezone(timezone.utc)
    return parsed, parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


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


__all__ = [
    "ENGINE_VERSION",
    "AiAttributionConfigurationError",
    "AiAttributionPass",
    "AiAttributionRule",
    "AiAttributionService",
    "InvalidAiAttributionInputError",
    "ValidatedAiAttributionPolicy",
    "resolve_ai_usage_attribution",
    "validate_ai_attribution_policy",
    "validate_ai_attribution_source_binding",
    "validate_ai_usage_attribution_record",
]
