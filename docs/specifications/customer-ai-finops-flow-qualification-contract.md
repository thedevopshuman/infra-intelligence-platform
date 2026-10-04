# Customer AI FinOps flow qualification contracts

**Status:** v1alpha1
**Decision:** [ADR 0142](../decisions/0142-customer-ai-finops-same-invocation-qualification.md)

These contracts qualify one exact, live AWS Bedrock invocation across the
selected customer OTLP route, IIP usage ledger, active attribution and price
generations, Prometheus aggregate, and provisioned Grafana dashboard. They are
operational evidence contracts, not serving APIs, billing records, or a general
trace-search interface.

## `CustomerAiFinopsFlowQualificationProfile`

The profile is reviewed, protected input. It selects:

- one exact clean release and immutable image;
- the canonical prerequisite-profile digest and raw prerequisite-report file
  digest;
- the canonical Bedrock qualification-profile digest;
- fixed `aws.bedrock` / `ConverseStream` / direct-to-provider /
  asynchronous-OTel / metadata-only semantics;
- the expected protected application and team;
- verified-HTTPS control-plane, OTLP trace, Prometheus, and Grafana targets;
- the `iip-ai-finops` Grafana dashboard; and
- closed freshness, timeout, polling, and validity objectives.

The selected control-plane identity still comes from its Bearer credential.
The profile cannot supply a tenant to the serving API or select attribution or
pricing generations. Those remain protected deployment configuration.

## `CustomerAiFinopsFlowRunEvidence`

The run evidence is temporary, mode-`0600`, non-transportable input to offline
verification. It contains the raw trace/span pair, tenant-bound correlation
digest, OTLP delivery-target digest, exact live-report and observation digests,
record IDs/digests, active source-document digests, protected application/team,
calculated cost, aggregate before/after counts, dashboard digest/panel count,
timing, and fixed privacy outcomes.

This document is credential-adjacent because it contains exact correlation and
customer attribution. It is intentionally absent from public SDK types. Never
check it into source control, copy it into Grafana/Prometheus, or retain it as
portable release evidence.

## `CustomerAiFinopsFlowQualificationReport`

The report is the transportable minimized result. `qualified` requires all 15
ordered checks:

1. source binding;
2. reviewed profile freshness;
3. exact prerequisite binding and freshness;
4. exact Bedrock profile binding;
5. protected input handling;
6. metadata-only direct request path;
7. one live provider call;
8. OTLP delivery correlation;
9. exact usage record;
10. expected active attribution;
11. exact active production pricing;
12. bounded processing latency;
13. protected-application Prometheus aggregate delta;
14. required Grafana dashboard panels; and
15. minimized output.

The output retains both record digests and active attribution/catalog document
digests. A priced result is still `calculated-estimate`. It does not establish
AWS invoice agreement or the effect of private rates, discounts, commitments,
tax, or credits.

Current reports bind `deploymentProfile` to `production-ai-finops-v1`. The
transport schema and SDK type keep the historical v0 value readable, but the
live qualifier requires a current v1 prerequisite report and will not admit a
v0 report into a new run.

## Digest rules

Canonical document digests use SHA-256 over UTF-8 JSON with object keys sorted,
ASCII escaping, and separators `,` and `:` without added whitespace. Target
binding digests use the same canonical encoding of the complete URL string.
The prerequisite report uses a raw-file SHA-256 so offline verification binds
the exact retained bytes. The Bedrock delivery correlation retains the raw URL
string SHA-256 defined by ADR 0141; the report separately retains the canonical
target-binding digest.

Report identity is the first 32 hexadecimal characters of SHA-256 over the
canonical `{metadata, spec}` document before `metadata.id` is inserted.

## Failure and authority semantics

Qualification is disabled unless the caller supplies the explicit live-call
flag. The script rejects dirty or mismatched source, stale evidence, non-HTTPS
or structurally unsafe URLs, redirect behavior, overlapping protected/output
paths, non-owner-only secret material, crossed tenant/catalog/profile facts,
incomplete downstream processing, missing aggregate change, and altered
retained evidence. Failures expose only stable `customer-ai-finops-flow.*`
codes.

The control-plane observation itself requires `platform-admin` plus the
`ai-economics:qualify` policy action. The operational harness receives no
approval, mutation, agent, or plugin authority.

## Machine contracts

- `contracts/schemas/customer-ai-finops-flow-qualification-profile.schema.json`
- `contracts/schemas/customer-ai-finops-flow-run-evidence.schema.json`
- `contracts/schemas/customer-ai-finops-flow-qualification-report.schema.json`
- `contracts/examples/customer-ai-finops-flow-qualification-profile.json`
- `contracts/examples/customer-ai-finops-flow-run-evidence.json`
- `contracts/examples/customer-ai-finops-flow-qualification-report.json`
