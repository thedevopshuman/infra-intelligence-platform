# AI invocation observation contracts

**Status:** v1alpha1  
**Decision:** [ADR 0141](../decisions/0141-privacy-minimized-exact-ai-invocation-observation.md)

These contracts support a narrow qualification operation: determine whether
one exact tenant-bound GenAI span has reached the immutable usage ledger and
the active attribution and calculated-cost generations. They are not a trace
search API, billing API, or dashboard query contract.

## `AiEconomicsInvocationObservationRequest`

The closed request contains one lowercase 32-hex trace ID and one lowercase
16-hex span ID. It is sent only in the JSON body of:

```text
POST /v1/operations/ai-economics/invocation-observations
```

The authenticated credential supplies tenant, actor, and roles. The request
must not contain tenant or source selection. The service requires
`platform-admin` and a positive `ai-economics:qualify` decision. Deployments
should treat request bodies for this operation as sensitive and exclude them
from access logs.

## `AiEconomicsInvocationObservation`

The response never returns the trace or span ID. `correlationDigest` is the
SHA-256 digest of their canonical tenant-bound tuple. It is useful only for
joining minimized qualification evidence; it grants no query authority.

The service reports the exact active attribution and pricing sources selected
by protected deployment configuration. Each source includes both its declared
source hash and the canonical digest of the complete policy/catalog document,
so offline qualification can bind the deployed generation to reviewed input.
Stage semantics are:

| Overall status | Meaning |
| --- | --- |
| `not-observed` | Neither matching live usage nor a retirement marker exists in the authenticated tenant. Attribution and cost remain pending. |
| `processing` | Usage exists, but one or both active downstream records are not committed yet. |
| `complete` | Usage and both downstream stages are terminal. Attribution may be `allocated` or `unallocated`; cost may be `priced`, `unpriced`, or `ambiguous`. |

Every present immutable record has its public stable ID and canonical document
digest. A priced result contains integer subunits under the selected currency,
scale, and fixed `calculated-estimate` basis. It is never an invoice amount.
Stored facts are revalidated against the authenticated tenant, usage record,
engine version, and active source identity before they are returned.

A recorded whole-invocation retirement takes precedence over any still-present
payload. The same storage snapshot checks the canonical tenant-bound
trace/span correlation digest before reading live stages. A matching marker
returns `410 ai.history.retired`, never `not-observed`, `processing`, or a
partial `complete` result. Successful v1alpha1 envelopes remain unchanged.
The separate [history availability report](ai-history-availability-contract.md)
reports interval-level counts; it neither grants exact-invocation authority
nor authorizes a later read. This boundary adds no physical payload deletion
or production marker writer.

Unknown fields, crossed contract kinds, malformed identifiers, caller-supplied
source generations, duplicate trace/span records, or invalid stored joins fail
closed with stable external error codes.

## `BedrockInvocationCorrelation`

The optional live Bedrock delivery mode writes an ephemeral owner-only
correlation file after constructing the same span sent by its asynchronous
OTLP/HTTP exporter. It binds trace and span IDs to the selected delivery
endpoint digest, source revision, dirty state, generation time, and
`contentCaptured: false`.

This operational artifact is deliberately excluded from public SDK types and
transportable evidence. Protect it as a credential-adjacent mode-`0600` file,
never check it into source control, never send it to Grafana or Prometheus, and
delete it after the live qualification has produced minimized evidence.

## Machine contracts

- `contracts/schemas/ai-economics-invocation-observation-request.schema.json`
- `contracts/schemas/ai-economics-invocation-observation.schema.json`
- `contracts/schemas/bedrock-invocation-correlation.schema.json`
- `contracts/examples/ai-economics-invocation-observation-request.json`
- `contracts/examples/ai-economics-invocation-observation.json`
- `contracts/examples/bedrock-invocation-correlation.json`
