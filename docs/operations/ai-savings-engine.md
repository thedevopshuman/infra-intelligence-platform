# AI savings engine

**Status:** Executable context-growth and retry-amplification rules; disabled by default
**Date:** 2026-09-05

The AI savings engine is a deterministic workflow-worker capability. It reads
immutable AI usage and calculated-cost cohorts and records an evidence-backed
`AiSavingsFinding`, a value-minimized CloudEvent, and an outbox row in one
transaction. It does not call a model, inspect prompt or response content, or
run in the customer inference path.

The runtime implements `context-growth` and `retry-amplification` version
`1.0.0`. Context growth compares mean input tokens per successful request;
retry amplification compares the share of successful operations reporting at
least one retry. Both use fixed, adjacent, equal-duration windows. The
expensive-model anomaly rule remains future work.

## Trust and authority boundary

- Only the non-interactive workflow worker constructs
  `ai-savings-worker:<worker-id>` with the exact `ai-savings:evaluate` role.
- The API and OTLP receiver do not receive savings profiles or price catalogs.
- Every profile, query, source record, finding, event, and database predicate
  is bound to one explicit tenant.
- Profiles are protected deployment policy. Span attributes cannot select a
  baseline, threshold, price catalog, environment, or commercial scope.
- Provider retry attribute names and optional zero-on-absence behavior are
  protected channel policy. The evaluator consumes only normalized
  `retryCount` facts.
- The engine reads metadata-only usage and calculated cost facts. It never
  reads prompts, responses, messages, tool arguments, or raw provider payloads.
- The recommendation is advisory and always requires workload-specific
  validation before context or retry behavior is changed.

## Protected profile configuration

`IIP_AI_SAVINGS_PROFILES_JSON` is a closed wrapper. One worker may hold at
most 1,000 profiles, and each `(tenantId, profileId)` pair must be unique:

```json
{
  "profiles": [
    {
      "profileId": "support-assistant-context",
      "ruleId": "context-growth",
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

A retry profile uses the same identity, catalog/query generation, scope,
windows, minimum, ceiling, and grace fields, but replaces
`growthThresholdBasisPoints` with:

```json
{
  "profileId": "support-assistant-retries",
  "ruleId": "retry-amplification",
  "retryRateIncreaseThresholdBasisPoints": 2500,
  "minimumCurrentRetryRateBasisPoints": 2500
}
```

The abbreviated object shows only the rule-specific fields; it is not a
standalone valid profile. Existing context-growth profiles without `ruleId`
remain compatible and resolve to `context-growth`, but new configuration
should always state the rule explicitly.

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

## Exact context-growth eligibility

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

## Exact retry-amplification eligibility

A retry profile emits a finding only when:

1. its current window and grace have ended;
2. both exact-scope windows meet the protected request minimum and ceiling;
3. every record is a successful invocation with a normalized integer
   `retryCount` from 0 through 100;
4. the current share of operations with `retryCount > 0` meets the protected
   minimum; and
5. the absolute current-minus-baseline rate meets the protected increase
   threshold.

Missing retry facts make the profile `unsupported`; the rule does not infer
zero. The finding cites the complete usage cohort and persists only after the
adapter reloads and recalculates it. Its potential saving is always
`unresolved/retry-billing-unproven` in version `1.0.0`, with no cost reference
or amount. The final successful span cannot prove which hidden attempts were
billable.

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

Retry rate uses the same integer half-up convention:

```text
retryRateBasisPoints = roundHalfUp(retryingOperations * 10000 / operations)
increaseBasisPoints = currentRetryRateBasisPoints - baselineRetryRateBasisPoints
```

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

Apply packaged migrations through `0022_ai_retry_savings_rule.sql` before
enabling the worker. Chart validation rejects savings without the worker, cost
engine, tenant enrollment, and profile Secret. The profile is mounted only
into the worker.

## Operations, recovery, and verification

Publish a new profile or window as a reviewed Secret update and restart the
worker. Existing findings remain immutable. Roll back by restoring the prior
Secret and image; migrations `0020` and `0022` are forward-only and require no destructive
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
The provisioned Grafana dashboard and deterministic Bedrock-shaped end-to-end
gate are executable through the
[local AI FinOps topology](ai-finops-local-demo.md). Live Bedrock
qualification remains opt-in and environment-specific; the pinned official
instrumentation is qualified offline against the exact normalized boundary.
