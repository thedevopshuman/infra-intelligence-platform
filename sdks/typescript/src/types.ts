export type ResourceHealth = "healthy" | "degraded" | "unhealthy" | "unknown";
export type ResourceUid = `res_${string}`;
export type EvidenceId = `evd_${string}`;
export type InvestigationId = `inv_${string}`;
export type Sha256Digest = `sha256:${string}`;
export type ResourceLifecycle =
  | "active"
  | "creating"
  | "updating"
  | "deleting"
  | "deleted"
  | "unknown";

export interface ResourceRelationship {
  type: string;
  target: string;
  direction?: "outbound" | "inbound";
  attributes?: Record<string, unknown>;
}

export interface ResourceObservation {
  apiVersion: "iip.platform/v1alpha1";
  kind: "Resource";
  metadata: {
    uid?: ResourceUid;
    tenantId: string;
    observedAt: string;
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
    budgets: InvestigationBudgets;
    maxAuthority: "read" | "propose";
    priority?: "low" | "normal" | "high" | "critical";
  };
}

export interface InvestigationHypothesis {
  id: `hyp_${string}`;
  rank: number;
  statement: string;
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

export interface ApiErrorBody {
  error: { code: string };
}
