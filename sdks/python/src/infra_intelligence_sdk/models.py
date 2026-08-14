"""Public SDK models independent from server implementation classes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


API_VERSION = "iip.platform/v1alpha1"


@dataclass(frozen=True)
class ResourceObservationCursor:
    """Source ordering metadata carried by a resource observation."""

    source_id: str
    stream_id: str
    sequence: int
    mode: str
    resource_version: Optional[str] = None
    checkpoint: Optional[str] = None
    snapshot_id: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResourceObservationCursor":
        """Parse the public cursor without importing server domain models."""

        source_id = payload.get("sourceId")
        stream_id = payload.get("streamId")
        sequence = payload.get("sequence")
        mode = payload.get("mode")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("observation sourceId must be a non-empty string")
        if not isinstance(stream_id, str) or not stream_id.startswith("obs_"):
            raise ValueError("observation streamId is invalid")
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise ValueError("observation sequence must be an integer")
        if mode not in ("incremental", "reconciliation"):
            raise ValueError("observation mode is invalid")
        snapshot_id = payload.get("snapshotId")
        if mode == "reconciliation" and not isinstance(snapshot_id, str):
            raise ValueError("reconciliation observation requires snapshotId")
        if mode == "incremental" and snapshot_id is not None:
            raise ValueError("incremental observation prohibits snapshotId")

        return cls(
            source_id=source_id,
            stream_id=stream_id,
            sequence=sequence,
            mode=mode,
            resource_version=payload.get("resourceVersion"),
            checkpoint=payload.get("checkpoint"),
            snapshot_id=snapshot_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return the JSON representation used inside resource metadata."""

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


def _validate_envelope(
    payload: Mapping[str, Any],
    *,
    kind: str,
    label: str,
) -> Dict[str, Any]:
    if payload.get("apiVersion") != API_VERSION:
        raise ValueError(f"unsupported {label} apiVersion")
    if payload.get("kind") != kind:
        raise ValueError(f"{label} kind must be {kind}")
    if not isinstance(payload.get("metadata"), Mapping):
        raise ValueError(f"{label} metadata must be an object")
    if not isinstance(payload.get("spec"), Mapping):
        raise ValueError(f"{label} spec must be an object")
    return dict(payload)


@dataclass(frozen=True)
class ResourceObservation:
    """Versioned resource envelope accepted by the ingestion API."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResourceObservation":
        """Create a lightweight SDK model after envelope checks."""

        return cls(_validate_envelope(payload, kind="Resource", label="resource"))

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable copy of the envelope."""

        return dict(self.payload)


@dataclass(frozen=True)
class ResourceCollectionRequest:
    """Bounded, tenant-scoped request passed to a resource observer."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResourceCollectionRequest":
        """Create a collection request after versioned-envelope checks."""

        return cls(
            _validate_envelope(
                payload,
                kind="ResourceCollectionRequest",
                label="resource collection request",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable copy of the envelope."""

        return dict(self.payload)


@dataclass(frozen=True)
class ResourceCollectionResult:
    """Batch of canonical observations and its explicit completion state."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResourceCollectionResult":
        """Create a collection result after versioned-envelope checks."""

        return cls(
            _validate_envelope(
                payload,
                kind="ResourceCollectionResult",
                label="resource collection result",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable copy of the envelope."""

        return dict(self.payload)


@dataclass(frozen=True)
class ResourceNeighborhood:
    """Depth-one page of current graph nodes and canonical edges."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResourceNeighborhood":
        """Create a neighborhood model after versioned-envelope checks."""

        return cls(
            _validate_envelope(
                payload,
                kind="ResourceNeighborhood",
                label="resource neighborhood",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable copy of the envelope."""

        return dict(self.payload)


@dataclass(frozen=True)
class ResourceTimeline:
    """Ascending page of immutable observations for one resource."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResourceTimeline":
        """Create a timeline model after versioned-envelope checks."""

        return cls(
            _validate_envelope(
                payload,
                kind="ResourceTimeline",
                label="resource timeline",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable copy of the envelope."""

        return dict(self.payload)


@dataclass(frozen=True)
class Evidence:
    """Public metadata and provenance for one immutable evidence artifact."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Evidence":
        """Create an evidence model after versioned-envelope checks."""

        return cls(_validate_envelope(payload, kind="Evidence", label="evidence"))

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable copy of the envelope."""

        return dict(self.payload)


@dataclass(frozen=True)
class InvestigationRequest:
    """Bounded, tenant- and actor-scoped investigation input."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InvestigationRequest":
        """Create an investigation request after envelope checks."""

        return cls(
            _validate_envelope(
                payload,
                kind="InvestigationRequest",
                label="investigation request",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable copy of the envelope."""

        return dict(self.payload)


@dataclass(frozen=True)
class InvestigationReport:
    """Immutable terminal result of one bounded investigation."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InvestigationReport":
        """Create an investigation report after envelope checks."""

        return cls(
            _validate_envelope(
                payload,
                kind="InvestigationReport",
                label="investigation report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable copy of the envelope."""

        return dict(self.payload)


@dataclass(frozen=True)
class EvaluationScenario:
    """Replayable incident fixtures and their offline scoring oracle."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvaluationScenario":
        """Create an evaluation scenario after envelope checks."""

        return cls(
            _validate_envelope(
                payload,
                kind="EvaluationScenario",
                label="evaluation scenario",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable copy of the envelope."""

        return dict(self.payload)
