# Living documentation

The documentation tree is the product and engineering system of record. A change is incomplete when behavior and documentation disagree.

## Start learning

- [First Docker learning session](learning/first-session.md)
- [Learning preview release and limits](releases/learning-v0.84.0.md)
- [Public production-v1 roadmap](roadmap/public-v1-release-plan.md)

## Product

- [Product constitution](product/constitution.md)
- [Vision and scope](product/vision-and-scope.md)
- [Open-source and commercial boundary](product/open-source-boundary.md)
- [AI FinOps and generative-AI observability vision](product/ai-finops-vision.md)
- [First usable private-pilot release](product/private-pilot-v1.md)

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
- [AI economics architecture](architecture/ai-economics.md)

## Specifications

- [Contracts index](specifications/README.md)
- [Resource contract](specifications/resource-contract.md)
- [Event contract](specifications/event-contract.md)
- [Evidence contract](specifications/evidence-contract.md)
- [AI economics contracts](specifications/ai-economics-contracts.md)
- [AI savings finding read contract](specifications/ai-savings-finding-page-contract.md)
- [AI price-catalog qualification contracts](specifications/ai-price-catalog-qualification-contract.md)
- [Customer AI FinOps prerequisite contracts](specifications/customer-ai-finops-prerequisite-contract.md)
- [AI invocation observation contracts](specifications/ai-invocation-observation-contract.md)
- [AI model suitability report contract](specifications/ai-model-suitability-report-contract.md)
- [AI attribution contracts](specifications/ai-attribution-contracts.md)
- [AI economics telemetry contract](specifications/ai-economics-telemetry-contract.md)
- [Bedrock instrumentation compatibility report](specifications/bedrock-instrumentation-compatibility-contract.md)
- [OpenAI instrumentation compatibility report](specifications/openai-instrumentation-compatibility-contract.md)
- [Telemetry evidence request and result contracts](specifications/telemetry-evidence-contract.md)
- [Log evidence request and result contracts](specifications/log-evidence-contract.md)
- [OTLP metrics evidence contract](specifications/otlp-metrics-evidence-contract.md)
- [OTLP logs evidence contract](specifications/otlp-logs-evidence-contract.md)
- [Investigation request and report contracts](specifications/investigation-contract.md)
- [Ingestion freshness report contract](specifications/ingestion-freshness-contract.md)
- [Evaluation scenario contract](specifications/evaluation-scenario-contract.md)
- [Agent contract](specifications/agent-contract.md)
- [Plugin contract](specifications/plugin-contract.md)
- [Plugin compatibility report contract](specifications/plugin-compatibility-contract.md)
- [Plugin action mediation contracts](specifications/plugin-action-mediation-contract.md)
- [Customer deployment preflight report](specifications/customer-deployment-preflight-report-contract.md)
- [Customer continuity qualification report](specifications/customer-continuity-qualification-report-contract.md)
- [Customer OIDC qualification contracts](specifications/customer-oidc-qualification-report-contract.md)
- [Customer policy qualification contracts](specifications/customer-policy-qualification-report-contract.md)
- [Customer deployment qualification report](specifications/customer-deployment-qualification-report-contract.md)
- [Control-plane load qualification report](specifications/control-plane-load-qualification-report-contract.md)
- [Customer sustained core-workload contracts](specifications/customer-sustained-workload-qualification-contract.md)
- [Customer planned failure-overlap contracts](specifications/customer-failure-overlap-qualification-contract.md)
- [Customer private-pilot readiness contracts](specifications/customer-pilot-readiness-contract.md)
- [Kubernetes availability qualification report](specifications/kubernetes-availability-qualification-report-contract.md)
- [Workflow worker health contract](specifications/worker-health-contract.md)

## Decisions and delivery

- [Architecture decision records](decisions/README.md)
- [Initial roadmap](roadmap/initial-roadmap.md)
- [AI FinOps roadmap](roadmap/ai-finops-roadmap.md)
- [Local development](operations/local-development.md)
- [Persistent community installation](operations/community-installation.md)
- [Offline encrypted community recovery](operations/community-recovery.md)
- [Stopped-stack community transport rotation](operations/community-trust-rotation.md)
- [Transactional outbox event delivery](operations/event-delivery.md)
- [Helm deployment and schema migration](operations/helm-deployment.md)
- [Customer deployment preflight](operations/customer-deployment-preflight.md)
- [Post-install deployment diagnostics](operations/deployment-diagnostics.md)
- [Customer control-plane continuity qualification](operations/customer-continuity-qualification.md)
- [Customer OIDC prerequisite qualification](operations/customer-oidc-qualification.md)
- [Customer policy-engine bundle qualification](operations/customer-policy-qualification.md)
- [Customer deployment qualification](operations/customer-deployment-qualification.md)
- [Customer control-plane load qualification](operations/control-plane-load-qualification.md)
- [Customer sustained core-workload qualification](operations/customer-sustained-workload-qualification.md)
- [Customer planned failure-overlap qualification](operations/customer-failure-overlap-qualification.md)
- [Customer private-pilot readiness](operations/customer-pilot-readiness.md)
- [Private-pilot onboarding and handoff](operations/private-pilot-onboarding.md)
- [Private-pilot feedback and telemetry](operations/private-pilot-feedback.md)
- [Kubernetes planned-disruption availability qualification](operations/kubernetes-availability-qualification.md)
- [Release artifacts and supply-chain evidence](operations/release-artifacts.md)
- [OpenTelemetry metrics export](operations/opentelemetry-export.md)
- [Operational alert-policy handoff](operations/operational-alerts.md)
- [Prometheus telemetry evidence](operations/prometheus-evidence.md)
- [OTLP metrics receiver](operations/otlp-metrics-receiver.md)
- [AI usage OTLP trace receiver](operations/ai-usage-receiver.md)
- [AI usage attribution](operations/ai-attribution.md)
- [AI cost engine](operations/ai-cost-engine.md)
- [AI savings engine](operations/ai-savings-engine.md)
- [Local AI FinOps reference dashboard](operations/ai-finops-local-demo.md)
- [Bedrock instrumentation qualification](operations/bedrock-instrumentation-qualification.md)
- [Customer AI FinOps prerequisite qualification](operations/customer-ai-finops-prerequisites.md)
- [OpenAI instrumentation qualification](operations/openai-instrumentation-qualification.md)
- [Log evidence and OTLP logs intake](operations/log-evidence.md)
- [Resource and deployment change evidence](operations/resource-change-evidence.md)
- [Repository and runbook context evidence](operations/context-evidence.md)
- [PostgreSQL backup and restore experiment](operations/postgresql-backup-restore.md)
- [PostgreSQL physical continuity qualification](operations/postgresql-continuity.md)
- [Customer PostgreSQL primary-promotion qualification](operations/customer-postgresql-continuity-qualification.md)
- [Glossary](glossary.md)

Repository-level [support](../SUPPORT.md) and
[security](../SECURITY.md) policies define the public/private reporting
boundary. A customer pilot still requires separately established accountable
owners and private channels.

## Research

- [OpenSRE reference analysis](research/opensre-reference-analysis.md)
- [Brand and company track](research/brand/README.md)

## Document status

Every normative page carries a status. `Draft` is open for change, `Accepted` is the current rule, and `Superseded` must link to its replacement. Dates use ISO 8601. Decision records are append-only once accepted; later decisions supersede rather than silently rewrite them.
