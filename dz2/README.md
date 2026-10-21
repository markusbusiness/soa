# Marketplace: OpenAPI + CRUD

Сервис маркетплейса на Python/FastAPI с контрактным подходом: OpenAPI-спецификация является источником правды, модели и роутеры генерируются из неё при сборке.

Стек: Python 3.12, FastAPI, asyncpg, PostgreSQL 16, Flyway, openapi-generator (`python-fastapi`), PyJWT, argon2.

## Запуск

Требования: Docker + Docker Compose.

```bash
docker compose up --build
```

Порядок старта: `postgres` (healthcheck) → `flyway migrate` → `app`. Приложение слушает `http://localhost:8080`, порт меняется через `APP_PORT=18090 docker compose up --build`.

Swagger UI: `http://localhost:8080/docs`, спецификация: `http://localhost:8080/openapi.json` (отдаётся файл `openapi/marketplace.yaml` как есть).

Переменные окружения `app` (заданы в `docker-compose.yml`):

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `ORDER_RATE_LIMIT_MINUTES` | 1 | N минут между CREATE_ORDER / UPDATE_ORDER одного пользователя |
| `ACCESS_TOKEN_TTL_MINUTES` | 15 | Срок жизни access-токена |
| `REFRESH_TOKEN_TTL_DAYS` | 7 | Срок жизни refresh-токена |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | `admin@marketplace.local` / `admin12345` | Учётка ADMIN, создаётся при старте |
| `JWT_SECRET` | dev-значение | Ключ подписи HS256 |

Остановить и удалить данные: `docker compose down -v`.

## Команды

| Команда | Что делает |
|---|---|
| `make generate` | Генерирует код из спецификации в `generated/` |
| `make up` | `docker compose up --build` |
| `make test` | Поднимает отдельный стек `dz2-test` и гоняет pytest против настоящего Postgres |
| `make psql` | Открывает `psql` в контейнере БД |
| `make logs` | JSON-логи приложения |

`scripts/demo.sh` прогоняет E2E-сценарий через curl (нужны `curl` и `jq`): регистрация, товары, промокод, заказ, ошибки, смена статусов, мягкое удаление. Адрес задаётся через `BASE_URL`.

## Структура

```
openapi/
  marketplace.yaml          спецификация (источник правды)
  generator-config.yaml     конфиг openapi-generator
  templates/                переопределённые шаблоны генератора
db/migration/V1__init_schema.sql   миграция Flyway
src/marketplace/
  main.py                   сборка приложения
  security.py               JWT, проверка ролей
  errors.py                 единый формат ошибок
  request_logging.py        middleware JSON-логов и X-Request-Id
  handlers/                 реализации сгенерированных Base*Api
  services/                 бизнес-логика и SQL
tests/                      pytest
generated/                  сгенерированный код (в .gitignore)
```

## Кодогенерация

`openapi/marketplace.yaml` → openapi-generator → пакет `marketplace_api`:

- `models/*.py`: pydantic-модели всех схем с ограничениями из спеки (`min_length`, `max_length`, `ge`, `le`, `multiple_of`, `pattern`, enum);
- `apis/*_api.py`: FastAPI-роутеры со всеми эндпоинтами, параметрами и описанием ответов;
- `apis/*_api_base.py`: базовые классы `Base*Api`, которые реализует приложение.

Генерация выполняется одной командой (`make generate`) и автоматически на первой стадии `Dockerfile`, поэтому `generated/` в репозиторий не попадает. Ручных DTO нет: обработчики в `src/marketplace/handlers/` принимают и возвращают только сгенерированные модели.

Переопределены четыре шаблона генератора:

- `api.mustache`: успешный код ответа из спеки (201 для создания), `x-allowed-roles` попадает в метаданные роута, токен передаётся в реализацию;
- `base_api.mustache`: сигнатуры методов принимают токен;
- `endpoint_argument_definition.mustache`: query-параметры без `strict`, чтобы `?page=1` валидировался как число, тело запроса обязательное;
- `extra_models.mustache`: `TokenModel` с полем `role`.

