# Living documentation

The documentation tree is the product and engineering system of record. A change is incomplete when behavior and documentation disagree.

## Product

- [Product constitution](product/constitution.md)
- [Vision and scope](product/vision-and-scope.md)
- [Open-source and commercial boundary](product/open-source-boundary.md)

## Architecture

- [System overview](architecture/overview.md)
- [Repository structure and dependency rules](architecture/repository-structure.md)
- [Investigation lifecycle](architecture/investigation-lifecycle.md)
- [Security, tenancy, and authority](architecture/security-tenancy.md)
- [Authentication boundary](architecture/authentication-boundary.md)
- [PostgreSQL resource and event substrate](architecture/postgresql-resource-event-substrate.md)
- [Evidence collection pipeline](architecture/evidence-collection-pipeline.md)
- [Ingestion freshness telemetry](architecture/ingestion-freshness-telemetry.md)
- [OpenTelemetry portability boundary](architecture/opentelemetry-portability.md)

## Specifications

- [Contracts index](specifications/README.md)
- [Resource contract](specifications/resource-contract.md)
- [Event contract](specifications/event-contract.md)
- [Evidence contract](specifications/evidence-contract.md)
- [Telemetry evidence request and result contracts](specifications/telemetry-evidence-contract.md)
- [OTLP metrics evidence contract](specifications/otlp-metrics-evidence-contract.md)
- [Investigation request and report contracts](specifications/investigation-contract.md)
- [Ingestion freshness report contract](specifications/ingestion-freshness-contract.md)
- [Evaluation scenario contract](specifications/evaluation-scenario-contract.md)
- [Agent contract](specifications/agent-contract.md)
- [Plugin contract](specifications/plugin-contract.md)

## Decisions and delivery

- [Architecture decision records](decisions/README.md)
- [Initial roadmap](roadmap/initial-roadmap.md)
- [Local development](operations/local-development.md)
- [OpenTelemetry metrics export](operations/opentelemetry-export.md)
- [Prometheus telemetry evidence](operations/prometheus-evidence.md)
- [OTLP metrics receiver](operations/otlp-metrics-receiver.md)
- [PostgreSQL backup and restore experiment](operations/postgresql-backup-restore.md)
- [Glossary](glossary.md)

## Research

- [OpenSRE reference analysis](research/opensre-reference-analysis.md)
- [Brand and company track](research/brand/README.md)

## Document status

Every normative page carries a status. `Draft` is open for change, `Accepted` is the current rule, and `Superseded` must link to its replacement. Dates use ISO 8601. Decision records are append-only once accepted; later decisions supersede rather than silently rewrite them.
