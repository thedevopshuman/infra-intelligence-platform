"""Resource and event domain models used by the reference vertical slice."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
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


def _parsed_timestamp(value: str) -> datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(candidate)


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
class ObservationCursor:
    """Source-defined ordering and replay position for one observation."""

    source_id: str
    stream_id: str
    sequence: int
    mode: str
    resource_version: Optional[str] = None
    checkpoint: Optional[str] = None
    snapshot_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not re.fullmatch(
            r"[a-z][a-z0-9._-]{2,127}", self.source_id
        ):
            raise ContractError("metadata.observation.sourceId is invalid")
        if not isinstance(self.stream_id, str) or not re.fullmatch(
            r"obs_[a-f0-9]{32}", self.stream_id
        ):
            raise ContractError("metadata.observation.streamId is invalid")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise ContractError("metadata.observation.sequence must be an integer")
        if self.sequence < 0 or self.sequence > 9_007_199_254_740_991:
            raise ContractError("metadata.observation.sequence is out of range")
        if self.mode not in ("incremental", "reconciliation"):
            raise ContractError("metadata.observation.mode is invalid")
        if self.resource_version is not None:
            _required_string(
                self.resource_version,
                "metadata.observation.resourceVersion",
            )
        if self.checkpoint is not None:
            _required_string(self.checkpoint, "metadata.observation.checkpoint")
        if self.mode == "reconciliation":
            if not isinstance(self.snapshot_id, str) or not re.fullmatch(
                r"snap_[a-f0-9]{32}", self.snapshot_id
            ):
                raise ContractError(
                    "metadata.observation.snapshotId is required for reconciliation"
                )
        elif self.snapshot_id is not None:
            raise ContractError(
                "metadata.observation.snapshotId is prohibited for incremental observations"
            )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ObservationCursor":
        """Parse the public observation cursor."""

        source_id = _required_string(
            payload.get("sourceId"),
            "metadata.observation.sourceId",
        )
        stream_id = _required_string(
            payload.get("streamId"),
            "metadata.observation.streamId",
        )
        mode = _required_string(payload.get("mode"), "metadata.observation.mode")
        return cls(
            source_id=source_id,
            stream_id=stream_id,
            sequence=payload.get("sequence"),
            mode=mode,
            resource_version=payload.get("resourceVersion"),
            checkpoint=payload.get("checkpoint"),
            snapshot_id=payload.get("snapshotId"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the cursor without inventing provider ordering semantics."""

        result: Dict[str, Any] = {
            "sourceId": self.source_id,
            "streamId": self.stream_id,
            "sequence": self.sequence,
            "mode": self.mode,
        }
        if self.resource_version is not None:
            result["resourceVersion"] = self.resource_version
        if self.checkpoint is not None:
            result["checkpoint"] = self.checkpoint
        if self.snapshot_id is not None:
            result["snapshotId"] = self.snapshot_id
        return result


