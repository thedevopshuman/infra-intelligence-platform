# ADR 0083: Calendar-aware seasonal telemetry baseline

**Status:** Accepted
**Date:** 2026-08-18

## Context

ADR 0076 anchors seasonal lookback windows at the accepted investigation
`scope.end` and shifts each window backward by exact elapsed
`periodSeconds`. That is reproducible and portable, but it explicitly left
"calendar-aware schedules" and "daylight-saving behavior" as revisit
triggers: a raw elapsed-seconds shift of one or more whole days does not
land on the same local wall-clock time of day once a daylight-saving
transition falls between the evaluation window and a lookback window. A
caller comparing "the same hour, one week ago" in a DST-observing region
gets a baseline window shifted by an extra or missing hour of real
operational activity around the transition, without any indication in the
report that this happened.

## Decision

Add two optional, additive fields to `seasonalBaselineComparison`:
`calendarAligned` (must be the literal `true` when present) and `timezone`
(an IANA zone name). Both are required together or omitted together; the
existing elapsed-seconds behavior is unchanged when they are absent, so
every existing `v1alpha1` request stays valid.

When `calendarAligned` is `true`, `periodSeconds` must be an exact multiple
of `86400` (one to seven whole days), and each lookback window is derived
by converting the anchor instant to local wall-clock time in `timezone`,
subtracting whole calendar days, and converting back to UTC — using
Python's standard `zoneinfo` database rather than a vendor calendar
library. A local time that becomes ambiguous across a fall-back transition
resolves to its earlier UTC instant; a local time that falls in a
spring-forward gap resolves per the interpreter's standard
pre-transition-offset behavior. Both outcomes are deterministic and
reproduce identically from the same request and system tzdata.

`timezone` is validated against the closed set of names the runtime's
installed IANA database actually exposes, never treated as a file path or
passed to a vendor scheduling service. The pinned `tzdata` PyPI package
gives every deployment the same database regardless of the host OS's own
timezone data, keeping the calculation reproducible across environments.

The committed report always echoes `calendarAligned` and `timezone`
(`null` when not calendar-aligned) next to the existing period parameters,
so the exact anchor, timezone, and derived absolute ranges remain
sufficient to reproduce the comparison without re-deriving which shift
rule applied.

Protected investigation-signal catalogs still cannot generate seasonal
rules of either kind; ADR 0076's request-scoped-only boundary is
unchanged.

## Consequences

- a caller can ask for "the same local hour, N days or weeks ago" in a
  named region and get a window that tracks daylight-saving transitions
  instead of silently drifting by an hour;
- the two-field co-requirement and the multiple-of-a-day bound on
  `periodSeconds` keep calendar alignment an explicit, self-describing
  choice rather than a hidden default;
- the report's `calendarAligned`/`timezone` echo keeps every derived
  window reproducible from the committed artifact alone;
- calendar alignment is bounded to whole days within the existing one
  hour to one week `periodSeconds` range; sub-day calendar alignment,
  month/quarter periods, and learned or statistically fitted baselines
  remain out of scope.
