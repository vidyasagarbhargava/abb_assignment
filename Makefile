.PHONY: install migrate run worker test lint typecheck check seed up openapi

PY ?= .venv/bin/python
BACKEND = backend

install:
	$(PY) -m pip install -r $(BACKEND)/requirements-dev.txt

migrate:
	cd $(BACKEND) && ../$(PY) -m alembic upgrade head

run:  ## API + embedded worker for local development
	cd $(BACKEND) && ../$(PY) -m uvicorn app.main:app_factory --factory --reload

worker:
	cd $(BACKEND) && ../$(PY) -m app.worker

test:
	cd $(BACKEND) && ../$(PY) -m pytest --cov=app --cov-report=term-missing:skip-covered

lint:
	cd $(BACKEND) && ../$(PY) -m ruff check . && ../$(PY) -m ruff format --check .

typecheck:
	cd $(BACKEND) && ../$(PY) -m mypy app

check: lint typecheck test

seed:
	$(PY) scripts/seed.py --tenant acme

up:
	docker compose up --build

openapi:
	cd $(BACKEND) && ../$(PY) -c "import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))" > ../docs/openapi.json
