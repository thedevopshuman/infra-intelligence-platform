"""Public SDK models independent from server implementation classes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping


API_VERSION = "iip.platform/v1alpha1"


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
