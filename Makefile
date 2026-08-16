.PHONY: help install-verify-deps validate validate-schemas test test-postgres test-backup-restore test-otel test-otlp-receiver test-prometheus test-loki test-kubernetes-events test-kubernetes-actions test-kubernetes-live test-local-product db-migrate helm-lint verify run package-chart dev-init dev-up dev-status dev-credentials dev-down

PYTHON ?= python3
HELM ?= helm
DOCKER ?= docker

help:
	@echo "install-verify-deps Install pinned verification-only Python dependencies"
	@echo "validate      Validate contracts, links, and package boundaries"
	@echo "validate-schemas Validate contract examples against JSON Schemas"
	@echo "test          Run the reference-kernel and SDK tests"
	@echo "test-postgres Run PostgreSQL integration tests with Docker Desktop"
	@echo "test-backup-restore Measure and verify PostgreSQL recovery with Docker Desktop"
	@echo "test-otel     Send reference metrics and traces to an OpenTelemetry Collector"
	@echo "test-otlp-receiver Send official OTLP metrics and logs into the built API image"
	@echo "test-prometheus Query a real Prometheus server through the evidence adapter"
	@echo "test-loki     Query a real Loki server through the log evidence adapter"
	@echo "test-kubernetes-events Query a local cluster through the Event evidence adapter"
	@echo "test-kubernetes-actions Verify server dry-run and a governed restart on a local cluster"
	@echo "test-kubernetes-live Run the observer against an explicit local Kubernetes context"
	@echo "test-local-product Exercise the customer workflow against the running Docker stack"
	@echo "db-migrate    Apply PostgreSQL migrations using IIP_DATABASE_URL"
	@echo "helm-lint     Lint and render the Helm chart"
	@echo "verify        Run all local quality gates"
	@echo "run           Start the reference HTTP API on port 8080"
	@echo "dev-up        Start the durable local product stack in Docker Desktop"
	@echo "dev-status    Show the durable local stack status"
	@echo "dev-credentials Show the local console URL and operator token"
	@echo "dev-down      Stop the local stack while preserving its database"
	@echo "package-chart Package the Helm chart under dist/"

install-verify-deps:
	$(PYTHON) -m pip install --requirement requirements/verify.txt

validate:
	$(PYTHON) scripts/validate_repo.py

validate-schemas:
	$(PYTHON) scripts/validate_schemas.py

test:
	PYTHONPATH=src:sdks/python/src $(PYTHON) -m unittest discover -s tests -v

test-postgres:
	IIP_DOCKER_BIN=$(DOCKER) IIP_TEST_PYTHON=$(PYTHON) scripts/test_postgres.sh

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

test-kubernetes-events:
	IIP_TEST_PYTHON=$(PYTHON) scripts/test_kubernetes_events.sh

test-kubernetes-actions:
	IIP_TEST_PYTHON=$(PYTHON) scripts/test_kubernetes_actions.sh

test-kubernetes-live:
	IIP_TEST_PYTHON=$(PYTHON) scripts/test_kubernetes_live.sh

test-local-product:
	$(PYTHON) scripts/test_local_product.py

db-migrate:
	PYTHONPATH=src $(PYTHON) -m iip.adapters.postgres

helm-lint:
	$(HELM) lint deploy/helm/infra-intelligence
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system >/dev/null

verify: validate validate-schemas test helm-lint

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
