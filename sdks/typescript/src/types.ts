export type ResourceHealth = "healthy" | "degraded" | "unhealthy" | "unknown";
export type ResourceUid = `res_${string}`;
export type EvidenceId = `evd_${string}`;
export type InvestigationId = `inv_${string}`;
export type InvestigationCancellationId = `can_${string}`;
export type ActionId = `act_${string}`;
export type ApprovalId = `apr_${string}`;
export type IntegrationId = `int_${string}`;
export type PluginSessionId = `psn_${string}`;
export type Sha256Digest = `sha256:${string}`;
export type ResourceLifecycle =
  | "active"
  | "creating"
  | "updating"
  | "deleting"
  | "deleted"
  | "unknown";

export interface SessionContext {
  apiVersion: "iip.platform/v1alpha1";
  kind: "SessionContext";
  metadata: {
    tenantId: string;
    actorId: string;
  };
  spec: {
    roles: string[];
  };
}

export interface ResourceRelationship {
  type: string;
  target: string;
  direction?: "outbound" | "inbound";
  attributes?: Record<string, unknown>;
}

export interface ResourceObservationCursorBase {
  sourceId: string;
  streamId: `obs_${string}`;
  sequence: number;
  resourceVersion?: string;
  checkpoint?: string;
}

export type ResourceObservationCursor = ResourceObservationCursorBase &
  (
    | { mode: "incremental"; snapshotId?: never }
    | { mode: "reconciliation"; snapshotId: `snap_${string}` }
  );

export interface ResourceObservation {
  apiVersion: "iip.platform/v1alpha1";
  kind: "Resource";
  metadata: {
    uid?: ResourceUid;
    tenantId: string;
    observedAt: string;
    observation?: ResourceObservationCursor;
    labels?: Record<string, string>;
  };
  spec: {
    provider: string;
    type: string;
    externalId: string;
    displayName?: string;
    attributes?: Record<string, unknown>;
    relationships?: ResourceRelationship[];
  };
  status?: {
    health?: ResourceHealth;
    lifecycle?: ResourceLifecycle;
  };
}

export interface ResourceCollectionScope {
  provider: string;
  integrationId: string;
  rootExternalId: string;
  parameters?: Record<string, unknown>;
}

export interface ResourceCollectionResume {
  checkpoint: string;
  providerCursors: Record<string, string>;
}

export interface ResourceCollectionRequestBase {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ResourceCollectionRequest";
  metadata: {
    requestId: `col_${string}`;
    tenantId: string;
    actorId: string;
    requestedAt: string;
  };
}

export type ResourceCollectionRequest = ResourceCollectionRequestBase & {
  spec: {
    sourceId: string;
    streamId: `obs_${string}`;
    startSequence: number;
    scope: ResourceCollectionScope;
    limits: {
      maxResources: number;
      maxOutputBytes: number;
    };
    deadline: string;
    resume?: ResourceCollectionResume;
  } &
    (
      | { mode: "incremental"; snapshotId?: never }
      | { mode: "reconciliation"; snapshotId: `snap_${string}` }
    );
};

export type ResourceCollectionCompletion = {
  resourceCount: number;
  nextSequence: number;
  snapshotId?: `snap_${string}`;
  scopeDigest: Sha256Digest;
} &
  (
    | {
        status: "complete";
        checkpoint: string;
        providerCursors?: Record<string, string>;
        reasonCode?: never;
      }
    | {
        status: "partial" | "failed" | "cancelled";
        reasonCode: string;
        checkpoint?: never;
        providerCursors?: never;
      }
  );

export interface ResourceCollectionResult {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ResourceCollectionResult";
  metadata: {
    requestId: `col_${string}`;
    tenantId: string;
    sourceId: string;
    createdAt: string;
  };
  spec: {
    observations: ResourceObservation[];
    completion: ResourceCollectionCompletion;
  };
}

export interface PageInfo {
  limit: number;
  hasMore: boolean;
  nextCursor?: `p1.${string}`;
}

export interface ResourceGraphEdge {
  id: `rel_${string}`;
  observedResourceUid: ResourceUid;
  type: string;
  source: string;
  target: string;
  attributes: Record<string, unknown>;
}

export interface ResourceNeighborhood {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ResourceNeighborhood";
  metadata: {
    tenantId: string;
    rootResourceUid: ResourceUid;
  };
  spec: {
    depth: 1;
    direction: "incoming" | "outgoing" | "both";
    nodes: ResourceObservation[];
    edges: ResourceGraphEdge[];
    page: PageInfo;
  };
}

export interface ResourceTimelineItem {
  offset: number;
  recordedAt: string;
  disposition: "accepted" | "stale" | "conflict";
  observationHash: string;
  resource: ResourceObservation;
}

