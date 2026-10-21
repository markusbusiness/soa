GENERATOR_IMAGE := openapitools/openapi-generator-cli:v7.16.0

.PHONY: generate up down logs test psql

generate:
	rm -rf generated
	docker run --rm -u $$(id -u):$$(id -g) -v "$(CURDIR)":/build -w /build $(GENERATOR_IMAGE) generate -c openapi/generator-config.yaml

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f app

test:
	docker compose -p dz2-test down -v
	docker compose -p dz2-test --profile test run --rm --build tests; status=$$?; docker compose -p dz2-test down -v; exit $$status

psql:
	docker compose exec postgres psql -U marketplace -d marketplace
