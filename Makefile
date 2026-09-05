.PHONY: help install-verify-deps validate validate-schemas test test-typescript test-deployment-preflight preflight-deployment-live verify-deployment-preflight-report test-ingress-availability qualify-ingress-availability verify-ingress-availability-report test-kubernetes-availability qualify-kubernetes-availability verify-kubernetes-availability-report test-postgres test-capacity test-credential-broker test-oidc test-policy-engine test-github-context qualify-github-context verify-github-context-report test-external-secrets test-backup-restore verify-backup-restore-report test-postgres-continuity verify-postgres-continuity-report test-otel test-otlp-receiver test-ai-finops test-ai-price-catalog-qualification qualify-ai-price-catalog verify-ai-price-catalog-report test-bedrock-instrumentation test-bedrock-live test-openai-instrumentation test-openai-live test-prometheus test-collector-queue-loss test-loki test-opensearch test-kubernetes-events test-kubernetes-actions test-kubernetes-live test-plugin-runner test-plugin-compatibility test-local-product test-helm-install test-release-install test-release-upgrade qualify-release test-release-signatures qualify-release-signatures verify-release-signature-report test-release-vulnerabilities qualify-release-vulnerabilities verify-release-vulnerability-report db-migrate helm-lint verify run package-chart release-bundle verify-release-bundle verify-release-qualification dev-init dev-up dev-status dev-credentials dev-down ai-finops-up ai-finops-status ai-finops-down

PYTHON ?= python3
HELM ?= helm
KUBECTL ?= kubectl
DOCKER ?= docker
KIND ?= kind
NPM ?= npm
COSIGN ?= cosign
IIP_DATABASE_RECOVERY_REPORT ?= dist/postgresql-recovery-qualification-report.json
IIP_DATABASE_CONTINUITY_REPORT ?= dist/postgresql-continuity-qualification-report.json
IIP_DEPLOYMENT_PREFLIGHT_REPORT ?= dist/customer-deployment-preflight-report.json
IIP_DEPLOYMENT_PROFILE ?= production-core-v1
IIP_DEPLOYMENT_VALUES ?= deploy/helm/infra-intelligence/examples/production-core.values.yaml
IIP_DEPLOYMENT_NAMESPACE ?= iip-system
IIP_KUBERNETES_CONTEXT ?=
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
IIP_KUBERNETES_AVAILABILITY_REPORT ?= dist/kubernetes-availability-qualification-report.json
IIP_GITHUB_CONTEXT_COMPATIBILITY_REPORT ?= dist/github-context-compatibility-report.json
IIP_RELEASE_SIGNATURE_POLICY ?=
IIP_RELEASE_SIGNATURE_REPORT ?= dist/release-signature-verification-report.json
IIP_RELEASE_VULNERABILITY_POLICY ?= contracts/examples/release-vulnerability-policy.json
IIP_RELEASE_VULNERABILITY_REPORT ?= dist/release-vulnerability-qualification-report.json
IIP_AI_PRICE_CATALOG_FILE ?=
IIP_AI_PRICE_QUALIFICATION_POLICY ?=
IIP_AI_PRICE_QUALIFICATION_REPORT ?= dist/ai-price-catalog-qualification-report.json

help:
	@echo "install-verify-deps Install pinned verification-only Python dependencies"
	@echo "validate      Validate contracts, links, and package boundaries"
	@echo "validate-schemas Validate contract examples against JSON Schemas"
	@echo "test          Run the reference-kernel and SDK tests"
	@echo "test-typescript Install locked TypeScript tooling and type-check the SDK"
	@echo "test-deployment-preflight Validate the sanitized core, GitHub-context, and AI profiles"
	@echo "preflight-deployment-live Check a customer values file and explicit Kubernetes context"
	@echo "verify-deployment-preflight-report Verify current source/configuration-bound preflight evidence"
	@echo "test-ingress-availability Exercise the minimized external probe against real local routes"
	@echo "qualify-ingress-availability Qualify one HTTPS customer ingress and exact release identity"
	@echo "verify-ingress-availability-report Verify clean current ingress qualification evidence"
	@echo "test-kubernetes-availability Validate the planned-disruption report and harness"
	@echo "qualify-kubernetes-availability Prove API/OTLP availability during an owned Kind worker drain"
	@echo "verify-kubernetes-availability-report Verify clean current planned-disruption evidence"
	@echo "test-postgres Run PostgreSQL integration tests with Docker Desktop"
	@echo "test-capacity Certify large-tenant investigation dispatch capacity with PostgreSQL"
	@echo "test-credential-broker Certify the external broker client over local TLS"
	@echo "test-oidc     Certify OIDC/JWKS authentication over local TLS"
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
	@echo "test-otlp-receiver Send official OTLP metrics, logs, and AI usage-to-cost traces"
	@echo "test-ai-finops Prove the local multi-provider AI economics slice"
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
	@echo "test-release-signatures Exercise pinned Cosign signing and tamper rejection locally"
	@echo "qualify-release-signatures Verify published release digests against organizational trust"
	@echo "verify-release-signature-report Validate retained minimized signature evidence"
	@echo "test-release-vulnerabilities Exercise the pinned scanner and offline SBOM path"
	@echo "qualify-release-vulnerabilities Qualify every release SBOM under protected policy"
	@echo "verify-release-vulnerability-report Validate retained minimized vulnerability evidence"
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
	@echo "verify-release-qualification Verify the complete environment-scoped release report"

install-verify-deps:
	$(PYTHON) -m pip install --requirement requirements/verify.txt

validate:
	$(PYTHON) scripts/validate_repo.py

validate-schemas:
	$(PYTHON) scripts/validate_schemas.py

test:
	PYTHONPATH=src:sdks/python/src $(PYTHON) -m unittest discover -s tests -v

test-typescript:
	cd sdks/typescript && $(NPM) ci --ignore-scripts --no-audit --no-fund
	cd sdks/typescript && $(NPM) run check

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

test-oidc:
	IIP_DOCKER_BIN=$(DOCKER) PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/run_oidc_issuer_compatibility.py \
		--report dist/oidc-issuer-compatibility-report.json

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

test-otlp-receiver:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) scripts/test_otlp_receiver.sh

test-ai-finops:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) scripts/test_ai_finops.sh

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

verify: validate validate-schemas test test-typescript helm-lint test-deployment-preflight

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
