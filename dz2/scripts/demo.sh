#!/usr/bin/env bash
# E2E-сценарий через HTTP. Требует curl и jq, стек должен быть поднят (docker compose up).
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@marketplace.local}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin12345}"
SUFFIX="$(date +%s)"

step() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }

call() {
  local method="$1" path="$2" token="${3:-}" body="${4:-}"
  local args=(-s -X "$method" "$BASE_URL$path" -H "Content-Type: application/json" -w '\n%{http_code}')
  [[ -n "$token" ]] && args+=(-H "Authorization: Bearer $token")
  [[ -n "$body" ]] && args+=(-d "$body")
  local response
  response="$(curl "${args[@]}")"
  STATUS="${response##*$'\n'}"
  BODY="${response%$'\n'*}"
  echo "$method $path -> $STATUS"
  echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
}

login() {
  call POST /auth/login "" "{\"email\":\"$1\",\"password\":\"$2\"}" >/dev/null
  echo "$BODY" | jq -r .access_token
}

step "Регистрация продавца и покупателя"
call POST /auth/register "" "{\"email\":\"seller-$SUFFIX@demo.local\",\"password\":\"password123\",\"role\":\"SELLER\"}"
call POST /auth/register "" "{\"email\":\"buyer-$SUFFIX@demo.local\",\"password\":\"password123\"}"
SELLER="$(login "seller-$SUFFIX@demo.local" password123)"
BUYER="$(login "buyer-$SUFFIX@demo.local" password123)"
ADMIN="$(login "$ADMIN_EMAIL" "$ADMIN_PASSWORD")"

step "Продавец создаёт товары"
call POST /products "$SELLER" '{"name":"Phone","description":"Smartphone","price":199.99,"stock":10,"category":"electronics","status":"ACTIVE"}'
PHONE="$(echo "$BODY" | jq -r .id)"
call POST /products "$SELLER" '{"name":"Case","price":15.50,"stock":3,"category":"accessories","status":"ACTIVE"}'
CASE="$(echo "$BODY" | jq -r .id)"

step "Список товаров с фильтром и пагинацией"
call GET "/products?category=electronics&status=ACTIVE&page=0&size=5" "$BUYER"

step "Промокод на 10%"
call POST /promo-codes "$SELLER" "{\"code\":\"DEMO$SUFFIX\",\"discount_type\":\"PERCENTAGE\",\"discount_value\":10,\"min_order_amount\":100,\"max_uses\":100,\"valid_from\":\"2020-01-01T00:00:00Z\",\"valid_until\":\"2099-01-01T00:00:00Z\"}"

step "Покупатель создаёт заказ с промокодом"
call POST /orders "$BUYER" "{\"items\":[{\"product_id\":\"$PHONE\",\"quantity\":2},{\"product_id\":\"$CASE\",\"quantity\":1}],\"promo_code\":\"DEMO$SUFFIX\"}"
ORDER="$(echo "$BODY" | jq -r .id)"

step "Остатки после резервирования"
call GET "/products/$PHONE" "$BUYER"

step "Повторный заказ сразу: первым срабатывает лимит частоты (ORDER_RATE_LIMIT_MINUTES)"
call POST /orders "$BUYER" "{\"items\":[{\"product_id\":\"$CASE\",\"quantity\":1}]}"

step "Ошибки: валидация, нехватка остатка, роль"
call POST /products "$SELLER" '{"name":"","price":0,"stock":-1,"category":"x","status":"NEW"}'
call PUT "/orders/$ORDER" "$BUYER" "{\"items\":[{\"product_id\":\"$CASE\",\"quantity\":50}]}"
call POST /products "$BUYER" '{"name":"x","price":1,"stock":1,"category":"x","status":"ACTIVE"}'

step "Админ двигает заказ по статусам, отмена из PAID запрещена"
call POST "/orders/$ORDER/status" "$ADMIN" '{"status":"PAYMENT_PENDING"}'
call POST "/orders/$ORDER/status" "$ADMIN" '{"status":"PAID"}'
call POST "/orders/$ORDER/cancel" "$BUYER"

step "Мягкое удаление товара"
call DELETE "/products/$CASE" "$SELLER"

printf '\nORDER_ID=%s\nPHONE_ID=%s\nCASE_ID=%s\n' "$ORDER" "$PHONE" "$CASE"
