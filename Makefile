.PHONY: help validate test helm-lint verify run package-chart

PYTHON ?= python3
HELM ?= helm

help:
	@echo "validate      Validate contracts, links, and package boundaries"
	@echo "test          Run the reference-kernel and SDK tests"
	@echo "helm-lint     Lint and render the Helm chart"
	@echo "verify        Run all local quality gates"
	@echo "run           Start the reference HTTP API on port 8080"
	@echo "package-chart Package the Helm chart under dist/"

validate:
	$(PYTHON) scripts/validate_repo.py

test:
	PYTHONPATH=src:sdks/python/src $(PYTHON) -m unittest discover -s tests -v

helm-lint:
	$(HELM) lint deploy/helm/infra-intelligence
	$(HELM) template iip deploy/helm/infra-intelligence --namespace iip-system >/dev/null

verify: validate test helm-lint

run:
	PYTHONPATH=src $(PYTHON) -m iip.surfaces.http

package-chart:
	mkdir -p dist
	$(HELM) package deploy/helm/infra-intelligence --destination dist

