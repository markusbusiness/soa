.PHONY: generate up down logs test psql-flights psql-bookings

generate:
	rm -rf generated && mkdir generated
	docker run --rm -u $$(id -u):$$(id -g) -e HOME=/tmp -v "$(CURDIR)":/build -w /build python:3.12-slim sh -c \
	  "pip install -q --user grpcio-tools==1.85.1 && python -m grpc_tools.protoc -I proto \
	   --python_out=generated --pyi_out=generated --grpc_python_out=generated proto/flight/v1/flight.proto"

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f flight-service booking-service

test:
	docker compose -p dz3-test down -v
	BOOKING_PORT=18000 FLIGHT_GRPC_PORT=15051 REDIS_NET_PREFIX=10.204.0 docker compose -p dz3-test --profile test run --rm --build tests; \
	  status=$$?; docker compose -p dz3-test down -v; exit $$status

psql-flights:
	docker compose exec flight-db psql -U flights -d flights

psql-bookings:
	docker compose exec booking-db psql -U bookings -d bookings
