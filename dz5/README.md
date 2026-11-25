Pipeline аналитики онлайн-кинотеатра. Producer принимает события по HTTP и генерирует синтетический поток, публикует их в Kafka в формате Avro. ClickHouse читает топик через Kafka Engine и хранит сырые события. Aggregation Service считает дневные метрики в ClickHouse и выгружает их в PostgreSQL. Exporter складывает агрегаты за закрытые дни в S3 (MinIO), Grafana строит дашборд по агрегатам

Стек: Python 3.12, FastAPI, confluent-kafka (librdkafka) + Avro, Kafka 4 (Confluent Platform 8.2, KRaft), Schema Registry, ClickHouse 26.8, PostgreSQL 17 + Flyway, MinIO, Grafana 13 + grafana-clickhouse-datasource.

```
producer    HTTP / генератор ─▶ Kafka movie-events (3 брокера, Avro + Schema Registry)
ClickHouse  Kafka Engine ─▶ movie_events ─▶ user_first_view, user_activity_daily
            нераспознанное ─▶ movie_events_dlq ─▶ dlq-recovery ─▶ movie_events
aggregator  movie_events ─▶ agg_* (ClickHouse) ─▶ UPSERT в PostgreSQL
exporter    PostgreSQL ─▶ s3://movie-analytics/daily/YYYY-MM-DD/aggregates.json
Grafana     agg_* (ClickHouse)
```

## Запуск

Требования: Docker + Docker Compose, для скриптов `curl` и `jq`

```bash
docker compose up --build
```

Порядок старта: 3 брокера Kafka → Schema Registry → `kafka-init` (топик + регистрация схемы) → ClickHouse → `clickhouse-migrations` → PostgreSQL → Flyway → MinIO → `minio-init` (бакет) → producer, aggregator, exporter, dlq-recovery, Grafana. Ручных шагов нет: через ~1 минуту после старта в Grafana уже есть данные за 14 дней

| Сервис | Адрес |
|---|---|
| Producer | http://localhost:8010 (Swagger `/docs`) |
| Aggregator | http://localhost:8011 |
| Exporter | http://localhost:8012 |
| Grafana | http://localhost:3030/d/movie-analytics (admin/admin, анонимный просмотр включён) |
| MinIO Console | http://localhost:9001 (minio/minio-secret) |
| ClickHouse HTTP | http://localhost:8123 (analytics/analytics) |
| Schema Registry | http://localhost:8081 |

Порты меняются переменными `PRODUCER_PORT`, `AGGREGATOR_PORT`, `EXPORTER_PORT`, `GRAFANA_PORT`, `MINIO_PORT`, `MINIO_CONSOLE_PORT`, `CLICKHOUSE_HTTP_PORT`, `SCHEMA_REGISTRY_PORT`

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `GENERATOR_ENABLED` | true | Генератор событий в producer |
| `GENERATOR_NEW_USERS_PER_DAY` | 80 | Среднее число новых пользователей в день (±30%) |
| `GENERATOR_BACKFILL_DAYS` | 14 | За сколько дней до первого старта «запущен» сервис: эта история публикуется сразу |
| `KAFKA_DELIVERY_TIMEOUT_MS` | 10000 | `delivery.timeout.ms` одной попытки публикации |
| `PUBLISH_MAX_ATTEMPTS` / `PUBLISH_BACKOFF_BASE_MS` / `PUBLISH_BACKOFF_MAX_MS` | 4 / 200 / 5000 | Retry публикации |
| `AGGREGATION_INTERVAL_SECONDS` | 60 | Интервал расписания агрегации |
| `TOP_MOVIES_LIMIT` | 10 | Размер топа фильмов в PostgreSQL |
| `EXPORT_INTERVAL_SECONDS` | 300 | Интервал запуска экспорта |
| `DLQ_RECOVERY_INTERVAL_SECONDS` | 30 | Интервал разбора DLQ |

Остановить и удалить данные: `docker compose down -v`

## Команды

| Команда | Что делает |
|---|---|
| `make up` | `docker compose up --build` |
| `make test` | Поднимает отдельный стек `dz5-test` (генератор выключен, расписания по 5 с), гоняет pytest, удаляет стек |
| `make logs` | Логи producer, aggregator, exporter |
| `make psql` / `make clickhouse` | Консоль PostgreSQL / ClickHouse |
| `make demo` | `scripts/demo.sh`: событие через API, ошибка валидации, событие в ClickHouse, пересчёт, метрики, экспорт, когорты |
| `make fault-demo` | `scripts/fault_demo.sh`: остановка брокеров Kafka, Schema Registry и MinIO |

