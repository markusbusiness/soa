import asyncio
import uuid

from conftest import assert_error
from marketplace.runtime import runtime


async def stock_of(product: dict) -> int:
    return await runtime.pool.fetchval("SELECT stock FROM products WHERE id = $1", uuid.UUID(product["id"]))


async def uses_of(promo: dict) -> int:
    return await runtime.pool.fetchval("SELECT current_uses FROM promo_codes WHERE id = $1", uuid.UUID(promo["id"]))


async def test_create_reserves_stock_and_snapshots_price(client, api):
    seller, buyer = await api.user("SELLER"), await api.user()
    phone, case = await api.product(seller, price=199.99, stock=10), await api.product(seller, price=5.5, stock=3)

    response = await api.order(buyer, [(phone, 2), (case, 3)])
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["status"] == "CREATED" and order["user_id"] == buyer["id"]
    assert order["total_amount"] == 416.48 and order["discount_amount"] == 0
    assert order["promo_code_id"] is None
    assert await stock_of(phone) == 8 and await stock_of(case) == 0

    reprice = {"name": "Phone", "price": 1, "stock": 8, "category": phone["category"], "status": "ACTIVE"}
    assert (await client.put(f"/products/{phone['id']}", json=reprice, headers=seller["headers"])).status_code == 200
    fetched = (await client.get(f"/orders/{order['id']}", headers=buyer["headers"])).json()
    assert fetched["total_amount"] == 416.48
    assert {i["product_id"]: i["price_at_order"] for i in fetched["items"]}[phone["id"]] == 199.99

    operations = await runtime.pool.fetchval(
        "SELECT count(*) FROM user_operations WHERE user_id = $1 AND operation_type = 'CREATE_ORDER'", uuid.UUID(buyer["id"]))
    assert operations == 1


async def test_second_active_order_rejected(api):
    seller, buyer = await api.user("SELLER"), await api.user()
    product = await api.product(seller)
    assert (await api.order(buyer, [(product, 1)])).status_code == 201
    assert_error(await api.order(buyer, [(product, 1)]), 409, "ORDER_HAS_ACTIVE")


async def test_catalog_checks(client, api):
    seller, buyer = await api.user("SELLER"), await api.user()
    inactive = await api.product(seller, status="INACTIVE")
    archived = await api.product(seller)
    await client.delete(f"/products/{archived['id']}", headers=seller["headers"])
    missing = {"id": str(uuid.uuid4())}

    not_found = assert_error(await api.order(buyer, [(inactive, 1), (missing, 1)]), 404, "PRODUCT_NOT_FOUND")
    assert not_found["details"]["product_ids"] == [missing["id"]]
    assert_error(await api.order(buyer, [(inactive, 1)]), 409, "PRODUCT_INACTIVE")
    assert_error(await api.order(buyer, [(archived, 1)]), 409, "PRODUCT_INACTIVE")


async def test_insufficient_stock_lists_every_shortage_and_changes_nothing(api):
    seller, buyer = await api.user("SELLER"), await api.user()
    a, b, c = await api.product(seller, stock=1), await api.product(seller, stock=2), await api.product(seller, stock=50)

    body = assert_error(await api.order(buyer, [(a, 5), (b, 3), (c, 1)]), 409, "INSUFFICIENT_STOCK")
    assert body["details"]["items"] == [
        {"product_id": a["id"], "requested": 5, "available": 1},
        {"product_id": b["id"], "requested": 3, "available": 2},
    ]
    assert (await stock_of(a), await stock_of(b), await stock_of(c)) == (1, 2, 50)


async def test_order_validation(client, api):
    seller, buyer = await api.user("SELLER"), await api.user()
    product = await api.product(seller)
    cases = [
        {"items": []},
        {"items": [{"product_id": str(uuid.uuid4()), "quantity": 1}] * 51},
        {"items": [{"product_id": product["id"], "quantity": 0}]},
        {"items": [{"product_id": product["id"], "quantity": 1000}]},
        {"items": [{"product_id": product["id"], "quantity": 1}], "promo_code": "bad-code"},
        {"items": [{"product_id": product["id"], "quantity": 1}], "promo_code": "ABC"},
        {"items": [{"product_id": "nope", "quantity": 1}]},
        {"items": [{"product_id": product["id"], "quantity": 1}, {"product_id": product["id"], "quantity": 2}]},
    ]
    for body in cases:
        assert_error(await client.post("/orders", json=body, headers=buyer["headers"]), 400, "VALIDATION_ERROR")
    assert await stock_of(product) == 10


async def test_percentage_and_fixed_promo(api):
    seller = await api.user("SELLER")
    product = await api.product(seller, price=100, stock=100)
    percent = await api.promo(seller, discount_type="PERCENTAGE", discount_value=15)
    fixed = await api.promo(seller, discount_type="FIXED_AMOUNT", discount_value=500)

    first = (await api.order(await api.user(), [(product, 2)], percent["code"])).json()
    assert (first["total_amount"], first["discount_amount"], first["promo_code"]) == (170, 30, percent["code"])
    second = (await api.order(await api.user(), [(product, 3)], fixed["code"])).json()
    assert (second["total_amount"], second["discount_amount"]) == (0, 300)
    assert await uses_of(percent) == 1 and await uses_of(fixed) == 1


