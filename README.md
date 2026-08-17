# Infrastructure Intelligence Platform

> Temporary neutral project name. Product and company naming are intentionally tracked separately under [`docs/research/brand`](docs/research/brand/README.md).

Infrastructure Intelligence Platform (IIP) is an architecture-first foundation for building a vendor-neutral control plane that understands infrastructure as a resource graph and event timeline, then lets governed agents investigate and act with evidence.

This repository now contains an **executable local reference slice across Roadmap Phases 1–5**. It includes live Kubernetes reconciliation with per-type cursor resume and watch-expiration recovery, durable membership and tombstones, tenant-scoped at-least-once outbox delivery through replaceable publishers, durable PostgreSQL resources/evidence/investigations/actions/audit, tenant-scoped projection verification and rebuild, measured full-schema backup/restore verification, automatic tenant/source ingestion-freshness sampling with optional OTLP/HTTP metrics export, optional bounded investigation trace export, backend-neutral bounded metric and log evidence queries, real Prometheus-compatible metric and Loki log adapters, value-minimized resource/deployment-change evidence, allowlisted and redacted repository/runbook context evidence, isolated optional tenant-bound OTLP/HTTP metrics and logs evidence receivers, normalized Kubernetes Event evidence with a live exact-scope read-only API adapter, a shared external workload-identity credential-broker client, deterministic auditable cross-signal investigation planning, durable asynchronous investigation dispatch with tenant-explicit worker claims, heartbeats and crash recovery, cooperative cancellation, unit-aware threshold assessment, explicit and scope-end rolling baseline/evaluation comparison, event-condition correlation, conservative document/log/change-count assessment from committed artifacts, adversarial instruction-boundary release scoring, evidence-backed investigation and evaluation, one-shot fail-closed governed actions, automatic non-replaying reconciliation of expired action attempts, an opt-in UID-bound Kubernetes restart adapter with server-side dry-run, readiness verification, and rollback, bounded plugin sessions, and a signed digest-pinned no-network Docker plugin runner. The local Phase 1 exit gate is complete, but the later phase exit gates are not: protected-catalog candidate generation and seasonal baselines, remote repository backends, production broker/dead-letter policy, a production credential issuer and interoperability gate, additional customer telemetry backends/signals, receiver workload-identity/mTLS and buffering, production telemetry/SLO windows, production-environment action interoperability, mediated plugin network/credentials, durable cross-restart plugin execution claims, design-partner operation, and legal/brand decisions remain. It is not yet a production system.

## Start here

1. Read the [product constitution](docs/product/constitution.md).
2. Read the [architecture overview](docs/architecture/overview.md).
3. Review the [contracts index](docs/specifications/README.md).
4. Follow the [initial roadmap](docs/roadmap/initial-roadmap.md).
5. When changing code with Codex, follow [AGENTS.md](AGENTS.md).

## Local quick start

Use Python 3.11 or newer and Node.js 24 for the TypeScript SDK boundary. Python and npm verification dependencies are locked for reproducible local and CI behavior. With Docker Desktop running, the durable product stack now has a one-command start:

```bash
make dev-up
```

