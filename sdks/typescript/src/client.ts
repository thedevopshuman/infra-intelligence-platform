import type {
  ActionApproval,
  ActionExecutionStatus,
  ActionId,
  ActionProposal,
  ActionResult,
  ActionWorkflow,
  ActionWorkflowPage,
  AiAllocationReport,
  ApiErrorBody,
  ContextEvidenceRequest,
  ConsoleAuthenticationConfiguration,
  Evidence,
  EvidenceRetentionReport,
  EvidenceId,
  EventDeliveryHealthReport,
  EventDeliverySloReport,
  EventDeliveryReplayParameters,
  IngestionFreshnessReport,
  InvestigationCompletionSloReport,
  InvestigationId,
  InvestigationCancellationRequest,
  InvestigationReport,
  InvestigationRequest,
  InvestigationStatus,
  InvestigationJobStatus,
  KubernetesEventEvidenceRequest,
  LogEvidenceRequest,
  PluginSession,
  PluginInvocationCancellationRequest,
  PluginInvocationId,
  PluginInvocationReconciliationRequest,
  PluginInvocationStatus,
  ResourceCollectionRequest,
  ResourceCollectionResult,
  ResourceChangeEvidenceRequest,
  ResourceNeighborhood,
  ResourceObservation,
  ResourceTimeline,
  ResourceUid,
  RuntimeVersionReport,
  SessionContext,
  TelemetryEvidenceRequest,
  TelemetryDeploymentExportHealthReport,
  TelemetryExportHealthReport,
  TelemetryExportSloReport,
  TelemetryExportBurnRateReport,
  CollectorQueueLossReport,
} from "./types.js";

export interface ClientOptions {
  baseUrl: string;
  bearerToken: string;
  fetch?: typeof globalThis.fetch;
}

export class PlatformApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(`platform request failed (${status}, ${code})`);
  }
}

export interface ConsoleAuthenticationDiscoveryOptions {
  baseUrl: string;
  fetch?: typeof globalThis.fetch;
}

function validateConsoleAuthentication(
  payload: unknown,
): ConsoleAuthenticationConfiguration {
  if (!payload || typeof payload !== "object") {
    throw new Error("console authentication configuration is invalid");
  }
  const document = payload as Partial<ConsoleAuthenticationConfiguration>;
  const spec = document.spec;
  if (
    Object.keys(payload).sort().join(",") !== "apiVersion,kind,spec" ||
    document.apiVersion !== "iip.platform/v1alpha1" ||
    document.kind !== "ConsoleAuthenticationConfiguration" ||
    !spec ||
    !["local-token", "access-token", "oidc-pkce"].includes(spec.mode)
  ) {
    throw new Error("console authentication configuration is invalid");
  }
  if (spec.mode !== "oidc-pkce") {
    if (Object.keys(spec).join(",") !== "mode") {
      throw new Error("console OIDC configuration is inconsistent");
    }
    return document as ConsoleAuthenticationConfiguration;
  }
  const profile = spec.oidc;
  if (
    !profile ||
    typeof profile !== "object" ||
    Object.keys(spec).sort().join(",") !== "mode,oidc" ||
    Object.keys(profile).sort().join(",") !==
      "authorizationEndpoint,clientId,issuer,pkceMethod,providerLabel,redirectUri,scopes,tokenEndpoint"
  ) {
    throw new Error("console OIDC profile is invalid");
  }
  if (
    [profile.issuer, profile.authorizationEndpoint, profile.tokenEndpoint, profile.redirectUri]
      .some((value) => typeof value !== "string" || value.length < 1 || value.length > 2048 || /[\x00-\x20\x7f]/.test(value))
  ) {
    throw new Error("console OIDC profile is invalid");
  }
  let issuer: URL;
  let authorizationEndpoint: URL;
  let tokenEndpoint: URL;
  let redirectUri: URL;
  try {
    issuer = new URL(profile.issuer);
    authorizationEndpoint = new URL(profile.authorizationEndpoint);
    tokenEndpoint = new URL(profile.tokenEndpoint);
    redirectUri = new URL(profile.redirectUri);
  } catch {
    throw new Error("console OIDC profile is invalid");
  }
  const cleanHttps = (value: URL) =>
    value.protocol === "https:" &&
    !value.username &&
    !value.password &&
    !value.search &&
    !value.hash;
  const redirectIsSafe =
    redirectUri.protocol === "https:" ||
    (redirectUri.protocol === "http:" &&
      ["localhost", "127.0.0.1"].includes(redirectUri.hostname));
  if (
    !cleanHttps(issuer) ||
    !cleanHttps(authorizationEndpoint) ||
    !cleanHttps(tokenEndpoint) ||
    !redirectIsSafe ||
    !!redirectUri.username ||
    !!redirectUri.password ||
    !!redirectUri.search ||
    !!redirectUri.hash ||
    !["/console", "/console/"].includes(redirectUri.pathname) ||
    !/^[^\s\x00-\x1f]{1,256}$/.test(profile.clientId) ||
    !Array.isArray(profile.scopes) ||
    profile.scopes.length < 1 ||
    profile.scopes.length > 32 ||
    new Set(profile.scopes).size !== profile.scopes.length ||
    !profile.scopes.includes("openid") ||
    profile.scopes.some((scope) => !/^[A-Za-z0-9._:/-]{1,128}$/.test(scope)) ||
    !/^[^\x00-\x1f\x7f]{1,64}$/.test(profile.providerLabel) ||
    profile.pkceMethod !== "S256"
  ) {
    throw new Error("console OIDC profile is invalid");
  }
  return document as ConsoleAuthenticationConfiguration;
}

