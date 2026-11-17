PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: help install run test lint migrate revision init demo check docker-up docker-down backup

help:
	@echo "PeaWork HRMS v2 targets"
	@echo "  make install   Create .venv and install v2 development dependencies"
	@echo "  make run       Run the development server on http://127.0.0.1:8090"
	@echo "  make test      Run the v2 test suite"
	@echo "  make lint      Run ruff"
	@echo "  make migrate   Apply Alembic migrations"
	@echo "  make revision  Autogenerate a migration (m=\"message\")"
	@echo "  make init      Bootstrap organisation, admin and reference data"
	@echo "  make demo      Seed demo data (NEVER in production)"
	@echo "  make check     Print deployment diagnostics"
	@echo "  make docker-up Start the Docker Compose stack"
	@echo "  make backup    Run the backup script"

install:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements/v2-dev.txt

run:
	$(PYTHON) -m uvicorn peawork.main:app --reload --host 127.0.0.1 --port 8090

test:
	PEAWORK_ENV=test $(PYTHON) -m pytest tests/v2 -q

lint:
	$(PYTHON) -m ruff check peawork tests/v2

migrate:
	$(PYTHON) -m alembic upgrade head

revision:
	$(PYTHON) -m alembic revision --autogenerate -m "$(m)"

init:
	$(PYTHON) -m peawork init

demo:
	$(PYTHON) -m peawork seed-demo

check:
	$(PYTHON) -m peawork check

docker-up:
	docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build

docker-down:
	docker compose -f deploy/docker-compose.yml --env-file deploy/.env down

backup:
	sudo -u peawork ./deploy/backup.sh