export interface ResourceTimeline {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ResourceTimeline";
  metadata: {
    tenantId: string;
    resourceUid: ResourceUid;
  };
  spec: {
    items: ResourceTimelineItem[];
    page: PageInfo;
  };
}

export type IngestionFreshnessViolation =
  | "checkpoint-age-exceeded"
  | "observation-age-exceeded"
  | "ingestion-delay-exceeded"
  | "pending-event-age-exceeded"
  | "clock-skew-detected";

export interface IngestionFreshnessReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "IngestionFreshnessReport";
  metadata: {
    tenantId: string;
    sourceId: string;
    evaluatedAt: string;
  };
  spec: {
    status: "within-objective" | "breached";
    streamId: `obs_${string}`;
    checkpoint: {
      sequence: number;
      committedAt: string;
      ageSeconds: number;
    };
    latestObservation?: {
      observedAt: string;
      recordedAt: string;
      ageSeconds: number;
      ingestionDelaySeconds: number;
    };
    acceptedObservationCount: number;
    delivery:
      | { pendingEvents: 0; oldestPendingEventAgeSeconds?: never }
      | { pendingEvents: number; oldestPendingEventAgeSeconds: number };
    objectives: {
      maximumCheckpointAgeSeconds: number;
      maximumObservationAgeSeconds: number;
      maximumIngestionDelaySeconds: number;
      maximumPendingEventAgeSeconds: number;
      maximumClockSkewSeconds: number;
    };
    violations: IngestionFreshnessViolation[];
  };
}

export interface PlatformEvent<TData extends Record<string, unknown> = Record<string, unknown>> {
  specversion: "1.0";
  id: string;
  source: string;
  type: `io.iip.${string}.v${number}`;
  time: string;
  subject: string;
  datacontenttype?: "application/json";
  tenantid: string;
  correlationid?: string;
  causationid?: string;
  traceparent?: string;
  data: TData;
}

export interface Evidence {
  apiVersion: "iip.platform/v1alpha1";
  kind: "Evidence";
  metadata: {
    id: EvidenceId;
    tenantId: string;
    recordedAt: string;
  };
  spec: {
    type: string;
    source: {
      provider: string;
      integrationId: string;
      locator: string;
    };
    observedAt: string;
    retrievedAt: string;
    resourceRefs: ResourceUid[];
    query?: string;
    summary: string;
    artifact: {
      mediaType: string;
      contentHash: Sha256Digest;
      sizeBytes: number;
      storageRef: string;
      encoding?: "identity" | "gzip" | "zstd";
    };
    handling: {
      redaction: {
        status: "not-required" | "applied";
        methods: string[];
      };
      sensitivity: "public" | "internal" | "confidential" | "restricted";
      retentionClass: "ephemeral" | "standard" | "extended" | "legal-hold";
      expiresAt?: string;
    };
  };
}

export type TelemetryAggregation =
  | "avg"
  | "min"
  | "max"
  | "sum"
  | "count"
  | "rate"
  | "p50"
  | "p95"
  | "p99";

export interface TelemetryMetricQuery {
  metric: string;
  filters: {
    attribute: string;
    operator: "eq" | "neq";
    value: string;
  }[];
  aggregation: {
    function: TelemetryAggregation;
    stepSeconds: number;
  };
  groupBy: string[];
}

export interface TelemetryEvidenceLimits {
  maxSeries: number;
  maxDataPoints: number;
  maxBytes: number;
}

export interface TelemetryEvidenceRequest {
  apiVersion: "iip.platform/v1alpha1";
  kind: "TelemetryEvidenceRequest";
  metadata: {
    requestId: `teq_${string}`;
    tenantId: string;
    actorId: string;
    requestedAt: string;
  };
  spec: {
    integrationId: string;
    resourceRefs: ResourceUid[];
    signal: "metrics";
    timeRange: { start: string; end: string };
    query: TelemetryMetricQuery;
    limits: TelemetryEvidenceLimits;
    deadline: string;
  };
}

export type TelemetryEvidenceWarning =
  | "backend-partial"
  | "series-limit"
  | "data-point-limit"
  | "resolution-adjusted";

export interface TelemetryEvidenceResult {
  apiVersion: "iip.platform/v1alpha1";
  kind: "TelemetryEvidenceResult";
  metadata: {
    requestId: `teq_${string}`;
    tenantId: string;
    integrationId: string;
    createdAt: string;
  };
  spec: {
    signal: "metrics";
    requestDigest: Sha256Digest;
    timeRange: { start: string; end: string };
    status: "complete" | "partial" | "no-data";
    series: {
      metric: string;
      unit: string;
      attributes: Record<string, string>;
      points: { timestamp: string; value: number }[];
    }[];
    summary: {
      seriesCount: number;
      dataPointCount: number;
    };
    warnings: TelemetryEvidenceWarning[];
  };
}