export async function discoverConsoleAuthentication(
  options: ConsoleAuthenticationDiscoveryOptions,
): Promise<ConsoleAuthenticationConfiguration> {
  const baseUrl = options.baseUrl.replace(/\/$/, "");
  if (!baseUrl) throw new Error("baseUrl is required");
  const fetcher = options.fetch ?? globalThis.fetch;
  const response = await fetcher(`${baseUrl}/v1/authentication/console`, {
    headers: { accept: "application/json" },
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
    referrerPolicy: "no-referrer",
  });
  const declaredLength = response.headers.get("content-length");
  if (
    declaredLength !== null &&
    (!/^\d+$/.test(declaredLength) || Number(declaredLength) > 65_536)
  ) {
    throw new Error("console authentication response is invalid");
  }
  const text = await response.text();
  if (new TextEncoder().encode(text).byteLength > 65_536) {
    throw new Error("console authentication response is invalid");
  }
  let body: ConsoleAuthenticationConfiguration | ApiErrorBody;
  try {
    body = JSON.parse(text) as ConsoleAuthenticationConfiguration | ApiErrorBody;
  } catch {
    throw new Error("console authentication response is invalid");
  }
  if (!response.ok) {
    const candidate = body as Partial<ApiErrorBody>;
    throw new PlatformApiError(
      response.status,
      candidate.error?.code ?? "request.failed",
    );
  }
  return validateConsoleAuthentication(body);
}

export class InfrastructureIntelligenceClient {
  private readonly baseUrl: string;
  private readonly bearerToken: string;
  private readonly fetcher: typeof globalThis.fetch;

  constructor(options: ClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    if (!options.bearerToken) throw new Error("bearerToken is required");
    this.bearerToken = options.bearerToken;
    this.fetcher = options.fetch ?? globalThis.fetch;
  }

  async ingestResource(
    resource: ResourceObservation,
    correlationId?: string,
  ): Promise<ResourceObservation> {
    const headers = this.headers(correlationId);
    const response = await this.fetcher(`${this.baseUrl}/v1/resources`, {
      method: "POST",
      headers,
      body: JSON.stringify(resource),
    });
    return this.read<ResourceObservation>(response);
  }

  async listResources(): Promise<ResourceObservation[]> {
    const response = await this.fetcher(`${this.baseUrl}/v1/resources`, {
      headers: this.headers(),
    });
    const result = await this.read<{ items: ResourceObservation[] }>(response);
    return result.items;
  }

