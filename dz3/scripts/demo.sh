#!/usr/bin/env bash
# Happy path and business errors through the Booking Service REST API. Needs curl and jq.
set -euo pipefail

BASE_URL=${BASE_URL:-http://localhost:8000}
FLIGHT=11111111-1111-1111-1111-111111111106

step() { printf '\n=== %s\n' "$*"; }
call() { curl -s -w '\nHTTP %{http_code}\n' "$@"; }

step "Search SVX -> OVB on 2026-12-10"
curl -s "$BASE_URL/flights?origin=SVX&destination=OVB&date=2026-12-10" | jq '.flights[] | {id, flight_number, available_seats, price}'

step "Get flight"
curl -s "$BASE_URL/flights/$FLIGHT" | jq '{flight_number, total_seats, available_seats, status}'

step "Book 2 seats"
BOOKING=$(curl -s -X POST "$BASE_URL/bookings" -H 'content-type: application/json' -d "{
  \"user_id\": \"demo-user\", \"flight_id\": \"$FLIGHT\",
  \"passenger_name\": \"Ivan Petrov\", \"passenger_email\": \"ivan@example.com\", \"seat_count\": 2}")
echo "$BOOKING" | jq .
BOOKING_ID=$(echo "$BOOKING" | jq -r .id)

step "Available seats after booking"
curl -s "$BASE_URL/flights/$FLIGHT" | jq .available_seats

step "Book 5 more seats: not enough seats -> 409"
call -X POST "$BASE_URL/bookings" -H 'content-type: application/json' -d "{
  \"user_id\": \"demo-user\", \"flight_id\": \"$FLIGHT\",
  \"passenger_name\": \"Anna Petrova\", \"passenger_email\": \"anna@example.com\", \"seat_count\": 5}"

step "Unknown flight -> 404"
call "$BASE_URL/flights/00000000-0000-0000-0000-000000000000"

step "Bookings of demo-user"
curl -s "$BASE_URL/bookings?user_id=demo-user" | jq '.bookings[] | {id, seat_count, total_price, status}'

step "Cancel booking"
curl -s -X POST "$BASE_URL/bookings/$BOOKING_ID/cancel" | jq '{id, status}'

step "Cancel again -> 409"
call -X POST "$BASE_URL/bookings/$BOOKING_ID/cancel"

step "Available seats after cancel"
curl -s "$BASE_URL/flights/$FLIGHT" | jq .available_seats
