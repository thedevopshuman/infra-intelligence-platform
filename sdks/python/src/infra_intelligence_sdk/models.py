"""Public SDK models independent from server implementation classes."""

from __future__ import annotations

import math
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
class InvestigationTelemetrySelection:
    """Provider-neutral metric candidate bounded by an investigation request."""

    selection_id: str
    integration_id: str
    query: Mapping[str, Any]
    limits: Mapping[str, Any]
    root_cause_classes: tuple[str, ...] = ()
    interpretation: Optional[InvestigationTelemetryInterpretation] = None
    baseline_comparison: Optional[InvestigationTelemetryBaselineComparison] = None

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
        if interpretation is not None and baseline_comparison is not None:
            raise ValueError("investigation telemetry assessment rule is ambiguous")
        if (
            interpretation is not None or baseline_comparison is not None
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
    def telemetry_assessments(
        self,
    ) -> tuple[
        InvestigationTelemetryAssessment | InvestigationTelemetryBaselineAssessment,
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
            InvestigationTelemetryBaselineAssessment.from_dict(value)
            if value.get("assessmentType") == "baseline-comparison"
            else InvestigationTelemetryAssessment.from_dict(value)
            for value in values
        )


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
class ActionProposal:
    """Immutable proposal for one reversible governed operation."""

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
class ActionResult:
    """Auditable terminal result of a governed operation."""

    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ActionResult":
        return cls(_validate_envelope(payload, kind="ActionResult", label="action result"))

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
