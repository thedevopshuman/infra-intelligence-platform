export type ResourceHealth = "healthy" | "degraded" | "unhealthy" | "unknown";
export type ResourceUid = `res_${string}`;
export type EvidenceId = `evd_${string}`;
export type EvidenceRedactionPolicyId = `erp_${string}`;
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
export type AiUsageRecordId = `aiu_${string}`;
export type AiAttributionPolicyId = `aap_${string}`;
export type AiUsageAttributionRecordId = `aia_${string}`;
export type AiPriceCatalogId = `apc_${string}`;
export type AwsBedrockPriceCatalogImportPolicyId = `abp_${string}`;
export type AiPriceCatalogImportReportId = `apir_${string}`;
export type AiPriceCatalogQualificationPolicyId = `apqp_${string}`;
export type AiPriceCatalogQualificationReportId = `apq_${string}`;
export type AiCostRecordId = `aic_${string}`;
export type AiSavingsFindingId = `aif_${string}`;
export type AiModelSuitabilityReportId = `ams_${string}`;
export type Sha256Digest = `sha256:${string}`;
export type ResourceLifecycle =
  | "active"
  | "creating"
  | "updating"
  | "deleting"
  | "deleted"
  | "unknown";

export type AiServiceTier =
  | "default"
  | "standard"
  | "flex"
  | "priority"
  | "reserved"
  | "unknown";
export type AiRoutingMode =
  | "in-region"
  | "geographic"
  | "global"
  | "unknown";
export type AiPurchaseMode =
  | "on-demand"
  | "batch"
  | "provisioned-throughput"
  | "unknown";
export type AiCurrencyScale = 6 | 9 | 12;

export interface AiUsageRecord {
  apiVersion: "iip.platform/v1alpha1";
  kind: "AiUsageRecord";
  metadata: {
    id: AiUsageRecordId;
    tenantId: string;
    recordedAt: string;
  };
  spec: {
    source: {
      integrationId: string;
      channelId: string;
      transport: "otlp";
      signal: "traces";
      semanticConventionVersion: string;
      instrumentation: { scopeName: string; scopeVersion?: string };
    };
    invocation: {
      provider: string;
      operationName: string;
      requestModel: string;
      responseModel?: string;
      region: string;
      serviceTier: AiServiceTier;
      routingMode: AiRoutingMode;
      purchaseMode: AiPurchaseMode;
      startedAt: string;
      durationMillis: number;
      outcome: "success" | "error" | "cancelled";
      errorType?: string;
      traceId: string;
      spanId: string;
      requestIdHash?: Sha256Digest;
      retryCount?: number;
    };
    attribution: {
      serviceName: string;
      serviceNamespace?: string;
      deploymentEnvironment?: string;
      resourceRefs: ResourceUid[];
    };
    usage: {
      inputTokens?: number;
      outputTokens?: number;
      cacheReadInputTokens?: number;
      cacheWriteInputTokens?: number;
      reasoningOutputTokens?: number;
      reportedBy: "provider" | "instrumentation" | "derived";
      completeness: "complete" | "partial";
      missingFields: (
        | "inputTokens"
        | "outputTokens"
        | "cacheReadInputTokens"
        | "cacheWriteInputTokens"
        | "reasoningOutputTokens"
      )[];
    };
    privacy: {
      contentPolicy: "metadata-only";
      contentCaptured: false;
      rawPayloadPersisted: false;
      droppedAttributeCount: number;
    };
    deduplicationKey: Sha256Digest;
  };
}

export interface AiOrganizationalUnit {
  id: string;
  name: string;
}

export interface AiAttributionPolicyRule {
  id: string;
  priority: number;
  match: {
    serviceName: string;
    serviceNamespace?: string;
    deploymentEnvironment?: string;
    resourceRef?: ResourceUid;
  };
  allocation: {
    application: AiOrganizationalUnit;
    team: AiOrganizationalUnit;
  };
  effectiveFrom: string;
  effectiveUntil?: string;
}

export interface AiAttributionPolicy {
  apiVersion: "iip.platform/v1alpha1";
  kind: "AiAttributionPolicy";
  metadata: {
    id: AiAttributionPolicyId;
    tenantId: string;
    version: string;
    publishedAt: string;
  };
  spec: {
    source: {
      kind: "operator-managed" | "test-fixture";
      locator: string;
      retrievedAt: string;
      contentHash: Sha256Digest;
    };
    rules: AiAttributionPolicyRule[];
  };
}

export type AiUsageAttributionResolution =
  | {
      status: "allocated";
      ruleId: string;
      application: AiOrganizationalUnit;
      team: AiOrganizationalUnit;
    }
  | {
      status: "unallocated";
      reasonCode: "no-matching-rule";
    };

export interface AiUsageAttributionRecord {
  apiVersion: "iip.platform/v1alpha1";
  kind: "AiUsageAttributionRecord";
  metadata: {
    id: AiUsageAttributionRecordId;
    tenantId: string;
    resolvedAt: string;
  };
  spec: {
    usageRecordId: AiUsageRecordId;
    effectiveAt: string;
    engineVersion: string;
    policy: {
      id: AiAttributionPolicyId;
      version: string;
      sourceHash: Sha256Digest;
    };
    observedIdentity: {
      serviceName: string;
      serviceNamespace?: string;
      deploymentEnvironment?: string;
      resourceRefs: ResourceUid[];
    };
    resolution: AiUsageAttributionResolution;
  };
}

export interface AiAllocationMoney {
  currency: string;
  currencyScale: AiCurrencyScale;
  totalSubunits: number;
  costBasis: "calculated-estimate";
}

export interface AiAllocationCoverage {
  usageRecords: number;
  allocatedRecords: number;
  unallocatedRecords: number;
  pendingAttributionRecords: number;
  pricedRecords: number;
  unpricedRecords: number;
  ambiguousRecords: number;
  pendingCostRecords: number;
}

export interface AiAllocationTotals {
  inputTokens: number;
  inputTokenRecords: number;
  outputTokens: number;
  outputTokenRecords: number;
  pricedCost?: AiAllocationMoney;
}

export type AiAllocationGroup = AiAllocationTotals & {
  usageRecords: number;
  pricedRecords: number;
  unpricedRecords: number;
  ambiguousRecords: number;
  pendingCostRecords: number;
} & (
    | {
        allocationStatus: "allocated";
        dimension: AiOrganizationalUnit;
      }
    | {
        allocationStatus: "unallocated";
        reasonCode: "no-matching-rule";
      }
    | {
        allocationStatus: "pending";
        reasonCode: "not-yet-attributed";
      }
  );

export interface AiAllocationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "AiAllocationReport";
  metadata: { tenantId: string; generatedAt: string };
  spec: {
    scope: {
      start: string;
      end: string;
      groupBy: "application" | "team";
      sourceRecordLimit: number;
    };
    sources: {
      attribution: {
        id: AiAttributionPolicyId;
        version: string;
        sourceHash: Sha256Digest;
        engineVersion: string;
      };
      pricing: {
        id: AiPriceCatalogId;
        version: string;
        sourceHash: Sha256Digest;
        engineVersion: string;
        currency: string;
        currencyScale: AiCurrencyScale;
        costBasis: "calculated-estimate";
      };
    };
    coverage: AiAllocationCoverage;
    totals: AiAllocationTotals;
    groups: AiAllocationGroup[];
  };
}

export interface AiEconomicsInvocationObservationRequest {
  apiVersion: "iip.platform/v1alpha1";
  kind: "AiEconomicsInvocationObservationRequest";
  spec: { traceId: string; spanId: string };
}

export interface AiEconomicsInvocationObservation {
  apiVersion: "iip.platform/v1alpha1";
  kind: "AiEconomicsInvocationObservation";
  metadata: { tenantId: string; generatedAt: string };
  spec: {
    status: "not-observed" | "processing" | "complete";
    correlationDigest: Sha256Digest;
    sources: {
      attribution: {
        id: AiAttributionPolicyId;
        version: string;
        sourceHash: Sha256Digest;
        documentDigest: Sha256Digest;
        engineVersion: string;
      };
      pricing: {
        id: AiPriceCatalogId;
        version: string;
        sourceHash: Sha256Digest;
        documentDigest: Sha256Digest;
        engineVersion: string;
        currency: string;
        currencyScale: AiCurrencyScale;
        costBasis: "calculated-estimate";
      };
    };
    usage:
      | { status: "not-observed" }
      | {
          status: "recorded";
          recordId: AiUsageRecordId;
          recordDigest: Sha256Digest;
          startedAt: string;
        };
    attribution:
      | { status: "pending" }
      | {
          status: "allocated";
          recordId: AiUsageAttributionRecordId;
          recordDigest: Sha256Digest;
          applicationId: string;
          teamId: string;
        }
      | {
          status: "unallocated";
          recordId: AiUsageAttributionRecordId;
          recordDigest: Sha256Digest;
          reasonCode: "no-matching-rule";
        };
    cost:
      | { status: "pending" }
      | {
          status: "priced";
          recordId: AiCostRecordId;
          recordDigest: Sha256Digest;
          pricedCost: AiAllocationMoney;
        }
      | {
          status: "unpriced" | "ambiguous";
          recordId: AiCostRecordId;
          recordDigest: Sha256Digest;
          reasonCode: string;
        };
  };
}

export interface AiTokenPrice {
  priceSubunitsPerMillionTokens: number;
}

export interface AiPriceCatalogEntry {
  id: string;
  provider: string;
  modelId: string;
  regions: string[];
  serviceTiers: AiServiceTier[];
  routingModes: AiRoutingMode[];
  purchaseModes: AiPurchaseMode[];
  effectiveFrom: string;
  effectiveUntil?: string;
  rates: {
    uncachedInputTokens: AiTokenPrice;
    cacheReadInputTokens: AiTokenPrice;
    cacheWriteInputTokens: AiTokenPrice;
    nonReasoningOutputTokens: AiTokenPrice;
    reasoningOutputTokens: AiTokenPrice;
  };
}

export interface AiPriceCatalog {
  apiVersion: "iip.platform/v1alpha1";
  kind: "AiPriceCatalog";
  metadata: {
    id: AiPriceCatalogId;
    tenantId: string;
    version: string;
    publishedAt: string;
  };
  spec: {
    currency: string;
    currencyScale: AiCurrencyScale;
    source: {
      kind: "provider-published" | "operator-managed" | "test-fixture";
      locator: string;
      retrievedAt: string;
      contentHash: Sha256Digest;
    };
    entries: AiPriceCatalogEntry[];
  };
}

export interface AwsPriceRateReference {
  sku: string;
  offerTermCode: string;
  rateCode: string;
  expectedUnit: "1K tokens" | "1M tokens";
  expectedAttributes: {
    feature: string;
    inferenceType: string;
    model: string;
    provider: string;
    regionCode: string;
    usagetype: string;
  };
}

export interface AwsBedrockPriceCatalogImportPolicy {
  apiVersion: "iip.platform/v1alpha1";
  kind: "AwsBedrockPriceCatalogImportPolicy";
  metadata: {
    id: AwsBedrockPriceCatalogImportPolicyId;
    tenantId: string;
    version: string;
  };
  spec: {
    source: {
      profile: "aws-price-list-v1";
      serviceCode: "AmazonBedrock";
      locator: string;
      maximumBytes: number;
    };
    catalog: {
      version: string;
      currency: "USD";
      currencyScale: AiCurrencyScale;
    };
    entries: Array<{
      id: string;
      modelId: string;
      region: string;
      serviceTier: AiServiceTier;
      routingMode: AiRoutingMode;
      purchaseMode: AiPurchaseMode;
      effectiveFrom: string;
      effectiveUntil?: string;
      rates: {
        uncachedInputTokens: AwsPriceRateReference;
        cacheReadInputTokens: AwsPriceRateReference;
        cacheWriteInputTokens: AwsPriceRateReference;
        nonReasoningOutputTokens: AwsPriceRateReference;
        reasoningOutputTokens: AwsPriceRateReference;
      };
    }>;
  };
}

export type AiPriceCatalogImportCheckId =
  | "source-envelope"
  | "policy-identity"
  | "exact-rate-references"
  | "decimal-conversion"
  | "catalog-contract";

export interface AiPriceCatalogImportReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "AiPriceCatalogImportReport";
  metadata: {
    id: AiPriceCatalogImportReportId;
    tenantId: string;
    generatedAt: string;
  };
  spec: {
    status: "imported";
    sourceProfile: "aws-price-list-v1";
    importerVersion: "0.1.0";
    source: {
      serviceCode: "AmazonBedrock";
      version: string;
      contentHash: Sha256Digest;
      publicationDate: string;
      retrievedAt: string;
      sizeBytes: number;
    };
    policy: {
      id: AwsBedrockPriceCatalogImportPolicyId;
      version: string;
      documentDigest: Sha256Digest;
    };
    catalog: {
      id: AiPriceCatalogId;
      version: string;
      documentDigest: Sha256Digest;
      currency: "USD";
      currencyScale: AiCurrencyScale;
      entryCount: number;
    };
    measurements: {
      rateReferenceCount: number;
      uniquePriceDimensionCount: number;
    };
    checks: Array<{
      id: AiPriceCatalogImportCheckId;
      status: "passed";
    }>;
    summary: {
      totalChecks: 5;
      passedChecks: 5;
      failedChecks: 0;
      overallStatus: "imported";
    };
  };
}

export interface AiPriceCatalogQualificationScope {
  provider: string;
  modelId: string;
  region: string;
  serviceTier: AiServiceTier;
  routingMode: AiRoutingMode;
  purchaseMode: AiPurchaseMode;
  effectiveAt: string;
}

export interface AiPriceCatalogQualificationPolicy {
  apiVersion: "iip.platform/v1alpha1";
  kind: "AiPriceCatalogQualificationPolicy";
  metadata: {
    id: AiPriceCatalogQualificationPolicyId;
    tenantId: string;
    version: string;
  };
  spec: {
    maximumSourceAgeSeconds: number;
    reportValiditySeconds: number;
    maximumFutureSkewSeconds: number;
    requiredScopes: AiPriceCatalogQualificationScope[];
  };
}

export type AiPriceCatalogQualificationCheckId =
  | "source-profile"
  | "source-freshness"
  | "publication-order"
  | "non-overlapping-entries"
  | "required-scope-coverage";

export interface AiPriceCatalogQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "AiPriceCatalogQualificationReport";
  metadata: {
    id: AiPriceCatalogQualificationReportId;
    tenantId: string;
    generatedAt: string;
    validUntil: string;
  };
  spec: {
    status: "qualified" | "unqualified";
    qualificationLevel: "offline-static" | "production-catalog";
    qualifierVersion: "0.1.0";
    catalog: {
      id: AiPriceCatalogId;
      version: string;
      documentDigest: Sha256Digest;
      sourceKind: "provider-published" | "operator-managed" | "test-fixture";
      sourceHash: Sha256Digest;
      publishedAt: string;
      retrievedAt: string;
      currency: string;
      currencyScale: AiCurrencyScale;
    };
    policy: {
      id: AiPriceCatalogQualificationPolicyId;
      version: string;
      documentDigest: Sha256Digest;
      maximumSourceAgeSeconds: number;
      reportValiditySeconds: number;
      maximumFutureSkewSeconds: number;
    };
    measurements: {
      entryCount: number;
      requiredScopeCount: number;
      coveredScopeCount: number;
      missingScopeCount: number;
      ambiguousScopeCount: number;
      overlappingEntryPairCount: number;
    };
    checks: Array<{
      id: AiPriceCatalogQualificationCheckId;
      status: "passed" | "failed";
      errorCode?: string;
    }>;
    summary: {
      totalChecks: 5;
      passedChecks: number;
      failedChecks: number;
      overallStatus: "qualified" | "unqualified";
    };
  };
}

export type AiChargeCategory =
  | "uncached-input-tokens"
  | "cache-read-input-tokens"
  | "cache-write-input-tokens"
  | "non-reasoning-output-tokens"
  | "reasoning-output-tokens"
  | "aggregate-output-tokens";

export interface AiCostLine {
  chargeCategory: AiChargeCategory;
  observedQuantity: number;
  billableQuantity: number;
  catalogEntryId: string;
  priceSubunitsPerMillionTokens: number;
  amountSubunits: number;
}

export type AiCostResult =
  | {
      costStatus: "priced";
      coverage: "complete";
      currency: string;
      currencyScale: AiCurrencyScale;
      totalSubunits: number;
      lines: AiCostLine[];
      warnings: string[];
    }
  | {
      costStatus: "unpriced" | "ambiguous";
      coverage: "none" | "partial";
      reasonCode:
        | "no-catalog-match"
        | "multiple-catalog-matches"
        | "missing-usage"
        | "unsupported-meter"
        | "invalid-breakdown";
      warnings: string[];
    };

export interface AiCostRecord {
  apiVersion: "iip.platform/v1alpha1";
  kind: "AiCostRecord";
  metadata: {
    id: AiCostRecordId;
    tenantId: string;
    calculatedAt: string;
  };
  spec: {
    usageRecordId: AiUsageRecordId;
    calculation: {
      engineVersion: string;
      costBasis: "calculated-estimate";
      catalogId: AiPriceCatalogId;
      catalogVersion: string;
      catalogSourceHash: Sha256Digest;
    };
    result: AiCostResult;
  };
}

export type AiFinopsRuntimeCompatibilityCheckId =
  | "compose-configuration"
  | "all-components-healthy"
  | "collector-delivery"
  | "normalized-ledger-persistence"
  | "metadata-only-content-rejection"
  | "replay-idempotency"
  | "data-driven-cost"
  | "aggregate-output-rate-equivalence"
  | "unpriced-coverage"
  | "protected-attribution"
  | "deterministic-findings"
  | "otlp-aggregate-export"
  | "grafana-dashboard"
  | "replacement-boundaries";