async def test_percentage_above_70_gives_full_price(api):
    seller = await api.user("SELLER")
    product = await api.product(seller, price=100)
    promo = await api.promo(seller, discount_type="PERCENTAGE", discount_value=80)
    order = (await api.order(await api.user(), [(product, 1)], promo["code"])).json()
    assert (order["total_amount"], order["discount_amount"]) == (100, 0)


async def test_invalid_promo_codes(client, api):
    seller, admin = await api.user("SELLER"), await api.admin()
    product = await api.product(seller, price=100)
    expired = await api.promo(seller, valid_from="2020-01-01T00:00:00Z", valid_until="2021-01-01T00:00:00Z")
    future = await api.promo(seller, valid_from="2098-01-01T00:00:00Z")
    inactive = await api.promo(seller, active=False)
    exhausted = await api.promo(admin, max_uses=1)
    assert (await api.order(await api.user(), [(product, 1)], exhausted["code"])).status_code == 201

    buyer = await api.user()
    for code in ("NOSUCHCODE", expired["code"], future["code"], inactive["code"], exhausted["code"]):
        assert_error(await api.order(buyer, [(product, 1)], code), 422, "PROMO_CODE_INVALID")

    min_amount = await api.promo(seller, min_order_amount=1000)
    body = assert_error(await api.order(buyer, [(product, 2)], min_amount["code"]), 422, "PROMO_CODE_MIN_AMOUNT")
    assert body["details"] == {"min_order_amount": 1000, "order_amount": 200}
    assert await stock_of(product) == 9 and await uses_of(min_amount) == 0


async def test_update_returns_stock_and_recalculates(client, api):
    seller, buyer = await api.user("SELLER"), await api.user()
    a, b = await api.product(seller, price=300, stock=10), await api.product(seller, price=50, stock=10)
    promo = await api.promo(seller, discount_type="FIXED_AMOUNT", discount_value=100, min_order_amount=500)
    order = (await api.order(buyer, [(a, 2)], promo["code"])).json()
    assert (order["total_amount"], order["discount_amount"]) == (500, 100)

    update = await client.put(f"/orders/{order['id']}", json={"items": [{"product_id": a["id"], "quantity": 3},
                                                                        {"product_id": b["id"], "quantity": 4}]},
                              headers=buyer["headers"])
    assert update.status_code == 200, update.text
    assert (update.json()["total_amount"], update.json()["discount_amount"]) == (1000, 100)
    assert await stock_of(a) == 7 and await stock_of(b) == 6

    below_min = await client.put(f"/orders/{order['id']}", json={"items": [{"product_id": b["id"], "quantity": 1}]},
                                 headers=buyer["headers"])
    body = below_min.json()
    assert (body["total_amount"], body["discount_amount"], body["promo_code_id"]) == (50, 0, None)
    assert await stock_of(a) == 10 and await stock_of(b) == 9 and await uses_of(promo) == 0


async def test_failed_update_changes_nothing(client, api):
    seller, buyer = await api.user("SELLER"), await api.user()
    a, b = await api.product(seller, stock=5), await api.product(seller, stock=1)
    order = (await api.order(buyer, [(a, 5)])).json()
    response = await client.put(f"/orders/{order['id']}", json={"items": [{"product_id": a["id"], "quantity": 5},
                                                                          {"product_id": b["id"], "quantity": 2}]},
                                headers=buyer["headers"])
    body = assert_error(response, 409, "INSUFFICIENT_STOCK")
    assert body["details"]["items"] == [{"product_id": b["id"], "requested": 2, "available": 1}]
    assert await stock_of(a) == 0 and await stock_of(b) == 1


async def test_update_and_cancel_ownership(client, api):
    seller, owner, stranger = await api.user("SELLER"), await api.user(), await api.user()
    product = await api.product(seller)
    order = (await api.order(owner, [(product, 1)])).json()
    items = {"items": [{"product_id": product["id"], "quantity": 2}]}

    assert_error(await client.get(f"/orders/{order['id']}", headers=stranger["headers"]), 403, "ORDER_OWNERSHIP_VIOLATION")
    assert_error(await client.put(f"/orders/{order['id']}", json=items, headers=stranger["headers"]), 403,
                 "ORDER_OWNERSHIP_VIOLATION")
    assert_error(await client.post(f"/orders/{order['id']}/cancel", headers=stranger["headers"]), 403,
                 "ORDER_OWNERSHIP_VIOLATION")
    assert_error(await client.get(f"/orders/{uuid.uuid4()}", headers=owner["headers"]), 404, "ORDER_NOT_FOUND")
    assert_error(await client.get(f"/orders/{order['id']}", headers=seller["headers"]), 403, "ACCESS_DENIED")
    assert_error(await api.order(seller, [(product, 1)]), 403, "ACCESS_DENIED")


