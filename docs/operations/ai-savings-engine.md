# AI savings engine

**Status:** Executable Phase A context-growth rule; disabled by default
**Date:** 2026-09-05

The AI savings engine is a deterministic workflow-worker capability. It reads
immutable, priced AI usage cohorts and records an evidence-backed
`AiSavingsFinding`, a value-minimized CloudEvent, and an outbox row in one
transaction. It does not call a model, inspect prompt or response content, or
run in the customer inference path.

V0 implements only `context-growth` version `1.0.0`. It compares mean input
tokens per successful request in two fixed, adjacent, equal-duration windows.
Retry amplification and expensive-model anomaly rules remain future work.

## Trust and authority boundary

- Only the non-interactive workflow worker constructs
  `ai-savings-worker:<worker-id>` with the exact `ai-savings:evaluate` role.
- The API and OTLP receiver do not receive savings profiles or price catalogs.
- Every profile, query, source record, finding, event, and database predicate
  is bound to one explicit tenant.
- Profiles are protected deployment policy. Span attributes cannot select a
  baseline, threshold, price catalog, environment, or commercial scope.
- The engine reads metadata-only usage and calculated cost facts. It never
  reads prompts, responses, messages, tool arguments, or raw provider payloads.
- The recommendation is advisory and always requires workload-specific
  validation before context is changed.

## Protected profile configuration

`IIP_AI_SAVINGS_PROFILES_JSON` is a closed wrapper. One worker may hold at
most 1,000 profiles, and each `(tenantId, profileId)` pair must be unique:

```json
{
  "profiles": [
    {
      "profileId": "support-assistant-context",
      "tenantId": "tenant-a",
      "catalogId": "apc_11111111111111111111111111111111",
      "costEngineVersion": "0.1.0",
      "scope": {
        "provider": "aws.bedrock",
        "modelId": "example.foundation-model-v1:0",
        "region": "us-east-1",
        "serviceName": "support-assistant",
        "deploymentEnvironment": "production"
      },
      "baselineWindow": {
        "start": "2026-09-03T10:00:00Z",
        "end": "2026-09-04T10:00:00Z"
      },
      "currentWindow": {
        "start": "2026-09-04T10:00:00Z",
        "end": "2026-09-05T10:00:00Z"
      },
      "minimumRequestsPerWindow": 20,
      "growthThresholdBasisPoints": 2500,
      "maxRecordsPerWindow": 100,
      "evaluationGraceSeconds": 300
    }
  ]
}
```

The windows must be adjacent, equal in duration, and between one minute and
31 days. Minimum requests are 2–100, the per-window record ceiling is at most
100 and cannot be below the minimum, and grace is 0–86,400 seconds. The
configured catalog must be the exact catalog enrolled for that tenant's cost
engine. When savings are enabled, profile tenants must exactly equal
`IIP_WORKER_TENANTS` and the enabled cost-engine tenant set.

| Variable | Default | Meaning |
| --- | --- | --- |
| `IIP_AI_SAVINGS_ENGINE_ENABLED` | `false` | Enable deterministic savings evaluation in the workflow worker. |
| `IIP_AI_SAVINGS_PROFILES_JSON` | required when enabled | Protected closed profile wrapper. |
| `IIP_AI_SAVINGS_INTERVAL_SECONDS` | `60` | Delay between evaluation passes; range 1–3,600 seconds. |
| `IIP_OTEL_AI_ECONOMICS_ATTRIBUTE_MODE` | `tenant-scope` | Export protected scope labels with tenant identity, or use `scope` for a single-tenant-isolated backend. |

## Exact V0 eligibility

A profile emits a finding only when all of these conditions hold:

1. The current window plus its grace period has ended.
2. Both windows contain at least the configured minimum and no more than the
   configured maximum records.
3. Every record is a successful, complete, metadata-only usage record matching
   the exact provider, model, region, service, and deployment environment.
4. Cache-read and cache-write input are both zero. V0 deliberately avoids
   presenting cached input as an avoidable uncached saving.
5. Every usage record has an exact `priced` cost fact from the configured
   catalog and cost-engine version.
6. Currency and currency scale agree across both cohorts, and the current
   uncached-input rate is uniform.
7. Current mean input tokens exceed the baseline mean by at least the protected
   basis-point threshold.

No finding is evidence, not zero savings. A pass reports aggregate counts for
`pending`, `insufficient`, `unresolved`, `unsupported`, `belowThreshold`, and
`failures` without logging tenant IDs, model IDs, token counts, rates, money,
record IDs, or exception text.

## Deterministic calculation

All calculations use non-negative safe integers and round halves upward:

```text
baselineMean = roundHalfUp(sum(baselineInputTokens) / baselineCount)
currentMean = roundHalfUp(sum(currentInputTokens) / currentCount)
growthBasisPoints = roundHalfUp((currentMean - baselineMean) * 10000 / baselineMean)
excessQuantity = (currentMean - baselineMean) * currentCount
potentialSaving = roundHalfUp(excessQuantity * currentUncachedInputRate / 1000000)
```

The finding ID is derived from the canonical finding specification. Evaluation
time is excluded from the immutable document hash, so a later exact retry
returns the first record and emits no duplicate event. Severity and confidence
use fixed rule-version thresholds; they are not model judgments.

The PostgreSQL adapter reloads the complete exact-scope cohort, every cited
usage and cost fact, and recalculates the formula before committing. A forged,
partial, cross-tenant, stale-scope, or differently priced result fails closed.

## Helm enablement

Store the profile wrapper in an existing Secret and reference only the Secret
name and key from values:

```yaml
worker:
  enabled: true
  tenants: [tenant-a]

aiCostEngine:
  enabled: true
  catalogsExistingSecret: iip-ai-price-catalogs

aiSavingsEngine:
  enabled: true
  profilesExistingSecret: iip-ai-savings-profiles
  profilesSecretKey: ai-savings-profiles-json
  intervalSeconds: 60
```

Apply packaged migrations through `0020_ai_savings_ledger.sql` before enabling
the worker. Chart validation rejects savings without the worker, cost engine,
tenant enrollment, and profile Secret. The profile is mounted only into the
worker.

## Operations, recovery, and verification

Publish a new profile or window as a reviewed Secret update and restart the
worker. Existing findings remain immutable. Roll back by restoring the prior
Secret and image; migration `0020` is forward-only and requires no destructive
database rollback. Do not delete findings to change a recommendation—publish a
new rule version or profile window instead.

Run the repository and real PostgreSQL gates:

```bash
make verify PYTHON=.venv/bin/python
make test-postgres PYTHON=.venv/bin/python
```

The privacy-bounded aggregate metric projection is executable through the
existing OTLP exporter and documented in the
[AI economics telemetry contract](../specifications/ai-economics-telemetry-contract.md).
The Grafana dashboard, full Bedrock-shaped end-to-end gate, and live Bedrock
instrumentation qualification remain separate Phase A exit work.
