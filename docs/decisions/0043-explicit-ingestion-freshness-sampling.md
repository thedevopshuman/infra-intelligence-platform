# ADR 0043: explicit ingestion-freshness sampling

**Status:** Accepted
**Date:** 2026-08-17

## Context

Ingestion freshness was measurable and exportable, but an evaluation occurred only when an authenticated user called the freshness endpoint. Operational metrics could disappear precisely when nobody had the console open. The workflow worker already owns non-impacting periodic work, but it previously loaded the interactive identity secret even though it accepts no user requests.

## Decision

The workflow worker automatically evaluates a closed list of `{tenantId, sourceId}` targets every 5–3600 seconds. Targets are protected deployment configuration, are unique and bounded to 1000, and each tenant must also appear in the worker's explicit tenant enrollment. There is no wildcard or database-wide discovery mode.

Each evaluation uses the fixed internal actor `iip-ingestion-monitor` with the `system-monitor` role and passes through the normal `ingestion-telemetry:read` policy decision. Missing, denied, invalid, and failed targets are isolated so one source cannot block the others. The worker writes only aggregate counts to stdout; tenant/source identifiers remain governed by the configured OpenTelemetry attribute mode. Successful evaluation uses the existing provider-neutral telemetry sink.

The worker runtime composes `DenyAllAuthenticator`, never loads interactive Bearer or OIDC credentials, never exposes an HTTP listener, and continues to omit the action executor. Helm no longer mounts an interactive identity Secret or OIDC CA into the worker pod and disables ambient service-account token mounting. Explicit projected identity for the credential broker remains separate.

## Consequences

- Ingestion freshness telemetry continues without console or API traffic.
- Enrollment and policy remain explicit at tenant and source granularity.
- Multiple worker replicas may evaluate the same target; the resulting gauges are observational and idempotent, but production ownership should assign each target to one worker group to avoid redundant cost.
- Export delivery health, durable telemetry buffering, SLO windows, and alert routing remain Phase 3 work.
