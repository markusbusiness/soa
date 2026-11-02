Система бронирования авиабилетов из двух микросервисов. Booking Service отдаёт REST API клиентам и ходит в Flight Service по gRPC. Флайт сервис хранит рейсы и резервации мест и кеширует чтения в редис. У каждого сервиса свой постгри

Стек: Python 3.12, grpcio + grpcio-tools (protoc), FastAPI, asyncpg, PostgreSQL 16, Flyway, Redis 7.4 Sentinel, redis-py.

## Запуск

Требования: Docker + Docker Compose

```bash
docker compose up --build
```

Порядок старта: обе PostgreSQL → Flyway-миграции → Redis master, replica и 3 sentinel → `flight-service` → `booking-service`. REST: `http://localhost:8000` (Swagger UI `/docs`), gRPC: `localhost:50051` (reflection включён). Порты меняются через `BOOKING_PORT` и `FLIGHT_GRPC_PORT`.

Миграция `V2__seed_data.sql` заводит справочники и 7 рейсов на декабрь 2026, например `SVO → LED` 2026-12-01 и `U6263 SVX → OVB` на 3 места

Переменные окружения (заданы в `docker-compose.yml`):

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `FLIGHT_SERVICE_API_KEY` | dev-значение | API key межсервисных вызовов, один на оба сервиса |
| `CACHE_TTL_SECONDS` | 300 | TTL ключей кеша |
| `GRPC_TIMEOUT_SECONDS` | 2 | Дедлайн одной попытки gRPC-вызова |
| `RETRY_MAX_ATTEMPTS` / `RETRY_BASE_DELAY_MS` / `RETRY_BACKOFF_MULTIPLIER` | 3 / 100 / 2 | Retry |
| `CB_FAILURE_THRESHOLD` / `CB_WINDOW_SECONDS` / `CB_OPEN_TIMEOUT_SECONDS` | 5 / 30 / 15 | Circuit Breaker |
| `PENDING_RESERVATION_TIMEOUT_SECONDS` | 60 | Через сколько освобождать резерв, бронь по которому не записалась |

Остановить и удалить данные: `docker compose down -v`.

## Команды

| Команда | Что делает |
|---|---|
| `make generate` | Генерирует код из `.proto` в `generated/` |
| `make up` | `docker compose up --build` |
| `make test` | Поднимает отдельный стек `dz3-test` и гоняет pytest против настоящих сервисов |
| `make logs` | Логи обоих сервисов |
| `make psql-flights` / `make psql-bookings` | `psql` в БД сервиса |

`scripts/demo.sh` прогоняет E2E-сценарий через curl (нужны `curl` и `jq`): поиск, бронь, нехватка мест, 404, отмена. `scripts/fault_demo.sh` останавливает Flight Service (видно retry и переходы circuit breaker), потом текущий Redis master (видно failover).

## Структура

```
proto/flight/v1/flight.proto       gRPC-контракт
docs/er-diagram.md                 ER-диаграмма (Mermaid)
flight-service/
  db/migration/                    Flyway: схема и сиды
  src/flight_service/
    servicer.py                    методы FlightService
    auth.py                        проверка API key
    cache.py                       Cache-Aside на Redis Sentinel
booking-service/
  db/migration/                    Flyway: bookings, pending_reservations
  src/booking_service/
    main.py                        REST-эндпоинты
    bookings.py                    создание и отмена брони
    sweeper.py                     освобождение зависших резервов
    flight_client.py               gRPC-канал с interceptor'ами
    grpc_middleware/               circuit breaker, retry, API key
tests/                             pytest
generated/                         сгенерированный код (в .gitignore)
```

## Тз

**1. gRPC-контракт.** `proto/flight/v1/flight.proto`: `SearchFlights`, `GetFlight`, `ReserveSeats`, `ReleaseReservation`, плюс `CreateFlight` и `UpdateFlight`. Все даты, включая дату поиска, в `google.protobuf.Timestamp`, статусы в enum `FlightStatus` и `ReservationStatus`, коды ошибок перечислены в комментарии к сервису (`INVALID_ARGUMENT`, `NOT_FOUND`, `RESOURCE_EXHAUSTED`, `FAILED_PRECONDITION`, `ALREADY_EXISTS`, `UNAUTHENTICATED`). Код генерируется `grpc_tools.protoc` на первой стадии каждого Dockerfile.

**2. ER-диаграмма.** `docs/er-diagram.md`. Авиакомпании и аэропорты вынесены в справочники, номер рейса хранится как код авиакомпании + номер, дата вылета не дублируется колонкой. Ограничения заданы `CHECK`: `available_seats` от 0 до `total_seats`, `total_seats`, цена и `seat_count` положительные, прилёт позже вылета, аэропорты различны. Уникальность «номер рейса + дата» даёт уникальный индекс по `(departure_time AT TIME ZONE 'UTC')::date`, одну резервацию на бронь даёт `UNIQUE (booking_id)`.