export type LogSeverity =
  | "trace"
  | "debug"
  | "info"
  | "warn"
  | "error"
  | "fatal"
  | "unspecified";

export interface LogEvidenceQuery {
  serviceNames: string[];
  severities: LogSeverity[];
  filters: { attribute: string; operator: "eq" | "neq"; value: string }[];
}

export interface LogEvidenceLimits {
  maxRecords: number;
  maxBytes: number;
}

export interface LogEvidenceRequest {
  apiVersion: "iip.platform/v1alpha1";
  kind: "LogEvidenceRequest";
  metadata: {
    requestId: `leq_${string}`;
    tenantId: string;
    actorId: string;
    requestedAt: string;
  };
  spec: {
    integrationId: string;
    resourceRefs: ResourceUid[];
    signal: "logs";
    timeRange: { start: string; end: string };
    query: LogEvidenceQuery;
    limits: LogEvidenceLimits;
    deadline: string;
  };
}

export interface NormalizedLogRecord {
  id: `log_${string}`;
  resourceRef: ResourceUid;
  timestamp: string;
  observedTimestamp?: string;
  severity: LogSeverity;
  serviceName: string;
  body: string;
  attributes: Record<string, string>;
  traceId?: string;
  spanId?: string;
}

export interface LogEvidenceResult {
  apiVersion: "iip.platform/v1alpha1";
  kind: "LogEvidenceResult";
  metadata: {
    requestId: `leq_${string}`;
    tenantId: string;
    integrationId: string;
    createdAt: string;
  };
  spec: {
    signal: "logs";
    requestDigest: Sha256Digest;
    timeRange: { start: string; end: string };
    status: "complete" | "partial" | "no-data";
    records: NormalizedLogRecord[];
    summary: { recordCount: number; errorCount: number };
    warnings: ("backend-partial" | "record-limit")[];
  };
}

export interface KubernetesEventQuery {
  severities: ("normal" | "warning")[];
  reasons: string[];
}

export interface KubernetesEventEvidenceLimits {
  maxEvents: number;
  maxBytes: number;
}

export interface KubernetesEventEvidenceRequest {
  apiVersion: "iip.platform/v1alpha1";
  kind: "KubernetesEventEvidenceRequest";
  metadata: {
    requestId: `keq_${string}`;
    tenantId: string;
    actorId: string;
    requestedAt: string;
  };
  spec: {
    integrationId: string;
    resourceRefs: ResourceUid[];
    timeRange: { start: string; end: string };
    query: KubernetesEventQuery;
    limits: KubernetesEventEvidenceLimits;
    deadline: string;
  };
}

export interface KubernetesEventEvidenceResult {
  apiVersion: "iip.platform/v1alpha1";
  kind: "KubernetesEventEvidenceResult";
  metadata: {
    requestId: `keq_${string}`;
    tenantId: string;
    integrationId: string;
    createdAt: string;
  };
  spec: {
    requestDigest: Sha256Digest;
    timeRange: { start: string; end: string };
    status: "complete" | "partial" | "no-data";
    events: {
      id: `kve_${string}`;
      resourceRef: ResourceUid;
      severity: "normal" | "warning";
      reason: string;
      condition: string;
      firstObservedAt: string;
      lastObservedAt: string;
      occurrenceCount: number;
      reportingController?: string;
      message?: string;
    }[];
    summary: { eventCount: number; warningEventCount: number };
    warnings: ("backend-partial" | "event-limit")[];
  };
}

export type ResourceChangeKind =
  | "created"
  | "configuration"
  | "image"
  | "scale"
  | "relationships"
  | "status"
  | "deleted";

export interface ResourceChangeEvidenceRequest {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ResourceChangeEvidenceRequest";
  metadata: {
    requestId: `ceq_${string}`;
    tenantId: string;
    actorId: string;
    requestedAt: string;
  };
  spec: {
    integrationId: string;
    resourceRefs: ResourceUid[];
    timeRange: { start: string; end: string };
    query: { changeKinds: ResourceChangeKind[] };
    limits: {
      maxChanges: number;
      maxObservationsPerResource: number;
      maxBytes: number;
    };
    deadline: string;
  };
}