  async getSession(): Promise<SessionContext> {
    return this.get<SessionContext>("/v1/session");
  }

  async getRuntimeVersion(): Promise<RuntimeVersionReport> {
    return this.get<RuntimeVersionReport>("/v1/system/version");
  }

  async getResourceNeighborhood(
    resourceUid: ResourceUid,
    options: {
      direction?: "incoming" | "outgoing" | "both";
      relationshipTypes?: string[];
      limit?: number;
      cursor?: string;
    } = {},
  ): Promise<ResourceNeighborhood> {
    const query = new URLSearchParams({
      depth: "1",
      direction: options.direction ?? "both",
      limit: String(options.limit ?? 50),
    });
    for (const type of options.relationshipTypes ?? []) {
      query.append("relationshipType", type);
    }
    if (options.cursor) query.set("cursor", options.cursor);
    const response = await this.fetcher(
      `${this.baseUrl}/v1/resources/${encodeURIComponent(resourceUid)}/neighborhood?${query}`,
      { headers: this.headers() },
    );
    return this.read<ResourceNeighborhood>(response);
  }

  async getResourceTimeline(
    resourceUid: ResourceUid,
    options: { limit?: number; cursor?: string } = {},
  ): Promise<ResourceTimeline> {
    const query = new URLSearchParams({ limit: String(options.limit ?? 50) });
    if (options.cursor) query.set("cursor", options.cursor);
    const response = await this.fetcher(
      `${this.baseUrl}/v1/resources/${encodeURIComponent(resourceUid)}/timeline?${query}`,
      { headers: this.headers() },
    );
    return this.read<ResourceTimeline>(response);
  }

  async getIngestionFreshness(sourceId: string): Promise<IngestionFreshnessReport> {
    const query = new URLSearchParams({ sourceId });
    return this.get<IngestionFreshnessReport>(`/v1/telemetry/ingestion?${query}`);
  }

  async getTelemetryExportHealth(): Promise<TelemetryExportHealthReport> {
    return this.get<TelemetryExportHealthReport>(
      "/v1/operations/telemetry/export-health",
    );
  }

  async getTelemetryDeploymentExportHealth(): Promise<TelemetryDeploymentExportHealthReport> {
    return this.get<TelemetryDeploymentExportHealthReport>(
      "/v1/operations/telemetry/deployment-export-health",
    );
  }

  async getTelemetryExportSlo(): Promise<TelemetryExportSloReport> {
    return this.get<TelemetryExportSloReport>(
      "/v1/operations/telemetry/export-slo",
    );
  }

  async getTelemetryExportBurnRate(): Promise<TelemetryExportBurnRateReport> {
    return this.get<TelemetryExportBurnRateReport>(
      "/v1/operations/telemetry/export-burn-rate",
    );
  }

  /**
   * Distinct from getTelemetryExportBurnRate, which measures IIP's own
   * outbound exporter attempts rather than the customer Collector's
   * internal queue for its pipeline to IIP.
   */
  async getCollectorQueueLoss(): Promise<CollectorQueueLossReport> {
    return this.get<CollectorQueueLossReport>(
      "/v1/operations/telemetry/collector-queue-loss",
    );
  }

  async getEventDeliveryHealth(limit = 50): Promise<EventDeliveryHealthReport> {
    const query = new URLSearchParams({ limit: String(limit) });
    return this.get<EventDeliveryHealthReport>(
      `/v1/operations/events/delivery-health?${query}`,
    );
  }

  async getEventDeliverySlo(): Promise<EventDeliverySloReport> {
    return this.get<EventDeliverySloReport>(
      "/v1/operations/events/delivery-slo",
    );
  }

  async getInvestigationCompletionSlo(): Promise<InvestigationCompletionSloReport> {
    return this.get<InvestigationCompletionSloReport>(
      "/v1/operations/investigations/completion-slo",
    );
  }

  async getEvidenceRetention(): Promise<EvidenceRetentionReport> {
    return this.get<EvidenceRetentionReport>(
      "/v1/operations/evidence/retention",
    );
  }

