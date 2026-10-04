# Persistent single-host community preview

**Status:** Pre-release installation path, not public v1 or production qualification

This path starts an empty installation with your reviewed configuration. It
does not send demo traffic, call AWS, invent prices, or grant provider access.
It is separate from the disposable [AI FinOps demo](ai-finops-local-demo.md).
The existing resource, evidence, investigation, and console surfaces remain
available; this onboarding unit focuses on inbound Bedrock telemetry.

## Supported boundary

| Item | This preview |
| --- | --- |
| Host | One trusted local Docker Desktop/Docker Engine daemon with Compose v2 and a Unix socket; no remote Docker contexts |
| Application | Current checkout built explicitly, or an operator-selected, already available application image; public signed distribution remains pending |
| Installation scope | One protected tenant and one Bedrock trace channel; one Collector and one metrics backend |
| Instrumentation | Pinned Python botocore profile plus the existing Bedrock usage adapter; exact scope `opentelemetry.instrumentation.botocore.bedrock-runtime` |
| Provider access | Only the instrumented application calls Bedrock; IIP receives no AWS credentials |
| Pricing | One non-fixture catalog and current exact `production-catalog` qualification; calculated estimates, not invoices |
| Ownership | One reviewed attribution policy; unmatched observations remain unallocated |
| Savings | Optional reviewed fixed-window profiles; no fabricated opportunity or monetary saving |
| Persistence | Named PostgreSQL, Collector queue, Prometheus, and Grafana volumes survive `down` |
| Recovery | Explicit encrypted offline whole-installation copy to fresh stopped state; exact deployment/images and source fencing required; not an upgrade or recovery objective |
| Transport lifecycle | Explicit stopped-stack whole-CA/leaf rotation with external overlap trust, pre-finalization rollback, and operator intake verification; no hot renewal |
| Interfaces | API/console, authenticated Grafana, and Prometheus on loopback; HTTPS/Bearer Collector intake on loopback; database and mTLS receiver have no host ports |

The source host needs Python with the repository dependencies, Docker, and
Compose. Use the repository's documented dependency installation before
running the commands below. Docker administrators and the local account are
trusted: protected values are used in container environments and can be read
by daemon administrators. The launcher checks the selected Unix-socket daemon
without installation credentials, then pins that socket for Compose. Unset
`DOCKER_HOST` and select a local Docker context. It never uses a remote daemon
for this profile. Keep Docker CLI/plugins and its configuration trusted.
The first Docker operation also binds the installation to that socket and
daemon ID in protected `daemon.json`. Selecting another local daemon fails
closed; there is no automatic rebind that could hide the original running
stack. Preserve this binding with the installation's recovery state.

## Prepare the reviewed inputs

Keep populated inputs outside Git, in owner-only directories, with each JSON
file mode `0600`. Symlinks, hard-linked files, duplicate JSON keys, broad file
permissions, inconsistent tenants, and invalid contracts are rejected.

Supply these existing configuration wrappers; they are not new contracts:

1. `--channel`: `{"channels":[...]}` from the [receiver configuration](ai-usage-receiver.md).
   Review exact service/namespace/environment, provider models, regions,
   operations, semantic-convention version, commercial scope, and intake bounds.
   The installer replaces only the channel ID and token hash. Use the exact
   instrumentation scope above, standard five GenAI usage attribute mappings,
   `aws.request_id`/`aws.retry_count` invocation mappings, and **empty**
   `zeroWhenAbsent` arrays. Missing meters must not become invented zeroes.
2. `--catalogs`: `{"catalogs":[...]}` containing a complete reviewed catalog.
   Use the [AWS source import workflow](aws-bedrock-price-catalog-import.md)
   where applicable. Do not relabel the example's synthetic rates as real
   provider prices. Preserve the source evidence and its actual digest.
3. `--qualifications`: `{"policies":[...],"reports":[...]}` containing the
   exact policy and current report produced by [catalog qualification](ai-cost-engine.md).
   Both startup and every cost pass enforce the report's validity window.
   This does not establish negotiated-price approval or invoice agreement.
4. `--attribution`: `{"policies":[...]}` containing your effective-time
   [service-to-application/team policy](ai-attribution.md). Telemetry cannot
   declare its own organizational owner.
5. Optional `--savings`: `{"profiles":[...]}` containing reviewed
   [savings profiles](ai-savings-engine.md). Omission disables saving evaluation;
   rolling usage/cost views do not depend on it.

Initialize once, using your actual protected file paths:

```bash
.venv/bin/python scripts/community_stack.py init \
  --channel /protected/bedrock-channels.json \
  --catalogs /protected/ai-catalogs.json \
  --qualifications /protected/ai-catalog-qualifications.json \
  --attribution /protected/ai-attribution.json
make community-check PYTHON=.venv/bin/python
make community-up PYTHON=.venv/bin/python
```