export interface ResourceChangeEvidenceResult {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ResourceChangeEvidenceResult";
  metadata: {
    requestId: `ceq_${string}`;
    tenantId: string;
    integrationId: string;
    createdAt: string;
  };
  spec: {
    requestDigest: Sha256Digest;
    timeRange: { start: string; end: string };
    status: "complete" | "partial" | "no-data";
    changes: {
      id: `chg_${string}`;
      resourceRef: ResourceUid;
      kind: ResourceChangeKind;
      observedAt: string;
      recordedAt: string;
      beforeObservationHash?: string;
      afterObservationHash: string;
      changedPaths: string[];
      source: {
        sourceId: string;
        streamId: `obs_${string}`;
        sequence: number;
        resourceVersion?: string;
      };
    }[];
    summary: {
      changeCount: number;
      affectedResourceCount: number;
      countsByKind: Partial<Record<ResourceChangeKind, number>>;
    };
    warnings: ("observation-limit" | "change-limit")[];
  };
}

export type ContextDocumentKind =
  | "runbook"
  | "source"
  | "configuration"
  | "service-catalog";

export interface ContextEvidenceRequest {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ContextEvidenceRequest";
  metadata: {
    requestId: `ctq_${string}`;
    tenantId: string;
    actorId: string;
    requestedAt: string;
  };
  spec: {
    integrationId: string;
    resourceRefs: ResourceUid[];
    query: { kinds: ContextDocumentKind[]; referenceIds: string[] };
    limits: {
      maxDocuments: number;
      maxExcerptChars: number;
      maxBytes: number;
    };
    deadline: string;
  };
}

export interface ContextEvidenceResult {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ContextEvidenceResult";
  metadata: {
    requestId: `ctq_${string}`;
    tenantId: string;
    integrationId: string;
    createdAt: string;
  };
  spec: {
    requestDigest: Sha256Digest;
    status: "complete" | "partial" | "no-data";
    documents: {
      id: `ctx_${string}`;
      referenceId: string;
      resourceRefs: ResourceUid[];
      kind: ContextDocumentKind;
      title: string;
      locator: string;
      revision: string;
      excerpt: string;
      excerptHash: Sha256Digest;
      redactionMethods: string[];
      trust: "untrusted";
      instructionPolicy: "data-only";
    }[];
    summary: {
      documentCount: number;
      countsByKind: Partial<Record<ContextDocumentKind, number>>;
    };
    warnings: ("backend-partial" | "document-limit" | "excerpt-limit")[];
  };
}

export interface OtlpMetricsEvidence {
  apiVersion: "iip.platform/v1alpha1";
  kind: "OtlpMetricsEvidence";
  metadata: {
    tenantId: string;
    integrationId: string;
    channelId: string;
    receivedAt: string;
  };
  spec: {
    signal: "metrics";
    protocol: "otlp/http-protobuf";
    timeRange: { start: string; end: string };
    series: (
      | {
          metric: string;
          unit: string;
          kind: "gauge";
          attributes: Record<string, string>;
          points: { timestamp: string; value: number }[];
          temporality?: never;
          monotonic?: never;
        }
      | {
          metric: string;
          unit: string;
          kind: "sum";
          temporality: "delta" | "cumulative";
          monotonic: boolean;
          attributes: Record<string, string>;
          points: { timestamp: string; value: number }[];
        }
    )[];
    summary: {
      metricCount: number;
      seriesCount: number;
      dataPointCount: number;
    };
  };
}

export interface OtlpLogsEvidence {
  apiVersion: "iip.platform/v1alpha1";
  kind: "OtlpLogsEvidence";
  metadata: {
    tenantId: string;
    integrationId: string;
    channelId: string;
    receivedAt: string;
  };
  spec: {
    signal: "logs";
    protocol: "otlp/http-protobuf";
    timeRange: { start: string; end: string };
    records: NormalizedLogRecord[];
    summary: { serviceCount: number; recordCount: number; errorCount: number };
  };
}

export interface InvestigationScope {
  resourceUids: ResourceUid[];
  timeRange: {
    start: string;
    end: string;
  };
}

export interface InvestigationBudgets {
  maxToolCalls: number;
  maxWallTimeSeconds: number;
  maxModelTokens: number;
  maxCostUsd: number;
  maxEvidenceItems: number;
  maxIterations: number;
}

export interface InvestigationTelemetrySelection {
  id: `tqs_${string}`;
  integrationId: string;
  rootCauseClasses?: string[];
  query: TelemetryMetricQuery;
  limits: TelemetryEvidenceLimits;
  interpretation?: InvestigationTelemetryInterpretation;
  baselineComparison?: InvestigationTelemetryBaselineComparison;
  rollingBaselineComparison?: InvestigationTelemetryRollingBaselineComparison;
}

