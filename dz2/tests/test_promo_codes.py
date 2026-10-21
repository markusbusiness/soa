import uuid

from conftest import assert_error


def promo_body(**overrides) -> dict:
    return {"code": "SUMMER_2026", "discount_type": "PERCENTAGE", "discount_value": 10, "max_uses": 10,
            "valid_from": "2026-01-01T00:00:00Z", "valid_until": "2026-12-31T23:59:59Z", **overrides}


async def test_create_by_seller_and_admin(api):
    seller, admin = await api.user("SELLER"), await api.admin()
    created = await api.promo(seller, min_order_amount=99.9)
    assert created["current_uses"] == 0 and created["active"] is True and created["min_order_amount"] == 99.9
    response = await api.client.post("/promo-codes", json=promo_body(code=f"D{uuid.uuid4().hex[:10].upper()}"), headers=admin["headers"])
    assert response.status_code == 201, response.text
    assert response.json()["min_order_amount"] == 0 and response.json()["active"] is True


async def test_user_cannot_create(client, api):
    user = await api.user()
    assert_error(await client.post("/promo-codes", json=promo_body(), headers=user["headers"]), 403, "ACCESS_DENIED")


async def test_duplicate_code(client, api):
    seller = await api.user("SELLER")
    promo = await api.promo(seller)
    response = await client.post("/promo-codes", json=promo_body(code=promo["code"]), headers=seller["headers"])
    assert_error(response, 409, "PROMO_CODE_ALREADY_EXISTS")


async def test_validation(client, api):
    seller = await api.user("SELLER")
    for body, field in [
        (promo_body(code="lower_case"), "code"),
        (promo_body(code="ABC"), "code"),
        (promo_body(discount_value=0), "discount_value"),
        (promo_body(discount_value=150), "discount_value"),
        (promo_body(max_uses=0), "max_uses"),
        (promo_body(valid_until="2025-01-01T00:00:00Z"), "valid_until"),
        (promo_body(discount_type="BOGUS"), "discount_type"),
    ]:
        error = assert_error(await client.post("/promo-codes", json=body, headers=seller["headers"]), 400, "VALIDATION_ERROR")
        assert [f["field"] for f in error["details"]["fields"]] == [field], error
