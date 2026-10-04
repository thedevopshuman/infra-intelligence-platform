# PostgreSQL transport security

The packaged production profiles connect every database client with libpq
`verify-full`: traffic is encrypted, the server certificate must chain to the
selected customer CA, and the certificate identity must match the database
hostname. The same policy covers the API, workflow worker, isolated OTLP
receiver, schema migrator, readiness probes, maintenance command, and scheduled
backup.

## Production Helm configuration

Create the database URL and CA as separate existing Secrets. Keep TLS policy
out of the URL; IIP owns those libpq options so a Secret rotation cannot weaken
the selected deployment policy.

```yaml
database:
  existingSecret: iip-database
  secretKey: database-url
  transportSecurity:
    mode: verify-full
    caExistingSecret: iip-database-ca
    caKey: ca.crt
```

The database URL must contain a hostname and must not contain `sslmode`,
`sslrootcert`, `sslcert`, `sslkey`, or other TLS policy fields. A Unix-socket
target is not accepted by the production mode. The CA Secret is mounted
read-only at the chart's fixed database trust path; it is never copied into a
ConfigMap, deployment report, or release bundle.

When Helm backup is enabled, the URL must use `postgres://` or `postgresql://`,
one or more comma-separated TCP hostnames or IP addresses, and a nonempty
database path. Optional ports must be numeric and IPv6 addresses bracketed.
The backup wrapper rejects all query parameters and fragments, keyword
connection strings, empty hosts, service indirection, and Unix sockets before
starting `pg_dump`. Percent encoding is allowed in user information and the
database name, but not in host targets. This conservative URI boundary avoids
reimplementing libpq's keyword parser in the backup utility image.

Packaged Python clients reject ambient `PGSSL*`, `PGSERVICE`, `PGSERVICEFILE`,
`PGHOSTADDR`, and related libpq transport controls. Remove those variables from
the workload environment and configure transport through the IIP settings
above. The check runs at startup and before opening each connection without
modifying process-wide environment variables.

Use an overlap CA bundle during planned trust rotation, update the existing
Secret, and roll all enabled IIP database clients under the customer's change
policy. Confirm dependency readiness and run the customer deployment gates
after the rollout. The chart does not issue certificates or decide a managed
database's PKI lifecycle.

## Local plaintext development

Repository-controlled Docker and disposable Kind profiles that use plaintext
PostgreSQL declare the exception explicitly:

```yaml
database:
  transportSecurity:
    mode: insecure-local
```

For a directly launched local process, set
`IIP_DATABASE_TRANSPORT_MODE=insecure-local`. Never use this mode for a shared,
customer, staging, pilot, or production database. Current production preflight
profiles reject it.

## Local verification

Run `make test-postgres-tls PYTHON=.venv/bin/python` with Docker Desktop running.
It creates two disposable PostgreSQL servers and a temporary CA, checks an
encrypted connection with the correct hostname, and requires rejection of an
untrusted CA, a mismatched hostname, and a plaintext server. The explicit local
mode succeeds against the plaintext fixture and is rejected by the TLS-only
fixture. The gate also checks that ambient libpq overrides fail closed without
changing the process environment. Generated certificates, containers, and test
volumes are removed afterward.

`make test-helm-install PYTHON=.venv/bin/python` also uses a disposable
TLS-only database, a separate CA Secret, and the real chart. It checks the
mounted CA and encrypted session from the API pod, then exercises migration,
readiness, a two-replica API upgrade, the backup CronJob, and restore. That gate
does not start the worker or OTLP receiver; their shared configuration and
Secret mounts are covered by the composition and rendered-chart tests.

This is local transport evidence; customer certificate lifecycle and database
topology still require qualification in the selected environment.

## Failure behavior

Startup fails before serving when the mode is unknown, a required CA is absent
or unreadable, the target is a Unix socket, or the URL attempts to supply its
own TLS settings. Wrong trust roots, hostname mismatches, expired certificates,
and plaintext servers fail the libpq handshake. Public readiness remains the
single `readiness.unavailable` response; logs and reports must not contain the
database URL, host, user, password, CA path, certificate content, or raw libpq
message.

This boundary proves connection confidentiality and server identity. It does
not prove database high availability, failover correctness, fencing, recovery,
RPO/PITR, private networking, or certificate lifecycle. Use the PostgreSQL
continuity and customer deployment qualification procedures for those separate
claims.
