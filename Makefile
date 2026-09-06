.PHONY: help install-verify-deps validate validate-schemas test test-console-javascript test-typescript test-evidence-redaction test-deployment-preflight preflight-deployment-live verify-deployment-preflight-report test-deployment-diagnostics diagnose-deployment verify-deployment-diagnostic-report test-ingress-availability qualify-ingress-availability verify-ingress-availability-report test-customer-oidc-qualification qualify-customer-oidc verify-customer-oidc-qualification-report test-customer-policy-qualification qualify-customer-policy verify-customer-policy-qualification-report test-customer-credential-broker-qualification qualify-customer-credential-broker verify-customer-credential-broker-qualification-report test-customer-github-context-qualification qualify-customer-github-context verify-customer-github-context-qualification-report test-customer-bedrock-qualification qualify-customer-bedrock verify-customer-bedrock-qualification-report test-customer-ai-finops-prerequisites qualify-customer-ai-finops-prerequisites verify-customer-ai-finops-prerequisite-report test-customer-ai-finops-flow qualify-customer-ai-finops-flow verify-customer-ai-finops-flow-report test-customer-pilot-readiness assess-customer-pilot-readiness verify-customer-pilot-readiness-report test-customer-failure-overlap assess-customer-failure-overlap verify-customer-failure-overlap-report test-customer-otlp-receiver-qualification qualify-customer-otlp-receiver verify-customer-otlp-receiver-qualification-report test-customer-continuity qualify-customer-continuity verify-customer-continuity-report test-customer-processing-continuity qualify-customer-processing-continuity verify-customer-processing-continuity-report test-customer-sustained-workload qualify-customer-sustained-workload verify-customer-sustained-workload-report test-customer-postgresql-continuity qualify-customer-postgresql-continuity verify-customer-postgresql-continuity-report test-customer-deployment-qualification qualify-customer-deployment verify-customer-deployment-qualification-report test-control-plane-load qualify-control-plane-load verify-control-plane-load-report test-kubernetes-availability qualify-kubernetes-availability verify-kubernetes-availability-report test-postgres test-capacity test-credential-broker test-oidc test-oidc-verifier test-oidc-browser test-policy-engine test-github-context qualify-github-context verify-github-context-report test-external-secrets test-backup-restore verify-backup-restore-report test-postgres-continuity verify-postgres-continuity-report test-otel test-operational-alerts test-otlp-receiver test-ai-finops verify-ai-finops-runtime-report test-aws-bedrock-price-import import-aws-bedrock-price-catalog verify-aws-bedrock-price-import test-ai-price-catalog-qualification qualify-ai-price-catalog verify-ai-price-catalog-report test-bedrock-instrumentation test-bedrock-live test-openai-instrumentation test-openai-live test-prometheus test-collector-queue-loss test-loki test-opensearch test-kubernetes-events test-kubernetes-actions test-kubernetes-live test-plugin-runner test-plugin-compatibility test-local-product test-helm-install test-release-install test-release-upgrade qualify-release test-release-publication test-release-signatures qualify-release-signatures verify-release-signature-report test-release-vulnerabilities qualify-release-vulnerabilities verify-release-vulnerability-report test-release-readiness assess-release-readiness verify-release-readiness-report qualify-local-release db-migrate helm-lint verify run package-chart release-bundle verify-release-bundle verify-release-qualification dev-init dev-up dev-status dev-credentials dev-down ai-finops-up ai-finops-status ai-finops-down

PYTHON ?= python3
HELM ?= helm
KUBECTL ?= kubectl
DOCKER ?= docker
KIND ?= kind
NPM ?= npm
COSIGN ?= cosign
IIP_DATABASE_RECOVERY_REPORT ?= dist/postgresql-recovery-qualification-report.json
IIP_AI_FINOPS_RUNTIME_REPORT ?= dist/ai-finops-runtime-compatibility-report.json
IIP_DATABASE_CONTINUITY_REPORT ?= dist/postgresql-continuity-qualification-report.json
IIP_DEPLOYMENT_PREFLIGHT_REPORT ?= dist/customer-deployment-preflight-report.json
IIP_DEPLOYMENT_PROFILE ?= production-core-v1
IIP_DEPLOYMENT_VALUES ?= deploy/helm/infra-intelligence/examples/production-core.values.yaml
IIP_DEPLOYMENT_NAMESPACE ?= iip-system
IIP_KUBERNETES_CONTEXT ?=
IIP_DEPLOYMENT_DIAGNOSTIC_REPORT ?= dist/deployment-diagnostic-report.json
IIP_DIAGNOSTIC_RELEASE_NAME ?= iip
IIP_DIAGNOSTIC_IMAGE_DIGEST ?=
IIP_INGRESS_QUALIFICATION_REPORT ?= dist/ingress-availability-qualification-report.json
IIP_INGRESS_BASE_URL ?=
IIP_INGRESS_TOKEN_FILE ?=
IIP_INGRESS_IMAGE_DIGEST ?=
IIP_INGRESS_CA_FILE ?=
IIP_INGRESS_SAMPLES ?= 100
IIP_INGRESS_MINIMUM_AVAILABILITY_BASIS_POINTS ?= 9990
IIP_INGRESS_MAXIMUM_P95_LATENCY_MILLISECONDS ?= 2000
IIP_INGRESS_REQUEST_TIMEOUT_MILLISECONDS ?= 2000
IIP_INGRESS_INTERVAL_MILLISECONDS ?= 1000
IIP_CUSTOMER_OIDC_REPORT ?= dist/customer-oidc-qualification-report.json
IIP_CUSTOMER_OIDC_PROFILE ?=
IIP_CUSTOMER_OIDC_API_BASE_URL ?= $(IIP_PROCESSING_API_BASE_URL)
IIP_CUSTOMER_OIDC_ACCESS_TOKEN_FILE ?=
IIP_CUSTOMER_OIDC_API_CA_FILE ?=
IIP_CUSTOMER_OIDC_ISSUER_CA_FILE ?=
IIP_CUSTOMER_OIDC_IMAGE_DIGEST ?= $(IIP_CONTINUITY_IMAGE_DIGEST)
IIP_CUSTOMER_OIDC_ALLOW_IDENTITY_OBSERVATION ?= false
IIP_CUSTOMER_POLICY_REPORT ?= dist/customer-policy-qualification-report.json
IIP_CUSTOMER_POLICY_PROFILE ?=
IIP_CUSTOMER_POLICY_ENDPOINT ?=
IIP_CUSTOMER_POLICY_BEARER_TOKEN_FILE ?=
IIP_CUSTOMER_POLICY_CA_FILE ?=
IIP_CUSTOMER_POLICY_IMAGE_DIGEST ?= $(IIP_CONTINUITY_IMAGE_DIGEST)
IIP_CUSTOMER_POLICY_ALLOW_OBSERVATION ?= false
IIP_CUSTOMER_CREDENTIAL_BROKER_REPORT ?= dist/customer-credential-broker-qualification-report.json
IIP_CUSTOMER_CREDENTIAL_BROKER_PROFILE ?=
IIP_CUSTOMER_CREDENTIAL_BROKER_ENDPOINT ?=
IIP_CUSTOMER_CREDENTIAL_BROKER_WORKLOAD_TOKEN_FILE ?=
IIP_CUSTOMER_CREDENTIAL_BROKER_CA_FILE ?=
IIP_CUSTOMER_CREDENTIAL_BROKER_IMAGE_DIGEST ?= $(IIP_CONTINUITY_IMAGE_DIGEST)
IIP_CUSTOMER_CREDENTIAL_BROKER_ALLOW_OBSERVATION ?= false
IIP_CUSTOMER_GITHUB_CONTEXT_REPORT ?= dist/customer-github-context-qualification-report.json
IIP_CUSTOMER_GITHUB_CONTEXT_PROFILE ?=
IIP_CUSTOMER_GITHUB_CONTEXT_INTEGRATION_CONFIG ?=
IIP_CUSTOMER_GITHUB_CONTEXT_GITHUB_CA_FILE ?=
IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_REPORT ?=
IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_PROFILE ?=
IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_ENDPOINT ?=
IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_WORKLOAD_TOKEN_FILE ?=
IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_CA_FILE ?=
IIP_CUSTOMER_GITHUB_CONTEXT_IMAGE_DIGEST ?= $(IIP_CONTINUITY_IMAGE_DIGEST)
IIP_CUSTOMER_GITHUB_CONTEXT_ALLOW_OBSERVATION ?= false
IIP_CUSTOMER_BEDROCK_REPORT ?= dist/customer-bedrock-qualification-report.json
IIP_CUSTOMER_BEDROCK_LIVE_REPORT ?= dist/customer-bedrock-live-compatibility-report.json
IIP_CUSTOMER_BEDROCK_PROFILE ?=
IIP_CUSTOMER_BEDROCK_AWS_CREDENTIALS_FILE ?=
IIP_CUSTOMER_BEDROCK_IMAGE_DIGEST ?= $(IIP_CONTINUITY_IMAGE_DIGEST)
IIP_CUSTOMER_BEDROCK_ALLOW_PROVIDER_CALL ?= false
IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE ?=
IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_REPORT ?= dist/customer-ai-finops-prerequisite-report.json
IIP_CUSTOMER_AI_FINOPS_FLOW_PROFILE ?=
IIP_CUSTOMER_AI_FINOPS_FLOW_REPORT ?= dist/customer-ai-finops-flow-qualification-report.json
IIP_CUSTOMER_AI_FINOPS_FLOW_RUN_EVIDENCE ?= dist/customer-ai-finops-flow-run-evidence.json
IIP_CUSTOMER_AI_FINOPS_FLOW_LIVE_REPORT ?= dist/customer-ai-finops-flow-live-compatibility-report.json
IIP_CUSTOMER_AI_FINOPS_FLOW_OBSERVATION ?= dist/customer-ai-finops-flow-invocation-observation.json
IIP_CUSTOMER_AI_FINOPS_FLOW_BEDROCK_PROFILE ?= $(IIP_CUSTOMER_BEDROCK_PROFILE)
IIP_CUSTOMER_AI_FINOPS_FLOW_AWS_CREDENTIALS_FILE ?= $(IIP_CUSTOMER_BEDROCK_AWS_CREDENTIALS_FILE)
IIP_CUSTOMER_AI_FINOPS_FLOW_CONTROL_HEADERS ?=
IIP_CUSTOMER_AI_FINOPS_FLOW_OTLP_HEADERS ?=
IIP_CUSTOMER_AI_FINOPS_FLOW_PROMETHEUS_HEADERS ?=
IIP_CUSTOMER_AI_FINOPS_FLOW_GRAFANA_HEADERS ?=
IIP_CUSTOMER_AI_FINOPS_FLOW_CONTROL_CA ?=
IIP_CUSTOMER_AI_FINOPS_FLOW_OTLP_CA ?=
IIP_CUSTOMER_AI_FINOPS_FLOW_PROMETHEUS_CA ?=
IIP_CUSTOMER_AI_FINOPS_FLOW_GRAFANA_CA ?=
IIP_CUSTOMER_AI_FINOPS_FLOW_OTLP_CLIENT_CERT ?=
IIP_CUSTOMER_AI_FINOPS_FLOW_OTLP_CLIENT_KEY ?=
IIP_CUSTOMER_AI_FINOPS_FLOW_ALLOW_PROVIDER_CALL ?= false
IIP_CUSTOMER_PILOT_READINESS_PROFILE ?=
IIP_CUSTOMER_PILOT_READINESS_REPORT ?= dist/customer-pilot-readiness-report.json
IIP_CUSTOMER_OTLP_REPORT ?= dist/customer-otlp-receiver-qualification-report.json
IIP_CUSTOMER_OTLP_PROFILE ?=
IIP_CUSTOMER_OTLP_API_BASE_URL ?=
IIP_CUSTOMER_OTLP_RECEIVER_ENDPOINT ?=
IIP_CUSTOMER_OTLP_API_TOKEN_FILE ?=
IIP_CUSTOMER_OTLP_API_CA_FILE ?=
IIP_CUSTOMER_OTLP_METRICS_TOKEN_FILE ?=
IIP_CUSTOMER_OTLP_LOGS_TOKEN_FILE ?=
IIP_CUSTOMER_OTLP_TRACES_TOKEN_FILE ?=
IIP_CUSTOMER_OTLP_RECEIVER_CA_FILE ?=
IIP_CUSTOMER_OTLP_CLIENT_CERTIFICATE_FILE ?=
IIP_CUSTOMER_OTLP_CLIENT_KEY_FILE ?=
IIP_CUSTOMER_OTLP_IMAGE_DIGEST ?= $(IIP_CONTINUITY_IMAGE_DIGEST)
IIP_CUSTOMER_OTLP_ALLOW_OBSERVATION ?= false
IIP_CONTINUITY_REPORT ?= dist/customer-continuity-qualification-report.json
IIP_CONTINUITY_INGRESS_REPORT ?= dist/customer-continuity-ingress-report.json
IIP_CONTINUITY_BASE_URL ?=
IIP_CONTINUITY_TOKEN_FILE ?=
IIP_CONTINUITY_IMAGE_DIGEST ?=
IIP_CONTINUITY_CA_FILE ?=
IIP_CONTINUITY_DEPLOYMENT ?= iip-infra-intelligence
IIP_CONTINUITY_ALLOW_DISRUPTION ?= false
IIP_CONTINUITY_SAMPLES ?= 721
IIP_CONTINUITY_INTERVAL_MILLISECONDS ?= 500
IIP_CONTINUITY_MINIMUM_WINDOW_SECONDS ?= 300
IIP_CONTINUITY_MINIMUM_BASELINE_SECONDS ?= 60
IIP_CONTINUITY_MINIMUM_POST_RECOVERY_SECONDS ?= 60
IIP_CONTINUITY_MINIMUM_AVAILABILITY_BASIS_POINTS ?= 9990
IIP_CONTINUITY_MAXIMUM_P95_LATENCY_MILLISECONDS ?= 2000
IIP_CONTINUITY_REQUEST_TIMEOUT_MILLISECONDS ?= 2000
IIP_CONTINUITY_MAXIMUM_RECOVERY_SECONDS ?= 120
IIP_PROCESSING_REPORT ?= dist/customer-processing-continuity-qualification-report.json
IIP_PROCESSING_API_BASE_URL ?=
IIP_PROCESSING_API_TOKEN_FILE ?=
IIP_PROCESSING_OTLP_BASE_URL ?=
IIP_PROCESSING_OTLP_TOKEN_FILE ?=
IIP_PROCESSING_OTLP_CLIENT_CERT_FILE ?=
IIP_PROCESSING_OTLP_CLIENT_KEY_FILE ?=
IIP_PROCESSING_API_CA_FILE ?=
IIP_PROCESSING_OTLP_CA_FILE ?=
IIP_PROCESSING_PROFILE ?=
IIP_PROCESSING_IMAGE_DIGEST ?=
IIP_PROCESSING_WORKER_DEPLOYMENT ?= iip-infra-intelligence-worker
IIP_PROCESSING_RECEIVER_DEPLOYMENT ?= iip-infra-intelligence-otlp-receiver
IIP_PROCESSING_ALLOW_DISRUPTION ?= false
IIP_PROCESSING_ATTEMPTS_PER_PHASE ?= 20
IIP_PROCESSING_PROBE_INTERVAL_MILLISECONDS ?= 250
IIP_PROCESSING_MAXIMUM_WORKFLOW_MILLISECONDS ?= 60000
IIP_PROCESSING_MAXIMUM_RECOVERY_MILLISECONDS ?= 120000
IIP_PROCESSING_REQUEST_TIMEOUT_MILLISECONDS ?= 2000
IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE ?=
IIP_CUSTOMER_SUSTAINED_WORKLOAD_REPORT ?= dist/customer-sustained-workload-qualification-report.json
IIP_CUSTOMER_SUSTAINED_WORKLOAD_API_TOKEN_FILE ?=
IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_TOKEN_FILE ?=
IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_CLIENT_CERT_FILE ?=
IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_CLIENT_KEY_FILE ?=
IIP_CUSTOMER_SUSTAINED_WORKLOAD_API_CA_FILE ?=
IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_CA_FILE ?=
IIP_CUSTOMER_SUSTAINED_WORKLOAD_ALLOW_TRAFFIC ?= false
IIP_CUSTOMER_FAILURE_OVERLAP_PROFILE ?=
IIP_CUSTOMER_FAILURE_OVERLAP_REPORT ?= dist/customer-failure-overlap-qualification-report.json
IIP_CUSTOMER_POSTGRESQL_REPORT ?= dist/customer-postgresql-continuity-qualification-report.json
IIP_CUSTOMER_POSTGRESQL_PROFILE ?=
IIP_CUSTOMER_POSTGRESQL_HOST ?=
IIP_CUSTOMER_POSTGRESQL_PORT ?= 5432
IIP_CUSTOMER_POSTGRESQL_PASSWORD_FILE ?=
IIP_CUSTOMER_POSTGRESQL_CA_FILE ?=
IIP_CUSTOMER_POSTGRESQL_CLIENT_CERT_FILE ?=
IIP_CUSTOMER_POSTGRESQL_CLIENT_KEY_FILE ?=
IIP_CUSTOMER_POSTGRESQL_ALLOW_FAILOVER_OBSERVATION ?= false
IIP_CUSTOMER_POSTGRESQL_ATTEMPTS_PER_PHASE ?= 20
IIP_CUSTOMER_POSTGRESQL_PROBE_INTERVAL_MILLISECONDS ?= 250
IIP_CUSTOMER_POSTGRESQL_MAXIMUM_PROMOTION_MILLISECONDS ?= 300000
IIP_CUSTOMER_POSTGRESQL_MAXIMUM_WORKFLOW_MILLISECONDS ?= 60000
IIP_CUSTOMER_POSTGRESQL_REQUEST_TIMEOUT_MILLISECONDS ?= 2000
IIP_CUSTOMER_POSTGRESQL_MINIMUM_API_AVAILABILITY_BASIS_POINTS ?= 9500
IIP_CUSTOMER_POSTGRESQL_MINIMUM_RECEIVER_AVAILABILITY_BASIS_POINTS ?= 9500
IIP_CUSTOMER_POSTGRESQL_MAXIMUM_CONSECUTIVE_FAILURES ?= 20
IIP_CUSTOMER_DEPLOYMENT_QUALIFICATION_REPORT ?= dist/customer-deployment-qualification-report.json
IIP_CUSTOMER_QUALIFICATION_VALUES ?= $(IIP_DEPLOYMENT_VALUES)
IIP_CUSTOMER_QUALIFICATION_MAXIMUM_EVIDENCE_AGE_SECONDS ?= 86400
IIP_CUSTOMER_QUALIFICATION_MAXIMUM_CLOCK_SKEW_SECONDS ?= 300
IIP_CONTROL_PLANE_LOAD_REPORT ?= dist/control-plane-load-qualification-report.json
IIP_CONTROL_PLANE_LOAD_BASE_URL ?=
IIP_CONTROL_PLANE_LOAD_TOKEN_FILE ?=
IIP_CONTROL_PLANE_LOAD_IMAGE_DIGEST ?=
IIP_CONTROL_PLANE_LOAD_CA_FILE ?=
IIP_CONTROL_PLANE_LOAD_ALLOW_TRAFFIC ?= false
IIP_CONTROL_PLANE_LOAD_DURATION_SECONDS ?= 300
IIP_CONTROL_PLANE_LOAD_REQUESTS_PER_SECOND ?= 10
IIP_CONTROL_PLANE_LOAD_CONCURRENCY ?= 8
IIP_CONTROL_PLANE_LOAD_MINIMUM_SUCCESS_BASIS_POINTS ?= 9990
IIP_CONTROL_PLANE_LOAD_MAXIMUM_SCHEDULER_MISS_BASIS_POINTS ?= 10
IIP_CONTROL_PLANE_LOAD_MAXIMUM_P95_MILLISECONDS ?= 2000
IIP_CONTROL_PLANE_LOAD_MAXIMUM_P99_MILLISECONDS ?= 5000
IIP_CONTROL_PLANE_LOAD_REQUEST_TIMEOUT_MILLISECONDS ?= 2000
IIP_CONTROL_PLANE_LOAD_MAXIMUM_SCHEDULER_LAG_MILLISECONDS ?= 1000
IIP_KUBERNETES_AVAILABILITY_REPORT ?= dist/kubernetes-availability-qualification-report.json
IIP_GITHUB_CONTEXT_COMPATIBILITY_REPORT ?= dist/github-context-compatibility-report.json
IIP_RELEASE_SIGNATURE_POLICY ?=
IIP_RELEASE_SIGNATURE_REPORT ?= dist/release-signature-verification-report.json
IIP_RELEASE_PUBLICATION_REPORT ?= dist/release-publication-report.json
IIP_RELEASE_VULNERABILITY_POLICY ?= contracts/examples/release-vulnerability-policy.json
IIP_RELEASE_VULNERABILITY_REPORT ?= dist/release-vulnerability-qualification-report.json
IIP_RELEASE_EVIDENCE_DIR ?= dist
IIP_RELEASE_READINESS_REPORT ?= dist/release-readiness-report.json
IIP_UPGRADE_FROM_REVISION ?=
IIP_AI_PRICE_CATALOG_FILE ?=
IIP_AI_PRICE_QUALIFICATION_POLICY ?=
IIP_AI_PRICE_QUALIFICATION_REPORT ?= dist/ai-price-catalog-qualification-report.json
IIP_AWS_BEDROCK_PRICE_IMPORT_POLICY ?=
IIP_AWS_BEDROCK_PRICE_SOURCE ?=
IIP_AWS_BEDROCK_PRICE_CATALOG ?= dist/aws-bedrock-price-catalog.json
IIP_AWS_BEDROCK_PRICE_IMPORT_REPORT ?= dist/aws-bedrock-price-import-report.json

