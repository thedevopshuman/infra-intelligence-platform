# AI usage attribution

**Status:** Executable Phase B foundation; disabled by default
**Date:** 2026-09-05

The workflow worker can resolve immutable AI usage to reviewed application and
team ownership. It is asynchronous and never enters the model request path.

## Trust boundary

- Only `ai-attribution-worker:<worker-id>` with the exact
  `ai-attribution:resolve` role can register policies and commit results.
- Policies are protected deployment configuration and are mounted only into
  the worker. The API and OTLP receiver do not receive them.
- A policy, usage record, result, event, database query, and actor are all bound
  to one explicit tenant.
- Prompt, response, message, tool, embedding, and raw payload data are neither
  required nor accepted.
- Application/team labels received from telemetry are not attribution inputs.

## Configuration

`IIP_AI_ATTRIBUTION_POLICIES_JSON` is a closed JSON wrapper:

```json
{
  "policies": [
    {
      "apiVersion": "iip.platform/v1alpha1",
      "kind": "AiAttributionPolicy",
      "metadata": {
        "id": "aap_22222222222222222222222222222222",
        "tenantId": "tenant-a",
        "version": "2026-09-05.1",
        "publishedAt": "2026-09-05T09:00:00Z"
      },
      "spec": {
        "source": {
          "kind": "operator-managed",
          "locator": "urn:customer:organization-catalog:2026-09-05.1",
          "retrievedAt": "2026-09-05T08:59:00Z",
          "contentHash": "sha256:2222222222222222222222222222222222222222222222222222222222222222"
        },
        "rules": [
          {
            "id": "support-production",
            "priority": 100,
            "match": {
              "serviceName": "support-assistant",
              "deploymentEnvironment": "production"
            },
            "allocation": {
              "application": {"id": "support-experience", "name": "Support Experience"},
              "team": {"id": "customer-experience", "name": "Customer Experience"}
            },
            "effectiveFrom": "2026-01-01T00:00:00Z"
          }
        ]
      }
    }
  ]
}
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `IIP_AI_ATTRIBUTION_ENABLED` | `false` | Enable protected attribution passes. |
| `IIP_AI_ATTRIBUTION_POLICIES_JSON` | required when enabled | One policy per worker tenant. |
| `IIP_AI_ATTRIBUTION_ALLOW_TEST_FIXTURES` | `false` | Permit policies marked `test-fixture`; never enable in production. |
| `IIP_AI_ATTRIBUTION_BATCH_SIZE` | `100` | Usage records per tenant/pass; 1–1,000. |
| `IIP_AI_ATTRIBUTION_INTERVAL_SECONDS` | `10` | Delay between passes; 1–3,600 seconds. |

Helm users set `aiAttribution.enabled`, store the wrapper in an existing
Secret, and set `aiAttribution.policiesExistingSecret`. The configured policy
tenants must exactly equal `worker.tenants`.

## Rollout and recovery

Apply database migrations through `0021_ai_attribution_ledger.sql` before
enabling the worker. Start with a policy that intentionally covers known
production services and monitor unallocated results. Publish ownership changes
as a new immutable policy ID/version; do not edit existing rows or policies.

The worker logs only aggregate processed, allocated, and unallocated counts.
A bad source record or forged mapping fails the transaction closed, while a
worker or telemetry outage never affects the customer's model call.

Roll back by restoring the previous Secret and image. Migration `0021` is
forward-only and does not require destructive database rollback. Existing
attribution facts remain immutable.

Verify locally with:

```bash
make verify PYTHON=.venv/bin/python
make test-postgres PYTHON=.venv/bin/python
make test-ai-finops PYTHON=.venv/bin/python
```
