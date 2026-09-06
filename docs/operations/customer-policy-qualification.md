# Customer policy-engine bundle qualification

**Status:** External customer-environment gate

**Decision:** [ADR 0132](../decisions/0132-customer-policy-engine-bundle-qualification.md)

**Contract:** [Customer policy qualification contracts](../specifications/customer-policy-qualification-report-contract.md)

This runbook sends reviewed, tenant-scoped allow and deny cases directly to one
customer policy endpoint through IIP's production adapter. It proves selected
bundle behavior without installing, reading, or changing the bundle and
without retaining authorization inputs or decisions.

## Prepare

1. Run the local adapter regression first with
   `make test-policy-engine PYTHON=.venv/bin/python`.
2. Copy `contracts/examples/customer-policy-qualification-profile.json`
   outside the repository. Replace the endpoint, identity, cases, snapshot,
   objectives, and review time. Include at least one reviewed allow and one
   reviewed deny case. Set mode `0600`.
3. Write the service bearer token plus an optional final newline to a separate
   mode-`0600` regular file. Do not put it on the command line, in the profile,
   or in Helm values.
4. Export the endpoint's CA chain to an explicit PEM file. Do not use insecure
   TLS flags. The client disables environment proxies and redirects.
5. Confirm every expected decision comes from the same immutable customer
   snapshot and that the profile contains no credentials or sensitive resource
   values.
6. Run from a clean checkout at the source revision represented by the exact
   immutable image digest. Inform the policy owner which cases and identity
   will be observed; the tool performs no policy mutation.

## Run

```bash
IIP_CUSTOMER_POLICY_PROFILE=/protected/customer-policy-profile.json \
IIP_CUSTOMER_POLICY_ENDPOINT=https://policy.example.com/v1/data/iip/decision \
IIP_CUSTOMER_POLICY_BEARER_TOKEN_FILE=/protected/policy-token \
IIP_CUSTOMER_POLICY_CA_FILE=/protected/policy-ca.pem \
IIP_CUSTOMER_POLICY_IMAGE_DIGEST=sha256:... \
IIP_CUSTOMER_POLICY_ALLOW_OBSERVATION=true \
  make qualify-customer-policy PYTHON=.venv/bin/python
```

The Make target checks that the separately supplied endpoint exactly matches
the protected profile, then writes
`dist/customer-policy-qualification-report.json`. Output contains only a stable
status and path. It never prints the token, endpoint, identity, cases,
decisions, provider responses, or errors.

A well-formed run with an unavailable service or mismatched decision writes a
valid `not-qualified` report and exits nonzero. Unsafe or malformed input,
future review time, dirty source, invalid CA/configuration, or crossed binding
fails without creating evidence. Do not weaken digest, tenant, snapshot, TLS,
redirect, proxy, response-size, or latency checks to make an environment pass.

## Verify retained evidence

Keep the minimized report with the exact protected profile and non-secret
endpoint/image inputs. From the same clean revision:

```bash
IIP_CUSTOMER_POLICY_PROFILE=/protected/customer-policy-profile.json \
IIP_CUSTOMER_POLICY_ENDPOINT=https://policy.example.com/v1/data/iip/decision \
IIP_CUSTOMER_POLICY_IMAGE_DIGEST=sha256:... \
  make verify-customer-policy-qualification-report PYTHON=.venv/bin/python
```

Verification is offline. It validates the report and rebinds current source,
image, endpoint, profile, case set, and snapshot set. Changing any one of them
invalidates the evidence.

## Complete the production policy gate

Before production approval, separately retain evidence for:

- the complete supported IIP action matrix across relevant roles and scopes;
- bundle review, signing, promotion, rollback, and emergency/break-glass paths;
- service credential rotation, revocation, least authority, and outage behavior;
- endpoint, DNS, certificate, network-policy, zone, and regional failover;
- latency and availability under representative sustained decision traffic;
- audit delivery, minimization, retention, alerting, and SIEM reconciliation;
  and
- change-owner approval and periodic access review.

The customer report supplements the local compatibility report. Neither is an
authorization decision or permission to enable live actions.
