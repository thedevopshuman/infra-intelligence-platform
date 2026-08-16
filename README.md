# Infrastructure Intelligence Platform

> Temporary neutral project name. Product and company naming are intentionally tracked separately under [`docs/research/brand`](docs/research/brand/README.md).

Infrastructure Intelligence Platform (IIP) is an architecture-first foundation for building a vendor-neutral control plane that understands infrastructure as a resource graph and event timeline, then lets governed agents investigate and act with evidence.

This repository now contains an **executable local reference slice across Roadmap Phases 1–5**. It includes live Kubernetes reconciliation with per-type cursor resume and watch-expiration recovery, durable membership and tombstones, durable PostgreSQL resources/evidence/investigations/actions/audit, tenant-scoped projection verification and rebuild, measured full-schema backup/restore verification, measurable ingestion freshness/source lag with optional OTLP/HTTP metrics export, backend-neutral bounded metric evidence queries with a real Prometheus-compatible adapter, an optional tenant-bound OTLP/HTTP metrics evidence receiver, deterministic investigation-driven metric selection, evidence-backed investigation and evaluation, dry-run governed actions, and bounded plugin sessions. The local Phase 1 exit gate is complete, but the later phase exit gates are not: evidence-aware metric interpretation, a production credential broker, additional customer telemetry backends/signals, receiver isolation and workload identity, production telemetry/SLO windows, live mutation/rollback, isolated signed plugin execution, design-partner operation, and legal/brand decisions remain. It is not yet a production system.

## Start here

1. Read the [product constitution](docs/product/constitution.md).
2. Read the [architecture overview](docs/architecture/overview.md).
3. Review the [contracts index](docs/specifications/README.md).
4. Follow the [initial roadmap](docs/roadmap/initial-roadmap.md).
5. When changing code with Codex, follow [AGENTS.md](AGENTS.md).

## Local quick start

Use Python 3.11 or newer. Verification dependencies and the PostgreSQL driver are pinned for reproducible local and CI behavior.

```bash
python3 -m pip install --requirement requirements/verify.txt
make verify
# With Docker Desktop running:
make test-postgres
make test-backup-restore
make test-otel
make test-otlp-receiver
make test-prometheus
# With the local kind cluster and explicit kubeconfig:
IIP_KUBECONFIG=/absolute/path/to/.kube/config make test-kubernetes-live
# Configure a local Bearer identity as described in docs/operations/local-development.md.
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

The API authenticates a Bearer credential into actor, tenant, and role context, validates collection/resource boundaries, exposes graph/timeline, ingestion-freshness, normalized metric-evidence queries, and an independently authenticated optional OTLP metrics intake, runs a bounded investigation that can select scoped metric candidates, and supports separately governed dry-run action and plugin-session workflows. The default local profile remains in memory; the Docker profile persists operational records in PostgreSQL.

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

Accepted foundations live in [`docs/decisions`](docs/decisions/README.md). PostgreSQL is accepted as the initial resource/event and operational record substrate, the deterministic investigator/dry-run action boundary is accepted as the safety baseline, and OTLP through an OpenTelemetry Collector is the accepted telemetry-portability direction. Optional outbound ingestion-metrics export, a replaceable historical metric-evidence query port, its first Prometheus-compatible adapter, a tenant-bound inbound OTLP metrics receiver, and investigation-driven bounded metric selection are executable; evidence-aware numeric interpretation, an external short-lived credential broker, additional production backends/signals, production receiver isolation/identity, export health, model, workflow/policy, plugin-runner, licensing, company, and brand decisions remain explicit roadmap work.

## Licensing

No open-source license has been selected yet. Until that decision is recorded, do not assume permission to redistribute this repository. See the [open-source and commercial boundary](docs/product/open-source-boundary.md).
