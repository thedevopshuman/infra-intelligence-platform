export type ResourceHealth = "healthy" | "degraded" | "unhealthy" | "unknown";
export type ResourceUid = `res_${string}`;
export type EvidenceId = `evd_${string}`;
export type InvestigationId = `inv_${string}`;
export type InvestigationCancellationId = `can_${string}`;
export type ActionId = `act_${string}`;
export type ApprovalId = `apr_${string}`;
export type IntegrationId = `int_${string}`;
export type PluginSessionId = `psn_${string}`;
export type PluginInvocationId = `pin_${string}`;
export type PluginInvocationCancellationId = `pcn_${string}`;
export type PluginInvocationReconciliationId = `prc_${string}`;
export type PluginMediationGrantId = `pmg_${string}`;
export type PluginMediationRequestId = `pmr_${string}`;
export type PluginActionMediationGrantId = `pag_${string}`;
export type PluginActionMediationRequestId = `par_${string}`;
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

export interface OidcPkceConsoleProfile {
  issuer: string;
  clientId: string;
  authorizationEndpoint: string;
  tokenEndpoint: string;
  redirectUri: string;
  scopes: string[];
  providerLabel: string;
  pkceMethod: "S256";
}

export type ConsoleAuthenticationConfiguration = {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ConsoleAuthenticationConfiguration";
} & {
  spec:
    | { mode: "local-token" | "access-token"; oidc?: never }
    | { mode: "oidc-pkce"; oidc: OidcPkceConsoleProfile };
};

export interface RuntimeVersionReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "RuntimeVersionReport";
  metadata: {
    tenantId: string;
    evaluatedAt: string;
  };
  spec: {
    application: { version: string };
    contracts: { apiVersion: "iip.platform/v1alpha1" };
    storage: { requiredMigration: string };
    build:
      | { mode: "development"; revision?: never }
      | { mode: "release"; revision: string };
    deployment: {
      helmChartVersion?: string;
      imageDigest?: Sha256Digest;
    };
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

export type TelemetryExportSignalStatus =
  | "disabled"
  | "awaiting-first-attempt"
  | "healthy"
  | "degraded";

export interface TelemetryExportSignalHealth {
  signal: "metrics" | "traces";
  enabled: boolean;
  status: TelemetryExportSignalStatus;
  attempts: number;
  successes: number;
  failures: number;
  consecutiveFailures: number;
  lastAttemptAt?: string;
  lastSuccessAt?: string;
  lastFailureAt?: string;
  lastFailureCode?:
    | "telemetry.export.exception"
    | "telemetry.export.failed"
    | "telemetry.export.rejected";
}

export interface TelemetryExportHealthReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "TelemetryExportHealthReport";
  metadata: { evaluatedAt: string };
  spec: {
    status: TelemetryExportSignalStatus;
    signals: [TelemetryExportSignalHealth, TelemetryExportSignalHealth];
  };
}

export interface TelemetryDeploymentExportHealthInstance {
  instanceId: Sha256Digest;
  component: "api" | "workflow-worker";
  startedAt: string;
  lastReportedAt: string;
  freshness: "current" | "stale";
  status: TelemetryExportSignalStatus;
  signals: [TelemetryExportSignalHealth, TelemetryExportSignalHealth];
}

export interface TelemetryDeploymentExportHealthReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "TelemetryDeploymentExportHealthReport";
  metadata: { evaluatedAt: string };
  spec: {
    status: TelemetryExportSignalStatus;
    freshness: {
      staleAfterSeconds: number;
      retentionSeconds: number;
    };
    summary: {
      includedInstances: number;
      currentInstances: number;
      staleInstances: number;
      truncated: boolean;
    };
    instances: TelemetryDeploymentExportHealthInstance[];
  };
}

export type TelemetryExportSloStatus =
  | "disabled"
  | "no-data"
  | "insufficient-data"
  | "meeting"
  | "breached";

