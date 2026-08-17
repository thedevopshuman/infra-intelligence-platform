# Local development

**Status:** Foundation

## Requirements

- Python 3.11 or newer
- Helm 3 or newer
- Optional: Docker Desktop for PostgreSQL, OpenTelemetry Collector, OTLP receiver, Prometheus/Loki integration tests, and the durable local stack
- Optional: Docker Desktop, kind, and `kubectl` for live Kubernetes collection and Event evidence tests

Install the pinned verification-only Python dependencies:

```bash
python3 -m pip install --requirement requirements/verify.txt
```

The domain, application, surfaces, and SDK boundaries remain vendor-independent. The verification environment pins `jsonschema`, its format checkers, Psycopg, and the OpenTelemetry SDK/exporter so validation and integration behavior cannot silently change with a developer's global environment.

## Verify

```bash
make verify
```

This validates JSON documents and internal links, enforces Python package directions, checks every contract schema and example with the pinned Draft 2020-12 validator, runs unit tests, lints the chart, and renders Kubernetes templates.

PostgreSQL integration tests skip when no database URL is supplied. With Docker Desktop running, execute the explicit durable-store gate:

```bash
make test-postgres
```

This starts an ephemeral PostgreSQL 18.4 container bound to `127.0.0.1:55432`, runs only the PostgreSQL integration suite, and removes its container and volume on exit.

Run the separate end-to-end recovery measurement with:

```bash
make test-backup-restore
```

It seeds the durable reference workflow, backs up the complete platform schema, restores into a fresh database, verifies every table and sequence plus projection consistency, prints measured local RPO/RTO evidence, and removes its isolated Compose project and volume. See the [backup and restore procedure](postgresql-backup-restore.md) for the measurement semantics and production gaps.

Exercise the official OTLP/HTTP exporter against a real OpenTelemetry Collector with:

```bash
make test-otel
```

The isolated target verifies receipt of a reference ingestion metric and terminal investigation trace, then removes its Collector. See the [OpenTelemetry export guide](opentelemetry-export.md) for the long-running Compose profile, Helm settings, security constraints, and production gaps.

Exercise official OTLP/HTTP exporters against the isolated receiver process with:

```bash
make test-otlp-receiver
```

The gate runs PostgreSQL plus separate control-plane and receiver containers, seeds the configured tenant/resource through the control plane, exports an allowlisted cumulative sum and log record through independent channel authentication, verifies route isolation, requires OTLP success, and removes the isolated stack afterward. Pure normalization, redaction, scope, and persistence behavior remains covered by `make verify`. See the [metrics receiver guide](otlp-metrics-receiver.md) and [log evidence guide](log-evidence.md) for protected channel and Helm configuration.

Exercise the historical metric-evidence adapter against a real Prometheus server with:

```bash
make test-prometheus
```

The isolated target queries Prometheus's self-scraped `up` metric through the generated range-query boundary, verifies normalized series, and removes the server afterward. See the [Prometheus evidence guide](prometheus-evidence.md) for protected integration mapping, credential, Compose, and Helm configuration.

Exercise the historical log-evidence adapter against a real Loki server with:

```bash
make test-loki
```

The isolated target pushes current logs, queries them through generated selector-only LogQL, verifies normalized records and investigation citation, and removes the server afterward. See the [log evidence guide](log-evidence.md) for protected mappings, credentials, Compose, and Helm configuration.

Exercise the read-only Kubernetes Event evidence adapter against an explicit local context with:

```bash
IIP_KUBECONFIG=/absolute/path/to/.kube/config \
IIP_KUBE_CONTEXT=kind-iip-dev \
make test-kubernetes-events
```

The target creates only the isolated `iip-event-test` namespace, grants its service account Deployment `get` and Event `get/list`, issues a short-lived test token, queries through the direct HTTPS adapter, commits normalized Evidence, and removes the namespace. See the [Kubernetes Event evidence guide](kubernetes-event-evidence.md) for protected registry, credential, TLS, Helm, and historical-aggregate semantics.

Keep deterministic contract, SDK, kernel, and observer-conformance tests in the normal `make verify` gate. Docker Desktop supplies external dependencies for integration tests; it is not required to validate pure normalization behavior. This split keeps feedback fast while still exercising PostgreSQL against the real engine.

The observer has an explicit `kubectl` development transport. It requires a named context and kubeconfig path, lists each resource API path independently, and normalizes the same public contract as the offline fixture. A complete result includes an aggregate checkpoint plus opaque per-path provider cursors. A later reconciliation request supplies that committed state in `spec.resume`; the observer runs bounded watches and then relists the full scope. Kubernetes `410 Gone` uses the same relist path, so an expired stream can never be mistaken for deletion.

Create the isolated cluster and run the live test:

