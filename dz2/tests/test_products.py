import uuid

from conftest import assert_error
from marketplace.runtime import runtime


async def test_crud_and_soft_delete(client, api):
    seller = await api.user("SELLER")
    created = await api.product(seller, name="Laptop", price=1234.56, stock=3, description=None)
    assert created["seller_id"] == seller["id"]
    assert created["description"] is None
    assert created["price"] == 1234.56
    assert created["created_at"] and created["updated_at"]

    fetched = await client.get(f"/products/{created['id']}", headers=seller["headers"])
    assert fetched.json() == created

    update = {"name": "Laptop Pro", "description": "16 inch", "price": 1500, "stock": 5,
              "category": created["category"], "status": "INACTIVE"}
    updated = await client.put(f"/products/{created['id']}", json=update, headers=seller["headers"])
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Laptop Pro" and updated.json()["status"] == "INACTIVE"
    assert updated.json()["updated_at"] > created["updated_at"]

    deleted = await client.delete(f"/products/{created['id']}", headers=seller["headers"])
    assert deleted.status_code == 200 and deleted.json()["status"] == "ARCHIVED"
    row = await runtime.pool.fetchrow("SELECT status FROM products WHERE id = $1", uuid.UUID(created["id"]))
    assert row["status"] == "ARCHIVED"


async def test_list_pagination_and_filters(client, api):
    seller = await api.user("SELLER")
    category = f"cat-{uuid.uuid4().hex[:8]}"
    for i in range(5):
        await api.product(seller, name=f"p{i}", category=category, status="ACTIVE" if i < 3 else "INACTIVE")

    page = await client.get("/products", params={"category": category, "page": 1, "size": 2}, headers=seller["headers"])
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["totalElements"] == 5 and body["page"] == 1 and body["size"] == 2 and len(body["items"]) == 2

    active = await client.get("/products", params={"category": category, "status": "ACTIVE"}, headers=seller["headers"])
    assert active.json()["totalElements"] == 3 and active.json()["size"] == 20
    assert {item["status"] for item in active.json()["items"]} == {"ACTIVE"}


async def test_list_rejects_bad_query(client, api):
    user = await api.user()
    response = await client.get("/products", params={"page": -1, "size": 0, "status": "SOLD"}, headers=user["headers"])
    body = assert_error(response, 400, "VALIDATION_ERROR")
    assert {f["field"] for f in body["details"]["fields"]} == {"page", "size", "status"}


async def test_create_validation_lists_every_field(client, api):
    seller = await api.user("SELLER")
    body = {"name": "", "description": "x" * 4001, "price": 0, "stock": -1, "category": "c" * 101, "status": "NEW"}
    response = await client.post("/products", json=body, headers=seller["headers"])
    error = assert_error(response, 400, "VALIDATION_ERROR")
    assert {f["field"] for f in error["details"]["fields"]} == {"name", "description", "price", "stock", "category", "status"}


async def test_create_rejects_missing_fields_and_bad_json(client, api):
    seller = await api.user("SELLER")
    missing = assert_error(await client.post("/products", json={"name": "x"}, headers=seller["headers"]), 400, "VALIDATION_ERROR")
    assert {f["field"] for f in missing["details"]["fields"]} == {"price", "stock", "category", "status"}
    malformed = await client.post("/products", content=b"{bad", headers={**seller["headers"], "Content-Type": "application/json"})
    assert_error(malformed, 400, "VALIDATION_ERROR")
    no_body = await client.post("/products", headers=seller["headers"])
    assert_error(no_body, 400, "VALIDATION_ERROR")


async def test_price_precision(client, api):
    seller = await api.user("SELLER")
    response = await client.post("/products", json={"name": "a", "price": 0.015, "stock": 1, "category": "c", "status": "ACTIVE"},
                                 headers=seller["headers"])
    assert_error(response, 400, "VALIDATION_ERROR")
    ok = await api.product(seller, price=0.01)
    assert ok["price"] == 0.01


async def test_not_found_and_bad_uuid(client, api):
    user = await api.user()
    assert_error(await client.get(f"/products/{uuid.uuid4()}", headers=user["headers"]), 404, "PRODUCT_NOT_FOUND")
    assert_error(await client.get("/products/not-a-uuid", headers=user["headers"]), 400, "VALIDATION_ERROR")


async def test_role_matrix(client, api):
    user, seller, other_seller, admin = await api.user(), await api.user("SELLER"), await api.user("SELLER"), await api.admin()
    product = await api.product(seller)
    update = {"name": "n", "price": 1, "stock": 1, "category": "c", "status": "ACTIVE"}

    assert_error(await client.post("/products", json=update, headers=user["headers"]), 403, "ACCESS_DENIED")
    assert_error(await client.put(f"/products/{product['id']}", json=update, headers=user["headers"]), 403, "ACCESS_DENIED")
    assert_error(await client.delete(f"/products/{product['id']}", headers=user["headers"]), 403, "ACCESS_DENIED")
    assert_error(await client.put(f"/products/{product['id']}", json=update, headers=other_seller["headers"]), 403, "ACCESS_DENIED")
    assert_error(await client.delete(f"/products/{product['id']}", headers=other_seller["headers"]), 403, "ACCESS_DENIED")

    assert (await client.get(f"/products/{product['id']}", headers=user["headers"])).status_code == 200
    admin_update = await client.put(f"/products/{product['id']}", json=update, headers=admin["headers"])
    assert admin_update.status_code == 200 and admin_update.json()["seller_id"] == seller["id"]
    assert (await client.post("/products", json=update, headers=admin["headers"])).status_code == 201


async def test_responses_carry_request_id(client, api):
    user = await api.user()
    ok = await client.get("/products", headers=user["headers"])
    failed = await client.get("/products")
    assert uuid.UUID(ok.headers["X-Request-Id"]) and uuid.UUID(failed.headers["X-Request-Id"])
    assert ok.headers["X-Request-Id"] != failed.headers["X-Request-Id"]
