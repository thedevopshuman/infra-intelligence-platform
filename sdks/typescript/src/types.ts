export type ResourceHealth = "healthy" | "degraded" | "unhealthy" | "unknown";
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
    uid?: `res_${string}`;
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

export interface ApiErrorBody {
  error: { code: string };
}

