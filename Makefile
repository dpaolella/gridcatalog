# Data Hub 1.0 — developer entry points.
# Every target here is also what CI runs; if they diverge, CI is wrong.

PY := .venv/bin/python
PIP := uv pip install --python .venv/bin/python

.DEFAULT_GOAL := help
.PHONY: help venv install lint fmt types test test-all conformance graph-suite \
        seed reindex serve web harvest clean up down check

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtualenv
	uv venv --python 3.12 .venv

install: venv ## Install the project with dev extras
	$(PIP) -e ".[dev,opensearch,mcp,llm]"

lint: ## Ruff check
	.venv/bin/ruff check services sdk tests

fmt: ## Ruff format
	.venv/bin/ruff format services sdk tests
	.venv/bin/ruff check --fix services sdk tests

types: ## mypy
	.venv/bin/mypy

test: ## Unit and integration-free test suite
	.venv/bin/pytest -q

test-all: ## Everything, including container-backed tests
	.venv/bin/pytest -q -m ""

conformance: ## SHACL conformance suite only
	.venv/bin/pytest -q tests/conformance -v

graph-suite: ## Q1-Q5 regression suite against a seeded store
	.venv/bin/pytest -q tests/graph -v

check: lint types test ## What CI runs

seed: ## Load the curated seed catalog into the graph
	$(PY) -m datahub.cli seed load

reindex: ## Full rebuild of the search index from the graph
	$(PY) -m datahub.cli index reindex

serve: ## REST API on :8000
	$(PY) -m datahub.cli serve --reload

web: ## Next.js dev server on :3000
	cd web && npm run dev

harvest: ## Harvest one source: make harvest SOURCE=oedi LIMIT=100
	$(PY) -m datahub.harvest --source $(SOURCE) --limit $(or $(LIMIT),100)

semantic: ## Resolve concepts and grade quality
	$(PY) -m datahub.cli semantic run

links: ## Compute inter-dataset links
	$(PY) -m datahub.cli links run

demo: seed reindex semantic links ## A populated local catalog, from nothing
	@echo "Catalog ready. 'make serve' then 'make web'."

web-build: ## Production build of the UI
	cd web && npm run build

e2e: ## Playwright over the M9 done-criterion flows
	@# Both servers, torn down on the way out. The suite drives a real API and
	@# a real UI: mocking either would test the components against a fiction of
	@# the other, which is the class of bug this suite exists to catch.
	E2E_CHROMIUM=$${E2E_CHROMIUM:-/opt/pw-browsers/chromium-1194/chrome-linux/chrome} \
	  npx playwright test

up: ## Start Fuseki, OpenSearch, Postgres, Redis
	docker compose -f ops/docker-compose.yml up -d

down: ## Stop them
	docker compose -f ops/docker-compose.yml down

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache var/ **/__pycache__ \
	  test-results/ playwright-report/ web/.next
