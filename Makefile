.PHONY: up down ps logs smoke seed test typecheck lint fmt frontend verify

up:
	docker compose up --build -d

down:
	docker compose down -v

ps:
	docker compose ps

logs:
	docker compose logs -f --tail=100

smoke:
	./scripts/smoke.sh

seed:
	./scripts/seed.sh

test:
	cd services/inventory-api && uv run pytest -q
	cd services/checkout-api && uv run pytest -q
	cd services/fulfillment-worker && uv run pytest -q
	cd services/orchestrator && uv run pytest -q

typecheck:
	cd services/orchestrator && uv run --with mypy mypy app

lint:
	uvx ruff check services/
	uvx ruff format --check services/
	cd frontend && npm run lint && npm run typecheck

fmt:
	uvx ruff format services/
	uvx ruff check --fix services/

frontend:
	cd frontend && npm install && npm run lint && npm run typecheck && npm run build

# Full pre-commit gate: lint + types + tests + frontend build.
verify: lint typecheck test frontend
