# Convenience targets. `make demo` runs the full incremental story;
# `make quality` runs the quality gate (lint, format, types, security, tests).
export PYTHONPATH := src
PY := python

.PHONY: install install-dev demo run update query test lint format typecheck security quality clean

install:
	$(PY) -m pip install -r requirements.txt

install-dev:
	$(PY) -m pip install -e ".[dev]"

# Full end-to-end demo (initial -> idempotent re-run -> update -> queries).
demo: install
	bash run.sh

# Ingest just the initial batch.
run:
	$(PY) -m football_pipeline run --source candidate_dataset/data/raw

# Ingest both batches (incremental update).
update:
	$(PY) -m football_pipeline run --source candidate_dataset/data/raw --source candidate_dataset/data/raw_update

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests

format:
	$(PY) -m ruff format src tests

typecheck:
	$(PY) -m mypy src/football_pipeline

security:
	$(PY) -m bandit -r src -q

# Full quality gate: lint -> format-check -> types -> security -> tests+coverage.
quality:
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests
	$(PY) -m mypy src/football_pipeline
	$(PY) -m bandit -r src -q
	$(PY) -m pytest

clean:
	rm -rf warehouse .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
