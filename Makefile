.PHONY: dev backend frontend frontend-build lint typecheck unit e2e test install

backend:
	GOVBUDGET_AUTH_ENABLED=false python -m uvicorn api.main:app --reload --port 8000

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
