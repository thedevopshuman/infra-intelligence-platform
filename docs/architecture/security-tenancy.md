# Security, tenancy, and authority

**Status:** Accepted baseline  
**Date:** 2026-08-14

## Trust boundaries

Untrusted inputs include external events, resource attributes, logs, traces, runbooks, repository text, plugin output, model output, user prompts, webhook metadata, and provider error messages. Normalize and label their provenance before use.

## Tenant invariant

The authenticated gateway establishes tenant and actor context. Payload tenant fields are consistency assertions, never the authority source. Every repository key, event partition, cache entry, credential lookup, tool call, evidence record, and audit record includes tenant scope.

Cross-tenant operations are a separate privileged use case with explicit policy and audit; they are not implemented by omitting the tenant predicate.

The Phase 1 HTTP surface follows the [authentication boundary](authentication-boundary.md): a replaceable authenticator verifies a Bearer credential and derives actor, tenant, and roles before any protected use case runs. Caller-controlled identity headers are ignored. The hashed opaque-token adapter is restricted to local development and tests; production identity-provider selection remains open.

## Authority levels

| Level | May do | Default approval |
| --- | --- | --- |
| Read | Query scoped resources, events, evidence, and provider state | None after policy allow |
| Propose | Produce a structured action plan without side effects | None after policy allow |
| Approve | Approve a specific immutable proposal | Human or delegated policy principal |
| Execute | Perform the exact approved, idempotent action | Policy re-check immediately before execution |

Agents and plugins declare a maximum authority; request policy may reduce but never increase it.

## Credential model

- Manifests request logical secret names and narrow scopes, not secret values.
- A broker issues short-lived credentials at tool execution time where providers support it.
- Credential material stays outside model context, events, evidence, logs, and plugin configuration.
- Revocation and rotation do not require rewriting manifests.
- Plugin credentials are isolated from the control-plane process.

The Prometheus and Kubernetes Event evidence adapters implement the `CredentialBroker` lease boundary. Each supplies authenticated tenant, actor, integration, provider, scope, reference, and deadline context; the returned bearer lease stays inside the adapter and has a redacted representation. Static protected-JSON brokers remain local-only. The optional external HTTPS client authenticates with explicitly projected workload identity, validates short lease lifetime and request correlation, and is shared across adapters. The external issuer remains responsible for independent workload/tenant/scope policy, issuance, rotation, revocation, and secret-free audit; process/plugin isolation remains deployment work.

## Agent and prompt threats

- Retrieved content cannot alter system policy, tool allowlists, budgets, or approval requirements.
- Tool descriptions and output are schema-validated and size-limited.
- High-impact parameters are produced as structured data and evaluated by policy.
- Model output is advisory until a deterministic application boundary accepts it.
- Data sent to a model follows tenant policy for residency, retention, provider, and redaction.

## Plugin threats

Plugins are signed artifacts with immutable version and digest, explicit network/secret/resource/action permissions, protocol compatibility, resource limits, and an audit identity. Default execution is out-of-process. A crash, timeout, or malformed response fails the capability closed without destabilizing the control plane.

## Audit minimum

Record actor, tenant, action, target, request/correlation/causation IDs, manifest and plugin versions, policy input hash and decision, tool input/output hashes, evidence references, approval identity, idempotency key, result, and timestamps. Never record raw secrets.
