.PHONY: help up down logs build migrate shell test lint fmt ps clean load

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.env:
	cp .env.example .env

up: .env ## Build and start the full stack (api, workers, beat, flower, postgres, redis)
	docker compose up --build -d
	@echo "API:    http://localhost:8000/api/v1/notifications/"
	@echo "Flower: http://localhost:5555"

down: ## Stop the stack
	docker compose down

clean: ## Stop the stack and remove volumes
	docker compose down -v --remove-orphans

build: ## Build images
	docker compose build

logs: ## Tail logs
	docker compose logs -f --tail=100

ps: ## Show running services
	docker compose ps

migrate: ## Run database migrations
	docker compose run --rm migrate

shell: ## Django shell inside the api container
	docker compose exec api python manage.py shell

test: ## Run test suite inside a container (uses postgres + redis from compose)
	docker compose run --rm --no-deps -e DJANGO_SETTINGS_MODULE=config.settings.test api pytest -q

test-local: ## Run tests with the local virtualenv (sqlite + fakeredis; no docker required)
	DJANGO_SETTINGS_MODULE=config.settings.test pytest -q

lint: ## Ruff lint
	ruff check .

fmt: ## Ruff format + autofix
	ruff format . && ruff check --fix .

LOAD_REQUESTS ?= 8000
LOAD_USERS ?= 400
LOAD_CONCURRENCY ?= 100
LOAD_API ?= http://127.0.0.1:8000

load: ## Burst-create notifications and print ingest and drain results
	LOAD_REQUESTS="$(LOAD_REQUESTS)" LOAD_USERS="$(LOAD_USERS)" \
	LOAD_CONCURRENCY="$(LOAD_CONCURRENCY)" LOAD_API="$(LOAD_API)" \
	python3 scripts/loadtest.py
