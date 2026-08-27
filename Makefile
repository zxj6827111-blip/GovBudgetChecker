.PHONY: dev backend backend-api backend-noauth worker frontend frontend-build lint log-safety env-check typecheck unit e2e test install setup-python perf-baseline perf-check

PYTHON ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,.venv/bin/python)

backend:
	GOVBUDGET_AUTH_ENABLED=$${GOVBUDGET_AUTH_ENABLED:-true} GOVBUDGET_API_KEY=$${GOVBUDGET_API_KEY:-dev-local-key} GOVBUDGET_RATE_LIMIT=$${GOVBUDGET_RATE_LIMIT:-2000} $(PYTHON) -m uvicorn api.main:app --reload --port 8000

backend-api:
	JOB_QUEUE_ROLE=api JOB_QUEUE_INLINE_FALLBACK=false GOVBUDGET_AUTH_ENABLED=$${GOVBUDGET_AUTH_ENABLED:-true} GOVBUDGET_API_KEY=$${GOVBUDGET_API_KEY:-dev-local-key} GOVBUDGET_RATE_LIMIT=$${GOVBUDGET_RATE_LIMIT:-2000} $(PYTHON) -m uvicorn api.main:app --reload --port 8000

backend-noauth:
	GOVBUDGET_AUTH_ENABLED=false $(PYTHON) -m uvicorn api.main:app --reload --port 8000

worker:
	JOB_QUEUE_ROLE=worker GOVBUDGET_AUTH_ENABLED=$${GOVBUDGET_AUTH_ENABLED:-true} GOVBUDGET_API_KEY=$${GOVBUDGET_API_KEY:-dev-local-key} GOVBUDGET_RATE_LIMIT=$${GOVBUDGET_RATE_LIMIT:-2000} $(PYTHON) -m api.worker

frontend:
	npm --prefix app run dev

dev:
	npm run dev

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) scripts/check_log_message_safety.py
	$(PYTHON) scripts/check_env_consistency.py

log-safety:
	$(PYTHON) scripts/check_log_message_safety.py

env-check:
	$(PYTHON) scripts/check_env_consistency.py

typecheck:
	$(PYTHON) -m mypy api src tests

unit:
	$(PYTHON) -m pytest

e2e:
	npm --prefix app run test:e2e

frontend-build:
	npm --prefix app run build

setup-python:
	python scripts/setup_python_env.py

install: setup-python
	npm --prefix app install
	npx --yes playwright install --with-deps chromium

test: unit frontend-build e2e

perf-baseline:
	GOVBUDGET_AUTH_ENABLED=false GOVBUDGET_API_KEY=dev PYTHONPATH=. $(PYTHON) scripts/perf_baseline.py

perf-check:
	GOVBUDGET_AUTH_ENABLED=false GOVBUDGET_API_KEY=dev PYTHONPATH=. $(PYTHON) scripts/perf_baseline.py --assert-thresholds