export interface TelemetryExportSloSignal {
  signal: "metrics" | "traces";
  status: TelemetryExportSloStatus;
  enabledObservations: number;
  eligibleAttempts: number;
  successfulAttempts: number;
  failedAttempts: number;
  attainmentBasisPoints: number | null;
}

export interface TelemetryExportSloReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "TelemetryExportSloReport";
  metadata: { evaluatedAt: string };
  spec: {
    status: TelemetryExportSloStatus;
    window: { durationSeconds: number; start: string; end: string };
    objective: {
      minimumAttainmentBasisPoints: number;
      minimumEligibleAttempts: number;
    };
    observation: { observedInstances: number; observedSamples: number };
    signals: [TelemetryExportSloSignal, TelemetryExportSloSignal];
  };
}

export type EventDeliveryHealthStatus = "healthy" | "backlogged" | "degraded";

export interface QuarantinedEventDelivery {
  outboxId: number;
  eventId: string;
  eventSource: string;
  eventType: `io.iip.${string}.v${number}`;
  subject: string;
  attempts: number;
  quarantinedAt: string;
  lastErrorCode: "event.publisher.unavailable";
}

export interface EventDeliveryHealthReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "EventDeliveryHealthReport";
  metadata: { tenantId: string; evaluatedAt: string };
  spec: {
    status: EventDeliveryHealthStatus;
    delivery: {
      pendingEvents: number;
      inFlightEvents: number;
      retryingEvents: number;
      quarantinedEvents: number;
      oldestPendingEventAgeSeconds?: number;
    };
    quarantine: {
      limit: number;
      hasMore: boolean;
      items: QuarantinedEventDelivery[];
    };
  };
}

export type EventDeliverySloStatus =
  | "no-data"
  | "insufficient-data"
  | "meeting"
  | "breached";

export interface EventDeliverySloReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "EventDeliverySloReport";
  metadata: { tenantId: string; evaluatedAt: string };
  spec: {
    status: EventDeliverySloStatus;
    window: {
      durationSeconds: number;
      start: string;
      end: string;
      maturityCutoff: string;
    };
    objective: {
      maximumDeliveryLatencySeconds: number;
      minimumAttainmentBasisPoints: number;
      minimumEligibleEvents: number;
    };
    measurement: {
      createdEvents: number;
      immatureEvents: number;
      eligibleEvents: number;
      withinObjectiveEvents: number;
      lateDeliveredEvents: number;
      undeliveredEvents: number;
      quarantinedEvents: number;
      attainmentBasisPoints: number | null;
    };
  };
}

export type InvestigationCompletionSloStatus =
  | "no-data"
  | "insufficient-data"
  | "meeting"
  | "breached";

export interface InvestigationCompletionSloReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "InvestigationCompletionSloReport";
  metadata: { tenantId: string; evaluatedAt: string };
  spec: {
    status: InvestigationCompletionSloStatus;
    window: {
      durationSeconds: number;
      start: string;
      end: string;
      maturityCutoff: string;
    };
    objective: {
      maximumCompletionSeconds: number;
      minimumAttainmentBasisPoints: number;
      minimumEligibleJobs: number;
    };
    measurement: {
      acceptedJobs: number;
      immatureJobs: number;
      eligibleJobs: number;
      withinObjectiveJobs: number;
      lateCompletedJobs: number;
      failedJobs: number;
      cancelledJobs: number;
      unfinishedJobs: number;
      attainmentBasisPoints: number | null;
    };
  };
}