export interface InvestigationKubernetesEventInterpretation {
  conditions: string[];
  minMatches: number;
  whenMatched: "supports" | "contradicts" | "neutral";
  whenNotMatched: "supports" | "contradicts" | "neutral";
}

export interface InvestigationKubernetesEventSelection {
  id: `kes_${string}`;
  integrationId: string;
  rootCauseClasses?: string[];
  query: KubernetesEventQuery;
  limits: KubernetesEventEvidenceLimits;
  interpretation?: InvestigationKubernetesEventInterpretation;
}

export interface InvestigationLogInterpretation {
  minRecords: number;
  whenMatched: "supports" | "contradicts" | "neutral";
  whenNotMatched: "supports" | "contradicts" | "neutral";
}

export interface InvestigationLogSelection {
  id: `lqs_${string}`;
  integrationId: string;
  rootCauseClasses?: string[];
  query: LogEvidenceQuery;
  limits: LogEvidenceLimits;
  interpretation?: InvestigationLogInterpretation;
}

export interface InvestigationChangeInterpretation {
  minChanges: number;
  whenMatched: "supports" | "contradicts" | "neutral";
  whenNotMatched: "supports" | "contradicts" | "neutral";
}

export interface InvestigationChangeSelection {
  id: `cqs_${string}`;
  integrationId: string;
  rootCauseClasses?: string[];
  query: { changeKinds: ResourceChangeKind[] };
  limits: {
    maxChanges: number;
    maxObservationsPerResource: number;
    maxBytes: number;
  };
  interpretation?: InvestigationChangeInterpretation;
}

export interface InvestigationContextInterpretation {
  minDocuments: number;
  whenMatched: "supports" | "contradicts" | "neutral";
  whenNotMatched: "supports" | "contradicts" | "neutral";
}

export interface InvestigationContextSelection {
  id: `xqs_${string}`;
  integrationId: string;
  rootCauseClasses?: string[];
  query: { kinds: ContextDocumentKind[]; referenceIds: string[] };
  limits: {
    maxDocuments: number;
    maxExcerptChars: number;
    maxBytes: number;
  };
  interpretation?: InvestigationContextInterpretation;
}

export interface InvestigationTelemetryInterpretation {
  statistic: "minimum" | "maximum" | "mean";
  unit: string;
  operator: "lt" | "lte" | "gt" | "gte";
  threshold: number;
  whenMatched: "supports" | "contradicts" | "neutral";
  whenNotMatched: "supports" | "contradicts" | "neutral";
}

export interface InvestigationTelemetryBaselineComparison {
  statistic: "minimum" | "maximum" | "mean";
  unit: string;
  baselineTimeRange: { start: string; end: string };
  evaluationTimeRange: { start: string; end: string };
  calculation: "difference" | "ratio";
  operator: "lt" | "lte" | "gt" | "gte";
  threshold: number;
  whenMatched: "supports" | "contradicts" | "neutral";
  whenNotMatched: "supports" | "contradicts" | "neutral";
}

export interface InvestigationTelemetryRollingBaselineComparison {
  statistic: "minimum" | "maximum" | "mean";
  unit: string;
  baselineDurationSeconds: number;
  evaluationDurationSeconds: number;
  gapSeconds: number;
  calculation: "difference" | "ratio";
  operator: "lt" | "lte" | "gt" | "gte";
  threshold: number;
  whenMatched: "supports" | "contradicts" | "neutral";
  whenNotMatched: "supports" | "contradicts" | "neutral";
}

export interface InvestigationCancellationRequest {
  apiVersion: "iip.platform/v1alpha1";
  kind: "InvestigationCancellationRequest";
  metadata: {
    id: InvestigationCancellationId;
    tenantId: string;
    actorId: string;
    requestedAt: string;
  };
  spec: {
    investigationId: InvestigationId;
    reasonCode: "operator-requested" | "incident-resolved" | "superseded";
  };
}

export interface InvestigationStatus {
  apiVersion: "iip.platform/v1alpha1";
  kind: "InvestigationStatus";
  metadata: {
    id: InvestigationId;
    tenantId: string;
    updatedAt: string;
  };
  spec: {
    requestDigest: Sha256Digest;
    state:
      | "running"
      | "cancellation-requested"
      | "completed"
      | "failed"
      | "cancelled";
    startedAt: string;
    leaseExpiresAt?: string;
    cancellation?: {
      requestedBy: string;
      requestedAt: string;
      reasonCode: "operator-requested" | "incident-resolved" | "superseded";
    };
    completedAt?: string;
    reportRef?: string;
  };
}