## Структура

```
schemas/movie_event.v1.avsc        Avro-схема события, версия 1
schemas/movie_event.v2.avsc        версия 2: + search_query
kafka/                             создание топика, регистрация схемы
clickhouse/migrations/             V001 сырые события, V002 активность пользователей,
                                   V003 таблицы агрегатов, V004 Kafka Engine + MV
clickhouse/migrate.sh              раннер миграций ClickHouse (schema_migrations + checksum)
postgres/migrations/               Flyway: таблицы агрегатов и состояния экспорта
producer/src/producer/
  main.py                          HTTP API, управление генератором
  models.py                        валидация входящего события
  publisher.py                     Kafka-продюсер, Avro, retry
  simulation.py                    модель пользователей и сессий, расписание событий
  generator.py                     публикация расписания: история пакетами, дальше в реальном времени
aggregator/src/aggregator/
  clickhouse_jobs.py               SQL метрик и когорт
  pg_store.py                      идемпотентная запись в PostgreSQL
  service.py                       циклы по расписанию и ручной пересчёт
exporter/src/exporter/export.py    PostgreSQL → S3
dlq-recovery/src/recovery/         разбор DLQ: восстановление событий из сырых байтов
grafana/                           datasource и дашборд (provisioning)
tests/                             pytest
scripts/                           demo.sh, fault_demo.sh
```

## Тз

**1. Топик и схема.** Схема в Avro: `event_id` (`string`/`uuid`), `timestamp` (`long`/`timestamp-millis`, UTC), `event_type` и `device_type` как enum, `progress_seconds` nullable (`["null","int"]`). Subject `movie-events-value` с совместимостью `BACKWARD` содержит две версии: v1 (`schemas/movie_event.v1.avsc`, обязательные поля из задания) и v2 (`schemas/movie_event.v2.avsc`, добавлено необязательное `search_query` с default `null`). `kafka-init` перед регистрацией каждой версии проверяет её совместимость через `/compatibility` и падает, если проверка не прошла. Producer пишет последней версией, consumer принимает сообщения обеих: ClickHouse берёт схему по id из заголовка сообщения, а для полей, которых нет в схеме писателя, подставляет `NULL` (`input_format_avro_allow_missing_fields=1`). Тест публикует сообщение, сериализованное v1, и проверяет его в ClickHouse. `kafka-init` создаёт `movie-events` с 6 партициями, RF=3 и `min.insync.replicas=2`.

Ключ партиционирования `user_id`. Все события пользователя (поиск, старт, паузы, финиш, лайк) лежат в одной партиции в порядке отправки, поэтому сессия читается консистентно, а метрики DAU и retention считаются по пользователю. Ключ `movie_id` дал бы перекос: популярный фильм собирает основную часть трафика в одну партицию, а события одной сессии пользователя на разных фильмах разъехались бы по партициям.

**2. Producer.** `POST /events` принимает JSON и проверяет его дважды: pydantic (типы, enum, UUID, timestamp обязательно с часовым поясом, `progress_seconds` ≥ 0 и обязателен для `VIEW_*`, `search_query` только у `SEARCHED`, лишние поля запрещены), затем `fastavro.validate` по схеме, загруженной из Schema Registry при старте. Ошибка → `422 VALIDATION_ERROR`. Успех → `202 {"event_id", "partition", "offset"}`; если `event_id` не передан, его генерирует сервис. Сериализация `AvroSerializer` с `auto.register.schemas=false`: producer пишет только зарегистрированной схемой.

Настройки продюсера: `acks=all`, `enable.idempotence=true` (librdkafka сам повторяет отправку без дублей и без нарушения порядка в партиции, `retry.backoff.ms` 100 → `retry.backoff.max.ms` 1000). Если сообщение не доставлено за `delivery.timeout.ms`, срабатывает прикладной retry: до 4 попыток с экспоненциальной задержкой 200 мс × 2ⁿ (потолок 5 с, jitter). Повторяются только временные ошибки (таймаут, нет лидера, `NOT_ENOUGH_REPLICAS`, недоступен Schema Registry). После исчерпания попыток `503 PUBLISH_FAILED`. Каждая публикация пишется в лог: `event published event_id=… event_type=… timestamp=… user_id=… partition=… offset=…`.

