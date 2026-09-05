# Local AI FinOps reference dashboard

**Status:** Executable disposable Phase A reference topology

This Docker Desktop profile demonstrates the complete local metadata path:

```text
Bedrock-shaped OTel spans -> Collector -> isolated IIP receiver -> PostgreSQL
-> cost worker -> context-growth rule -> OTLP metrics -> Collector
-> Prometheus -> Grafana
```

It uses synthetic Bedrock-shaped spans and the repository's explicitly gated
test price catalog. It does not call AWS, represent current public pricing, or
claim live Bedrock auto-instrumentation compatibility.

## Start the visible demo

Install the pinned verification dependencies, start Docker Desktop, and run:

```bash
make ai-finops-up PYTHON=.venv/bin/python
```

The command creates mode-`0600` time-relative fixture configuration under
`.iip/`, builds the application image, starts seven containers, sends the
fixture through the official OTLP Python exporter, and refuses to report ready
until the ledger, cost, saving, Prometheus, Loki, and provisioned-dashboard
checks pass. Open:

- Grafana: [http://127.0.0.1:13000/d/iip-ai-finops](http://127.0.0.1:13000/d/iip-ai-finops)
- Prometheus: [http://127.0.0.1:19091](http://127.0.0.1:19091)
- control API: [http://127.0.0.1:18082](http://127.0.0.1:18082)

The dashboard answers:

1. how many successful requests were observed in the current windows;
2. their calculated-estimate cost, explicitly not invoice cost;
3. which protected service/model/region/environment scope produced spend;
4. how input tokens per request changed against the preceding window;
5. one evidence-backed potential saving; and
6. whether pricing and usage coverage are complete.

The fixture includes one allowlisted model absent from the price catalog, so
unpriced usage is visible instead of silently becoming zero cost. It replays a
previously committed batch to prove deduplication and separately sends a
content-bearing span that the isolated receiver must reject. No prompt,
response, raw payload, provider request ID, trace ID, span ID, usage ID, cost
ID, finding ID, or evidence ID becomes a metric label.

Inspect or stop the demo with:

```bash
make ai-finops-status PYTHON=.venv/bin/python
make ai-finops-down PYTHON=.venv/bin/python
```

The demo database is intentionally disposable and stored in container tmpfs;
`ai-finops-down` removes it. `ai-finops-up` refuses to reseed a running profile
because its comparison windows and ledger are one coherent snapshot.

## Repeatable release gate

The non-interactive gate uses the same topology and removes its containers on
completion:

```bash
make test-ai-finops PYTHON=.venv/bin/python
```

It validates Collector and Prometheus configuration before startup, then
asserts eight unique usage facts, four priced and four unpriced cost facts, one
committed finding, exact current-window aggregates, visible unpriced coverage,
dashboard provisioning, Loki readiness, and privacy-safe labels.

## Components and replacement boundaries

| Component | Reference responsibility | Customer replacement boundary |
| --- | --- | --- |
| OpenTelemetry Collector | Receives fixture spans, authenticates to IIP, and exposes IIP aggregates | Keep OTLP; replace processors, queues, auth, and exporters through Collector configuration. |
| IIP receiver | Tenant-bound metadata validation and durable normalized usage | Deploy the isolated Helm receiver with mTLS/SPIFFE and protected channels. |
| PostgreSQL | Authoritative append-only usage, cost, finding, event, and outbox facts | Operate a supported PostgreSQL service with backup/restore evidence. |
| IIP worker | Data-driven cost calculation and deterministic context-growth evaluation | Supply reviewed catalogs/profiles through protected deployment configuration. |
| Prometheus | Reference aggregate metric store | Replace with any backend accepting the customer's chosen Collector exporter. |
| Loki | Provisioned reference OTLP log destination | Optional; it is not an accounting source and the V0 finding panel uses bounded metrics. |
| Grafana | Provisioned five-question visualization | Point equivalent queries at the selected telemetry backend. |

## Security boundary

This profile is safe only as a loopback-bound disposable developer fixture. It
uses HTTP inside its private Compose network, a known fixture channel token,
PostgreSQL trust authentication, and anonymous read-only Grafana. Do not expose
its ports or configuration on a shared network. Production requires the Helm
mTLS/SPIFFE profile, protected credentials/catalogs, customer authentication,
network policy, durable Collector queues, and the qualification gates listed
in the AI FinOps roadmap.