## Соответствие требованиям

**1–2. Спецификация и схемы.** Все эндпоинты Product, Order, PromoCode и Auth. Отдельные схемы `ProductCreate`, `ProductUpdate`, `ProductResponse`. Деньги `type: number, format: decimal, multipleOf: 0.01`, даты `format: date-time`, `nullable` у `description`, `promo_code`, `promo_code_id`, `details`. `GET /products` возвращает `items`, `totalElements`, `page`, `size`.

**3. Кодогенерация.** См. раздел выше.

**4. PostgreSQL.** Схема создаётся миграцией Flyway. Индекс `idx_products_status`. `created_at` заполняется `DEFAULT now()`, `updated_at` обновляет триггер `set_updated_at`. `DELETE /products/{id}` переводит товар в `ARCHIVED`.

**5. Ошибки.** Формат `ErrorResponse {error_code, message, details}`, каталог кодов в enum `ErrorCode`, у каждой операции описаны возможные ответы. 404/405 от роутера и 500 тоже приводятся к этому формату.

**6. Валидация.** Все ограничения заданы в спеке и проверяются сгенерированными моделями до вызова обработчика. Ответ `400 VALIDATION_ERROR` перечисляет каждое поле:

```json
{"error_code": "VALIDATION_ERROR", "message": "Request validation failed",
 "details": {"fields": [{"field": "price", "message": "Input should be greater than or equal to 0.01"}]}}
```

Проверки, которые нельзя выразить в OpenAPI 3.0 (повтор `product_id` в заказе, `valid_until > valid_from`, процент не больше 100), выполняются в обработчике и возвращают тот же формат.

**7. Заказы** (`src/marketplace/services/orders.py`). Каждая операция выполняется в одной транзакции.

Создание: лимит частоты → активный заказ → существование и статус товаров → остатки (в `details` все позиции с нехваткой) → резервирование → снапшот `price_at_order` → промокод → запись `CREATE_ORDER`.

Обновление: владелец → статус `CREATED` → лимит частоты → возврат старых остатков → проверка и резервирование новых → пересчёт суммы и промокода (если промокод перестал проходить по `min_order_amount` или сроку, скидка снимается, `current_uses` уменьшается) → запись `UPDATE_ORDER`.

Отмена: владелец → статус `CREATED` или `PAYMENT_PENDING` → возврат остатков → возврат использования промокода → `CANCELED`.

Скидка PERCENTAGE применяется, если она не больше 70% суммы заказа, иначе заказ стоит полную цену. FIXED_AMOUNT: `min(discount_value, total_amount)`.

Конкурентность: строки пользователя, заказа, товаров и промокода блокируются `SELECT ... FOR UPDATE`. Товары блокируются в порядке `id`, чтобы параллельные заказы не взаимоблокировались. Поэтому два одновременных заказа не продают больше остатка и не обходят лимит частоты. Частичный уникальный индекс `ux_orders_one_active_per_user` дополнительно гарантирует один активный заказ на пользователя на уровне БД.

Модель состояний: `CREATED → PAYMENT_PENDING → PAID → SHIPPED → COMPLETED`, отмена из `CREATED` и `PAYMENT_PENDING`. Служебный эндпоинт `POST /orders/{id}/status` (только ADMIN) переводит заказ на следующий шаг, любой другой переход даёт `INVALID_STATE_TRANSITION`.

**8. Логи.** На каждый запрос одна JSON-строка в stdout:

```json
{"request_id": "ce0e1275-...", "method": "POST", "endpoint": "/auth/register", "status_code": 201,
 "duration_ms": 67.61, "user_id": null, "timestamp": "2026-10-18T13:55:43.153798+00:00",
 "request_body": {"email": "seller@demo.local", "password": "***", "role": "SELLER"}}
```

Тело логируется для POST/PUT/DELETE, ключи с `password`, `token`, `secret` маскируются. `request_id` возвращается в заголовке `X-Request-Id`.