  async ingestResourceCollection(
    request: ResourceCollectionRequest,
    result: ResourceCollectionResult,
    correlationId?: string,
  ): Promise<ResourceObservation[]> {
    // Complete reconciliations may append host-generated Resource tombstones.
    const payload = await this.post<{ items: ResourceObservation[] }>(
      "/v1/collections/ingest",
      { request, result },
      correlationId,
    );
    return payload.items;
  }

  async runInvestigation(request: InvestigationRequest): Promise<InvestigationReport> {
    return this.post<InvestigationReport>("/v1/investigations", request);
  }

  async submitInvestigation(request: InvestigationRequest): Promise<InvestigationJobStatus> {
    return this.post<InvestigationJobStatus>("/v1/investigation-jobs", request);
  }

  async getInvestigationJob(id: InvestigationId): Promise<InvestigationJobStatus> {
    return this.get<InvestigationJobStatus>(
      `/v1/investigation-jobs/${encodeURIComponent(id)}`,
    );
  }

  async cancelInvestigationJob(
    request: InvestigationCancellationRequest,
  ): Promise<InvestigationJobStatus> {
    return this.post<InvestigationJobStatus>(
      `/v1/investigation-jobs/${encodeURIComponent(request.spec.investigationId)}/cancel`,
      request,
    );
  }

  async getInvestigation(id: InvestigationId): Promise<InvestigationReport> {
    return this.get<InvestigationReport>(`/v1/investigations/${encodeURIComponent(id)}`);
  }

  async getInvestigationStatus(id: InvestigationId): Promise<InvestigationStatus> {
    return this.get<InvestigationStatus>(
      `/v1/investigations/${encodeURIComponent(id)}/status`,
    );
  }

  async cancelInvestigation(
    request: InvestigationCancellationRequest,
  ): Promise<InvestigationStatus> {
    return this.post<InvestigationStatus>(
      `/v1/investigations/${encodeURIComponent(request.spec.investigationId)}/cancel`,
      request,
    );
  }

  async getEvidence(id: EvidenceId): Promise<Evidence> {
    return this.get<Evidence>(`/v1/evidence/${encodeURIComponent(id)}`);
  }

  async collectTelemetryEvidence(request: TelemetryEvidenceRequest): Promise<Evidence> {
    return this.post<Evidence>("/v1/evidence/telemetry/queries", request);
  }

  async collectLogEvidence(request: LogEvidenceRequest): Promise<Evidence> {
    return this.post<Evidence>("/v1/evidence/logs/queries", request);
  }

  async collectKubernetesEventEvidence(
    request: KubernetesEventEvidenceRequest,
  ): Promise<Evidence> {
    return this.post<Evidence>("/v1/evidence/kubernetes/events/queries", request);
  }

  async collectResourceChangeEvidence(
    request: ResourceChangeEvidenceRequest,
  ): Promise<Evidence> {
    return this.post<Evidence>("/v1/evidence/changes/queries", request);
  }

  async collectContextEvidence(request: ContextEvidenceRequest): Promise<Evidence> {
    return this.post<Evidence>("/v1/evidence/context/queries", request);
  }

  async proposeAction(command: {
    investigationId: InvestigationId;
    targetResourceUid: ResourceUid;
    idempotencyKey: string;
    expiresAt: string;
    dryRun?: boolean;
  } & (
    | {
        actionType: "kubernetes.restart-workload";
        parameters: {
          namespace: string;
          workloadKind: "deployment" | "statefulset" | "daemonset";
          workloadName: string;
        };
      }
    | {
        actionType: "event-delivery.requeue";
        parameters: EventDeliveryReplayParameters;
      }
  )): Promise<ActionProposal> {
    return this.post<ActionProposal>("/v1/actions/proposals", command);
  }

  async proposeEventDeliveryReplay(command: {
    investigationId: InvestigationId;
    targetResourceUid: ResourceUid;
    parameters: EventDeliveryReplayParameters;
    idempotencyKey: string;
    expiresAt: string;
    dryRun?: boolean;
  }): Promise<ActionProposal> {
    return this.proposeAction({
      ...command,
      actionType: "event-delivery.requeue",
    });
  }