help:
	@echo "install-verify-deps Install pinned verification-only Python dependencies"
	@echo "validate      Validate contracts, links, and package boundaries"
	@echo "validate-schemas Validate contract examples against JSON Schemas"
	@echo "test          Run the reference-kernel and SDK tests"
	@echo "test-console-javascript Validate defensive AI Economics rendering inputs"
	@echo "test-typescript Install locked TypeScript tooling and type-check the SDK"
	@echo "test-evidence-redaction Validate additive tenant redaction policy and deployment wiring"
	@echo "test-deployment-preflight Validate the sanitized core, GitHub-context, and AI profiles"
	@echo "preflight-deployment-live Check a customer values file and explicit Kubernetes context"
	@echo "verify-deployment-preflight-report Verify current source/configuration-bound preflight evidence"
	@echo "test-deployment-diagnostics Validate minimized post-install diagnostic semantics"
	@echo "diagnose-deployment Inspect one exact Kubernetes release without logs or secrets"
	@echo "verify-deployment-diagnostic-report Verify retained point-in-time diagnostic evidence"
	@echo "test-ingress-availability Exercise the minimized external probe against real local routes"
	@echo "qualify-ingress-availability Qualify one HTTPS customer ingress and exact release identity"
	@echo "verify-ingress-availability-report Verify clean current ingress qualification evidence"
	@echo "test-customer-oidc-qualification Validate external OIDC evidence and safe client boundaries"
	@echo "qualify-customer-oidc Qualify one customer issuer and deployed release"
	@echo "verify-customer-oidc-qualification-report Rebind retained OIDC evidence"
	@echo "test-customer-policy-qualification Validate external policy-bundle evidence"
	@echo "qualify-customer-policy Qualify reviewed customer allow/deny policy cases"
	@echo "verify-customer-policy-qualification-report Rebind retained customer policy evidence"
	@echo "test-customer-credential-broker-qualification Validate customer broker authority evidence"
	@echo "qualify-customer-credential-broker Qualify one customer broker and exact authority tuple"
	@echo "verify-customer-credential-broker-qualification-report Rebind retained customer broker evidence"
	@echo "test-customer-github-context-qualification Validate exact customer GitHub context evidence"
	@echo "qualify-customer-github-context Read one immutable customer GitHub document through the broker"
	@echo "verify-customer-github-context-qualification-report Rebind retained customer GitHub evidence"
	@echo "test-customer-bedrock-qualification Validate minimized live Bedrock evidence and credential isolation"
	@echo "qualify-customer-bedrock Make one protected exact customer Bedrock call"
	@echo "verify-customer-bedrock-qualification-report Rebind retained customer Bedrock evidence"
	@echo "test-customer-ai-finops-prerequisites Validate minimized cross-bound AI FinOps prerequisites"
	@echo "qualify-customer-ai-finops-prerequisites Bind current customer AI FinOps prerequisite reports"
	@echo "verify-customer-ai-finops-prerequisite-report Rebind retained AI FinOps prerequisite evidence"
	@echo "test-customer-ai-finops-flow Validate same-invocation customer flow evidence"
	@echo "qualify-customer-ai-finops-flow Make and bind one Bedrock-to-dashboard customer call"
	@echo "verify-customer-ai-finops-flow-report Rebind retained same-invocation evidence"
	@echo "test-customer-pilot-readiness Validate exact pre-pilot evidence aggregation"
	@echo "assess-customer-pilot-readiness Bind signed release, deployment, sustained load, and live AI evidence"
	@echo "verify-customer-pilot-readiness-report Rebind retained private-pilot candidate evidence"
	@echo "test-customer-otlp-receiver-qualification Validate pinned Collector-to-customer receiver evidence"
	@echo "qualify-customer-otlp-receiver Deliver metrics, logs, and GenAI traces through a pinned Collector"
	@echo "verify-customer-otlp-receiver-qualification-report Rebind retained customer receiver evidence"
	@echo "test-customer-continuity Validate the customer continuity contract and safe orchestrator"
	@echo "qualify-customer-continuity Probe HTTPS while evicting one explicitly selected API pod"
	@echo "verify-customer-continuity-report Verify exact customer continuity and ingress evidence"
	@echo "test-customer-processing-continuity Validate worker/receiver processing evidence"
	@echo "qualify-customer-processing-continuity Evict worker and receiver pods under live processing"
	@echo "verify-customer-processing-continuity-report Recompute exact processing evidence bindings"
	@echo "test-customer-sustained-workload Validate bounded mixed customer workload evidence"
	@echo "qualify-customer-sustained-workload Run API, durable OTLP, and investigation traffic"
	@echo "verify-customer-sustained-workload-report Rebind retained sustained workload evidence"
	@echo "test-customer-failure-overlap Validate customer-approved planned failure-overlap evidence"
	@echo "assess-customer-failure-overlap Correlate planned continuity windows inside sustained traffic"
	@echo "verify-customer-failure-overlap-report Rebind exact overlap profiles and source evidence"
	@echo "test-customer-postgresql-continuity Validate customer PostgreSQL promotion evidence"
	@echo "qualify-customer-postgresql-continuity Observe an externally initiated primary promotion"
	@echo "verify-customer-postgresql-continuity-report Recompute exact PostgreSQL target evidence"
	@echo "test-customer-deployment-qualification Validate exact customer evidence aggregation"
	@echo "qualify-customer-deployment Bind install, ingress, OIDC, policy, broker, continuity, processing, database, and health evidence"
	@echo "verify-customer-deployment-qualification-report Recompute the customer evidence chain"
	@echo "test-control-plane-load Validate bounded fixed-rate load evidence semantics"
	@echo "qualify-control-plane-load Run explicitly enabled external read load against one release"
	@echo "verify-control-plane-load-report Verify retained load evidence and exact target identity"
	@echo "test-kubernetes-availability Validate the planned-disruption report and harness"
	@echo "qualify-kubernetes-availability Prove API, durable OTLP intake, and worker completion during an owned Kind drain"
	@echo "verify-kubernetes-availability-report Verify clean current planned-disruption evidence"
	@echo "test-postgres Run PostgreSQL integration tests with Docker Desktop"
	@echo "test-capacity Certify large-tenant investigation dispatch capacity with PostgreSQL"
	@echo "test-credential-broker Certify the external broker client over local TLS"
	@echo "test-oidc     Certify OIDC/JWKS and browser PKCE boundaries over local TLS"
	@echo "test-oidc-verifier Certify OIDC/JWKS access-token verification over local TLS"
	@echo "test-oidc-browser Certify the real browser authorization-code/PKCE flow over local TLS"
	@echo "test-policy-engine Certify external policy decisions over local TLS"
	@echo "test-github-context Exercise the protected GitHub adapter and real-TLS fixture"
	@echo "qualify-github-context Retain clean-current GitHub adapter compatibility evidence"
	@echo "verify-github-context-report Verify clean-current GitHub adapter evidence"
	@echo "test-external-secrets Certify exact-key secret synchronization and rotation on local Kind"
	@echo "test-backup-restore Measure and verify PostgreSQL recovery with Docker Desktop"
	@echo "verify-backup-restore-report Verify clean-current PostgreSQL recovery evidence"
	@echo "test-postgres-continuity Qualify physical replication, promotion, and PITR locally"
	@echo "verify-postgres-continuity-report Verify clean-current physical continuity evidence"
	@echo "test-otel     Send reference metrics and traces to an OpenTelemetry Collector"
	@echo "test-operational-alerts Validate rendered alert profiles with Prometheus promtool"
	@echo "test-otlp-receiver Send official OTLP metrics, logs, and AI usage-to-cost traces"
	@echo "test-ai-finops Prove the local multi-provider AI economics slice"
	@echo "verify-ai-finops-runtime-report Verify clean current AI FinOps runtime evidence"
	@echo "test-aws-bedrock-price-import Validate exact public Bedrock price import"
	@echo "import-aws-bedrock-price-catalog Fetch, retain, and import one protected AWS offer"
	@echo "verify-aws-bedrock-price-import Reproduce an exact retained Bedrock price import"
	@echo "test-ai-price-catalog-qualification Validate deterministic minimized catalog evidence"
	@echo "qualify-ai-price-catalog Qualify an exact production catalog against protected scopes"
	@echo "verify-ai-price-catalog-report Verify a retained current catalog qualification report"
	@echo "test-bedrock-instrumentation Qualify pinned Bedrock Converse and ConverseStream instrumentation offline"
	@echo "test-bedrock-live Make one explicitly enabled live Bedrock compatibility call"
	@echo "test-openai-instrumentation Qualify pinned official OpenAI instrumentation offline"
	@echo "test-openai-live Make one explicitly enabled live OpenAI compatibility call"
	@echo "test-prometheus Query a real Prometheus server through the evidence adapter"
	@echo "test-collector-queue-loss Drive a real Collector's own self-metrics into a queue/loss report"
	@echo "test-loki     Query a real Loki server through the log evidence adapter"
	@echo "test-opensearch Query a real OpenSearch server through the log evidence adapter"
	@echo "test-kubernetes-events Query a local cluster through the Event evidence adapter"
	@echo "test-kubernetes-actions Verify server dry-run and a governed restart on a local cluster"
	@echo "test-kubernetes-live Run the observer against an explicit local Kubernetes context"
	@echo "test-plugin-runner Build and execute the signed no-network plugin sandbox"
	@echo "test-plugin-compatibility Generate the executable plugin compatibility matrix"
	@echo "test-local-product Exercise the customer workflow against the running Docker stack"
	@echo "test-helm-install Build and install the chart on the explicit local kind cluster"
	@echo "test-release-install Install a verified packaged release on the explicit local kind cluster"
	@echo "test-release-upgrade Prove sustained availability across a packaged N-1 transition"
	@echo "qualify-release Run both packaged profiles and require one complete report"
	@echo "test-release-publication Copy exact OCI layouts to an isolated local registry"
	@echo "test-release-signatures Exercise pinned Cosign signing and tamper rejection locally"
	@echo "qualify-release-signatures Verify published release digests against organizational trust"
	@echo "verify-release-signature-report Validate retained minimized signature evidence"
	@echo "test-release-vulnerabilities Exercise the pinned scanner and offline SBOM path"
	@echo "qualify-release-vulnerabilities Qualify every release SBOM under protected policy"
	@echo "verify-release-vulnerability-report Validate retained minimized vulnerability evidence"
	@echo "test-release-readiness Validate aggregate local release-readiness semantics"
	@echo "assess-release-readiness Aggregate exact local candidate evidence"
	@echo "verify-release-readiness-report Recompute and verify aggregate candidate evidence"
	@echo "qualify-local-release Build and qualify one exact local candidate through every retained gate"
	@echo "db-migrate    Apply PostgreSQL migrations using IIP_DATABASE_URL"
	@echo "helm-lint     Lint and render the Helm chart"
	@echo "verify        Run all local quality gates"
	@echo "run           Start the reference HTTP API on port 8080"
	@echo "dev-up        Start the durable local product stack in Docker Desktop"
	@echo "dev-status    Show the durable local stack status"
	@echo "dev-credentials Show the local console URL and operator token"
	@echo "dev-down      Stop the local stack while preserving its database"
	@echo "ai-finops-up  Start and seed the disposable multi-provider AI economics slice"
	@echo "ai-finops-status Show the local AI FinOps containers"
	@echo "ai-finops-down Stop and remove the disposable AI FinOps slice"
	@echo "package-chart Package the Helm chart under dist/"
	@echo "release-bundle Build an unsigned multi-platform release bundle with SBOM/provenance"
	@echo "verify-release-bundle Verify IIP_RELEASE_BUNDLE checksums and OCI attestations"