**3. PostgreSQL и сервисы.** Две БД (`flight-db`, `booking-db`), миграции Flyway запускаются отдельными контейнерами до старта сервисов.

**4. Взаимодействие по gRPC.** Создание брони: `GetFlight` → `ReserveSeats(flight_id, seat_count, booking_id)` → `total_price = seat_count * price` → запись `CONFIRMED`. Если резервирование не удалось, бронь не создаётся. Отмена проверяет `CONFIRMED`, вызывает `ReleaseReservation` и ставит `CANCELLED`.

**5. Транзакции.** `ReserveSeats` в одной транзакции блокирует рейс `SELECT ... FOR UPDATE`, проверяет остаток, уменьшает `available_seats` и создаёт резервацию. `ReleaseReservation` в одной транзакции возвращает места и ставит `RELEASED`. В Booking Service бронь и удаление записи из `pending_reservations` коммитятся вместе. Если ответ `ReserveSeats` потерялся или сервис упал до коммита, запись остаётся, и sweeper через 60 с вызывает `ReleaseReservation`.

**6. Аутентификация.** API key в metadata `x-api-key`. Серверный interceptor проверяет все методы, включая health и reflection, и отвечает `UNAUTHENTICATED`. Ключ передаётся переменной `FLIGHT_SERVICE_API_KEY`.

**7. Redis.** Cache-Aside для `flight:{id}` и `search:{origin}:{destination}:{date|any}`, TTL 300 с. После `ReserveSeats`, `ReleaseReservation`, `UpdateFlight` и `CreateFlight` удаляются ключ рейса и поисковые ключи его маршрута. В логах `cache hit`, `cache miss`, `cache invalidated`. Если Redis недоступен, чтение идёт в БД.

**8. Retry.** Client interceptor: 3 попытки, задержки 100 и 200 мс, повтор только на `UNAVAILABLE` и `DEADLINE_EXCEEDED`. `ReserveSeats` идемпотентен по `booking_id`: повтор возвращает существующую резервацию и места повторно не списывает.

**9. Redis Sentinel.** Master, replica и 3 sentinel с кворумом 2. Flight Service подключается через `redis.asyncio.sentinel.Sentinel`. Узлы Redis имеют статические IP в отдельной сети: Docker DNS долго резолвит имя остановленного контейнера, и без этого Sentinel не успевал сделать failover.

**10. Circuit Breaker.** Client interceptor перед retry. `CLOSED → OPEN` после 5 отказов (`UNAVAILABLE`, `DEADLINE_EXCEEDED`, `INTERNAL`, `UNKNOWN`) за 30 с, через 15 с `HALF_OPEN` с одним пробным запросом. В `OPEN` клиент сразу получает `503 FLIGHT_SERVICE_CIRCUIT_OPEN` с `Retry-After`. Переходы пишутся в лог: `circuit breaker CLOSED -> OPEN`.

## Примеры запросов

```bash
B=http://localhost:8000

curl -s "$B/flights?origin=SVO&destination=LED&date=2026-12-01"
curl -s -X POST $B/bookings -H 'Content-Type: application/json' \
  -d '{"user_id":"u1","flight_id":"11111111-1111-1111-1111-111111111101","passenger_name":"Ivan Petrov","passenger_email":"ivan@example.com","seat_count":2}'
curl -s "$B/bookings?user_id=u1"
curl -s -X POST $B/bookings/<id>/cancel

grpcurl -plaintext -H 'x-api-key: dev-flight-service-key-change-me' \
  -d '{"flight_id":"11111111-1111-1111-1111-111111111101"}' localhost:50051 flight.v1.FlightService/GetFlight
```

Сценарии ошибок для защиты:

| Сценарий | Ожидаемый ответ |
|---|---|
| Несуществующий рейс | 404 `FLIGHT_NOT_FOUND` |
| Мест меньше, чем `seat_count` | 409 `NOT_ENOUGH_SEATS` |
| Рейс не в `SCHEDULED` | 409 `FLIGHT_NOT_AVAILABLE` |
| Повторная отмена | 409 `INVALID_BOOKING_STATUS` |
| `seat_count: 0`, плохой email, нет `destination` | 400 `VALIDATION_ERROR` |
| Flight Service остановлен | 503 `FLIGHT_SERVICE_UNAVAILABLE` после 3 попыток, затем 503 `FLIGHT_SERVICE_CIRCUIT_OPEN` |
| gRPC без `x-api-key` или с неверным | `UNAUTHENTICATED` |
| Sweeper освободил резерв раньше, чем записалась бронь | 503 `BOOKING_EXPIRED` |

## Тесты

`make test` запускает 35 тестов против настоящих сервисов, PostgreSQL и Redis Sentinel: контракт и коды ошибок Flight Service, аутентификация всех методов, идемпотентность `ReserveSeats`, 20 параллельных резервирований на 3 места, ключи, TTL и инвалидация кеша, REST-сценарии брони и отмены, снимок цены, retry и circuit breaker против подставного gRPC-сервера, освобождение зависшего резерва sweeper'ом
