# Repository and runbook context operations

**Status:** Runnable local-file and protected GitHub adapters

The default context backend returns honest no-data. The `files` backend reads
only explicitly cataloged UTF-8 text files under a protected absolute root.
Symlink resolution must remain under that root, parent traversal and absolute
catalog paths are rejected, and only documented source/text extensions are
accepted.

## Docker Desktop

The local Compose stack mounts `deploy/context` read-only at `/var/run/iip-context`. Enable the example catalog before `make local-up`:

```bash
export IIP_CONTEXT_BACKEND=files
export IIP_CONTEXT_INTEGRATIONS_JSON="$(tr -d '\n' < deploy/context/integrations.example.json)"
make local-up
```

Ingest the canonical resource example, then update the request timestamps/deadline and submit `contracts/examples/context-evidence-request.json` to `POST /v1/evidence/context/queries`. The response is Evidence metadata; normalized artifact bytes remain internal.

## Helm

Set `contextEvidence.backend=files`, place the catalog JSON in a Secret key, set `contextEvidence.integrationsExistingSecret`, and mount the cataloged documents through `contextEvidence.documentsExistingConfigMap`. Every configured `root` must equal or contain the chart mount path. Large or frequently changing repositories should use the protected GitHub adapter below or an immutable synchronized volume instead of a ConfigMap.

Do not put repository credentials, secrets, private keys, tokens, or sensitive configuration values in the catalog JSON or example documents. The adapter performs mandatory text redaction, but prevention and least-privilege source access remain primary controls. A reviewed [tenant redaction policy](evidence-redaction.md) can additionally remove email and validated IPv4 values from `repository.context`; it does not make unrestricted repository ingestion safe.

## Protected GitHub repository reads

Set `IIP_CONTEXT_BACKEND=github` and provide
`IIP_CONTEXT_INTEGRATIONS_JSON` using the shape in
`deploy/context/github-integrations.example.json`. Each repository entry must
use an exact lowercase 40-character commit SHA; branches, tags, arbitrary
queries, recursive directory reads, and caller-supplied paths are rejected.
Logical references must remain unique across the integration.

The adapter calls only the configured HTTPS origin and GitHub Contents file
route. It sends `Accept: application/vnd.github+json`, a configured
`X-GitHub-Api-Version`, and one request-scoped Bearer lease. It disables
ambient HTTP proxies and redirects, verifies the system trust store or the
configured `caBundlePath`, bounds JSON at 2 MiB, accepts a Base64 UTF-8 file no
larger than 1 MiB, and recomputes the Git blob identity. It does not follow the
response's `download_url`.

Production should set `IIP_CREDENTIAL_BROKER_MODE=external-http`. The adapter
asks the shared broker for exactly:

```text
provider: github
scope: repository:contents:read
tenant/actor/integration/credentialRef/deadline: inherited exactly
```

For a disposable local profile only, `IIP_GITHUB_CONTEXT_CREDENTIALS_JSON`
may contain an exact static mapping:

```json
{
  "credentials": [{
    "tenantId": "local",
    "integrationId": "github-context",
    "credentialRef": "credential://local/github/context",
    "bearerToken": "obtain-at-runtime-and-do-not-commit",
    "expiresAt": "2026-09-06T12:05:00Z"
  }]
}
```

Never commit or place that JSON on a command line. Supply it through a
restricted environment or the Helm `contextEvidence.credentialsExistingSecret`
only for development. Production uses the external broker and does not mount a
provider token Secret into IIP.

### Docker Desktop

The durable Compose stack passes the backend, protected integrations, and
optional development credential mapping to both the API and workflow worker:

```bash
export IIP_CONTEXT_BACKEND=github
export IIP_CONTEXT_INTEGRATIONS_JSON="$(tr -d '\n' < deploy/context/github-integrations.example.json)"
export IIP_GITHUB_CONTEXT_CREDENTIALS_JSON="$(tr -d '\n' < /protected/iip-github-context-credentials.json)"
make dev-up PYTHON=.venv/bin/python
```

The credential file must be restricted and excluded from the repository. Use
the platform's secret injection mechanism and do not substitute a token
directly into shell history.

### Helm

Merge `examples/production-github-context.values.yaml` after the production
core values. Create the referenced integrations Secret through the owning
secret controller. If GitHub Enterprise uses a private issuer, also set
`contextEvidence.caBundleExistingSecret` and make every configured
`caBundlePath` equal `<contextEvidence.caBundleMountPath>/ca.crt`.

Enable `networkPolicy.contextEgress` with the reviewed GitHub API destination
CIDR and port. GitHub-hosted IP ranges can change; customers must own the
review/update process or route through a stable controlled egress boundary.
The chart refuses the GitHub backend without an integrations Secret, explicit
NetworkPolicy egress, and either the external credential broker or a
development credential Secret.

## Compatibility evidence

Exercise the adapter, real CA-verified TLS client, hostile ambient proxy,
redirect denial, immutable ref/response binding, credential scope, tenant
isolation, outage, and recovery:

```bash
make test-github-context PYTHON=.venv/bin/python
```

From a clean release checkout, retain and verify source-bound evidence:

```bash
make qualify-github-context PYTHON=.venv/bin/python
make verify-github-context-report PYTHON=.venv/bin/python
```

The generated report is intentionally value-minimized. It proves the shipped
adapter against the local GitHub-shaped fixture, not customer organization,
GitHub App, repository, issuer, rate-limit, or availability compatibility.