async def test_cancel_returns_stock_and_promo_use(client, api):
    seller, buyer = await api.user("SELLER"), await api.user()
    product = await api.product(seller, stock=4)
    promo = await api.promo(seller)
    order = (await api.order(buyer, [(product, 3)], promo["code"])).json()
    assert await stock_of(product) == 1 and await uses_of(promo) == 1

    canceled = await client.post(f"/orders/{order['id']}/cancel", headers=buyer["headers"])
    assert canceled.status_code == 200 and canceled.json()["status"] == "CANCELED"
    assert await stock_of(product) == 4 and await uses_of(promo) == 0
    assert_error(await client.post(f"/orders/{order['id']}/cancel", headers=buyer["headers"]), 409, "INVALID_STATE_TRANSITION")
    assert (await api.order(buyer, [(product, 1)])).status_code == 201


async def test_state_machine(client, api):
    seller, buyer, admin = await api.user("SELLER"), await api.user(), await api.admin()
    product = await api.product(seller)
    order = (await api.order(buyer, [(product, 1)])).json()
    url = f"/orders/{order['id']}"

    async def move(status: str):
        return await client.post(f"{url}/status", json={"status": status}, headers=admin["headers"])

    assert_error(await client.post(f"{url}/status", json={"status": "PAYMENT_PENDING"}, headers=buyer["headers"]), 403,
                 "ACCESS_DENIED")
    assert_error(await move("PAID"), 409, "INVALID_STATE_TRANSITION")
    assert (await move("PAYMENT_PENDING")).json()["status"] == "PAYMENT_PENDING"
    assert_error(await client.put(url, json={"items": [{"product_id": product["id"], "quantity": 1}]},
                                  headers=buyer["headers"]), 409, "INVALID_STATE_TRANSITION")
    assert (await move("PAID")).json()["status"] == "PAID"
    assert_error(await client.post(f"{url}/cancel", headers=buyer["headers"]), 409, "INVALID_STATE_TRANSITION")
    assert_error(await move("CANCELED"), 409, "INVALID_STATE_TRANSITION")
    assert (await move("SHIPPED")).json()["status"] == "SHIPPED"
    assert (await move("COMPLETED")).json()["status"] == "COMPLETED"
    assert_error(await move("CREATED"), 409, "INVALID_STATE_TRANSITION")


async def test_admin_manages_any_order(client, api):
    seller, buyer, admin = await api.user("SELLER"), await api.user(), await api.admin()
    product = await api.product(seller, stock=5)
    order = (await api.order(buyer, [(product, 1)])).json()

    assert (await client.get(f"/orders/{order['id']}", headers=admin["headers"])).status_code == 200
    updated = await client.put(f"/orders/{order['id']}", json={"items": [{"product_id": product["id"], "quantity": 2}]},
                               headers=admin["headers"])
    assert updated.status_code == 200 and updated.json()["user_id"] == buyer["id"]
    assert (await client.post(f"/orders/{order['id']}/cancel", headers=admin["headers"])).json()["status"] == "CANCELED"
    assert await stock_of(product) == 5


async def test_rate_limit_on_create_and_update(client, api, rate_limit):
    seller, buyer = await api.user("SELLER"), await api.user()
    product = await api.product(seller)
    rate_limit(5)

    order = (await api.order(buyer, [(product, 1)])).json()
    await client.post(f"/orders/{order['id']}/cancel", headers=buyer["headers"])
    limited = await api.order(buyer, [(product, 1)])
    body = assert_error(limited, 429, "ORDER_LIMIT_EXCEEDED")
    assert body["details"]["operation_type"] == "CREATE_ORDER" and 0 < body["details"]["retry_after_seconds"] <= 300
    assert limited.headers["Retry-After"] == str(body["details"]["retry_after_seconds"])

    await runtime.pool.execute("UPDATE user_operations SET created_at = created_at - interval '6 minutes' WHERE user_id = $1",
                               uuid.UUID(buyer["id"]))
    order = (await api.order(buyer, [(product, 1)])).json()
    items = {"items": [{"product_id": product["id"], "quantity": 2}]}
    assert (await client.put(f"/orders/{order['id']}", json=items, headers=buyer["headers"])).status_code == 200
    second = await client.put(f"/orders/{order['id']}", json=items, headers=buyer["headers"])
    assert assert_error(second, 429, "ORDER_LIMIT_EXCEEDED")["details"]["operation_type"] == "UPDATE_ORDER"


async def test_concurrent_orders_never_oversell(api):
    seller = await api.user("SELLER")
    product = await api.product(seller, stock=5)
    buyers = [await api.user() for _ in range(12)]

    responses = await asyncio.gather(*(api.order(buyer, [(product, 1)]) for buyer in buyers))
    statuses = sorted(response.status_code for response in responses)
    assert statuses == [201] * 5 + [409] * 7
    assert {r.json()["error_code"] for r in responses if r.status_code == 409} == {"INSUFFICIENT_STOCK"}
    assert await stock_of(product) == 0