class ObservationDisposition(str, Enum):
    """Result of comparing an incoming observation with the latest projection."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    STALE = "stale"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class Resource:
    """Canonical resource observation accepted at the ingestion boundary."""

    identity: ResourceIdentity
    observed_at: str
    observation: Optional[ObservationCursor] = None
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
        observation_payload = metadata.get("observation")
        observation = None
        if observation_payload is not None:
            observation = ObservationCursor.from_dict(
                _mapping(observation_payload, "metadata.observation")
            )
        relationships = spec.get("relationships", [])
        if not isinstance(relationships, list) or not all(
            isinstance(item, Mapping) for item in relationships
        ):
            raise ContractError("spec.relationships must be an array of objects")
        for relationship in relationships:
            unknown_fields = set(relationship).difference(
                {"type", "target", "direction", "attributes"}
            )
            if unknown_fields:
                raise ContractError("spec.relationships contains unknown fields")
            relationship_type = _required_string(
                relationship.get("type"), "spec.relationships.type"
            )
            if not re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", relationship_type):
                raise ContractError("spec.relationships.type is invalid")
            target = _required_string(
                relationship.get("target"), "spec.relationships.target"
            )
            if len(target) > 1024:
                raise ContractError("spec.relationships.target is too long")
            direction = relationship.get("direction", "outbound")
            if direction not in ("outbound", "inbound"):
                raise ContractError("spec.relationships.direction is invalid")
            _mapping(relationship.get("attributes", {}), "spec.relationships.attributes")

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

        lifecycle = str(status.get("lifecycle", "active"))
        if lifecycle == "deleted" and (attributes or relationships):
            raise ContractError(
                "deleted resources must not contain attributes or relationships"
            )

        return cls(
            identity=identity,
            observed_at=_timestamp(metadata.get("observedAt"), "metadata.observedAt"),
            observation=observation,
            display_name=display_name,
            labels={str(key): str(value) for key, value in labels.items()},
            attributes=dict(attributes),
            relationships=tuple(dict(item) for item in relationships),
            health=str(status.get("health", "unknown")),
            lifecycle=lifecycle,
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

        metadata: Dict[str, Any] = {
            "uid": self.identity.uid,
            "tenantId": self.identity.tenant_id,
            "observedAt": self.observed_at,
            "labels": dict(self.labels),
        }
        if self.observation is not None:
            metadata["observation"] = self.observation.to_dict()

        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "Resource",
            "metadata": metadata,
            "spec": spec,
            "status": {"health": self.health, "lifecycle": self.lifecycle},
        }


@dataclass(frozen=True)
class ResourceRelationshipEdge:
    """Canonical orientation of one relationship from a latest resource projection."""

    edge_id: str
    observed_resource_uid: str
    relationship_type: str
    source_ref: str
    target_ref: str
    attributes: Mapping[str, Any]


def index_resource_relationships(resource: Resource) -> tuple[ResourceRelationshipEdge, ...]:
    """Create deterministic, de-duplicated graph edges from one resource projection."""

    edges: Dict[str, ResourceRelationshipEdge] = {}
    observed_uid = resource.identity.uid
    for relationship in resource.relationships:
        relationship_type = str(relationship["type"])
        target = str(relationship["target"])
        direction = relationship.get("direction", "outbound")
        attributes = dict(relationship.get("attributes", {}))
        if direction == "inbound":
            source_ref, target_ref = target, observed_uid
        else:
            source_ref, target_ref = observed_uid, target
        material = {
            "attributes": attributes,
            "observedResourceUid": observed_uid,
            "source": source_ref,
            "target": target_ref,
            "type": relationship_type,
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:32]
        edge = ResourceRelationshipEdge(
            edge_id=f"rel_{digest}",
            observed_resource_uid=observed_uid,
            relationship_type=relationship_type,
            source_ref=source_ref,
            target_ref=target_ref,
            attributes=attributes,
        )
        edges[edge.edge_id] = edge
    return tuple(edges[key] for key in sorted(edges))


def classify_resource_observation(
    current: Resource,
    incoming: Resource,
) -> ObservationDisposition:
    """Classify whether an incoming observation may replace the latest projection."""

    if current.identity != incoming.identity:
        raise ContractError("cannot compare observations for different resources")

    current_hash = PlatformEvent.canonical_hash(current.to_dict())
    incoming_hash = PlatformEvent.canonical_hash(incoming.to_dict())
    if current_hash == incoming_hash:
        return ObservationDisposition.DUPLICATE

    current_cursor = current.observation
    incoming_cursor = incoming.observation
    if current_cursor is None and incoming_cursor is None:
        current_time = _parsed_timestamp(current.observed_at)
        incoming_time = _parsed_timestamp(incoming.observed_at)
        if incoming_time > current_time:
            return ObservationDisposition.ACCEPTED
        if incoming_time < current_time:
            return ObservationDisposition.STALE
        return ObservationDisposition.CONFLICT
    if current_cursor is None:
        return ObservationDisposition.ACCEPTED
    if incoming_cursor is None:
        return ObservationDisposition.STALE
    if current_cursor.source_id != incoming_cursor.source_id:
        return ObservationDisposition.CONFLICT
    if current_cursor.stream_id != incoming_cursor.stream_id:
        if incoming_cursor.mode == "reconciliation":
            return ObservationDisposition.ACCEPTED
        return ObservationDisposition.CONFLICT
    if incoming_cursor.sequence > current_cursor.sequence:
        return ObservationDisposition.ACCEPTED
    if incoming_cursor.sequence < current_cursor.sequence:
        return ObservationDisposition.STALE
    return ObservationDisposition.CONFLICT


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

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PlatformEvent":
        """Parse the public structured CloudEvents envelope."""

        if payload.get("specversion") != "1.0":
            raise ContractError("specversion must be 1.0")
        content_type = payload.get("datacontenttype", "application/json")
        if content_type != "application/json":
            raise ContractError("datacontenttype must be application/json")
        correlation_id = payload.get("correlationid")
        causation_id = payload.get("causationid")
        if correlation_id is not None:
            correlation_id = _required_string(correlation_id, "correlationid")
        if causation_id is not None:
            causation_id = _required_string(causation_id, "causationid")
        return cls(
            event_id=_required_string(payload.get("id"), "id"),
            event_type=_required_string(payload.get("type"), "type"),
            source=_required_string(payload.get("source"), "source"),
            time=_timestamp(payload.get("time"), "time"),
            subject=_required_string(payload.get("subject"), "subject"),
            tenant_id=_required_string(payload.get("tenantid"), "tenantid"),
            data=dict(_mapping(payload.get("data"), "data")),
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

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