The command creates protected local-only credentials under `.iip/`, builds and starts PostgreSQL, the API, and a durable workflow worker, waits until database connectivity and schema readiness are proven, and prints the operator token. The non-interactive worker dispatches investigations, closes expired action attempts without replaying impact, delivers outbox events to the explicit local structured-log sink, and samples the enrolled local ingestion source. Open [http://127.0.0.1:8080/console](http://127.0.0.1:8080/console) and enter that token. The browser console uses live tenant resources, graph/timeline queries, queued investigations, evidence, and a paginated governed-action queue; it does not display mock operational data. Run `make dev-credentials` to exercise proposal, independent approval, and one-shot execution with the three separate local identities.

Use `make dev-status` to inspect the containers, `make dev-credentials` to show the separate operator, approver, and executor tokens, `make test-local-product` to exercise the complete durable customer workflow without printing credentials, and `make dev-down` to stop the stack while preserving its database.

For repository verification and the isolated integration gates:

```bash
python3 -m pip install --requirement requirements/verify.txt
make verify
# With Docker Desktop running:
make test-postgres
make test-backup-restore
make test-otel
make test-otlp-receiver
make test-prometheus
make test-loki
make test-plugin-runner
make test-helm-install
make release-bundle PYTHON=.venv/bin/python
# With the local kind cluster and explicit kubeconfig:
IIP_KUBECONFIG=/absolute/path/to/.kube/config make test-kubernetes-events
IIP_KUBECONFIG=/absolute/path/to/.kube/config make test-kubernetes-actions
IIP_KUBECONFIG=/absolute/path/to/.kube/config make test-kubernetes-live
# Configure a local Bearer identity as described in docs/operations/local-development.md
# only when running the in-memory API outside Docker.
make run
```

Then, in another terminal:

```bash
curl http://localhost:8080/healthz
curl -X POST http://localhost:8080/v1/resources \
  -H 'content-type: application/json' \
  -H "authorization: Bearer $IIP_DEV_BEARER_TOKEN" \
  --data @contracts/examples/resource.json
```

The API authenticates a Bearer credential into actor, tenant, and role context, validates collection/resource boundaries, and exposes graph/timeline, ingestion-freshness, normalized metric, log, Kubernetes Event, resource-change, and repository/runbook context evidence queries. A separate optional process accepts independently authenticated OTLP metrics/logs on port `4318` without exposing control-plane routes or credentials. The API accepts bounded investigations synchronously or through durable background jobs that survive HTTP disconnects, exposes queue attempts and cooperative cancellation, and supports separately governed actions and plugin-session workflows. The action default is non-mutating; the opt-in Kubernetes API executor adds server dry-run and a doubly enabled live path. Default external provider backends honestly return no data; selected live adapters require protected endpoint, trust, scope, and credential configuration. The built-in change provider reads only tenant-scoped accepted observation history, while the file context adapter reads only cataloged files under a protected root. The default local profile remains in memory; the Docker profile persists operational records in PostgreSQL. The Helm chart has a strict closed values contract, applies packaged PostgreSQL migrations through a separately enabled database-only hook, keeps Services internal, can declare an existing-certificate TLS Ingress with exact controller NetworkPolicy authority, and can schedule checksum-complete logical backups into pre-created protected storage. Clean revisions can be packaged as an unsigned multi-platform release candidate with checksums, SPDX SBOM, SLSA provenance, chart, contracts, and SDK artifacts; see the [release procedure](docs/operations/release-artifacts.md).

## Repository map

| Path | Purpose |
| --- | --- |
| `api/` | Public protocol descriptions, starting with OpenAPI |
| `contracts/` | Machine-readable schemas and valid examples |
| `src/iip/` | Provider-neutral reference kernel and composition root |
| `sdks/` | Public Python and TypeScript client boundaries |
| `plugins/` | Public-contract-only plugins and conformance fixtures; vendor code stays outside the kernel |
| `deploy/` | Helm chart and local Kubernetes overlay |
| `docs/` | Living product, architecture, specifications, research, and roadmap |
| `scripts/` | Repository validation checks |
| `tests/` | Boundary and vertical-slice tests |

## Architectural stance

- Resource identity and event history are the system of record.
- Every agent conclusion must be traceable to evidence.
- Read, propose, approve, and execute are different authority levels.
- Vendor adapters and plugins depend on stable platform ports; the domain never depends on a vendor SDK.
- Contracts are versioned before implementations fan out.
- Tenant isolation and auditability are design inputs, not later hardening tasks.

## Current decisions and open questions

Accepted foundations live in [`docs/decisions`](docs/decisions/README.md). PostgreSQL is accepted as the initial resource/event and operational record substrate, the deterministic investigator/non-mutating action boundary is accepted as the safety baseline, and OTLP through an OpenTelemetry Collector is the accepted telemetry-portability direction. Tenant-scoped outbox dispatch with local structured-log and authenticated HTTPS publishers, optional outbound ingestion-metrics and terminal-investigation trace export, automatic exact-source freshness sampling, replaceable historical metric/log/Kubernetes Event/context evidence ports, the first Prometheus-compatible metric and Loki log adapters, isolated tenant-bound inbound OTLP metrics/logs receivers, a live exact-scope Kubernetes Event API adapter, a protected file-context adapter, OIDC/JWKS authentication, an external HTTPS policy-decision adapter, an external HTTPS credential-broker client with explicitly projected workload identity, auditable cross-signal request-candidate planning, tenant-scoped workflow workers, one-shot governed execution and non-replaying action reconciliation, the opt-in verified Kubernetes restart adapter, a signed no-network plugin runner, unit-aware threshold assessment, ordered two-window baseline comparison, event-condition correlation, and conservative document/log/change-count correlation from committed evidence are executable; production broker/dead-letter policy, production credential issuance/interoperability, protected-catalog candidate generation, remote repository adapters, additional production backends/signals, receiver workload identity/mTLS and buffering, export health, model-provider selection, mediated plugin connectivity, licensing, company, and brand decisions remain explicit roadmap work.

## Licensing

No open-source license has been selected yet. Until that decision is recorded, do not assume permission to redistribute this repository. See the [open-source and commercial boundary](docs/product/open-source-boundary.md).