```bash
kind create cluster --name iip-dev --wait 120s
IIP_KUBECONFIG=/absolute/path/to/.kube/config make test-kubernetes-live
```

The kind cluster runs as containers inside Docker Desktop. Docker Desktop's separate built-in Kubernetes feature is not required for this workflow and should normally remain disabled to avoid an unnecessary second context and control plane.

The target seeds `deploy/kubernetes/dev/seed-incident.yaml`, whose intentionally nonexistent image produces a real `ErrImagePull`/`ImagePullBackOff`. It verifies that the observer returns a complete canonical graph and an unhealthy Pod with only the safe waiting reason. It then deletes only the harmless reconciliation-probe ConfigMap, resumes from every committed per-path cursor, relists the complete scope, and requires one host-generated tombstone. This local transport does not use the external credential-broker client and does not replace the future isolated plugin runner.

## Run the reference API

Create a high-entropy local Bearer token and configure only its SHA-256 verifier. Keep the token in the current shell; do not commit it, paste it into documentation, or place it in request payloads.

```bash
export IIP_DEV_BEARER_TOKEN="$(openssl rand -hex 32)"
IIP_DEV_TOKEN_DIGEST="$(printf '%s' "$IIP_DEV_BEARER_TOKEN" | shasum -a 256 | awk '{print $1}')"
export IIP_AUTH_IDENTITIES_JSON="{\"identities\":[{\"tokenSha256\":\"sha256:${IIP_DEV_TOKEN_DIGEST}\",\"actorId\":\"local-developer\",\"tenantId\":\"local\",\"roles\":[\"developer\"]}]}"
```

The verifier configuration is not a raw credential, but it must still be protected because weak tokens could be guessed offline. The generated token has 256 bits of entropy. The API fails closed at startup when authentication configuration is absent or malformed.

```bash
make run
```

The local surface stores resources in memory. Its hashed opaque-token authenticator is a local reference boundary, not a production identity provider.

Open `http://127.0.0.1:8080/console` for the browser console, or use the API directly. The console sends the Bearer token only to the same-origin control plane. By default the token exists only in page memory; the optional “keep for this browser tab” setting uses `sessionStorage`, never persistent `localStorage`. The console shell is public, while every tenant data request still authenticates independently.

```bash
curl -X POST http://localhost:8080/v1/resources \
  -H 'content-type: application/json' \
  -H "authorization: Bearer $IIP_DEV_BEARER_TOKEN" \
  --data @contracts/examples/resource.json

curl -H "authorization: Bearer $IIP_DEV_BEARER_TOKEN" \
  http://localhost:8080/v1/resources
```

After a complete collection has committed for a source, inspect its point-in-time freshness and event-delivery backlog:

```bash
curl -H "authorization: Bearer $IIP_DEV_BEARER_TOKEN" \
  'http://localhost:8080/v1/telemetry/ingestion?sourceId=kubernetes-local'
```

The local in-memory process starts empty, so a source returns `ingestion.source_not_found` until collection ingestion commits its first checkpoint. The long-running Docker profile retains that state in PostgreSQL.

## Run the durable Docker profile

The supported local onboarding path generates 256-bit Bearer tokens for three separate development actors, stores only their SHA-256 verifiers in the API environment, writes raw tokens to a mode-`0600` ignored file, and generates a separate PostgreSQL password:

```bash
make dev-up
```

This starts PostgreSQL, the API, and a tenant-explicit workflow worker, waits until the API proves both database connectivity and the latest packaged schema migration, and prints the console URL plus the `local-operator` token. The local operator has `developer` and `platform-admin` roles so it can use the protected operations surface; this development identity is not a production role model. `/healthz` remains process-only while `/readyz` is the serving gate. The worker dispatches investigations, performs non-executing action timer reconciliation, delivers local outbox events to an explicit development structured-log sink, and samples the enrolled `local/kubernetes-local` ingestion source every 60 seconds. It has no interactive identity Secret and does not compose an action executor. The `local-approver` and `local-executor` credentials stay distinct to preserve separation of duties. Run `make dev-credentials` when exercising the console approval workflow; that explicit command prints all three identities from the protected mode-`0600` file. Useful lifecycle commands are:

```bash
make dev-status
make dev-credentials
make test-local-product
make dev-down
```

`make test-local-product` reads the protected local credentials without printing
them and exercises the durable customer path end to end: complete collection ingestion,
leased event delivery, durably queued worker investigation, immutable proposal, independent approval, one-shot
dry-run execution, replay safety, and the paginated action queue.

`dev-down` preserves the named PostgreSQL volume. The setup never deletes local state automatically. If manual environment control is needed instead, the Compose profile requires a local-only password supplied at runtime and never committed:

```bash
export IIP_POSTGRES_PASSWORD="$(openssl rand -hex 24)"
docker compose -f deploy/docker-compose.yml up --build --detach
docker compose -f deploy/docker-compose.yml ps
```

