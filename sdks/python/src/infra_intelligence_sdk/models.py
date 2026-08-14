"""Public SDK models independent from server implementation classes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping


@dataclass(frozen=True)
class ResourceObservation:
    """Versioned resource envelope accepted by the ingestion API."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResourceObservation":
        """Create a lightweight SDK model after envelope checks."""

        if payload.get("apiVersion") != "iip.platform/v1alpha1":
            raise ValueError("unsupported resource apiVersion")
        if payload.get("kind") != "Resource":
            raise ValueError("resource kind must be Resource")
        if not isinstance(payload.get("metadata"), Mapping):
            raise ValueError("resource metadata must be an object")
        if not isinstance(payload.get("spec"), Mapping):
            raise ValueError("resource spec must be an object")
        return cls(dict(payload))

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable copy of the envelope."""

        return dict(self.payload)

