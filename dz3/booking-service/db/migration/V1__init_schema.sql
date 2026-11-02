CREATE TABLE bookings (
    id                 UUID          PRIMARY KEY,
    user_id            VARCHAR(64)   NOT NULL CHECK (btrim(user_id) <> ''),
    flight_id          UUID          NOT NULL,
    passenger_name     VARCHAR(200)  NOT NULL CHECK (btrim(passenger_name) <> ''),
    passenger_email    VARCHAR(254)  NOT NULL CHECK (passenger_email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    seat_count         INTEGER       NOT NULL CHECK (seat_count > 0),
    total_price_minor  BIGINT        NOT NULL CHECK (total_price_minor > 0),
    currency_code      CHAR(3)       NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
    status             VARCHAR(16)   NOT NULL DEFAULT 'CONFIRMED'
                                     CHECK (status IN ('CONFIRMED', 'CANCELLED')),
    created_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX bookings_user_idx ON bookings (user_id, created_at DESC);

-- Reservations requested before the booking commit; leftovers of failed creations are released by the sweeper.
CREATE TABLE pending_reservations (
    booking_id  UUID         PRIMARY KEY,
    flight_id   UUID         NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX pending_reservations_created_idx ON pending_reservations (created_at);
