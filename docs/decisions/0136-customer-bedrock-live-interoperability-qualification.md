# ADR 0136: Customer Bedrock live interoperability qualification

**Status:** Accepted

**Date:** 2026-09-08

## Context

ADR 0092 provides reproducible offline evidence for the pinned official Python
botocore instrumentation and an opt-in live call. The original live wrapper
accepted AWS access-key material through container environment variables and
produced a compatibility report without a protected reviewed target, evidence
expiry, or immutable application-image binding. It therefore could not satisfy
the customer `live-bedrock-model-region-streaming` prerequisite by itself.

A successful call also cannot prove that the calling role contains only the
minimum IAM authority. Amazon Bedrock maps `Converse` to
`bedrock:InvokeModel` and `ConverseStream` to
`bedrock:InvokeModelWithResponseStream`, but inference profiles, provisioned
throughput, guardrails, or other target types can require additional authority.
The qualification must not turn a successful request into a least-privilege or
billing claim.

## Decision

1. Add protected `CustomerBedrockQualificationProfile` and minimized,
   expiry-bound `CustomerBedrockQualificationReport` contracts for one exact
   environment, model, region, operation, application version, and immutable
   image digest.
2. Keep the official pinned boto3/botocore and OpenTelemetry dependencies in
   the isolated compatibility image. Do not add an IIP inference SDK or put IIP
   in the application-to-Bedrock request path.
3. Require an explicit provider-call enable flag and a dedicated, current-user
   owned mode-`0600` AWS shared-credentials file containing exactly one named
   profile and exactly `aws_access_key_id`, `aws_secret_access_key`, and
   `aws_session_token`. Requiring the session-token field rejects long-lived
   two-part access keys but does not prove the credential's actual expiry.
4. Mount only that file read-only into the disposable container. Do not pass
   AWS credential values as environment variables, mount a host `.aws`
   directory, query instance metadata, accept configured endpoint overrides,
   inherit a proxy, or retain a credential digest.
5. Continue using one fixed, non-sensitive synthetic request with content
   capture disabled. Bound botocore connect/read timeouts, disable automatic
   retry attempts for the qualification call, and measure the provider call
   against the reviewed objective.
6. Require the live compatibility evidence to match the current clean source,
   pinned dependency versions, application version, model, region, and
   operation. `ConverseStream` additionally requires complete stream
   consumption before the span finishes.
7. Retain the exact model, region, and environment only in the protected
   profile and protected underlying compatibility report. The transportable
   report retains canonical digests, operation class, aggregate timing/counts,
   closed checks, validity, and explicit limitations.
8. Keep customer Collector/PKI delivery and authoritative catalog
   qualification as separate evidence. Missing cache/reasoning meters remain
   partial and cannot become exact cost merely because the provider call
   succeeds.

## Consequences

An operator can now run and later rebind one live Bedrock interoperability
observation without exposing credentials through `docker inspect` or
overstating what the observation proves. The command incurs one provider call
and is intentionally excluded from `make verify`.

Repository code now supplies the customer qualification mechanism, but a
particular customer model/region/operation remains externally required until
the operator runs it with an approved target and temporary credential. IAM
least authority, credential revocation, model quality/safety, authoritative
prices, customer Collector delivery, sustained load, quotas, and regional
availability remain separate gates.
