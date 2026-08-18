.PHONY: help install-verify-deps validate validate-schemas test test-typescript test-postgres test-capacity test-credential-broker test-oidc test-policy-engine test-backup-restore test-otel test-otlp-receiver test-prometheus test-loki test-opensearch test-kubernetes-events test-kubernetes-actions test-kubernetes-live test-plugin-runner test-plugin-compatibility test-local-product test-helm-install db-migrate helm-lint verify run package-chart release-bundle verify-release-bundle dev-init dev-up dev-status dev-credentials dev-down

PYTHON ?= python3
HELM ?= helm
DOCKER ?= docker
NPM ?= npm

help:
	@echo "install-verify-deps Install pinned verification-only Python dependencies"
	@echo "validate      Validate contracts, links, and package boundaries"
	@echo "validate-schemas Validate contract examples against JSON Schemas"
	@echo "test          Run the reference-kernel and SDK tests"
	@echo "test-typescript Install locked TypeScript tooling and type-check the SDK"
	@echo "test-postgres Run PostgreSQL integration tests with Docker Desktop"
	@echo "test-capacity Certify large-tenant investigation dispatch capacity with PostgreSQL"
	@echo "test-credential-broker Certify the external broker client over local TLS"
	@echo "test-oidc     Certify OIDC/JWKS authentication over local TLS"
	@echo "test-policy-engine Certify external policy decisions over local TLS"
	@echo "test-backup-restore Measure and verify PostgreSQL recovery with Docker Desktop"
	@echo "test-otel     Send reference metrics and traces to an OpenTelemetry Collector"
	@echo "test-otlp-receiver Send official OTLP metrics and logs into the isolated receiver"
	@echo "test-prometheus Query a real Prometheus server through the evidence adapter"
	@echo "test-loki     Query a real Loki server through the log evidence adapter"
	@echo "test-opensearch Query a real OpenSearch server through the log evidence adapter"
	@echo "test-kubernetes-events Query a local cluster through the Event evidence adapter"
	@echo "test-kubernetes-actions Verify server dry-run and a governed restart on a local cluster"
	@echo "test-kubernetes-live Run the observer against an explicit local Kubernetes context"
	@echo "test-plugin-runner Build and execute the signed no-network plugin sandbox"
	@echo "test-plugin-compatibility Generate the executable plugin compatibility matrix"
	@echo "test-local-product Exercise the customer workflow against the running Docker stack"
	@echo "test-helm-install Build and install the chart on the explicit local kind cluster"
	@echo "db-migrate    Apply PostgreSQL migrations using IIP_DATABASE_URL"
	@echo "helm-lint     Lint and render the Helm chart"
	@echo "verify        Run all local quality gates"
	@echo "run           Start the reference HTTP API on port 8080"
	@echo "dev-up        Start the durable local product stack in Docker Desktop"
	@echo "dev-status    Show the durable local stack status"
	@echo "dev-credentials Show the local console URL and operator token"
	@echo "dev-down      Stop the local stack while preserving its database"
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

test-typescript:
	cd sdks/typescript && $(NPM) ci --ignore-scripts --no-audit --no-fund
	cd sdks/typescript && $(NPM) run check

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

test-backup-restore:
	IIP_DOCKER_BIN=$(DOCKER) PYTHONPATH=src:sdks/python/src $(PYTHON) scripts/backup_restore_experiment.py

test-otel:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) scripts/test_otel.sh

test-otlp-receiver:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) scripts/test_otlp_receiver.sh

test-prometheus:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) scripts/test_prometheus.sh

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

verify: validate validate-schemas test test-typescript helm-lint

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
