# Support policy

Infrastructure Intelligence Platform is pre-release software. Community use is
best-effort, and a private design-partner pilot requires named customer and
platform owners plus an agreed private support path before installation. This
repository does not yet publish a staffed support address or response-time
commitment.

## Where to report a problem

- Use a public repository discussion or issue only for reproducible,
  non-sensitive product defects and documentation questions after a repository
  host is selected.
- Use the separately agreed private pilot channel for customer-environment
  incidents, diagnostic artifacts, operational identifiers, or commercially
  sensitive usage and cost observations.
- Follow [SECURITY.md](SECURITY.md) for a suspected vulnerability. Never place
  exploit details, credentials, prompts, customer data, infrastructure
  identifiers, or private endpoints in a public issue.

If no private channel and accountable owner have been agreed, do not send
sensitive material and do not start a customer pilot. Repository maintainers
must record the chosen channel, participants, escalation path, service hours,
and response objectives in the customer-owned pilot plan; those values are not
portable product defaults.

## A useful support packet

Start with the privacy-minimized
[deployment diagnostic report](docs/operations/deployment-diagnostics.md), then
provide only the information authorized for the selected support channel:

- exact application, chart, image-digest, and source-revision identity;
- UTC start/end time and the affected operation;
- stable IIP error codes and aggregate symptoms;
- relevant qualification-report filenames and SHA-256 digests;
- whether rollback or decommissioning has already started.

Do not attach Secret values, bearer tokens, certificates or keys, prompts,
responses, raw logs, OTLP payloads, provider request identifiers, tenant or
resource names, database dumps, or protected qualification profiles. The
[private-pilot onboarding guide](docs/operations/private-pilot-onboarding.md)
defines the triage, rollback, evidence, and exit workflow.

## Support boundary

The current private-pilot scope is defined in
[the first usable release](docs/product/private-pilot-v1.md). A successful
customer readiness report means the exact candidate passed its declared
preflight; it is not an SLA, production certification, or acceptance decision.
Long-window availability, automatic failover, multi-region operation, provider
billing reconciliation, and public support remain explicit external gates.
