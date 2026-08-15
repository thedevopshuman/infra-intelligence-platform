.PHONY: help install-verify-deps validate validate-schemas test test-postgres test-backup-restore test-otel test-kubernetes-live db-migrate helm-lint verify run package-chart

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
	@echo "test-otel     Send reference metrics to an OpenTelemetry Collector"
	@echo "test-kubernetes-live Run the observer against an explicit local Kubernetes context"
	@echo "db-migrate    Apply PostgreSQL migrations using IIP_DATABASE_URL"
	@echo "helm-lint     Lint and render the Helm chart"
	@echo "verify        Run all local quality gates"
	@echo "run           Start the reference HTTP API on port 8080"
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

test-kubernetes-live:
	IIP_TEST_PYTHON=$(PYTHON) scripts/test_kubernetes_live.sh

db-migrate:
	PYTHONPATH=src $(PYTHON) -m iip.adapters.postgres

helm-lint:
	$(HELM) lint deploy/helm/infra-intelligence
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system >/dev/null

verify: validate validate-schemas test helm-lint

run:
	PYTHONPATH=src $(PYTHON) -m iip.surfaces.http

package-chart:
	mkdir -p dist
	$(HELM) package deploy/helm/infra-intelligence --destination dist
