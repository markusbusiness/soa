.PHONY: up down logs test psql clickhouse demo fault-demo

TEST_ENV = SCHEMA_REGISTRY_PORT=18081 CLICKHOUSE_HTTP_PORT=18123 MINIO_PORT=19000 MINIO_CONSOLE_PORT=19001 \
	PRODUCER_PORT=18010 AGGREGATOR_PORT=18011 EXPORTER_PORT=18012 GRAFANA_PORT=13030 \
	GENERATOR_ENABLED=false AGGREGATION_INTERVAL_SECONDS=5 EXPORT_INTERVAL_SECONDS=5 DLQ_RECOVERY_INTERVAL_SECONDS=5

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f producer aggregator exporter

test:
	$(TEST_ENV) docker compose -p dz5-test down -v
	$(TEST_ENV) docker compose -p dz5-test --profile test run --rm --build tests; \
	  status=$$?; $(TEST_ENV) docker compose -p dz5-test down -v; exit $$status

psql:
	docker compose exec postgres psql -U metrics -d metrics

clickhouse:
	docker compose exec clickhouse clickhouse-client -u analytics --password analytics -d analytics

demo:
	./scripts/demo.sh

fault-demo:
	./scripts/fault_demo.sh
