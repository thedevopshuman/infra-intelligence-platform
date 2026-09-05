# Evidence redaction policy contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/evidence-redaction-policy.schema.json`

`EvidenceRedactionPolicy` lets an operator add reviewed privacy detectors for
one exact tenant and evidence type. It extends the mandatory credential
redaction baseline; it cannot disable, replace, or weaken that baseline.

## Identity and tenancy

The `erp_` identifier is the first 32 hexadecimal characters of the canonical
SHA-256 digest over `tenantId`, policy `version`, and `spec`. Any rule change
therefore requires a new ID. A runtime accepts at most one policy per tenant,
requires policy documents to be ordered by tenant, and never falls back from
one tenant to another.

The policy set is protected startup configuration, not a public request. An
invalid document, duplicate tenant, ambiguous evidence-type rule, unknown
field, unsupported detector, or non-canonical ordering fails process startup.
When no policy exists for a tenant, the mandatory credential detectors remain
active.

## Additive rules

Each rule has a stable ID, one or more exact evidence types, and one or more
built-in value classes. Rules and their arrays are lexically sorted and unique.
An evidence type can occur in only one rule, avoiding precedence or union
ambiguity. Wildcards, caller-selected policy IDs, custom regular expressions,
replacement strings, and literal secret values are not accepted.

The first classes are:

| Value class | Behavior |
| --- | --- |
| `email-address` | Replaces a bounded email-shaped value with `[REDACTED EMAIL]`. |
| `ipv4-address` | Replaces a dotted-quad only when all four decimal octets are between 0 and 255. |

Detectors operate on decoded JSON string values and UTF-8 text. Structured
credential fields, secret-like assignments, Bearer credentials, and private
keys are always inspected first by the non-configurable baseline. Binary and
unsupported media types continue to fail closed.

## Evidence provenance

When a tenant policy is selected, `Evidence.spec.handling.redaction.policyRef`
records only its content-derived ID and reviewed version. The policy reference
is present even when no optional detector matched, distinguishing “evaluated
under this policy” from “no tenant policy configured.” Applied detector method
names remain in `redaction.methods`; raw matched values and rule details never
enter the Evidence envelope.

Artifact hashing and immutable persistence occur after all redaction. A policy
change never rewrites existing Evidence. New retrieval produces new redacted
bytes, hash, Evidence identity, and policy reference.

## Boundaries

This contract is a bounded privacy control, not a data-classification engine or
proof that every sensitive value was discovered. Email and IPv4 removal can
also remove operationally useful evidence, so customers scope them to exact
evidence types and evaluate investigation quality. Data minimization at the
Collector/source, tenant-scoped access, retention, residency, and deletion
remain independent controls.