export interface InvestigationRequest {
  apiVersion: "iip.platform/v1alpha1";
  kind: "InvestigationRequest";
  metadata: {
    id: InvestigationId;
    tenantId: string;
    actorId: string;
    requestedAt: string;
    correlationId?: string;
  };
  spec: {
    question: string;
    trigger: {
      type: "user" | "alert" | "event" | "scheduled";
      source: string;
      reference?: string;
      summary: string;
    };
    scope: InvestigationScope;
    agentSelector?: {
      id: string;
      version: string;
    };
    evidenceTypes?: string[];
    allowedTools?: string[];
    kubernetesEventSelections?: InvestigationKubernetesEventSelection[];
    changeSelections?: InvestigationChangeSelection[];
    contextSelections?: InvestigationContextSelection[];
    logSelections?: InvestigationLogSelection[];
    telemetrySelections?: InvestigationTelemetrySelection[];
    budgets: InvestigationBudgets;
    maxAuthority: "read" | "propose";
    priority?: "low" | "normal" | "high" | "critical";
  };
}

export interface InvestigationHypothesis {
  id: `hyp_${string}`;
  rank: number;
  statement: string;
  rootCauseClass?: string;
  confidence: number;
  disposition: "leading" | "alternative" | "rejected";
  supportingEvidenceIds: EvidenceId[];
  contradictingEvidenceIds: EvidenceId[];
}

export interface InvestigationUnknown {
  statement: string;
  impact: "low" | "medium" | "high";
  requestedEvidenceTypes: string[];
}

export interface InvestigationRecommendation {
  id: `rec_${string}`;
  type: "next-check" | "monitor" | "escalate";
  description: string;
  priority: "low" | "normal" | "high" | "critical";
  evidenceIds: EvidenceId[];
}

export interface InvestigationTelemetryAssessment {
  selectionId: `tqs_${string}`;
  evidenceId: EvidenceId;
  rootCauseClass: string;
  metric: string;
  statistic: "minimum" | "maximum" | "mean";
  unit: string;
  operator: "lt" | "lte" | "gt" | "gte";
  threshold: number;
  observedValue?: number;
  disposition:
    | "supporting"
    | "contradicting"
    | "neutral"
    | "no-data"
    | "incomplete";
}

export interface InvestigationKubernetesEventAssessment {
  selectionId: `kes_${string}`;
  evidenceId: EvidenceId;
  rootCauseClass: string;
  conditions: string[];
  minMatches: number;
  matchedEventCount?: number;
  matchedEventIds?: `kve_${string}`[];
  disposition:
    | "supporting"
    | "contradicting"
    | "neutral"
    | "no-data"
    | "incomplete";
}

export interface InvestigationLogAssessment {
  selectionId: `lqs_${string}`;
  evidenceId: EvidenceId;
  rootCauseClass: string;
  minRecords: number;
  observedRecordCount?: number;
  disposition:
    | "supporting"
    | "contradicting"
    | "neutral"
    | "no-data"
    | "incomplete";
}

export interface InvestigationChangeAssessment {
  selectionId: `cqs_${string}`;
  evidenceId: EvidenceId;
  rootCauseClass: string;
  changeKinds: ResourceChangeKind[];
  minChanges: number;
  observedChangeCount?: number;
  observedChangeIds?: `chg_${string}`[];
  disposition:
    | "supporting"
    | "contradicting"
    | "neutral"
    | "no-data"
    | "incomplete";
}

export interface InvestigationContextAssessment {
  selectionId: `xqs_${string}`;
  evidenceId: EvidenceId;
  rootCauseClass: string;
  kinds: ContextDocumentKind[];
  referenceIds: string[];
  minDocuments: number;
  observedDocumentCount?: number;
  observedDocumentIds?: `ctx_${string}`[];
  observedReferenceIds?: string[];
  disposition:
    | "supporting"
    | "contradicting"
    | "neutral"
    | "no-data"
    | "incomplete";
}

export interface InvestigationTelemetryBaselineAssessment {
  assessmentType: "baseline-comparison";
  selectionId: `tqs_${string}`;
  evidenceId: EvidenceId;
  rootCauseClass: string;
  metric: string;
  statistic: "minimum" | "maximum" | "mean";
  unit: string;
  baselineTimeRange: { start: string; end: string };
  evaluationTimeRange: { start: string; end: string };
  calculation: "difference" | "ratio";
  comparisonUnit: string;
  operator: "lt" | "lte" | "gt" | "gte";
  threshold: number;
  baselineValue?: number;
  evaluationValue?: number;
  comparisonValue?: number;
  disposition:
    | "supporting"
    | "contradicting"
    | "neutral"
    | "no-data"
    | "incomplete";
}