Генератор (`GENERATOR_ENABLED`, `GET /generator`, `POST /generator/start|stop`) строит сессии по сценарию: иногда `SEARCHED` → `VIEW_STARTED` (progress 0) → 0–3 пары `VIEW_PAUSED`/`VIEW_RESUMED` на растущем progress → `VIEW_FINISHED` с длительностью фильма (70%) и иногда `LIKED`, либо брошенный просмотр с финальной паузой. У `SEARCHED` есть текст запроса. Популярность фильмов по закону Ципфа, у пользователя предпочитаемое устройство. Время каждого события равно времени начала сессии плюс реальная длительность просмотра и пауз, поэтому фильм на 95 минут досматривается не раньше чем через 95 минут.

Поведение пользователей одно для истории и живого потока. Начиная с даты запуска каждый день приходит около `GENERATOR_NEW_USERS_PER_DAY` новых пользователей; у каждого своя «липкость» β(2, 3), и вероятность вернуться на N-й день жизни равна `липкость · 0.85^(N−1)`. Сессии начинаются между 07:00 и 22:00 UTC, у 30% активных пользователей их две за день. При дефолтах DAU растёт от ~90 в день запуска до ~300 к 14-му дню, это ~2000 событий в сутки. План каждого дня детерминирован (seed, дата запуска и день; `event_id` через uuid5), а событие публикуется, когда наступает его `timestamp`. При первом старте дата запуска ставится на `GENERATOR_BACKFILL_DAYS` дней назад, и всё прошедшее время публикуется пакетами; дальше генератор ждёт время следующего события. Дата запуска и момент, до которого всё опубликовано, лежат в `/state/generator.json` (volume `producer-state`): после рестарта генератор досылает пропущенный интервал, а если публикация не удалась, повторяет его целиком, и повторы отсекает дедупликация в ClickHouse.

**3. ClickHouse.** `movie_events_queue` (Kafka Engine, формат `AvroConfluent`: схему по id из сообщения берёт из Schema Registry) → `movie_events_mv` → `movie_events`. Колонки и типы совпадают со схемой: `UUID`, `Enum8` с теми же символами, `DateTime64(3, 'UTC')`, `Nullable(Int32)`; это проверяет тест. В `movie_events` добавлены `event_date`, `kafka_partition`, `kafka_offset`, `ingested_at`.

`movie_events` — `ReplacingMergeTree`, `PARTITION BY toYYYYMM(event_date)`, `ORDER BY (event_date, event_type, user_id, event_id)`. Все запросы агрегации фильтруют по дню и типу события, поэтому дата и тип стоят первыми. `event_id` в ключе даёт дедупликацию: Kafka Engine доставляет at-least-once, прикладной retry продюсера тоже может повторить событие, и запросы агрегации читают таблицу с `FINAL`. Skip-индекс minmax по `ingested_at` нужен агрегатору для поиска новых данных.

`kafka_handle_error_mode='stream'`: сообщение, которое не разобралось, уходит в `movie_events_dlq` с сырыми байтами и текстом ошибки и не блокирует потребление партиции. Туда же может попасть и валидное событие: если ClickHouse после перезапуска не достучался до Schema Registry, часть блоков ждёт, но часть сообщений уходит в DLQ с ошибкой `while fetching schema`. Такие события разбирает сервис `dlq-recovery`: раз в `DLQ_RECOVERY_INTERVAL_SECONDS` он берёт неразобранные сообщения, получает схему писателя по id из Schema Registry, декодирует Avro и вставляет событие в `movie_events` с исходными партицией и offset (повторная вставка отсекается дедупликацией по `event_id`). Итог пишется в `movie_events_dlq_resolution`: `recovered` или `invalid` (нет magic byte, неизвестный id схемы, Avro не декодируется). Если Schema Registry недоступен, сообщение остаётся в очереди до следующего запуска. Восстановленные события получают свежий `ingested_at`, и агрегатор пересчитывает их дни.

Из `movie_events` материализованные представления ведут две вспомогательные таблицы: `user_first_view` (дата первого `VIEW_STARTED` пользователя, `AggregatingMergeTree` с `min`) и `user_activity_daily` (пары день–пользователь). Retention считается по ним без полного скана сырых событий.

Миграции ClickHouse применяет `clickhouse/migrate.sh` при каждом старте: версии и checksum хранятся в `analytics.schema_migrations`, изменённая применённая миграция останавливает старт.