**9. JWT.** `POST /auth/register`, `/auth/login`, `/auth/refresh`. Access живёт 15 минут, refresh 7 дней. Refresh одноразовый: при обмене старый отзывается (таблица `refresh_tokens`), повторное предъявление отзывает все сессии пользователя. Ошибки `TOKEN_EXPIRED`, `TOKEN_INVALID`, `REFRESH_TOKEN_INVALID` (401).

**10. Роли.** Роль лежит в claim `role`. Разрешённые роли каждой операции указаны в спеке в `x-allowed-roles` и в описании операции. `security.authenticate` проверяет токен и сверяет роль с `x-allowed-roles` роута, так что матрица доступа задаётся только в спеке. Владение проверяется в сервисах: SELLER меняет только товары со своим `seller_id`, USER работает только со своими заказами (`ORDER_OWNERSHIP_VIOLATION`). Недостаточная роль даёт `403 ACCESS_DENIED`. Через `/auth/register` можно стать USER или SELLER, ADMIN создаётся из конфигурации.

## Проверка данных в БД

```bash
docker compose exec postgres psql -U marketplace -d marketplace
```

```sql
SELECT id, name, price, stock, status, seller_id FROM products;
SELECT id, user_id, status, promo_code_id, total_amount, discount_amount FROM orders;
SELECT order_id, product_id, quantity, price_at_order FROM order_items;
SELECT code, discount_type, discount_value, current_uses, max_uses FROM promo_codes;
SELECT user_id, operation_type, created_at FROM user_operations ORDER BY created_at DESC;
SELECT version, description, success FROM flyway_schema_history;
\d products
```

## Примеры запросов

```bash
B=http://localhost:8080

curl -s -X POST $B/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"seller@demo.local","password":"password123","role":"SELLER"}'
SELLER=$(curl -s -X POST $B/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"seller@demo.local","password":"password123"}' | jq -r .access_token)

curl -s -X POST $B/products -H "Authorization: Bearer $SELLER" -H 'Content-Type: application/json' \
  -d '{"name":"Phone","price":199.99,"stock":10,"category":"electronics","status":"ACTIVE"}'

curl -s "$B/products?status=ACTIVE&category=electronics&page=0&size=20" -H "Authorization: Bearer $SELLER"
```

Сценарии ошибок для защиты:

| Сценарий | Ожидаемый ответ |
|---|---|
| Без токена / с мусорным токеном | 401 `TOKEN_INVALID` |
| USER создаёт товар | 403 `ACCESS_DENIED` |
| SELLER меняет чужой товар | 403 `ACCESS_DENIED` |
| `price: 0`, `name: ""`, `stock: -1` | 400 `VALIDATION_ERROR` со списком полей |
| Несуществующий / неактивный товар в заказе | 404 `PRODUCT_NOT_FOUND` / 409 `PRODUCT_INACTIVE` |
| Количество больше остатка | 409 `INSUFFICIENT_STOCK`, в `details` requested и available |
| `quantity: 0` или `1000`, 51 позиция, `promo_code: "abc"` | 400 `VALIDATION_ERROR` |
| Повторный заказ в течение N минут | 429 `ORDER_LIMIT_EXCEEDED` |
| Второй заказ при активном (N истекло или `ORDER_RATE_LIMIT_MINUTES=0`) | 409 `ORDER_HAS_ACTIVE` |
| Истёкший / исчерпанный / неактивный промокод | 422 `PROMO_CODE_INVALID` |
| Сумма меньше `min_order_amount` | 422 `PROMO_CODE_MIN_AMOUNT` |
| Чужой заказ: GET / PUT / cancel | 403 `ORDER_OWNERSHIP_VIOLATION` |
| Отмена или обновление заказа в `PAID` | 409 `INVALID_STATE_TRANSITION` |

## Тесты

`make test` запускает 45 тестов против настоящего PostgreSQL с применённой миграцией: CRUD и фильтры, валидация каждого поля, все коды ошибок, расчёт скидок, обновление и отмена заказа с проверкой остатков и `current_uses`, модель состояний, лимит частоты, матрица ролей, ротация refresh-токенов и параллельные заказы на один товар.