export interface InvestigationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "InvestigationReport";
  metadata: {
    id: InvestigationId;
    tenantId: string;
    createdAt: string;
  };
  spec: {
    requestDigest: Sha256Digest;
    outcome: "conclusive" | "inconclusive" | "failed" | "cancelled";
    terminalReason:
      | "sufficient-evidence"
      | "insufficient-evidence"
      | "budget-exhausted"
      | "deadline-exceeded"
      | "cancelled"
      | "policy-denied"
      | "runtime-error"
      | "no-applicable-agent";
    startedAt: string;
    completedAt: string;
    scope: InvestigationScope;
    agent?: {
      id: string;
      version: string;
      manifestDigest: Sha256Digest;
      modelClass: "fast" | "reasoning" | "local" | "policy-selected";
    };
    summary: string;
    hypotheses: InvestigationHypothesis[];
    unknowns: InvestigationUnknown[];
    signalPlan?: {
      strategy: "risk-aware-v1";
      rootCauseClass?: string;
      capacity: { toolCalls: number; evidenceItems: number };
      candidateCount: number;
      scheduledCount: number;
      deferredCount: number;
      steps: {
        position: number;
        signal:
          | "kubernetes.event"
          | "repository.context"
          | "resource.change"
          | "telemetry.metrics"
          | "telemetry.logs";
        selectionId: string;
        decision: "scheduled" | "deferred";
        reason:
          | "eligible"
          | "root-cause-mismatch"
          | "request-upper-bound"
          | "budget-exhausted";
      }[];
    };
    telemetryAssessments?: (
      | InvestigationTelemetryAssessment
      | InvestigationTelemetryBaselineAssessment
    )[];
    kubernetesEventAssessments?: InvestigationKubernetesEventAssessment[];
    changeAssessments?: InvestigationChangeAssessment[];
    contextAssessments?: InvestigationContextAssessment[];
    logAssessments?: InvestigationLogAssessment[];
    evidenceIds: EvidenceId[];
    recommendations: InvestigationRecommendation[];
    toolCallLedgerRef: string;
    policySnapshotRef: string;
    usage: {
      toolCalls: number;
      iterations: number;
      modelTokens: number;
      wallTimeSeconds: number;
      costUsd: number;
      evidenceItems: number;
    };
  };
}

export interface EvaluationScenarioExpectations {
  outcome: "conclusive" | "inconclusive" | "failed" | "cancelled";
  rootCauseClass: string;
  affectedResourceUids: ResourceUid[];
  requiredEvidenceIds: EvidenceId[];
  forbiddenEvidenceTypes: string[];
  redHerringEvidenceIds: EvidenceId[];
  adversarialEvidenceIds: EvidenceId[];
  prohibitedOutputFragments: string[];
}

export interface EvaluationScenarioScoring {
  passScore: number;
  hardGates: (
    | "root-cause"
    | "required-evidence"
    | "forbidden-evidence"
    | "instruction-boundary"
    | "budget"
  )[];
  weights: {
    rootCause: number;
    requiredEvidence: number;
    forbiddenEvidence: number;
    redHerringResistance: number;
    unsupportedCertainty: number;
    instructionBoundary: number;
    budgetCompliance: number;
  };
}

export interface EvaluationScenario {
  apiVersion: "iip.platform/v1alpha1";
  kind: "EvaluationScenario";
  metadata: {
    id: string;
    version: string;
    displayName: string;
    tenantId: string;
    createdAt: string;
  };
  spec: {
    description: string;
    tags: string[];
    fixtures: {
      graph: {
        rootResourceUids: ResourceUid[];
        resources: ResourceObservation[];
      };
      timelines: ResourceTimeline[];
      alert: PlatformEvent;
      evidence: Evidence[];
    };
    request: InvestigationRequest;
    expectations: EvaluationScenarioExpectations;
    scoring: EvaluationScenarioScoring;
  };
}

export interface IntegrationConfig {
  apiVersion: "iip.platform/v1alpha1";
  kind: "IntegrationConfig";
  metadata: { id: IntegrationId; tenantId: string; createdAt: string };
  spec: {
    provider: string;
    displayName: string;
    enabled: boolean;
    endpoint?: string;
    credentialRef: `credential://${string}` | `secret-ref://${string}`;
    collection: {
      sourceId: string;
      rootExternalId: string;
      mode: "reconciliation";
      intervalSeconds: number;
      parameters: Record<string, unknown>;
    };
    permissions: { readOnly: true; resourceTypes: string[] };
  };
}

