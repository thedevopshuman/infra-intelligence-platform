# AI economics telemetry contract

**Status:** v1alpha1 executable metrics profile

This contract defines the privacy-bounded OTLP metrics emitted from the
deterministic AI economics worker. The customer-controlled OpenTelemetry
Collector remains the replaceable routing boundary. These metrics are a
reporting projection of immutable IIP usage, cost, and finding records; they
are not the accounting ledger or an invoice.

## Snapshot boundary

One measurement represents the current window of one protected
`context-growth` profile after its window and grace period have ended. The
worker derives it from the same bounded cohort used by the rule and offers it
to the telemetry sink only after any qualifying finding has committed.

A separate allocation snapshot represents one tenant's complete bounded
rolling ledger report, projected once by protected application and once by
protected team. It uses the exact configured attribution-policy, price-catalog,
and engine generations. Replacing a snapshot zeros disappeared series for that
tenant only; it never changes another tenant's last-value series.

The source snapshot contains:

- successful request count and known input/output token totals;
- separate input/output meter coverage and incomplete-usage count;
- exact cost coverage counts for `priced`, `unpriced`, `ambiguous`, and
  `pending` records;
- calculated-estimate cost for the priced subset, when a single currency and
  scale exist;
- baseline/current input-token means and their signed basis-point change when
  both cohorts have complete input totals and a nonzero baseline; and
- zero or one committed context-growth finding and its potential saving.

If a profile exceeds its configured record ceiling, no partial aggregate is
exported as a window total. Missing cost is represented by coverage, never by
inventing zero cost. A cost amount is absent when no priced record establishes
its currency and scale.

## Instruments

All instruments are last-value gauges except
`iip.telemetry.record.failures`, which is a monotonic counter shared with other
platform telemetry sinks.

| Instrument | Unit | Meaning |
| --- | --- | --- |
| `iip.ai.usage.requests` | `{request}` | Successful invocations in the current profile window. |
| `iip.ai.usage.input_tokens` | `{token}` | Sum of known current-window input tokens. |
| `iip.ai.usage.output_tokens` | `{token}` | Sum of known current-window output tokens. |
| `iip.ai.usage.metered_requests` | `{request}` | Requests with the `input` or `output` meter present. |
| `iip.ai.usage.incomplete_requests` | `{request}` | Requests whose usage fact is incomplete. |
| `iip.ai.cost.requests` | `{request}` | Requests in each exact cost status. |
| `iip.ai.cost.amount` | `{currency-subunit}` | Calculated-estimate total for priced requests. |
| `iip.ai.usage.input_tokens_per_request` | `{token}/{request}` | Half-up mean for the `baseline` or `current` comparison window. |
| `iip.ai.context_growth.change` | `1` | Signed change in basis points from baseline to current mean. |
| `iip.ai.savings.profile_status` | `1` | One-hot deterministic evaluation status. |
| `iip.ai.savings.findings` | `{finding}` | Zero or one committed finding for the profile snapshot. |
| `iip.ai.savings.potential_amount` | `{currency-subunit}` | Calculated potential saving for a committed finding. |
| `iip.ai.allocation.requests` | `{request}` | Invocations grouped by protected application or team allocation. |
| `iip.ai.allocation.input_tokens` | `{token}` | Known input tokens in an allocation group. |
| `iip.ai.allocation.output_tokens` | `{token}` | Known output tokens in an allocation group. |
| `iip.ai.allocation.metered_requests` | `{request}` | Allocation-group requests with the `input` or `output` token total present. |
| `iip.ai.allocation.cost_requests` | `{request}` | Allocation-group requests in each exact cost status. |
| `iip.ai.allocation.cost_amount` | `{currency-subunit}` | Calculated-estimate priced cost for an allocation group. |

`iip.ai.cost.amount` is only the priced subset. Consumers must display its
four coverage gauges beside it and must label it as calculated cost, not
provider billing. Convert subunits for display with
`amount / 10^iip.ai.currency_scale`; do not change the stored/exported integer.
The allocation cost instrument is likewise absent when a group has no priced
records; its coverage series carries the unresolved state. A zero is emitted
only to retire a previously exported priced last-value series.

## Attribute allowlist

Every AI economics series has only these protected, profile-bounded scope
attributes:

- `iip.ai.profile.id`
- `gen_ai.provider.name`
- `gen_ai.response.model`
- `cloud.region`
- `service.name`
- `deployment.environment.name`
- `iip.tenant.id` when `tenant-scope` mode is selected

Individual instruments add only their declared closed dimensions:

| Attribute | Values |
| --- | --- |
| `iip.ai.usage.meter` | `input`, `output` |
| `iip.ai.cost.status` | `priced`, `unpriced`, `ambiguous`, `pending` |
| `iip.ai.comparison.window` | `baseline`, `current` |
| `iip.ai.savings.status` | `qualified`, `insufficient`, `unresolved`, `unsupported`, `below-threshold` |
| `iip.ai.savings.rule.id` | `context-growth` |
| `iip.ai.savings.rule.version` | `1.0.0` |
| `iip.ai.savings.severity` | `none`, `low`, `medium`, `high` |
| `iip.ai.currency` | Three-letter uppercase currency code from the catalog. |
| `iip.ai.currency_scale` | `6`, `9`, or `12` |
| `iip.ai.cost.basis` | `calculated-estimate` |
| `iip.ai.allocation.dimension` | `application`, `team` |
| `iip.ai.allocation.status` | `allocated`, `unallocated`, `pending` |
| `iip.ai.application.id` | Stable IDs from the protected attribution policy; present only for allocated application groups. |
| `iip.ai.team.id` | Stable IDs from the protected attribution policy; present only for allocated team groups. |

Profile and policy rule counts are bounded to 1,000 per configured tenant, and
allocation source reads are capped at 10,000 records, so label sets cannot
expand from arbitrary invocation attributes. Application/team display names
never become labels. Usage, cost, finding, trace, span,
request, and evidence IDs; timestamps; catalog IDs; quantities/rates inside
formulas; prompts; responses; messages; tool arguments; and raw payload fields
are prohibited as attributes.

## Tenant and failure semantics

`IIP_OTEL_AI_ECONOMICS_ATTRIBUTE_MODE=tenant-scope` is the default and keeps
series distinct when one worker explicitly serves multiple tenants. `scope`
omits the tenant label for a backend isolated to one tenant. This setting does
not grant cross-tenant query authority; the customer must independently route
and authorize backend access.

Recording is best effort. A rejected measurement increments
`iip.telemetry.record.failures` with instrument `ai-economics` or
`ai-allocation`. Collector or
backend delivery is covered by the existing metrics exporter-health path.
Neither failure changes cost/finding persistence, worker success, API
readiness, or customer inference.
