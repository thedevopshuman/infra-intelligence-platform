import type {
  ApiErrorBody,
  ResourceNeighborhood,
  ResourceObservation,
  ResourceTimeline,
  ResourceUid,
} from "./types.js";

export interface ClientOptions {
  baseUrl: string;
  tenantId: string;
  actorId: string;
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

export class InfrastructureIntelligenceClient {
  private readonly baseUrl: string;
  private readonly tenantId: string;
  private readonly actorId: string;
  private readonly fetcher: typeof globalThis.fetch;

  constructor(options: ClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.tenantId = options.tenantId;
    this.actorId = options.actorId;
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

  private headers(correlationId?: string): Record<string, string> {
    const headers: Record<string, string> = {
      "content-type": "application/json",
      "x-iip-tenant-id": this.tenantId,
      "x-iip-actor-id": this.actorId,
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
