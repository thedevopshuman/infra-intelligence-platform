# ADR 0092: Exact Bedrock instrumentation compatibility profiles

**Status:** Accepted

**Date:** 2026-09-05

## Context

The synthetic Phase A flow used the newer `gen_ai.provider.name` attribute and
the generic botocore instrumentation scope. The current official Python
botocore instrumentation instead emits `gen_ai.system=aws.bedrock` and the
service-specific scope
`opentelemetry.instrumentation.botocore.bedrock-runtime`. It also emits provider
input/output token totals but not the cache and reasoning breakdowns required
to prove exact pricing for every Bedrock capability.

A hand-authored OTLP span cannot prove SDK interoperability or that a failed
telemetry exporter leaves the model result unchanged. Conversely, calling AWS
from a default verification target would require credentials, incur spend,
and make local and CI verification non-deterministic.

## Decision

Add a source-bound compatibility report and two explicit qualification levels:

1. the default offline gate runs the pinned boto3 client and official botocore
   instrumentation for both `Converse` and `ConverseStream` against
   `botocore.stub.Stubber` in a no-network container; the streaming fixture is
   injected through a scoped public botocore event handler as an actual
   `EventStream` after Stubber response-shape validation;
2. the opt-in live gate makes one bounded explicitly selected operation only
   when the operator supplies an enable flag, model, region, and short-lived
   AWS credentials;
3. both levels keep message-content capture disabled, capture the actual span,
   pass its encoded OTLP payload through the real IIP receiver adapter, and
   prove an asynchronous exporter failure does not alter the provider result;
4. the receiver accepts either `gen_ai.provider.name` or legacy
   `gen_ai.system`, accepts equal dual publication, and rejects conflicts;
5. the official service-specific instrumentation scope must be explicitly
   allowlisted by the protected channel configuration; and
6. absent cache/reasoning breakdowns remain missing. They are never converted
   to zero without a separately qualified model/billing profile; and
7. `ConverseStream` compatibility requires complete stream consumption, a
   final metadata event with provider totals, and proof that the official span
   did not finish before consumption.

## Consequences

- The standard Python auto-instrumentation route is executable without adding
  an IIP application SDK or placing IIP in the inference path.
- Offline evidence is reproducible and safe but does not claim a live AWS
  qualification.
- A live report is model-, region-, and operation-specific. Streaming is
  claimed only by a `ConverseStream` report; neither operation proves invoice
  agreement, customer PKI/Collector behavior, or production load.
- Cost calculation for this conservative upstream profile stays unresolved
  until a model-specific breakdown policy or richer standard instrumentation
  is qualified.
- Future semantic-convention migrations remain adapter concerns and must add
  conflict tests rather than silently changing normalized domain records.