This is the long-running development stack visible in Docker Desktop: PostgreSQL, the API, the background workflow worker, and a named database volume. It differs from `make test-postgres`, whose test container and volume are always removed on exit. The API is published only on `127.0.0.1`; both application containers run as a non-root user with read-only filesystems, dropped Linux capabilities, and `no-new-privileges`. Stop the manual stack with `docker compose -f deploy/docker-compose.yml down`; add `--volumes` only when you intentionally want to delete its local database.

The API migrates the local Compose database on startup. Automatic migration is disabled in Helm serving pods; `database.migrations.enabled=true` explicitly runs the isolated pre-install/pre-upgrade Job described in the [Helm deployment guide](helm-deployment.md).

### Verify or rebuild resource projections

The PostgreSQL maintenance command verifies one explicit tenant against immutable accepted observation history. It is read-only unless `--apply` is present and prints only stable counts and canonical digests.

```bash
export IIP_DATABASE_URL='postgresql://postgres:<local-password>@127.0.0.1:5432/iip'
PYTHONPATH=src python3 -m iip.surfaces.maintenance \
  rebuild-projections --tenant local --actor local-operator
```

Review `driftDetected`, `beforeDigest`, and `expectedDigest`. Apply the atomic replacement only when recovery is intended:

```bash
PYTHONPATH=src python3 -m iip.surfaces.maintenance \
  rebuild-projections --tenant local --actor local-operator --apply
```

This command does not alter immutable observations, events, outbox delivery state, checkpoints, or reconciliation membership. It is local operator tooling and does not create a cross-tenant HTTP administration path.

## Helm

```bash
helm lint deploy/helm/infra-intelligence
helm template iip deploy/helm/infra-intelligence --namespace iip-system
make test-helm-install
```

