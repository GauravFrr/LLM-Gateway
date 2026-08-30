.PHONY: up down test lint seed load-test

up:
	docker-compose up -d --build

down:
	docker-compose down

test:
	.venv/Scripts/pytest || pytest

lint:
	.venv/Scripts/ruff check . || ruff check .

seed:
	docker-compose exec -w /workspace gateway python -m app.db.seed

load-test:
	docker run --rm --network=llmgateway_default -e GATEWAY_URL=http://gateway:8000 -v "$$(pwd)/tests/load:/load" grafana/k6 run /load/gateway_load_test.js
