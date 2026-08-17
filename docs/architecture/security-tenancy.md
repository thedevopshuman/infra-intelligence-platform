# Security, tenancy, and authority

**Status:** Accepted baseline  
**Date:** 2026-08-14

## Trust boundaries

Untrusted inputs include external events, resource attributes, logs, traces, runbooks, repository text, plugin output, model output, user prompts, webhook metadata, and provider error messages. Normalize and label their provenance before use.

## Tenant invariant

The authenticated gateway establishes tenant and actor context. Payload tenant fields are consistency assertions, never the authority source. Every repository key, event partition, cache entry, credential lookup, tool call, evidence record, and audit record includes tenant scope.

Cross-tenant operations are a separate privileged use case with explicit policy and audit; they are not implemented by omitting the tenant predicate.

Asynchronous investigation admission is tenant-local and bounded twice: non-terminal backlog at enqueue and unexpired live leases at worker claim. Both PostgreSQL checks serialize only the exact tenant, preserve idempotent submission, and return value-minimized stable errors. Capacity configuration never grants scope or reveals another tenant's demand.

The HTTP surface follows the [authentication boundary](authentication-boundary.md): a replaceable authenticator verifies a Bearer credential and derives actor, tenant, and roles before any protected use case runs. Caller-controlled identity headers are ignored. The hashed opaque-token adapter is restricted to local development and tests; deployed profiles can verify issuer- and audience-bound OIDC JWTs against an explicitly trusted HTTPS JWKS endpoint.

## Authority levels

| Level | May do | Default approval |
| --- | --- | --- |
| Read | Query scoped resources, events, evidence, and provider state | None after policy allow |
| Propose | Produce a structured action plan without side effects | None after policy allow |
| Approve | Approve a specific immutable proposal | Human or delegated policy principal |
| Execute | Perform the exact approved, idempotent action | Policy re-check immediately before execution |

Execution authority is consumed by a durable one-shot claim before an adapter receives a request. A lost process cannot turn an uncertain outcome into an automatic retry: after lease expiry, the attempt requires manual provider-state reconciliation. The claim records content digests and identities, never execution credentials.

The Kubernetes restart adapter additionally requires an observed provider UID and resource version, a tenant/integration namespace-kind allowlist, server-side dry-run, and a brokered `resources:read` + `workloads:patch` lease. Live execution needs two independent switches: an immutable non-dry-run proposal and protected integration enablement. It cannot submit arbitrary paths or patch bodies.

Agents and plugins declare a maximum authority; request policy may reduce but never increase it.

Deployed profiles may delegate each exact decision through the [external policy boundary](authorization-policy.md). The external decision cannot change authenticated identity or request scope. A malformed, unavailable, or wrong-tenant result denies closed, and governed actions retain the returned immutable policy snapshot reference.

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

The offline evaluation contract identifies instruction-shaped fixture evidence and prohibited output fragments. Release scoring fails an `instruction-boundary` hard gate if those fragments are copied into a report; root-cause, evidence, least-privilege, and budget gates independently catch behavioral diversion. Live context and log tests keep malicious text inside the protected Evidence artifact while verifying that classification and report output remain unchanged. [ADR 0033](../decisions/0033-adversarial-evidence-release-gate.md) records this defense-in-depth gate.

Log bodies receive confidential handling by default and remain untrusted after redaction: retrieved or pushed text cannot grant authority or become instructions. OTLP channel credentials are independent of interactive control-plane identities, and channel configuration—not payload resource attributes—fixes tenant, integration, resource, service catalog, retention, and admission limits.

## Plugin threats

Plugins are signed artifacts with immutable version and digest, explicit network/secret/resource/action permissions, protocol compatibility, resource limits, and an audit identity. Default execution is out-of-process. A crash, timeout, or malformed response fails the capability closed without destabilizing the control plane.

## Build and CI authority

CI has read-only repository permission, does not persist checkout credentials, and runs within a fixed deadline on an explicit runner generation. Third-party Actions are referenced by reviewed commit SHA and service containers by OCI digest; comments retain human-readable release versions. Automated dependency checks may propose pull requests but never bypass review or verification. Release publication and signing require separate organizational identities that are not present in this repository.

## Audit minimum

Record actor, tenant, action, target, request/correlation/causation IDs, manifest and plugin versions, policy input hash and decision, tool input/output hashes, evidence references, approval identity, idempotency key, result, and timestamps. Never record raw secrets.