install-verify-deps:
	$(PYTHON) -m pip install --requirement requirements/verify.txt

validate:
	$(PYTHON) scripts/validate_repo.py

validate-schemas:
	$(PYTHON) scripts/validate_schemas.py

test:
	PYTHONPATH=src:sdks/python/src $(PYTHON) -m unittest discover -s tests -v

test-console-javascript:
	node scripts/test_console_ai_economics.mjs

test-typescript:
	cd sdks/typescript && $(NPM) ci --ignore-scripts --no-audit --no-fund
	cd sdks/typescript && $(NPM) run check

test-evidence-redaction:
	PYTHONPATH=src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_evidence_redaction_policy \
		tests.test_evidence_collection \
		tests.test_context_evidence \
		tests.test_helm_deployment \
		tests.test_deployment_preflight -v

test-deployment-preflight:
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/deployment_preflight.py generate \
		--profile production-core-v1 \
		--values deploy/helm/infra-intelligence/examples/production-core.values.yaml \
		--output dist/customer-deployment-preflight-report.json
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/deployment_preflight.py verify \
		--report dist/customer-deployment-preflight-report.json \
		--values deploy/helm/infra-intelligence/examples/production-core.values.yaml
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/deployment_preflight.py generate \
		--profile production-ai-finops-v0 \
		--values deploy/helm/infra-intelligence/examples/production-core.values.yaml \
		--values deploy/helm/infra-intelligence/examples/production-ai-finops.values.yaml \
		--output dist/customer-ai-finops-preflight-report.json
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/deployment_preflight.py verify \
		--report dist/customer-ai-finops-preflight-report.json \
		--values deploy/helm/infra-intelligence/examples/production-core.values.yaml \
		--values deploy/helm/infra-intelligence/examples/production-ai-finops.values.yaml
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/deployment_preflight.py generate \
		--profile production-core-v1 \
		--values deploy/helm/infra-intelligence/examples/production-core.values.yaml \
		--values deploy/helm/infra-intelligence/examples/production-github-context.values.yaml \
		--output dist/customer-github-context-preflight-report.json
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/deployment_preflight.py verify \
		--report dist/customer-github-context-preflight-report.json \
		--values deploy/helm/infra-intelligence/examples/production-core.values.yaml \
		--values deploy/helm/infra-intelligence/examples/production-github-context.values.yaml

preflight-deployment-live:
	@test -n "$(IIP_KUBERNETES_CONTEXT)" || \
		(echo "IIP_KUBERNETES_CONTEXT is required" >&2; exit 2)
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/deployment_preflight.py generate \
		--profile "$(IIP_DEPLOYMENT_PROFILE)" --values "$(IIP_DEPLOYMENT_VALUES)" \
		--namespace "$(IIP_DEPLOYMENT_NAMESPACE)" --context "$(IIP_KUBERNETES_CONTEXT)" \
		--live --output "$(IIP_DEPLOYMENT_PREFLIGHT_REPORT)"

verify-deployment-preflight-report:
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/deployment_preflight.py verify \
		--report "$(IIP_DEPLOYMENT_PREFLIGHT_REPORT)" \
		--values "$(IIP_DEPLOYMENT_VALUES)" \
		--namespace "$(IIP_DEPLOYMENT_NAMESPACE)" \
		--require-clean --require-install-ready

test-deployment-diagnostics:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_deployment_diagnostics -v

diagnose-deployment:
	@test -n "$(IIP_KUBERNETES_CONTEXT)" || \
		(echo "IIP_KUBERNETES_CONTEXT is required" >&2; exit 2)
	@test -n "$(IIP_DIAGNOSTIC_IMAGE_DIGEST)" || \
		(echo "IIP_DIAGNOSTIC_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/deployment_diagnostics.py generate \
		--context "$(IIP_KUBERNETES_CONTEXT)" \
		--namespace "$(IIP_DEPLOYMENT_NAMESPACE)" \
		--release-name "$(IIP_DIAGNOSTIC_RELEASE_NAME)" \
		--image-digest "$(IIP_DIAGNOSTIC_IMAGE_DIGEST)" \
		--kubectl "$(KUBECTL)" \
		--output "$(IIP_DEPLOYMENT_DIAGNOSTIC_REPORT)" \
		--require-clean --require-healthy

verify-deployment-diagnostic-report:
	@test -n "$(IIP_KUBERNETES_CONTEXT)" || \
		(echo "IIP_KUBERNETES_CONTEXT is required" >&2; exit 2)
	@test -n "$(IIP_DIAGNOSTIC_IMAGE_DIGEST)" || \
		(echo "IIP_DIAGNOSTIC_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/deployment_diagnostics.py verify \
		--context "$(IIP_KUBERNETES_CONTEXT)" \
		--namespace "$(IIP_DEPLOYMENT_NAMESPACE)" \
		--release-name "$(IIP_DIAGNOSTIC_RELEASE_NAME)" \
		--image-digest "$(IIP_DIAGNOSTIC_IMAGE_DIGEST)" \
		--report "$(IIP_DEPLOYMENT_DIAGNOSTIC_REPORT)" \
		--require-clean --require-healthy

test-ingress-availability:
	PYTHONPATH=src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_ingress_availability_qualification -v

qualify-ingress-availability:
	@test -n "$(IIP_INGRESS_BASE_URL)" || \
		(echo "IIP_INGRESS_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_INGRESS_TOKEN_FILE)" || \
		(echo "IIP_INGRESS_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_INGRESS_IMAGE_DIGEST)" || \
		(echo "IIP_INGRESS_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/qualify_ingress_availability.py run \
		--profile customer-ingress --base-url "$(IIP_INGRESS_BASE_URL)" \
		--token-file "$(IIP_INGRESS_TOKEN_FILE)" \
		--image-digest "$(IIP_INGRESS_IMAGE_DIGEST)" \
		--samples "$(IIP_INGRESS_SAMPLES)" \
		--minimum-availability-basis-points "$(IIP_INGRESS_MINIMUM_AVAILABILITY_BASIS_POINTS)" \
		--maximum-p95-latency-milliseconds "$(IIP_INGRESS_MAXIMUM_P95_LATENCY_MILLISECONDS)" \
		--request-timeout-milliseconds "$(IIP_INGRESS_REQUEST_TIMEOUT_MILLISECONDS)" \
		--interval-milliseconds "$(IIP_INGRESS_INTERVAL_MILLISECONDS)" \
		$(if $(IIP_INGRESS_CA_FILE),--ca-file "$(IIP_INGRESS_CA_FILE)",) \
		--output "$(IIP_INGRESS_QUALIFICATION_REPORT)"

verify-ingress-availability-report:
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/qualify_ingress_availability.py verify \
		--report "$(IIP_INGRESS_QUALIFICATION_REPORT)" \
		--require-clean --require-qualified

test-customer-oidc-qualification:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_customer_oidc_qualification -v

qualify-customer-oidc:
	@test "$(IIP_CUSTOMER_OIDC_ALLOW_IDENTITY_OBSERVATION)" = true || \
		(echo "IIP_CUSTOMER_OIDC_ALLOW_IDENTITY_OBSERVATION must equal true" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OIDC_PROFILE)" || \
		(echo "IIP_CUSTOMER_OIDC_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OIDC_API_BASE_URL)" || \
		(echo "IIP_CUSTOMER_OIDC_API_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OIDC_ACCESS_TOKEN_FILE)" || \
		(echo "IIP_CUSTOMER_OIDC_ACCESS_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OIDC_API_CA_FILE)" || \
		(echo "IIP_CUSTOMER_OIDC_API_CA_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OIDC_ISSUER_CA_FILE)" || \
		(echo "IIP_CUSTOMER_OIDC_ISSUER_CA_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OIDC_IMAGE_DIGEST)" || \
		(echo "IIP_CUSTOMER_OIDC_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_oidc.py qualify \
		--profile "$(IIP_CUSTOMER_OIDC_PROFILE)" \
		--api-base-url "$(IIP_CUSTOMER_OIDC_API_BASE_URL)" \
		--access-token-file "$(IIP_CUSTOMER_OIDC_ACCESS_TOKEN_FILE)" \
		--api-ca-file "$(IIP_CUSTOMER_OIDC_API_CA_FILE)" \
		--issuer-ca-file "$(IIP_CUSTOMER_OIDC_ISSUER_CA_FILE)" \
		--image-digest "$(IIP_CUSTOMER_OIDC_IMAGE_DIGEST)" \
		--output "$(IIP_CUSTOMER_OIDC_REPORT)" \
		--allow-identity-observation

verify-customer-oidc-qualification-report:
	@test -n "$(IIP_CUSTOMER_OIDC_PROFILE)" || \
		(echo "IIP_CUSTOMER_OIDC_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OIDC_API_BASE_URL)" || \
		(echo "IIP_CUSTOMER_OIDC_API_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OIDC_IMAGE_DIGEST)" || \
		(echo "IIP_CUSTOMER_OIDC_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_oidc.py verify \
		--report "$(IIP_CUSTOMER_OIDC_REPORT)" \
		--profile "$(IIP_CUSTOMER_OIDC_PROFILE)" \
		--api-base-url "$(IIP_CUSTOMER_OIDC_API_BASE_URL)" \
		--image-digest "$(IIP_CUSTOMER_OIDC_IMAGE_DIGEST)" \
		--require-qualified

test-customer-policy-qualification:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_customer_policy_qualification tests.test_policy_adapter -v

qualify-customer-policy:
	@test "$(IIP_CUSTOMER_POLICY_ALLOW_OBSERVATION)" = true || \
		(echo "IIP_CUSTOMER_POLICY_ALLOW_OBSERVATION must equal true" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POLICY_PROFILE)" || \
		(echo "IIP_CUSTOMER_POLICY_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POLICY_ENDPOINT)" || \
		(echo "IIP_CUSTOMER_POLICY_ENDPOINT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POLICY_BEARER_TOKEN_FILE)" || \
		(echo "IIP_CUSTOMER_POLICY_BEARER_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POLICY_CA_FILE)" || \
		(echo "IIP_CUSTOMER_POLICY_CA_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POLICY_IMAGE_DIGEST)" || \
		(echo "IIP_CUSTOMER_POLICY_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_policy.py qualify \
		--profile "$(IIP_CUSTOMER_POLICY_PROFILE)" \
		--endpoint "$(IIP_CUSTOMER_POLICY_ENDPOINT)" \
		--bearer-token-file "$(IIP_CUSTOMER_POLICY_BEARER_TOKEN_FILE)" \
		--ca-bundle-file "$(IIP_CUSTOMER_POLICY_CA_FILE)" \
		--image-digest "$(IIP_CUSTOMER_POLICY_IMAGE_DIGEST)" \
		--output "$(IIP_CUSTOMER_POLICY_REPORT)" \
		--allow-policy-observation

verify-customer-policy-qualification-report:
	@test -n "$(IIP_CUSTOMER_POLICY_PROFILE)" || \
		(echo "IIP_CUSTOMER_POLICY_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POLICY_ENDPOINT)" || \
		(echo "IIP_CUSTOMER_POLICY_ENDPOINT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POLICY_IMAGE_DIGEST)" || \
		(echo "IIP_CUSTOMER_POLICY_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_policy.py verify \
		--report "$(IIP_CUSTOMER_POLICY_REPORT)" \
		--profile "$(IIP_CUSTOMER_POLICY_PROFILE)" \
		--endpoint "$(IIP_CUSTOMER_POLICY_ENDPOINT)" \
		--image-digest "$(IIP_CUSTOMER_POLICY_IMAGE_DIGEST)" \
		--require-qualified

test-customer-credential-broker-qualification:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_customer_credential_broker_qualification tests.test_credential_broker -v

qualify-customer-credential-broker:
	@test "$(IIP_CUSTOMER_CREDENTIAL_BROKER_ALLOW_OBSERVATION)" = true || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_ALLOW_OBSERVATION must equal true" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_CREDENTIAL_BROKER_PROFILE)" || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_CREDENTIAL_BROKER_ENDPOINT)" || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_ENDPOINT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_CREDENTIAL_BROKER_WORKLOAD_TOKEN_FILE)" || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_WORKLOAD_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_CREDENTIAL_BROKER_CA_FILE)" || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_CA_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_CREDENTIAL_BROKER_IMAGE_DIGEST)" || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_credential_broker.py qualify \
		--profile "$(IIP_CUSTOMER_CREDENTIAL_BROKER_PROFILE)" \
		--endpoint "$(IIP_CUSTOMER_CREDENTIAL_BROKER_ENDPOINT)" \
		--workload-identity-token-file "$(IIP_CUSTOMER_CREDENTIAL_BROKER_WORKLOAD_TOKEN_FILE)" \
		--ca-bundle-file "$(IIP_CUSTOMER_CREDENTIAL_BROKER_CA_FILE)" \
		--image-digest "$(IIP_CUSTOMER_CREDENTIAL_BROKER_IMAGE_DIGEST)" \
		--output "$(IIP_CUSTOMER_CREDENTIAL_BROKER_REPORT)" \
		--allow-credential-observation

verify-customer-credential-broker-qualification-report:
	@test -n "$(IIP_CUSTOMER_CREDENTIAL_BROKER_PROFILE)" || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_CREDENTIAL_BROKER_ENDPOINT)" || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_ENDPOINT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_CREDENTIAL_BROKER_CA_FILE)" || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_CA_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_CREDENTIAL_BROKER_IMAGE_DIGEST)" || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_credential_broker.py verify \
		--report "$(IIP_CUSTOMER_CREDENTIAL_BROKER_REPORT)" \
		--profile "$(IIP_CUSTOMER_CREDENTIAL_BROKER_PROFILE)" \
		--endpoint "$(IIP_CUSTOMER_CREDENTIAL_BROKER_ENDPOINT)" \
		--ca-bundle-file "$(IIP_CUSTOMER_CREDENTIAL_BROKER_CA_FILE)" \
		--image-digest "$(IIP_CUSTOMER_CREDENTIAL_BROKER_IMAGE_DIGEST)" \
		--require-qualified

test-customer-github-context-qualification:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_customer_github_context_qualification \
		tests.test_github_context_backend tests.test_credential_broker -v

qualify-customer-github-context:
	@test "$(IIP_CUSTOMER_GITHUB_CONTEXT_ALLOW_OBSERVATION)" = true || \
		(echo "IIP_CUSTOMER_GITHUB_CONTEXT_ALLOW_OBSERVATION must equal true" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_GITHUB_CONTEXT_PROFILE)" || (echo "IIP_CUSTOMER_GITHUB_CONTEXT_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_GITHUB_CONTEXT_INTEGRATION_CONFIG)" || (echo "IIP_CUSTOMER_GITHUB_CONTEXT_INTEGRATION_CONFIG is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_REPORT)" || (echo "IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_REPORT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_PROFILE)" || (echo "IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_ENDPOINT)" || (echo "IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_ENDPOINT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_WORKLOAD_TOKEN_FILE)" || (echo "IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_WORKLOAD_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_CA_FILE)" || (echo "IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_CA_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_GITHUB_CONTEXT_IMAGE_DIGEST)" || (echo "IIP_CUSTOMER_GITHUB_CONTEXT_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_github_context.py qualify \
		--profile "$(IIP_CUSTOMER_GITHUB_CONTEXT_PROFILE)" \
		--integration-config "$(IIP_CUSTOMER_GITHUB_CONTEXT_INTEGRATION_CONFIG)" \
		--credential-broker-report "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_REPORT)" \
		--credential-broker-profile "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_PROFILE)" \
		--credential-broker-endpoint "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_ENDPOINT)" \
		--credential-broker-workload-token-file "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_WORKLOAD_TOKEN_FILE)" \
		--credential-broker-ca-file "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_CA_FILE)" \
		$(if $(IIP_CUSTOMER_GITHUB_CONTEXT_GITHUB_CA_FILE),--github-ca-file "$(IIP_CUSTOMER_GITHUB_CONTEXT_GITHUB_CA_FILE)") \
		--image-digest "$(IIP_CUSTOMER_GITHUB_CONTEXT_IMAGE_DIGEST)" \
		--output "$(IIP_CUSTOMER_GITHUB_CONTEXT_REPORT)" \
		--allow-context-observation

