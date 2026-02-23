.PHONY: dev backend frontend frontend-build lint typecheck unit e2e test install

backend:
	GOVBUDGET_AUTH_ENABLED=false uvicorn api.main:app --reload --port 8001

frontend:
	npm --prefix app run dev

dev:
	bash -c 'trap "kill 0" EXIT; npm --prefix app run dev & GOVBUDGET_AUTH_ENABLED=false uvicorn api.main:app --reload --port 8001'

lint:
	ruff check .

typecheck:
	mypy api src tests

unit:
	python -m pytest

e2e:
	@if [ "$${RUN_E2E:-0}" = "1" ] && [ -x app/node_modules/.bin/playwright ]; then \
		npm --prefix app run test:e2e; \
	else \
		echo "Skipping e2e tests (set RUN_E2E=1 to enable)."; \
	fi

frontend-build:
	npm --prefix app run build

install:
	pip install -r api/requirements.txt
	npm --prefix app install
	npx --yes playwright install --with-deps chromium

test: unit frontend-build e2e
