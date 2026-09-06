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
  | "reasoning-output-tokens";

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
      deploymentEnvironment?: string;
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

export type CustomerDeploymentPreflightCoreCheckId =
  | "helm-render"
  | "immutable-image"
  | "published-image-repository"
  | "api-redundancy"
  | "worker-enrollment"
  | "worker-redundancy"
  | "external-database"
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
  | "evidence-backends"
  | "service-account-isolation"
  | "test-fixtures-denied";

export type CustomerDeploymentPreflightAiCheckId =
  | "ai-usage-intake"
  | "ai-receiver-mtls"
  | "ai-receiver-redundancy"
  | "ai-cost-allocation-savings"
  | "collector-loss-objective";

export type CustomerDeploymentPreflightCheckId =
  | CustomerDeploymentPreflightCoreCheckId
  | CustomerDeploymentPreflightAiCheckId
  | "cluster-api"
  | "referenced-dependencies";

export type CustomerDeploymentPreflightRequirement =
  | "signed-published-release"
  | "vulnerability-qualified-release"
  | "customer-oidc-browser-issuer"
  | "customer-policy-bundle"
  | "customer-workload-identity-broker"
  | "customer-collector-pki"
  | "customer-postgresql-ha-dr"
  | "customer-workload-slo"
  | "live-bedrock-model-region-streaming"
  | "authoritative-ai-price-catalog"
  | "ai-workload-saving-validation";

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
      name: "production-core-v1" | "production-ai-finops-v0";
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
      totalChecks: 23 | 28;
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
    qualificationLevel: "local-multi-node-kind-v1";
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
      totalChecks: 14;
      passedChecks: 14;
      failedChecks: 0;
      totalProbeAttempts: number;
      failedProbeAttempts: 0;
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
      requiredEvidence: 18;
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
