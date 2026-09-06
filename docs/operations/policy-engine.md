# External policy-engine operations

**Status:** Production-facing boundary with local compatibility evidence

The `external-http` policy mode posts the exact authenticated actor, action,
roles, and tenant-scoped resource to one configured HTTPS decision endpoint. A
decision grants authority only when it echoes the canonical request digest and
returns an immutable snapshot reference for the same tenant. Every malformed,
stale, oversized, redirected, untrusted, or unavailable response denies closed
as `policy.unavailable`.

Run the local qualification gate with Docker Desktop:

```bash
make test-policy-engine PYTHON=.venv/bin/python
```

The disposable fixture listens only on `127.0.0.1:19444`, uses an ephemeral CA,
runs as UID 65532 with a read-only filesystem and dropped capabilities, and is
removed after the test. The generated
`dist/policy-engine-compatibility-report.json` must identify the intended source
revision and set `sourceDirty` to `false` for release evidence.

For a customer engine, use the separate [customer policy qualification
runbook](customer-policy-qualification.md) with its real certificate chain,
protected bearer credential, tenant mapping, reviewed cases, and immutable
bundle identifier. That gate proves selected allow/deny behavior through this
production adapter. Independently verify complete action coverage, credential
rotation and revocation, response-size and timeout limits, network policy,
audit delivery, failover, and recovery. Never put bearer credentials, raw
requests, actor or tenant identifiers, resource details, or provider error text
into retained evidence.

Rollback is fail-closed: restore the last reviewed endpoint, CA bundle,
credential projection, and policy snapshot together. If the service or its
identity is uncertain, keep the external mode unavailable; do not switch a
production deployment to the permissive local policy adapter.
