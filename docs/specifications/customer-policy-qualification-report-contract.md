# Customer policy qualification contracts

**Status:** `v1alpha1` executable customer-environment evidence

**Decision:** [ADR 0132](../decisions/0132-customer-policy-engine-bundle-qualification.md)

**Machine contracts:**
`contracts/schemas/customer-policy-qualification-profile.schema.json` and
`contracts/schemas/customer-policy-qualification-report.schema.json`

These contracts qualify selected allow and deny behavior from one customer
policy endpoint and immutable bundle through IIP's production adapter. They do
not model or install a provider policy bundle and are not served by the
control-plane API.

## Protected profile

`CustomerPolicyQualificationProfile` is a mode-`0600` operator input. It binds
one exact HTTPS decision endpoint, tenant, actor, sorted role set, and two to 64
ordered cases. Every case contains the exact application action, tenant-scoped
resource input, and expected allowed flag, stable reason code, and immutable
tenant snapshot reference.

The profile requires at least one allow and one deny case. Case identifiers are
unique and ordered, resource values are bounded JSON, credential-like resource
keys are rejected, and every expectation must identify the same snapshot. An
expected `policy.unavailable`, input error, or synthetic unavailable snapshot
is prohibited because transport failure cannot be accepted as policy intent.

The separately protected bearer file is read by the production adapter for
every request. The CA bundle and immutable image digest are also separate
inputs. The committed example is documentation only; copy it outside the
checkout, replace its values, and protect it before qualification.

## Minimized report

`CustomerPolicyQualificationReport` retains:

- exact application, contract, source-revision, and immutable-image identity;
- SHA-256 bindings for the endpoint, complete protected profile, ordered case
  set, and selected snapshot set;
- only reviewed/start/completion times, case and outcome counts, request count,
  and maximum decision latency;
- fixed CA-verified, no-redirect, no-proxy, bounded-client behavior;
- fourteen ordered checks and a derived summary; and
- four mandatory limitations covering HA/network failover, credential
  lifecycle, bundle governance/break-glass, and audit/SIEM delivery.

The `cpq_` identifier is content-derived. Semantic validation recomputes check
error codes, summary, status, time and count relationships, fixed always-pass
safety checks, latency/profile-age outcomes, and report identity. Offline
verification additionally recomputes every digest from the protected inputs.

The report rejects fields named for endpoint URLs, tenant, actor, roles,
actions, resources, decisions, reasons, snapshots, credentials, or tokens. Raw
requests, responses, provider errors, and bearer-token digests are never
retained.

## Qualification boundary

`qualified` means the production adapter obtained every reviewed exact allow or
deny decision from one tenant-bound immutable snapshot, repeated the first case
consistently, and remained within the declared latency objective. A released
decision could only reach comparison after the adapter had verified the
response's canonical input digest, tenant, closed shape, and snapshot syntax.

This result covers only the selected cases and observation window. It does not
prove all IIP actions, every role/resource combination, organizational bundle
approval, emergency access, engine or network HA, credential revocation, or
audit retention. Those outcomes require separate customer evidence.

Python and TypeScript SDKs expose the protected and minimized shapes for release
tooling. They do not load profiles, send credentials, evaluate policy, or grant
authority.