`init` performs no Docker or provider action. It creates `.iip/community` with
mode `0700`, five independent random credentials, a private local trust domain,
and immutable content-addressed configuration generations. Every host-side
file is owner-only. The generation binds both the reviewed inputs and the
rendered Collector/dashboard; missing or changed generated files fail closed.
`.dockerignore` excludes `.iip`, environment files, logs, and archives from
the source build context.

`community-up` explicitly builds the application image first, then starts a
root-only, network-disabled volume initializer, a separate non-root migrator,
and seven non-root long-running services. Serving processes never migrate the
schema. It waits for component health and verifies that the TLS Collector
rejects an unauthenticated empty export. This is **not** proof that a real
invocation has arrived or been priced.

Building requires all installation containers removed: this includes
`make community-up` and `scripts/community_stack.py up --build`. Ordinary
`scripts/community_stack.py up` against an exact healthy, unchanged project is
a read-only no-op, not a repair command. It checks expected service health and
the initializer's selected trust, installation/credential, and operational
deployment bindings without rerunning the initializer. For unhealthy or
mismatched state, source/configuration changes, rebuilding, or repair, use
`down` first, preserving volumes, then the appropriate startup command. Never
use `down --volumes` for this workflow.

Successful startup now records actual container image IDs and their
OS/architecture in protected `runtime-images.json`, bound to the operational
deployment files. Preserve that record for offline backup; version strings or
mutable tags cannot reconstruct it. Recovered startup instead requires those
exact images locally and refuses build/pull fallback.

For an existing selected image, pass `--image` to `init`, then run
`scripts/community_stack.py up` without `--build`. Prefer a verified digest
when release artifacts become available; this work does not publish any.
Custom state uses `--state /absolute/protected/path` before the command. Its
absolute location determines the isolated Compose project: do not move it or
delete/reinitialize it while retained data belongs to that installation.
Custom state inside the checkout must be beneath `.iip/`; otherwise initialization
rejects it to keep protected files outside the image build context.

## Connect an application and see first value

