.PHONY: dev backend backend-api backend-noauth worker frontend frontend-build lint typecheck unit e2e test install

backend:
	GOVBUDGET_AUTH_ENABLED=$${GOVBUDGET_AUTH_ENABLED:-true} GOVBUDGET_API_KEY=$${GOVBUDGET_API_KEY:-dev-local-key} GOVBUDGET_RATE_LIMIT=$${GOVBUDGET_RATE_LIMIT:-2000} python -m uvicorn api.main:app --reload --port 8000

backend-api:
	JOB_QUEUE_ROLE=api JOB_QUEUE_INLINE_FALLBACK=false GOVBUDGET_AUTH_ENABLED=$${GOVBUDGET_AUTH_ENABLED:-true} GOVBUDGET_API_KEY=$${GOVBUDGET_API_KEY:-dev-local-key} GOVBUDGET_RATE_LIMIT=$${GOVBUDGET_RATE_LIMIT:-2000} python -m uvicorn api.main:app --reload --port 8000

backend-noauth:
	GOVBUDGET_AUTH_ENABLED=false python -m uvicorn api.main:app --reload --port 8000

worker:
	JOB_QUEUE_ROLE=worker GOVBUDGET_AUTH_ENABLED=$${GOVBUDGET_AUTH_ENABLED:-true} GOVBUDGET_API_KEY=$${GOVBUDGET_API_KEY:-dev-local-key} GOVBUDGET_RATE_LIMIT=$${GOVBUDGET_RATE_LIMIT:-2000} python -m api.worker

frontend:
	npm --prefix app run dev

dev:
	npm run dev

lint:
	ruff check .

typecheck:
	mypy api src tests

unit:
	python -m pytest

e2e:
	npm --prefix app run test:e2e

frontend-build:
	npm --prefix app run build

install:
	pip install -r api/requirements.txt
	npm --prefix app install
	npx --yes playwright install --with-deps chromium

test: unit frontend-build e2e
