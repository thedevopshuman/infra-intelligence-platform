"""Resource and event domain models used by the reference vertical slice."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional


class ContractError(ValueError):
    """Raised when an input violates a public contract invariant."""


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field_name} must be a non-empty string")
    return value.strip()


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{field_name} must be an object")
    return value


def _timestamp(value: Any, field_name: str) -> str:
    text = _required_string(value, field_name)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ContractError(f"{field_name} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{field_name} must include a timezone")
    return text


@dataclass(frozen=True)
class ResourceIdentity:
    """Tenant-scoped identity for one external infrastructure object."""

    tenant_id: str
    provider: str
    resource_type: str
    external_id: str

    def __post_init__(self) -> None:
        for name in ("tenant_id", "provider", "resource_type", "external_id"):
            _required_string(getattr(self, name), name)

    @property
    def uid(self) -> str:
        """Return the deterministic platform identity for this resource."""

        canonical = "\x1f".join(
            (self.tenant_id, self.provider, self.resource_type, self.external_id)
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
        return f"res_{digest}"


@dataclass(frozen=True)
class Resource:
    """Canonical resource observation accepted at the ingestion boundary."""

    identity: ResourceIdentity
    observed_at: str
    display_name: Optional[str] = None
    labels: Mapping[str, str] = field(default_factory=dict)
    attributes: Mapping[str, Any] = field(default_factory=dict)
    relationships: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    health: str = "unknown"
    lifecycle: str = "active"

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Resource":
        """Parse the versioned resource envelope and enforce core invariants."""

        if payload.get("apiVersion") != "iip.platform/v1alpha1":
            raise ContractError("apiVersion must be iip.platform/v1alpha1")
        if payload.get("kind") != "Resource":
            raise ContractError("kind must be Resource")

        metadata = _mapping(payload.get("metadata"), "metadata")
        spec = _mapping(payload.get("spec"), "spec")
        status = _mapping(payload.get("status", {}), "status")
        labels = _mapping(metadata.get("labels", {}), "metadata.labels")
        attributes = _mapping(spec.get("attributes", {}), "spec.attributes")
        relationships = spec.get("relationships", [])
        if not isinstance(relationships, list) or not all(
            isinstance(item, Mapping) for item in relationships
        ):
            raise ContractError("spec.relationships must be an array of objects")

        identity = ResourceIdentity(
            tenant_id=_required_string(metadata.get("tenantId"), "metadata.tenantId"),
            provider=_required_string(spec.get("provider"), "spec.provider"),
            resource_type=_required_string(spec.get("type"), "spec.type"),
            external_id=_required_string(spec.get("externalId"), "spec.externalId"),
        )

        supplied_uid = metadata.get("uid")
        if supplied_uid is not None and supplied_uid != identity.uid:
            raise ContractError("metadata.uid does not match the deterministic identity")

        display_name = spec.get("displayName")
        if display_name is not None:
            display_name = _required_string(display_name, "spec.displayName")

        return cls(
            identity=identity,
            observed_at=_timestamp(metadata.get("observedAt"), "metadata.observedAt"),
            display_name=display_name,
            labels={str(key): str(value) for key, value in labels.items()},
            attributes=dict(attributes),
            relationships=tuple(dict(item) for item in relationships),
            health=str(status.get("health", "unknown")),
            lifecycle=str(status.get("lifecycle", "active")),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this resource to the public envelope."""

        spec: Dict[str, Any] = {
            "provider": self.identity.provider,
            "type": self.identity.resource_type,
            "externalId": self.identity.external_id,
            "attributes": dict(self.attributes),
            "relationships": [dict(item) for item in self.relationships],
        }
        if self.display_name is not None:
            spec["displayName"] = self.display_name

        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "Resource",
            "metadata": {
                "uid": self.identity.uid,
                "tenantId": self.identity.tenant_id,
                "observedAt": self.observed_at,
                "labels": dict(self.labels),
            },
            "spec": spec,
            "status": {"health": self.health, "lifecycle": self.lifecycle},
        }


@dataclass(frozen=True)
class PlatformEvent:
    """CloudEvents-compatible record with platform tenancy extensions."""

    event_id: str
    event_type: str
    source: str
    time: str
    subject: str
    tenant_id: str
    data: Mapping[str, Any]
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this event using the structured CloudEvents JSON shape."""

        event: Dict[str, Any] = {
            "specversion": "1.0",
            "id": self.event_id,
            "type": self.event_type,
            "source": self.source,
            "time": self.time,
            "subject": self.subject,
            "datacontenttype": "application/json",
            "tenantid": self.tenant_id,
            "data": dict(self.data),
        }
        if self.correlation_id:
            event["correlationid"] = self.correlation_id
        if self.causation_id:
            event["causationid"] = self.causation_id
        return event

    @staticmethod
    def canonical_hash(data: Mapping[str, Any]) -> str:
        """Hash evidence or payload data without depending on map insertion order."""

        encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def now() -> str:
        """Return a UTC RFC 3339 timestamp."""

        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

