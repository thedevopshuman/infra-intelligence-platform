# Agent contract

**Status:** v1alpha1 manifest; runtime protocol pending  
**Machine contract:** `contracts/schemas/agent-manifest.schema.json`

An agent is a versioned reasoning capability, not a free-form prompt. The manifest makes selection, authority, evaluation, and operation deterministic around a potentially non-deterministic model.

## Manifest

- Immutable `id` and semantic `version`.
- One clear purpose.
- Runtime protocol and entrypoint.
- Named, schema-addressed inputs and outputs.
- Explicit tool allowlist.
- Maximum authority and resource scope expression.
- Tool-call, wall-time, and cost budgets.
- Model class selected through policy, not a hard-coded provider credential.

## Runtime request

The eventual runtime request will include tenant/actor context, investigation ID, immutable manifest digest, input values, resource/time scope, resolved tool capabilities, budget, policy snapshot reference, and correlation/trace context. Credentials are resolved only inside tool execution and never placed in the request.

## Runtime result

Agents return structured output plus:

- completion status and terminal reason;
- hypotheses with confidence and supporting/contradicting evidence IDs;
- explicit unknowns and evidence gaps;
- recommendations and separately typed action proposals;
- tool-call ledger references;
- cost, time, and budget utilization;
- model/runtime identifiers needed for evaluation and audit.

Text narration may be derived from the structured result. It is not the authoritative result.

## Lifecycle and safety

The registry validates and stores an immutable manifest release. At execution, the runtime intersects manifest capabilities with tenant policy, actor authority, and request scope. Tools enforce scope independently. The runtime stops on budget, cancellation, stagnation, policy denial, or sufficient result.

Agents cannot approve their own proposals. An `execute` authority manifest still requires an immutable proposal, current policy decision, idempotency key, and audit trail.

## Evaluation

An agent release is promoted with versioned [evaluation scenario](evaluation-scenario-contract.md) results covering root-cause class, required and forbidden evidence, red herrings, unsupported certainty, tool efficiency, latency, cost, policy compliance, and stability across repeated runs.