export interface AiFinopsRuntimeCompatibilityReport {
  apiVersion: "iip.dev/v1alpha1";
  kind: "AiFinopsRuntimeCompatibilityReport";
  metadata: {
    id: `afc_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: boolean;
  };
  spec: {
    status: "compatible" | "incompatible";
    qualificationLevel: "local-multi-provider-ai-finops-v1";
    environment: {
      platform: string;
      containerRuntime: "docker";
      containerRuntimeVersion: string;
      applicationVersion: string;
      costEngineVersion: "0.2.0";
    };
    profile: {
      providers: ["aws.bedrock", "openai"];
      collectionPath: "otel-collector-to-isolated-otlp-trace-receiver";
      storage: "postgresql";
      telemetryBackend: "prometheus";
      dashboard: "grafana";
      pricingSource: "test-fixture";
      contentPolicy: "metadata-only";
    };
    measurements: {
      usageRecordCount: 15;
      pricedRecordCount: 11;
      aggregateOutputPricedRecordCount: 1;
      unpricedRecordCount: 4;
      allocatedRecordCount: 10;
      unallocatedRecordCount: 5;
      findingCount: 3;
      rejectedContentSpanCount: 2;
      providerCount: 2;
    };
    checks: Array<
      | { id: AiFinopsRuntimeCompatibilityCheckId; status: "passed" }
      | {
          id: AiFinopsRuntimeCompatibilityCheckId;
          status: "failed";
          errorCode: string;
        }
    >;
    limitations: [
      "synthetic-provider-spans",
      "test-fixture-pricing",
      "single-host-docker-runtime",
      "customer-collector-pki-and-network-not-qualified",
      "live-provider-private-prices-and-invoice-not-qualified",
      "sustained-load-regional-ha-and-backend-lifecycle-not-qualified",
    ];
    summary: {
      totalChecks: 14;
      passedChecks: number;
      failedChecks: number;
      overallStatus: "compatible" | "incompatible";
    };
  };
}

export interface AiFinopsSustainedLoadWorkload {
  durationSeconds: number;
  spansPerSecond: number;
  producerConcurrency: number;
  replayBasisPoints: number;
  requestTimeoutMilliseconds: number;
  maximumSchedulerLagMilliseconds: number;
  maximumPipelineDrainMilliseconds: number;
}

export interface AiFinopsSustainedLoadObjectives {
  maximumSchedulerMissBasisPoints: number;
  minimumCollectorAcceptanceBasisPoints: number;
  minimumUsagePersistenceBasisPoints: number;
  minimumAttributionCompletionBasisPoints: number;
  minimumCostCompletionBasisPoints: number;
  minimumPricedCoverageBasisPoints: number;
  maximumExportP95Milliseconds: number;
  maximumAttributionP95Milliseconds: number;
  maximumCostP95Milliseconds: number;
}

export interface AiFinopsSustainedLoadProfile {
  apiVersion: "iip.dev/v1alpha1";
  kind: "AiFinopsSustainedLoadProfile";
  metadata: {
    id: `afslp_${string}`;
  };
  spec: {
    qualificationLevel: "local-ai-finops-sustained-load-v1";
    providers: ["aws.bedrock", "openai"];
    distribution: "round-robin-equal";
    workload: AiFinopsSustainedLoadWorkload;
    objectives: AiFinopsSustainedLoadObjectives;
  };
}

export interface AiFinopsSustainedLoadLatency {
  p50Milliseconds: number | null;
  p95Milliseconds: number | null;
  p99Milliseconds: number | null;
  maximumMilliseconds: number | null;
}

export interface AiFinopsSustainedLoadProviderMeasurements {
  provider: "aws.bedrock" | "openai";
  scheduledSpans: number;
  usageRecords: number;
  attributionRecords: number;
  costRecords: number;
  pricedCostRecords: number;
}

export type AiFinopsSustainedLoadCheckId =
  | "source-binding"
  | "profile-binding"
  | "compose-configuration"
  | "all-components-healthy"
  | "bounded-load-volume"
  | "fixed-rate-scheduler"
  | "collector-acceptance"
  | "usage-ledger-persistence"
  | "replay-idempotency"
  | "attribution-completion"
  | "cost-completion"
  | "priced-coverage"
  | "export-p95-latency"
  | "attribution-p95-latency"
  | "cost-p95-latency"
  | "pipeline-drain"
  | "prometheus-convergence"
  | "grafana-dashboard"
  | "metadata-only-boundaries";

export type AiFinopsSustainedLoadCheck<
  TId extends AiFinopsSustainedLoadCheckId,
> =
  | { id: TId; status: "passed" }
  | {
      id: TId;
      status: "failed";
      errorCode: `ai-finops-sustained-load.${string}`;
    };

export interface AiFinopsSustainedLoadQualificationReport {
  apiVersion: "iip.dev/v1alpha1";
  kind: "AiFinopsSustainedLoadQualificationReport";
  metadata: {
    id: `afslq_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: boolean;
  };
  spec: {
    status: "qualified" | "not-qualified";
    qualificationLevel: "local-ai-finops-sustained-load-v1";
    qualificationBoundary: "single-host-docker-synthetic-ai-economics-load";
    subject: {
      applicationVersion: string;
      attributionEngineVersion: "0.1.0";
      costEngineVersion: "0.2.0";
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    bindings: {
      profileId: `afslp_${string}`;
      profileDigest: Sha256Digest;
      attributionPolicyId: "aap_22222222222222222222222222222222";
      attributionPolicyVersion: "2026-09-05.1";
      priceCatalogId: "apc_11111111111111111111111111111111";
      priceCatalogVersion: "2026-09-05.1";
    };
    workload: AiFinopsSustainedLoadWorkload;
    objectives: AiFinopsSustainedLoadObjectives;
    environment: {
      platform: `${string}/${string}`;
      pythonVersion: string;
      containerRuntime: "docker";
      containerRuntimeVersion: string;
      collectorImageDigest: Sha256Digest;
      composeConfigurationValid: boolean;
      allComponentsHealthy: boolean;
      collectionTransport: "otlp-http-protobuf";
      collectorAcceptanceBoundary: "collector-pipeline-acceptance-only";
      receiverDeliveryBoundary: "postgresql-commit-before-http-200";
      database: {
        engine: "postgresql";
        version: string;
        migration: `${number}_${string}.sql`;
      };
      telemetryBackend: "prometheus";
      dashboard: "grafana";
      pricingSource: "test-fixture";
      contentPolicy: "metadata-only";
      scheduler: "bounded-fixed-rate-v1";
    };
    measurements: {
      startedAt: string;
      completedAt: string;
      actualDurationMilliseconds: number;
      generator: {
        scheduledSpans: number;
        attemptedSpans: number;
        schedulerMissedSpans: number;
        collectorAcceptedSpans: number;
        collectorRejectedSpans: number;
        schedulerMissBasisPoints: number;
        collectorAcceptanceBasisPoints: number;
        exportLatency: AiFinopsSustainedLoadLatency;
      };
      replay: {
        scheduledSpans: number;
        attemptedSpans: number;
        receiverAcknowledgedSpans: number;
        receiverUnacknowledgedSpans: number;
        usageRecordsBeforeReplay: number;
        usageRecordsAfterReplay: number;
      };
      pipeline: {
        usageRecords: number;
        usagePersistenceBasisPoints: number;
        attributionRecords: number;
        attributionCompletionBasisPoints: number;
        costRecords: number;
        costCompletionBasisPoints: number;
        pricedCostRecords: number;
        unpricedCostRecords: number;
        pricedCoverageBasisPoints: number;
        backlogSampleCount: number;
        peakAttributionBacklogRecords: number;
        finalAttributionBacklogRecords: number;
        peakCostBacklogRecords: number;
        finalCostBacklogRecords: number;
        attributionLatency: AiFinopsSustainedLoadLatency;
        costLatency: AiFinopsSustainedLoadLatency;
        drainMilliseconds: number;
      };
      providers: [
        AiFinopsSustainedLoadProviderMeasurements & { provider: "aws.bedrock" },
        AiFinopsSustainedLoadProviderMeasurements & { provider: "openai" },
      ];
      observability: {
        prometheusConverged: boolean;
        expectedUsageRequests: number;
        observedUsageRequests: number;
        expectedCostRequests: number;
        observedCostRequests: number;
        cohortIsolationPreserved: boolean;
        grafanaDashboardAvailable: boolean;
        metadataOnlyPreserved: boolean;
        prohibitedLabelsAbsent: boolean;
      };
    };
    checks: [
      AiFinopsSustainedLoadCheck<"source-binding">,
      AiFinopsSustainedLoadCheck<"profile-binding">,
      AiFinopsSustainedLoadCheck<"compose-configuration">,
      AiFinopsSustainedLoadCheck<"all-components-healthy">,
      AiFinopsSustainedLoadCheck<"bounded-load-volume">,
      AiFinopsSustainedLoadCheck<"fixed-rate-scheduler">,
      AiFinopsSustainedLoadCheck<"collector-acceptance">,
      AiFinopsSustainedLoadCheck<"usage-ledger-persistence">,
      AiFinopsSustainedLoadCheck<"replay-idempotency">,
      AiFinopsSustainedLoadCheck<"attribution-completion">,
      AiFinopsSustainedLoadCheck<"cost-completion">,
      AiFinopsSustainedLoadCheck<"priced-coverage">,
      AiFinopsSustainedLoadCheck<"export-p95-latency">,
      AiFinopsSustainedLoadCheck<"attribution-p95-latency">,
      AiFinopsSustainedLoadCheck<"cost-p95-latency">,
      AiFinopsSustainedLoadCheck<"pipeline-drain">,
      AiFinopsSustainedLoadCheck<"prometheus-convergence">,
      AiFinopsSustainedLoadCheck<"grafana-dashboard">,
      AiFinopsSustainedLoadCheck<"metadata-only-boundaries">,
    ];
    limitations: [
      "synthetic-provider-spans",
      "test-fixture-pricing",
      "single-tenant-single-host-docker-runtime",
      "customer-collector-pki-and-network-not-qualified",
      "live-provider-private-prices-and-invoice-not-qualified",
      "burst-failure-regional-ha-and-long-window-slo-not-qualified",
      "customer-workload-representativeness-not-approved",
    ];
    summary: {
      totalChecks: 19;
      passedChecks: number;
      failedChecks: number;
      scheduledSpans: number;
      persistedUsageRecords: number;
      completedAttributions: number;
      completedCosts: number;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export type AiSavingsRule =
  | "context-growth"
  | "retry-amplification"
  | "expensive-model-anomaly";

export interface AiSavingsObservation {
  metric:
    | "input-tokens-per-request"
    | "retrying-operations-rate"
    | "request-attempts-per-operation"
    | "calculated-cost-per-request";
  unit:
    | "tokens-per-request"
    | "basis-points"
    | "attempts-per-operation"
    | "currency-subunits-per-request";
  baseline: { value: number; sampleCount: number };
  current: { value: number; sampleCount: number };
  changeBasisPoints: number;
}

export type AiSavingsEvidenceRef =
  | { type: "ai-usage-record"; id: AiUsageRecordId }
  | { type: "ai-cost-record"; id: AiCostRecordId }
  | { type: "evidence"; id: EvidenceId }
  | {
      type: "ai-model-suitability-report";
      id: AiModelSuitabilityReportId;
    };

export type AiPotentialSavings =
  | {
      status: "calculated";
      currency: string;
      currencyScale: AiCurrencyScale;
      amountSubunits: number;
      period: { start: string; end: string };
      calculation:
        | {
            method: "avoidable-excess-at-observed-rate";
            excessQuantity: number;
            chargeCategory:
              | "uncached-input-tokens"
              | "cache-read-input-tokens"
              | "cache-write-input-tokens";
            priceSubunitsPerMillionTokens: number;
          }
        | {
            method: "qualified-model-cost-difference";
            candidateCostPerRequestSubunits: number;
            referenceCostPerRequestSubunits: number;
            referenceRequestCount: number;
            suitabilityReportId: AiModelSuitabilityReportId;
          };
      costRecordRefs: AiCostRecordId[];
    }
  | {
      status: "unpriced";
      reasonCode:
        | "unpriced-usage"
        | "ambiguous-pricing"
        | "insufficient-baseline";
    }
  | {
      status: "unresolved";
      reasonCode: "retry-billing-unproven";
      period: { start: string; end: string };
    };

export interface AiSavingsFinding {
  apiVersion: "iip.platform/v1alpha1";
  kind: "AiSavingsFinding";
  metadata: {
    id: AiSavingsFindingId;
    tenantId: string;
    evaluatedAt: string;
  };
  spec: {
    rule: { id: AiSavingsRule; version: string };
    scope: {
      baselineWindow: { start: string; end: string };
      currentWindow: { start: string; end: string };
      provider: string;
      modelId: string;
      candidateModelId?: string;
      region: string;
      serviceName: string;
      deploymentEnvironment: string;
    };
    finding: {
      category: AiSavingsRule;
      severity: "info" | "low" | "medium" | "high";
      summary: string;
      confidenceBasisPoints: number;
    };
    observations: AiSavingsObservation[];
    potentialSavings: AiPotentialSavings;
    recommendation: {
      actionCode:
        | "review-context-retention"
        | "review-retry-policy"
        | "evaluate-lower-cost-model";
      summary: string;
      requiresValidation: true;
    };
    evidenceRefs: AiSavingsEvidenceRef[];
  };
}

export interface AiSavingsFindingPage {
  apiVersion: "iip.platform/v1alpha1";
  kind: "AiSavingsFindingPage";
  metadata: { tenantId: string; generatedAt: string };
  spec: {
    scope: { start: string; end: string };
    items: AiSavingsFinding[];
    page: PageInfo;
  };
}

export interface AiModelSuitabilityReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "AiModelSuitabilityReport";
  metadata: {
    id: AiModelSuitabilityReportId;
    tenantId: string;
    evaluatedAt: string;
    validUntil: string;
  };
  spec: {
    status: "qualified";
    evaluatorVersion: "1.0.0";
    source: {
      kind: "operator-attested" | "test-fixture";
      locator: string;
      retrievedAt: string;
      contentHash: Sha256Digest;
    };
    scope: {
      provider: string;
      referenceModelId: string;
      candidateModelId: string;
      region: string;
      serviceName: string;
      deploymentEnvironment: string;
    };
    workload: {
      profileId: string;
      criteriaDigest: Sha256Digest;
    };
    gates: Array<{
      id: "quality" | "latency" | "safety" | "compliance";
      status: "passed";
      sampleCount: number;
      resultDigest: Sha256Digest;
    }>;
    contentHandling: {
      promptContentPersisted: false;
      responseContentPersisted: false;
      toolContentPersisted: false;
      artifactContainsContent: false;
    };
    limitations: ["workload-specific", "time-bounded", "advisory-only"];
  };
}

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

export type DeploymentDiagnosticComponentId =
  | "control-plane-api"
  | "workflow-worker"
  | "otlp-receiver";

export type DeploymentDiagnosticCheckId =
  | "source-bound-tooling"
  | "explicit-target"
  | "minimized-output"
  | "kubernetes-api-access"
  | "release-selection"
  | "control-plane-availability"
  | "workload-rollouts"
  | "immutable-image-identity"
  | "pod-health-signals";

export interface DeploymentDiagnosticIdentity {
  applicationVersion: string;
  chartVersion: string;
  imageDigest: Sha256Digest;
}

export type DeploymentDiagnosticComponent =
  | {
      id: DeploymentDiagnosticComponentId;
      state: "not-observed";
    }
  | {
      id: DeploymentDiagnosticComponentId;
      state: "healthy" | "progressing" | "unavailable";
      identityStatus: "match" | "mismatch";
      identity: DeploymentDiagnosticIdentity;
      rollout: {
        desiredReplicas: number;
        currentReplicas: number;
        updatedReplicas: number;
        readyReplicas: number;
        availableReplicas: number;
      };
      pods: {
        observed: number;
        ready: number;
        restarts: number;
        unschedulable: number;
        crashLoopingContainers: number;
      };
    }
  | {
      id: DeploymentDiagnosticComponentId;
      state: "healthy" | "progressing" | "unavailable";
      identityStatus: "invalid";
      rollout: {
        desiredReplicas: number;
        currentReplicas: number;
        updatedReplicas: number;
        readyReplicas: number;
        availableReplicas: number;
      };
      pods: {
        observed: number;
        ready: number;
        restarts: number;
        unschedulable: number;
        crashLoopingContainers: number;
      };
    };

export interface DeploymentDiagnosticReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "DeploymentDiagnosticReport";
  metadata: {
    id: `ddr_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: boolean;
  };
  spec: {
    status: "healthy" | "attention-required" | "blocked";
    diagnosticBoundary: "point-in-time-support-only";
    targetBindingDigest: Sha256Digest;
    expectedIdentity: DeploymentDiagnosticIdentity;
    environment: {
      observedAt: string;
      platform: `${string}/${string}`;
      pythonVersion: string;
      kubectlVersion: string;
      clusterAccess: "observed" | "unavailable";
      kubernetesVersion: string | null;
    };
    selection: {
      selectedDeployments: number;
      selectedPods: number;
      unrecognizedDeployments: number;
      duplicateComponents: number;
    };
    components: DeploymentDiagnosticComponent[];
    checks: Array<
      | { id: DeploymentDiagnosticCheckId; status: "passed" }
      | {
          id: DeploymentDiagnosticCheckId;
          status: "warning" | "failed";
          errorCode: `deployment-diagnostic.${string}`;
        }
    >;
    summary: {
      observedComponents: number;
      healthyComponents: number;
      progressingComponents: number;
      unavailableComponents: number;
      notObservedComponents: number;
      totalRestarts: number;
      unschedulablePods: number;
      crashLoopingContainers: number;
      totalChecks: 9;
      passedChecks: number;
      warningChecks: number;
      failedChecks: number;
      overallStatus: "healthy" | "attention-required" | "blocked";
    };
  };
}

export type CustomerDeploymentPreflightCoreCheckId =
  | "helm-render"
  | "immutable-image"
  | "published-image-repository"
  | "api-redundancy"
  | "worker-enrollment"
  | "worker-redundancy"
  | "external-database"
  | "database-transport-security"
  | "controlled-migrations"
  | "oidc-authentication"
  | "external-policy"
  | "workload-identity-broker"
  | "tls-ingress"
  | "network-isolation"
  | "pod-disruption-budget"
  | "hard-topology-spread"
  | "scheduled-backup"
  | "evidence-retention"
  | "platform-telemetry"
  | "operational-alert-policy"
  | "evidence-backends"
  | "service-account-isolation"
  | "test-fixtures-denied";

export type CustomerDeploymentPreflightAiCheckId =
  | "ai-usage-intake"
  | "ai-receiver-mtls"
  | "ai-receiver-redundancy"
  | "ai-cost-allocation-savings"
  | "ai-price-catalog-qualification"
  | "collector-loss-objective";

export type CustomerDeploymentPreflightCheckId =
  | CustomerDeploymentPreflightCoreCheckId
  | CustomerDeploymentPreflightAiCheckId
  | "cluster-api"
  | "operational-alert-api"
  | "operational-alert-namespace"
  | "referenced-dependencies";

export type CustomerDeploymentPreflightRequirement =
  | "signed-published-release"
  | "vulnerability-qualified-release"
  | "customer-oidc-browser-issuer"
  | "customer-policy-bundle"
  | "customer-workload-identity-broker"
  | "customer-collector-pki"
  | "customer-operational-alert-routing"
  | "customer-postgresql-ha-dr"
  | "customer-workload-slo"
  | "live-bedrock-model-region-streaming"
  | "authoritative-ai-price-catalog"
  | "ai-workload-saving-validation";

export type CustomerDeploymentPreflightProfileName =
  | "production-core-v1"
  | "production-ai-finops-v0"
  | "production-core-v2"
  | "production-ai-finops-v1";

export interface CustomerDeploymentPreflightReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerDeploymentPreflightReport";
  metadata: {
    id: `cdp_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: boolean;
  };
  spec: {
    status: "blocked" | "configuration-ready" | "install-ready";
    qualificationBoundary: "pre-install-only";
    profile: {
      name: CustomerDeploymentPreflightProfileName;
      chartVersion: string;
      applicationVersion: string;
      valuesDigest: Sha256Digest;
      configurationDigest: Sha256Digest;
    };
    environment:
      | {
          mode: "static";
          platform: `${string}/${string}`;
          pythonVersion: string;
          helmVersion: string;
        }
      | {
          mode: "cluster";
          platform: `${string}/${string}`;
          pythonVersion: string;
          helmVersion: string;
          kubectlVersion: string;
          kubernetesVersion: string;
          clusterBindingDigest: Sha256Digest;
          namespaceDigest: Sha256Digest;
        };
    dependencies: {
      configuredCount: number;
      observedCount: number;
      missingCount: number;
      invalidCount: number;
      unavailableCount: number;
      bindingDigest: Sha256Digest;
      observationDigest?: Sha256Digest;
      verificationStatus: "not-run" | "passed" | "failed";
    };
    checks: Array<
      | { id: CustomerDeploymentPreflightCheckId; status: "passed" }
      | {
          id: CustomerDeploymentPreflightCheckId;
          status: "failed" | "not-run";
          errorCode: `preflight.${string}`;
        }
    >;
    customerQualificationRequired: CustomerDeploymentPreflightRequirement[];
    summary: {
      totalChecks: 26 | 27 | 32 | 33;
      passedChecks: number;
      failedChecks: number;
      notRunChecks: number;
      overallStatus: "blocked" | "configuration-ready" | "install-ready";
    };
  };
}

export type KubernetesAvailabilityComponentId =
  | "control-plane-api"
  | "workflow-worker"
  | "otlp-receiver";

export type KubernetesAvailabilityCheckId =
  | "source-binding"
  | "isolated-cluster"
  | "immutable-image"
  | "zero-unavailable-rollout"
  | "component-pdbs"
  | "hard-topology-spread"
  | "durable-intake-seed"
  | "component-baseline"
  | "voluntary-drain"
  | "disruption-capacity"
  | "api-zero-failure"
  | "receiver-zero-failure"
  | "receiver-durable-intake"
  | "worker-baseline-completion"
  | "worker-disruption-completion"
  | "worker-recovery-completion"
  | "component-recovery"
  | "output-minimization";

export interface KubernetesAvailabilityComponentState {
  readyReplicas: number;
  unavailableReplicas: number;
  domainCount: number;
  pdbDisruptionsAllowed: number;
}

export interface KubernetesAvailabilityQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "KubernetesAvailabilityQualificationReport";
  metadata: {
    id: `kaq_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: boolean;
  };
  spec: {
    status: "qualified";
    qualificationLevel: "local-multi-node-kind-v2";
    subject: {
      applicationVersion: string;
      chartVersion: string;
      requiredMigration: `${number}_${string}.sql`;
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    environment: {
      platform: `linux/${string}`;
      kubernetesVersion: string;
      kindVersion: string;
      dockerVersion: string;
      containerdVersion: string;
      nodeCount: 3;
      workerNodeCount: 2;
      clusterBindingDigest: Sha256Digest;
    };
    policy: {
      rollout: { maxUnavailable: 0; maxSurge: 1 };
      podDisruptionBudget: { minAvailable: 1 };
      topologySpread: {
        topologyKey: "kubernetes.io/hostname";
        maxSkew: 1;
        minDomains: 2;
        whenUnsatisfiable: "DoNotSchedule";
      };
    };
    components: Array<{
      id: KubernetesAvailabilityComponentId;
      desiredReplicas: 2;
      baseline: KubernetesAvailabilityComponentState;
      disruption: KubernetesAvailabilityComponentState;
      recovery: KubernetesAvailabilityComponentState;
    }>;
    intakeSeed: { resourceAccepted: true; metricAccepted: true };
    receiverIntake: {
      signal: "metric";
      payload: "non-empty-otlp-protobuf";
      successBoundary: "postgresql-commit-before-http-200";
    };
    workflowProcessing: {
      operation: "durable-investigation";
      phases: Array<{
        id: "baseline" | "disruption" | "recovery";
        submitted: 1;
        completed: 1;
        failures: 0;
        pollAttempts: number;
        completionMilliseconds: number;
      }>;
    };
    disruption: {
      method: "kubectl-drain";
      targetNodeDigest: Sha256Digest;
      nodeCordoned: true;
      nodeDrained: true;
      nodeUncordoned: true;
      evictedComponentCount: 3;
    };
    probes: Array<{
      id: "control-plane-api" | "otlp-metrics";
      protocol: "http-json" | "otlp-http-protobuf";
      path: "/v1/system/version" | "/v1/metrics";
      phases: Array<{
        id: "baseline" | "disruption" | "recovery";
        attempts: number;
        successes: number;
        failures: 0;
      }>;
    }>;
    summary: {
      totalChecks: 18;
      passedChecks: 18;
      failedChecks: 0;
      totalProbeAttempts: number;
      failedProbeAttempts: 0;
      totalWorkflowSubmissions: 3;
      completedWorkflows: 3;
      failedWorkflows: 0;
      maximumWorkflowCompletionMilliseconds: number;
      overallStatus: "qualified";
    };
    checks: Array<{ id: KubernetesAvailabilityCheckId; status: "passed" }>;
    limitations: [
      "planned-disruption-only",
      "local-single-region",
      "shared-dependencies-not-qualified",
      "involuntary-failure-not-qualified",
    ];
  };
}

export type CustomerContinuityQualificationCheckId =
  | "source-binding"
  | "minimized-output"
  | "direct-no-redirect-probe"
  | "explicit-context"
  | "exact-release-identity"
  | "redundant-capacity"
  | "zero-unavailable-rollout"
  | "pdb-protected"
  | "sustained-probe-window"
  | "probe-eviction-overlap"
  | "eviction-admitted"
  | "replacement-observed"
  | "recovery-objective"
  | "ingress-probe-qualified"
  | "availability-objective"
  | "latency-objective";

export interface CustomerContinuityQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerContinuityQualificationReport";
  metadata: {
    id: `ccq_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "qualified" | "not-qualified";
    qualificationLevel: "customer-control-plane-pod-eviction-v1";
    subject: {
      applicationVersion: string;
      chartVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      requiredMigration: `${number}_${string}.sql`;
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    bindings: {
      targetBindingDigest: Sha256Digest;
      kubernetesContextBindingDigest: Sha256Digest;
      namespaceBindingDigest: Sha256Digest;
      deploymentBindingDigest: Sha256Digest;
      evictedPodBindingDigest: Sha256Digest;
    };
    objective: {
      sampleCount: number;
      intervalMilliseconds: number;
      minimumWindowSeconds: number;
      minimumBaselineSeconds: number;
      minimumPostRecoverySeconds: number;
      minimumAvailabilityBasisPoints: number;
      maximumP95LatencyMilliseconds: number;
      requestTimeoutMilliseconds: number;
      maximumRecoverySeconds: number;
    };
    environment: {
      platform: `${string}/${string}`;
      pythonVersion: string;
      kubernetesVersion: string;
      transport: "verified-https";
      caSource: "system" | "custom";
      proxyMode: "disabled";
      redirectMode: "deny";
      evictionApi: "policy/v1";
    };
    measurements: {
      startedAt: string;
      evictionStartedAt: string;
      recoveredAt: string;
      completedAt: string;
      scheduledWindowSeconds: number;
      actualWindowSeconds: number;
      baselineSeconds: number;
      recoverySeconds: number;
      postRecoverySeconds: number;
      ingress: {
        reportId: `iaq_${string}`;
        reportDigest: Sha256Digest;
        status: "qualified" | "not-qualified";
        sampleCount: number;
        successfulSamples: number;
        failedSamples: number;
        availabilityBasisPoints: number;
        p95CycleLatencyMilliseconds: number | null;
        failureCategories: {
          transport: number;
          "http-status": number;
          contract: number;
          identity: number;
        };
      };
      deployment: {
        desiredReplicas: number;
        readyReplicasBefore: number;
        readyReplicasAfter: number;
        maxUnavailable: 0;
        pdbMinAvailable: number;
        pdbDisruptionsAllowedBefore: number;
        evictionAccepted: true;
        originalPodReplaced: true;
      };
    };
    checks: Array<
      | { id: CustomerContinuityQualificationCheckId; status: "passed" }
      | {
          id: CustomerContinuityQualificationCheckId;
          status: "failed";
          errorCode: `customer-continuity.${string}`;
        }
    >;
    limitations: [
      "single-api-pod-eviction",
      "single-cluster",
      "read-only-synthetic-traffic",
      "database-failure-not-qualified",
      "worker-receiver-continuity-not-qualified",
      "regional-slo-not-qualified",
    ];
    summary: {
      totalChecks: 16;
      passedChecks: number;
      failedChecks: number;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export interface CustomerProcessingQualificationProfile {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerProcessingQualificationProfile";
  metadata: { tenantId: string; actorId: string };
  spec: {
    resourceUid: `res_${string}`;
    metric: { name: string; unit: string; serviceName: string };
    investigation: {
      evidenceTypes: Array<
        | "resource.change"
        | "kubernetes.event"
        | "telemetry.metric"
        | "telemetry.log"
      >;
      allowedTools: Array<
        | "resources/query"
        | "evidence/fetch"
        | "events/query"
        | "metrics/query"
        | "logs/query"
      >;
      maxToolCalls: number;
      maxWallTimeSeconds: number;
      maxEvidenceItems: number;
      maxIterations: number;
    };
  };
}

export interface CustomerPostgreSQLContinuityProfile {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerPostgreSQLContinuityProfile";
  metadata: { tenantId: string; actorId: string };
  spec: {
    resourceUid: `res_${string}`;
    metric: { name: string; unit: string; serviceName: string };
    investigation: CustomerProcessingQualificationProfile["spec"]["investigation"];
    database: { name: string; user: string; minimumMajorVersion: number };
  };
}

export type CustomerPostgreSQLContinuityCheckId =
  | "source-binding"
  | "minimized-output"
  | "explicit-target-bindings"
  | "api-verified-https"
  | "receiver-mutual-tls"
  | "database-verified-tls"
  | "database-read-only-probe"
  | "initial-writable-primary"
  | "promotion-observed"
  | "timeline-advanced"
  | "promoted-writable-primary"
  | "api-availability"
  | "receiver-availability"
  | "bounded-api-outage"
  | "bounded-receiver-outage"
  | "receiver-durable-intake"
  | "workflow-baseline-completion"
  | "workflow-survived-promotion"
  | "workflow-recovery-completion";

export interface CustomerPostgreSQLContinuityQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerPostgreSQLContinuityQualificationReport";
  metadata: {
    id: `cpgq_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "qualified" | "not-qualified";
    qualificationLevel: "customer-postgresql-primary-promotion-v1";
    subject: {
      applicationVersion: string;
      chartVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      requiredMigration: `${number}_${string}.sql`;
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    bindings: {
      apiTargetBindingDigest: Sha256Digest;
      otlpTargetBindingDigest: Sha256Digest;
      databaseTargetBindingDigest: Sha256Digest;
      kubernetesContextBindingDigest: Sha256Digest;
      namespaceBindingDigest: Sha256Digest;
      profileDigest: Sha256Digest;
    };
    objective: {
      minimumProbeAttemptsPerPhase: number;
      probeIntervalMilliseconds: number;
      maximumPromotionMilliseconds: number;
      maximumWorkflowCompletionMilliseconds: number;
      requestTimeoutMilliseconds: number;
      minimumApiAvailabilityBasisPoints: number;
      minimumReceiverAvailabilityBasisPoints: number;
      maximumConsecutiveFailures: number;
    };
    environment: {
      platform: `${string}/${string}`;
      pythonVersion: string;
      databaseEngine: "postgresql";
      databaseMajorVersion: number;
      databaseTransport: "verified-tls";
      databaseCaSource: "custom";
      databaseClientIdentity: "password" | "mutual-tls-password";
      apiTransport: "verified-https";
      otlpTransport: "mutual-tls-https";
      apiCaSource: "system" | "custom";
      otlpCaSource: "system" | "custom";
      proxyMode: "disabled";
      redirectMode: "deny";
    };
    databaseProbe: {
      mode: "read-only-native-functions";
      session: "default-transaction-read-only";
      primarySelection: "libpq-target-session-attrs-read-write";
      promotionTrigger: "external-operator";
      promotionProof: "writable-primary-wal-timeline-advance";
    };
    receiverIntake: {
      signal: "metric";
      payload: "non-empty-otlp-protobuf";
      successBoundary: "postgresql-commit-before-http-200";
    };
    measurements: {
      startedAt: string;
      promotionWaitStartedAt: string;
      promotionObservedAt: string;
      completedAt: string;
      database: {
        initialTimeline: number;
        promotedTimeline: number;
        timelineDelta: number;
        primaryBefore: boolean;
        primaryAfter: boolean;
        tlsBefore: boolean;
        tlsAfter: boolean;
        transactionReadOnlyBefore: boolean;
        transactionReadOnlyAfter: boolean;
        initialServerBindingDigest: Sha256Digest;
        promotedServerBindingDigest: Sha256Digest;
        serverIdentityChanged: boolean;
        connectionAttempts: number;
        connectionFailures: number;
        promotionMilliseconds: number;
      };
      phases: Array<{
        id: "baseline" | "promotion-observation" | "recovery";
        apiAttempts: number;
        apiSuccesses: number;
        apiFailures: number;
        apiMaximumConsecutiveFailures: number;
        receiverAttempts: number;
        receiverSuccesses: number;
        receiverFailures: number;
        receiverMaximumConsecutiveFailures: number;
        workflowSubmitted: 1;
        workflowCompleted: 0 | 1;
        workflowFailures: 0 | 1;
        workflowPollAttempts: number;
        workflowCompletionMilliseconds: number;
        workflowBoundary:
          | "completed-before-promotion"
          | "submitted-before-observed-after-promotion"
          | "completed-after-promotion";
      }>;
    };
    checks: Array<
      | { id: CustomerPostgreSQLContinuityCheckId; status: "passed" }
      | {
          id: CustomerPostgreSQLContinuityCheckId;
          status: "failed";
          errorCode: `customer-postgresql-continuity.${string}`;
        }
    >;
    limitations: [
      "operator-triggered-planned-promotion",
      "single-stable-database-endpoint",
      "topology-and-failure-domain-not-proven",
      "fencing-and-split-brain-not-proven",
      "zero-data-loss-and-rpo-not-proven",
      "synthetic-qualification-traffic",
      "regional-disaster-recovery-not-proven",
    ];
    summary: {
      totalChecks: 19;
      passedChecks: number;
      failedChecks: number;
      totalApiAttempts: number;
      failedApiAttempts: number;
      apiAvailabilityBasisPoints: number;
      totalReceiverAttempts: number;
      failedReceiverAttempts: number;
      receiverAvailabilityBasisPoints: number;
      totalWorkflowSubmissions: 3;
      completedWorkflows: number;
      failedWorkflows: number;
      maximumApiConsecutiveFailures: number;
      maximumReceiverConsecutiveFailures: number;
      promotionMilliseconds: number;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export interface CustomerOidcQualificationProfile {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerOidcQualificationProfile";
  metadata: { name: string; reviewedAt: string };
  spec: {
    identity: { tenantId: string; actorId: string; roles: string[] };
    oidc: {
      issuer: string;
      audience: string;
      discoveryUrl: string;
      jwksUrl: string;
      actorClaim: string;
      tenantClaim: string;
      rolesClaim: string;
      browser: {
        clientId: string;
        authorizationEndpoint: string;
        tokenEndpoint: string;
        redirectUri: string;
        scopes: string[];
        providerLabel: string;
      };
    };
    objective: {
      minimumRemainingTokenSeconds: number;
      maximumTokenLifetimeSeconds: number;
      requestTimeoutMilliseconds: number;
      maximumResponseBytes: number;
    };
  };
}

export type CustomerOidcQualificationCheckId =
  | "profile-binding"
  | "source-binding"
  | "api-target-binding"
  | "ca-verified-discovery"
  | "issuer-metadata-binding"
  | "jwks-metadata-binding"
  | "authorization-code-supported"
  | "s256-supported"
  | "public-client-supported"
  | "console-profile-binding"
  | "token-cors-exact-origin"
  | "token-cors-other-origin-denied"
  | "access-token-claims-binding"
  | "access-token-api-authentication"
  | "tampered-token-denial"
  | "runtime-release-binding"
  | "minimized-output";

export interface CustomerOidcQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerOidcQualificationReport";
  metadata: {
    id: `coq_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "qualified" | "not-qualified";
    qualification: "customer-oidc-verifier-browser-prerequisites-v1";
    subject: {
      applicationVersion: string;
      chartVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      requiredMigration: `${number}_${string}.sql`;
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    bindings: {
      apiTargetBindingDigest: Sha256Digest;
      profileDigest: Sha256Digest;
      issuerMetadataDigest: Sha256Digest;
      runtimeBindingDigest: Sha256Digest;
    };
    objective: CustomerOidcQualificationProfile["spec"]["objective"];
    protocol: {
      discoveryTransport: "ca-verified-https-no-redirect";
      apiTransport: "ca-verified-https-no-redirect";
      flow: "authorization-code";
      pkceMethod: "S256";
      tokenEndpointAuthentication: "none";
      accessTokenAlgorithm: "RS256";
      apiAuthentication: "bearer";
    };
    measurements: {
      startedAt: string;
      completedAt: string;
      discoveryResponseBytes: number;
      jwksResponseBytes: number;
      jwksKeyCount: number;
      tokenLifetimeSeconds: number;
      tokenRemainingSeconds: number;
    };
    checks: Array<
      | { id: CustomerOidcQualificationCheckId; status: "passed" }
      | {
          id: CustomerOidcQualificationCheckId;
          status: "failed";
          errorCode: `customer-oidc-qualification.${string}`;
        }
    >;
    limitations: [
      "interactive-authorization-and-mfa-not-observed",
      "logout-session-and-consent-not-qualified",
      "disablement-and-revocation-latency-not-qualified",
      "issuer-ha-certificate-and-key-rotation-not-qualified",
    ];
    summary: {
      totalChecks: 17;
      passedChecks: number;
      failedChecks: number;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export interface CustomerOperationalAlertQualificationProfile {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerOperationalAlertQualificationProfile";
  metadata: { name: string; reviewedAt: string };
  spec: {
    ruleSet: "core-v1" | "ai-finops-v0";
    deployment: {
      clusterBindingDigest: Sha256Digest;
      namespaceBindingDigest: Sha256Digest;
    };
    monitoring: {
      prometheusBaseUrl: string;
      alertmanagerBaseUrl: string;
      receiptUrl: string;
      productionRuleGroup: "iip.platform.availability";
      syntheticRuleGroup: string;
      syntheticAlertName: "IIPQualificationSynthetic";
      probeId: string;
      routeId: string;
      services: Array<{
        component: "api" | "workflow-worker" | "otlp-receiver";
        serviceName: string;
      }>;
    };
    objective: {
      requestTimeoutMilliseconds: number;
      maximumResponseBytes: number;
      maximumObservationAgeSeconds: number;
      maximumNotificationLatencyMilliseconds: number;
      maximumClockSkewSeconds: number;
      maximumProfileAgeSeconds: number;
      reportValiditySeconds: number;
    };
  };
}

export type CustomerOperationalAlertQualificationCheckId =
  | "profile-binding"
  | "source-binding"
  | "immutable-release"
  | "protected-input-files"
  | "verified-https"
  | "no-proxy-no-redirect"
  | "prometheus-ready"
  | "production-rule-group-loaded"
  | "production-rules-complete"
  | "production-rules-healthy"
  | "component-heartbeats-observed"
  | "synthetic-rule-loaded"
  | "synthetic-rule-healthy"
  | "synthetic-rule-inactive"
  | "alertmanager-ready"
  | "receipt-evidence-current"
  | "firing-notification-delivered"
  | "recovery-notification-delivered"
  | "notification-latency-objective"
  | "minimized-output";

export interface CustomerOperationalAlertQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerOperationalAlertQualificationReport";
  metadata: {
    id: `coar_${string}`;
    generatedAt: string;
    validUntil: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "qualified" | "not-qualified";
    qualification: "customer-operational-alert-routing-v1";
    qualificationBoundary: "customer-rule-evaluation-and-notification-route";
    subject: {
      applicationVersion: string;
      chartVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      requiredMigration: `${number}_${string}.sql`;
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    bindings: {
      profileDigest: Sha256Digest;
      clusterBindingDigest: Sha256Digest;
      namespaceBindingDigest: Sha256Digest;
      prometheusTargetBindingDigest: Sha256Digest;
      alertmanagerTargetBindingDigest: Sha256Digest;
      receiptTargetBindingDigest: Sha256Digest;
      probeRouteBindingDigest: Sha256Digest;
      prometheusCaBundleDigest: Sha256Digest;
      alertmanagerCaBundleDigest: Sha256Digest;
      receiptCaBundleDigest: Sha256Digest;
      releaseBindingDigest: Sha256Digest;
    };
    profile: {
      name: "customer-operational-alert-routing-v1";
      ruleSet: "core-v1" | "ai-finops-v0";
      metricNameProfile: "otel-prometheus-underscore-no-suffix-v1";
      prometheusApi: "v1";
      alertmanagerApi: "v2";
      receiptApi: "iip.qualification/v1";
      transport: "ca-verified-https";
      proxyMode: "disabled";
      redirectMode: "denied";
    };
    objective: CustomerOperationalAlertQualificationProfile["spec"]["objective"];
    measurements: {
      profileReviewedAt: string;
      startedAt: string;
      completedAt: string;
      profileAgeSeconds: number;
      expectedRuleCount: number;
      loadedExpectedRuleCount: number;
      unhealthyExpectedRuleCount: number;
      expectedComponentCount: number;
      observedComponentCount: number;
      receiptEventCount: number;
      firingNotificationCount: number;
      recoveryNotificationCount: number;
      maximumNotificationLatencyMilliseconds: number;
    };
    observations: {
      verifiedHttps: boolean;
      protectedInputs: boolean;
      prometheusReady: boolean;
      productionGroupLoaded: boolean;
      syntheticRuleLoaded: boolean;
      syntheticRuleHealthy: boolean;
      syntheticRuleInactive: boolean;
      alertmanagerReady: boolean;
      receiptEvidenceCurrent: boolean;
    };
    checks: Array<
      | {
          id: CustomerOperationalAlertQualificationCheckId;
          status: "passed";
        }
      | {
          id: CustomerOperationalAlertQualificationCheckId;
          status: "failed";
          errorCode: `customer-operational-alert-qualification.${string}`;
        }
    >;
    limitations: [
      "customer-receipt-service-authenticity-and-retention-not-qualified",
      "additional-routes-receivers-silences-and-inhibition-not-qualified",
      "human-on-call-acknowledgement-and-escalation-not-qualified",
      "collector-backend-and-alertmanager-ha-not-qualified",
      "long-window-regional-slo-and-disaster-recovery-not-qualified",
      "real-component-failure-and-customer-workload-impact-not-qualified",
    ];
    summary: {
      totalChecks: 20;
      passedChecks: number;
      failedChecks: number;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export interface CustomerCredentialBrokerQualificationProfile {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerCredentialBrokerQualificationProfile";
  metadata: { name: string; reviewedAt: string };
  spec: {
    endpoint: string;
    cases: [
      {
        id: "exact-authority";
        tenantId: string;
        actorId: string;
        integrationId: string;
        credentialRef: `credential://${string}`;
        provider: string;
        scopes: string[];
        expectedOutcome: "issued";
      },
      ...Array<{
        id:
          | "cross-tenant-denial"
          | "actor-binding-denial"
          | "integration-binding-denial"
          | "provider-binding-denial"
          | "scope-escalation-denial"
          | "credential-reference-denial";
        tenantId: string;
        actorId: string;
        integrationId: string;
        credentialRef: `credential://${string}`;
        provider: string;
        scopes: string[];
        expectedOutcome: "denied";
      }>,
    ];
    objective: {
      requestTimeoutSeconds: number;
      maximumResponseBytes: number;
      maximumLeaseSeconds: number;
      maximumClockSkewSeconds: number;
      leaseRequestDeadlineSeconds: number;
      maximumLeaseLatencyMilliseconds: number;
      maximumProfileAgeSeconds: number;
    };
  };
}

export type CustomerCredentialBrokerQualificationCheckId =
  | "profile-binding"
  | "source-binding"
  | "protected-input-files"
  | "endpoint-binding"
  | "ca-verified-tls"
  | "workload-identity-accepted"
  | "exact-authority-issued"
  | "cross-tenant-denied"
  | "actor-binding-denied"
  | "integration-binding-denied"
  | "provider-binding-denied"
  | "scope-escalation-denied"
  | "credential-reference-denied"
  | "response-correlation"
  | "lease-bounds"
  | "repeat-consistency"
  | "latency-objective"
  | "minimized-output";

export interface CustomerCredentialBrokerQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerCredentialBrokerQualificationReport";
  metadata: {
    id: `ccbq_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "qualified" | "not-qualified";
    qualification: "customer-credential-broker-authority-prerequisites-v1";
    subject: {
      applicationVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    bindings: {
      endpointBindingDigest: Sha256Digest;
      profileDigest: Sha256Digest;
      authoritySetDigest: Sha256Digest;
      caBundleDigest: Sha256Digest;
    };
    profile: {
      name: "customer-external-http-credential-broker-v1";
      brokerProtocol: "iip.broker/v1alpha1";
      transport: "ca-verified-https-json";
      identityMode: "protected-token-file-read-per-request";
      leaseScheme: "bearer";
      redirectMode: "denied";
      proxyMode: "disabled";
    };
    objective: CustomerCredentialBrokerQualificationProfile["spec"]["objective"];
    measurements: {
      profileReviewedAt: string;
      startedAt: string;
      completedAt: string;
      caseCount: 7;
      issuedCaseCount: 1;
      deniedCaseCount: 6;
      requestCount: 8;
      minimumRemainingLeaseSeconds: number;
      maximumLeaseLatencyMilliseconds: number;
    };
    checks: Array<
      | {
          id: CustomerCredentialBrokerQualificationCheckId;
          status: "passed";
        }
      | {
          id: CustomerCredentialBrokerQualificationCheckId;
          status: "failed";
          errorCode: `customer-credential-broker-qualification.${string}`;
        }
    >;
    limitations: [
      "workload-identity-rotation-revocation-and-federation-not-qualified",
      "provider-credential-rotation-revocation-and-emergency-access-not-qualified",
      "broker-ha-network-certificate-rotation-and-recovery-not-qualified",
      "audit-delivery-retention-and-siem-integration-not-qualified",
      "non-bearer-provider-credentials-not-qualified",
    ];
    summary: {
      totalChecks: 18;
      passedChecks: number;
      failedChecks: number;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export interface CustomerGithubContextQualificationProfile {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerGithubContextQualificationProfile";
  metadata: { name: string; reviewedAt: string };
  spec: {
    serviceMode: "github-cloud" | "github-enterprise-server";
    query: {
      tenantId: string;
      actorId: string;
      integrationId: string;
      resourceUid: `res_${string}`;
      referenceId: string;
      documentKind: "runbook" | "source" | "configuration" | "service-catalog";
    };
    expected: {
      endpoint: `https://${string}`;
      apiVersion: string;
      credentialRef: `credential://${string}`;
      repositoryOwner: string;
      repositoryName: string;
      commitSha: string;
      path: string;
      blobSha: string;
    };
    objective: {
      requestTimeoutSeconds: number;
      maximumResponseBytes: number;
      maximumDocumentBytes: number;
      requestDeadlineSeconds: number;
      maximumReadLatencyMilliseconds: number;
      maximumProfileAgeSeconds: number;
    };
  };
}

export type CustomerGithubContextQualificationCheckId =
  | "profile-binding"
  | "source-binding"
  | "immutable-release"
  | "protected-input-files"
  | "integration-config-binding"
  | "exact-endpoint"
  | "exact-api-version"
  | "qualified-credential-broker"
  | "exact-read-authority"
  | "workload-identity-accepted"
  | "provider-credential-accepted"
  | "ca-verified-tls"
  | "direct-no-proxy-no-redirect"
  | "immutable-commit-request"
  | "document-path-binding"
  | "complete-single-document"
  | "expected-git-blob"
  | "bounded-read-latency"
  | "content-not-retained"
  | "minimized-output";

export interface CustomerGithubContextQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerGithubContextQualificationReport";
  metadata: {
    id: `cgcq_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "qualified" | "not-qualified";
    qualification: "customer-github-context-prerequisites-v1";
    subject: {
      applicationVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    bindings: {
      profileDigest: Sha256Digest;
      integrationConfigurationDigest: Sha256Digest;
      endpointBindingDigest: Sha256Digest;
      repositoryBindingDigest: Sha256Digest;
      documentBindingDigest: Sha256Digest;
      githubCaBundleDigest: Sha256Digest;
      credentialBrokerReportDigest: Sha256Digest;
      credentialBrokerEndpointBindingDigest: Sha256Digest;
      credentialBrokerProfileDigest: Sha256Digest;
      credentialBrokerAuthoritySetDigest: Sha256Digest;
      credentialBrokerCaBundleDigest: Sha256Digest;
      observedRevisionDigest: Sha256Digest;
    };
    profile: {
      name: "customer-github-context-prerequisites-v1";
      serviceMode: "github-cloud" | "github-enterprise-server";
      provider: "github";
      api: "rest-repository-contents";
      credentialMode: "external-request-scoped-bearer";
      credentialScope: "repository:contents:read";
      revisionMode: "exact-commit-and-git-blob";
      transport: "ca-verified-https-json";
      redirectMode: "denied";
      proxyMode: "disabled";
      contentRetention: "none-in-qualification-report";
    };
    objective: CustomerGithubContextQualificationProfile["spec"]["objective"];
    measurements: {
      profileReviewedAt: string;
      startedAt: string;
      completedAt: string;
      selectedRepositoryCount: 1;
      requestedDocumentCount: 1;
      returnedDocumentCount: 0 | 1;
      readLatencyMilliseconds: number;
    };
    checks: Array<
      | { id: CustomerGithubContextQualificationCheckId; status: "passed" }
      | {
          id: CustomerGithubContextQualificationCheckId;
          status: "failed";
          errorCode: `customer-github-context-qualification.${string}`;
        }
    >;
    limitations: [
      "github-app-installation-and-credential-lifecycle-not-qualified",
      "organization-repository-and-content-governance-not-qualified",
      "rate-limit-secondary-throttling-and-sustained-load-not-qualified",
      "certificate-network-proxy-and-service-ha-not-qualified",
      "additional-repositories-documents-and-provider-apis-not-qualified",
    ];
    summary: {
      totalChecks: 20;
      passedChecks: number;
      failedChecks: number;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export interface CustomerBedrockQualificationProfile {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerBedrockQualificationProfile";
  metadata: {
    name: string;
    environmentId: string;
    reviewedAt: string;
  };
  spec: {
    target: {
      provider: "aws.bedrock";
      modelId: string;
      region: string;
      operation: "Converse" | "ConverseStream";
      credentialsProfile: string;
    };
    release: {
      applicationVersion: string;
      imageDigest: Sha256Digest;
    };
    objective: {
      maximumProviderCallLatencyMilliseconds: number;
      maximumProfileAgeSeconds: number;
      maximumReportAgeSeconds: number;
    };
  };
}

export type CustomerBedrockQualificationCheckId =
  | "profile-binding"
  | "source-binding"
  | "immutable-release"
  | "protected-profile"
  | "protected-session-credentials"
  | "exact-provider"
  | "exact-model"
  | "exact-region"
  | "exact-operation"
  | "live-provider-call"
  | "official-instrumentation"
  | "supported-instrumentation-scope"
  | "metadata-only-span"
  | "provider-token-totals"
  | "cache-meter-enrichment"
  | "otel-total-input-semantics"
  | "receiver-normalization"
  | "async-export-failure-isolated"
  | "cost-eligibility-honest"
  | "minimized-output";

export interface CustomerBedrockQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerBedrockQualificationReport";
  metadata: {
    id: `cbq_${string}`;
    generatedAt: string;
    validUntil: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "qualified" | "not-qualified";
    qualification: "customer-bedrock-live-interoperability-v1";
    subject: {
      applicationVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    bindings: {
      profileDigest: Sha256Digest;
      environmentBindingDigest: Sha256Digest;
      targetBindingDigest: Sha256Digest;
      liveCompatibilityReportDigest: Sha256Digest;
    };
    profile: {
      name: "customer-bedrock-live-interoperability-v1";
      provider: "aws.bedrock";
      operation: "Converse" | "ConverseStream";
      invocationTarget: "aws-bedrock";
      instrumentation: "official-botocore-with-pinned-iip-usage-adapter";
      credentialMode: "protected-dedicated-session-credentials-file";
      requestPath: "direct-to-provider";
      telemetryPath: "asynchronous-otel";
      contentPolicy: "fixed-synthetic-request-not-retained";
      costEligibility: "partial-usage-not-exact-cost-eligible";
    };
    objective: CustomerBedrockQualificationProfile["spec"]["objective"];
    measurements: {
      profileReviewedAt: string;
      startedAt: string;
      completedAt: string;
      providerCallCount: 1;
      normalizedUsageRecordCount: 1;
      providerCallLatencyMilliseconds: number;
    };
    checks: Array<
      | { id: CustomerBedrockQualificationCheckId; status: "passed" }
      | {
          id: CustomerBedrockQualificationCheckId;
          status: "failed";
          errorCode: `customer-bedrock-qualification.${string}`;
        }
    >;
    limitations: [
      "credential-expiration-iam-least-authority-and-revocation-not-qualified",
      "model-quality-safety-and-output-correctness-not-qualified",
      "customer-collector-pki-and-network-path-not-qualified",
      "authoritative-pricing-private-rates-and-invoice-agreement-not-qualified",
      "sustained-load-quota-throttling-and-regional-ha-not-qualified",
      "additional-models-regions-operations-and-provider-apis-not-qualified",
    ];
    summary: {
      totalChecks: 20;
      passedChecks: number;
      failedChecks: number;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export interface CustomerAiFinopsPrerequisiteProfile {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerAiFinopsPrerequisiteProfile";
  metadata: {
    name: string;
    environmentId: string;
    reviewedAt: string;
  };
  spec: {
    release: {
      applicationVersion: string;
      chartVersion: string;
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    collection: {
      provider: "aws.bedrock";
      operation: "ConverseStream";
      requestPath: "direct-to-provider";
      telemetryPath: "asynchronous-otel";
      contentPolicy: "metadata-only";
    };
    pricing: {
      tenantId: string;
      catalogId: AiPriceCatalogId;
      catalogVersion: string;
      catalogDocumentDigest: Sha256Digest;
      qualificationPolicyId: AiPriceCatalogQualificationPolicyId;
      qualificationPolicyVersion: string;
      sourceClass: "provider-published" | "operator-managed";
      costBasis: "calculated-estimate";
    };
    presentation: {
      telemetryBackend: "prometheus";
      dashboard: "grafana";
    };
    objective: {
      maximumProfileAgeSeconds: number;
      maximumEvidenceAgeSeconds: number;
      maximumClockSkewSeconds: number;
      reportValiditySeconds: number;
    };
  };
}

export type CustomerAiFinopsPrerequisiteEvidenceId =
  | "release-readiness"
  | "local-ai-finops-runtime"
  | "customer-deployment"
  | "customer-otlp-receiver"
  | "customer-bedrock"
  | "production-price-catalog";

export type CustomerAiFinopsPrerequisiteCheckId =
  | "source-binding"
  | "profile-review"
  | "exact-release-identity"
  | "ai-finops-deployment-profile"
  | "release-readiness"
  | "local-ai-finops-runtime"
  | "customer-deployment"
  | "customer-otlp-receiver"
  | "customer-bedrock-live"
  | "production-price-catalog"
  | "runtime-readiness-chain"
  | "receiver-deployment-chain"
  | "catalog-profile-binding"
  | "metadata-only-direct-request-path"
  | "evidence-freshness"
  | "minimized-prerequisite-only-output";

export interface CustomerAiFinopsPrerequisiteReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerAiFinopsPrerequisiteReport";
  metadata: {
    id: `cafp_${string}`;
    generatedAt: string;
    validUntil: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "prerequisites-ready" | "not-ready";
    qualificationLevel: "customer-ai-finops-prerequisites-v1";
    qualificationBoundary: "prerequisite-aggregation-only";
    subject: {
      deploymentProfile: "production-ai-finops-v0" | "production-ai-finops-v1";
      applicationVersion: string;
      chartVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    bindings: {
      profileDigest: Sha256Digest;
      environmentBindingDigest: Sha256Digest;
      priceTenantBindingDigest: Sha256Digest;
      catalogBindingDigest: Sha256Digest;
      releaseReadinessReportDigest: Sha256Digest;
      aiFinopsRuntimeReportDigest: Sha256Digest;
      customerDeploymentReportDigest: Sha256Digest;
      customerOtlpReceiverReportDigest: Sha256Digest;
      customerBedrockReportDigest: Sha256Digest;
      priceCatalogQualificationReportDigest: Sha256Digest;
    };
    profile: {
      provider: "aws.bedrock";
      operation: "ConverseStream";
      requestPath: "direct-to-provider";
      telemetryPath: "asynchronous-otel";
      contentPolicy: "metadata-only";
      costBasis: "calculated-estimate";
      pricingSourceClass: "provider-published" | "operator-managed";
      telemetryBackend: "prometheus";
      dashboard: "grafana";
    };
    objective: CustomerAiFinopsPrerequisiteProfile["spec"]["objective"];
    measurements: {
      profileReviewedAt: string;
      qualifiedAt: string;
      oldestEvidenceAgeSeconds: number;
      productionCatalogEntryCount: number;
      productionCatalogRequiredScopeCount: number;
      customerReceiverDeliveredItemCount: number;
      liveProviderCallCount: number;
    };
    evidence: Array<{
      id: CustomerAiFinopsPrerequisiteEvidenceId;
      contractKind:
        | "ReleaseReadinessReport"
        | "AiFinopsRuntimeCompatibilityReport"
        | "CustomerDeploymentQualificationReport"
        | "CustomerOtlpReceiverQualificationReport"
        | "CustomerBedrockQualificationReport"
        | "AiPriceCatalogQualificationReport";
      qualificationBoundary:
        | "local-candidate"
        | "local-runtime"
        | "customer-deployment"
        | "customer-receiver"
        | "live-provider"
        | "production-pricing";
      status: "passed" | "missing" | "rejected";
      reportId?: string;
      reportDigest?: Sha256Digest;
      observedStatus?: string;
      sourceRevision?: string;
      errorCode?: `customer-ai-finops-prerequisite.${string}`;
    }>;
    checks: Array<
      | { id: CustomerAiFinopsPrerequisiteCheckId; status: "passed" }
      | {
          id: CustomerAiFinopsPrerequisiteCheckId;
          status: "failed";
          errorCode: `customer-ai-finops-prerequisite.${string}`;
        }
    >;
    limitations: [
      "same-live-invocation-end-to-end-path-not-qualified",
      "live-bedrock-to-customer-collector-delivery-not-qualified",
      "deployed-price-catalog-secret-binding-not-qualified",
      "customer-long-running-collector-configuration-not-qualified",
      "non-prometheus-dashboard-query-portability-not-qualified",
      "invoice-private-rates-discounts-and-commitments-not-qualified",
      "sustained-load-node-zone-region-and-backend-ha-not-qualified",
    ];
    summary: {
      requiredEvidence: 6;
      passedEvidence: number;
      missingEvidence: number;
      rejectedEvidence: number;
      totalChecks: 16;
      passedChecks: number;
      failedChecks: number;
      overallStatus: "prerequisites-ready" | "not-ready";
    };
  };
}

export interface CustomerAiFinopsFlowQualificationProfile {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerAiFinopsFlowQualificationProfile";
  metadata: {
    name: string;
    environmentId: string;
    reviewedAt: string;
  };
  spec: {
    release: {
      applicationVersion: string;
      chartVersion: string;
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    prerequisites: {
      profileDigest: Sha256Digest;
      reportDigest: Sha256Digest;
    };
    bedrock: { profileDigest: Sha256Digest };
    collection: {
      provider: "aws.bedrock";
      operation: "ConverseStream";
      requestPath: "direct-to-provider";
      telemetryPath: "asynchronous-otel";
      contentPolicy: "metadata-only";
      qualificationServiceName: "iip-bedrock-compatibility";
    };
    attribution: { applicationId: string; teamId: string };
    targets: {
      controlPlaneBaseUrl: string;
      otlpTracesEndpoint: string;
      prometheusBaseUrl: string;
      grafanaBaseUrl: string;
    };
    presentation: {
      telemetryBackend: "prometheus";
      dashboard: "grafana";
      dashboardUid: "iip-ai-finops";
    };
    objective: {
      maximumProfileAgeSeconds: number;
      maximumPrerequisiteAgeSeconds: number;
      maximumClockSkewSeconds: number;
      maximumEndToEndLatencyMilliseconds: number;
      pollIntervalMilliseconds: number;
      requestTimeoutSeconds: number;
      reportValiditySeconds: number;
    };
  };
}

export type CustomerAiFinopsFlowQualificationCheckId =
  | "source-binding"
  | "profile-review"
  | "prerequisite-binding"
  | "bedrock-profile-binding"
  | "protected-inputs"
  | "metadata-only-direct-request-path"
  | "live-provider-call"
  | "otlp-delivery-correlation"
  | "exact-usage-record"
  | "active-attribution"
  | "active-pricing"
  | "bounded-processing-latency"
  | "prometheus-aggregate-delta"
  | "grafana-dashboard"
  | "minimized-output";

export interface CustomerAiFinopsFlowQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerAiFinopsFlowQualificationReport";
  metadata: {
    id: `caff_${string}`;
    generatedAt: string;
    validUntil: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "qualified" | "not-qualified";
    qualificationLevel: "customer-ai-finops-flow-v1";
    qualificationBoundary: "same-invocation-customer-runtime";
    subject: {
      deploymentProfile: "production-ai-finops-v0" | "production-ai-finops-v1";
      applicationVersion: string;
      chartVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    bindings: {
      profileDigest: Sha256Digest;
      environmentBindingDigest: Sha256Digest;
      prerequisiteProfileDigest: Sha256Digest;
      prerequisiteReportDigest: Sha256Digest;
      bedrockProfileDigest: Sha256Digest;
      controlPlaneTargetDigest: Sha256Digest;
      otlpTargetDigest: Sha256Digest;
      prometheusTargetDigest: Sha256Digest;
      grafanaTargetDigest: Sha256Digest;
      runEvidenceDigest: Sha256Digest;
      liveCompatibilityReportDigest: Sha256Digest;
      invocationObservationDigest: Sha256Digest;
      correlationDigest: Sha256Digest;
      usageRecordDigest: Sha256Digest;
      attributionRecordDigest: Sha256Digest;
      activeAttributionPolicyDocumentDigest: Sha256Digest;
      costRecordDigest: Sha256Digest;
      activePriceCatalogDocumentDigest: Sha256Digest;
    };
    profile: {
      provider: "aws.bedrock";
      operation: "ConverseStream";
      requestPath: "direct-to-provider";
      telemetryPath: "asynchronous-otel";
      contentPolicy: "metadata-only";
      costBasis: "calculated-estimate";
      telemetryBackend: "prometheus";
      dashboard: "grafana";
      correlationMode: "protected-request-digest-response";
    };
    measurements: {
      profileReviewedAt: string;
      startedAt: string;
      completedAt: string;
      endToEndLatencyMilliseconds: number;
      observationPolls: number;
      providerCallCount: 1;
      usageRecordCount: 1;
      attributionRecordCount: 1;
      costRecordCount: 1;
      prometheusRequestDelta: number;
      dashboardPanelCount: number;
    };
    results: {
      providerCall: "completed";
      otlpDelivery: "correlated";
      usage: "recorded";
      attribution: "allocated";
      cost: "priced-calculated-estimate";
      telemetryAggregate: "observed";
      dashboard: "provisioned";
    };
    checks: Array<
      | { id: CustomerAiFinopsFlowQualificationCheckId; status: "passed" }
      | {
          id: CustomerAiFinopsFlowQualificationCheckId;
          status: "failed";
          errorCode: `customer-ai-finops-flow.${string}`;
        }
    >;
    limitations: [
      "dashboard-proof-is-protected-dimension-aggregate-not-trace-labelled",
      "invoice-private-rates-discounts-and-commitments-not-qualified",
      "customer-long-running-collector-and-backend-lifecycle-not-qualified",
      "sustained-load-node-zone-region-and-backend-ha-not-qualified",
      "additional-models-regions-operations-providers-and-backends-not-qualified",
    ];
    summary: {
      totalChecks: 15;
      passedChecks: number;
      failedChecks: number;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export interface CustomerFailureOverlapProfile {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerFailureOverlapProfile";
  metadata: {
    id: `cfop_${string}`;
    reviewedAt: string;
    validUntil: string;
  };
  spec: {
    qualificationLevel: "customer-private-pilot-failure-overlap-v1";
    release: CustomerSustainedWorkloadProfile["spec"]["release"];
    bindings: {
      deploymentReportDigest: Sha256Digest;
      sustainedWorkloadProfileDigest: Sha256Digest;
      clusterBindingDigest: Sha256Digest;
      kubernetesContextBindingDigest: Sha256Digest;
      namespaceBindingDigest: Sha256Digest;
      apiTargetBindingDigest: Sha256Digest;
      otlpTargetBindingDigest: Sha256Digest;
      databaseTargetBindingDigest: Sha256Digest;
      processingProfileDigest: Sha256Digest;
      postgresqlProfileDigest: Sha256Digest;
    };
    review: {
      basis: "customer-approved-private-pilot-core-proxy";
      approvalRecordDigest: Sha256Digest;
      approvedScenarios: [
        "api-pod-eviction",
        "workflow-worker-pod-eviction",
        "otlp-receiver-pod-eviction",
        "postgresql-primary-promotion",
      ];
      dataHandlingReviewed: true;
      recoveryObjectivesReviewed: true;
    };
    objective: {
      minimumPreFailureLoadSeconds: number;
      minimumPostFailureLoadSeconds: number;
      maximumProfileAgeSeconds: number;
      maximumEvidenceAgeSeconds: number;
      maximumClockSkewSeconds: number;
      reportValiditySeconds: number;
    };
  };
}

export type CustomerFailureOverlapEvidenceId =
  | "customer-deployment"
  | "sustained-core-workload"
  | "control-plane-continuity"
  | "worker-receiver-continuity"
  | "postgresql-primary-promotion";

export type CustomerFailureOverlapCheckId =
  | "source-binding"
  | "profile-review"
  | "customer-approval"
  | "evidence-freshness"
  | "exact-release-identity"
  | "customer-deployment"
  | "sustained-core-workload"
  | "control-plane-continuity"
  | "worker-receiver-continuity"
  | "postgresql-primary-promotion"
  | "deployment-source-binding"
  | "sustained-profile-binding"
  | "api-target-chain"
  | "otlp-target-chain"
  | "kubernetes-environment-chain"
  | "database-target-chain"
  | "post-deployment-window"
  | "sustained-failure-enclosure"
  | "pre-failure-load-window"
  | "post-failure-load-window"
  | "minimized-output";

export interface CustomerFailureOverlapQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerFailureOverlapQualificationReport";
  metadata: {
    id: `cfoq_${string}`;
    generatedAt: string;
    validUntil: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    qualificationLevel: "customer-private-pilot-failure-overlap-v1";
    qualificationBoundary: "customer-environment-planned-failure-overlap";
    status: "qualified" | "not-qualified";
    subject: CustomerFailureOverlapProfile["spec"]["release"];
    environment: {
      correlationMode: "source-report-window-containment-v1";
      failureMode: "operator-coordinated-planned";
      reviewAuthority: "protected-customer-profile";
    };
    bindings: CustomerFailureOverlapProfile["spec"]["bindings"] & {
      profileDigest: Sha256Digest;
      approvalRecordDigest: Sha256Digest;
      sustainedWorkloadReportDigest: Sha256Digest;
      controlPlaneContinuityReportDigest: Sha256Digest;
      processingContinuityReportDigest: Sha256Digest;
      postgresqlContinuityReportDigest: Sha256Digest;
    };
    objective: CustomerFailureOverlapProfile["spec"]["objective"];
    evidence: Array<{
      id: CustomerFailureOverlapEvidenceId;
      contractKind:
        | "CustomerDeploymentQualificationReport"
        | "CustomerSustainedWorkloadQualificationReport"
        | "CustomerContinuityQualificationReport"
        | "CustomerProcessingContinuityQualificationReport"
        | "CustomerPostgreSQLContinuityQualificationReport";
      qualificationBoundary:
        | "customer-deployment"
        | "customer-environment-sustained-workload"
        | "customer-control-plane-continuity"
        | "customer-worker-receiver-processing-continuity"
        | "customer-postgresql-primary-promotion";
      reportId: string;
      reportDigest: Sha256Digest;
      generatedAt: string;
      validUntil: string | null;
      ageSeconds: number;
      observedStatus: "qualified" | "not-qualified";
    } & (
      | { status: "passed"; errorCode?: never }
      | {
          status: "failed";
          errorCode: `customer-failure-overlap.${string}`;
        }
    )>;
    measurements: {
      profileReviewedAt: string;
      sustainedStartedAt: string;
      sustainedCompletedAt: string;
      firstFailureQualificationStartedAt: string;
      lastFailureQualificationCompletedAt: string;
      preFailureLoadSeconds: number;
      postFailureLoadSeconds: number;
      qualificationWindows: Array<{
        id:
          | "control-plane-continuity"
          | "worker-receiver-continuity"
          | "postgresql-primary-promotion";
        startedAt: string;
        completedAt: string;
        durationMilliseconds: number;
      }>;
      assessedAt: string;
      oldestEvidenceAgeSeconds: number;
    };
    checks: Array<
      | { id: CustomerFailureOverlapCheckId; status: "passed" }
      | {
          id: CustomerFailureOverlapCheckId;
          status: "failed";
          errorCode: `customer-failure-overlap.${string}`;
        }
    >;
    limitations: [
      "customer-approved-private-pilot-core-proxy",
      "operator-coordinated-planned-failures",
      "single-cluster-and-selected-targets",
      "automatic-failover-fencing-and-split-brain-not-proven",
      "node-zone-region-and-disaster-recovery-not-qualified",
      "production-volume-long-window-slo-and-partner-acceptance-not-qualified",
    ];
    summary: {
      totalEvidence: 5;
      passedEvidence: number;
      failedEvidence: number;
      totalChecks: 21;
      passedChecks: number;
      failedChecks: number;
      qualificationWindows: 3;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export interface CustomerPilotReadinessProfile {
  apiVersion: "iip.platform/v1alpha2";
  kind: "CustomerPilotReadinessProfile";
  metadata: {
    id: `cprp_${string}`;
    reviewedAt: string;
    validUntil: string;
  };
  spec: {
    qualificationLevel: "customer-ai-finops-design-partner-v2";
    release: {
      applicationVersion: string;
      chartVersion: string;
      sourceRevision: string;
      manifestDigest: Sha256Digest;
      controlPlaneImageDigest: Sha256Digest;
      pluginMediationBridgeImageDigest: Sha256Digest;
    };
    bindings: {
      signaturePolicyDigest: Sha256Digest;
      publicationTargetSetDigest: Sha256Digest;
      clusterBindingDigest: Sha256Digest;
      namespaceBindingDigest: Sha256Digest;
      environmentBindingDigest: Sha256Digest;
      controlPlaneTargetDigest: Sha256Digest;
      otlpTargetDigest: Sha256Digest;
      sustainedWorkloadProfileDigest: Sha256Digest;
      failureOverlapProfileDigest: Sha256Digest;
      operationalAlertProfileDigest: Sha256Digest;
      operationalAlertBindingSetDigest: Sha256Digest;
    };
    objective: {
      maximumProfileAgeSeconds: number;
      maximumFoundationEvidenceAgeSeconds: number;
      maximumCustomerEvidenceAgeSeconds: number;
      maximumClockSkewSeconds: number;
      reportValiditySeconds: number;
    };
  };
}

export type CustomerPilotReadinessEvidenceId =
  | "release-readiness"
  | "registry-publication"
  | "organizational-signatures"
  | "customer-deployment"
  | "control-plane-load"
  | "sustained-core-workload"
  | "customer-failure-overlap"
  | "ai-finops-prerequisites"
  | "same-invocation-ai-finops"
  | "customer-operational-alerts";

export type CustomerPilotReadinessCheckId =
  | "source-binding"
  | "profile-review"
  | "evidence-freshness"
  | "exact-release-identity"
  | "local-release-readiness"
  | "registry-publication"
  | "organizational-signatures"
  | "customer-deployment"
  | "control-plane-load"
  | "sustained-core-workload"
  | "customer-failure-overlap"
  | "ai-finops-prerequisites"
  | "same-invocation-ai-finops"
  | "customer-operational-alerts"
  | "publication-signature-chain"
  | "deployed-image-chain"
  | "customer-environment-chain"
  | "operational-alert-environment-chain"
  | "sustained-workload-environment-chain"
  | "failure-overlap-environment-chain"
  | "post-deployment-load-window"
  | "post-deployment-operational-alert-window"
  | "post-deployment-sustained-workload-window"
  | "minimized-output";

export interface CustomerPilotReadinessReport {
  apiVersion: "iip.platform/v1alpha2";
  kind: "CustomerPilotReadinessReport";
  metadata: {
    id: `cpr_${string}`;
    generatedAt: string;
    validUntil: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "design-partner-candidate" | "not-candidate";
    qualificationLevel: "customer-ai-finops-design-partner-v2";
    qualificationBoundary: "private-design-partner-preflight";
    subject: {
      applicationVersion: string;
      chartVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      sourceRevision: string;
      manifestDigest: Sha256Digest;
      controlPlaneImageDigest: Sha256Digest;
      pluginMediationBridgeImageDigest: Sha256Digest;
    };
    bindings: {
      profileDigest: Sha256Digest;
      releaseReadinessReportDigest: Sha256Digest;
      releasePublicationReportDigest: Sha256Digest;
      releaseSignatureReportDigest: Sha256Digest;
      customerDeploymentReportDigest: Sha256Digest;
      customerOperationalAlertReportDigest: Sha256Digest;
      controlPlaneLoadReportDigest: Sha256Digest;
      sustainedWorkloadReportDigest: Sha256Digest;
      failureOverlapReportDigest: Sha256Digest;
      aiFinopsPrerequisiteReportDigest: Sha256Digest;
      aiFinopsFlowReportDigest: Sha256Digest;
      signaturePolicyDigest: Sha256Digest;
      publicationTargetSetDigest: Sha256Digest;
      customerEnvironmentSetDigest: Sha256Digest;
    };
    objective: {
      maximumProfileAgeSeconds: number;
      maximumFoundationEvidenceAgeSeconds: number;
      maximumCustomerEvidenceAgeSeconds: number;
      maximumClockSkewSeconds: number;
      reportValiditySeconds: number;
    };
    measurements: {
      profileReviewedAt: string;
      oldestFoundationEvidenceAt: string;
      newestFoundationEvidenceAt: string;
      oldestCustomerEvidenceAt: string;
      customerDeploymentQualifiedAt: string;
      operationalAlertStartedAt: string;
      operationalAlertQualifiedAt: string;
      controlPlaneLoadStartedAt: string;
      controlPlaneLoadCompletedAt: string;
      sustainedWorkloadStartedAt: string;
      sustainedWorkloadCompletedAt: string;
      failureOverlapQualifiedAt: string;
      aiFinopsFlowCompletedAt: string;
      assessedAt: string;
      oldestFoundationEvidenceAgeSeconds: number;
      oldestCustomerEvidenceAgeSeconds: number;
    };
    evidence: Array<{
      id: CustomerPilotReadinessEvidenceId;
      contractKind:
        | "ReleaseReadinessReport"
        | "ReleasePublicationReport"
        | "ReleaseSignatureVerificationReport"
        | "CustomerDeploymentQualificationReport"
        | "CustomerOperationalAlertQualificationReport"
        | "ControlPlaneLoadQualificationReport"
        | "CustomerSustainedWorkloadQualificationReport"
        | "CustomerFailureOverlapQualificationReport"
        | "CustomerAiFinopsPrerequisiteReport"
        | "CustomerAiFinopsFlowQualificationReport";
      qualificationBoundary:
        | "local-candidate"
        | "registry-publication"
        | "organizational-trust"
        | "customer-deployment"
        | "customer-rule-evaluation-and-notification-route"
        | "customer-load"
        | "customer-environment-sustained-workload"
        | "customer-environment-planned-failure-overlap"
        | "ai-finops-prerequisites"
        | "same-invocation-customer-runtime";
      reportId: string;
      reportDigest: Sha256Digest;
      observedStatus: string;
    } & (
      | { status: "passed"; errorCode?: never }
      | {
          status: "rejected";
          errorCode: `customer-pilot-readiness.${string}`;
        }
    )>;
    checks: Array<
      | { id: CustomerPilotReadinessCheckId; status: "passed" }
      | {
          id: CustomerPilotReadinessCheckId;
          status: "failed";
          errorCode: `customer-pilot-readiness.${string}`;
        }
    >;
    limitations: [
      "private-design-partner-evaluation-only",
      "design-partner-operation-and-acceptance-not-qualified",
      "public-license-legal-brand-and-governance-not-qualified",
      "invoice-private-rates-discounts-and-commitments-not-qualified",
      "customer-approved-private-pilot-core-proxy-not-production-representativeness",
      "node-zone-region-and-long-window-slo-not-qualified",
      "additional-integrations-models-providers-and-backends-not-qualified",
    ];
    externalGates: [
      {
        id: "design-partner-operation-and-acceptance";
        status: "external-required";
        reasonCode: "customer-pilot-readiness.external.design-partner";
      },
      {
        id: "customer-production-operating-qualification";
        status: "external-required";
        reasonCode: "customer-pilot-readiness.external.production-operations";
      },
      {
        id: "public-license-legal-brand-and-governance";
        status: "external-required";
        reasonCode: "customer-pilot-readiness.external.public-governance";
      },
    ];
    summary: {
      requiredEvidence: 10;
      passedEvidence: number;
      rejectedEvidence: number;
      totalChecks: 24;
      passedChecks: number;
      failedChecks: number;
      externalGateCount: 3;
      overallStatus: "design-partner-candidate" | "not-candidate";
    };
  };
}

export interface CustomerOtlpReceiverQualificationProfile {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerOtlpReceiverQualificationProfile";
  metadata: { name: string; reviewedAt: string };
  spec: {
    receiverEndpoint: string;
    collector: {
      distribution: "opentelemetry-collector-contrib";
      version: "0.158.0";
      image: "otel/opentelemetry-collector-contrib@sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5";
    };
    signals: {
      metrics: { serviceName: string; metricName: string; unit: string };
      logs: { serviceName: string; deploymentEnvironment: string };
      genAiTraces: {
        serviceName: string;
        serviceNamespace: string;
        deploymentEnvironment: string;
        provider: "aws.bedrock" | "openai";
        operation: "chat" | "text_completion";
        requestModel: string;
        responseModel: string;
        region: string;
        instrumentationScope: string;
        semanticConventionVersion: string;
      };
    };
    objective: {
      requestTimeoutSeconds: number;
      deliveryTimeoutSeconds: number;
      pollIntervalMilliseconds: number;
      maximumDeliveryLatencyMilliseconds: number;
      maximumProfileAgeSeconds: number;
    };
  };
}

export type CustomerOtlpReceiverQualificationCheckId =
  | "profile-binding"
  | "source-binding"
  | "immutable-release"
  | "api-runtime-identity"
  | "receiver-endpoint-binding"
  | "protected-input-files"
  | "collector-image-pinned"
  | "collector-config-validation"
  | "verified-tls"
  | "client-certificate-authentication"
  | "separate-channel-credentials"
  | "direct-no-proxy-no-redirect"
  | "metrics-delivery"
  | "logs-delivery"
  | "metadata-only-genai-delivery"
  | "receiver-durable-acknowledgement"
  | "collector-zero-send-failure"
  | "collector-queue-drained"
  | "delivery-latency-objective"
  | "minimized-output";

export interface CustomerOtlpReceiverQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerOtlpReceiverQualificationReport";
  metadata: {
    id: `corq_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "qualified" | "not-qualified";
    qualification: "customer-pinned-collector-to-iip-receiver-v1";
    subject: {
      applicationVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      sourceRevision: string;
      chartVersion: string;
      requiredMigration: `${number}_${string}.sql`;
      imageDigest: Sha256Digest;
    };
    bindings: {
      apiTargetBindingDigest: Sha256Digest;
      receiverEndpointBindingDigest: Sha256Digest;
      profileDigest: Sha256Digest;
      signalSetDigest: Sha256Digest;
      apiCaBundleDigest: Sha256Digest;
      receiverCaBundleDigest: Sha256Digest;
      clientCertificateDigest: Sha256Digest;
    };
    profile: {
      name: "customer-pinned-collector-to-iip-receiver-v1";
      collectorDistribution: "opentelemetry-collector-contrib";
      collectorVersion: "0.158.0";
      collectorImageDigest: "sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5";
      inputTransport: "loopback-otlp-http-protobuf";
      outputTransport: "ca-verified-https-otlp-http-protobuf";
      workloadIdentity: "x509-client-certificate";
      channelCredential: "separate-per-signal-bearer";
      queueMode: "file-storage-persistent-sending-queue";
      redirectMode: "denied";
      proxyMode: "disabled";
    };
    objective: CustomerOtlpReceiverQualificationProfile["spec"]["objective"];
    measurements: {
      profileReviewedAt: string;
      startedAt: string;
      completedAt: string;
      signalCount: 3;
      collectorSubmittedItemCount: 3;
      directReceiverAcceptedPathCount: number;
      collectorAcceptedItemCount: number;
      receiverDeliveredItemCount: number;
      collectorSendFailureCount: number;
      finalQueueItemCount: number;
      maximumDeliveryLatencyMilliseconds: number;
    };
    checks: Array<
      | { id: CustomerOtlpReceiverQualificationCheckId; status: "passed" }
      | {
          id: CustomerOtlpReceiverQualificationCheckId;
          status: "failed";
          errorCode: `customer-otlp-receiver-qualification.${string}`;
        }
    >;
    limitations: [
      "customer-long-running-collector-configuration-not-qualified",
      "application-telemetry-fail-open-behavior-not-qualified",
      "sustained-throughput-queue-capacity-and-disk-recovery-not-qualified",
      "customer-pki-rotation-revocation-crl-distribution-and-ocsp-not-qualified",
      "node-zone-region-and-multi-collector-failure-not-qualified",
    ];
    summary: {
      totalChecks: 20;
      passedChecks: number;
      failedChecks: number;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export interface CustomerPolicyQualificationProfile {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerPolicyQualificationProfile";
  metadata: { name: string; reviewedAt: string };
  spec: {
    endpoint: string;
    identity: { tenantId: string; actorId: string; roles: string[] };
    cases: Array<{
      id: string;
      action: `${string}:${string}`;
      resource: { tenantId: string } & Record<string, unknown>;
      expected: {
        allowed: boolean;
        reasonCode: string;
        policySnapshotRef: `policy://${string}/snapshots/${string}`;
      };
    }>;
    objective: {
      requestTimeoutSeconds: number;
      maximumResponseBytes: number;
      maximumDecisionLatencyMilliseconds: number;
      maximumProfileAgeSeconds: number;
    };
  };
}

export type CustomerPolicyQualificationCheckId =
  | "profile-binding"
  | "source-binding"
  | "protected-input-files"
  | "endpoint-binding"
  | "ca-verified-tls"
  | "credential-accepted"
  | "exact-input-digest-binding"
  | "tenant-binding"
  | "snapshot-binding"
  | "allow-cases"
  | "deny-cases"
  | "repeat-consistency"
  | "latency-objective"
  | "minimized-output";

export interface CustomerPolicyQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerPolicyQualificationReport";
  metadata: {
    id: `cpq_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "qualified" | "not-qualified";
    qualification: "customer-policy-engine-bundle-prerequisites-v1";
    subject: {
      applicationVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    bindings: {
      endpointBindingDigest: Sha256Digest;
      profileDigest: Sha256Digest;
      caseSetDigest: Sha256Digest;
      snapshotSetDigest: Sha256Digest;
    };
    profile: {
      name: "customer-external-http-policy-v1";
      transport: "ca-verified-https-json";
      requestWrapper: "opa-input";
      credentialMode: "protected-bearer-file-read-per-request";
      redirectMode: "denied";
      proxyMode: "disabled";
    };
    objective: CustomerPolicyQualificationProfile["spec"]["objective"];
    measurements: {
      profileReviewedAt: string;
      startedAt: string;
      completedAt: string;
      caseCount: number;
      allowCaseCount: number;
      denyCaseCount: number;
      requestCount: number;
      maximumDecisionLatencyMilliseconds: number;
    };
    checks: Array<
      | { id: CustomerPolicyQualificationCheckId; status: "passed" }
      | {
          id: CustomerPolicyQualificationCheckId;
          status: "failed";
          errorCode: `customer-policy-qualification.${string}`;
        }
    >;
    limitations: [
      "policy-engine-ha-failover-and-network-path-not-qualified",
      "credential-rotation-revocation-and-emergency-access-not-qualified",
      "bundle-review-change-control-and-break-glass-not-qualified",
      "audit-delivery-retention-and-siem-integration-not-qualified",
    ];
    summary: {
      totalChecks: 14;
      passedChecks: number;
      failedChecks: number;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export type CustomerProcessingContinuityCheckId =
  | "source-binding"
  | "minimized-output"
  | "explicit-context"
  | "immutable-image"
  | "api-verified-https"
  | "receiver-mutual-tls"
  | "direct-no-proxy-no-redirect"
  | "worker-redundant-capacity"
  | "receiver-redundant-capacity"
  | "zero-unavailable-rollouts"
  | "component-pdbs"
  | "uid-preconditioned-evictions"
  | "worker-reduced-capacity-observed"
  | "receiver-reduced-capacity-observed"
  | "api-zero-failure"
  | "receiver-zero-failure"
  | "receiver-durable-intake"
  | "workflow-baseline-completion"
  | "workflow-worker-disruption-overlap"
  | "workflow-receiver-disruption-overlap"
  | "workflow-recovery-completion"
  | "component-recovery";

export interface CustomerProcessingContinuityQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerProcessingContinuityQualificationReport";
  metadata: {
    id: `cpcq_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "qualified" | "not-qualified";
    qualificationLevel: "customer-worker-receiver-pod-eviction-v1";
    subject: {
      applicationVersion: string;
      chartVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      requiredMigration: `${number}_${string}.sql`;
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    bindings: {
      apiTargetBindingDigest: Sha256Digest;
      otlpTargetBindingDigest: Sha256Digest;
      kubernetesContextBindingDigest: Sha256Digest;
      namespaceBindingDigest: Sha256Digest;
      profileDigest: Sha256Digest;
      components: Array<{
        id: "workflow-worker" | "otlp-receiver";
        deploymentBindingDigest: Sha256Digest;
        evictedPodBindingDigest: Sha256Digest;
      }>;
    };
    objective: {
      minimumProbeAttemptsPerPhase: number;
      probeIntervalMilliseconds: number;
      maximumWorkflowCompletionMilliseconds: number;
      maximumRecoveryMilliseconds: number;
      requestTimeoutMilliseconds: number;
    };
    environment: {
      platform: `${string}/${string}`;
      pythonVersion: string;
      kubernetesVersion: string;
      apiTransport: "verified-https";
      otlpTransport: "mutual-tls-https";
      apiCaSource: "system" | "custom";
      otlpCaSource: "system" | "custom";
      proxyMode: "disabled";
      redirectMode: "deny";
      evictionApi: "policy/v1";
    };
    receiverIntake: {
      signal: "metric";
      payload: "non-empty-otlp-protobuf";
      successBoundary: "postgresql-commit-before-http-200";
    };
    measurements: {
      startedAt: string;
      completedAt: string;
      phases: Array<{
        id: "baseline" | "worker-disruption" | "receiver-disruption" | "recovery";
        apiAttempts: number;
        apiSuccesses: number;
        apiFailures: number;
        receiverAttempts: number;
        receiverSuccesses: number;
        receiverFailures: number;
        workflowSubmitted: 1;
        workflowCompleted: 0 | 1;
        workflowFailures: 0 | 1;
        workflowPollAttempts: number;
        workflowCompletionMilliseconds: number;
        submittedDuringReducedCapacity: boolean;
      }>;
      components: Array<{
        id: "workflow-worker" | "otlp-receiver";
        desiredReplicas: number;
        readyReplicasBefore: number;
        readyReplicasDuring: number;
        readyReplicasAfter: number;
        maxUnavailable: 0;
        pdbMinAvailable: number;
        pdbDisruptionsAllowedBefore: number;
        evictionAccepted: true;
        reducedCapacityObserved: true;
        originalPodReplaced: true;
        recoveryMilliseconds: number;
      }>;
    };
    checks: Array<
      | { id: CustomerProcessingContinuityCheckId; status: "passed" }
      | {
          id: CustomerProcessingContinuityCheckId;
          status: "failed";
          errorCode: `customer-processing-continuity.${string}`;
        }
    >;
    limitations: [
      "single-cluster",
      "sequential-single-pod-evictions",
      "synthetic-qualification-traffic",
      "shared-database-failure-not-qualified",
      "node-zone-region-failure-not-qualified",
      "sustained-customer-load-not-qualified",
    ];
    summary: {
      totalChecks: 22;
      passedChecks: number;
      failedChecks: number;
      totalApiAttempts: number;
      failedApiAttempts: number;
      totalReceiverAttempts: number;
      failedReceiverAttempts: number;
      totalWorkflowSubmissions: 4;
      completedWorkflows: number;
      failedWorkflows: number;
      maximumWorkflowCompletionMilliseconds: number;
      maximumRecoveryMilliseconds: number;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export interface CustomerSustainedWorkloadProfile {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerSustainedWorkloadProfile";
  metadata: {
    id: `cswp_${string}`;
    tenantId: string;
    actorId: string;
    reviewedAt: string;
    validUntil: string;
  };
  spec: {
    qualificationLevel: "customer-sustained-core-workload-v1";
    release: {
      applicationVersion: string;
      chartVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      requiredMigration: `${number}_${string}.sql`;
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    targets: { apiBaseUrl: string; otlpBaseUrl: string };
    resourceUid: `res_${string}`;
    metric: { name: string; unit: string; serviceName: string };
    investigation: CustomerProcessingQualificationProfile["spec"]["investigation"];
    objective: {
      durationSeconds: number;
      probeCyclesPerSecond: number;
      probeConcurrency: number;
      workflowSubmissionsPerMinute: number;
      workflowConcurrency: number;
      minimumApiSuccessBasisPoints: number;
      minimumReceiverSuccessBasisPoints: number;
      minimumWorkflowCompletionBasisPoints: number;
      maximumProbeSchedulerMissBasisPoints: number;
      maximumWorkflowSchedulerMissBasisPoints: number;
      maximumApiP95LatencyMilliseconds: number;
      maximumApiP99LatencyMilliseconds: number;
      maximumReceiverP95LatencyMilliseconds: number;
      maximumReceiverP99LatencyMilliseconds: number;
      maximumWorkflowP95CompletionMilliseconds: number;
      maximumWorkflowP99CompletionMilliseconds: number;
      requestTimeoutMilliseconds: number;
      maximumSchedulerLagMilliseconds: number;
      maximumWorkflowCompletionMilliseconds: number;
      maximumProfileAgeSeconds: number;
      reportValiditySeconds: number;
    };
  };
}

export type CustomerSustainedWorkloadCheckId =
  | "source-binding"
  | "profile-review"
  | "explicit-traffic-enable"
  | "direct-no-proxy-no-redirect"
  | "api-verified-https"
  | "receiver-mutual-tls"
  | "immutable-release-identity"
  | "bounded-workload-volume"
  | "observation-window"
  | "probe-scheduler-attainment"
  | "workflow-scheduler-attainment"
  | "api-success-attainment"
  | "api-p95-latency"
  | "api-p99-latency"
  | "receiver-success-attainment"
  | "receiver-p95-latency"
  | "receiver-p99-latency"
  | "receiver-durable-intake"
  | "workflow-completion-attainment"
  | "workflow-p95-completion"
  | "workflow-p99-completion"
  | "minimized-output";

export interface CustomerSustainedWorkloadQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerSustainedWorkloadQualificationReport";
  metadata: {
    id: `cswq_${string}`;
    generatedAt: string;
    validUntil: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "qualified" | "not-qualified";
    qualificationLevel: "customer-sustained-core-workload-v1";
    qualificationBoundary: "customer-environment-sustained-workload";
    subject: CustomerSustainedWorkloadProfile["spec"]["release"];
    bindings: {
      profileDigest: Sha256Digest;
      apiTargetBindingDigest: Sha256Digest;
      otlpTargetBindingDigest: Sha256Digest;
    };
    objective: CustomerSustainedWorkloadProfile["spec"]["objective"];
    environment: {
      platform: `${string}/${string}`;
      pythonVersion: string;
      apiTransport: "verified-https";
      otlpTransport: "mutual-tls-https";
      apiCaSource: "system" | "custom";
      otlpCaSource: "system" | "custom";
      proxyMode: "disabled";
      redirectMode: "deny";
      connectionMode: "close-per-request";
      scheduler: "bounded-dual-fixed-rate-v1";
      receiverSuccessBoundary: "postgresql-commit-before-http-200";
    };
    measurements: {
      profileReviewedAt: string;
      startedAt: string;
      completedAt: string;
      actualDurationMilliseconds: number;
      probes: {
        scheduledCycles: number;
        attemptedCycles: number;
        schedulerMissedCycles: number;
        schedulerMissBasisPoints: number;
        schedulerLagP95Milliseconds: number;
        api: CustomerSustainedWorkloadPathMeasurements;
        receiver: CustomerSustainedWorkloadPathMeasurements;
      };
      workflows: {
        scheduledWorkflows: number;
        attemptedSubmissions: number;
        acceptedSubmissions: number;
        submissionFailures: number;
        completedWorkflows: number;
        terminalFailures: number;
        schedulerMissedWorkflows: number;
        completionBasisPoints: number;
        schedulerMissBasisPoints: number;
        schedulerLagP95Milliseconds: number;
        pollAttempts: number;
        completionLatency: CustomerSustainedWorkloadLatency;
      };
    };
    checks: Array<
      | { id: CustomerSustainedWorkloadCheckId; status: "passed" }
      | {
          id: CustomerSustainedWorkloadCheckId;
          status: "failed";
          errorCode: `customer-sustained-workload.${string}`;
        }
    >;
    limitations: [
      "bounded-synthetic-core-workload",
      "single-tenant-resource-and-target-pair",
      "api-identity-read-otlp-metric-and-investigation-only",
      "no-provider-call-or-inference-path",
      "no-failure-injection-or-database-failover",
      "node-zone-region-and-long-window-slo-not-qualified",
      "customer-workload-representativeness-not-approved",
    ];
    summary: {
      totalChecks: 22;
      passedChecks: number;
      failedChecks: number;
      scheduledProbeCycles: number;
      successfulApiRequests: number;
      successfulReceiverWrites: number;
      scheduledWorkflows: number;
      completedWorkflows: number;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export interface CustomerSustainedWorkloadLatency {
  p50Milliseconds: number | null;
  p95Milliseconds: number | null;
  p99Milliseconds: number | null;
  maximumMilliseconds: number | null;
}

export interface CustomerSustainedWorkloadPathMeasurements {
  attempts: number;
  successes: number;
  failures: number;
  successBasisPoints: number;
  latency: CustomerSustainedWorkloadLatency;
}

export type CustomerDeploymentQualificationEvidenceId =
  | "live-install-preflight"
  | "post-install-health"
  | "customer-ingress"
  | "customer-oidc"
  | "customer-policy"
  | "customer-credential-broker"
  | "customer-otlp-receiver"
  | "control-plane-continuity"
  | "worker-receiver-processing"
  | "postgresql-primary-promotion";

export type CustomerDeploymentQualificationCheckId =
  | "source-binding"
  | "profile-binding"
  | "exact-release-identity"
  | "explicit-current-cluster"
  | "live-install-preflight"
  | "post-continuity-health"
  | "customer-ingress"
  | "customer-oidc"
  | "customer-policy"
  | "customer-credential-broker"
  | "customer-otlp-receiver"
  | "control-plane-continuity"
  | "worker-receiver-processing"
  | "postgresql-primary-promotion"
  | "continuity-ingress-chain"
  | "oidc-target-chain"
  | "policy-binding-chain"
  | "credential-broker-binding-chain"
  | "otlp-receiver-binding-chain"
  | "processing-target-chain"
  | "database-target-chain"
  | "evidence-order"
  | "evidence-freshness"
  | "minimized-output";

export interface CustomerDeploymentQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CustomerDeploymentQualificationReport";
  metadata: {
    id: `cdq_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "qualified" | "not-qualified";
    qualificationLevel: "single-cluster-database-identity-policy-broker-receiver-prerequisites-v7";
    subject: {
      profile: CustomerDeploymentPreflightProfileName;
      applicationVersion: string;
      chartVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      requiredMigration: `${number}_${string}.sql`;
      sourceRevision: string;
      imageDigest: Sha256Digest;
    };
    bindings: {
      clusterBindingDigest: Sha256Digest;
      namespaceBindingDigest: Sha256Digest;
      releaseBindingDigest: Sha256Digest;
      deploymentBindingDigest: Sha256Digest;
      continuityTargetBindingDigest: Sha256Digest;
      oidcProfileDigest: Sha256Digest;
      oidcIssuerMetadataDigest: Sha256Digest;
      policyEndpointBindingDigest: Sha256Digest;
      policyProfileDigest: Sha256Digest;
      policySnapshotSetDigest: Sha256Digest;
      credentialBrokerEndpointBindingDigest: Sha256Digest;
      credentialBrokerProfileDigest: Sha256Digest;
      credentialBrokerAuthoritySetDigest: Sha256Digest;
      credentialBrokerCaBundleDigest: Sha256Digest;
      otlpReceiverApiTargetBindingDigest: Sha256Digest;
      otlpReceiverEndpointBindingDigest: Sha256Digest;
      otlpReceiverProfileDigest: Sha256Digest;
      otlpReceiverSignalSetDigest: Sha256Digest;
      otlpReceiverApiCaBundleDigest: Sha256Digest;
      otlpReceiverCaBundleDigest: Sha256Digest;
      otlpReceiverClientCertificateDigest: Sha256Digest;
      processingOtlpTargetBindingDigest: Sha256Digest;
      processingProfileDigest: Sha256Digest;
      workerDeploymentBindingDigest: Sha256Digest;
      receiverDeploymentBindingDigest: Sha256Digest;
      databaseTargetBindingDigest: Sha256Digest;
      databaseProfileDigest: Sha256Digest;
    };
    objective: {
      maximumEvidenceAgeSeconds: number;
      maximumClockSkewSeconds: number;
      requirePostContinuityHealth: true;
    };
    measurements: {
      preflightGeneratedAt: string;
      oidcStartedAt: string;
      oidcCompletedAt: string;
      policyStartedAt: string;
      policyCompletedAt: string;
      credentialBrokerStartedAt: string;
      credentialBrokerCompletedAt: string;
      otlpReceiverStartedAt: string;
      otlpReceiverCompletedAt: string;
      continuityStartedAt: string;
      continuityCompletedAt: string;
      processingStartedAt: string;
      processingCompletedAt: string;
      databaseContinuityStartedAt: string;
      databaseContinuityCompletedAt: string;
      postContinuityHealthObservedAt: string;
      qualifiedAt: string;
      oldestEvidenceAgeSeconds: number;
    };
    evidence: Array<{
      id: CustomerDeploymentQualificationEvidenceId;
      contractKind:
        | "CustomerDeploymentPreflightReport"
        | "DeploymentDiagnosticReport"
        | "IngressAvailabilityQualificationReport"
        | "CustomerOidcQualificationReport"
        | "CustomerPolicyQualificationReport"
        | "CustomerCredentialBrokerQualificationReport"
        | "CustomerOtlpReceiverQualificationReport"
        | "CustomerContinuityQualificationReport"
        | "CustomerProcessingContinuityQualificationReport"
        | "CustomerPostgreSQLContinuityQualificationReport";
      reportId: string;
      reportDigest: Sha256Digest;
      observedStatus:
        | "blocked"
        | "configuration-ready"
        | "install-ready"
        | "healthy"
        | "attention-required"
        | "qualified"
        | "not-qualified";
      status: "passed" | "rejected";
      errorCode?: `customer-deployment-qualification.${string}`;
    }>;
    checks: Array<
      | { id: CustomerDeploymentQualificationCheckId; status: "passed" }
      | {
          id: CustomerDeploymentQualificationCheckId;
          status: "failed";
          errorCode: `customer-deployment-qualification.${string}`;
        }
    >;
    limitations: [
      "single-customer-cluster",
      "planned-sequential-api-worker-receiver-pod-disruptions",
      "point-in-time-dependency-observation",
      "artifact-publication-signatures-vulnerabilities-not-qualified",
      "database-topology-fencing-and-rpo-not-qualified",
      "regional-database-disaster-recovery-not-qualified",
      "customer-oidc-interactive-policy-lifecycle-other-integrations-and-live-ai-not-qualified",
      "regional-slo-and-capacity-not-qualified",
      "design-partner-legal-brand-governance-not-qualified",
    ];
    summary: {
      requiredEvidence: 10;
      passedEvidence: number;
      rejectedEvidence: number;
      totalChecks: 24;
      passedChecks: number;
      failedChecks: number;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export type ControlPlaneLoadQualificationCheckId =
  | "source-binding"
  | "minimized-output"
  | "explicit-traffic-enable"
  | "direct-no-redirect-client"
  | "verified-https"
  | "exact-release-identity"
  | "bounded-request-volume"
  | "observation-window"
  | "scheduler-attainment"
  | "successful-request-attainment"
  | "p95-latency-objective"
  | "p99-latency-objective";

export interface ControlPlaneLoadQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "ControlPlaneLoadQualificationReport";
  metadata: {
    id: `clq_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "qualified" | "not-qualified";
    qualificationLevel: "customer-control-plane-read-load-v1";
    targetBindingDigest: Sha256Digest;
    targetIdentity: {
      applicationVersion: string;
      contractsApiVersion: "iip.platform/v1alpha1";
      requiredMigration: `${number}_${string}.sql`;
      buildMode: "release";
      sourceRevision: string;
      chartVersion: string;
      imageDigest: Sha256Digest;
    };
    objective: {
      durationSeconds: number;
      targetRequestsPerSecond: number;
      concurrency: number;
      minimumSuccessfulRequestBasisPoints: number;
      maximumSchedulerMissBasisPoints: number;
      maximumP95LatencyMilliseconds: number;
      maximumP99LatencyMilliseconds: number;
      requestTimeoutMilliseconds: number;
      maximumSchedulerLagMilliseconds: number;
    };
    environment: {
      platform: `${string}/${string}`;
      pythonVersion: string;
      transport: "verified-https";
      caSource: "system" | "custom";
      proxyMode: "disabled";
      redirectMode: "deny";
      connectionMode: "close-per-request";
      scheduler: "bounded-fixed-rate-v1";
    };
    measurements: {
      startedAt: string;
      completedAt: string;
      actualDurationMilliseconds: number;
      targetRequestCount: number;
      attemptedRequests: number;
      successfulRequests: number;
      failedRequests: number;
      schedulerMissedRequests: number;
      successfulRequestBasisPoints: number;
      schedulerMissBasisPoints: number;
      achievedSuccessfulMilliRequestsPerSecond: number;
      schedulerLagP95Milliseconds: number | null;
      requestLatency: {
        p50Milliseconds: number | null;
        p95Milliseconds: number | null;
        p99Milliseconds: number | null;
        maximumMilliseconds: number | null;
      };
      failureCategories: {
        transport: number;
        "http-status": number;
        contract: number;
        identity: number;
      };
    };
    checks: Array<
      | { id: ControlPlaneLoadQualificationCheckId; status: "passed" }
      | {
          id: ControlPlaneLoadQualificationCheckId;
          status: "failed";
          errorCode: `control-plane-load.${string}`;
        }
    >;
    limitations: [
      "authenticated-runtime-identity-read-only",
      "single-endpoint",
      "fixed-rate-synthetic-traffic",
      "single-ingress-path",
      "write-database-worker-receiver-capacity-not-qualified",
      "failure-regional-and-long-window-slo-not-qualified",
    ];
    summary: {
      totalChecks: 12;
      passedChecks: number;
      failedChecks: number;
      overallStatus: "qualified" | "not-qualified";
    };
  };
}

export type ReleaseQualificationInstallCheckId =
  | "bundle-integrity"
  | "source-identity"
  | "immutable-deployment"
  | "runtime-identity"
  | "schema-migrations"
  | "protected-operations"
  | "tls-ingress"
  | "backup-checksum"
  | "isolated-restore"
  | "helm-upgrade";

export type ReleaseQualificationUpgradeCheckId =
  | "bundle-integrity"
  | "source-identity"
  | "strict-ancestry"
  | "base-runtime-identity"
  | "target-runtime-identity"
  | "tenant-data-preservation"
  | "non-regressing-migration"
  | "forward-schema-rollback"
  | "idempotent-reupgrade"
  | "zero-failure-service-availability"
  | "in-flight-request-drain"
  | "helm-history";

export interface ReleaseQualificationRuntimeIdentity {
  version: string;
  chartVersion: string;
  revision: string;
  imageDigest: Sha256Digest;
}

export interface ReleaseQualificationReport {
  apiVersion: "iip.dev/v1alpha1";
  kind: "ReleaseQualificationReport";
  metadata: {
    id: `rqr_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "incomplete" | "qualified";
    candidate: {
      version: string;
      chartVersion: string;
      revision: string;
      releaseManifestDigest: Sha256Digest;
      controlPlaneImageDigest: Sha256Digest;
      platforms: `linux/${string}`[];
      signatureStatus: "unsigned" | "signed";
    };
    environment: {
      profile: "local-kind";
      platform: `linux/${string}`;
      kubernetesVersion: string;
      containerRuntime: { name: "docker"; version: string };
    };
    profiles: Array<
      | {
          name: "packaged-install";
          observedAt: string;
          result: "passed";
          checks: Array<{
            id: ReleaseQualificationInstallCheckId;
            status: "passed";
          }>;
          measurement: {
            runtime: ReleaseQualificationRuntimeIdentity;
            requiredMigration: string;
            appliedMigrationCount: number;
            finalHelmRevision: 2;
            backupChecksumVerified: true;
            isolatedRestoreVerified: true;
          };
        }
      | {
          name: "n-minus-one-upgrade";
          observedAt: string;
          result: "passed";
          checks: Array<{
            id: ReleaseQualificationUpgradeCheckId;
            status: "passed";
          }>;
          measurement: {
            base: ReleaseQualificationRuntimeIdentity & { migration: string };
            target: ReleaseQualificationRuntimeIdentity & { migration: string };
            migrationRelation: "retained" | "advanced";
            appliedMigrationCount: number;
            finalHelmRevision: 4;
            availability: {
              attemptCount: number;
              requestCount: number;
              successCount: number;
              failureCount: 0;
              baseSuccessCount: number;
              targetSuccessCount: number;
            };
            inFlightDrain: {
              blockedReadObserved: true;
              podTerminationRequested: true;
              requestCompleted: true;
            };
          };
        }
    >;
    summary: {
      requiredProfiles: 2;
      passedProfiles: 1 | 2;
      overallStatus: "incomplete" | "qualified";
    };
  };
}

export type ReleaseReadinessEvidenceStatus = "passed" | "missing" | "rejected";

export type ReleaseReadinessExternalGateId =
  | "approved-registry-publication"
  | "organizational-release-signatures"
  | "customer-install-preflight"
  | "customer-ingress-availability"
  | "customer-integration-interoperability"
  | "live-ai-provider-and-price-authority"
  | "design-partner-acceptance"
  | "legal-brand-and-governance";

export interface ReleaseReadinessReport {
  apiVersion: "iip.dev/v1alpha1";
  kind: "ReleaseReadinessReport";
  metadata: {
    id: `rrr_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: false;
  };
  spec: {
    status: "incomplete" | "locally-qualified";
    qualificationBoundary: "local-candidate-only";
    release: {
      version: string;
      chartVersion: string;
      revision: string;
      manifestDigest: Sha256Digest;
      signatureStatus: "unsigned" | "signed";
    };
    evidence: Array<{
      id: string;
      contractKind: string;
      qualificationBoundary:
        | "packaged-local"
        | "security-local"
        | "local-runtime"
        | "local-integration"
        | "offline-provider"
        | "local-recovery"
        | "local-availability"
        | "configuration-only";
      status: ReleaseReadinessEvidenceStatus;
      reportDigest?: Sha256Digest;
      observedStatus?: string;
      sourceRevision?: string;
      errorCode?: `release-readiness.${string}`;
    }>;
    externalGates: Array<{
      id: ReleaseReadinessExternalGateId;
      status: "external-required";
      reasonCode: `release-readiness.external.${string}`;
    }>;
    summary: {
      requiredEvidence: 19;
      passedEvidence: number;
      missingEvidence: number;
      rejectedEvidence: number;
      externalGateCount: 8;
      overallStatus: "incomplete" | "locally-qualified";
    };
  };
}

export type PostgreSQLRecoveryQualificationCheckId =
  | "representative-state"
  | "complete-schema-backup"
  | "source-quiescence"
  | "isolated-restore"
  | "row-integrity"
  | "sequence-integrity"
  | "projection-consistency"
  | "recovery-point-age-objective"
  | "recovery-ready-objective";

export interface PostgreSQLRecoveryQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "PostgreSQLRecoveryQualificationReport";
  metadata: {
    id: `pgr_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: boolean;
  };
  spec: {
    status: "qualified" | "failed";
    environment: {
      profile: "local-docker";
      platform: `${string}/${string}`;
      pythonVersion: string;
      applicationVersion: string;
      containerRuntime: { name: "docker"; version: string };
      database: {
        engine: "postgresql";
        version: string;
        migration: `${number}_${string}.sql`;
        image: `postgres:18.4-alpine@sha256:${string}`;
      };
    };
    profile: {
      name: "quiesced-logical-restore-v1";
      backup: "pg_dump-custom-format";
      restore: "fresh-database-pg_restore";
      workload: "quiesced-representative-workflow";
      scope: "complete-iip-schema";
      objectives: {
        classification: "local-regression-guardrail";
        maximumRecoveryPointAgeMilliseconds: number;
        maximumRecoveryReadyMilliseconds: number;
      };
    };
    measurements: {
      fixture: {
        tenantCount: 1;
        resourceCount: number;
        relationshipCount: number;
        investigationCount: 1;
        governedActionCount: 1;
        pluginSessionCount: 1;
        pluginInvocationCount: 1;
      };
      backup: {
        bytes: number;
        durationMilliseconds: number;
        recoveryPoint: string;
        recoveryPointAgeMilliseconds: number;
        committedRecordLoss: 0;
        sourceStable: true;
      };
      restore: {
        commandDurationMilliseconds: number;
        recoveryReadyMilliseconds: number;
        isolatedDatabase: true;
      };
      integrity: {
        matched: true;
        databaseDigest: Sha256Digest;
        tableCount: number;
        rowCount: number;
        tables: Record<string, { rowCount: number; digest: Sha256Digest }>;
        sequenceCount: number;
        sequenceDigest: Sha256Digest;
        projectionVerification: {
          driftDetected: false;
          resourceCount: number;
          relationshipCount: number;
          latestObservationOffset: number;
          projectionDigest: Sha256Digest;
        };
      };
    };
    checks: Array<{
      id: PostgreSQLRecoveryQualificationCheckId;
      status: "passed" | "failed";
      errorCode?: `postgresql.recovery.${string}`;
    }>;
  };
}

export type PostgreSQLContinuityQualificationCheckId =
  | "representative-state"
  | "physical-base-backups"
  | "streaming-standby"
  | "zero-lag-cutover"
  | "replica-row-integrity"
  | "replica-sequence-safety"
  | "primary-stopped"
  | "standby-promoted"
  | "failover-row-integrity"
  | "failover-sequence-safety"
  | "failover-projection-consistency"
  | "wal-archive"
  | "named-restore-target"
  | "pitr-boundary"
  | "pitr-row-integrity"
  | "pitr-sequence-safety"
  | "pitr-projection-consistency"
  | "catchup-objective"
  | "failover-ready-objective"
  | "pitr-ready-objective";

export interface PostgreSQLContinuityIntegrity {
  rowMatched: true;
  rowDigest: Sha256Digest;
  tableCount: number;
  rowCount: number;
  tables: Record<string, { rowCount: number; digest: Sha256Digest }>;
  sequenceCount: number;
  sequenceDigest: Sha256Digest;
  sequenceFloorSatisfied: true;
  projectionVerification: {
    driftDetected: false;
    resourceCount: number;
    relationshipCount: number;
    latestObservationOffset: number;
    projectionDigest: Sha256Digest;
  };
}

export interface PostgreSQLContinuityQualificationReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "PostgreSQLContinuityQualificationReport";
  metadata: {
    id: `pgc_${string}`;
    generatedAt: string;
    sourceRevision: string;
    sourceDirty: boolean;
  };
  spec: {
    status: "qualified" | "failed";
    environment: {
      profile: "local-docker";
      platform: `${string}/${string}`;
      pythonVersion: string;
      applicationVersion: string;
      containerRuntime: { name: "docker"; version: string };
      database: {
        engine: "postgresql";
        version: string;
        migration: `${number}_${string}.sql`;
        image: `postgres:18.4-alpine@sha256:${string}`;
      };
    };
    profile: {
      name: "physical-streaming-pitr-v1";
      backup: "pg-basebackup-stream-wal";
      replication: "asynchronous-streaming";
      failover: "manual-pg-promote";
      recovery: "archived-wal-named-restore-point";
      workload: "bounded-committed-resource-writes";
      scope: "complete-iip-schema";
      objectives: {
        classification: "local-regression-guardrail";
        maximumCatchupMilliseconds: number;
        maximumFailoverReadyMilliseconds: number;
        maximumPitrReadyMilliseconds: number;
      };
    };
    measurements: {
      fixture: {
        tenantCount: 1;
        resourceCount: number;
        relationshipCount: number;
        investigationCount: 1;
        governedActionCount: 1;
        pluginSessionCount: 1;
        pluginInvocationCount: 1;
        beforeTargetResourceCount: 1;
        afterTargetResourceCount: 1;
      };
      physicalBackup: {
        copyCount: 2;
        totalBytes: number;
        durationMilliseconds: number;
      };
      replication: {
        standbyInRecovery: true;
        primaryTimeline: number;
        primaryFlushLsn: string;
        standbyReplayLsn: string;
        replayLagBytes: 0;
        catchupMilliseconds: number;
        sourceRowDigest: Sha256Digest;
        standbyRowDigest: Sha256Digest;
        sourceSequenceDigest: Sha256Digest;
        standbySequenceDigest: Sha256Digest;
        sequenceFloorSatisfied: true;
      };
      failover: {
        primaryStopped: true;
        standbyPromoted: true;
        promotedTimeline: number;
        readyMilliseconds: number;
        committedRecordLoss: 0;
        integrity: PostgreSQLContinuityIntegrity;
      };
      pitr: {
        restorePointName: "iip_pitr_target";
        archivedWalFileCount: number;
        targetReached: true;
        readyMilliseconds: number;
        boundary: {
          baselinePresent: true;
          beforeTargetPresent: true;
          afterTargetAbsent: true;
        };
        expectedTargetRowDigest: Sha256Digest;
        recoveredRowDigest: Sha256Digest;
        expectedSequenceDigest: Sha256Digest;
        recoveredSequenceDigest: Sha256Digest;
        sequenceFloorSatisfied: true;
        integrity: PostgreSQLContinuityIntegrity;
      };
    };
    checks: Array<{
      id: PostgreSQLContinuityQualificationCheckId;
      status: "passed" | "failed";
      errorCode?: `postgresql.continuity.${string}`;
    }>;
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
  component: "api" | "workflow-worker" | "otlp-receiver";
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

export type TelemetryExportBurnRateStatus =
  | "disabled"
  | "no-data"
  | "insufficient-data"
  | "sustainable"
  | "elevated"
  | "critical";

export interface TelemetryExportBurnRateWindowObservation {
  status: TelemetryExportBurnRateStatus;
  enabledObservations: number;
  eligibleAttempts: number;
  successfulAttempts: number;
  failedAttempts: number;
  attainmentBasisPoints: number | null;
  burnRateHundredths: number | null;
}

export interface TelemetryExportBurnRateSignal {
  signal: "metrics" | "traces";
  status: TelemetryExportBurnRateStatus;
  short: TelemetryExportBurnRateWindowObservation;
  long: TelemetryExportBurnRateWindowObservation;
}

export interface TelemetryExportBurnRateReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "TelemetryExportBurnRateReport";
  metadata: { evaluatedAt: string };
  spec: {
    status: TelemetryExportBurnRateStatus;
    objective: {
      minimumAttainmentBasisPoints: number;
      minimumEligibleAttempts: number;
      criticalBurnRateHundredths: number;
    };
    windows: {
      short: { durationSeconds: number; start: string; end: string };
      long: { durationSeconds: number; start: string; end: string };
    };
    signals: [TelemetryExportBurnRateSignal, TelemetryExportBurnRateSignal];
  };
}

export type CollectorQueueLossStatus =
  | "disabled"
  | "no-data"
  | "insufficient-data"
  | "meeting"
  | "breached";

export interface CollectorQueueLossSignal {
  signal: "metrics" | "logs";
  status: CollectorQueueLossStatus;
  sentDelta: number | null;
  failedDelta: number | null;
  lossBasisPoints: number | null;
  queueSize: number | null;
  queueCapacity: number | null;
  queueUtilizationBasisPoints: number | null;
}

/**
 * Customer OpenTelemetry Collector-observed sending-queue depth and
 * send-loss objective for its pipeline to IIP's receiver. Distinct from
 * TelemetryExportBurnRateReport and TelemetryExportSloReport, which measure
 * IIP's own outbound exporter attempts rather than the Collector's internal
 * queue.
 */
export interface CollectorQueueLossReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "CollectorQueueLossReport";
  metadata: { evaluatedAt: string };
  spec: {
    status: CollectorQueueLossStatus;
    objective: {
      maxLossBasisPoints: number;
      maxQueueUtilizationBasisPoints: number;
      minimumEligibleAttempts: number;
    };
    window: { durationSeconds: number; start: string; end: string };
    binding: { integrationId: string; exporterName: string } | null;
    signals: [CollectorQueueLossSignal, CollectorQueueLossSignal];
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

export interface EventOutboxRetentionReport {
  apiVersion: "iip.platform/v1alpha1";
  kind: "EventOutboxRetentionReport";
  metadata: { tenantId: string; evaluatedAt: string };
  spec: {
    status: "disabled" | "current" | "cleanup-required";
    mode: "observe" | "expire";
    policy: {
      enabled: boolean;
      publishedSeconds: number;
      batchSize: number;
      digest: Sha256Digest;
    };
    rows: {
      storedBefore: number;
      published: number;
      eligible: number;
      expired: number;
      remainingEligible: number;
      protected: number;
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
        policyRef?: {
          id: EvidenceRedactionPolicyId;
          version: string;
        };
      };
      sensitivity: "public" | "internal" | "confidential" | "restricted";
      retentionClass: "ephemeral" | "standard" | "extended" | "legal-hold";
      expiresAt?: string;
    };
  };
}

export type EvidenceRedactionValueClass =
  | "email-address"
  | "ipv4-address";

export interface EvidenceRedactionPolicy {
  apiVersion: "iip.platform/v1alpha1";
  kind: "EvidenceRedactionPolicy";
  metadata: {
    id: EvidenceRedactionPolicyId;
    tenantId: string;
    version: string;
  };
  spec: {
    rules: Array<{
      id: string;
      evidenceTypes: string[];
      valueClasses: EvidenceRedactionValueClass[];
    }>;
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
  seasonalBaselineComparison?: InvestigationTelemetrySeasonalBaselineComparison;
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

export type InvestigationTelemetrySeasonalBaselineComparison =
  | {
      statistic: "minimum" | "maximum" | "mean";
      unit: string;
      periodSeconds: number;
      lookbackPeriods: number;
      evaluationDurationSeconds: number;
      baselineAggregation: "mean" | "median";
      calculation: "difference" | "ratio";
      operator: "lt" | "lte" | "gt" | "gte";
      threshold: number;
      whenMatched: "supports" | "contradicts" | "neutral";
      whenNotMatched: "supports" | "contradicts" | "neutral";
    }
  | {
      statistic: "minimum" | "maximum" | "mean";
      unit: string;
      /** Calendar-aligned periods must be a whole number of days (multiple of 86400). */
      periodSeconds: number;
      lookbackPeriods: number;
      evaluationDurationSeconds: number;
      baselineAggregation: "mean" | "median";
      calculation: "difference" | "ratio";
      operator: "lt" | "lte" | "gt" | "gte";
      threshold: number;
      whenMatched: "supports" | "contradicts" | "neutral";
      whenNotMatched: "supports" | "contradicts" | "neutral";
      /** Shift lookback windows by whole calendar days in `timezone` instead of raw elapsed seconds. */
      calendarAligned: true;
      /** IANA timezone name, e.g. "America/New_York". */
      timezone: string;
    };

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

export interface InvestigationTelemetrySeasonalBaselineAssessment {
  assessmentType: "seasonal-baseline-comparison";
  selectionId: `tqs_${string}`;
  evidenceId: EvidenceId;
  rootCauseClass: string;
  metric: string;
  statistic: "minimum" | "maximum" | "mean";
  unit: string;
  periodSeconds: number;
  lookbackPeriods: number;
  evaluationDurationSeconds: number;
  baselineAggregation: "mean" | "median";
  calendarAligned: boolean;
  timezone: string | null;
  baselineTimeRanges: { start: string; end: string }[];
  evaluationTimeRange: { start: string; end: string };
  calculation: "difference" | "ratio";
  comparisonUnit: string;
  operator: "lt" | "lte" | "gt" | "gte";
  threshold: number;
  baselinePeriodValues?: number[];
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
      | InvestigationTelemetrySeasonalBaselineAssessment
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
