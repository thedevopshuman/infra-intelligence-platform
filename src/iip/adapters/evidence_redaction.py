"""Protected tenant evidence-redaction policy configuration."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from types import MappingProxyType


_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_POLICY_ID = re.compile(r"erp_[a-f0-9]{32}")
_VERSION = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[.][0-9]+")
_RULE_ID = re.compile(r"[a-z][a-z0-9-]{2,63}")
_EVIDENCE_TYPE = re.compile(r"[a-z][a-z0-9._/-]{2,127}")
_VALUE_CLASSES = frozenset({"email-address", "ipv4-address"})


class EvidenceRedactionPolicyConfigurationError(ValueError):
    """A protected redaction policy set is malformed or ambiguous."""


@dataclass(frozen=True)
class ValidatedEvidenceRedactionPolicy:
    """Immutable exact-tenant policy used by the redaction adapter."""

    document: Mapping[str, object]
    policy_id: str
    tenant_id: str
    version: str
    rules: Mapping[str, frozenset[str]]

    def value_classes_for(self, evidence_type: str) -> frozenset[str]:
        return self.rules.get(evidence_type, frozenset())


class EvidenceRedactionPolicyRegistry:
    """Resolve at most one content-addressed policy for an exact tenant."""

    MAX_CONFIGURATION_BYTES = 1_048_576
    MAX_POLICIES = 256

    def __init__(self, policies: Iterable[Mapping[str, object]] = ()) -> None:
        try:
            validated = tuple(
                validate_evidence_redaction_policy(policy) for policy in policies
            )
            tenant_ids = tuple(policy.tenant_id for policy in validated)
            if (
                tenant_ids != tuple(sorted(tenant_ids))
                or len(set(tenant_ids)) != len(tenant_ids)
                or len(validated) > self.MAX_POLICIES
            ):
                raise ValueError
        except (TypeError, ValueError):
            raise EvidenceRedactionPolicyConfigurationError(
                "evidence.redaction.configuration.invalid"
            ) from None
        self._policies = {policy.tenant_id: policy for policy in validated}

    @classmethod
    def from_json(cls, raw: str) -> "EvidenceRedactionPolicyRegistry":
        try:
            if (
                not isinstance(raw, str)
                or not 2 <= len(raw.encode("utf-8")) <= cls.MAX_CONFIGURATION_BYTES
            ):
                raise ValueError
            document = json.loads(raw)
            root = _closed(document, {"policies"})
            policies = root["policies"]
            if (
                not isinstance(policies, list)
                or not 1 <= len(policies) <= cls.MAX_POLICIES
                or any(not isinstance(item, dict) for item in policies)
            ):
                raise ValueError
            return cls(policies)
        except (
            EvidenceRedactionPolicyConfigurationError,
            TypeError,
            ValueError,
            UnicodeError,
            json.JSONDecodeError,
        ):
            raise EvidenceRedactionPolicyConfigurationError(
                "evidence.redaction.configuration.invalid"
            ) from None

    def get(self, tenant_id: str) -> ValidatedEvidenceRedactionPolicy | None:
        if not isinstance(tenant_id, str) or _TENANT_ID.fullmatch(tenant_id) is None:
            raise EvidenceRedactionPolicyConfigurationError(
                "evidence.redaction.tenant.invalid"
            )
        return self._policies.get(tenant_id)

    def documents(self) -> tuple[Mapping[str, object], ...]:
        return tuple(_thaw_object(policy.document) for policy in self._policies.values())


def derive_evidence_redaction_policy_id(document: object) -> str:
    """Calculate the content-derived ID after validating the remaining policy."""

    try:
        copied = _json_copy(document)
        root = _closed(copied, {"apiVersion", "kind", "metadata", "spec"})
        metadata = _closed(root["metadata"], {"id", "tenantId", "version"})
        tenant_id = _matched(metadata["tenantId"], _TENANT_ID)
        version = _matched(metadata["version"], _VERSION)
        expected = "erp_" + _digest(
            {"tenantId": tenant_id, "version": version, "spec": root["spec"]}
        )[:32]
        mutable_metadata = dict(metadata)
        mutable_metadata["id"] = expected
        copied["metadata"] = mutable_metadata
        validate_evidence_redaction_policy(copied)
        return expected
    except (KeyError, TypeError, ValueError, OverflowError):
        raise EvidenceRedactionPolicyConfigurationError(
            "evidence.redaction.configuration.invalid"
        ) from None


def validate_evidence_redaction_policy(
    document: object,
) -> ValidatedEvidenceRedactionPolicy:
    """Validate one closed, content-addressed additive privacy policy."""

    try:
        copied = _json_copy(document)
        root = _closed(copied, {"apiVersion", "kind", "metadata", "spec"})
        if (
            root["apiVersion"] != "iip.platform/v1alpha1"
            or root["kind"] != "EvidenceRedactionPolicy"
        ):
            raise ValueError
        metadata = _closed(root["metadata"], {"id", "tenantId", "version"})
        spec = _closed(root["spec"], {"rules"})
        policy_id = _matched(metadata["id"], _POLICY_ID)
        tenant_id = _matched(metadata["tenantId"], _TENANT_ID)
        version = _matched(metadata["version"], _VERSION)
        raw_rules = spec["rules"]
        if not isinstance(raw_rules, list) or not 1 <= len(raw_rules) <= 32:
            raise ValueError
        rule_ids: list[str] = []
        by_evidence_type: dict[str, frozenset[str]] = {}
        for raw_rule in raw_rules:
            rule = _closed(raw_rule, {"id", "evidenceTypes", "valueClasses"})
            rule_id = _matched(rule["id"], _RULE_ID)
            evidence_types = _sorted_values(
                rule["evidenceTypes"],
                maximum=32,
                pattern=_EVIDENCE_TYPE,
            )
            value_classes = _sorted_values(
                rule["valueClasses"],
                maximum=2,
                allowed=_VALUE_CLASSES,
            )
            for evidence_type in evidence_types:
                if evidence_type in by_evidence_type:
                    raise ValueError
                by_evidence_type[evidence_type] = frozenset(value_classes)
            rule_ids.append(rule_id)
        if rule_ids != sorted(rule_ids) or len(set(rule_ids)) != len(rule_ids):
            raise ValueError
        identity = {"tenantId": tenant_id, "version": version, "spec": spec}
        if policy_id != "erp_" + _digest(identity)[:32]:
            raise ValueError
        return ValidatedEvidenceRedactionPolicy(
            _freeze_mapping(root),
            policy_id,
            tenant_id,
            version,
            MappingProxyType(by_evidence_type),
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        raise EvidenceRedactionPolicyConfigurationError(
            "evidence.redaction.configuration.invalid"
        ) from None


def _sorted_values(
    value: object,
    *,
    maximum: int,
    pattern: re.Pattern[str] | None = None,
    allowed: frozenset[str] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError
    values = tuple(value)
    if (
        any(not isinstance(item, str) for item in values)
        or values != tuple(sorted(values))
        or len(set(values)) != len(values)
        or (pattern is not None and any(pattern.fullmatch(item) is None for item in values))
        or (allowed is not None and any(item not in allowed for item in values))
    ):
        raise ValueError
    return values


def _closed(value: object, required: Set[str]) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError
    return value


def _matched(value: object, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError
    return value


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _json_copy(value: object) -> dict[str, object]:
    copied = json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))
    if not isinstance(copied, dict):
        raise ValueError
    return copied


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(
        {key: _freeze_value(item) for key, item in value.items()}
    )


def _freeze_value(value: object) -> object:
    if isinstance(value, dict):
        return _freeze_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _thaw_object(value: object) -> dict[str, object]:
    thawed = _thaw_value(value)
    if not isinstance(thawed, dict):
        raise ValueError
    return thawed


def _thaw_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return value


__all__ = [
    "EvidenceRedactionPolicyConfigurationError",
    "EvidenceRedactionPolicyRegistry",
    "ValidatedEvidenceRedactionPolicy",
    "derive_evidence_redaction_policy_id",
    "validate_evidence_redaction_policy",
]
