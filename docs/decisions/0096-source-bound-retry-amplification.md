# ADR 0096: Detect retry amplification without inventing billed savings

**Status:** Accepted  
**Date:** 2026-09-05

## Context

AI provider instrumentation can report a retry count for a successful logical
operation, but attribute names and absence semantics differ by provider and
instrumentation version. A final successful span also does not prove that each
hidden retry attempt consumed billable tokens. Multiplying the final request
cost by the reported retry count would therefore create an unsupported saving.

## Decision

1. Protected OTLP channel configuration maps provider-specific request-ID and
   retry-count attributes into the existing provider-neutral invocation facts.
   A channel may declare that an absent mapped retry count means zero; otherwise
   absence remains unknown.
2. `retry-amplification` version `1.0.0` compares the share of successful
   operations with `retryCount > 0` in adjacent equal-duration windows. The
   protected profile fixes the exact scope, minimum cohort, absolute
   basis-point increase, minimum current rate, record ceiling, and grace.
3. Every record in both cohorts must carry a normalized retry count. Missing
   retry evidence makes the profile unsupported; it is never interpreted as
   zero by the evaluator.
4. A qualifying finding cites every immutable usage record and persists through
   the existing tenant-bound finding/event/outbox transaction. Persistence
   reloads and recalculates the complete source cohort before commit.
5. The finding's potential-saving status is `unresolved` with reason
   `retry-billing-unproven`. It contains no currency, rate, cost reference, or
   amount. The recommendation is to review throttling, timeouts, and retry
   policy and always requires validation.
6. OTLP reporting publishes bounded retry coverage, retrying operations, excess
   attempts, baseline/current rates, rate increase, rule status, and finding
   count. It does not duplicate usage/cost totals or publish a monetary saving.

## Consequences

The platform can identify a concrete operational regression while remaining
privacy-first and evidence-backed. Customers must qualify the configured
attribute mapping and zero-on-absence assertion for each instrumentation
profile. A future rule version may calculate retry cost only after telemetry or
billing evidence identifies the usage and price of each billable attempt.

