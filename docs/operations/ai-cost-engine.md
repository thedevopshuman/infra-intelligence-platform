# AI cost engine

**Status:** Executable Phase A reference path
**Date:** 2026-09-05

The AI cost engine is a disabled-by-default workflow-worker capability. It
reads immutable metadata-only `AiUsageRecord` rows, registers one protected
price-catalog snapshot for each explicitly enrolled tenant, and atomically
commits one explainable `AiCostRecord`, CloudEvent, and outbox row. It is
separate from OTLP collection and never runs in a customer inference request.

## Trust and authority boundary

- Only the non-interactive worker constructs the exact
  `ai-cost-worker:<worker-id>` actor with `ai-cost:calculate` authority.
- The control-plane API and OTLP receiver do not receive the price-catalog
  configuration.
- Every catalog, usage lookup, cost row, event, and database predicate is bound
  to one tenant.
- Catalog source locators reject embedded credentials and credential-like
  content. Catalog JSON can still contain negotiated rates, so supply it only
  through protected runtime configuration.
- `test-fixture` catalogs are rejected unless an explicit non-production gate
  is enabled. Every resulting record is marked `test-fixture-pricing`.

The engine has no provider credential and makes no network request. Updating a
catalog is a reviewed deployment/configuration operation, not automatic price
scraping.

## Configuration shape

`IIP_AI_PRICE_CATALOGS_JSON` is a closed wrapper containing exactly one catalog
for every tenant in `IIP_WORKER_TENANTS`:

```json
{
  "catalogs": [
    {
      "apiVersion": "iip.platform/v1alpha1",
      "kind": "AiPriceCatalog",
      "metadata": {
        "id": "apc_11111111111111111111111111111111",
        "tenantId": "tenant-a",
        "version": "2026-09-05.1",
        "publishedAt": "2026-09-05T09:00:00Z"
      },
      "spec": {
        "currency": "USD",
        "currencyScale": 9,
        "source": {
          "kind": "operator-managed",
          "locator": "urn:customer:approved-ai-prices:2026-09-05",
          "retrievedAt": "2026-09-05T08:55:00Z",
          "contentHash": "sha256:1111111111111111111111111111111111111111111111111111111111111111"
        },
        "entries": []
      }
    }
  ]
}
```

The abbreviated `entries` array above is not valid configuration. Build each
complete item from the reviewed
[`AiPriceCatalog` example](../../contracts/examples/ai-price-catalog.json) and
schema, change its source from `test-fixture`, and verify that the source hash
identifies the reviewed upstream material. Arrays used for match dimensions
are unique and lexically sorted.

| Variable | Default | Meaning |
| --- | --- | --- |
| `IIP_AI_COST_ENGINE_ENABLED` | `false` | Enable price registration and bounded cost passes in the workflow worker. |
| `IIP_AI_PRICE_CATALOGS_JSON` | required when enabled | Protected closed catalog wrapper; one exact tenant catalog per worker tenant. |
| `IIP_AI_PRICE_CATALOG_ALLOW_TEST_FIXTURES` | `false` | Permit explicitly marked fixture rates for tests only. |
| `IIP_AI_COST_BATCH_SIZE` | `100` | Maximum usage records processed for one tenant in one pass; range 1–1000. |
| `IIP_AI_COST_INTERVAL_SECONDS` | `10` | Delay between cost passes; range 1–3600 seconds. |

Startup fails closed when the enabled catalog tenant set differs from worker
enrollment, a catalog is malformed, two catalogs target one tenant, or fixture
pricing is present without its gate.

## Helm enablement

Store the entire wrapper under one key in an existing Secret, then reference
only its name and key from protected values:

```yaml
worker:
  enabled: true
  tenants: [tenant-a]

aiCostEngine:
  enabled: true
  catalogsExistingSecret: iip-ai-price-catalogs
  catalogsSecretKey: ai-price-catalogs-json
  allowTestFixtures: false
  batchSize: 100
  intervalSeconds: 10
```

Kubernetes Secret object-size limits bound this chart profile below the
application parser's defensive maximum. Split larger tenant fleets across
separately enrolled worker releases until a protected catalog service is
accepted. Never place catalog bodies directly in a committed values file.

Apply packaged migrations through `0019_ai_cost_ledger.sql` before enabling the
worker. The migration hook remains database-only; serving workloads never
apply schema changes.

## Calculation and unresolved coverage

The engine chooses `responseModel` when present, otherwise `requestModel`, and
matches provider, model, region, service tier, routing mode, purchase mode, and
invocation start time. It uses five non-overlapping token categories and
integer half-up arithmetic:

```text
uncached input = input - cache read - cache write
non-reasoning output = output - reasoning output
line amount = floor((quantity * rate + 500000) / 1000000)
```

A unique match with complete meters produces `priced`. No match, incomplete
meters, an invalid subset, or arithmetic beyond the contract's safe integer
range produces `unpriced` with a reason. Multiple applicable entries produce
`ambiguous`. Neither unresolved state is converted to numeric zero.

Exact retries return the existing result and emit no second event. A new
catalog ID/version intentionally recalculates source usage into new immutable
facts; it never mutates or deletes earlier calculations. The current service
records calculated estimates only and makes no invoice, discount, commitment,
tax, or billing-reconciliation claim.

## Operations and recovery

The worker emits only aggregate pass counts: processed, priced, unpriced,
ambiguous, and failures. It does not log tenant IDs, catalog values, usage IDs,
rates, totals, or exception text. Investigate nonzero failures through protected
configuration and database health rather than increasing log detail.

To roll forward, publish a new immutable catalog ID and version, update the
Secret through the customer's secret-management workflow, and restart the
worker. Keep the old catalog and cost facts for audit. To roll back calculation,
restore the previous Secret reference and worker image; database migration
`0019` is forward-only and does not require deleting the new tables. Do not
delete calculated records as a rollback mechanism.

Verify pure calculation, privacy, tenant, duplicate, and adapter behavior with:

```bash
make verify PYTHON=.venv/bin/python
```

With Docker Desktop running, prove real PostgreSQL catalog, usage, cost, event,
and outbox durability with:

```bash
make test-postgres PYTHON=.venv/bin/python
```

The isolated receiver gate additionally sends an official SDK-exported,
Bedrock-shaped OTLP trace through mutual TLS, waits for the asynchronous worker,
and verifies the linked priced cost fact and cost event/outbox row:

```bash
make test-otlp-receiver PYTHON=.venv/bin/python
```

The context-growth saving rule, bounded aggregate export, dashboard, and real
Bedrock instrumentation qualification are separate Phase A exit work.