Open the [console](http://127.0.0.1:18083/console) or
[community dashboard](http://127.0.0.1:13001/d/iip-community-ai-finops).
Use the `apiToken` from the protected `credentials.json` for the console;
Grafana's user is `admin` and its password is `grafanaPassword` in that file.
The launcher never prints credentials. No anonymous Grafana access is enabled.

Configure the application's existing asynchronous OTel exporter:

- trace endpoint: `https://localhost:14322/v1/traces`;
- protocol: OTLP/HTTP protobuf;
- CA: the `caPath` returned by `scripts/community_trust.py --state PATH status`,
  explicitly supplied to the exporter; `transport/ca.crt` is current only
  before transport-generation enrollment;
- Authorization: `Bearer` plus `collectorToken` from protected credentials;
- resource attributes: the exact reviewed service, namespace, environment,
  and cloud region;
- retain eligible metadata spans; disable prompt/message/body capture.

Keep this CA local to the exporter, not the system trust store. Do not disable
certificate verification. Another container's `localhost` is not the host:
this first profile is for a host application; remote/container onboarding
needs explicit routing and certificate design, not exposing these ports.
Follow the [Bedrock instrumentation guide](bedrock-instrumentation-qualification.md)
for the existing provider adapter. It is not an inference proxy or a new IIP SDK.

The generated Collector filters exact reviewed string values before its
persistent queue, rejects content-bearing keys/events/links and dropped-field
counts, strips unknown metadata, span/status descriptions and schema URLs,
and drops free-form `error.type` (errors become `unknown`). It hashes provider
request IDs before disk; the receiver hashes that value again. This remains
deterministic within this profile, but its request correlation digest differs
from direct receiver intake. Do not mix direct and community paths to claim
cross-path retry identity. Rejected Collector spans are not accounting facts.
Before queueing, the pinned Collector rebuilds resources from the sanitized
attributes, intentionally discarding entity references. The executable privacy
gate also checks unknown protobuf fields. Collector image upgrades must rerun
that gate; a configuration render alone does not establish this property.

After an authorized application invocation, check **AI Economics** in the
console. Pending cost/attribution and unpriced/unallocated counts are visible.
The worker updates a rolling 24-hour projection; the dashboard uses only one
allocation dimension for totals, avoiding application/team double-counting.
Currency and scale are rendered from the selected catalog. The change/saving
panels refer to optional completed fixed windows, not continuous predictions.
An empty window, missing profile, or missing price is not a zero-cost claim.
Backend freshness alone does not prove that the latest projection succeeded.
For release-quality live proof, use the existing
[same-invocation qualification](customer-ai-finops-flow-qualification.md).

## Stop and update configuration

```bash
make community-status PYTHON=.venv/bin/python
make community-down PYTHON=.venv/bin/python
.venv/bin/python scripts/community_stack.py configure \
  --channel /protected/bedrock-channels.json \
  --catalogs /protected/new-ai-catalogs.json \
  --qualifications /protected/new-ai-catalog-qualifications.json \
  --attribution /protected/ai-attribution.json
make community-up PYTHON=.venv/bin/python
```

`down` removes containers, not named volumes or credentials. `configure`
requires **all** previous project containers removed, validates the complete
new inputs, retains the old generation, and atomically switches the manifest.
A per-installation lock serializes CLI commands. Keep the same tenant; use
new immutable catalog/policy IDs and versions where their contracts require
them. Refresh expiring qualification before restarting. Invalid replacement
inputs leave the selected generation unchanged. To restore a prior policy,
submit its exact still-valid input wrappers through `configure`.

Never use `docker compose down --volumes` for a real installation. No destructive
purge command is provided. Protect backups and the configuration/credentials
together. Named volumes are persistence, not a backup or disaster-recovery plan.

The [offline encrypted recovery runbook](community-recovery.md) covers the
four data volumes together with protected configuration, credentials, and
transport material. It requires a clean shutdown and all containers removed,
a separately protected key, and a fresh destination that remains stopped.
It never stops a source, fences another host automatically, uploads data, or
restores over an existing installation. Do not use `make community-up` on
recovered state: its implicit `--build` is intentionally rejected; follow the
runbook's explicit validated no-build startup.

The [transport rotation runbook](community-trust-rotation.md) replaces the
whole local CA and all five leaf/key pairs while keeping passwords, tokens,
data, and pricing/attribution unchanged. Prepare/activate/rollback require
removed containers; startup is blocked while prepared. Configuration and
backup are blocked during an unfinished rotation. External exporter trust is
staged by the operator, not silently changed by IIP.

## Known release work

This preview is deliberately not a replacement for the current production
Helm profiles. API/Grafana HTTP and internal metric traffic are local-only;
external identity, HTTPS ingress, HA, operational alerts, and fresh-machine
public artifact installation remain release work. No external credential
broker is needed for inbound telemetry; integrations that perform provider
reads still need their own governed authority.

Leaf certificates expire after 365 days and fail closed. The CA signing key is
not retained; the new stopped-stack rotation command replaces the complete
trust generation and preserves the installation. Its status view warns within
30 days of expiry, but scheduling and external-exporter changes remain
operator-owned. Do not delete installation state to regenerate certificates
against an existing database: that would also regenerate its password.
The owned local synthetic rotation/rollback/recovery gate has passed; see its
[recorded scope](community-trust-rotation.md#evidence-and-remaining-responsibilities).
Long-lived operation still needs the gate rerun for the release candidate and
a customer-tested maintenance/expiry procedure; a command is not an unattended
renewal service.

Prometheus is bounded to 30 days/2 GB. The Collector has a bounded persistent
queue; records older than the protected intake age may be rejected after a
long outage. Database AI-ledger retention is not implemented. The separate
[delivered-outbox retention control](event-outbox-retention.md) is available
through explicit application/Helm configuration, but is disabled in this
community profile and cannot remove undelivered work. Event publishing
is disabled to avoid silently logging customer identities, so the durable
outbox also grows until an explicit downstream-delivery/retention policy is
configured. Monitor disk; this is not an unattended long-term deployment.

The configuration source/owner remains responsible for valid pricing and
attribution; static qualification is not organizational approval. The source
repository and Apache-2.0 learning prerelease are public; that release is a
separate disposable profile. Signed public runtime artifacts, supported-version
promises, live Bedrock evidence, recovery qualification, and upgrade proof
remain in the
[public v1 plan](../roadmap/public-v1-release-plan.md).

## Verification

`make verify PYTHON=.venv/bin/python` includes the installer, credential,
configuration, transport, Collector-generation, and dashboard tests.
`make test-community PYTHON=.venv/bin/python` separately allocates its own
disposable Docker project and synthetic test inputs. It checks empty startup,
actual encrypted database clients, authenticated metadata intake, independent
asynchronous pricing/attribution, deduplication, pre-queue privacy, queue and
database survival across `down`/`up`, rolling Prometheus output without savings,
and authenticated Grafana provisioning. Only that test's volumes are removed.
It proves local mechanics, not real provider or price correctness.

`make test-community-recovery PYTHON=.venv/bin/python` is a separate opt-in
owned Docker recovery gate. Its harness must be run for the exact candidate;
the [recovery runbook](community-recovery.md) records the exercised local
roundtrip and its limits. Archive/crypto and recovered-startup unit tests run
in ordinary `make verify`.