  async decideAction(
    proposalId: ActionId,
    decision: "approved" | "rejected",
    rationale: string,
  ): Promise<ActionApproval> {
    return this.post<ActionApproval>(
      `/v1/actions/${encodeURIComponent(proposalId)}/decision`,
      { decision, rationale },
    );
  }

  async executeAction(proposalId: ActionId): Promise<ActionResult> {
    return this.post<ActionResult>(
      `/v1/actions/${encodeURIComponent(proposalId)}/execute`,
      {},
    );
  }

  async getAction(
    proposalId: ActionId,
  ): Promise<ActionProposal | ActionExecutionStatus | ActionResult> {
    return this.get<ActionProposal | ActionExecutionStatus | ActionResult>(
      `/v1/actions/${encodeURIComponent(proposalId)}`,
    );
  }

  async getActionWorkflow(proposalId: ActionId): Promise<ActionWorkflow> {
    return this.get<ActionWorkflow>(
      `/v1/actions/${encodeURIComponent(proposalId)}/workflow`,
    );
  }

  async listActionWorkflows(
    options: { limit?: number; cursor?: string } = {},
  ): Promise<ActionWorkflowPage> {
    const query = new URLSearchParams({ limit: String(options.limit ?? 50) });
    if (options.cursor) query.set("cursor", options.cursor);
    return this.get<ActionWorkflowPage>(`/v1/actions?${query}`);
  }

  async getAiAllocationReport(options: {
    start: string;
    end: string;
    groupBy: "application" | "team";
  }): Promise<AiAllocationReport> {
    const query = new URLSearchParams({
      start: options.start,
      end: options.end,
      groupBy: options.groupBy,
    });
    return this.get<AiAllocationReport>(
      `/v1/ai/economics/allocation?${query}`,
    );
  }

  async openPluginSession(command: {
    manifest: Record<string, unknown>;
    requestedCapabilities: string[];
    capabilityToken: string;
    limits?: {
      maxRequests?: number;
      maxWallTimeSeconds?: number;
      maxOutputBytes?: number;
    };
  }): Promise<PluginSession> {
    return this.post<PluginSession>("/v1/plugin-sessions", command);
  }

  async getPluginInvocationStatus(
    invocationId: PluginInvocationId,
  ): Promise<PluginInvocationStatus> {
    return this.get<PluginInvocationStatus>(
      `/v1/plugin-invocations/${encodeURIComponent(invocationId)}/status`,
    );
  }

  async cancelPluginInvocation(
    request: PluginInvocationCancellationRequest,
  ): Promise<PluginInvocationStatus> {
    return this.post<PluginInvocationStatus>(
      `/v1/plugin-invocations/${encodeURIComponent(request.spec.invocationId)}/cancel`,
      request,
    );
  }

  async reconcilePluginInvocation(
    request: PluginInvocationReconciliationRequest,
  ): Promise<PluginInvocationStatus> {
    return this.post<PluginInvocationStatus>(
      `/v1/plugin-invocations/${encodeURIComponent(request.spec.invocationId)}/reconcile`,
      request,
    );
  }

  private async get<T>(path: string): Promise<T> {
    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      headers: this.headers(),
    });
    return this.read<T>(response);
  }

  private async post<T>(
    path: string,
    payload: unknown,
    correlationId?: string,
  ): Promise<T> {
    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: this.headers(correlationId),
      body: JSON.stringify(payload),
    });
    return this.read<T>(response);
  }

  private headers(correlationId?: string): Record<string, string> {
    const headers: Record<string, string> = {
      "content-type": "application/json",
      authorization: `Bearer ${this.bearerToken}`,
    };
    if (correlationId) headers["x-correlation-id"] = correlationId;
    return headers;
  }

  private async read<T>(response: Response): Promise<T> {
    const body = (await response.json()) as T | ApiErrorBody;
    if (!response.ok) {
      const candidate = body as Partial<ApiErrorBody>;
      throw new PlatformApiError(
        response.status,
        candidate.error?.code ?? "request.failed",
      );
    }
    return body as T;
  }
}
