CREATE TABLE airlines (
    iata_code CHAR(2)      PRIMARY KEY CHECK (iata_code ~ '^[A-Z0-9]{2}$'),
    name      VARCHAR(100) NOT NULL CHECK (btrim(name) <> '')
);

CREATE TABLE airports (
    iata_code CHAR(3)      PRIMARY KEY CHECK (iata_code ~ '^[A-Z]{3}$'),
    name      VARCHAR(100) NOT NULL CHECK (btrim(name) <> ''),
    city      VARCHAR(100) NOT NULL CHECK (btrim(city) <> '')
);

CREATE TABLE flights (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    airline_code        CHAR(2)      NOT NULL REFERENCES airlines (iata_code),
    number              VARCHAR(4)   NOT NULL CHECK (number ~ '^[0-9]{1,4}$'),
    origin_code         CHAR(3)      NOT NULL REFERENCES airports (iata_code),
    destination_code    CHAR(3)      NOT NULL REFERENCES airports (iata_code),
    departure_time      TIMESTAMPTZ  NOT NULL,
    arrival_time        TIMESTAMPTZ  NOT NULL,
    total_seats         INTEGER      NOT NULL CHECK (total_seats > 0),
    available_seats     INTEGER      NOT NULL CHECK (available_seats >= 0),
    price_minor         BIGINT       NOT NULL CHECK (price_minor > 0),
    currency_code       CHAR(3)      NOT NULL DEFAULT 'RUB' CHECK (currency_code ~ '^[A-Z]{3}$'),
    status              VARCHAR(16)  NOT NULL DEFAULT 'SCHEDULED'
                                     CHECK (status IN ('SCHEDULED', 'DEPARTED', 'CANCELLED', 'COMPLETED')),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT flights_route_check CHECK (origin_code <> destination_code),
    CONSTRAINT flights_schedule_check CHECK (arrival_time > departure_time),
    CONSTRAINT flights_seats_check CHECK (available_seats <= total_seats)
);

-- One flight number per departure date (UTC): SU1234 on 2026-04-01 is a single flight.
CREATE UNIQUE INDEX flights_number_date_uq
    ON flights (airline_code, number, ((departure_time AT TIME ZONE 'UTC')::date));

CREATE INDEX flights_search_idx
    ON flights (origin_code, destination_code, ((departure_time AT TIME ZONE 'UTC')::date))
    WHERE status = 'SCHEDULED';

CREATE TABLE seat_reservations (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    flight_id   UUID         NOT NULL REFERENCES flights (id),
    booking_id  UUID         NOT NULL UNIQUE,
    seat_count  INTEGER      NOT NULL CHECK (seat_count > 0),
    status      VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE'
                             CHECK (status IN ('ACTIVE', 'RELEASED', 'EXPIRED')),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX seat_reservations_flight_idx ON seat_reservations (flight_id);