The default image reference is a placeholder until an image pipeline exists. Do not install the chart into a production cluster.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `IIP_HTTP_HOST` | `0.0.0.0` | Reference API bind address |
| `IIP_HTTP_PORT` | `8080` | Reference API port |
| `IIP_AUTH_MODE` | `local-hashed` | Authenticator selection: local hashed verifier or `oidc` |
| `IIP_AUTH_IDENTITIES_JSON` | required by API startup | Local Bearer-token verifier identities; supply through protected runtime configuration |
| `IIP_AUTH_OIDC_CONFIG_JSON` | required in `oidc` mode | HTTPS issuer/JWKS, audience, claim mapping, CA path, cache, and clock-skew bounds |
| `IIP_POLICY_MODE` | `local` | Policy adapter selection: local reference rules or `external-http` |
| `IIP_POLICY_CONFIG_JSON` | required in `external-http` mode | TLS endpoint, protected CA/token paths, timeout, and response-size bounds |
| `IIP_CREDENTIAL_BROKER_MODE` | `static` | Provider credential resolution: provider-specific local `static` brokers or shared `external-http` client |
| `IIP_CREDENTIAL_BROKER_CONFIG_JSON` | unset | Required non-secret HTTPS/trust/workload-token-path/lease bounds when external mode is selected |
| `IIP_DATABASE_URL` | unset | Select the PostgreSQL profile when set |
| `IIP_DATABASE_AUTO_MIGRATE` | `false` | Apply packaged migrations at startup; local Compose only |
| `IIP_WORKER_TENANTS` | unset | Required comma-separated exact tenant enrollment for the workflow worker; wildcard is invalid |
| `IIP_WORKER_ID` | pod/host name | Stable workflow-worker identity |
| `IIP_ACTION_RECONCILIATION_BATCH_SIZE` | `100` | Maximum expired action leases scanned per enrolled tenant and timer pass |
| `IIP_EVENT_PUBLISHER_MODE` | `disabled` | Transactional-outbox publisher; local `stdout-json` or production-oriented `https-webhook` are explicit opt-ins |
| `IIP_EVENT_PUBLISHER_CONFIG_JSON` | unset | TLS endpoint, exact tenant IDs, mounted token/CA paths, and bounds for HTTPS publishing |
| `IIP_OUTBOX_BATCH_SIZE` | `100` | Maximum outbox messages claimed for one tenant pass |
| `IIP_INGESTION_MONITOR_TARGETS_JSON` | unset | Worker-only closed target list; each target tenant must be explicitly enrolled; omission disables sampling |
| `IIP_INGESTION_MONITOR_INTERVAL_SECONDS` | `60` | Worker-only automatic freshness cadence from 5–3600 seconds |
| `IIP_INGESTION_MAX_CHECKPOINT_AGE_SECONDS` | `300` | Local maximum age of the last complete committed collection |
| `IIP_INGESTION_MAX_OBSERVATION_AGE_SECONDS` | `300` | Local maximum age of the latest accepted source observation |
| `IIP_INGESTION_MAX_DELAY_SECONDS` | `60` | Local maximum provider-observation to platform-recording delay |
| `IIP_INGESTION_MAX_PENDING_EVENT_AGE_SECONDS` | `60` | Local maximum age of the oldest unpublished source event |
| `IIP_INGESTION_MAX_CLOCK_SKEW_SECONDS` | `5` | Maximum future timestamp tolerance before a skew violation |
| `IIP_OTEL_METRICS_ENABLED` | `false` | Compose optional outbound OTLP/HTTP freshness metrics |
| `IIP_OTEL_TRACES_ENABLED` | `false` | Compose optional outbound OTLP/HTTP terminal investigation traces |
| `IIP_OTLP_RECEIVER_ENABLED` | `false` | Enable the optional tenant-bound OTLP/HTTP metrics Evidence receiver |
| `IIP_OTLP_RECEIVER_CHANNELS_JSON` | required when receiver is enabled | Protected hashed channel credentials, fixed scope, catalogs, admission limits, and handling policy |
| `IIP_TELEMETRY_METRICS_BACKEND` | `no-data` | Historical metric evidence backend; `no-data` or `prometheus` |
| `IIP_TELEMETRY_LOGS_BACKEND` | `no-data` | Historical log evidence backend; `no-data` or `loki` |
| `IIP_OTLP_LOGS_RECEIVER_ENABLED` | `false` | Enable the optional tenant-bound OTLP/HTTP logs Evidence receiver |
| `IIP_OTLP_LOGS_RECEIVER_CHANNELS_JSON` | required when logs receiver is enabled | Protected hashed logs-channel credentials, fixed resource/service scope, mappings, limits, and handling policy |
| `IIP_PROMETHEUS_INTEGRATIONS_JSON` | unset | Protected non-secret tenant/integration endpoint and allowlisted metric catalog required by the Prometheus backend |
| `IIP_PROMETHEUS_CREDENTIALS_JSON` | empty credential set | Secret-bearing local reference broker configuration; supply only through a protected runtime channel |
| `IIP_LOKI_INTEGRATIONS_JSON` | unset | Protected non-secret tenant/integration endpoint, organization, label, service, and severity catalog required by the Loki backend |
| `IIP_LOKI_CREDENTIALS_JSON` | empty credential set | Secret-bearing local Loki broker configuration; supply only through a protected runtime channel |
| `IIP_KUBERNETES_EVENTS_BACKEND` | `no-data` | Kubernetes Event evidence backend; `no-data` or `kubernetes-api` |
| `IIP_KUBERNETES_EVENTS_INTEGRATIONS_JSON` | unset | Protected non-secret tenant endpoint, CA path, cluster/scope allowlists, limits, and condition mappings required by the live adapter |
| `IIP_KUBERNETES_EVENTS_CREDENTIALS_JSON` | unset | Secret-bearing local reference broker configuration required by the live adapter |
| `IIP_OTEL_INGESTION_ATTRIBUTE_MODE` | `source` | Metric identity dimensions: `none`, `source`, or `tenant-source` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset outside Compose | Standard OTLP base endpoint; the enabled signal path is appended |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | unset | Exact signal-specific OTLP metrics endpoint |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | unset | Exact signal-specific OTLP traces endpoint |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` | Standard OTLP transport selection; only HTTP/protobuf is supported |
| `OTEL_EXPORTER_OTLP_HEADERS` | unset | Protected standard exporter header configuration |
| `OTEL_METRIC_EXPORT_INTERVAL` | `60000` | Periodic SDK export interval in milliseconds |
| `OTEL_METRIC_EXPORT_TIMEOUT` | `10000` | Periodic SDK export timeout in milliseconds |
| `OTEL_SERVICE_NAME` | `infra-intelligence-api` | OpenTelemetry service resource name |
| `IIP_TEST_DATABASE_URL` | unset | Enable PostgreSQL integration tests against an explicit test database |
| `IIP_TEST_PROMETHEUS_ENDPOINT` | unset | Enable the real Prometheus adapter integration test against an explicit endpoint |
| `IIP_TEST_LOKI_ENDPOINT` | unset | Enable the real Loki adapter integration test against an explicit endpoint |
| `IIP_TEST_OTLP_RECEIVER_ENDPOINT` | unset | Enable the official-exporter receiver integration test against an explicit API endpoint |
| `IIP_KUBECONFIG` | required by live tests | Explicit kubeconfig path; neither test chooses an implicit current context |
| `IIP_KUBE_CONTEXT` | `kind-iip-dev` | Explicit local context used by `test-kubernetes-live` and `test-kubernetes-events` |

Future secrets must be logical references resolved by the deployment/runtime, never committed environment files.
