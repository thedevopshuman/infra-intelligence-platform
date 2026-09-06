# Customer PostgreSQL primary-promotion qualification

**Status:** External customer-environment gate

**Decision:** [ADR 0130](../decisions/0130-customer-postgresql-primary-promotion-continuity.md)

**Contract:** [Customer PostgreSQL continuity qualification](../specifications/customer-postgresql-continuity-qualification-report-contract.md)

This runbook observes an independently authorized planned PostgreSQL promotion
while continuously exercising the installed platform. The command never calls
a cloud API, database promotion function, Kubernetes operator, or failover
controller.

## Prepare

1. Select an approved qualification tenant and existing Resource. Expect three
   synthetic investigations and at least three probe windows of synthetic OTLP
   metrics.
2. Copy
   `contracts/examples/customer-postgresql-continuity-profile.json` outside the
   repository, update it, and set mode `0600`.
3. Create separate mode-`0600` files for the API Bearer token, OTLP Bearer
   token, OTLP client key, and database password. Provide the OTLP client
   certificate/key and explicit database CA. If the database requires mutual
   TLS, also provide its client certificate/key.
4. Use a database observer role with `CONNECT` and only the read-only built-in
   function access described by the contract. Test the stable writer endpoint's
   certificate hostname before the maintenance window.
5. Arrange a separately authorized operator to initiate the planned promotion
   only after the qualifier prints its readiness message.

## Run

From a clean checkout at the exact deployed revision:

```bash
IIP_KUBERNETES_CONTEXT=customer-production \
IIP_PROCESSING_API_BASE_URL=https://iip.example.com \
IIP_PROCESSING_API_TOKEN_FILE=/protected/api-token \
IIP_PROCESSING_OTLP_BASE_URL=https://otlp.iip.example.com \
IIP_PROCESSING_OTLP_TOKEN_FILE=/protected/otlp-token \
IIP_PROCESSING_OTLP_CLIENT_CERT_FILE=/protected/otlp-client.crt \
IIP_PROCESSING_OTLP_CLIENT_KEY_FILE=/protected/otlp-client.key \
IIP_PROCESSING_IMAGE_DIGEST=sha256:... \
IIP_CUSTOMER_POSTGRESQL_PROFILE=/protected/customer-postgresql-profile.json \
IIP_CUSTOMER_POSTGRESQL_HOST=writer.database.example.com \
IIP_CUSTOMER_POSTGRESQL_PASSWORD_FILE=/protected/database-password \
IIP_CUSTOMER_POSTGRESQL_CA_FILE=/protected/database-ca.pem \
IIP_CUSTOMER_POSTGRESQL_ALLOW_FAILOVER_OBSERVATION=true \
  make qualify-customer-postgresql-continuity PYTHON=.venv/bin/python
```

The baseline completes before this message appears:

```text
customer postgresql continuity: baseline complete; initiate the planned primary promotion
```

The authorized database operator then performs the promotion through the
customer's normal provider or controller workflow. The qualifier continues
until it observes a higher PostgreSQL timeline or the bounded timeout expires.

## Verify retained evidence

Retain
`dist/customer-postgresql-continuity-qualification-report.json`, the protected
profile, and the exact non-secret targets. Recompute it from the same clean
source:

```bash
IIP_KUBERNETES_CONTEXT=customer-production \
IIP_PROCESSING_API_BASE_URL=https://iip.example.com \
IIP_PROCESSING_OTLP_BASE_URL=https://otlp.iip.example.com \
IIP_PROCESSING_IMAGE_DIGEST=sha256:... \
IIP_CUSTOMER_POSTGRESQL_PROFILE=/protected/customer-postgresql-profile.json \
IIP_CUSTOMER_POSTGRESQL_HOST=writer.database.example.com \
  make verify-customer-postgresql-continuity-report PYTHON=.venv/bin/python
```

Do not interpret `serverIdentityChanged` as topology proof. A proxy can hide the
backend identity, and a server restart changes its start-time binding. The
strictly increasing WAL timeline is the promotion proof; topology, fencing,
replication mode, and fault domain need separate provider evidence.