verify-customer-github-context-qualification-report:
	@test -n "$(IIP_CUSTOMER_GITHUB_CONTEXT_PROFILE)" || (echo "IIP_CUSTOMER_GITHUB_CONTEXT_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_GITHUB_CONTEXT_INTEGRATION_CONFIG)" || (echo "IIP_CUSTOMER_GITHUB_CONTEXT_INTEGRATION_CONFIG is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_REPORT)" || (echo "IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_REPORT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_PROFILE)" || (echo "IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_ENDPOINT)" || (echo "IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_ENDPOINT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_CA_FILE)" || (echo "IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_CA_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_GITHUB_CONTEXT_IMAGE_DIGEST)" || (echo "IIP_CUSTOMER_GITHUB_CONTEXT_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_github_context.py verify \
		--report "$(IIP_CUSTOMER_GITHUB_CONTEXT_REPORT)" \
		--profile "$(IIP_CUSTOMER_GITHUB_CONTEXT_PROFILE)" \
		--integration-config "$(IIP_CUSTOMER_GITHUB_CONTEXT_INTEGRATION_CONFIG)" \
		--credential-broker-report "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_REPORT)" \
		--credential-broker-profile "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_PROFILE)" \
		--credential-broker-endpoint "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_ENDPOINT)" \
		--credential-broker-ca-file "$(IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_CA_FILE)" \
		$(if $(IIP_CUSTOMER_GITHUB_CONTEXT_GITHUB_CA_FILE),--github-ca-file "$(IIP_CUSTOMER_GITHUB_CONTEXT_GITHUB_CA_FILE)") \
		--image-digest "$(IIP_CUSTOMER_GITHUB_CONTEXT_IMAGE_DIGEST)" \
		--require-qualified

test-customer-bedrock-qualification:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_customer_bedrock_qualification \
		tests.test_bedrock_instrumentation_compatibility -v

qualify-customer-bedrock:
	@test "$(IIP_CUSTOMER_BEDROCK_ALLOW_PROVIDER_CALL)" = true || \
		(echo "IIP_CUSTOMER_BEDROCK_ALLOW_PROVIDER_CALL must equal true" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_BEDROCK_PROFILE)" || (echo "IIP_CUSTOMER_BEDROCK_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_BEDROCK_AWS_CREDENTIALS_FILE)" || (echo "IIP_CUSTOMER_BEDROCK_AWS_CREDENTIALS_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_BEDROCK_IMAGE_DIGEST)" || (echo "IIP_CUSTOMER_BEDROCK_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_bedrock.py qualify \
		--profile "$(IIP_CUSTOMER_BEDROCK_PROFILE)" \
		--aws-credentials-file "$(IIP_CUSTOMER_BEDROCK_AWS_CREDENTIALS_FILE)" \
		--image-digest "$(IIP_CUSTOMER_BEDROCK_IMAGE_DIGEST)" \
		--live-report-output "$(IIP_CUSTOMER_BEDROCK_LIVE_REPORT)" \
		--output "$(IIP_CUSTOMER_BEDROCK_REPORT)" \
		--docker-bin "$(DOCKER)" \
		--allow-provider-call

verify-customer-bedrock-qualification-report:
	@test -n "$(IIP_CUSTOMER_BEDROCK_PROFILE)" || (echo "IIP_CUSTOMER_BEDROCK_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_BEDROCK_IMAGE_DIGEST)" || (echo "IIP_CUSTOMER_BEDROCK_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_bedrock.py verify \
		--report "$(IIP_CUSTOMER_BEDROCK_REPORT)" \
		--profile "$(IIP_CUSTOMER_BEDROCK_PROFILE)" \
		--live-report "$(IIP_CUSTOMER_BEDROCK_LIVE_REPORT)" \
		--image-digest "$(IIP_CUSTOMER_BEDROCK_IMAGE_DIGEST)" \
		--require-qualified

test-customer-ai-finops-prerequisites:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_customer_ai_finops_prerequisites -v

qualify-customer-ai-finops-prerequisites:
	@test -n "$(IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE)" || \
		(echo "IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_ai_finops.py generate \
		--profile "$(IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE)" \
		--release-readiness "$(IIP_RELEASE_READINESS_REPORT)" \
		--ai-finops-runtime "$(IIP_AI_FINOPS_RUNTIME_REPORT)" \
		--customer-deployment "$(IIP_CUSTOMER_DEPLOYMENT_QUALIFICATION_REPORT)" \
		--customer-otlp-receiver "$(IIP_CUSTOMER_OTLP_REPORT)" \
		--customer-bedrock "$(IIP_CUSTOMER_BEDROCK_REPORT)" \
		--price-catalog-qualification "$(IIP_AI_PRICE_QUALIFICATION_REPORT)" \
		--output "$(IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_REPORT)"

verify-customer-ai-finops-prerequisite-report:
	@test -n "$(IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE)" || \
		(echo "IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_ai_finops.py verify \
		--report "$(IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_REPORT)" \
		--profile "$(IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE)" \
		--release-readiness "$(IIP_RELEASE_READINESS_REPORT)" \
		--ai-finops-runtime "$(IIP_AI_FINOPS_RUNTIME_REPORT)" \
		--customer-deployment "$(IIP_CUSTOMER_DEPLOYMENT_QUALIFICATION_REPORT)" \
		--customer-otlp-receiver "$(IIP_CUSTOMER_OTLP_REPORT)" \
		--customer-bedrock "$(IIP_CUSTOMER_BEDROCK_REPORT)" \
		--price-catalog-qualification "$(IIP_AI_PRICE_QUALIFICATION_REPORT)" \
		--require-ready

test-customer-ai-finops-flow:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_customer_ai_finops_flow -v

qualify-customer-ai-finops-flow:
	@test "$(IIP_CUSTOMER_AI_FINOPS_FLOW_ALLOW_PROVIDER_CALL)" = true || \
		(echo "IIP_CUSTOMER_AI_FINOPS_FLOW_ALLOW_PROVIDER_CALL must equal true" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_AI_FINOPS_FLOW_PROFILE)" || (echo "IIP_CUSTOMER_AI_FINOPS_FLOW_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE)" || (echo "IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_AI_FINOPS_FLOW_BEDROCK_PROFILE)" || (echo "IIP_CUSTOMER_AI_FINOPS_FLOW_BEDROCK_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_AI_FINOPS_FLOW_AWS_CREDENTIALS_FILE)" || (echo "IIP_CUSTOMER_AI_FINOPS_FLOW_AWS_CREDENTIALS_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_AI_FINOPS_FLOW_CONTROL_HEADERS)" || (echo "IIP_CUSTOMER_AI_FINOPS_FLOW_CONTROL_HEADERS is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_AI_FINOPS_FLOW_OTLP_HEADERS)" || (echo "IIP_CUSTOMER_AI_FINOPS_FLOW_OTLP_HEADERS is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_AI_FINOPS_FLOW_PROMETHEUS_HEADERS)" || (echo "IIP_CUSTOMER_AI_FINOPS_FLOW_PROMETHEUS_HEADERS is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_AI_FINOPS_FLOW_GRAFANA_HEADERS)" || (echo "IIP_CUSTOMER_AI_FINOPS_FLOW_GRAFANA_HEADERS is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_ai_finops_flow.py qualify \
		--profile "$(IIP_CUSTOMER_AI_FINOPS_FLOW_PROFILE)" \
		--prerequisite-profile "$(IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE)" \
		--prerequisite-report "$(IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_REPORT)" \
		--bedrock-profile "$(IIP_CUSTOMER_AI_FINOPS_FLOW_BEDROCK_PROFILE)" \
		--aws-credentials-file "$(IIP_CUSTOMER_AI_FINOPS_FLOW_AWS_CREDENTIALS_FILE)" \
		--control-plane-headers "$(IIP_CUSTOMER_AI_FINOPS_FLOW_CONTROL_HEADERS)" \
		--otlp-headers "$(IIP_CUSTOMER_AI_FINOPS_FLOW_OTLP_HEADERS)" \
		--prometheus-headers "$(IIP_CUSTOMER_AI_FINOPS_FLOW_PROMETHEUS_HEADERS)" \
		--grafana-headers "$(IIP_CUSTOMER_AI_FINOPS_FLOW_GRAFANA_HEADERS)" \
		$(if $(IIP_CUSTOMER_AI_FINOPS_FLOW_CONTROL_CA),--control-plane-ca "$(IIP_CUSTOMER_AI_FINOPS_FLOW_CONTROL_CA)") \
		$(if $(IIP_CUSTOMER_AI_FINOPS_FLOW_OTLP_CA),--otlp-ca "$(IIP_CUSTOMER_AI_FINOPS_FLOW_OTLP_CA)") \
		$(if $(IIP_CUSTOMER_AI_FINOPS_FLOW_PROMETHEUS_CA),--prometheus-ca "$(IIP_CUSTOMER_AI_FINOPS_FLOW_PROMETHEUS_CA)") \
		$(if $(IIP_CUSTOMER_AI_FINOPS_FLOW_GRAFANA_CA),--grafana-ca "$(IIP_CUSTOMER_AI_FINOPS_FLOW_GRAFANA_CA)") \
		$(if $(IIP_CUSTOMER_AI_FINOPS_FLOW_OTLP_CLIENT_CERT),--otlp-client-cert "$(IIP_CUSTOMER_AI_FINOPS_FLOW_OTLP_CLIENT_CERT)") \
		$(if $(IIP_CUSTOMER_AI_FINOPS_FLOW_OTLP_CLIENT_KEY),--otlp-client-key "$(IIP_CUSTOMER_AI_FINOPS_FLOW_OTLP_CLIENT_KEY)") \
		--docker-bin "$(DOCKER)" \
		--run-evidence-output "$(IIP_CUSTOMER_AI_FINOPS_FLOW_RUN_EVIDENCE)" \
		--live-report-output "$(IIP_CUSTOMER_AI_FINOPS_FLOW_LIVE_REPORT)" \
		--observation-output "$(IIP_CUSTOMER_AI_FINOPS_FLOW_OBSERVATION)" \
		--output "$(IIP_CUSTOMER_AI_FINOPS_FLOW_REPORT)" \
		--allow-provider-call

verify-customer-ai-finops-flow-report:
	@test -n "$(IIP_CUSTOMER_AI_FINOPS_FLOW_PROFILE)" || \
		(echo "IIP_CUSTOMER_AI_FINOPS_FLOW_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE)" || \
		(echo "IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_AI_FINOPS_FLOW_BEDROCK_PROFILE)" || \
		(echo "IIP_CUSTOMER_AI_FINOPS_FLOW_BEDROCK_PROFILE is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_ai_finops_flow.py verify \
		--profile "$(IIP_CUSTOMER_AI_FINOPS_FLOW_PROFILE)" \
		--prerequisite-profile "$(IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE)" \
		--prerequisite-report "$(IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_REPORT)" \
		--bedrock-profile "$(IIP_CUSTOMER_AI_FINOPS_FLOW_BEDROCK_PROFILE)" \
		--run-evidence "$(IIP_CUSTOMER_AI_FINOPS_FLOW_RUN_EVIDENCE)" \
		--live-report "$(IIP_CUSTOMER_AI_FINOPS_FLOW_LIVE_REPORT)" \
		--observation "$(IIP_CUSTOMER_AI_FINOPS_FLOW_OBSERVATION)" \
		--report "$(IIP_CUSTOMER_AI_FINOPS_FLOW_REPORT)" \
		--require-qualified

test-customer-pilot-readiness:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_customer_pilot_readiness -v

assess-customer-pilot-readiness:
	@test -n "$(IIP_CUSTOMER_PILOT_READINESS_PROFILE)" || \
		(echo "IIP_CUSTOMER_PILOT_READINESS_PROFILE is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/assess_customer_pilot_readiness.py generate \
		--profile "$(IIP_CUSTOMER_PILOT_READINESS_PROFILE)" \
		--release-readiness "$(IIP_RELEASE_READINESS_REPORT)" \
		--release-publication "$(IIP_RELEASE_PUBLICATION_REPORT)" \
		--release-signatures "$(IIP_RELEASE_SIGNATURE_REPORT)" \
		--customer-deployment "$(IIP_CUSTOMER_DEPLOYMENT_QUALIFICATION_REPORT)" \
		--control-plane-load "$(IIP_CONTROL_PLANE_LOAD_REPORT)" \
		--sustained-workload "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_REPORT)" \
		--failure-overlap "$(IIP_CUSTOMER_FAILURE_OVERLAP_REPORT)" \
		--ai-finops-prerequisites "$(IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_REPORT)" \
		--ai-finops-flow "$(IIP_CUSTOMER_AI_FINOPS_FLOW_REPORT)" \
		--output "$(IIP_CUSTOMER_PILOT_READINESS_REPORT)"

verify-customer-pilot-readiness-report:
	@test -n "$(IIP_CUSTOMER_PILOT_READINESS_PROFILE)" || \
		(echo "IIP_CUSTOMER_PILOT_READINESS_PROFILE is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/assess_customer_pilot_readiness.py verify \
		--report "$(IIP_CUSTOMER_PILOT_READINESS_REPORT)" \
		--profile "$(IIP_CUSTOMER_PILOT_READINESS_PROFILE)" \
		--release-readiness "$(IIP_RELEASE_READINESS_REPORT)" \
		--release-publication "$(IIP_RELEASE_PUBLICATION_REPORT)" \
		--release-signatures "$(IIP_RELEASE_SIGNATURE_REPORT)" \
		--customer-deployment "$(IIP_CUSTOMER_DEPLOYMENT_QUALIFICATION_REPORT)" \
		--control-plane-load "$(IIP_CONTROL_PLANE_LOAD_REPORT)" \
		--sustained-workload "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_REPORT)" \
		--failure-overlap "$(IIP_CUSTOMER_FAILURE_OVERLAP_REPORT)" \
		--ai-finops-prerequisites "$(IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_REPORT)" \
		--ai-finops-flow "$(IIP_CUSTOMER_AI_FINOPS_FLOW_REPORT)" \
		--require-candidate

test-customer-otlp-receiver-qualification:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_customer_otlp_receiver_qualification -v

qualify-customer-otlp-receiver:
	@test "$(IIP_CUSTOMER_OTLP_ALLOW_OBSERVATION)" = true || \
		(echo "IIP_CUSTOMER_OTLP_ALLOW_OBSERVATION must equal true" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_PROFILE)" || (echo "IIP_CUSTOMER_OTLP_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_API_BASE_URL)" || (echo "IIP_CUSTOMER_OTLP_API_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_RECEIVER_ENDPOINT)" || (echo "IIP_CUSTOMER_OTLP_RECEIVER_ENDPOINT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_API_TOKEN_FILE)" || (echo "IIP_CUSTOMER_OTLP_API_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_METRICS_TOKEN_FILE)" || (echo "IIP_CUSTOMER_OTLP_METRICS_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_LOGS_TOKEN_FILE)" || (echo "IIP_CUSTOMER_OTLP_LOGS_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_TRACES_TOKEN_FILE)" || (echo "IIP_CUSTOMER_OTLP_TRACES_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_RECEIVER_CA_FILE)" || (echo "IIP_CUSTOMER_OTLP_RECEIVER_CA_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_CLIENT_CERTIFICATE_FILE)" || (echo "IIP_CUSTOMER_OTLP_CLIENT_CERTIFICATE_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_CLIENT_KEY_FILE)" || (echo "IIP_CUSTOMER_OTLP_CLIENT_KEY_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_IMAGE_DIGEST)" || (echo "IIP_CUSTOMER_OTLP_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_otlp_receiver.py qualify \
		--profile "$(IIP_CUSTOMER_OTLP_PROFILE)" \
		--api-base-url "$(IIP_CUSTOMER_OTLP_API_BASE_URL)" \
		--receiver-endpoint "$(IIP_CUSTOMER_OTLP_RECEIVER_ENDPOINT)" \
		--api-token-file "$(IIP_CUSTOMER_OTLP_API_TOKEN_FILE)" \
		$(if $(IIP_CUSTOMER_OTLP_API_CA_FILE),--api-ca-file "$(IIP_CUSTOMER_OTLP_API_CA_FILE)") \
		--metrics-token-file "$(IIP_CUSTOMER_OTLP_METRICS_TOKEN_FILE)" \
		--logs-token-file "$(IIP_CUSTOMER_OTLP_LOGS_TOKEN_FILE)" \
		--traces-token-file "$(IIP_CUSTOMER_OTLP_TRACES_TOKEN_FILE)" \
		--receiver-ca-file "$(IIP_CUSTOMER_OTLP_RECEIVER_CA_FILE)" \
		--client-certificate-file "$(IIP_CUSTOMER_OTLP_CLIENT_CERTIFICATE_FILE)" \
		--client-key-file "$(IIP_CUSTOMER_OTLP_CLIENT_KEY_FILE)" \
		--image-digest "$(IIP_CUSTOMER_OTLP_IMAGE_DIGEST)" \
		--output "$(IIP_CUSTOMER_OTLP_REPORT)" \
		--allow-observation

