"""Public SDK models independent from server implementation classes."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Literal, Mapping, Optional, TypeAlias
from urllib.parse import urlsplit


API_VERSION = "iip.platform/v1alpha1"
PILOT_READINESS_API_VERSION = "iip.platform/v1alpha2"


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
    api_version: str = API_VERSION,
) -> Dict[str, Any]:
    if payload.get("apiVersion") != api_version:
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

    @property
    def resume(self) -> Optional[Mapping[str, Any]]:
        """Return the opaque host-committed resume state, when supplied."""

        spec = self.payload.get("spec")
        value = spec.get("resume") if isinstance(spec, Mapping) else None
        return dict(value) if isinstance(value, Mapping) else None


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

    @property
    def provider_cursors(self) -> Mapping[str, str]:
        """Return the complete provider cursor map without interpreting it."""

        spec = self.payload.get("spec")
        completion = spec.get("completion") if isinstance(spec, Mapping) else None
        value = (
            completion.get("providerCursors")
            if isinstance(completion, Mapping)
            else None
        )
        return dict(value) if isinstance(value, Mapping) else {}


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
class IngestionFreshnessReport:
    """Point-in-time SLI evaluation for one tenant-scoped ingestion source."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IngestionFreshnessReport":
        return cls(
            _validate_envelope(
                payload,
                kind="IngestionFreshnessReport",
                label="ingestion freshness report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class TelemetryExportHealthReport:
    """Process-local OTLP metric and trace delivery state for operators."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "TelemetryExportHealthReport":
        return cls(
            _validate_envelope(
                payload,
                kind="TelemetryExportHealthReport",
                label="telemetry export health report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class TelemetryDeploymentExportHealthReport:
    """Recent API, worker, and receiver OTLP delivery state for administrators."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "TelemetryDeploymentExportHealthReport":
        return cls(
            _validate_envelope(
                payload,
                kind="TelemetryDeploymentExportHealthReport",
                label="telemetry deployment export health report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class TelemetryExportSloReport:
    """Rolling deployment OTLP export-attempt objective for administrators."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "TelemetryExportSloReport":
        return cls(
            _validate_envelope(
                payload,
                kind="TelemetryExportSloReport",
                label="telemetry export SLO report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class TelemetryExportBurnRateReport:
    """Multi-window deployment OTLP export-attempt burn rate for administrators."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "TelemetryExportBurnRateReport":
        return cls(
            _validate_envelope(
                payload,
                kind="TelemetryExportBurnRateReport",
                label="telemetry export burn-rate report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CollectorQueueLossReport:
    """Collector-observed sending-queue depth and send-loss objective.

    Distinct from TelemetryExportBurnRateReport and TelemetryExportSloReport,
    which measure IIP's own outbound exporter attempts rather than the
    customer OpenTelemetry Collector's internal queue for its pipeline to
    IIP's receiver.
    """

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CollectorQueueLossReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CollectorQueueLossReport",
                label="collector queue/loss report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class EventDeliveryHealthReport:
    """Tenant-scoped outbox backlog and quarantine state for operators."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "EventDeliveryHealthReport":
        return cls(
            _validate_envelope(
                payload,
                kind="EventDeliveryHealthReport",
                label="event delivery health report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class EventDeliverySloReport:
    """Tenant-scoped rolling publication objective for operators."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "EventDeliverySloReport":
        return cls(
            _validate_envelope(
                payload,
                kind="EventDeliverySloReport",
                label="event delivery SLO report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class InvestigationCompletionSloReport:
    """Tenant-scoped rolling useful-completion objective for operators."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationCompletionSloReport":
        return cls(
            _validate_envelope(
                payload,
                kind="InvestigationCompletionSloReport",
                label="investigation completion SLO report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class EvidenceRetentionReport:
    """Tenant-scoped Evidence artifact lifecycle state for operators."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "EvidenceRetentionReport":
        return cls(
            _validate_envelope(
                payload,
                kind="EvidenceRetentionReport",
                label="evidence retention report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class RuntimeVersionReport:
    """Authenticated application, contract, storage, build, and deployment identity."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RuntimeVersionReport":
        return cls(
            _validate_envelope(
                payload,
                kind="RuntimeVersionReport",
                label="runtime version report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ReleaseQualificationReport:
    """Environment-scoped packaged install and N-1 upgrade evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReleaseQualificationReport":
        if payload.get("apiVersion") != "iip.dev/v1alpha1":
            raise ValueError("unsupported release qualification report apiVersion")
        if payload.get("kind") != "ReleaseQualificationReport":
            raise ValueError(
                "release qualification report kind must be ReleaseQualificationReport"
            )
        if not isinstance(payload.get("metadata"), Mapping):
            raise ValueError("release qualification report metadata must be an object")
        if not isinstance(payload.get("spec"), Mapping):
            raise ValueError("release qualification report spec must be an object")
        return cls(dict(payload))

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ReleaseReadinessReport:
    """Aggregate local candidate evidence with explicit external gates."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReleaseReadinessReport":
        if payload.get("apiVersion") != "iip.dev/v1alpha1":
            raise ValueError("unsupported release readiness report apiVersion")
        if payload.get("kind") != "ReleaseReadinessReport":
            raise ValueError(
                "release readiness report kind must be ReleaseReadinessReport"
            )
        if not isinstance(payload.get("metadata"), Mapping):
            raise ValueError("release readiness report metadata must be an object")
        if not isinstance(payload.get("spec"), Mapping):
            raise ValueError("release readiness report spec must be an object")
        return cls(dict(payload))

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiFinopsRuntimeCompatibilityReport:
    """Source-bound local AI FinOps reference-flow evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "AiFinopsRuntimeCompatibilityReport":
        if payload.get("apiVersion") != "iip.dev/v1alpha1":
            raise ValueError(
                "unsupported AI FinOps runtime compatibility report apiVersion"
            )
        if payload.get("kind") != "AiFinopsRuntimeCompatibilityReport":
            raise ValueError(
                "AI FinOps runtime compatibility report kind must be "
                "AiFinopsRuntimeCompatibilityReport"
            )
        if not isinstance(payload.get("metadata"), Mapping):
            raise ValueError(
                "AI FinOps runtime compatibility report metadata must be an object"
            )
        if not isinstance(payload.get("spec"), Mapping):
            raise ValueError(
                "AI FinOps runtime compatibility report spec must be an object"
            )
        return cls(dict(payload))

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiFinopsSustainedLoadProfile:
    """Closed local selection for one bounded AI economics load run."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "AiFinopsSustainedLoadProfile":
        return cls(
            _validate_envelope(
                payload,
                kind="AiFinopsSustainedLoadProfile",
                label="AI FinOps sustained load profile",
                api_version="iip.dev/v1alpha1",
            )
        )

    @property
    def workload(self) -> Mapping[str, Any]:
        spec = self.payload.get("spec")
        value = spec.get("workload") if isinstance(spec, Mapping) else None
        return value if isinstance(value, Mapping) else {}

    @property
    def objectives(self) -> Mapping[str, Any]:
        spec = self.payload.get("spec")
        value = spec.get("objectives") if isinstance(spec, Mapping) else None
        return value if isinstance(value, Mapping) else {}

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiFinopsSustainedLoadQualificationReport:
    """Source- and generation-bound aggregate local AI economics load evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "AiFinopsSustainedLoadQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="AiFinopsSustainedLoadQualificationReport",
                label="AI FinOps sustained load qualification report",
                api_version="iip.dev/v1alpha1",
            )
        )

    @property
    def status(self) -> Optional[str]:
        spec = self.payload.get("spec")
        value = spec.get("status") if isinstance(spec, Mapping) else None
        return value if isinstance(value, str) else None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class DeploymentDiagnosticReport:
    """Privacy-minimized point-in-time Kubernetes support evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DeploymentDiagnosticReport":
        return cls(
            _validate_envelope(
                payload,
                kind="DeploymentDiagnosticReport",
                label="deployment diagnostic report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


CustomerDeploymentPreflightProfileName: TypeAlias = Literal[
    "production-core-v1",
    "production-ai-finops-v0",
    "production-core-v2",
    "production-ai-finops-v1",
]

CustomerDeploymentPreflightCheckId: TypeAlias = Literal[
    "helm-render",
    "immutable-image",
    "published-image-repository",
    "api-redundancy",
    "worker-enrollment",
    "worker-redundancy",
    "external-database",
    "database-transport-security",
    "controlled-migrations",
    "oidc-authentication",
    "external-policy",
    "workload-identity-broker",
    "tls-ingress",
    "network-isolation",
    "pod-disruption-budget",
    "hard-topology-spread",
    "scheduled-backup",
    "evidence-retention",
    "platform-telemetry",
    "operational-alert-policy",
    "evidence-backends",
    "service-account-isolation",
    "test-fixtures-denied",
    "ai-usage-intake",
    "ai-receiver-mtls",
    "ai-receiver-redundancy",
    "ai-cost-allocation-savings",
    "ai-price-catalog-qualification",
    "collector-loss-objective",
    "cluster-api",
    "operational-alert-api",
    "operational-alert-namespace",
    "referenced-dependencies",
]


@dataclass(frozen=True)
class CustomerDeploymentPreflightReport:
    """Source- and configuration-bound pre-install customer evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerDeploymentPreflightReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerDeploymentPreflightReport",
                label="customer deployment preflight report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerContinuityQualificationReport:
    """Minimized customer control-plane pod-Eviction continuity evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerContinuityQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerContinuityQualificationReport",
                label="customer continuity qualification report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerProcessingContinuityQualificationReport:
    """Customer worker and receiver pod-Eviction processing evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerProcessingContinuityQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerProcessingContinuityQualificationReport",
                label="customer processing continuity qualification report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerProcessingQualificationProfile:
    """Protected input for one customer processing-continuity run."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerProcessingQualificationProfile":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerProcessingQualificationProfile",
                label="customer processing qualification profile",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerSustainedWorkloadProfile:
    """Protected selection for one bounded customer core workload run."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerSustainedWorkloadProfile":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerSustainedWorkloadProfile",
                label="customer sustained workload profile",
            )
        )

    @property
    def release(self) -> Mapping[str, Any]:
        spec = self.payload.get("spec")
        value = spec.get("release") if isinstance(spec, Mapping) else None
        return value if isinstance(value, Mapping) else {}

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerSustainedWorkloadQualificationReport:
    """Minimized bounded API, OTLP, and investigation workload evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerSustainedWorkloadQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerSustainedWorkloadQualificationReport",
                label="customer sustained workload qualification report",
            )
        )

    @property
    def status(self) -> Optional[str]:
        spec = self.payload.get("spec")
        value = spec.get("status") if isinstance(spec, Mapping) else None
        return value if isinstance(value, str) else None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerFailureOverlapProfile:
    """Protected review for one private-pilot planned-failure overlap run."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerFailureOverlapProfile":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerFailureOverlapProfile",
                label="customer failure overlap profile",
            )
        )

    @property
    def release(self) -> Mapping[str, Any]:
        spec = self.payload.get("spec")
        value = spec.get("release") if isinstance(spec, Mapping) else None
        return value if isinstance(value, Mapping) else {}

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerFailureOverlapQualificationReport:
    """Minimized source-bound sustained-traffic and planned-failure evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerFailureOverlapQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerFailureOverlapQualificationReport",
                label="customer failure overlap qualification report",
            )
        )

    @property
    def status(self) -> Optional[str]:
        spec = self.payload.get("spec")
        value = spec.get("status") if isinstance(spec, Mapping) else None
        return value if isinstance(value, str) else None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerPostgreSQLContinuityProfile:
    """Protected input for one customer PostgreSQL promotion observation."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerPostgreSQLContinuityProfile":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerPostgreSQLContinuityProfile",
                label="customer PostgreSQL continuity profile",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerPostgreSQLContinuityQualificationReport:
    """Minimized evidence for a customer PostgreSQL primary promotion."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerPostgreSQLContinuityQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerPostgreSQLContinuityQualificationReport",
                label="customer PostgreSQL continuity qualification report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerOidcQualificationProfile:
    """Protected expected customer OIDC identity and endpoint policy."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerOidcQualificationProfile":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerOidcQualificationProfile",
                label="customer OIDC qualification profile",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerOidcQualificationReport:
    """Minimized customer OIDC verifier and browser-prerequisite evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerOidcQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerOidcQualificationReport",
                label="customer OIDC qualification report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerOperationalAlertQualificationProfile:
    """Protected customer alert evaluator and notification-route selection."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerOperationalAlertQualificationProfile":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerOperationalAlertQualificationProfile",
                label="customer operational alert qualification profile",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerOperationalAlertQualificationReport:
    """Minimized proof for one customer alert evaluation and notification route."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerOperationalAlertQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerOperationalAlertQualificationReport",
                label="customer operational alert qualification report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerCredentialBrokerQualificationProfile:
    """Protected reviewed customer credential-broker authority cases."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerCredentialBrokerQualificationProfile":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerCredentialBrokerQualificationProfile",
                label="customer credential broker qualification profile",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerCredentialBrokerQualificationReport:
    """Minimized evidence for customer credential-broker authority behavior."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerCredentialBrokerQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerCredentialBrokerQualificationReport",
                label="customer credential broker qualification report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerGithubContextQualificationProfile:
    """Protected exact customer GitHub document qualification input."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerGithubContextQualificationProfile":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerGithubContextQualificationProfile",
                label="customer GitHub context qualification profile",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerGithubContextQualificationReport:
    """Minimized exact customer GitHub context interoperability evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerGithubContextQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerGithubContextQualificationReport",
                label="customer GitHub context qualification report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerBedrockQualificationProfile:
    """Protected exact customer Bedrock live qualification input."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerBedrockQualificationProfile":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerBedrockQualificationProfile",
                label="customer Bedrock qualification profile",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerBedrockQualificationReport:
    """Minimized exact customer Bedrock live-provider evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerBedrockQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerBedrockQualificationReport",
                label="customer Bedrock qualification report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerAiFinopsPrerequisiteProfile:
    """Protected exact customer AI FinOps prerequisite selection."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerAiFinopsPrerequisiteProfile":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerAiFinopsPrerequisiteProfile",
                label="customer AI FinOps prerequisite profile",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerAiFinopsPrerequisiteReport:
    """Minimized current customer AI FinOps prerequisite evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerAiFinopsPrerequisiteReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerAiFinopsPrerequisiteReport",
                label="customer AI FinOps prerequisite report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerAiFinopsFlowQualificationProfile:
    """Protected selection for one customer Bedrock-to-dashboard flow."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerAiFinopsFlowQualificationProfile":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerAiFinopsFlowQualificationProfile",
                label="customer AI FinOps flow qualification profile",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerAiFinopsFlowQualificationReport:
    """Minimized evidence for one exact customer AI FinOps invocation flow."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerAiFinopsFlowQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerAiFinopsFlowQualificationReport",
                label="customer AI FinOps flow qualification report",
            )
        )

    @property
    def status(self) -> Optional[str]:
        spec = self.payload.get("spec")
        value = spec.get("status") if isinstance(spec, Mapping) else None
        return value if isinstance(value, str) else None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerPilotReadinessProfile:
    """Protected exact release and customer-environment pilot selection."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CustomerPilotReadinessProfile":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerPilotReadinessProfile",
                label="customer pilot readiness profile",
                api_version=PILOT_READINESS_API_VERSION,
            )
        )

    @property
    def release(self) -> Mapping[str, Any]:
        spec = self.payload.get("spec")
        value = spec.get("release") if isinstance(spec, Mapping) else None
        return value if isinstance(value, Mapping) else {}

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerPilotReadinessReport:
    """Minimized preflight evidence for one private design-partner candidate."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CustomerPilotReadinessReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerPilotReadinessReport",
                label="customer pilot readiness report",
                api_version=PILOT_READINESS_API_VERSION,
            )
        )

    @property
    def status(self) -> Optional[str]:
        spec = self.payload.get("spec")
        value = spec.get("status") if isinstance(spec, Mapping) else None
        return value if isinstance(value, str) else None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerOtlpReceiverQualificationProfile:
    """Protected exact signal catalog for customer receiver qualification."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerOtlpReceiverQualificationProfile":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerOtlpReceiverQualificationProfile",
                label="customer OTLP receiver qualification profile",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerOtlpReceiverQualificationReport:
    """Minimized pinned-Collector customer receiver evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerOtlpReceiverQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerOtlpReceiverQualificationReport",
                label="customer OTLP receiver qualification report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerPolicyQualificationProfile:
    """Protected reviewed customer policy endpoint and decision cases."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerPolicyQualificationProfile":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerPolicyQualificationProfile",
                label="customer policy qualification profile",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerPolicyQualificationReport:
    """Minimized evidence for reviewed customer policy decisions."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerPolicyQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerPolicyQualificationReport",
                label="customer policy qualification report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class CustomerDeploymentQualificationReport:
    """Exact-release install, identity, receiver, continuity, and database chain."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CustomerDeploymentQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="CustomerDeploymentQualificationReport",
                label="customer deployment qualification report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ControlPlaneLoadQualificationReport:
    """Bounded fixed-rate customer control-plane read-load evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "ControlPlaneLoadQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="ControlPlaneLoadQualificationReport",
                label="control-plane load qualification report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class KubernetesAvailabilityQualificationReport:
    """Source-bound API, receiver, and worker drain-continuity evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "KubernetesAvailabilityQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="KubernetesAvailabilityQualificationReport",
                label="Kubernetes availability qualification report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PostgreSQLRecoveryQualificationReport:
    """Source-bound complete-schema logical backup and restore evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "PostgreSQLRecoveryQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="PostgreSQLRecoveryQualificationReport",
                label="PostgreSQL recovery qualification report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PostgreSQLContinuityQualificationReport:
    """Source-bound physical replication, promotion, and PITR evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "PostgreSQLContinuityQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="PostgreSQLContinuityQualificationReport",
                label="PostgreSQL continuity qualification report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class SessionContext:
    """Non-secret identity context derived from the client's credential."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SessionContext":
        return cls(
            _validate_envelope(
                payload,
                kind="SessionContext",
                label="session context",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ConsoleAuthenticationConfiguration:
    """Public, non-secret browser authentication discovery document."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "ConsoleAuthenticationConfiguration":
        if (
            not isinstance(payload, Mapping)
            or set(payload) != {"apiVersion", "kind", "spec"}
            or payload.get("apiVersion") != API_VERSION
            or payload.get("kind") != "ConsoleAuthenticationConfiguration"
            or not isinstance(payload.get("spec"), Mapping)
        ):
            raise ValueError("console authentication configuration is invalid")
        document = dict(payload)
        spec = document["spec"]
        assert isinstance(spec, Mapping)
        mode = spec.get("mode")
        if mode not in ("local-token", "access-token", "oidc-pkce"):
            raise ValueError("console authentication mode is invalid")
        if mode != "oidc-pkce":
            if set(spec) != {"mode"}:
                raise ValueError("console OIDC configuration is inconsistent")
            return cls(document)
        profile = spec.get("oidc")
        expected_profile_keys = {
            "issuer",
            "clientId",
            "authorizationEndpoint",
            "tokenEndpoint",
            "redirectUri",
            "scopes",
            "providerLabel",
            "pkceMethod",
        }
        if set(spec) != {"mode", "oidc"} or not isinstance(profile, Mapping):
            raise ValueError("console OIDC configuration is inconsistent")
        if set(profile) != expected_profile_keys:
            raise ValueError("console OIDC profile is invalid")
        try:
            url_values = (
                profile["issuer"],
                profile["authorizationEndpoint"],
                profile["tokenEndpoint"],
                profile["redirectUri"],
            )
            if any(
                not isinstance(value, str)
                or not 1 <= len(value) <= 2048
                or re.search(r"[\x00-\x20\x7f]", value) is not None
                for value in url_values
            ):
                raise ValueError
            issuer = urlsplit(profile["issuer"])
            authorization_endpoint = urlsplit(profile["authorizationEndpoint"])
            token_endpoint = urlsplit(profile["tokenEndpoint"])
            redirect_uri = urlsplit(profile["redirectUri"])
            for parsed in (
                issuer,
                authorization_endpoint,
                token_endpoint,
                redirect_uri,
            ):
                parsed.port
        except (KeyError, TypeError, ValueError):
            raise ValueError("console OIDC profile is invalid") from None
        endpoints = (authorization_endpoint, token_endpoint)
        scopes = profile["scopes"]
        redirect_is_safe = (
            redirect_uri.scheme == "https"
            or (
                redirect_uri.scheme == "http"
                and redirect_uri.hostname in ("localhost", "127.0.0.1")
            )
        )
        if (
            issuer.scheme != "https"
            or not issuer.hostname
            or issuer.username is not None
            or issuer.password is not None
            or issuer.query
            or issuer.fragment
            or any(
                endpoint.scheme != "https"
                or not endpoint.hostname
                or endpoint.username is not None
                or endpoint.password is not None
                or endpoint.query
                or endpoint.fragment
                for endpoint in endpoints
            )
            or not redirect_is_safe
            or not redirect_uri.hostname
            or redirect_uri.username is not None
            or redirect_uri.password is not None
            or redirect_uri.query
            or redirect_uri.fragment
            or redirect_uri.path not in ("/console", "/console/")
            or not isinstance(profile["clientId"], str)
            or re.fullmatch(r"[^\s\x00-\x1f]{1,256}", profile["clientId"])
            is None
            or not isinstance(scopes, list)
            or not 1 <= len(scopes) <= 32
            or any(
                not isinstance(scope, str)
                or re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", scope) is None
                for scope in scopes
            )
            or len(set(scopes)) != len(scopes)
            or "openid" not in scopes
            or not isinstance(profile["providerLabel"], str)
            or re.fullmatch(r"[^\x00-\x1f\x7f]{1,64}", profile["providerLabel"])
            is None
            or profile["pkceMethod"] != "S256"
        ):
            raise ValueError("console OIDC profile is invalid")
        return cls(document)

    def to_dict(self) -> Dict[str, Any]:
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
class EvidenceRedactionPolicy:
    """Protected exact-tenant additive evidence privacy policy."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceRedactionPolicy":
        return cls(
            _validate_envelope(
                payload,
                kind="EvidenceRedactionPolicy",
                label="evidence redaction policy",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class TelemetryEvidenceRequest:
    """Bounded backend-neutral request for tenant-scoped metric evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TelemetryEvidenceRequest":
        return cls(
            _validate_envelope(
                payload,
                kind="TelemetryEvidenceRequest",
                label="telemetry evidence request",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class TelemetryEvidenceResult:
    """Normalized metric series stored as an immutable evidence artifact."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TelemetryEvidenceResult":
        return cls(
            _validate_envelope(
                payload,
                kind="TelemetryEvidenceResult",
                label="telemetry evidence result",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class LogEvidenceRequest:
    """Bounded backend-neutral request for tenant-scoped log evidence."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LogEvidenceRequest":
        return cls(
            _validate_envelope(
                payload,
                kind="LogEvidenceRequest",
                label="log evidence request",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class LogEvidenceResult:
    """Normalized log records stored as an immutable evidence artifact."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LogEvidenceResult":
        return cls(
            _validate_envelope(
                payload,
                kind="LogEvidenceResult",
                label="log evidence result",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class KubernetesEventEvidenceRequest:
    """Bounded backend-neutral request for tenant-scoped Kubernetes Events."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "KubernetesEventEvidenceRequest":
        return cls(
            _validate_envelope(
                payload,
                kind="KubernetesEventEvidenceRequest",
                label="Kubernetes Event evidence request",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class KubernetesEventEvidenceResult:
    """Normalized Kubernetes Events stored as an immutable evidence artifact."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "KubernetesEventEvidenceResult":
        return cls(
            _validate_envelope(
                payload,
                kind="KubernetesEventEvidenceResult",
                label="Kubernetes Event evidence result",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ResourceChangeEvidenceRequest:
    """Bounded request for changes in tenant-scoped resource history."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResourceChangeEvidenceRequest":
        return cls(
            _validate_envelope(
                payload,
                kind="ResourceChangeEvidenceRequest",
                label="resource change evidence request",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ResourceChangeEvidenceResult:
    """Normalized resource changes stored as an immutable evidence artifact."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResourceChangeEvidenceResult":
        return cls(
            _validate_envelope(
                payload,
                kind="ResourceChangeEvidenceResult",
                label="resource change evidence result",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ContextEvidenceRequest:
    """Bounded request for allowlisted repository and runbook context."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ContextEvidenceRequest":
        return cls(
            _validate_envelope(
                payload,
                kind="ContextEvidenceRequest",
                label="context evidence request",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ContextEvidenceResult:
    """Normalized untrusted context stored as an immutable evidence artifact."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ContextEvidenceResult":
        return cls(
            _validate_envelope(
                payload,
                kind="ContextEvidenceResult",
                label="context evidence result",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class OtlpMetricsEvidence:
    """Normalized artifact produced by a tenant-bound OTLP metrics channel."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OtlpMetricsEvidence":
        return cls(
            _validate_envelope(
                payload,
                kind="OtlpMetricsEvidence",
                label="OTLP metrics evidence",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class OtlpLogsEvidence:
    """Normalized artifact produced by a tenant-bound OTLP logs channel."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OtlpLogsEvidence":
        return cls(
            _validate_envelope(
                payload,
                kind="OtlpLogsEvidence",
                label="OTLP logs evidence",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class InvestigationTelemetryInterpretation:
    """Closed threshold rule declared by an investigation caller."""

    statistic: str
    unit: str
    operator: str
    threshold: float
    when_matched: str
    when_not_matched: str

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationTelemetryInterpretation":
        threshold = payload.get("threshold")
        if payload.get("statistic") not in ("minimum", "maximum", "mean"):
            raise ValueError("investigation telemetry statistic is invalid")
        if not isinstance(payload.get("unit"), str) or not payload["unit"]:
            raise ValueError("investigation telemetry unit is invalid")
        if payload.get("operator") not in ("lt", "lte", "gt", "gte"):
            raise ValueError("investigation telemetry operator is invalid")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ValueError("investigation telemetry threshold is invalid")
        try:
            threshold_is_finite = math.isfinite(float(threshold))
        except (OverflowError, ValueError):
            threshold_is_finite = False
        dispositions = ("supports", "contradicts", "neutral")
        if (
            payload.get("whenMatched") not in dispositions
            or payload.get("whenNotMatched") not in dispositions
            or payload.get("whenMatched") == payload.get("whenNotMatched")
            or not threshold_is_finite
        ):
            raise ValueError("investigation telemetry disposition is invalid")
        return cls(
            statistic=payload["statistic"],
            unit=payload["unit"],
            operator=payload["operator"],
            threshold=float(threshold),
            when_matched=payload["whenMatched"],
            when_not_matched=payload["whenNotMatched"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statistic": self.statistic,
            "unit": self.unit,
            "operator": self.operator,
            "threshold": self.threshold,
            "whenMatched": self.when_matched,
            "whenNotMatched": self.when_not_matched,
        }


@dataclass(frozen=True)
class InvestigationTelemetryBaselineComparison:
    """Declared comparison between two subranges of investigation scope."""

    statistic: str
    unit: str
    baseline_time_range: Mapping[str, str]
    evaluation_time_range: Mapping[str, str]
    calculation: str
    operator: str
    threshold: float
    when_matched: str
    when_not_matched: str

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationTelemetryBaselineComparison":
        baseline = payload.get("baselineTimeRange")
        evaluation = payload.get("evaluationTimeRange")
        threshold = payload.get("threshold")
        if payload.get("statistic") not in ("minimum", "maximum", "mean"):
            raise ValueError("investigation telemetry baseline statistic is invalid")
        if not isinstance(payload.get("unit"), str) or not payload["unit"]:
            raise ValueError("investigation telemetry baseline unit is invalid")
        if not isinstance(baseline, Mapping) or not isinstance(evaluation, Mapping):
            raise ValueError("investigation telemetry baseline windows are invalid")
        if any(
            set(window) != {"start", "end"}
            or any(not isinstance(window.get(field), str) for field in ("start", "end"))
            for window in (baseline, evaluation)
        ):
            raise ValueError("investigation telemetry baseline windows are invalid")
        if payload.get("calculation") not in ("difference", "ratio"):
            raise ValueError("investigation telemetry baseline calculation is invalid")
        if payload.get("operator") not in ("lt", "lte", "gt", "gte"):
            raise ValueError("investigation telemetry baseline operator is invalid")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ValueError("investigation telemetry baseline threshold is invalid")
        try:
            threshold_is_finite = math.isfinite(float(threshold))
        except (OverflowError, ValueError):
            threshold_is_finite = False
        dispositions = ("supports", "contradicts", "neutral")
        if (
            not threshold_is_finite
            or payload.get("whenMatched") not in dispositions
            or payload.get("whenNotMatched") not in dispositions
            or payload.get("whenMatched") == payload.get("whenNotMatched")
        ):
            raise ValueError("investigation telemetry baseline disposition is invalid")
        return cls(
            statistic=payload["statistic"],
            unit=payload["unit"],
            baseline_time_range=dict(baseline),
            evaluation_time_range=dict(evaluation),
            calculation=payload["calculation"],
            operator=payload["operator"],
            threshold=float(threshold),
            when_matched=payload["whenMatched"],
            when_not_matched=payload["whenNotMatched"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statistic": self.statistic,
            "unit": self.unit,
            "baselineTimeRange": dict(self.baseline_time_range),
            "evaluationTimeRange": dict(self.evaluation_time_range),
            "calculation": self.calculation,
            "operator": self.operator,
            "threshold": self.threshold,
            "whenMatched": self.when_matched,
            "whenNotMatched": self.when_not_matched,
        }


@dataclass(frozen=True)
class InvestigationTelemetryRollingBaselineComparison:
    """Scope-end anchored baseline windows expanded by the control plane."""

    statistic: str
    unit: str
    baseline_duration_seconds: int
    evaluation_duration_seconds: int
    gap_seconds: int
    calculation: str
    operator: str
    threshold: float
    when_matched: str
    when_not_matched: str

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationTelemetryRollingBaselineComparison":
        expected = {
            "statistic",
            "unit",
            "baselineDurationSeconds",
            "evaluationDurationSeconds",
            "gapSeconds",
            "calculation",
            "operator",
            "threshold",
            "whenMatched",
            "whenNotMatched",
        }
        durations = (
            payload.get("baselineDurationSeconds"),
            payload.get("evaluationDurationSeconds"),
            payload.get("gapSeconds"),
        )
        threshold = payload.get("threshold")
        dispositions = ("supports", "contradicts", "neutral")
        if (
            set(payload) != expected
            or payload.get("statistic") not in ("minimum", "maximum", "mean")
            or not isinstance(payload.get("unit"), str)
            or not payload["unit"]
            or any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in durations
            )
            or not 60 <= durations[0] <= 604_800
            or not 60 <= durations[1] <= 604_800
            or not 1 <= durations[2] <= 86_400
            or payload.get("calculation") not in ("difference", "ratio")
            or payload.get("operator") not in ("lt", "lte", "gt", "gte")
            or isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(float(threshold))
            or payload.get("whenMatched") not in dispositions
            or payload.get("whenNotMatched") not in dispositions
            or payload.get("whenMatched") == payload.get("whenNotMatched")
        ):
            raise ValueError("investigation rolling baseline comparison is invalid")
        return cls(
            statistic=payload["statistic"],
            unit=payload["unit"],
            baseline_duration_seconds=durations[0],
            evaluation_duration_seconds=durations[1],
            gap_seconds=durations[2],
            calculation=payload["calculation"],
            operator=payload["operator"],
            threshold=float(threshold),
            when_matched=payload["whenMatched"],
            when_not_matched=payload["whenNotMatched"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statistic": self.statistic,
            "unit": self.unit,
            "baselineDurationSeconds": self.baseline_duration_seconds,
            "evaluationDurationSeconds": self.evaluation_duration_seconds,
            "gapSeconds": self.gap_seconds,
            "calculation": self.calculation,
            "operator": self.operator,
            "threshold": self.threshold,
            "whenMatched": self.when_matched,
            "whenNotMatched": self.when_not_matched,
        }


@dataclass(frozen=True)
class InvestigationTelemetrySeasonalBaselineComparison:
    """Scope-end comparison against matching prior periodic windows."""

    statistic: str
    unit: str
    period_seconds: int
    lookback_periods: int
    evaluation_duration_seconds: int
    baseline_aggregation: str
    calculation: str
    operator: str
    threshold: float
    when_matched: str
    when_not_matched: str

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationTelemetrySeasonalBaselineComparison":
        expected = {
            "statistic",
            "unit",
            "periodSeconds",
            "lookbackPeriods",
            "evaluationDurationSeconds",
            "baselineAggregation",
            "calculation",
            "operator",
            "threshold",
            "whenMatched",
            "whenNotMatched",
        }
        period = payload.get("periodSeconds")
        lookbacks = payload.get("lookbackPeriods")
        evaluation_duration = payload.get("evaluationDurationSeconds")
        threshold = payload.get("threshold")
        dispositions = ("supports", "contradicts", "neutral")
        if (
            set(payload) != expected
            or payload.get("statistic") not in ("minimum", "maximum", "mean")
            or not isinstance(payload.get("unit"), str)
            or not payload["unit"]
            or not isinstance(period, int)
            or isinstance(period, bool)
            or not 3_600 <= period <= 604_800
            or not isinstance(lookbacks, int)
            or isinstance(lookbacks, bool)
            or not 2 <= lookbacks <= 12
            or not isinstance(evaluation_duration, int)
            or isinstance(evaluation_duration, bool)
            or not 60 <= evaluation_duration <= 86_400
            or evaluation_duration >= period
            or payload.get("baselineAggregation") not in ("mean", "median")
            or payload.get("calculation") not in ("difference", "ratio")
            or payload.get("operator") not in ("lt", "lte", "gt", "gte")
            or isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(float(threshold))
            or payload.get("whenMatched") not in dispositions
            or payload.get("whenNotMatched") not in dispositions
            or payload.get("whenMatched") == payload.get("whenNotMatched")
        ):
            raise ValueError("investigation seasonal baseline comparison is invalid")
        return cls(
            statistic=payload["statistic"],
            unit=payload["unit"],
            period_seconds=period,
            lookback_periods=lookbacks,
            evaluation_duration_seconds=evaluation_duration,
            baseline_aggregation=payload["baselineAggregation"],
            calculation=payload["calculation"],
            operator=payload["operator"],
            threshold=float(threshold),
            when_matched=payload["whenMatched"],
            when_not_matched=payload["whenNotMatched"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statistic": self.statistic,
            "unit": self.unit,
            "periodSeconds": self.period_seconds,
            "lookbackPeriods": self.lookback_periods,
            "evaluationDurationSeconds": self.evaluation_duration_seconds,
            "baselineAggregation": self.baseline_aggregation,
            "calculation": self.calculation,
            "operator": self.operator,
            "threshold": self.threshold,
            "whenMatched": self.when_matched,
            "whenNotMatched": self.when_not_matched,
        }


@dataclass(frozen=True)
class InvestigationTelemetrySelection:
    """Provider-neutral metric candidate bounded by an investigation request."""

    selection_id: str
    integration_id: str
    query: Mapping[str, Any]
    limits: Mapping[str, Any]
    root_cause_classes: tuple[str, ...] = ()
    interpretation: Optional[InvestigationTelemetryInterpretation] = None
    baseline_comparison: Optional[InvestigationTelemetryBaselineComparison] = None
    rolling_baseline_comparison: Optional[
        InvestigationTelemetryRollingBaselineComparison
    ] = None
    seasonal_baseline_comparison: Optional[
        InvestigationTelemetrySeasonalBaselineComparison
    ] = None

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationTelemetrySelection":
        selection_id = payload.get("id")
        integration_id = payload.get("integrationId")
        query = payload.get("query")
        limits = payload.get("limits")
        classes = payload.get("rootCauseClasses", [])
        interpretation = payload.get("interpretation")
        baseline_comparison = payload.get("baselineComparison")
        rolling_baseline_comparison = payload.get("rollingBaselineComparison")
        seasonal_baseline_comparison = payload.get("seasonalBaselineComparison")
        if not isinstance(selection_id, str) or not selection_id.startswith("tqs_"):
            raise ValueError("investigation telemetry selection id is invalid")
        if not isinstance(integration_id, str) or not integration_id:
            raise ValueError("investigation telemetry integrationId is invalid")
        if not isinstance(query, Mapping) or not isinstance(limits, Mapping):
            raise ValueError("investigation telemetry query and limits are required")
        if not isinstance(classes, list) or any(
            not isinstance(item, str) for item in classes
        ):
            raise ValueError("investigation telemetry rootCauseClasses are invalid")
        if interpretation is not None and not isinstance(interpretation, Mapping):
            raise ValueError("investigation telemetry interpretation is invalid")
        if baseline_comparison is not None and not isinstance(
            baseline_comparison, Mapping
        ):
            raise ValueError("investigation telemetry baselineComparison is invalid")
        if rolling_baseline_comparison is not None and not isinstance(
            rolling_baseline_comparison, Mapping
        ):
            raise ValueError(
                "investigation telemetry rollingBaselineComparison is invalid"
            )
        if seasonal_baseline_comparison is not None and not isinstance(
            seasonal_baseline_comparison, Mapping
        ):
            raise ValueError(
                "investigation telemetry seasonalBaselineComparison is invalid"
            )
        rules = (
            interpretation,
            baseline_comparison,
            rolling_baseline_comparison,
            seasonal_baseline_comparison,
        )
        if sum(rule is not None for rule in rules) > 1:
            raise ValueError("investigation telemetry assessment rule is ambiguous")
        if (
            any(rule is not None for rule in rules)
        ) and not classes:
            raise ValueError(
                "investigation telemetry assessment rule requires rootCauseClasses"
            )
        return cls(
            selection_id=selection_id,
            integration_id=integration_id,
            query=dict(query),
            limits=dict(limits),
            root_cause_classes=tuple(classes),
            interpretation=(
                InvestigationTelemetryInterpretation.from_dict(interpretation)
                if isinstance(interpretation, Mapping)
                else None
            ),
            baseline_comparison=(
                InvestigationTelemetryBaselineComparison.from_dict(
                    baseline_comparison
                )
                if isinstance(baseline_comparison, Mapping)
                else None
            ),
            rolling_baseline_comparison=(
                InvestigationTelemetryRollingBaselineComparison.from_dict(
                    rolling_baseline_comparison
                )
                if isinstance(rolling_baseline_comparison, Mapping)
                else None
            ),
            seasonal_baseline_comparison=(
                InvestigationTelemetrySeasonalBaselineComparison.from_dict(
                    seasonal_baseline_comparison
                )
                if isinstance(seasonal_baseline_comparison, Mapping)
                else None
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "id": self.selection_id,
            "integrationId": self.integration_id,
            "query": dict(self.query),
            "limits": dict(self.limits),
        }
        if self.root_cause_classes:
            result["rootCauseClasses"] = list(self.root_cause_classes)
        if self.interpretation is not None:
            result["interpretation"] = self.interpretation.to_dict()
        if self.baseline_comparison is not None:
            result["baselineComparison"] = self.baseline_comparison.to_dict()
        if self.rolling_baseline_comparison is not None:
            result["rollingBaselineComparison"] = (
                self.rolling_baseline_comparison.to_dict()
            )
        if self.seasonal_baseline_comparison is not None:
            result["seasonalBaselineComparison"] = (
                self.seasonal_baseline_comparison.to_dict()
            )
        return result


@dataclass(frozen=True)
class InvestigationTelemetryAssessment:
    """Auditable result of applying one declared rule to stored metric evidence."""

    selection_id: str
    evidence_id: str
    root_cause_class: str
    metric: str
    statistic: str
    unit: str
    operator: str
    threshold: float
    disposition: str
    observed_value: Optional[float] = None

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationTelemetryAssessment":
        required_strings = (
            "selectionId",
            "evidenceId",
            "rootCauseClass",
            "metric",
            "statistic",
            "unit",
            "operator",
            "disposition",
        )
        if any(not isinstance(payload.get(field), str) for field in required_strings):
            raise ValueError("investigation telemetry assessment is invalid")
        threshold = payload.get("threshold")
        observed = payload.get("observedValue")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ValueError("investigation telemetry assessment threshold is invalid")
        if observed is not None and (
            isinstance(observed, bool) or not isinstance(observed, (int, float))
        ):
            raise ValueError("investigation telemetry observedValue is invalid")
        try:
            values_are_finite = math.isfinite(float(threshold)) and (
                observed is None or math.isfinite(float(observed))
            )
        except (OverflowError, ValueError):
            values_are_finite = False
        disposition = payload["disposition"]
        data_dispositions = ("supporting", "contradicting", "neutral")
        empty_dispositions = ("no-data", "incomplete")
        if (
            not values_are_finite
            or disposition not in data_dispositions + empty_dispositions
            or (disposition in data_dispositions and observed is None)
            or (disposition in empty_dispositions and observed is not None)
        ):
            raise ValueError("investigation telemetry assessment value is invalid")
        return cls(
            selection_id=payload["selectionId"],
            evidence_id=payload["evidenceId"],
            root_cause_class=payload["rootCauseClass"],
            metric=payload["metric"],
            statistic=payload["statistic"],
            unit=payload["unit"],
            operator=payload["operator"],
            threshold=float(threshold),
            disposition=disposition,
            observed_value=float(observed) if observed is not None else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "selectionId": self.selection_id,
            "evidenceId": self.evidence_id,
            "rootCauseClass": self.root_cause_class,
            "metric": self.metric,
            "statistic": self.statistic,
            "unit": self.unit,
            "operator": self.operator,
            "threshold": self.threshold,
            "disposition": self.disposition,
        }
        if self.observed_value is not None:
            result["observedValue"] = self.observed_value
        return result


@dataclass(frozen=True)
class InvestigationTelemetryBaselineAssessment:
    """Auditable baseline comparison evaluated from one stored metric artifact."""

    selection_id: str
    evidence_id: str
    root_cause_class: str
    metric: str
    statistic: str
    unit: str
    baseline_time_range: Mapping[str, str]
    evaluation_time_range: Mapping[str, str]
    calculation: str
    comparison_unit: str
    operator: str
    threshold: float
    disposition: str
    baseline_value: Optional[float] = None
    evaluation_value: Optional[float] = None
    comparison_value: Optional[float] = None

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationTelemetryBaselineAssessment":
        if payload.get("assessmentType") != "baseline-comparison":
            raise ValueError("investigation telemetry baseline assessment is invalid")
        required_strings = (
            "selectionId",
            "evidenceId",
            "rootCauseClass",
            "metric",
            "statistic",
            "unit",
            "calculation",
            "comparisonUnit",
            "operator",
            "disposition",
        )
        if any(
            not isinstance(payload.get(field), str) for field in required_strings
        ):
            raise ValueError("investigation telemetry baseline assessment is incomplete")
        baseline_range = payload.get("baselineTimeRange")
        evaluation_range = payload.get("evaluationTimeRange")
        threshold = payload.get("threshold")
        if (
            not isinstance(baseline_range, Mapping)
            or not isinstance(evaluation_range, Mapping)
            or isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
        ):
            raise ValueError("investigation telemetry baseline assessment is invalid")
        if (
            payload["statistic"] not in ("minimum", "maximum", "mean")
            or payload["calculation"] not in ("difference", "ratio")
            or payload["operator"] not in ("lt", "lte", "gt", "gte")
            or (
                payload["calculation"] == "ratio"
                and payload["comparisonUnit"] != "1"
            )
            or any(
                set(window) != {"start", "end"}
                or any(
                    not isinstance(window.get(field), str)
                    for field in ("start", "end")
                )
                for window in (baseline_range, evaluation_range)
            )
        ):
            raise ValueError("investigation telemetry baseline assessment is invalid")
        disposition = payload["disposition"]
        values = (
            payload.get("baselineValue"),
            payload.get("evaluationValue"),
            payload.get("comparisonValue"),
        )
        data_dispositions = ("supporting", "contradicting", "neutral")
        empty_dispositions = ("no-data", "incomplete")
        if (
            disposition not in data_dispositions + empty_dispositions
            or (disposition in data_dispositions and any(value is None for value in values))
            or (disposition in empty_dispositions and any(value is not None for value in values))
        ):
            raise ValueError("investigation telemetry baseline values are invalid")
        numeric_values = (threshold,) + values
        try:
            values_are_finite = all(
                value is None or (
                    not isinstance(value, bool)
                    and isinstance(value, (int, float))
                    and math.isfinite(float(value))
                )
                for value in numeric_values
            )
        except (OverflowError, ValueError):
            values_are_finite = False
        if not values_are_finite:
            raise ValueError("investigation telemetry baseline values are invalid")
        return cls(
            selection_id=payload["selectionId"],
            evidence_id=payload["evidenceId"],
            root_cause_class=payload["rootCauseClass"],
            metric=payload["metric"],
            statistic=payload["statistic"],
            unit=payload["unit"],
            baseline_time_range=dict(baseline_range),
            evaluation_time_range=dict(evaluation_range),
            calculation=payload["calculation"],
            comparison_unit=payload["comparisonUnit"],
            operator=payload["operator"],
            threshold=float(threshold),
            disposition=disposition,
            baseline_value=(
                float(values[0]) if values[0] is not None else None
            ),
            evaluation_value=(
                float(values[1]) if values[1] is not None else None
            ),
            comparison_value=(
                float(values[2]) if values[2] is not None else None
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "assessmentType": "baseline-comparison",
            "selectionId": self.selection_id,
            "evidenceId": self.evidence_id,
            "rootCauseClass": self.root_cause_class,
            "metric": self.metric,
            "statistic": self.statistic,
            "unit": self.unit,
            "baselineTimeRange": dict(self.baseline_time_range),
            "evaluationTimeRange": dict(self.evaluation_time_range),
            "calculation": self.calculation,
            "comparisonUnit": self.comparison_unit,
            "operator": self.operator,
            "threshold": self.threshold,
            "disposition": self.disposition,
        }
        if self.baseline_value is not None:
            result["baselineValue"] = self.baseline_value
        if self.evaluation_value is not None:
            result["evaluationValue"] = self.evaluation_value
        if self.comparison_value is not None:
            result["comparisonValue"] = self.comparison_value
        return result


@dataclass(frozen=True)
class InvestigationTelemetrySeasonalBaselineAssessment:
    """Auditable comparison against matching prior periodic windows."""

    selection_id: str
    evidence_id: str
    root_cause_class: str
    metric: str
    statistic: str
    unit: str
    period_seconds: int
    lookback_periods: int
    evaluation_duration_seconds: int
    baseline_aggregation: str
    baseline_time_ranges: tuple[Mapping[str, str], ...]
    evaluation_time_range: Mapping[str, str]
    calculation: str
    comparison_unit: str
    operator: str
    threshold: float
    disposition: str
    baseline_period_values: Optional[tuple[float, ...]] = None
    baseline_value: Optional[float] = None
    evaluation_value: Optional[float] = None
    comparison_value: Optional[float] = None

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationTelemetrySeasonalBaselineAssessment":
        if payload.get("assessmentType") != "seasonal-baseline-comparison":
            raise ValueError(
                "investigation telemetry seasonal baseline assessment is invalid"
            )
        required_strings = (
            "selectionId",
            "evidenceId",
            "rootCauseClass",
            "metric",
            "statistic",
            "unit",
            "baselineAggregation",
            "calculation",
            "comparisonUnit",
            "operator",
            "disposition",
        )
        if any(
            not isinstance(payload.get(field), str) for field in required_strings
        ):
            raise ValueError(
                "investigation telemetry seasonal baseline assessment is incomplete"
            )
        period = payload.get("periodSeconds")
        lookbacks = payload.get("lookbackPeriods")
        evaluation_duration = payload.get("evaluationDurationSeconds")
        baseline_ranges = payload.get("baselineTimeRanges")
        evaluation_range = payload.get("evaluationTimeRange")
        threshold = payload.get("threshold")
        if (
            not isinstance(period, int)
            or isinstance(period, bool)
            or not 3_600 <= period <= 604_800
            or not isinstance(lookbacks, int)
            or isinstance(lookbacks, bool)
            or not 2 <= lookbacks <= 12
            or not isinstance(evaluation_duration, int)
            or isinstance(evaluation_duration, bool)
            or not 60 <= evaluation_duration <= 86_400
            or evaluation_duration >= period
            or not isinstance(baseline_ranges, list)
            or len(baseline_ranges) != lookbacks
            or not isinstance(evaluation_range, Mapping)
            or isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
        ):
            raise ValueError(
                "investigation telemetry seasonal baseline assessment is invalid"
            )
        windows = (*baseline_ranges, evaluation_range)
        if any(
            not isinstance(window, Mapping)
            or set(window) != {"start", "end"}
            or any(
                not isinstance(window.get(field), str)
                for field in ("start", "end")
            )
            for window in windows
        ):
            raise ValueError(
                "investigation telemetry seasonal baseline windows are invalid"
            )
        if (
            payload["statistic"] not in ("minimum", "maximum", "mean")
            or payload["baselineAggregation"] not in ("mean", "median")
            or payload["calculation"] not in ("difference", "ratio")
            or payload["operator"] not in ("lt", "lte", "gt", "gte")
            or (
                payload["calculation"] == "ratio"
                and payload["comparisonUnit"] != "1"
            )
        ):
            raise ValueError(
                "investigation telemetry seasonal baseline assessment is invalid"
            )
        disposition = payload["disposition"]
        period_values = payload.get("baselinePeriodValues")
        values = (
            payload.get("baselineValue"),
            payload.get("evaluationValue"),
            payload.get("comparisonValue"),
        )
        data_dispositions = ("supporting", "contradicting", "neutral")
        empty_dispositions = ("no-data", "incomplete")
        if (
            disposition not in data_dispositions + empty_dispositions
            or (
                disposition in data_dispositions
                and (
                    not isinstance(period_values, list)
                    or len(period_values) != lookbacks
                    or any(value is None for value in values)
                )
            )
            or (
                disposition in empty_dispositions
                and (period_values is not None or any(value is not None for value in values))
            )
        ):
            raise ValueError(
                "investigation telemetry seasonal baseline values are invalid"
            )
        numeric_values = (threshold,) + values + tuple(period_values or ())
        try:
            values_are_finite = all(
                value is None
                or (
                    not isinstance(value, bool)
                    and isinstance(value, (int, float))
                    and math.isfinite(float(value))
                )
                for value in numeric_values
            )
        except (OverflowError, ValueError):
            values_are_finite = False
        if not values_are_finite:
            raise ValueError(
                "investigation telemetry seasonal baseline values are invalid"
            )
        return cls(
            selection_id=payload["selectionId"],
            evidence_id=payload["evidenceId"],
            root_cause_class=payload["rootCauseClass"],
            metric=payload["metric"],
            statistic=payload["statistic"],
            unit=payload["unit"],
            period_seconds=period,
            lookback_periods=lookbacks,
            evaluation_duration_seconds=evaluation_duration,
            baseline_aggregation=payload["baselineAggregation"],
            baseline_time_ranges=tuple(dict(window) for window in baseline_ranges),
            evaluation_time_range=dict(evaluation_range),
            calculation=payload["calculation"],
            comparison_unit=payload["comparisonUnit"],
            operator=payload["operator"],
            threshold=float(threshold),
            disposition=disposition,
            baseline_period_values=(
                tuple(float(value) for value in period_values)
                if isinstance(period_values, list)
                else None
            ),
            baseline_value=float(values[0]) if values[0] is not None else None,
            evaluation_value=float(values[1]) if values[1] is not None else None,
            comparison_value=float(values[2]) if values[2] is not None else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "assessmentType": "seasonal-baseline-comparison",
            "selectionId": self.selection_id,
            "evidenceId": self.evidence_id,
            "rootCauseClass": self.root_cause_class,
            "metric": self.metric,
            "statistic": self.statistic,
            "unit": self.unit,
            "periodSeconds": self.period_seconds,
            "lookbackPeriods": self.lookback_periods,
            "evaluationDurationSeconds": self.evaluation_duration_seconds,
            "baselineAggregation": self.baseline_aggregation,
            "baselineTimeRanges": [
                dict(window) for window in self.baseline_time_ranges
            ],
            "evaluationTimeRange": dict(self.evaluation_time_range),
            "calculation": self.calculation,
            "comparisonUnit": self.comparison_unit,
            "operator": self.operator,
            "threshold": self.threshold,
            "disposition": self.disposition,
        }
        if self.baseline_period_values is not None:
            result["baselinePeriodValues"] = list(self.baseline_period_values)
        if self.baseline_value is not None:
            result["baselineValue"] = self.baseline_value
        if self.evaluation_value is not None:
            result["evaluationValue"] = self.evaluation_value
        if self.comparison_value is not None:
            result["comparisonValue"] = self.comparison_value
        return result


@dataclass(frozen=True)
class InvestigationLogInterpretation:
    """Closed record-count rule declared by an investigation caller."""

    min_records: int
    when_matched: str
    when_not_matched: str

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationLogInterpretation":
        min_records = payload.get("minRecords")
        dispositions = ("supports", "contradicts", "neutral")
        if (
            not isinstance(min_records, int)
            or isinstance(min_records, bool)
            or min_records < 1
            or payload.get("whenMatched") not in dispositions
            or payload.get("whenNotMatched") not in dispositions
            or payload.get("whenMatched") == payload.get("whenNotMatched")
        ):
            raise ValueError("investigation log interpretation is invalid")
        return cls(
            min_records=min_records,
            when_matched=payload["whenMatched"],
            when_not_matched=payload["whenNotMatched"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "minRecords": self.min_records,
            "whenMatched": self.when_matched,
            "whenNotMatched": self.when_not_matched,
        }


@dataclass(frozen=True)
class InvestigationLogSelection:
    """Provider-neutral log candidate bounded by an investigation request."""

    selection_id: str
    integration_id: str
    query: Mapping[str, Any]
    limits: Mapping[str, Any]
    root_cause_classes: tuple[str, ...] = ()
    interpretation: Optional[InvestigationLogInterpretation] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InvestigationLogSelection":
        selection_id = payload.get("id")
        integration_id = payload.get("integrationId")
        query = payload.get("query")
        limits = payload.get("limits")
        classes = payload.get("rootCauseClasses", [])
        interpretation = payload.get("interpretation")
        if not isinstance(selection_id, str) or not selection_id.startswith("lqs_"):
            raise ValueError("investigation log selection id is invalid")
        if not isinstance(integration_id, str) or not integration_id:
            raise ValueError("investigation log integrationId is invalid")
        if not isinstance(query, Mapping) or not isinstance(limits, Mapping):
            raise ValueError("investigation log query and limits are required")
        if not isinstance(classes, list) or any(
            not isinstance(item, str) for item in classes
        ):
            raise ValueError("investigation log rootCauseClasses are invalid")
        if interpretation is not None and not isinstance(interpretation, Mapping):
            raise ValueError("investigation log interpretation is invalid")
        if interpretation is not None and not classes:
            raise ValueError(
                "investigation log interpretation requires rootCauseClasses"
            )
        normalized_interpretation = (
            InvestigationLogInterpretation.from_dict(interpretation)
            if isinstance(interpretation, Mapping)
            else None
        )
        max_records = limits.get("maxRecords")
        if (
            normalized_interpretation is not None
            and isinstance(max_records, int)
            and normalized_interpretation.min_records > max_records
        ):
            raise ValueError("investigation log interpretation exceeds result limit")
        return cls(
            selection_id=selection_id,
            integration_id=integration_id,
            query=dict(query),
            limits=dict(limits),
            root_cause_classes=tuple(classes),
            interpretation=normalized_interpretation,
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "id": self.selection_id,
            "integrationId": self.integration_id,
            "query": dict(self.query),
            "limits": dict(self.limits),
        }
        if self.root_cause_classes:
            result["rootCauseClasses"] = list(self.root_cause_classes)
        if self.interpretation is not None:
            result["interpretation"] = self.interpretation.to_dict()
        return result


@dataclass(frozen=True)
class InvestigationLogAssessment:
    """Auditable result of applying one rule to stored normalized logs."""

    selection_id: str
    evidence_id: str
    root_cause_class: str
    min_records: int
    disposition: str
    observed_record_count: Optional[int] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InvestigationLogAssessment":
        disposition = payload.get("disposition")
        minimum = payload.get("minRecords")
        observed = payload.get("observedRecordCount")
        data_dispositions = ("supporting", "contradicting", "neutral")
        empty_dispositions = ("no-data", "incomplete")
        if (
            any(
                not isinstance(payload.get(field), str)
                for field in ("selectionId", "evidenceId", "rootCauseClass")
            )
            or not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or minimum < 1
            or disposition not in data_dispositions + empty_dispositions
            or (
                disposition in data_dispositions
                and (
                    not isinstance(observed, int)
                    or isinstance(observed, bool)
                    or observed < 1
                )
            )
            or (disposition in empty_dispositions and observed is not None)
        ):
            raise ValueError("investigation log assessment is invalid")
        return cls(
            selection_id=payload["selectionId"],
            evidence_id=payload["evidenceId"],
            root_cause_class=payload["rootCauseClass"],
            min_records=minimum,
            disposition=disposition,
            observed_record_count=observed,
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "selectionId": self.selection_id,
            "evidenceId": self.evidence_id,
            "rootCauseClass": self.root_cause_class,
            "minRecords": self.min_records,
            "disposition": self.disposition,
        }
        if self.observed_record_count is not None:
            result["observedRecordCount"] = self.observed_record_count
        return result


@dataclass(frozen=True)
class InvestigationChangeInterpretation:
    """Closed resource-change count rule declared by an investigation caller."""

    min_changes: int
    when_matched: str
    when_not_matched: str

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationChangeInterpretation":
        minimum = payload.get("minChanges")
        dispositions = ("supports", "contradicts", "neutral")
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not 1 <= minimum <= 500
            or payload.get("whenMatched") not in dispositions
            or payload.get("whenNotMatched") not in dispositions
            or payload.get("whenMatched") == payload.get("whenNotMatched")
        ):
            raise ValueError("investigation change interpretation is invalid")
        return cls(minimum, payload["whenMatched"], payload["whenNotMatched"])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "minChanges": self.min_changes,
            "whenMatched": self.when_matched,
            "whenNotMatched": self.when_not_matched,
        }


@dataclass(frozen=True)
class InvestigationChangeSelection:
    """Value-minimized resource-change candidate bounded by an investigation."""

    selection_id: str
    integration_id: str
    query: Mapping[str, Any]
    limits: Mapping[str, Any]
    root_cause_classes: tuple[str, ...] = ()
    interpretation: Optional[InvestigationChangeInterpretation] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InvestigationChangeSelection":
        selection_id = payload.get("id")
        integration_id = payload.get("integrationId")
        query = payload.get("query")
        limits = payload.get("limits")
        classes = payload.get("rootCauseClasses", [])
        interpretation = payload.get("interpretation")
        if not isinstance(selection_id, str) or not selection_id.startswith("cqs_"):
            raise ValueError("investigation change selection id is invalid")
        if not isinstance(integration_id, str) or not integration_id:
            raise ValueError("investigation change integrationId is invalid")
        if not isinstance(query, Mapping) or not isinstance(limits, Mapping):
            raise ValueError("investigation change query and limits are required")
        if not isinstance(classes, list) or any(
            not isinstance(item, str) for item in classes
        ):
            raise ValueError("investigation change rootCauseClasses are invalid")
        if interpretation is not None and not isinstance(interpretation, Mapping):
            raise ValueError("investigation change interpretation is invalid")
        if interpretation is not None and not classes:
            raise ValueError(
                "investigation change interpretation requires rootCauseClasses"
            )
        normalized = (
            InvestigationChangeInterpretation.from_dict(interpretation)
            if isinstance(interpretation, Mapping)
            else None
        )
        maximum = limits.get("maxChanges")
        if (
            normalized is not None
            and isinstance(maximum, int)
            and normalized.min_changes > maximum
        ):
            raise ValueError("investigation change interpretation exceeds result limit")
        return cls(
            selection_id,
            integration_id,
            dict(query),
            dict(limits),
            tuple(classes),
            normalized,
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "id": self.selection_id,
            "integrationId": self.integration_id,
            "query": dict(self.query),
            "limits": dict(self.limits),
        }
        if self.root_cause_classes:
            result["rootCauseClasses"] = list(self.root_cause_classes)
        if self.interpretation is not None:
            result["interpretation"] = self.interpretation.to_dict()
        return result


@dataclass(frozen=True)
class InvestigationChangeAssessment:
    """Auditable assessment of committed value-minimized change evidence."""

    selection_id: str
    evidence_id: str
    root_cause_class: str
    change_kinds: tuple[str, ...]
    min_changes: int
    disposition: str
    observed_change_count: Optional[int] = None
    observed_change_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InvestigationChangeAssessment":
        disposition = payload.get("disposition")
        kinds = payload.get("changeKinds")
        minimum = payload.get("minChanges")
        count = payload.get("observedChangeCount")
        change_ids = payload.get("observedChangeIds")
        data_dispositions = ("supporting", "contradicting", "neutral")
        empty_dispositions = ("no-data", "incomplete")
        if (
            any(
                not isinstance(payload.get(field), str)
                for field in ("selectionId", "evidenceId", "rootCauseClass")
            )
            or not isinstance(kinds, list)
            or any(not isinstance(kind, str) for kind in kinds)
            or not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or minimum < 1
            or disposition not in data_dispositions + empty_dispositions
            or (
                disposition in data_dispositions
                and (
                    not isinstance(count, int)
                    or isinstance(count, bool)
                    or count < 1
                    or not isinstance(change_ids, list)
                    or len(change_ids) != count
                    or any(not isinstance(item, str) for item in change_ids)
                )
            )
            or (
                disposition in empty_dispositions
                and (count is not None or change_ids is not None)
            )
        ):
            raise ValueError("investigation change assessment is invalid")
        return cls(
            payload["selectionId"],
            payload["evidenceId"],
            payload["rootCauseClass"],
            tuple(kinds),
            minimum,
            disposition,
            count,
            tuple(change_ids or ()),
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "selectionId": self.selection_id,
            "evidenceId": self.evidence_id,
            "rootCauseClass": self.root_cause_class,
            "changeKinds": list(self.change_kinds),
            "minChanges": self.min_changes,
            "disposition": self.disposition,
        }
        if self.observed_change_count is not None:
            result["observedChangeCount"] = self.observed_change_count
            result["observedChangeIds"] = list(self.observed_change_ids)
        return result


@dataclass(frozen=True)
class InvestigationContextInterpretation:
    min_documents: int
    when_matched: str
    when_not_matched: str

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationContextInterpretation":
        minimum = payload.get("minDocuments")
        dispositions = ("supports", "contradicts", "neutral")
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not 1 <= minimum <= 32
            or payload.get("whenMatched") not in dispositions
            or payload.get("whenNotMatched") not in dispositions
            or payload.get("whenMatched") == payload.get("whenNotMatched")
        ):
            raise ValueError("investigation context interpretation is invalid")
        return cls(minimum, payload["whenMatched"], payload["whenNotMatched"])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "minDocuments": self.min_documents,
            "whenMatched": self.when_matched,
            "whenNotMatched": self.when_not_matched,
        }


@dataclass(frozen=True)
class InvestigationContextSelection:
    selection_id: str
    integration_id: str
    query: Mapping[str, Any]
    limits: Mapping[str, Any]
    root_cause_classes: tuple[str, ...] = ()
    interpretation: Optional[InvestigationContextInterpretation] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InvestigationContextSelection":
        selection_id = payload.get("id")
        integration_id = payload.get("integrationId")
        query = payload.get("query")
        limits = payload.get("limits")
        classes = payload.get("rootCauseClasses", [])
        interpretation = payload.get("interpretation")
        if not isinstance(selection_id, str) or not selection_id.startswith("xqs_"):
            raise ValueError("investigation context selection id is invalid")
        if not isinstance(integration_id, str) or not integration_id:
            raise ValueError("investigation context integrationId is invalid")
        if not isinstance(query, Mapping) or not isinstance(limits, Mapping):
            raise ValueError("investigation context query and limits are required")
        if not isinstance(classes, list) or any(
            not isinstance(item, str) for item in classes
        ):
            raise ValueError("investigation context rootCauseClasses are invalid")
        if interpretation is not None and not isinstance(interpretation, Mapping):
            raise ValueError("investigation context interpretation is invalid")
        if interpretation is not None and not classes:
            raise ValueError(
                "investigation context interpretation requires rootCauseClasses"
            )
        normalized = (
            InvestigationContextInterpretation.from_dict(interpretation)
            if isinstance(interpretation, Mapping)
            else None
        )
        maximum = limits.get("maxDocuments")
        if (
            normalized is not None
            and isinstance(maximum, int)
            and normalized.min_documents > maximum
        ):
            raise ValueError("investigation context interpretation exceeds result limit")
        return cls(
            selection_id,
            integration_id,
            dict(query),
            dict(limits),
            tuple(classes),
            normalized,
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "id": self.selection_id,
            "integrationId": self.integration_id,
            "query": dict(self.query),
            "limits": dict(self.limits),
        }
        if self.root_cause_classes:
            result["rootCauseClasses"] = list(self.root_cause_classes)
        if self.interpretation is not None:
            result["interpretation"] = self.interpretation.to_dict()
        return result


@dataclass(frozen=True)
class InvestigationContextAssessment:
    selection_id: str
    evidence_id: str
    root_cause_class: str
    kinds: tuple[str, ...]
    reference_ids: tuple[str, ...]
    min_documents: int
    disposition: str
    observed_document_count: Optional[int] = None
    observed_document_ids: tuple[str, ...] = ()
    observed_reference_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InvestigationContextAssessment":
        disposition = payload.get("disposition")
        kinds = payload.get("kinds")
        refs = payload.get("referenceIds")
        minimum = payload.get("minDocuments")
        count = payload.get("observedDocumentCount")
        document_ids = payload.get("observedDocumentIds")
        observed_refs = payload.get("observedReferenceIds")
        data_dispositions = ("supporting", "contradicting", "neutral")
        empty_dispositions = ("no-data", "incomplete")
        if (
            any(
                not isinstance(payload.get(field), str)
                for field in ("selectionId", "evidenceId", "rootCauseClass")
            )
            or not isinstance(kinds, list)
            or any(not isinstance(item, str) for item in kinds)
            or not isinstance(refs, list)
            or any(not isinstance(item, str) for item in refs)
            or not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or minimum < 1
            or disposition not in data_dispositions + empty_dispositions
            or (
                disposition in data_dispositions
                and (
                    not isinstance(count, int)
                    or isinstance(count, bool)
                    or count < 1
                    or not isinstance(document_ids, list)
                    or len(document_ids) != count
                    or not isinstance(observed_refs, list)
                    or not observed_refs
                )
            )
            or (
                disposition in empty_dispositions
                and any(
                    item is not None for item in (count, document_ids, observed_refs)
                )
            )
        ):
            raise ValueError("investigation context assessment is invalid")
        return cls(
            payload["selectionId"],
            payload["evidenceId"],
            payload["rootCauseClass"],
            tuple(kinds),
            tuple(refs),
            minimum,
            disposition,
            count,
            tuple(document_ids or ()),
            tuple(observed_refs or ()),
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "selectionId": self.selection_id,
            "evidenceId": self.evidence_id,
            "rootCauseClass": self.root_cause_class,
            "kinds": list(self.kinds),
            "referenceIds": list(self.reference_ids),
            "minDocuments": self.min_documents,
            "disposition": self.disposition,
        }
        if self.observed_document_count is not None:
            result["observedDocumentCount"] = self.observed_document_count
            result["observedDocumentIds"] = list(self.observed_document_ids)
            result["observedReferenceIds"] = list(self.observed_reference_ids)
        return result


@dataclass(frozen=True)
class InvestigationKubernetesEventInterpretation:
    """Closed condition-count rule declared by an investigation caller."""

    conditions: tuple[str, ...]
    min_matches: int
    when_matched: str
    when_not_matched: str

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationKubernetesEventInterpretation":
        conditions = payload.get("conditions")
        min_matches = payload.get("minMatches")
        dispositions = ("supports", "contradicts", "neutral")
        if (
            not isinstance(conditions, list)
            or not conditions
            or any(not isinstance(condition, str) for condition in conditions)
            or len(conditions) != len(set(conditions))
            or not isinstance(min_matches, int)
            or isinstance(min_matches, bool)
            or min_matches < 1
            or payload.get("whenMatched") not in dispositions
            or payload.get("whenNotMatched") not in dispositions
            or payload.get("whenMatched") == payload.get("whenNotMatched")
        ):
            raise ValueError("investigation Kubernetes Event interpretation is invalid")
        return cls(
            conditions=tuple(conditions),
            min_matches=min_matches,
            when_matched=payload["whenMatched"],
            when_not_matched=payload["whenNotMatched"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conditions": list(self.conditions),
            "minMatches": self.min_matches,
            "whenMatched": self.when_matched,
            "whenNotMatched": self.when_not_matched,
        }


@dataclass(frozen=True)
class InvestigationKubernetesEventSelection:
    """Provider-neutral event candidate bounded by an investigation request."""

    selection_id: str
    integration_id: str
    query: Mapping[str, Any]
    limits: Mapping[str, Any]
    root_cause_classes: tuple[str, ...] = ()
    interpretation: Optional[InvestigationKubernetesEventInterpretation] = None

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationKubernetesEventSelection":
        selection_id = payload.get("id")
        integration_id = payload.get("integrationId")
        query = payload.get("query")
        limits = payload.get("limits")
        classes = payload.get("rootCauseClasses", [])
        interpretation = payload.get("interpretation")
        if not isinstance(selection_id, str) or not selection_id.startswith("kes_"):
            raise ValueError("investigation Kubernetes Event selection id is invalid")
        if not isinstance(integration_id, str) or not integration_id:
            raise ValueError("investigation Kubernetes Event integrationId is invalid")
        if not isinstance(query, Mapping) or not isinstance(limits, Mapping):
            raise ValueError("investigation Kubernetes Event query and limits are required")
        if not isinstance(classes, list) or any(
            not isinstance(item, str) for item in classes
        ):
            raise ValueError("investigation Kubernetes Event rootCauseClasses are invalid")
        if interpretation is not None and not isinstance(interpretation, Mapping):
            raise ValueError("investigation Kubernetes Event interpretation is invalid")
        if interpretation is not None and not classes:
            raise ValueError(
                "investigation Kubernetes Event interpretation requires rootCauseClasses"
            )
        return cls(
            selection_id=selection_id,
            integration_id=integration_id,
            query=dict(query),
            limits=dict(limits),
            root_cause_classes=tuple(classes),
            interpretation=(
                InvestigationKubernetesEventInterpretation.from_dict(interpretation)
                if isinstance(interpretation, Mapping)
                else None
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "id": self.selection_id,
            "integrationId": self.integration_id,
            "query": dict(self.query),
            "limits": dict(self.limits),
        }
        if self.root_cause_classes:
            result["rootCauseClasses"] = list(self.root_cause_classes)
        if self.interpretation is not None:
            result["interpretation"] = self.interpretation.to_dict()
        return result


@dataclass(frozen=True)
class InvestigationKubernetesEventAssessment:
    """Auditable result of applying one rule to stored event evidence."""

    selection_id: str
    evidence_id: str
    root_cause_class: str
    conditions: tuple[str, ...]
    min_matches: int
    disposition: str
    matched_event_count: Optional[int] = None
    matched_event_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationKubernetesEventAssessment":
        conditions = payload.get("conditions")
        disposition = payload.get("disposition")
        count = payload.get("matchedEventCount")
        event_ids = payload.get("matchedEventIds")
        data_dispositions = ("supporting", "contradicting", "neutral")
        empty_dispositions = ("no-data", "incomplete")
        if (
            any(
                not isinstance(payload.get(field), str)
                for field in ("selectionId", "evidenceId", "rootCauseClass")
            )
            or not isinstance(conditions, list)
            or not conditions
            or any(not isinstance(condition, str) for condition in conditions)
            or not isinstance(payload.get("minMatches"), int)
            or isinstance(payload.get("minMatches"), bool)
            or disposition not in data_dispositions + empty_dispositions
            or (
                disposition in data_dispositions
                and (
                    not isinstance(count, int)
                    or isinstance(count, bool)
                    or not isinstance(event_ids, list)
                    or any(not isinstance(event_id, str) for event_id in event_ids)
                )
            )
            or (
                disposition in empty_dispositions
                and (count is not None or event_ids is not None)
            )
        ):
            raise ValueError("investigation Kubernetes Event assessment is invalid")
        return cls(
            selection_id=payload["selectionId"],
            evidence_id=payload["evidenceId"],
            root_cause_class=payload["rootCauseClass"],
            conditions=tuple(conditions),
            min_matches=payload["minMatches"],
            disposition=disposition,
            matched_event_count=count,
            matched_event_ids=tuple(event_ids or ()),
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "selectionId": self.selection_id,
            "evidenceId": self.evidence_id,
            "rootCauseClass": self.root_cause_class,
            "conditions": list(self.conditions),
            "minMatches": self.min_matches,
            "disposition": self.disposition,
        }
        if self.matched_event_count is not None:
            result["matchedEventCount"] = self.matched_event_count
            result["matchedEventIds"] = list(self.matched_event_ids)
        return result


@dataclass(frozen=True)
class InvestigationCancellationRequest:
    """Authenticated request for cooperative investigation cancellation."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationCancellationRequest":
        return cls(
            _validate_envelope(
                payload,
                kind="InvestigationCancellationRequest",
                label="investigation cancellation request",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class InvestigationStatus:
    """Current durable lifecycle state for an accepted investigation."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InvestigationStatus":
        return cls(
            _validate_envelope(
                payload,
                kind="InvestigationStatus",
                label="investigation status",
            )
        )

    @property
    def state(self) -> str:
        spec = self.payload.get("spec")
        state = spec.get("state") if isinstance(spec, Mapping) else None
        if not isinstance(state, str):
            raise ValueError("investigation status state is invalid")
        return state

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class InvestigationJobStatus:
    """Durable queue, lease, retry, and terminal state for background work."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InvestigationJobStatus":
        return cls(
            _validate_envelope(
                payload,
                kind="InvestigationJobStatus",
                label="investigation job status",
            )
        )

    @property
    def state(self) -> str:
        spec = self.payload.get("spec")
        state = spec.get("state") if isinstance(spec, Mapping) else None
        if not isinstance(state, str):
            raise ValueError("investigation job status state is invalid")
        return state

    @property
    def attempts(self) -> int:
        spec = self.payload.get("spec")
        attempts = spec.get("attempts") if isinstance(spec, Mapping) else None
        if not isinstance(attempts, int) or isinstance(attempts, bool):
            raise ValueError("investigation job status attempts is invalid")
        return attempts

    def to_dict(self) -> Dict[str, Any]:
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

    @property
    def catalog_snapshot(self) -> Optional[Mapping[str, Any]]:
        """Return server-owned protected-catalog provenance when resolved."""

        spec = self.payload.get("spec")
        value = spec.get("catalogSnapshot") if isinstance(spec, Mapping) else None
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("investigation catalogSnapshot must be an object")
        return dict(value)

    @property
    def telemetry_selections(self) -> tuple[InvestigationTelemetrySelection, ...]:
        """Return bounded metric candidates without importing server classes."""

        spec = self.payload.get("spec")
        values = spec.get("telemetrySelections", []) if isinstance(spec, Mapping) else []
        if not isinstance(values, list):
            raise ValueError("investigation telemetrySelections must be an array")
        if any(not isinstance(value, Mapping) for value in values):
            raise ValueError("investigation telemetry selection must be an object")
        return tuple(
            InvestigationTelemetrySelection.from_dict(value)
            for value in values
        )

    @property
    def kubernetes_event_selections(
        self,
    ) -> tuple[InvestigationKubernetesEventSelection, ...]:
        """Return bounded Kubernetes Event candidates."""

        spec = self.payload.get("spec")
        values = (
            spec.get("kubernetesEventSelections", [])
            if isinstance(spec, Mapping)
            else []
        )
        if not isinstance(values, list) or any(
            not isinstance(value, Mapping) for value in values
        ):
            raise ValueError("investigation kubernetesEventSelections must be an array")
        return tuple(
            InvestigationKubernetesEventSelection.from_dict(value)
            for value in values
        )

    @property
    def log_selections(self) -> tuple[InvestigationLogSelection, ...]:
        """Return bounded backend-neutral log candidates."""

        spec = self.payload.get("spec")
        values = spec.get("logSelections", []) if isinstance(spec, Mapping) else []
        if not isinstance(values, list) or any(
            not isinstance(value, Mapping) for value in values
        ):
            raise ValueError("investigation logSelections must be an array")
        return tuple(InvestigationLogSelection.from_dict(value) for value in values)

    @property
    def change_selections(self) -> tuple[InvestigationChangeSelection, ...]:
        """Return bounded value-minimized resource-change candidates."""

        spec = self.payload.get("spec")
        values = spec.get("changeSelections", []) if isinstance(spec, Mapping) else []
        if not isinstance(values, list) or any(
            not isinstance(value, Mapping) for value in values
        ):
            raise ValueError("investigation changeSelections must be an array")
        return tuple(InvestigationChangeSelection.from_dict(value) for value in values)

    @property
    def context_selections(self) -> tuple[InvestigationContextSelection, ...]:
        """Return bounded untrusted repository/runbook context candidates."""

        spec = self.payload.get("spec")
        values = spec.get("contextSelections", []) if isinstance(spec, Mapping) else []
        if not isinstance(values, list) or any(
            not isinstance(value, Mapping) for value in values
        ):
            raise ValueError("investigation contextSelections must be an array")
        return tuple(InvestigationContextSelection.from_dict(value) for value in values)


@dataclass(frozen=True)
class InvestigationSignalCatalog:
    """Protected tenant-bound profiles used to generate reviewed candidates."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationSignalCatalog":
        if payload.get("apiVersion") != API_VERSION:
            raise ValueError("unsupported investigation signal catalog apiVersion")
        if payload.get("kind") != "InvestigationSignalCatalog":
            raise ValueError(
                "investigation signal catalog kind must be InvestigationSignalCatalog"
            )
        profiles = payload.get("profiles")
        if not isinstance(profiles, list) or any(
            not isinstance(profile, Mapping) for profile in profiles
        ):
            raise ValueError("investigation signal catalog profiles must be an array")
        return cls(dict(payload))

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class InvestigationSignalPromotion:
    """One bounded, provenance-preserving adaptive-plan promotion."""

    trigger_signal: str
    trigger_selection_id: str
    trigger_outcome: str
    candidate_signal: str
    candidate_selection_id: str
    remaining_tool_calls: int
    remaining_evidence_items: int

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "InvestigationSignalPromotion":
        trigger = payload.get("trigger")
        candidate = payload.get("candidate")
        capacity = payload.get("remainingCapacity")
        if payload.get("position") != 1:
            raise ValueError("investigation signal promotion position must be one")
        if not all(
            isinstance(item, Mapping) for item in (trigger, candidate, capacity)
        ):
            raise ValueError("investigation signal promotion is incomplete")
        prefixes = {
            "kubernetes.event": "kes_",
            "repository.context": "xqs_",
            "resource.change": "cqs_",
            "telemetry.metrics": "tqs_",
            "telemetry.logs": "lqs_",
        }
        trigger_signal = trigger.get("signal")
        candidate_signal = candidate.get("signal")
        trigger_selection_id = trigger.get("selectionId")
        candidate_selection_id = candidate.get("selectionId")
        if (
            not isinstance(trigger_signal, str)
            or trigger_signal not in prefixes
            or not isinstance(candidate_signal, str)
            or candidate_signal not in prefixes
        ):
            raise ValueError("investigation signal promotion signal is invalid")
        if (
            not isinstance(trigger_selection_id, str)
            or re.fullmatch(
                re.escape(prefixes[trigger_signal]) + r"[a-f0-9]{16}",
                trigger_selection_id,
            )
            is None
            or not isinstance(candidate_selection_id, str)
            or re.fullmatch(
                re.escape(prefixes[candidate_signal]) + r"[a-f0-9]{16}",
                candidate_selection_id,
            )
            is None
        ):
            raise ValueError("investigation signal promotion selectionId is invalid")
        if trigger.get("outcome") not in {
            "provider-error",
            "provider-unavailable",
        }:
            raise ValueError("investigation signal promotion outcome is invalid")
        if candidate.get("initialReason") != "budget-exhausted":
            raise ValueError("investigation signal promotion reason is invalid")
        tool_calls = capacity.get("toolCalls")
        evidence_items = capacity.get("evidenceItems")
        if (
            isinstance(tool_calls, bool)
            or not isinstance(tool_calls, int)
            or not 1 <= tool_calls <= 200
            or isinstance(evidence_items, bool)
            or not isinstance(evidence_items, int)
            or not 1 <= evidence_items <= 1000
        ):
            raise ValueError("investigation signal promotion capacity is invalid")
        return cls(
            trigger_signal=trigger_signal,
            trigger_selection_id=trigger_selection_id,
            trigger_outcome=trigger["outcome"],
            candidate_signal=candidate_signal,
            candidate_selection_id=candidate_selection_id,
            remaining_tool_calls=tool_calls,
            remaining_evidence_items=evidence_items,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position": 1,
            "trigger": {
                "signal": self.trigger_signal,
                "selectionId": self.trigger_selection_id,
                "outcome": self.trigger_outcome,
            },
            "candidate": {
                "signal": self.candidate_signal,
                "selectionId": self.candidate_selection_id,
                "initialReason": "budget-exhausted",
            },
            "remainingCapacity": {
                "toolCalls": self.remaining_tool_calls,
                "evidenceItems": self.remaining_evidence_items,
            },
        }


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

    @property
    def signal_plan(self) -> Optional[Mapping[str, Any]]:
        """Return the auditable cross-signal plan when the runtime emitted one."""

        spec = self.payload.get("spec")
        value = spec.get("signalPlan") if isinstance(spec, Mapping) else None
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("investigation signalPlan must be an object")
        return dict(value)

    @property
    def signal_promotions(self) -> tuple[InvestigationSignalPromotion, ...]:
        """Return adaptive promotions; fixed plans return an empty tuple."""

        plan = self.signal_plan
        if plan is None:
            return ()
        replanning = plan.get("replanning")
        if replanning is None:
            return ()
        if not isinstance(replanning, Mapping):
            raise ValueError("investigation signalPlan replanning must be an object")
        promotions = replanning.get("promotions")
        if not isinstance(promotions, list) or any(
            not isinstance(item, Mapping) for item in promotions
        ):
            raise ValueError("investigation signalPlan promotions must be an array")
        return tuple(
            InvestigationSignalPromotion.from_dict(item) for item in promotions
        )

    @property
    def telemetry_assessments(
        self,
    ) -> tuple[
        InvestigationTelemetryAssessment
        | InvestigationTelemetryBaselineAssessment
        | InvestigationTelemetrySeasonalBaselineAssessment,
        ...,
    ]:
        """Return structured interpretations without importing server classes."""

        spec = self.payload.get("spec")
        values = spec.get("telemetryAssessments", []) if isinstance(spec, Mapping) else []
        if not isinstance(values, list) or any(
            not isinstance(value, Mapping) for value in values
        ):
            raise ValueError("investigation telemetryAssessments must be an array")
        return tuple(
            (
                InvestigationTelemetrySeasonalBaselineAssessment.from_dict(value)
                if value.get("assessmentType") == "seasonal-baseline-comparison"
                else InvestigationTelemetryBaselineAssessment.from_dict(value)
                if value.get("assessmentType") == "baseline-comparison"
                else InvestigationTelemetryAssessment.from_dict(value)
            )
            for value in values
        )

    @property
    def kubernetes_event_assessments(
        self,
    ) -> tuple[InvestigationKubernetesEventAssessment, ...]:
        """Return structured event assessments and their Evidence citations."""

        spec = self.payload.get("spec")
        values = (
            spec.get("kubernetesEventAssessments", [])
            if isinstance(spec, Mapping)
            else []
        )
        if not isinstance(values, list) or any(
            not isinstance(value, Mapping) for value in values
        ):
            raise ValueError("investigation kubernetesEventAssessments must be an array")
        return tuple(
            InvestigationKubernetesEventAssessment.from_dict(value)
            for value in values
        )

    @property
    def log_assessments(self) -> tuple[InvestigationLogAssessment, ...]:
        """Return structured log-count assessments and Evidence citations."""

        spec = self.payload.get("spec")
        values = spec.get("logAssessments", []) if isinstance(spec, Mapping) else []
        if not isinstance(values, list) or any(
            not isinstance(value, Mapping) for value in values
        ):
            raise ValueError("investigation logAssessments must be an array")
        return tuple(InvestigationLogAssessment.from_dict(value) for value in values)

    @property
    def change_assessments(self) -> tuple[InvestigationChangeAssessment, ...]:
        """Return structured change assessments and Evidence citations."""

        spec = self.payload.get("spec")
        values = spec.get("changeAssessments", []) if isinstance(spec, Mapping) else []
        if not isinstance(values, list) or any(
            not isinstance(value, Mapping) for value in values
        ):
            raise ValueError("investigation changeAssessments must be an array")
        return tuple(InvestigationChangeAssessment.from_dict(value) for value in values)

    @property
    def context_assessments(self) -> tuple[InvestigationContextAssessment, ...]:
        """Return context-count assessments without exposing excerpt text."""

        spec = self.payload.get("spec")
        values = spec.get("contextAssessments", []) if isinstance(spec, Mapping) else []
        if not isinstance(values, list) or any(
            not isinstance(value, Mapping) for value in values
        ):
            raise ValueError("investigation contextAssessments must be an array")
        return tuple(InvestigationContextAssessment.from_dict(value) for value in values)


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


@dataclass(frozen=True)
class IntegrationConfig:
    """Credential-free tenant integration configuration."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IntegrationConfig":
        return cls(
            _validate_envelope(
                payload, kind="IntegrationConfig", label="integration config"
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class EventDeliveryReplayCommand:
    """Typed command for one exact governed quarantine-generation replay."""

    investigation_id: str
    target_resource_uid: str
    outbox_id: int
    event_id: str
    quarantined_at: str
    attempts: int
    idempotency_key: str
    expires_at: str
    dry_run: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "investigationId": self.investigation_id,
            "actionType": "event-delivery.requeue",
            "targetResourceUid": self.target_resource_uid,
            "parameters": {
                "outboxId": self.outbox_id,
                "eventId": self.event_id,
                "quarantinedAt": self.quarantined_at,
                "attempts": self.attempts,
            },
            "idempotencyKey": self.idempotency_key,
            "expiresAt": self.expires_at,
            "dryRun": self.dry_run,
        }


@dataclass(frozen=True)
class ActionProposal:
    """Immutable proposal for one governed operation."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ActionProposal":
        return cls(
            _validate_envelope(payload, kind="ActionProposal", label="action proposal")
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ActionApproval:
    """Decision made by an actor distinct from the proposer."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ActionApproval":
        return cls(
            _validate_envelope(payload, kind="ActionApproval", label="action approval")
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ActionExecutionStatus:
    """Durable one-shot execution state, including fail-closed recovery."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ActionExecutionStatus":
        return cls(
            _validate_envelope(
                payload,
                kind="ActionExecutionStatus",
                label="action execution status",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ActionResult:
    """Auditable terminal result of a governed operation."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ActionResult":
        return cls(_validate_envelope(payload, kind="ActionResult", label="action result"))

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ActionWorkflow:
    """Reconstructed proposal, approval, execution, and result read model."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ActionWorkflow":
        return cls(
            _validate_envelope(payload, kind="ActionWorkflow", label="action workflow")
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ActionWorkflowPage:
    """Bounded tenant-scoped page of governed action workflows."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ActionWorkflowPage":
        return cls(
            _validate_envelope(
                payload,
                kind="ActionWorkflowPage",
                label="action workflow page",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PluginSession:
    """Bounded host-issued plugin handshake result."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginSession":
        return cls(
            _validate_envelope(payload, kind="PluginSession", label="plugin session")
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PluginManifest:
    """Declarative plugin capabilities, permissions, artifact, and signature."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginManifest":
        return cls(_validate_envelope(payload, kind="Plugin", label="plugin manifest"))

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PluginCompatibilityReport:
    """Exact-host compatibility evidence generated by plugin conformance."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginCompatibilityReport":
        return cls(
            _validate_envelope(
                payload,
                kind="PluginCompatibilityReport",
                label="plugin compatibility report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PluginInvocation:
    """Request-scoped plugin method input without capability bearer material."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginInvocation":
        return cls(
            _validate_envelope(
                payload, kind="PluginInvocation", label="plugin invocation"
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


def _validate_plugin_runtime_envelope(
    payload: Mapping[str, Any], *, kind: str, label: str
) -> Dict[str, Any]:
    if payload.get("apiVersion") != "iip.plugin-runtime/v1alpha1":
        raise ValueError(f"unsupported {label} apiVersion")
    if payload.get("kind") != kind:
        raise ValueError(f"{label} kind must be {kind}")
    if not isinstance(payload.get("metadata"), Mapping):
        raise ValueError(f"{label} metadata must be an object")
    if not isinstance(payload.get("spec"), Mapping):
        raise ValueError(f"{label} spec must be an object")
    return dict(payload)


@dataclass(frozen=True)
class PluginMediationGrant:
    """Host-issued metadata for one invocation-scoped provider read grant."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginMediationGrant":
        return cls(
            _validate_envelope(
                payload, kind="PluginMediationGrant", label="plugin mediation grant"
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PluginMediationRequest:
    """One destination-free JSON GET request for the invocation-local socket."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginMediationRequest":
        return cls(
            _validate_plugin_runtime_envelope(
                payload,
                kind="PluginMediationRequest",
                label="plugin mediation request",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PluginMediationResponse:
    """Bounded JSON provider data or a stable host-created failure."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginMediationResponse":
        return cls(
            _validate_plugin_runtime_envelope(
                payload,
                kind="PluginMediationResponse",
                label="plugin mediation response",
            )
        )

    @property
    def succeeded(self) -> bool:
        spec = self.payload.get("spec")
        return isinstance(spec, Mapping) and spec.get("status") == "succeeded"

    @property
    def body(self) -> object:
        spec = self.payload.get("spec")
        return spec.get("body") if isinstance(spec, Mapping) else None

    @property
    def error_code(self) -> Optional[str]:
        spec = self.payload.get("spec")
        error = spec.get("error") if isinstance(spec, Mapping) else None
        code = error.get("code") if isinstance(error, Mapping) else None
        return code if isinstance(code, str) else None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PluginActionMediationGrant:
    """Host-issued proposal-only action bounds for one plugin invocation."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginActionMediationGrant":
        return cls(
            _validate_envelope(
                payload,
                kind="PluginActionMediationGrant",
                label="plugin action mediation grant",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PluginActionMediationRequest:
    """Untrusted request for one ordinary governed action proposal."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginActionMediationRequest":
        return cls(
            _validate_plugin_runtime_envelope(
                payload,
                kind="PluginActionMediationRequest",
                label="plugin action mediation request",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PluginActionMediationResponse:
    """Host-created action proposal receipt or stable failure."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginActionMediationResponse":
        return cls(
            _validate_plugin_runtime_envelope(
                payload,
                kind="PluginActionMediationResponse",
                label="plugin action mediation response",
            )
        )

    @property
    def proposed(self) -> bool:
        spec = self.payload.get("spec")
        return isinstance(spec, Mapping) and spec.get("status") == "proposed"

    @property
    def proposal_id(self) -> Optional[str]:
        spec = self.payload.get("spec")
        value = spec.get("proposalId") if isinstance(spec, Mapping) else None
        return value if isinstance(value, str) else None

    @property
    def error_code(self) -> Optional[str]:
        spec = self.payload.get("spec")
        error = spec.get("error") if isinstance(spec, Mapping) else None
        code = error.get("code") if isinstance(error, Mapping) else None
        return code if isinstance(code, str) else None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PluginInvocationResult:
    """Durably replayable host result with output or a stable terminal error."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginInvocationResult":
        return cls(
            _validate_envelope(
                payload,
                kind="PluginInvocationResult",
                label="plugin invocation result",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PluginInvocationStatus:
    """Tenant-scoped durable claim, cancellation, and terminal-result state."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginInvocationStatus":
        return cls(
            _validate_envelope(
                payload,
                kind="PluginInvocationStatus",
                label="plugin invocation status",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PluginInvocationCancellationRequest:
    """Auditable request to stop a claimed plugin invocation cooperatively."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "PluginInvocationCancellationRequest":
        return cls(
            _validate_envelope(
                payload,
                kind="PluginInvocationCancellationRequest",
                label="plugin invocation cancellation request",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PluginInvocationReconciliationRequest:
    """Privileged request to close a post-deadline ambiguous invocation."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "PluginInvocationReconciliationRequest":
        return cls(
            _validate_envelope(
                payload,
                kind="PluginInvocationReconciliationRequest",
                label="plugin invocation reconciliation request",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiUsageRecord:
    """Metadata-only generative-AI invocation accepted from telemetry."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AiUsageRecord":
        return cls(
            _validate_envelope(
                payload,
                kind="AiUsageRecord",
                label="AI usage record",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiAttributionPolicy:
    """Protected versioned mapping from service identity to application/team."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AiAttributionPolicy":
        return cls(
            _validate_envelope(
                payload,
                kind="AiAttributionPolicy",
                label="AI attribution policy",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiUsageAttributionRecord:
    """Immutable application/team resolution for one AI usage record."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "AiUsageAttributionRecord":
        return cls(
            _validate_envelope(
                payload,
                kind="AiUsageAttributionRecord",
                label="AI usage attribution record",
            )
        )

    @property
    def status(self) -> Optional[str]:
        spec = self.payload.get("spec")
        resolution = spec.get("resolution") if isinstance(spec, Mapping) else None
        value = resolution.get("status") if isinstance(resolution, Mapping) else None
        return value if isinstance(value, str) else None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiAllocationReport:
    """Bounded application or team view over immutable AI economics facts."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AiAllocationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="AiAllocationReport",
                label="AI allocation report",
            )
        )

    @property
    def groups(self) -> tuple[Mapping[str, Any], ...]:
        spec = self.payload.get("spec")
        groups = spec.get("groups") if isinstance(spec, Mapping) else None
        if not isinstance(groups, list) or any(
            not isinstance(item, Mapping) for item in groups
        ):
            raise ValueError("AI allocation report groups are invalid")
        return tuple(dict(item) for item in groups)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiEconomicsInvocationObservationRequest:
    """Protected trace/span request for one exact AI economics observation."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "AiEconomicsInvocationObservationRequest":
        if (
            set(payload) != {"apiVersion", "kind", "spec"}
            or payload.get("apiVersion") != API_VERSION
            or payload.get("kind") != "AiEconomicsInvocationObservationRequest"
            or not isinstance(payload.get("spec"), Mapping)
        ):
            raise ValueError("AI economics invocation observation request is invalid")
        spec = payload["spec"]
        assert isinstance(spec, Mapping)
        if (
            set(spec) != {"traceId", "spanId"}
            or not isinstance(spec.get("traceId"), str)
            or re.fullmatch(r"[a-f0-9]{32}", spec["traceId"]) is None
            or not isinstance(spec.get("spanId"), str)
            or re.fullmatch(r"[a-f0-9]{16}", spec["spanId"]) is None
        ):
            raise ValueError("AI economics invocation observation request is invalid")
        return cls(dict(payload))

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiEconomicsInvocationObservation:
    """Digest-correlated usage, attribution, and calculated-cost progress."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "AiEconomicsInvocationObservation":
        return cls(
            _validate_envelope(
                payload,
                kind="AiEconomicsInvocationObservation",
                label="AI economics invocation observation",
            )
        )

    @property
    def status(self) -> Optional[str]:
        spec = self.payload.get("spec")
        value = spec.get("status") if isinstance(spec, Mapping) else None
        return value if isinstance(value, str) else None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiPriceCatalog:
    """Versioned data-driven token prices used for calculated cost."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AiPriceCatalog":
        return cls(
            _validate_envelope(
                payload,
                kind="AiPriceCatalog",
                label="AI price catalog",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AwsBedrockPriceCatalogImportPolicy:
    """Protected exact mapping from an AWS price snapshot into a catalog."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "AwsBedrockPriceCatalogImportPolicy":
        return cls(
            _validate_envelope(
                payload,
                kind="AwsBedrockPriceCatalogImportPolicy",
                label="AWS Bedrock price catalog import policy",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiPriceCatalogImportReport:
    """Minimized reproducibility evidence for one provider price import."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "AiPriceCatalogImportReport":
        return cls(
            _validate_envelope(
                payload,
                kind="AiPriceCatalogImportReport",
                label="AI price catalog import report",
            )
        )

    @property
    def status(self) -> Optional[str]:
        spec = self.payload.get("spec")
        value = spec.get("status") if isinstance(spec, Mapping) else None
        return value if isinstance(value, str) else None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiPriceCatalogQualificationPolicy:
    """Protected freshness and exact-scope requirements for a price catalog."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "AiPriceCatalogQualificationPolicy":
        return cls(
            _validate_envelope(
                payload,
                kind="AiPriceCatalogQualificationPolicy",
                label="AI price catalog qualification policy",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiPriceCatalogQualificationReport:
    """Minimized reproducible evidence for one exact price catalog and policy."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "AiPriceCatalogQualificationReport":
        return cls(
            _validate_envelope(
                payload,
                kind="AiPriceCatalogQualificationReport",
                label="AI price catalog qualification report",
            )
        )

    @property
    def status(self) -> Optional[str]:
        spec = self.payload.get("spec")
        value = spec.get("status") if isinstance(spec, Mapping) else None
        return value if isinstance(value, str) else None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiCostRecord:
    """Explainable calculated-cost result for one AI usage record."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AiCostRecord":
        return cls(
            _validate_envelope(
                payload,
                kind="AiCostRecord",
                label="AI cost record",
            )
        )

    @property
    def cost_status(self) -> Optional[str]:
        spec = self.payload.get("spec")
        result = spec.get("result") if isinstance(spec, Mapping) else None
        value = result.get("costStatus") if isinstance(result, Mapping) else None
        return value if isinstance(value, str) else None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiModelSuitabilityReport:
    """Protected time-bounded evidence for one candidate/reference model pair."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "AiModelSuitabilityReport":
        return cls(
            _validate_envelope(
                payload,
                kind="AiModelSuitabilityReport",
                label="AI model suitability report",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiSavingsFinding:
    """Deterministic evidence-backed AI economics finding."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AiSavingsFinding":
        return cls(
            _validate_envelope(
                payload,
                kind="AiSavingsFinding",
                label="AI savings finding",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class AiSavingsFindingPage:
    """Bounded newest-first page of tenant-scoped AI savings findings."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AiSavingsFindingPage":
        return cls(
            _validate_envelope(
                payload,
                kind="AiSavingsFindingPage",
                label="AI savings finding page",
            )
        )

    @property
    def items(self) -> tuple[AiSavingsFinding, ...]:
        spec = self.payload.get("spec")
        items = spec.get("items") if isinstance(spec, Mapping) else None
        if not isinstance(items, list) or any(
            not isinstance(item, Mapping) for item in items
        ):
            raise ValueError("AI savings finding page items are invalid")
        return tuple(AiSavingsFinding.from_dict(item) for item in items)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PolicyDecisionRequest:
    """Authenticated, tenant-scoped input sent to a replaceable policy service."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PolicyDecisionRequest":
        return cls(
            _validate_envelope(
                payload,
                kind="PolicyDecisionRequest",
                label="policy decision request",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class PolicyDecision:
    """Authorization result bound to one canonical input and policy snapshot."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PolicyDecision":
        return cls(
            _validate_envelope(
                payload,
                kind="PolicyDecision",
                label="policy decision",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)