export interface EvidenceRetentionReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "EvidenceRetentionReport";
  metadata: { tenantId: string; evaluatedAt: string };
  spec: {
    status: "disabled" | "current" | "cleanup-required";
    mode: "observe" | "expire";
    policy: {
      enabled: boolean;
      ephemeralSeconds: number;
      standardSeconds: number;
      extendedSeconds: number;
      batchSize: number;
      digest: Sha256Digest;
    };
    artifacts: {
      storedBefore: number;
      eligible: number;
      expired: number;
      remainingEligible: number;
      legalHold: number;
    };
    auditRef?: string;
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

export type InvestigationSignal =
  | "kubernetes.event"
  | "repository.context"
  | "resource.change"
  | "telemetry.metrics"
  | "telemetry.logs";

export interface InvestigationCatalogSnapshot {
  strategy: "protected-catalog-v1";
  profileId: string;
  profileVersion: string;
  profileDigest: Sha256Digest;
  generatedSelections: {
    signal: InvestigationSignal;
    selectionId: string;
  }[];
  snapshotDigest: Sha256Digest;
}

export interface InvestigationSignalCatalogProfile {
  tenantId: string;
  profileId: string;
  version: string;
  selections: {
    kubernetesEventSelections?: InvestigationKubernetesEventSelection[];
    contextSelections?: InvestigationContextSelection[];
    changeSelections?: InvestigationChangeSelection[];
    telemetrySelections?: InvestigationTelemetrySelection[];
    logSelections?: InvestigationLogSelection[];
  };
}

export interface InvestigationSignalCatalog {
  apiVersion: "iip.platform/v1alpha1";
  kind: "InvestigationSignalCatalog";
  profiles: InvestigationSignalCatalogProfile[];
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

export interface InvestigationJobStatus {
  apiVersion: "iip.platform/v1alpha1";
  kind: "InvestigationJobStatus";
  metadata: {
    id: InvestigationId;
    tenantId: string;
    updatedAt: string;
  };
  spec: {
    requestDigest: Sha256Digest;
    state:
      | "queued"
      | "running"
      | "cancellation-requested"
      | "completed"
      | "failed"
      | "cancelled";
    queuedAt: string;
    availableAt?: string;
    attempts: number;
    startedAt?: string;
    heartbeatAt?: string;
    leaseExpiresAt?: string;
    completedAt?: string;
    reportRef?: string;
    lastErrorCode?: string;
    cancellation?: {
      requestedBy: string;
      requestedAt: string;
      reasonCode: "operator-requested" | "incident-resolved" | "superseded";
    };
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
    readonly catalogSnapshot?: InvestigationCatalogSnapshot;
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
    signalPlan?: InvestigationSignalPlan;
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

export interface InvestigationSignalPromotion {
  position: 1;
  trigger: {
    signal: InvestigationSignal;
    selectionId: string;
    outcome: "provider-error" | "provider-unavailable";
  };
  candidate: {
    signal: InvestigationSignal;
    selectionId: string;
    initialReason: "budget-exhausted";
  };
  remainingCapacity: { toolCalls: number; evidenceItems: number };
}

export interface InvestigationSignalPlan {
  strategy: "risk-aware-v1" | "risk-aware-v2";
  rootCauseClass?: string;
  catalog?: {
    profileId: string;
    profileVersion: string;
    profileDigest: Sha256Digest;
    snapshotDigest: Sha256Digest;
  };
  capacity: { toolCalls: number; evidenceItems: number };
  replanning?: {
    maximumPromotions: 1;
    promotionCount: 1;
    promotions: InvestigationSignalPromotion[];
  };
  candidateCount: number;
  scheduledCount: number;
  deferredCount: number;
  steps: {
    position: number;
    signal: InvestigationSignal;
    selectionId: string;
    origin: "request" | "protected-catalog";
    decision: "scheduled" | "deferred";
    reason:
      | "eligible"
      | "root-cause-mismatch"
      | "request-upper-bound"
      | "budget-exhausted";
  }[];
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

export interface KubernetesRestartActionParameters {
  namespace: string;
  workloadKind: "deployment" | "statefulset" | "daemonset";
  workloadName: string;
}

export interface EventDeliveryReplayParameters {
  outboxId: number;
  eventId: string;
  quarantinedAt: string;
  attempts: number;
}

interface ActionProposalCommonSpec {
  investigationId: InvestigationId;
  investigationDigest?: Sha256Digest;
  targetResourceUid: ResourceUid;
  targetDigest?: Sha256Digest;
  risk: "low" | "medium" | "high" | "critical";
  dryRun: boolean;
  idempotencyKey: string;
  expiresAt: string;
  policyDecision: {
    allowed: boolean;
    reasonCode: string;
    policySnapshotRef: string;
    inputDigest?: Sha256Digest;
  };
}

export type ActionProposalSpec = ActionProposalCommonSpec & (
  | {
      actionType: "kubernetes.restart-workload";
      integrationId?: string;
      providerObjectUid?: string;
      parameters: KubernetesRestartActionParameters;
      reversible: true;
    }
  | {
      actionType: "event-delivery.requeue";
      integrationId?: never;
      providerObjectUid?: never;
      parameters: EventDeliveryReplayParameters;
      reversible: false;
    }
);

export interface ActionProposal {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ActionProposal";
  metadata: { id: ActionId; tenantId: string; actorId: string; createdAt: string };
  spec: ActionProposalSpec;
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
  | "expired"
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

export type PluginCapability =
  | "resource-observer"
  | "event-source"
  | "evidence-provider"
  | "action-provider"
  | "surface";

export interface PluginManifest {
  apiVersion: "iip.platform/v1alpha1";
  kind: "Plugin";
  metadata: {
    id: string;
    version: string;
    displayName: string;
    publisher?: string;
    description?: string;
  };
  spec: {
    protocolVersion: "1.0";
    entrypoint: {
      transport: "stdio" | "http" | "grpc" | "wasm";
      target: string;
      healthPath?: string;
    };
    artifact?: {
      type: "oci-image";
      reference: string;
      digest: Sha256Digest;
      signature:
        | {
            algorithm: "ed25519";
            keyId: string;
            value: string;
            profile?: never;
            manifestDigest?: never;
          }
        | {
            profile: "iip.plugin-signature/v2";
            algorithm: "ed25519";
            keyId: string;
            manifestDigest: Sha256Digest;
            value: string;
          };
    };
    capabilities: PluginCapability[];
    interfaces?: Array<{
      capability: PluginCapability;
      method: string;
      inputSchema: string;
      outputSchema: string;
    }>;
    permissions: {
      network: string[];
      secrets: string[];
      resources: string[];
      actions: string[];
    };
    configSchema?: Record<string, unknown>;
  };
}

export type PluginCompatibilityCheckId =
  | "manifest-schema"
  | "publisher-signature"
  | "immutable-plugin-artifact"
  | "immutable-mediation-bridge"
  | "no-network-sandbox"
  | "bounded-sandbox"
  | "input-contract"
  | "output-contract"
  | "golden-result"
  | "invocation-local-socket"
  | "host-mediated-read"
  | "credentials-host-only"
  | "host-mediated-action-proposal"
  | "governed-proposal-queue"
  | "approval-not-granted"
  | "execution-not-granted";

export interface PluginCompatibilityReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "PluginCompatibilityReport";
  metadata: {
    id: `pcr_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: boolean;
  };
  spec: {
    host: {
      platform: `linux/${string}`;
      containerRuntime: "docker";
      containerRuntimeVersion: string;
      applicationVersion: string;
      sdk: { language: "python"; version: string };
    };
    plugin: {
      id: string;
      version: string;
      protocolVersion: "1.0";
      capability: PluginCapability;
      method: string;
      artifactDigest: Sha256Digest;
      mediationBridgeDigest: Sha256Digest;
    };
    profiles: Array<{
      name:
        | "offline-fixture"
        | "host-mediated-read"
        | "host-mediated-action-proposal";
      capability?: PluginCapability;
      method?: string;
      manifestDigest: Sha256Digest;
      result: "compatible" | "incompatible";
      checks: Array<
        | {
            id: PluginCompatibilityCheckId;
            status: "passed";
            errorCode?: never;
          }
        | {
            id: PluginCompatibilityCheckId;
            status: "failed";
            errorCode: string;
          }
      >;
    }>;
    summary: {
      totalProfiles: number;
      compatibleProfiles: number;
      incompatibleProfiles: number;
      overallStatus: "compatible" | "incompatible";
    };
  };
}

export interface PluginInvocation {
  apiVersion: "iip.platform/v1alpha1";
  kind: "PluginInvocation";
  metadata: {
    id: PluginInvocationId;
    sessionId: PluginSessionId;
    tenantId: string;
    actorId: string;
    createdAt: string;
    deadline: string;
  };
  spec: {
    manifestDigest: Sha256Digest;
    capability: string;
    method: string;
    input: Record<string, unknown>;
    mediationGrants?: PluginMediationGrant[];
    actionMediationGrants?: PluginActionMediationGrant[];
  };
}

export interface PluginMediationGrant {
  apiVersion: "iip.platform/v1alpha1";
  kind: "PluginMediationGrant";
  metadata: {
    id: PluginMediationGrantId;
    invocationId: PluginInvocationId;
    tenantId: string;
    actorId: string;
    issuedAt: string;
    expiresAt: string;
  };
  spec: {
    integrationId: string;
    provider: string;
    destination: string;
    credentialName: string;
    operation: "http-json-read";
    pathTemplates: string[];
    queryKeys: string[];
    scopes: string[];
    limits: { maxRequests: number; maxResponseBytes: number };
  };
}

export interface PluginMediationRequest {
  apiVersion: "iip.plugin-runtime/v1alpha1";
  kind: "PluginMediationRequest";
  metadata: {
    id: PluginMediationRequestId;
    invocationId: PluginInvocationId;
    grantId: PluginMediationGrantId;
  };
  spec: {
    method: "GET";
    path: string;
    query: Record<string, string | string[]>;
  };
}

export interface PluginMediationResponseBase {
  apiVersion: "iip.plugin-runtime/v1alpha1";
  kind: "PluginMediationResponse";
  metadata: {
    requestId: PluginMediationRequestId;
    invocationId: PluginInvocationId;
    completedAt: string;
  };
}

export type PluginMediationResponse = PluginMediationResponseBase & {
  spec:
    | {
        status: "succeeded";
        mediaType: "application/json";
        body: Record<string, unknown> | unknown[];
        bodyDigest: Sha256Digest;
        bodyBytes: number;
        error?: never;
      }
    | {
        status: "failed";
        error: {
          code:
            | "plugin.mediation.audit-unavailable"
            | "plugin.mediation.credential-unavailable"
            | "plugin.mediation.deadline-exceeded"
            | "plugin.mediation.denied"
            | "plugin.mediation.limit-exceeded"
            | "plugin.mediation.provider-unavailable"
            | "plugin.mediation.request-invalid"
            | "plugin.mediation.response-invalid"
            | "plugin.mediation.response-too-large";
        };
        mediaType?: never;
        body?: never;
        bodyDigest?: never;
        bodyBytes?: never;
      };
};

export interface PluginActionMediationGrant {
  apiVersion: "iip.platform/v1alpha1";
  kind: "PluginActionMediationGrant";
  metadata: {
    id: PluginActionMediationGrantId;
    invocationId: PluginInvocationId;
    tenantId: string;
    actorId: string;
    issuedAt: string;
    expiresAt: string;
  };
  spec: {
    operation: "governed-action-proposal";
    actionTypes: ["kubernetes.restart-workload"];
    targetResourceUids: ResourceUid[];
    dryRunPolicy: "required" | "allowed";
    limits: { maxRequests: number; maxProposalLifetimeSeconds: number };
  };
}

export interface PluginActionMediationRequest {
  apiVersion: "iip.plugin-runtime/v1alpha1";
  kind: "PluginActionMediationRequest";
  metadata: {
    id: PluginActionMediationRequestId;
    invocationId: PluginInvocationId;
    grantId: PluginActionMediationGrantId;
  };
  spec: {
    investigationId: InvestigationId;
    actionType: "kubernetes.restart-workload";
    targetResourceUid: ResourceUid;
    parameters: KubernetesRestartActionParameters;
    dryRun: boolean;
  };
}

export interface PluginActionMediationResponseBase {
  apiVersion: "iip.plugin-runtime/v1alpha1";
  kind: "PluginActionMediationResponse";
  metadata: {
    requestId: PluginActionMediationRequestId;
    invocationId: PluginInvocationId;
    completedAt: string;
  };
}

export type PluginActionMediationResponse =
  PluginActionMediationResponseBase & {
    spec:
      | {
          status: "proposed";
          proposalId: ActionId;
          proposalDigest: Sha256Digest;
          expiresAt: string;
          dryRun: boolean;
          error?: never;
        }
      | {
          status: "failed";
          error: {
            code:
              | "plugin.action.audit-unavailable"
              | "plugin.action.deadline-exceeded"
              | "plugin.action.denied"
              | "plugin.action.limit-exceeded"
              | "plugin.action.proposal-rejected"
              | "plugin.action.request-invalid";
          };
          proposalId?: never;
          proposalDigest?: never;
          expiresAt?: never;
          dryRun?: never;
        };
  };

export interface PluginInvocationResult {
  apiVersion: "iip.platform/v1alpha1";
  kind: "PluginInvocationResult";
  metadata: {
    id: PluginInvocationId;
    sessionId: PluginSessionId;
    tenantId: string;
    pluginId: string;
    pluginVersion: string;
    completedAt: string;
  };
  spec: {
    usage: { wallTimeMillis: number; outputBytes: number };
  } & (
    | {
        status: "succeeded";
        outputDigest: Sha256Digest;
        output: Record<string, unknown>;
        error?: never;
      }
    | {
        status: "failed" | "cancelled";
        error: { code: string };
        outputDigest?: never;
        output?: never;
      }
  );
}

export interface PluginInvocationCancellation {
  requestedBy: string;
  requestedAt: string;
  reasonCode: "operator-requested" | "session-superseded" | "shutdown";
}

export interface PluginInvocationStatus {
  apiVersion: "iip.platform/v1alpha1";
  kind: "PluginInvocationStatus";
  metadata: {
    id: PluginInvocationId;
    sessionId: PluginSessionId;
    tenantId: string;
    pluginId: string;
    pluginVersion: string;
    updatedAt: string;
  };
  spec: {
    requestDigest: Sha256Digest;
    claimedAt: string;
    deadline: string;
  } & (
    | {
        state: "claimed";
        cancellation?: never;
        completedAt?: never;
        resultRef?: never;
      }
    | {
        state: "cancellation-requested";
        cancellation: PluginInvocationCancellation;
        completedAt?: never;
        resultRef?: never;
      }
    | {
        state: "succeeded" | "failed";
        cancellation?: never;
        completedAt: string;
        resultRef: `plugin-result://${string}`;
      }
    | {
        state: "cancelled";
        cancellation: PluginInvocationCancellation;
        completedAt: string;
        resultRef: `plugin-result://${string}`;
      }
  );
}

export interface PluginInvocationCancellationRequest {
  apiVersion: "iip.platform/v1alpha1";
  kind: "PluginInvocationCancellationRequest";
  metadata: {
    id: PluginInvocationCancellationId;
    tenantId: string;
    actorId: string;
    requestedAt: string;
  };
  spec: {
    invocationId: PluginInvocationId;
    reasonCode: PluginInvocationCancellation["reasonCode"];
  };
}

export interface PluginInvocationReconciliationRequest {
  apiVersion: "iip.platform/v1alpha1";
  kind: "PluginInvocationReconciliationRequest";
  metadata: {
    id: PluginInvocationReconciliationId;
    tenantId: string;
    actorId: string;
    requestedAt: string;
  };
  spec: {
    invocationId: PluginInvocationId;
    reasonCode: "runner-lost" | "deadline-elapsed";
  };
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