verify-customer-otlp-receiver-qualification-report:
	@test -n "$(IIP_CUSTOMER_OTLP_PROFILE)" || (echo "IIP_CUSTOMER_OTLP_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_API_BASE_URL)" || (echo "IIP_CUSTOMER_OTLP_API_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_RECEIVER_ENDPOINT)" || (echo "IIP_CUSTOMER_OTLP_RECEIVER_ENDPOINT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_RECEIVER_CA_FILE)" || (echo "IIP_CUSTOMER_OTLP_RECEIVER_CA_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_CLIENT_CERTIFICATE_FILE)" || (echo "IIP_CUSTOMER_OTLP_CLIENT_CERTIFICATE_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_IMAGE_DIGEST)" || (echo "IIP_CUSTOMER_OTLP_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_otlp_receiver.py verify \
		--report "$(IIP_CUSTOMER_OTLP_REPORT)" \
		--profile "$(IIP_CUSTOMER_OTLP_PROFILE)" \
		--api-base-url "$(IIP_CUSTOMER_OTLP_API_BASE_URL)" \
		--receiver-endpoint "$(IIP_CUSTOMER_OTLP_RECEIVER_ENDPOINT)" \
		$(if $(IIP_CUSTOMER_OTLP_API_CA_FILE),--api-ca-file "$(IIP_CUSTOMER_OTLP_API_CA_FILE)") \
		--receiver-ca-file "$(IIP_CUSTOMER_OTLP_RECEIVER_CA_FILE)" \
		--client-certificate-file "$(IIP_CUSTOMER_OTLP_CLIENT_CERTIFICATE_FILE)" \
		--image-digest "$(IIP_CUSTOMER_OTLP_IMAGE_DIGEST)" \
		--require-qualified

test-customer-continuity:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_customer_continuity_qualification \
		tests.test_ingress_availability_qualification -v

qualify-customer-continuity:
	@test "$(IIP_CONTINUITY_ALLOW_DISRUPTION)" = true || \
		(echo "IIP_CONTINUITY_ALLOW_DISRUPTION must equal true" >&2; exit 2)
	@test -n "$(IIP_KUBERNETES_CONTEXT)" || \
		(echo "IIP_KUBERNETES_CONTEXT is required" >&2; exit 2)
	@test -n "$(IIP_CONTINUITY_BASE_URL)" || \
		(echo "IIP_CONTINUITY_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_CONTINUITY_TOKEN_FILE)" || \
		(echo "IIP_CONTINUITY_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CONTINUITY_IMAGE_DIGEST)" || \
		(echo "IIP_CONTINUITY_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_continuity.py run \
		--base-url "$(IIP_CONTINUITY_BASE_URL)" \
		--token-file "$(IIP_CONTINUITY_TOKEN_FILE)" \
		--image-digest "$(IIP_CONTINUITY_IMAGE_DIGEST)" \
		--context "$(IIP_KUBERNETES_CONTEXT)" \
		--namespace "$(IIP_DEPLOYMENT_NAMESPACE)" \
		--deployment "$(IIP_CONTINUITY_DEPLOYMENT)" \
		--ingress-report "$(IIP_CONTINUITY_INGRESS_REPORT)" \
		--output "$(IIP_CONTINUITY_REPORT)" \
		--kubectl "$(KUBECTL)" \
		--samples "$(IIP_CONTINUITY_SAMPLES)" \
		--interval-milliseconds "$(IIP_CONTINUITY_INTERVAL_MILLISECONDS)" \
		--minimum-window-seconds "$(IIP_CONTINUITY_MINIMUM_WINDOW_SECONDS)" \
		--minimum-baseline-seconds "$(IIP_CONTINUITY_MINIMUM_BASELINE_SECONDS)" \
		--minimum-post-recovery-seconds "$(IIP_CONTINUITY_MINIMUM_POST_RECOVERY_SECONDS)" \
		--minimum-availability-basis-points "$(IIP_CONTINUITY_MINIMUM_AVAILABILITY_BASIS_POINTS)" \
		--maximum-p95-latency-milliseconds "$(IIP_CONTINUITY_MAXIMUM_P95_LATENCY_MILLISECONDS)" \
		--request-timeout-milliseconds "$(IIP_CONTINUITY_REQUEST_TIMEOUT_MILLISECONDS)" \
		--maximum-recovery-seconds "$(IIP_CONTINUITY_MAXIMUM_RECOVERY_SECONDS)" \
		$(if $(IIP_CONTINUITY_CA_FILE),--ca-file "$(IIP_CONTINUITY_CA_FILE)",) \
		--allow-disruption

verify-customer-continuity-report:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_continuity.py verify \
		--report "$(IIP_CONTINUITY_REPORT)" \
		--ingress-report "$(IIP_CONTINUITY_INGRESS_REPORT)" \
		--require-clean --require-qualified

test-customer-processing-continuity:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_customer_processing_continuity \
		tests.test_customer_continuity_qualification -v

qualify-customer-processing-continuity:
	@test "$(IIP_PROCESSING_ALLOW_DISRUPTION)" = true || \
		(echo "IIP_PROCESSING_ALLOW_DISRUPTION must equal true" >&2; exit 2)
	@test -n "$(IIP_KUBERNETES_CONTEXT)" || \
		(echo "IIP_KUBERNETES_CONTEXT is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_API_BASE_URL)" || \
		(echo "IIP_PROCESSING_API_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_API_TOKEN_FILE)" || \
		(echo "IIP_PROCESSING_API_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_OTLP_BASE_URL)" || \
		(echo "IIP_PROCESSING_OTLP_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_OTLP_TOKEN_FILE)" || \
		(echo "IIP_PROCESSING_OTLP_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_OTLP_CLIENT_CERT_FILE)" || \
		(echo "IIP_PROCESSING_OTLP_CLIENT_CERT_FILE is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_OTLP_CLIENT_KEY_FILE)" || \
		(echo "IIP_PROCESSING_OTLP_CLIENT_KEY_FILE is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_PROFILE)" || \
		(echo "IIP_PROCESSING_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_IMAGE_DIGEST)" || \
		(echo "IIP_PROCESSING_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_processing_continuity.py run \
		--api-base-url "$(IIP_PROCESSING_API_BASE_URL)" \
		--api-token-file "$(IIP_PROCESSING_API_TOKEN_FILE)" \
		--otlp-base-url "$(IIP_PROCESSING_OTLP_BASE_URL)" \
		--otlp-token-file "$(IIP_PROCESSING_OTLP_TOKEN_FILE)" \
		--otlp-client-cert-file "$(IIP_PROCESSING_OTLP_CLIENT_CERT_FILE)" \
		--otlp-client-key-file "$(IIP_PROCESSING_OTLP_CLIENT_KEY_FILE)" \
		$(if $(IIP_PROCESSING_API_CA_FILE),--api-ca-file "$(IIP_PROCESSING_API_CA_FILE)",) \
		$(if $(IIP_PROCESSING_OTLP_CA_FILE),--otlp-ca-file "$(IIP_PROCESSING_OTLP_CA_FILE)",) \
		--profile "$(IIP_PROCESSING_PROFILE)" \
		--image-digest "$(IIP_PROCESSING_IMAGE_DIGEST)" \
		--context "$(IIP_KUBERNETES_CONTEXT)" \
		--namespace "$(IIP_DEPLOYMENT_NAMESPACE)" \
		--worker-deployment "$(IIP_PROCESSING_WORKER_DEPLOYMENT)" \
		--receiver-deployment "$(IIP_PROCESSING_RECEIVER_DEPLOYMENT)" \
		--attempts-per-phase "$(IIP_PROCESSING_ATTEMPTS_PER_PHASE)" \
		--probe-interval-milliseconds "$(IIP_PROCESSING_PROBE_INTERVAL_MILLISECONDS)" \
		--maximum-workflow-milliseconds "$(IIP_PROCESSING_MAXIMUM_WORKFLOW_MILLISECONDS)" \
		--maximum-recovery-milliseconds "$(IIP_PROCESSING_MAXIMUM_RECOVERY_MILLISECONDS)" \
		--request-timeout-milliseconds "$(IIP_PROCESSING_REQUEST_TIMEOUT_MILLISECONDS)" \
		--kubectl "$(KUBECTL)" --output "$(IIP_PROCESSING_REPORT)" \
		--allow-disruption

verify-customer-processing-continuity-report:
	@test -n "$(IIP_KUBERNETES_CONTEXT)" || \
		(echo "IIP_KUBERNETES_CONTEXT is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_API_BASE_URL)" || \
		(echo "IIP_PROCESSING_API_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_OTLP_BASE_URL)" || \
		(echo "IIP_PROCESSING_OTLP_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_PROFILE)" || \
		(echo "IIP_PROCESSING_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_IMAGE_DIGEST)" || \
		(echo "IIP_PROCESSING_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_processing_continuity.py verify \
		--report "$(IIP_PROCESSING_REPORT)" \
		--api-base-url "$(IIP_PROCESSING_API_BASE_URL)" \
		--otlp-base-url "$(IIP_PROCESSING_OTLP_BASE_URL)" \
		--profile "$(IIP_PROCESSING_PROFILE)" \
		--image-digest "$(IIP_PROCESSING_IMAGE_DIGEST)" \
		--context "$(IIP_KUBERNETES_CONTEXT)" \
		--namespace "$(IIP_DEPLOYMENT_NAMESPACE)" \
		--worker-deployment "$(IIP_PROCESSING_WORKER_DEPLOYMENT)" \
		--receiver-deployment "$(IIP_PROCESSING_RECEIVER_DEPLOYMENT)" \
		--require-clean --require-qualified

test-customer-sustained-workload:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_customer_sustained_workload -v

qualify-customer-sustained-workload:
	@test "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_ALLOW_TRAFFIC)" = true || \
		(echo "IIP_CUSTOMER_SUSTAINED_WORKLOAD_ALLOW_TRAFFIC must equal true" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE)" || \
		(echo "IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_API_TOKEN_FILE)" || \
		(echo "IIP_CUSTOMER_SUSTAINED_WORKLOAD_API_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_TOKEN_FILE)" || \
		(echo "IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_CLIENT_CERT_FILE)" || \
		(echo "IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_CLIENT_CERT_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_CLIENT_KEY_FILE)" || \
		(echo "IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_CLIENT_KEY_FILE is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_sustained_workload.py generate \
		--profile "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE)" \
		--api-token-file "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_API_TOKEN_FILE)" \
		--otlp-token-file "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_TOKEN_FILE)" \
		--otlp-client-cert-file "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_CLIENT_CERT_FILE)" \
		--otlp-client-key-file "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_CLIENT_KEY_FILE)" \
		$(if $(IIP_CUSTOMER_SUSTAINED_WORKLOAD_API_CA_FILE),--api-ca-file "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_API_CA_FILE)",) \
		$(if $(IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_CA_FILE),--otlp-ca-file "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_CA_FILE)",) \
		--output "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_REPORT)" --allow-traffic

verify-customer-sustained-workload-report:
	@test -n "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE)" || \
		(echo "IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_sustained_workload.py verify \
		--profile "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE)" \
		--report "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_REPORT)" \
		--require-qualified

test-customer-failure-overlap:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_customer_failure_overlap -v

assess-customer-failure-overlap:
	@test -n "$(IIP_CUSTOMER_FAILURE_OVERLAP_PROFILE)" || \
		(echo "IIP_CUSTOMER_FAILURE_OVERLAP_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE)" || \
		(echo "IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/assess_customer_failure_overlap.py assess \
		--profile "$(IIP_CUSTOMER_FAILURE_OVERLAP_PROFILE)" \
		--sustained-profile "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE)" \
		--customer-deployment "$(IIP_CUSTOMER_DEPLOYMENT_QUALIFICATION_REPORT)" \
		--sustained-workload "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_REPORT)" \
		--control-plane-continuity "$(IIP_CONTINUITY_REPORT)" \
		--processing-continuity "$(IIP_PROCESSING_REPORT)" \
		--postgresql-continuity "$(IIP_CUSTOMER_POSTGRESQL_REPORT)" \
		--output "$(IIP_CUSTOMER_FAILURE_OVERLAP_REPORT)"

verify-customer-failure-overlap-report:
	@test -n "$(IIP_CUSTOMER_FAILURE_OVERLAP_PROFILE)" || \
		(echo "IIP_CUSTOMER_FAILURE_OVERLAP_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE)" || \
		(echo "IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/assess_customer_failure_overlap.py verify \
		--report "$(IIP_CUSTOMER_FAILURE_OVERLAP_REPORT)" \
		--profile "$(IIP_CUSTOMER_FAILURE_OVERLAP_PROFILE)" \
		--sustained-profile "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE)" \
		--customer-deployment "$(IIP_CUSTOMER_DEPLOYMENT_QUALIFICATION_REPORT)" \
		--sustained-workload "$(IIP_CUSTOMER_SUSTAINED_WORKLOAD_REPORT)" \
		--control-plane-continuity "$(IIP_CONTINUITY_REPORT)" \
		--processing-continuity "$(IIP_PROCESSING_REPORT)" \
		--postgresql-continuity "$(IIP_CUSTOMER_POSTGRESQL_REPORT)"

test-customer-postgresql-continuity:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_customer_postgresql_continuity -v

qualify-customer-postgresql-continuity:
	@test "$(IIP_CUSTOMER_POSTGRESQL_ALLOW_FAILOVER_OBSERVATION)" = true || \
		(echo "IIP_CUSTOMER_POSTGRESQL_ALLOW_FAILOVER_OBSERVATION must equal true" >&2; exit 2)
	@test -n "$(IIP_KUBERNETES_CONTEXT)" || \
		(echo "IIP_KUBERNETES_CONTEXT is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_API_BASE_URL)" || \
		(echo "IIP_PROCESSING_API_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_API_TOKEN_FILE)" || \
		(echo "IIP_PROCESSING_API_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_OTLP_BASE_URL)" || \
		(echo "IIP_PROCESSING_OTLP_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_OTLP_TOKEN_FILE)" || \
		(echo "IIP_PROCESSING_OTLP_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_OTLP_CLIENT_CERT_FILE)" || \
		(echo "IIP_PROCESSING_OTLP_CLIENT_CERT_FILE is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_OTLP_CLIENT_KEY_FILE)" || \
		(echo "IIP_PROCESSING_OTLP_CLIENT_KEY_FILE is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_IMAGE_DIGEST)" || \
		(echo "IIP_PROCESSING_IMAGE_DIGEST is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POSTGRESQL_PROFILE)" || \
		(echo "IIP_CUSTOMER_POSTGRESQL_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POSTGRESQL_HOST)" || \
		(echo "IIP_CUSTOMER_POSTGRESQL_HOST is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POSTGRESQL_PASSWORD_FILE)" || \
		(echo "IIP_CUSTOMER_POSTGRESQL_PASSWORD_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POSTGRESQL_CA_FILE)" || \
		(echo "IIP_CUSTOMER_POSTGRESQL_CA_FILE is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_postgresql_continuity.py run \
		--api-base-url "$(IIP_PROCESSING_API_BASE_URL)" \
		--api-token-file "$(IIP_PROCESSING_API_TOKEN_FILE)" \
		--otlp-base-url "$(IIP_PROCESSING_OTLP_BASE_URL)" \
		--otlp-token-file "$(IIP_PROCESSING_OTLP_TOKEN_FILE)" \
		--otlp-client-cert-file "$(IIP_PROCESSING_OTLP_CLIENT_CERT_FILE)" \
		--otlp-client-key-file "$(IIP_PROCESSING_OTLP_CLIENT_KEY_FILE)" \
		$(if $(IIP_PROCESSING_API_CA_FILE),--api-ca-file "$(IIP_PROCESSING_API_CA_FILE)",) \
		$(if $(IIP_PROCESSING_OTLP_CA_FILE),--otlp-ca-file "$(IIP_PROCESSING_OTLP_CA_FILE)",) \
		--profile "$(IIP_CUSTOMER_POSTGRESQL_PROFILE)" \
		--image-digest "$(IIP_PROCESSING_IMAGE_DIGEST)" \
		--context "$(IIP_KUBERNETES_CONTEXT)" \
		--namespace "$(IIP_DEPLOYMENT_NAMESPACE)" \
		--database-host "$(IIP_CUSTOMER_POSTGRESQL_HOST)" \
		--database-port "$(IIP_CUSTOMER_POSTGRESQL_PORT)" \
		--database-password-file "$(IIP_CUSTOMER_POSTGRESQL_PASSWORD_FILE)" \
		--database-ca-file "$(IIP_CUSTOMER_POSTGRESQL_CA_FILE)" \
		$(if $(IIP_CUSTOMER_POSTGRESQL_CLIENT_CERT_FILE),--database-client-cert-file "$(IIP_CUSTOMER_POSTGRESQL_CLIENT_CERT_FILE)",) \
		$(if $(IIP_CUSTOMER_POSTGRESQL_CLIENT_KEY_FILE),--database-client-key-file "$(IIP_CUSTOMER_POSTGRESQL_CLIENT_KEY_FILE)",) \
		--attempts-per-phase "$(IIP_CUSTOMER_POSTGRESQL_ATTEMPTS_PER_PHASE)" \
		--probe-interval-milliseconds "$(IIP_CUSTOMER_POSTGRESQL_PROBE_INTERVAL_MILLISECONDS)" \
		--maximum-promotion-milliseconds "$(IIP_CUSTOMER_POSTGRESQL_MAXIMUM_PROMOTION_MILLISECONDS)" \
		--maximum-workflow-milliseconds "$(IIP_CUSTOMER_POSTGRESQL_MAXIMUM_WORKFLOW_MILLISECONDS)" \
		--request-timeout-milliseconds "$(IIP_CUSTOMER_POSTGRESQL_REQUEST_TIMEOUT_MILLISECONDS)" \
		--minimum-api-availability-basis-points "$(IIP_CUSTOMER_POSTGRESQL_MINIMUM_API_AVAILABILITY_BASIS_POINTS)" \
		--minimum-receiver-availability-basis-points "$(IIP_CUSTOMER_POSTGRESQL_MINIMUM_RECEIVER_AVAILABILITY_BASIS_POINTS)" \
		--maximum-consecutive-failures "$(IIP_CUSTOMER_POSTGRESQL_MAXIMUM_CONSECUTIVE_FAILURES)" \
		--output "$(IIP_CUSTOMER_POSTGRESQL_REPORT)" --allow-failover-observation

verify-customer-postgresql-continuity-report:
	@test -n "$(IIP_KUBERNETES_CONTEXT)" || \
		(echo "IIP_KUBERNETES_CONTEXT is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_API_BASE_URL)" || \
		(echo "IIP_PROCESSING_API_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_OTLP_BASE_URL)" || \
		(echo "IIP_PROCESSING_OTLP_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_IMAGE_DIGEST)" || \
		(echo "IIP_PROCESSING_IMAGE_DIGEST is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POSTGRESQL_PROFILE)" || \
		(echo "IIP_CUSTOMER_POSTGRESQL_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POSTGRESQL_HOST)" || \
		(echo "IIP_CUSTOMER_POSTGRESQL_HOST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_postgresql_continuity.py verify \
		--report "$(IIP_CUSTOMER_POSTGRESQL_REPORT)" \
		--api-base-url "$(IIP_PROCESSING_API_BASE_URL)" \
		--otlp-base-url "$(IIP_PROCESSING_OTLP_BASE_URL)" \
		--profile "$(IIP_CUSTOMER_POSTGRESQL_PROFILE)" \
		--image-digest "$(IIP_PROCESSING_IMAGE_DIGEST)" \
		--context "$(IIP_KUBERNETES_CONTEXT)" \
		--namespace "$(IIP_DEPLOYMENT_NAMESPACE)" \
		--database-host "$(IIP_CUSTOMER_POSTGRESQL_HOST)" \
		--database-port "$(IIP_CUSTOMER_POSTGRESQL_PORT)" \
		--require-clean --require-qualified

test-customer-deployment-qualification:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_customer_deployment_qualification -v

qualify-customer-deployment:
	@test -n "$(IIP_KUBERNETES_CONTEXT)" || \
		(echo "IIP_KUBERNETES_CONTEXT is required" >&2; exit 2)
	@test -n "$(IIP_CONTINUITY_IMAGE_DIGEST)" || \
		(echo "IIP_CONTINUITY_IMAGE_DIGEST is required" >&2; exit 2)
	@test -n "$(strip $(IIP_CUSTOMER_QUALIFICATION_VALUES))" || \
		(echo "IIP_CUSTOMER_QUALIFICATION_VALUES is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_PROFILE)" || \
		(echo "IIP_PROCESSING_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_API_BASE_URL)" || \
		(echo "IIP_PROCESSING_API_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OIDC_PROFILE)" || \
		(echo "IIP_CUSTOMER_OIDC_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OIDC_API_BASE_URL)" || \
		(echo "IIP_CUSTOMER_OIDC_API_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POLICY_PROFILE)" || \
		(echo "IIP_CUSTOMER_POLICY_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POLICY_ENDPOINT)" || \
		(echo "IIP_CUSTOMER_POLICY_ENDPOINT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_CREDENTIAL_BROKER_PROFILE)" || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_CREDENTIAL_BROKER_ENDPOINT)" || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_ENDPOINT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_CREDENTIAL_BROKER_CA_FILE)" || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_CA_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_PROFILE)" || \
		(echo "IIP_CUSTOMER_OTLP_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_API_BASE_URL)" || \
		(echo "IIP_CUSTOMER_OTLP_API_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_RECEIVER_ENDPOINT)" || \
		(echo "IIP_CUSTOMER_OTLP_RECEIVER_ENDPOINT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_RECEIVER_CA_FILE)" || \
		(echo "IIP_CUSTOMER_OTLP_RECEIVER_CA_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_CLIENT_CERTIFICATE_FILE)" || \
		(echo "IIP_CUSTOMER_OTLP_CLIENT_CERTIFICATE_FILE is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_OTLP_BASE_URL)" || \
		(echo "IIP_PROCESSING_OTLP_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POSTGRESQL_PROFILE)" || \
		(echo "IIP_CUSTOMER_POSTGRESQL_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POSTGRESQL_HOST)" || \
		(echo "IIP_CUSTOMER_POSTGRESQL_HOST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_deployment.py generate \
		--preflight-report "$(IIP_DEPLOYMENT_PREFLIGHT_REPORT)" \
		--diagnostic-report "$(IIP_DEPLOYMENT_DIAGNOSTIC_REPORT)" \
		--ingress-report "$(IIP_CONTINUITY_INGRESS_REPORT)" \
		--oidc-report "$(IIP_CUSTOMER_OIDC_REPORT)" \
		--oidc-profile "$(IIP_CUSTOMER_OIDC_PROFILE)" \
		--oidc-api-base-url "$(IIP_CUSTOMER_OIDC_API_BASE_URL)" \
		--policy-report "$(IIP_CUSTOMER_POLICY_REPORT)" \
		--policy-profile "$(IIP_CUSTOMER_POLICY_PROFILE)" \
		--policy-endpoint "$(IIP_CUSTOMER_POLICY_ENDPOINT)" \
		--credential-broker-report "$(IIP_CUSTOMER_CREDENTIAL_BROKER_REPORT)" \
		--credential-broker-profile "$(IIP_CUSTOMER_CREDENTIAL_BROKER_PROFILE)" \
		--credential-broker-endpoint "$(IIP_CUSTOMER_CREDENTIAL_BROKER_ENDPOINT)" \
		--credential-broker-ca-file "$(IIP_CUSTOMER_CREDENTIAL_BROKER_CA_FILE)" \
		--otlp-receiver-qualification-report "$(IIP_CUSTOMER_OTLP_REPORT)" \
		--otlp-receiver-qualification-profile "$(IIP_CUSTOMER_OTLP_PROFILE)" \
		--otlp-receiver-api-base-url "$(IIP_CUSTOMER_OTLP_API_BASE_URL)" \
		--otlp-receiver-endpoint "$(IIP_CUSTOMER_OTLP_RECEIVER_ENDPOINT)" \
		$(if $(IIP_CUSTOMER_OTLP_API_CA_FILE),--otlp-receiver-api-ca-file "$(IIP_CUSTOMER_OTLP_API_CA_FILE)") \
		--otlp-receiver-ca-file "$(IIP_CUSTOMER_OTLP_RECEIVER_CA_FILE)" \
		--otlp-receiver-client-certificate-file "$(IIP_CUSTOMER_OTLP_CLIENT_CERTIFICATE_FILE)" \
		--continuity-report "$(IIP_CONTINUITY_REPORT)" \
		--processing-report "$(IIP_PROCESSING_REPORT)" \
		--processing-profile "$(IIP_PROCESSING_PROFILE)" \
		--processing-api-base-url "$(IIP_PROCESSING_API_BASE_URL)" \
		--processing-otlp-base-url "$(IIP_PROCESSING_OTLP_BASE_URL)" \
		--postgresql-report "$(IIP_CUSTOMER_POSTGRESQL_REPORT)" \
		--postgresql-profile "$(IIP_CUSTOMER_POSTGRESQL_PROFILE)" \
		--postgresql-database-host "$(IIP_CUSTOMER_POSTGRESQL_HOST)" \
		--postgresql-database-port "$(IIP_CUSTOMER_POSTGRESQL_PORT)" \
		$(foreach value,$(IIP_CUSTOMER_QUALIFICATION_VALUES),--values "$(value)") \
		--context "$(IIP_KUBERNETES_CONTEXT)" \
		--namespace "$(IIP_DEPLOYMENT_NAMESPACE)" \
		--release-name "$(IIP_DIAGNOSTIC_RELEASE_NAME)" \
		--deployment "$(IIP_CONTINUITY_DEPLOYMENT)" \
		--worker-deployment "$(IIP_PROCESSING_WORKER_DEPLOYMENT)" \
		--receiver-deployment "$(IIP_PROCESSING_RECEIVER_DEPLOYMENT)" \
		--image-digest "$(IIP_CONTINUITY_IMAGE_DIGEST)" \
		--helm "$(HELM)" --kubectl "$(KUBECTL)" \
		--maximum-evidence-age-seconds "$(IIP_CUSTOMER_QUALIFICATION_MAXIMUM_EVIDENCE_AGE_SECONDS)" \
		--maximum-clock-skew-seconds "$(IIP_CUSTOMER_QUALIFICATION_MAXIMUM_CLOCK_SKEW_SECONDS)" \
		--output "$(IIP_CUSTOMER_DEPLOYMENT_QUALIFICATION_REPORT)"

verify-customer-deployment-qualification-report:
	@test -n "$(IIP_KUBERNETES_CONTEXT)" || \
		(echo "IIP_KUBERNETES_CONTEXT is required" >&2; exit 2)
	@test -n "$(IIP_CONTINUITY_IMAGE_DIGEST)" || \
		(echo "IIP_CONTINUITY_IMAGE_DIGEST is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_PROFILE)" || \
		(echo "IIP_PROCESSING_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_API_BASE_URL)" || \
		(echo "IIP_PROCESSING_API_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OIDC_PROFILE)" || \
		(echo "IIP_CUSTOMER_OIDC_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OIDC_API_BASE_URL)" || \
		(echo "IIP_CUSTOMER_OIDC_API_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POLICY_PROFILE)" || \
		(echo "IIP_CUSTOMER_POLICY_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POLICY_ENDPOINT)" || \
		(echo "IIP_CUSTOMER_POLICY_ENDPOINT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_CREDENTIAL_BROKER_PROFILE)" || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_CREDENTIAL_BROKER_ENDPOINT)" || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_ENDPOINT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_CREDENTIAL_BROKER_CA_FILE)" || \
		(echo "IIP_CUSTOMER_CREDENTIAL_BROKER_CA_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_PROFILE)" || \
		(echo "IIP_CUSTOMER_OTLP_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_API_BASE_URL)" || \
		(echo "IIP_CUSTOMER_OTLP_API_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_RECEIVER_ENDPOINT)" || \
		(echo "IIP_CUSTOMER_OTLP_RECEIVER_ENDPOINT is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_RECEIVER_CA_FILE)" || \
		(echo "IIP_CUSTOMER_OTLP_RECEIVER_CA_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_OTLP_CLIENT_CERTIFICATE_FILE)" || \
		(echo "IIP_CUSTOMER_OTLP_CLIENT_CERTIFICATE_FILE is required" >&2; exit 2)
	@test -n "$(IIP_PROCESSING_OTLP_BASE_URL)" || \
		(echo "IIP_PROCESSING_OTLP_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POSTGRESQL_PROFILE)" || \
		(echo "IIP_CUSTOMER_POSTGRESQL_PROFILE is required" >&2; exit 2)
	@test -n "$(IIP_CUSTOMER_POSTGRESQL_HOST)" || \
		(echo "IIP_CUSTOMER_POSTGRESQL_HOST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_customer_deployment.py verify \
		--report "$(IIP_CUSTOMER_DEPLOYMENT_QUALIFICATION_REPORT)" \
		--preflight-report "$(IIP_DEPLOYMENT_PREFLIGHT_REPORT)" \
		--diagnostic-report "$(IIP_DEPLOYMENT_DIAGNOSTIC_REPORT)" \
		--ingress-report "$(IIP_CONTINUITY_INGRESS_REPORT)" \
		--oidc-report "$(IIP_CUSTOMER_OIDC_REPORT)" \
		--oidc-profile "$(IIP_CUSTOMER_OIDC_PROFILE)" \
		--oidc-api-base-url "$(IIP_CUSTOMER_OIDC_API_BASE_URL)" \
		--policy-report "$(IIP_CUSTOMER_POLICY_REPORT)" \
		--policy-profile "$(IIP_CUSTOMER_POLICY_PROFILE)" \
		--policy-endpoint "$(IIP_CUSTOMER_POLICY_ENDPOINT)" \
		--credential-broker-report "$(IIP_CUSTOMER_CREDENTIAL_BROKER_REPORT)" \
		--credential-broker-profile "$(IIP_CUSTOMER_CREDENTIAL_BROKER_PROFILE)" \
		--credential-broker-endpoint "$(IIP_CUSTOMER_CREDENTIAL_BROKER_ENDPOINT)" \
		--credential-broker-ca-file "$(IIP_CUSTOMER_CREDENTIAL_BROKER_CA_FILE)" \
		--otlp-receiver-qualification-report "$(IIP_CUSTOMER_OTLP_REPORT)" \
		--otlp-receiver-qualification-profile "$(IIP_CUSTOMER_OTLP_PROFILE)" \
		--otlp-receiver-api-base-url "$(IIP_CUSTOMER_OTLP_API_BASE_URL)" \
		--otlp-receiver-endpoint "$(IIP_CUSTOMER_OTLP_RECEIVER_ENDPOINT)" \
		$(if $(IIP_CUSTOMER_OTLP_API_CA_FILE),--otlp-receiver-api-ca-file "$(IIP_CUSTOMER_OTLP_API_CA_FILE)") \
		--otlp-receiver-ca-file "$(IIP_CUSTOMER_OTLP_RECEIVER_CA_FILE)" \
		--otlp-receiver-client-certificate-file "$(IIP_CUSTOMER_OTLP_CLIENT_CERTIFICATE_FILE)" \
		--continuity-report "$(IIP_CONTINUITY_REPORT)" \
		--processing-report "$(IIP_PROCESSING_REPORT)" \
		--processing-profile "$(IIP_PROCESSING_PROFILE)" \
		--processing-api-base-url "$(IIP_PROCESSING_API_BASE_URL)" \
		--processing-otlp-base-url "$(IIP_PROCESSING_OTLP_BASE_URL)" \
		--postgresql-report "$(IIP_CUSTOMER_POSTGRESQL_REPORT)" \
		--postgresql-profile "$(IIP_CUSTOMER_POSTGRESQL_PROFILE)" \
		--postgresql-database-host "$(IIP_CUSTOMER_POSTGRESQL_HOST)" \
		--postgresql-database-port "$(IIP_CUSTOMER_POSTGRESQL_PORT)" \
		$(foreach value,$(IIP_CUSTOMER_QUALIFICATION_VALUES),--values "$(value)") \
		--context "$(IIP_KUBERNETES_CONTEXT)" \
		--namespace "$(IIP_DEPLOYMENT_NAMESPACE)" \
		--release-name "$(IIP_DIAGNOSTIC_RELEASE_NAME)" \
		--deployment "$(IIP_CONTINUITY_DEPLOYMENT)" \
		--worker-deployment "$(IIP_PROCESSING_WORKER_DEPLOYMENT)" \
		--receiver-deployment "$(IIP_PROCESSING_RECEIVER_DEPLOYMENT)" \
		--image-digest "$(IIP_CONTINUITY_IMAGE_DIGEST)" \
		--helm "$(HELM)" --kubectl "$(KUBECTL)" \
		--require-current-cluster --require-qualified

test-control-plane-load:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_control_plane_load_qualification -v

qualify-control-plane-load:
	@test "$(IIP_CONTROL_PLANE_LOAD_ALLOW_TRAFFIC)" = true || \
		(echo "IIP_CONTROL_PLANE_LOAD_ALLOW_TRAFFIC must equal true" >&2; exit 2)
	@test -n "$(IIP_CONTROL_PLANE_LOAD_BASE_URL)" || \
		(echo "IIP_CONTROL_PLANE_LOAD_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_CONTROL_PLANE_LOAD_TOKEN_FILE)" || \
		(echo "IIP_CONTROL_PLANE_LOAD_TOKEN_FILE is required" >&2; exit 2)
	@test -n "$(IIP_CONTROL_PLANE_LOAD_IMAGE_DIGEST)" || \
		(echo "IIP_CONTROL_PLANE_LOAD_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_control_plane_load.py run \
		--allow-traffic \
		--base-url "$(IIP_CONTROL_PLANE_LOAD_BASE_URL)" \
		--token-file "$(IIP_CONTROL_PLANE_LOAD_TOKEN_FILE)" \
		--image-digest "$(IIP_CONTROL_PLANE_LOAD_IMAGE_DIGEST)" \
		$(if $(IIP_CONTROL_PLANE_LOAD_CA_FILE),--ca-file "$(IIP_CONTROL_PLANE_LOAD_CA_FILE)",) \
		--duration-seconds "$(IIP_CONTROL_PLANE_LOAD_DURATION_SECONDS)" \
		--target-requests-per-second "$(IIP_CONTROL_PLANE_LOAD_REQUESTS_PER_SECOND)" \
		--concurrency "$(IIP_CONTROL_PLANE_LOAD_CONCURRENCY)" \
		--minimum-successful-request-basis-points "$(IIP_CONTROL_PLANE_LOAD_MINIMUM_SUCCESS_BASIS_POINTS)" \
		--maximum-scheduler-miss-basis-points "$(IIP_CONTROL_PLANE_LOAD_MAXIMUM_SCHEDULER_MISS_BASIS_POINTS)" \
		--maximum-p95-latency-milliseconds "$(IIP_CONTROL_PLANE_LOAD_MAXIMUM_P95_MILLISECONDS)" \
		--maximum-p99-latency-milliseconds "$(IIP_CONTROL_PLANE_LOAD_MAXIMUM_P99_MILLISECONDS)" \
		--request-timeout-milliseconds "$(IIP_CONTROL_PLANE_LOAD_REQUEST_TIMEOUT_MILLISECONDS)" \
		--maximum-scheduler-lag-milliseconds "$(IIP_CONTROL_PLANE_LOAD_MAXIMUM_SCHEDULER_LAG_MILLISECONDS)" \
		--output "$(IIP_CONTROL_PLANE_LOAD_REPORT)"

verify-control-plane-load-report:
	@test -n "$(IIP_CONTROL_PLANE_LOAD_BASE_URL)" || \
		(echo "IIP_CONTROL_PLANE_LOAD_BASE_URL is required" >&2; exit 2)
	@test -n "$(IIP_CONTROL_PLANE_LOAD_IMAGE_DIGEST)" || \
		(echo "IIP_CONTROL_PLANE_LOAD_IMAGE_DIGEST is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_control_plane_load.py verify \
		--report "$(IIP_CONTROL_PLANE_LOAD_REPORT)" \
		--base-url "$(IIP_CONTROL_PLANE_LOAD_BASE_URL)" \
		--image-digest "$(IIP_CONTROL_PLANE_LOAD_IMAGE_DIGEST)" \
		--require-qualified

test-kubernetes-availability:
	PYTHONPATH=src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_kubernetes_availability_qualification -v

qualify-kubernetes-availability:
	IIP_AVAILABILITY_REPORT="$(IIP_KUBERNETES_AVAILABILITY_REPORT)" \
		IIP_DOCKER_BIN=$(DOCKER) IIP_KIND_BIN=$(KIND) \
		IIP_KUBECTL_BIN=$(KUBECTL) IIP_HELM_BIN=$(HELM) \
		IIP_TEST_PYTHON=$(PYTHON) scripts/test_kubernetes_availability.sh

verify-kubernetes-availability-report:
	PYTHONPATH=src:sdks/python/src $(PYTHON) \
		scripts/kubernetes_availability_qualification.py verify \
		--report "$(IIP_KUBERNETES_AVAILABILITY_REPORT)" \
		--require-clean --require-qualified

test-postgres:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) scripts/test_postgres.sh

test-capacity:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) scripts/test_capacity.sh

test-credential-broker:
	IIP_DOCKER_BIN=$(DOCKER) PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/run_credential_broker_compatibility.py \
		--report dist/credential-broker-compatibility-report.json

test-oidc: test-oidc-verifier test-oidc-browser

test-oidc-verifier:
	IIP_DOCKER_BIN=$(DOCKER) PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/run_oidc_issuer_compatibility.py \
		--report dist/oidc-issuer-compatibility-report.json

test-oidc-browser:
	IIP_DOCKER_BIN=$(DOCKER) PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/run_oidc_browser_compatibility.py \
		--report dist/oidc-browser-compatibility-report.json

test-policy-engine:
	IIP_DOCKER_BIN=$(DOCKER) PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/run_policy_engine_compatibility.py \
		--report dist/policy-engine-compatibility-report.json

test-github-context:
	PYTHONPATH=src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_github_context_backend tests.test_github_context_compatibility -v
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/run_github_context_compatibility.py \
		--output "$(IIP_GITHUB_CONTEXT_COMPATIBILITY_REPORT)"

qualify-github-context:
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/run_github_context_compatibility.py \
		--output "$(IIP_GITHUB_CONTEXT_COMPATIBILITY_REPORT)" --require-clean

verify-github-context-report:
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/run_github_context_compatibility.py \
		--verify "$(IIP_GITHUB_CONTEXT_COMPATIBILITY_REPORT)" --require-clean

test-external-secrets:
	IIP_KUBECTL_BIN=$(KUBECTL) IIP_HELM_BIN=$(HELM) scripts/test_external_secrets.sh

test-backup-restore:
	IIP_DOCKER_BIN=$(DOCKER) PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/backup_restore_experiment.py \
		--output "$(IIP_DATABASE_RECOVERY_REPORT)"

verify-backup-restore-report:
	@test -n "$(IIP_DATABASE_RECOVERY_REPORT)" || \
		(echo "IIP_DATABASE_RECOVERY_REPORT is required" >&2; exit 2)
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/backup_restore_experiment.py \
		--verify-report "$(IIP_DATABASE_RECOVERY_REPORT)" --require-clean

test-postgres-continuity:
	IIP_DOCKER_BIN=$(DOCKER) PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/postgresql_continuity_experiment.py \
		--output "$(IIP_DATABASE_CONTINUITY_REPORT)"

verify-postgres-continuity-report:
	@test -n "$(IIP_DATABASE_CONTINUITY_REPORT)" || \
		(echo "IIP_DATABASE_CONTINUITY_REPORT is required" >&2; exit 2)
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/postgresql_continuity_experiment.py \
		--verify-report "$(IIP_DATABASE_CONTINUITY_REPORT)" --require-clean

test-otel:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) scripts/test_otel.sh

test-operational-alerts:
	IIP_DOCKER_BIN=$(DOCKER) IIP_HELM_BIN=$(HELM) scripts/test_operational_alerts.sh

test-otlp-receiver:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) scripts/test_otlp_receiver.sh

test-ai-finops:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) \
		IIP_AI_FINOPS_RUNTIME_REPORT=$(IIP_AI_FINOPS_RUNTIME_REPORT) \
		scripts/test_ai_finops.sh

verify-ai-finops-runtime-report:
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/ai_finops_fixture.py \
		verify-report --report "$(IIP_AI_FINOPS_RUNTIME_REPORT)" --require-clean

test-aws-bedrock-price-import:
	PYTHONPATH=src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_aws_bedrock_price_catalog_import -v

import-aws-bedrock-price-catalog:
	@test -n "$(IIP_AWS_BEDROCK_PRICE_IMPORT_POLICY)" || \
		(echo "IIP_AWS_BEDROCK_PRICE_IMPORT_POLICY is required" >&2; exit 2)
	@test -n "$(IIP_AWS_BEDROCK_PRICE_SOURCE)" || \
		(echo "IIP_AWS_BEDROCK_PRICE_SOURCE is required" >&2; exit 2)
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/import_aws_bedrock_price_catalog.py fetch \
		--policy "$(IIP_AWS_BEDROCK_PRICE_IMPORT_POLICY)" \
		--source-output "$(IIP_AWS_BEDROCK_PRICE_SOURCE)" \
		--catalog-output "$(IIP_AWS_BEDROCK_PRICE_CATALOG)" \
		--report-output "$(IIP_AWS_BEDROCK_PRICE_IMPORT_REPORT)"

verify-aws-bedrock-price-import:
	@test -n "$(IIP_AWS_BEDROCK_PRICE_IMPORT_POLICY)" || \
		(echo "IIP_AWS_BEDROCK_PRICE_IMPORT_POLICY is required" >&2; exit 2)
	@test -n "$(IIP_AWS_BEDROCK_PRICE_SOURCE)" || \
		(echo "IIP_AWS_BEDROCK_PRICE_SOURCE is required" >&2; exit 2)
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/import_aws_bedrock_price_catalog.py verify \
		--source "$(IIP_AWS_BEDROCK_PRICE_SOURCE)" \
		--policy "$(IIP_AWS_BEDROCK_PRICE_IMPORT_POLICY)" \
		--catalog "$(IIP_AWS_BEDROCK_PRICE_CATALOG)" \
		--report "$(IIP_AWS_BEDROCK_PRICE_IMPORT_REPORT)"

test-ai-price-catalog-qualification:
	PYTHONPATH=src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_ai_price_catalog_qualification -v

qualify-ai-price-catalog:
	@test -n "$(IIP_AI_PRICE_CATALOG_FILE)" || \
		(echo "IIP_AI_PRICE_CATALOG_FILE is required" >&2; exit 2)
	@test -n "$(IIP_AI_PRICE_QUALIFICATION_POLICY)" || \
		(echo "IIP_AI_PRICE_QUALIFICATION_POLICY is required" >&2; exit 2)
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/qualify_ai_price_catalog.py generate \
		--catalog "$(IIP_AI_PRICE_CATALOG_FILE)" \
		--policy "$(IIP_AI_PRICE_QUALIFICATION_POLICY)" \
		--qualification-level production-catalog \
		--output "$(IIP_AI_PRICE_QUALIFICATION_REPORT)"

verify-ai-price-catalog-report:
	@test -n "$(IIP_AI_PRICE_CATALOG_FILE)" || \
		(echo "IIP_AI_PRICE_CATALOG_FILE is required" >&2; exit 2)
	@test -n "$(IIP_AI_PRICE_QUALIFICATION_POLICY)" || \
		(echo "IIP_AI_PRICE_QUALIFICATION_POLICY is required" >&2; exit 2)
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/qualify_ai_price_catalog.py verify \
		--report "$(IIP_AI_PRICE_QUALIFICATION_REPORT)" \
		--catalog "$(IIP_AI_PRICE_CATALOG_FILE)" \
		--policy "$(IIP_AI_PRICE_QUALIFICATION_POLICY)"

test-bedrock-instrumentation:
	IIP_DOCKER_BIN=$(DOCKER) IIP_BEDROCK_COMPATIBILITY_MODE=offline \
		scripts/test_bedrock_instrumentation.sh

test-bedrock-live:
	IIP_DOCKER_BIN=$(DOCKER) IIP_BEDROCK_COMPATIBILITY_MODE=live \
		scripts/test_bedrock_instrumentation.sh

test-openai-instrumentation:
	IIP_DOCKER_BIN=$(DOCKER) IIP_OPENAI_COMPATIBILITY_MODE=offline \
		scripts/test_openai_instrumentation.sh

test-openai-live:
	IIP_DOCKER_BIN=$(DOCKER) IIP_OPENAI_COMPATIBILITY_MODE=live \
		scripts/test_openai_instrumentation.sh

test-prometheus:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) scripts/test_prometheus.sh

test-collector-queue-loss:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) scripts/test_collector_queue_loss.sh

test-loki:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) scripts/test_loki.sh

test-opensearch:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) scripts/test_opensearch.sh

test-kubernetes-events:
	IIP_TEST_PYTHON=$(PYTHON) scripts/test_kubernetes_events.sh

test-kubernetes-actions:
	IIP_TEST_PYTHON=$(PYTHON) scripts/test_kubernetes_actions.sh

test-kubernetes-live:
	IIP_TEST_PYTHON=$(PYTHON) scripts/test_kubernetes_live.sh

test-plugin-runner: test-plugin-compatibility

test-plugin-compatibility:
	IIP_DOCKER_BIN=$(DOCKER) PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/run_plugin_runner_conformance.py \
		--report dist/plugin-compatibility-report.json

test-local-product:
	$(PYTHON) scripts/test_local_product.py

test-helm-install:
	IIP_DOCKER_BIN=$(DOCKER) IIP_HELM_BIN=$(HELM) IIP_TEST_PYTHON=$(PYTHON) scripts/test_helm_install.sh

test-release-install:
	@test -n "$(IIP_RELEASE_BUNDLE)" || \
		(echo "IIP_RELEASE_BUNDLE is required" >&2; exit 2)
	IIP_RELEASE_BUNDLE="$(IIP_RELEASE_BUNDLE)" \
		IIP_RELEASE_QUALIFICATION_REPORT="$(IIP_RELEASE_QUALIFICATION_REPORT)" \
		IIP_DOCKER_BIN=$(DOCKER) \
		IIP_HELM_BIN=$(HELM) IIP_TEST_PYTHON=$(PYTHON) scripts/test_helm_install.sh

test-release-upgrade:
	@test -n "$(IIP_RELEASE_BUNDLE)" || \
		(echo "IIP_RELEASE_BUNDLE is required" >&2; exit 2)
	@test -n "$(IIP_UPGRADE_FROM_REVISION)" || \
		(echo "IIP_UPGRADE_FROM_REVISION is required" >&2; exit 2)
	IIP_RELEASE_BUNDLE="$(IIP_RELEASE_BUNDLE)" \
		IIP_RELEASE_QUALIFICATION_REPORT="$(IIP_RELEASE_QUALIFICATION_REPORT)" \
		IIP_UPGRADE_FROM_REVISION="$(IIP_UPGRADE_FROM_REVISION)" \
		IIP_DOCKER_BIN=$(DOCKER) IIP_HELM_BIN=$(HELM) \
		IIP_TEST_PYTHON=$(PYTHON) scripts/test_release_upgrade.sh

qualify-release: test-release-install test-release-upgrade verify-release-qualification

test-release-signatures:
	PYTHONPATH=src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_release_signature_verification -v
	IIP_DOCKER_BIN=$(DOCKER) scripts/test_release_signatures.sh

test-release-publication:
	PYTHONPATH=src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_release_publication tests.test_release_workflow -v
	IIP_DOCKER_BIN=$(DOCKER) PYTHONPATH=src:sdks/python/src $(PYTHON) \
		scripts/run_release_publication_compatibility.py
	IIP_COSIGN_DOCKER_BIN=$(DOCKER) scripts/cosign_container.sh \
		version --json >/dev/null

qualify-release-signatures:
	@test -n "$(IIP_RELEASE_BUNDLE)" || \
		(echo "IIP_RELEASE_BUNDLE is required" >&2; exit 2)
	@test -n "$(IIP_RELEASE_SIGNATURE_POLICY)" || \
		(echo "IIP_RELEASE_SIGNATURE_POLICY is required" >&2; exit 2)
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/release_signature_verification.py run \
		--bundle "$(IIP_RELEASE_BUNDLE)" \
		--policy "$(IIP_RELEASE_SIGNATURE_POLICY)" \
		--output "$(IIP_RELEASE_SIGNATURE_REPORT)" \
		--cosign "$(COSIGN)" --require-clean --require-promotable

verify-release-signature-report:
	@test -n "$(IIP_RELEASE_SIGNATURE_REPORT)" || \
		(echo "IIP_RELEASE_SIGNATURE_REPORT is required" >&2; exit 2)
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/release_signature_verification.py verify \
		--report "$(IIP_RELEASE_SIGNATURE_REPORT)" --require-clean

test-release-vulnerabilities:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) \
		scripts/test_release_vulnerabilities.sh

qualify-release-vulnerabilities:
	@test -n "$(IIP_RELEASE_BUNDLE)" || \
		(echo "IIP_RELEASE_BUNDLE is required" >&2; exit 2)
	@test -n "$(IIP_RELEASE_VULNERABILITY_POLICY)" || \
		(echo "IIP_RELEASE_VULNERABILITY_POLICY is required" >&2; exit 2)
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/release_vulnerability_qualification.py run \
		--bundle "$(IIP_RELEASE_BUNDLE)" \
		--policy "$(IIP_RELEASE_VULNERABILITY_POLICY)" \
		--output "$(IIP_RELEASE_VULNERABILITY_REPORT)" --docker "$(DOCKER)" \
		--require-clean --require-qualified

verify-release-vulnerability-report:
	@test -n "$(IIP_RELEASE_VULNERABILITY_REPORT)" || \
		(echo "IIP_RELEASE_VULNERABILITY_REPORT is required" >&2; exit 2)
	PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/release_vulnerability_qualification.py verify \
		--report "$(IIP_RELEASE_VULNERABILITY_REPORT)" \
		--require-clean --require-qualified

test-release-readiness:
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) -m unittest \
		tests.test_release_readiness -v

assess-release-readiness:
	@test -n "$(IIP_RELEASE_BUNDLE)" || \
		(echo "IIP_RELEASE_BUNDLE is required" >&2; exit 2)
	@test -n "$(IIP_RELEASE_QUALIFICATION_REPORT)" || \
		(echo "IIP_RELEASE_QUALIFICATION_REPORT is required" >&2; exit 2)
	@test -n "$(IIP_RELEASE_VULNERABILITY_REPORT)" || \
		(echo "IIP_RELEASE_VULNERABILITY_REPORT is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/release_readiness.py generate \
		--bundle "$(IIP_RELEASE_BUNDLE)" \
		--release-qualification "$(IIP_RELEASE_QUALIFICATION_REPORT)" \
		--vulnerability-qualification "$(IIP_RELEASE_VULNERABILITY_REPORT)" \
		--evidence-dir "$(IIP_RELEASE_EVIDENCE_DIR)" \
		--output "$(IIP_RELEASE_READINESS_REPORT)" \
		--require-clean --require-locally-qualified

verify-release-readiness-report:
	@test -n "$(IIP_RELEASE_BUNDLE)" || \
		(echo "IIP_RELEASE_BUNDLE is required" >&2; exit 2)
	@test -n "$(IIP_RELEASE_QUALIFICATION_REPORT)" || \
		(echo "IIP_RELEASE_QUALIFICATION_REPORT is required" >&2; exit 2)
	@test -n "$(IIP_RELEASE_VULNERABILITY_REPORT)" || \
		(echo "IIP_RELEASE_VULNERABILITY_REPORT is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/release_readiness.py verify \
		--bundle "$(IIP_RELEASE_BUNDLE)" \
		--release-qualification "$(IIP_RELEASE_QUALIFICATION_REPORT)" \
		--vulnerability-qualification "$(IIP_RELEASE_VULNERABILITY_REPORT)" \
		--evidence-dir "$(IIP_RELEASE_EVIDENCE_DIR)" \
		--report "$(IIP_RELEASE_READINESS_REPORT)" \
		--require-clean --require-locally-qualified

qualify-local-release:
	@test -n "$(IIP_UPGRADE_FROM_REVISION)" || \
		(echo "IIP_UPGRADE_FROM_REVISION is required" >&2; exit 2)
	PYTHONPATH=scripts:src:sdks/python/src $(PYTHON) scripts/qualify_local_release.py \
		--upgrade-from-revision "$(IIP_UPGRADE_FROM_REVISION)" \
		--make-bin "$(MAKE)" --python-bin "$(PYTHON)" \
		--docker-bin "$(DOCKER)" --helm-bin "$(HELM)" \
		--kubectl-bin "$(KUBECTL)" --kind-bin "$(KIND)" \
		--npm-bin "$(NPM)" --cosign-bin "$(COSIGN)"

db-migrate:
	PYTHONPATH=src $(PYTHON) -m iip.adapters.postgres

helm-lint:
	$(HELM) lint deploy/helm/infra-intelligence
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system >/dev/null
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set-string image.digest=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set database.existingSecret=iip-database \
		--set otlpReceiver.enabled=true \
		--set otlpReceiver.channelsExistingSecret=iip-otlp-metrics \
		--set otlpLogsReceiver.enabled=true \
		--set otlpLogsReceiver.channelsExistingSecret=iip-otlp-logs \
		--set aiUsageReceiver.enabled=true \
		--set aiUsageReceiver.channelsExistingSecret=iip-ai-usage \
		--set networkPolicy.enabled=true \
		--set networkPolicy.databaseEgress.enabled=true \
		--set networkPolicy.otlpReceiverIngress.enabled=true >/dev/null
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set database.existingSecret=iip-database \
		--set otlpReceiver.enabled=true \
		--set otlpReceiver.channelsExistingSecret=iip-otlp-metrics \
		--set telemetry.metricsEnabled=true \
		--set telemetry.otlpEndpoint=http://otel-collector.observability:4318 \
		--set networkPolicy.enabled=true \
		--set networkPolicy.databaseEgress.enabled=true \
		--set networkPolicy.otlpReceiverIngress.enabled=true \
		--set networkPolicy.otlpEgress.enabled=true >/dev/null
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set database.existingSecret=iip-database \
		--set otlpReceiver.enabled=true \
		--set otlpReceiver.channelsExistingSecret=iip-otlp-metrics \
		--set otlpIngest.tls.mode=mutual-spiffe \
		--set otlpIngest.tls.serverExistingSecret=iip-otlp-server-tls \
		--set otlpIngest.tls.clientCaExistingSecret=iip-otlp-client-ca \
		--set otlpIngest.tls.identitiesExistingSecret=iip-otlp-identities >/dev/null
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set database.existingSecret=iip-database \
		--set worker.enabled=true \
		--set 'worker.tenants[0]=tenant-a' \
		--set 'worker.ingestionMonitorTargets[0].tenantId=tenant-a' \
		--set 'worker.ingestionMonitorTargets[0].sourceId=kubernetes-a' >/dev/null
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set database.existingSecret=iip-database \
		--set worker.enabled=true \
		--set 'worker.tenants[0]=tenant-a' \
		--set evidenceRetention.enabled=true >/dev/null
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set database.existingSecret=iip-database \
		--set worker.enabled=true \
		--set 'worker.tenants[0]=tenant-a' \
		--set aiAttribution.enabled=true \
		--set aiAttribution.policiesExistingSecret=iip-ai-attribution \
		--set aiCostEngine.enabled=true \
		--set aiCostEngine.catalogsExistingSecret=iip-ai-prices \
		--set aiSavingsEngine.enabled=true \
		--set aiSavingsEngine.profilesExistingSecret=iip-ai-savings \
		--set aiAllocationReporting.enabled=true \
		--set telemetry.metricsEnabled=true \
		--set telemetry.otlpEndpoint=http://otel-collector.observability:4318 >/dev/null
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set database.existingSecret=iip-database \
		--set worker.enabled=true \
		--set 'worker.tenants[0]=tenant-a' \
		--set eventPublisher.mode=https-webhook \
		--set eventPublisher.httpsWebhook.endpoint=https://events.example.test/v1/cloudevents \
		--set eventPublisher.httpsWebhook.tokenExistingSecret=iip-event-token \
		--set networkPolicy.enabled=true \
		--set networkPolicy.databaseEgress.enabled=true \
		--set networkPolicy.eventPublisherEgress.enabled=true >/dev/null
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set database.existingSecret=iip-database \
		--set database.migrations.enabled=true \
		--set networkPolicy.enabled=true \
		--set networkPolicy.databaseEgress.enabled=true >/dev/null
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set database.existingSecret=iip-database \
		--set backup.enabled=true \
		--set backup.destination.existingClaim=iip-backups \
		--set networkPolicy.enabled=true \
		--set networkPolicy.databaseEgress.enabled=true >/dev/null
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set contextEvidence.backend=github \
		--set contextEvidence.integrationsExistingSecret=iip-github-context \
		--set contextEvidence.credentialsExistingSecret=iip-github-context-token \
		--set networkPolicy.enabled=true \
		--set networkPolicy.contextEgress.enabled=true >/dev/null
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set ingress.enabled=true \
		--set ingress.className=nginx \
		--set ingress.host=iip.example.test \
		--set ingress.tls.existingSecret=iip-tls \
		--set ingress.tlsRedirectAnnotation=nginx.ingress.kubernetes.io/force-ssl-redirect \
		--set networkPolicy.enabled=true \
		--set networkPolicy.ingressController.enabled=true >/dev/null
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--values deploy/helm/infra-intelligence/examples/production-core.values.yaml \
		--values deploy/helm/infra-intelligence/examples/production-ai-finops.values.yaml \
		--values deploy/helm/infra-intelligence/examples/production-operational-alerts.values.yaml \
		--show-only templates/operational-alerts.yaml >/dev/null
	@if $(HELM) lint deploy/helm/infra-intelligence --set replicaCount=0 >/dev/null 2>&1; then \
		echo "Helm values schema accepted an invalid replica count" >&2; exit 1; \
	fi
	@if $(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set database.existingSecret=iip-database \
		--set otlpReceiver.enabled=true \
		--set otlpReceiver.channelsExistingSecret=iip-otlp-metrics \
		--set telemetry.metricsEnabled=true \
		--set telemetry.otlpEndpoint=http://otel-collector.observability:4318 \
		--set networkPolicy.enabled=true \
		--set networkPolicy.databaseEgress.enabled=true >/dev/null 2>&1; then \
		echo "Helm validation accepted receiver telemetry without Collector egress" >&2; exit 1; \
	fi
	@if $(HELM) lint deploy/helm/infra-intelligence --set unknownCustomerSetting=true >/dev/null 2>&1; then \
		echo "Helm values schema accepted an unknown setting" >&2; exit 1; \
	fi
	@if $(HELM) lint deploy/helm/infra-intelligence \
		--set-string image.digest=sha256:not-a-digest >/dev/null 2>&1; then \
		echo "Helm values schema accepted a mutable or malformed image digest" >&2; exit 1; \
	fi
	@if $(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set worker.enabled=true \
		--set database.existingSecret=iip-database \
		--set 'worker.tenants[0]=tenant-a' \
		--set worker.heartbeatSeconds=30 \
		--set worker.leaseSeconds=30 >/dev/null 2>&1; then \
		echo "Helm validation accepted a heartbeat that cannot renew its lease" >&2; exit 1; \
	fi
	@if $(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set eventDeliverySlo.windowSeconds=300 \
		--set eventDeliverySlo.maximumDeliveryLatencySeconds=300 >/dev/null 2>&1; then \
		echo "Helm validation accepted an event-delivery latency objective as long as its window" >&2; exit 1; \
	fi
	@if $(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set investigationCompletionSlo.windowSeconds=300 \
		--set investigationCompletionSlo.maximumCompletionSeconds=300 >/dev/null 2>&1; then \
		echo "Helm validation accepted an investigation completion objective as long as its window" >&2; exit 1; \
	fi
	@if $(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set evidenceRetention.enabled=true >/dev/null 2>&1; then \
		echo "Helm validation accepted evidence retention without a worker" >&2; exit 1; \
	fi
	@if $(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set worker.enabled=true \
		--set 'worker.tenants[0]=tenant-a' \
		--set aiCostEngine.enabled=true \
		--set aiCostEngine.catalogsExistingSecret=iip-ai-prices \
		--set aiCostEngine.requireQualification=true >/dev/null 2>&1; then \
		echo "Helm validation accepted required AI price qualification without its Secret" >&2; exit 1; \
	fi
	@if $(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set evidenceRetention.ephemeralSeconds=200000 \
		--set evidenceRetention.standardSeconds=100000 >/dev/null 2>&1; then \
		echo "Helm validation accepted unordered evidence retention durations" >&2; exit 1; \
	fi
	@if $(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set ingress.enabled=true \
		--set ingress.className=nginx \
		--set ingress.host=iip.example.test \
		--set networkPolicy.enabled=true \
		--set networkPolicy.ingressController.enabled=true >/dev/null 2>&1; then \
		echo "Helm validation accepted ingress without TLS and redirect policy" >&2; exit 1; \
	fi
	@if $(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set database.existingSecret=iip-database \
		--set backup.enabled=true \
		--set backup.destination.existingClaim=iip-backups >/dev/null 2>&1; then \
		echo "Helm validation accepted backup without database-only NetworkPolicy" >&2; exit 1; \
	fi
	@if $(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set contextEvidence.backend=github \
		--set contextEvidence.integrationsExistingSecret=iip-github-context \
		--set contextEvidence.credentialsExistingSecret=iip-github-context-token \
		--set networkPolicy.enabled=true >/dev/null 2>&1; then \
		echo "Helm validation accepted GitHub context without explicit provider egress" >&2; exit 1; \
	fi
	@if $(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--set operationalAlerts.enabled=true >/dev/null 2>&1; then \
		echo "Helm validation accepted operational alerts without metric export" >&2; exit 1; \
	fi
	@if $(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--values deploy/helm/infra-intelligence/examples/production-core.values.yaml \
		--set telemetry.exportIntervalMillis=300000 \
		--set operationalAlerts.telemetryHeartbeatWindowSeconds=300 >/dev/null 2>&1; then \
		echo "Helm validation accepted a heartbeat window shorter than two export intervals" >&2; exit 1; \
	fi
	@if $(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system \
		--values deploy/helm/infra-intelligence/examples/production-core.values.yaml \
		--set telemetry.healthReportIntervalSeconds=200 \
		--set operationalAlerts.telemetryHeartbeatWindowSeconds=300 >/dev/null 2>&1; then \
		echo "Helm validation accepted a heartbeat window shorter than two health-report intervals" >&2; exit 1; \
	fi

verify: validate validate-schemas test test-console-javascript test-typescript helm-lint test-deployment-preflight

run:
	PYTHONPATH=src $(PYTHON) -m iip.surfaces.http

dev-init:
	$(PYTHON) scripts/local_stack.py init

dev-up:
	$(PYTHON) scripts/local_stack.py up

dev-status:
	$(PYTHON) scripts/local_stack.py status

dev-credentials:
	$(PYTHON) scripts/local_stack.py credentials

dev-down:
	$(PYTHON) scripts/local_stack.py down

ai-finops-up:
	$(PYTHON) scripts/ai_finops_stack.py up

ai-finops-status:
	$(PYTHON) scripts/ai_finops_stack.py status

ai-finops-down:
	$(PYTHON) scripts/ai_finops_stack.py down

package-chart:
	mkdir -p dist
	$(HELM) package deploy/helm/infra-intelligence --destination dist

release-bundle:
	IIP_RELEASE_DOCKER_BIN=$(DOCKER) IIP_RELEASE_HELM_BIN=$(HELM) \
		IIP_RELEASE_NPM_BIN=$(NPM) IIP_RELEASE_PYTHON=$(PYTHON) \
		scripts/build_release_bundle.sh

verify-release-bundle:
	@test -n "$(IIP_RELEASE_BUNDLE)" || \
		(echo "IIP_RELEASE_BUNDLE is required" >&2; exit 2)
	$(PYTHON) scripts/release_bundle.py verify "$(IIP_RELEASE_BUNDLE)"

verify-release-qualification:
	@test -n "$(IIP_RELEASE_BUNDLE)" || \
		(echo "IIP_RELEASE_BUNDLE is required" >&2; exit 2)
	@IIP_QUALIFICATION_REPORT="$(IIP_RELEASE_QUALIFICATION_REPORT)"; \
	if [ -z "$$IIP_QUALIFICATION_REPORT" ]; then \
		IIP_QUALIFICATION_REPORT="$(IIP_RELEASE_BUNDLE).qualification.json"; \
	fi; \
	$(PYTHON) scripts/release_qualification.py verify \
		"$(IIP_RELEASE_BUNDLE)" "$$IIP_QUALIFICATION_REPORT" --require-complete
