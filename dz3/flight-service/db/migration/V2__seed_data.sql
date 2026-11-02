INSERT INTO airlines (iata_code, name) VALUES
    ('SU', 'Aeroflot'),
    ('S7', 'S7 Airlines'),
    ('DP', 'Pobeda'),
    ('U6', 'Ural Airlines');

INSERT INTO airports (iata_code, name, city) VALUES
    ('SVO', 'Sheremetyevo', 'Moscow'),
    ('VKO', 'Vnukovo', 'Moscow'),
    ('DME', 'Domodedovo', 'Moscow'),
    ('LED', 'Pulkovo', 'Saint Petersburg'),
    ('AER', 'Sochi', 'Sochi'),
    ('KZN', 'Kazan', 'Kazan'),
    ('SVX', 'Koltsovo', 'Yekaterinburg'),
    ('OVB', 'Tolmachevo', 'Novosibirsk');

INSERT INTO flights (id, airline_code, number, origin_code, destination_code,
                     departure_time, arrival_time, total_seats, available_seats, price_minor) VALUES
    ('11111111-1111-1111-1111-111111111101', 'SU', '1234', 'SVO', 'LED',
     '2026-12-01 07:00:00+00', '2026-12-01 08:30:00+00', 150, 150, 650000),
    ('11111111-1111-1111-1111-111111111102', 'SU', '1236', 'SVO', 'LED',
     '2026-12-01 15:00:00+00', '2026-12-01 16:30:00+00', 150, 150, 720000),
    ('11111111-1111-1111-1111-111111111103', 'DP', '201', 'VKO', 'LED',
     '2026-12-01 09:15:00+00', '2026-12-01 10:40:00+00', 189, 189, 349900),
    ('11111111-1111-1111-1111-111111111104', 'SU', '1234', 'SVO', 'LED',
     '2026-12-02 07:00:00+00', '2026-12-02 08:30:00+00', 150, 150, 610000),
    ('11111111-1111-1111-1111-111111111105', 'S7', '1021', 'DME', 'AER',
     '2026-12-05 06:00:00+00', '2026-12-05 09:55:00+00', 176, 176, 890000),
    ('11111111-1111-1111-1111-111111111106', 'U6', '263', 'SVX', 'OVB',
     '2026-12-10 18:20:00+00', '2026-12-10 21:30:00+00', 3, 3, 540000),
    ('11111111-1111-1111-1111-111111111107', 'S7', '55', 'OVB', 'KZN',
     '2026-12-12 04:00:00+00', '2026-12-12 07:45:00+00', 120, 120, 1150000);