**4. Интеграционный тест.** `tests/test_pipeline.py` публикует событие через HTTP API продюсера, ждёт его в ClickHouse и сравнивает все поля, включая партицию из ответа продюсера. Отдельно проверяются `NULL` в `progress_seconds`, дедупликация повторной отправки одного `event_id` и порядок событий сессии. Тестовые события изолированы уникальными `user_id`/`movie_id`, а для агрегатов берётся случайный день в прошлом без событий в окне ±30 дней, поэтому тесты можно гонять многократно на одном стеке. `make test` поднимает чистый стек сам.

**5. Сервис агрегации.** Отдельный контейнер, читает только ClickHouse, пишет в PostgreSQL.

Расписание раз в `AGGREGATION_INTERVAL_SECONDS`. Цикл находит дни, в которые пришли новые события (`ingested_at` больше сохранённого в PostgreSQL watermark), и пересчитывает все дни с событиями от min(D)−7 до сегодня. События дня D меняют метрики D, ячейки когорт D−7…D, WAU и MAU следующих дней, а опоздавший первый просмотр переносит пользователя в когорту D из более поздней. В обычном режиме новые события приходят за сегодня, и это 8 дней. Watermark сдвигается только если все дни посчитались, иначе цикл повторит их. Первый цикл без watermark считает всю историю.

`POST /recompute?date=YYYY-MM-DD` пересчитывает один день сразу. `GET /metrics?date=` отдаёт готовые метрики из PostgreSQL.

Лог цикла:
```
aggregation cycle started trigger=schedule dates=15 range=2026-11-11..2026-11-25
date aggregated date=2026-11-24 events=914 rows_written=28 changed=True
aggregation cycle finished trigger=schedule dates=15 events_processed=7794 rows_written=482 failed_dates=none duration=1.39s
```

Метрики за день D считаются в ClickHouse (`INSERT … SELECT`) и материализуются в таблицах `agg_*` (`ReplacingMergeTree(computed_at)`):

| Таблица | Содержимое |
|---|---|
| `agg_retention_cohort` | когорта D (первый `VIEW_STARTED` в D): размер, вернувшиеся и доля для Day 0…7; ячейки в будущем не пишутся |
| `agg_daily_metrics` | `dau` (`uniqExact(user_id)`), `wau` и `mau` (уникальные пользователи за 7 и 30 дней по D включительно), `avg_view_seconds` (`avgIf(progress_seconds, VIEW_FINISHED)`), `view_conversion` (`countIf(FINISHED)/countIf(STARTED)`), `retention_d1`, `retention_d7`, `new_viewers`, `views_started`, `views_finished`, `sessions`, `likes`, `searches`, `events_total` |
| `agg_movie_daily` | просмотры, уникальные зрители, досмотры, лайки и место фильма (`row_number() OVER (ORDER BY views DESC, …)`) |
| `agg_device_daily` | пользователи и просмотры по устройствам |

«Вернулся в день N» означает любое событие пользователя в день D+N. Неизвестные пока значения (D7 для свежей когорты, конверсия без стартов) хранятся как `NULL`. Метрики дня считаются по `timestamp` событий, поэтому сессия, начатая до полуночи, даёт `VIEW_STARTED` в один день и `VIEW_FINISHED` в следующий, и конверсия отдельного дня может немного превышать 1.

PostgreSQL (`postgres/migrations/V1__aggregates.sql`): `daily_metrics (metric_date, metric_name, value, computed_at, updated_at)` с первичным ключом `(metric_date, metric_name)`, `top_movies`, `device_daily`, `retention_cohorts`, `aggregate_dates` (последний расчёт и последнее изменение данных за день), `aggregation_state` (watermark), `s3_exports`. Запись дня идёт одной транзакцией через `INSERT … ON CONFLICT DO UPDATE`: повторный пересчёт обновляет `computed_at`, а `updated_at` меняется только если изменилось значение. Лишние места топа удаляются. Чтение ClickHouse и запись в PostgreSQL повторяются до 5 раз с экспоненциальной задержкой, каждая неудача пишется в лог.

**6. Grafana.** Datasource ClickHouse (пользователь `grafana`, readonly) и дашборд `Online Cinema Analytics` подключаются через provisioning. Все панели читают только `agg_*`, это проверяет тест.

Retention Cohort Heatmap: таблица, строки — когорты, колонки — размер когорты и Day 0…Day 7 в процентах, ячейки окрашены градиентом от красного к зелёному, будущие дни пустые. Запрос панели разворачивает `agg_retention_cohort` через `maxIf(retention_rate, day_offset = N)`; когорты, первые даты и возвраты по дням уже посчитаны агрегатором. Дополнительные панели: DAU / WAU / MAU, конверсия, среднее время просмотра, топ-10 фильмов за период, просмотры по устройствам.

