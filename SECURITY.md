# Security policy

The learning preview is not supported for production or customer data. It has
deliberately public demo credentials and ephemeral storage. Do not expose its
ports, use a remote Docker host, or put real credentials into its examples.
Production/community hardening and qualified deployment instructions are
separate; do not infer their completion from a learning release.

Use GitHub's private vulnerability reporting for this repository:
[Report a vulnerability](https://github.com/thedevopshuman/infra-intelligence-platform/security/advisories/new).
Do not publish credentials, customer telemetry, private prompts, or exploitation
details in a public issue. If the private reporting form is unavailable, open
an issue requesting a private contact **without vulnerability details**.

Include the release/commit, affected boundary, a minimal synthetic reproducer,
and expected versus actual behavior. Remove private data. There is no promised
response time, bounty program, security certification, or long-term maintenance
window for the learning preview. A patched preview may replace the current
one; production-version support policy remains part of the v1 release work.

## Private-pilot boundary

A private customer pilot still needs its own named security owner and private
reporting/escalation path before installation. Do not send customer profiles,
infrastructure identifiers, protected diagnostics or private endpoints to the
public project. An authorized report should preserve the exact release,
observation window, minimized evidence digests and impact description. Rotate
or revoke exposed credentials through the customer system that owns them;
IIP does not own customer identity, PKI, secret-store, provider or policy-engine
lifecycles.

Only the exact candidate/environment admitted by the
[private-pilot onboarding workflow](docs/operations/private-pilot-onboarding.md)
is in pilot scope. A branch tip, learning release, unsigned bundle, expired
qualification or modified artifact is not an admitted candidate. Coordinated
disclosure handling and production severity/support objectives remain
organizational work. See [SUPPORT.md](SUPPORT.md) and the baseline
[security, tenancy and authority model](docs/architecture/security-tenancy.md).