export interface ActionProposal {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ActionProposal";
  metadata: { id: ActionId; tenantId: string; actorId: string; createdAt: string };
  spec: {
    investigationId: InvestigationId;
    investigationDigest?: Sha256Digest;
    integrationId?: string;
    actionType: string;
    targetResourceUid: ResourceUid;
    targetDigest?: Sha256Digest;
    providerObjectUid?: string;
    parameters: {
      namespace: string;
      workloadKind: "deployment" | "statefulset" | "daemonset";
      workloadName: string;
    };
    risk: "low" | "medium" | "high" | "critical";
    reversible: true;
    dryRun: boolean;
    idempotencyKey: string;
    expiresAt: string;
    policyDecision: {
      allowed: boolean;
      reasonCode: string;
      policySnapshotRef: string;
      inputDigest?: Sha256Digest;
    };
  };
  status: "pending-approval" | "denied" | "expired";
}

export interface ActionApproval {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ActionApproval";
  metadata: {
    id: ApprovalId;
    tenantId: string;
    approverId: string;
    decidedAt: string;
  };
  spec: {
    proposalId: ActionId;
    proposalDigest?: Sha256Digest;
    decision: "approved" | "rejected";
    rationale: string;
    policySnapshotRef: string;
    policyInputDigest?: Sha256Digest;
  };
}

export interface ActionExecutionStatus {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ActionExecutionStatus";
  metadata: { id: ActionId; tenantId: string; updatedAt: string };
  spec: {
    proposalDigest: Sha256Digest;
    approvalId: ApprovalId;
    state:
      | "executing"
      | "dry-run"
      | "succeeded"
      | "failed"
      | "rolled-back"
      | "manual-reconciliation-required";
    attempt: 1;
    executorActorId: string;
    startedAt: string;
    leaseExpiresAt?: string;
    completedAt?: string;
    operationRef?: string;
    summary?: string;
    policyDecision: {
      allowed: true;
      reasonCode: string;
      policySnapshotRef: string;
      inputDigest: Sha256Digest;
    };
  };
}

export interface ActionResult {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ActionResult";
  metadata: { id: ActionId; tenantId: string; completedAt: string };
  spec: {
    proposalDigest: Sha256Digest;
    approvalId: ApprovalId;
    outcome: "dry-run" | "succeeded" | "failed" | "rolled-back";
    idempotencyKey: string;
    execution: { provider: string; operationRef: string };
    verification: { status: "not-run" | "passed" | "failed"; summary: string };
    auditRef: string;
    executionStatusRef?: string;
    executionPolicyInputDigest?: Sha256Digest;
    errorCode?: string;
    rollback?: { status: "succeeded" | "failed"; summary: string };
  };
}

export type ActionWorkflowState =
  | "pending-approval"
  | "denied"
  | "approved"
  | "rejected"
  | "executing"
  | "dry-run"
  | "succeeded"
  | "failed"
  | "rolled-back"
  | "manual-reconciliation-required";

export interface ActionWorkflow {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ActionWorkflow";
  metadata: { id: ActionId; tenantId: string };
  spec: {
    state: ActionWorkflowState;
    proposal: ActionProposal;
    approval?: ActionApproval;
    executionStatus?: ActionExecutionStatus;
    result?: ActionResult;
  };
}

export interface ActionWorkflowPage {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ActionWorkflowPage";
  metadata: { tenantId: string };
  spec: {
    items: ActionWorkflow[];
    page: PageInfo;
  };
}

export interface PluginSession {
  apiVersion: "iip.platform/v1alpha1";
  kind: "PluginSession";
  metadata: {
    id: PluginSessionId;
    tenantId: string;
    pluginId: string;
    pluginVersion: string;
    createdAt: string;
  };
  spec: {
    manifestDigest: Sha256Digest;
    protocolVersion: "1.0";
    grantedCapabilities: string[];
    capabilityTokenRef: `capability://${string}`;
    capabilityTokenDigest: Sha256Digest;
    expiresAt: string;
    cancellation: { supported: true; endpoint: string };
    limits: {
      maxRequests: number;
      maxWallTimeSeconds: number;
      maxOutputBytes: number;
    };
  };
  status: "ready" | "denied";
}

export interface PolicyDecisionRequest {
  apiVersion: "iip.platform/v1alpha1";
  kind: "PolicyDecisionRequest";
  metadata: { tenantId: string; actorId: string };
  spec: {
    action: `${string}:${string}`;
    roles: string[];
    resource: { tenantId: string } & Record<string, unknown>;
  };
}

export interface PolicyDecision {
  apiVersion: "iip.platform/v1alpha1";
  kind: "PolicyDecision";
  metadata: { tenantId: string };
  spec: {
    inputDigest: Sha256Digest;
    allowed: boolean;
    reasonCode: string;
    policySnapshotRef: `policy://${string}/snapshots/${string}`;
  };
}

export interface ApiErrorBody {
  error: { code: string };
}
