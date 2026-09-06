# Private-pilot feedback and telemetry

**Status:** Accepted privacy-first operating boundary

**Date:** 2026-09-06

## Default behavior

IIP does not phone home. It emits bounded OpenTelemetry signals only to the
customer-selected Collector or compatible endpoint, and those signals remain
under customer routing and retention policy. No prompt, response, raw evidence,
tenant/resource identity, provider request ID, credential, endpoint, or
customer cost amount is sent to the project automatically.

The pilot may succeed without sharing telemetry with the project. Any sharing
requires explicit customer agreement on purpose, recipients, fields,
retention, deletion, and whether a value can be quoted outside the pilot.

## Weekly customer-owned scorecard

Use existing platform reports and the customer's telemetry backend; do not add
a second analytics agent or export path. Review these categories:

| Category | Preferred source | Minimum interpretation |
| --- | --- | --- |
| Release identity | Runtime version and release manifest | Exact candidate still deployed |
| API/receiver health | `iip.query.*`, `iip.otlp.receiver.*` | Availability and latency against the pilot objective |
| Export path | Export-health, SLO, burn-rate, and Collector queue/loss reports | Local success is not mistaken for backend delivery |
| Resource/investigation value | Freshness and investigation-completion reports | Chosen scope is current and reports complete |
| AI coverage | `iip.ai.usage.*`, attribution, and cost status metrics | Unknown/unallocated/unpriced work remains visible |
| Savings quality | Evidence-backed finding and reviewer disposition | A potential saving is not reported as realized |
| Operations | Minimized diagnostic and support log | Operator effort, incidents, rollback, and unresolved risks |

Raw monetary values, team/application names, model names, resource names, and
free-form user feedback remain customer-confidential unless the pilot agreement
explicitly includes them. Prefer basis points, bounded counts, stable result
codes, and report digests when aggregate comparison is enough.

## Feedback record

For each reviewed observation, the customer-owned pilot log should record:

- UTC period and exact release revision;
- category and stable result code;
- the SHA-256 digest of the supporting minimized report, when applicable;
- disposition: `accepted`, `needs-product-change`, `configuration`,
  `external-dependency`, or `not-actionable`;
- owner, target review date, and whether sharing was approved;
- a separate private reference for sensitive details, never copied into Git.

This is an operating record, not a new public API contract. Do not place the
pilot log or customer observations in this repository.

## Exit review

At the pilot end, the customer and platform owners decide whether to expand,
repeat, pause, or decommission. Record whether the agreed product questions
were answered, which evidence was reproducible, which unknowns remain, and
whether data deletion and credential revocation completed. A positive review
does not promote the build to public or production readiness; those gates stay
independent.