**7. Экспорт в S3.** Exporter — отдельный контейнер, читает только PostgreSQL. Раз в `EXPORT_INTERVAL_SECONDS` он выгружает каждый закрытый день (раньше сегодняшнего по UTC), у которого нет файла или данные изменились после прошлой выгрузки (`aggregate_dates.updated_at > s3_exports.source_updated_at`). Изменение возможно, когда в когорту дописывается новый Day N или приходят опоздавшие события.

Файл `s3://movie-analytics/daily/YYYY-MM-DD/aggregates.json` собирается из одного снимка (`REPEATABLE READ`): дата, `computed_at`, `exported_at`, все метрики дня с `computed_at`, топ фильмов, устройства, строка когорты. Повторная выгрузка перезаписывает тот же ключ. `POST /export?date=` выгружает любой день вручную (404, если агрегатов нет). Если PostgreSQL или S3 недоступен, ошибка пишется в лог, состояние в `s3_exports` не меняется, и следующий запуск выгрузит день снова.

MinIO запускается из образа `pgsty/minio`: MinIO перестал публиковать образы community-версии, `pgsty/minio` — поддерживаемый сообществом форк с тем же S3 API.

**8. Отказоустойчивость Kafka.** Три узла KRaft (broker + controller), `replication.factor=3`, `min.insync.replicas=2`, `auto.create.topics.enable=false`, служебные топики тоже с RF=3. Три контроллера нужны для кворума: при двух остановка одного узла лишает кластер контроллера. С `acks=all` запись подтверждается двумя репликами, поэтому остановка любого брокера не теряет подтверждённые события и не останавливает запись. Schema Registry хранит схемы в `_schemas` с RF=3. Health checks есть у всех долгоживущих контейнеров: брокеры (`kafka-broker-api-versions`), Schema Registry, ClickHouse, PostgreSQL, MinIO, Grafana и сервисы (`/health`; у producer он проверяет метаданные топика, у aggregator — ClickHouse и PostgreSQL, у dlq-recovery — ClickHouse).

`scripts/fault_demo.sh`:

| Сценарий | Результат |
|---|---|
| Остановлен `kafka-2` | его партиции получают лидеров на живых брокерах, ISR из 2 реплик, `POST /events` → 202 |
| Остановлены `kafka-2` и `kafka-3` | ISR = 1 < 2, продюсер ретраит, через ~43 с `503 PUBLISH_FAILED` |
| Брокеры запущены снова | ISR восстанавливается до 3, генератор и ClickHouse продолжают сами |
| Остановлен Schema Registry, перезапущен ClickHouse | ClickHouse не коммитит offset, отставание растёт; после возврата отставание дочитывается, сообщения, попавшие в DLQ, восстанавливает `dlq-recovery` (`unresolved = 0`) |
| Остановлен MinIO | `POST /export` → `503 EXPORT_FAILED`, ошибка в логе; после запуска экспорт проходит |

## Тесты

`make test` запускает 55 тестов. Модель генератора: детерминированность плана, время событий не раньше достигнутого прогресса, отсутствие событий из будущего и пропусков между интервалами, сессии через полночь, равномерный приток пользователей и стабильный DAU. Остальные идут против настоящих сервисов: кластер из 3 брокеров, партиции, RF и `min.insync.replicas` топика, версии 1 и 2 схемы и их совместимость, соответствие колонок ClickHouse схеме; валидация продюсера (11 видов ошибок), генерация `event_id`, перевод времени в UTC, партиционирование по `user_id`; путь события до ClickHouse со всеми полями, `NULL`, `search_query`, сообщение в схеме v1, дедупликация, порядок сессии; битое сообщение в DLQ без остановки потребления, восстановление валидного события из DLQ; метрики на наборе с заранее известным ответом (DAU/WAU/MAU 5, конверсия 0.75, среднее 2000 с, топ фильмов, устройства, когорта 100/50/0…0/50), совпадение ClickHouse и PostgreSQL, идемпотентность пересчёта, срабатывание расписания без ручного вызова, перенос пользователя в более раннюю когорту при опоздавшем первом просмотре; ручной и плановый экспорт, перезапись файла, 404; здоровье datasource Grafana, панели только по агрегатам, запрос heatmap через API Grafana.
