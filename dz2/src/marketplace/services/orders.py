import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

import asyncpg

from marketplace.errors import ApiError, validation_error
from marketplace.runtime import runtime
from marketplace.services.pricing import MAX_AMOUNT, Promo, discount_for, subtotal
from marketplace_api.models.error_code import ErrorCode
from marketplace_api.models.extra_models import TokenModel

CANCELABLE = {"CREATED", "PAYMENT_PENDING"}
NEXT_STATUS = {"CREATED": "PAYMENT_PENDING", "PAYMENT_PENDING": "PAID", "PAID": "SHIPPED", "SHIPPED": "COMPLETED"}


@dataclass(frozen=True)
class Item:
    product_id: uuid.UUID
    quantity: int


@dataclass(frozen=True)
class OrderView:
    order: asyncpg.Record
    items: list[asyncpg.Record]


async def get(principal: TokenModel, order_id: uuid.UUID) -> OrderView:
    async with runtime.pool.acquire() as conn:
        view = await _load(conn, order_id)
    if view is None:
        raise _order_not_found(order_id)
    _check_owner(principal, view.order)
    return view


async def create(principal: TokenModel, items: list[Item], promo_code: str | None) -> OrderView:
    user_id = uuid.UUID(principal.sub)
    async with runtime.transaction() as conn:
        now = await _lock_user(conn, user_id)
        await _check_rate_limit(conn, user_id, "CREATE_ORDER", now)

        active_order = await conn.fetchval(
            "SELECT id FROM orders WHERE user_id = $1 AND status IN ('CREATED', 'PAYMENT_PENDING')", user_id
        )
        if active_order is not None:
            raise ApiError(ErrorCode.ORDER_HAS_ACTIVE, "User already has an active order",
                           {"order_id": str(active_order)})

        products = await _lock_products(conn, (item.product_id for item in items))
        stock = {product_id: row["stock"] for product_id, row in products.items()}
        _check_catalog(items, products)
        _check_stock(items, stock)
        await _reserve(conn, items)

        amount = _amount(items, products)
        promo_id, discount = None, Decimal("0.00")
        if promo_code is not None:
            promo = await conn.fetchrow("SELECT * FROM promo_codes WHERE code = $1 FOR UPDATE", promo_code)
            if promo is None or not _promo_usable(promo, now) or promo["current_uses"] >= promo["max_uses"]:
                raise ApiError(ErrorCode.PROMO_CODE_INVALID, "Promo code is not found, expired, exhausted or inactive",
                               {"promo_code": promo_code})
            if amount < promo["min_order_amount"]:
                raise ApiError(ErrorCode.PROMO_CODE_MIN_AMOUNT, "Order amount is below the promo code minimum",
                               {"min_order_amount": float(promo["min_order_amount"]), "order_amount": float(amount)})
            discount = discount_for(amount, _promo(promo))
            await conn.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE id = $1", promo["id"])
            promo_id = promo["id"]

        order_id = await conn.fetchval(
            """
            INSERT INTO orders (user_id, status, promo_code_id, total_amount, discount_amount)
            VALUES ($1, 'CREATED', $2, $3, $4) RETURNING id
            """,
            user_id, promo_id, amount - discount, discount,
        )
        await _insert_items(conn, order_id, items, products)
        await conn.execute("INSERT INTO user_operations (user_id, operation_type) VALUES ($1, 'CREATE_ORDER')", user_id)
        return await _load(conn, order_id)


async def update(principal: TokenModel, order_id: uuid.UUID, items: list[Item]) -> OrderView:
    actor_id = uuid.UUID(principal.sub)
    async with runtime.transaction() as conn:
        now = await _lock_user(conn, actor_id)
        order = await _lock_order(conn, order_id)
        _check_owner(principal, order)
        if order["status"] != "CREATED":
            raise _invalid_transition(order["status"], "update")
        await _check_rate_limit(conn, actor_id, "UPDATE_ORDER", now)

        old_items = [Item(row["product_id"], row["quantity"]) for row in
                     await conn.fetch("SELECT product_id, quantity FROM order_items WHERE order_id = $1", order_id)]
        products = await _lock_products(conn, [i.product_id for i in old_items] + [i.product_id for i in items])
        stock = {product_id: row["stock"] for product_id, row in products.items()}

        await _release(conn, old_items)
        for item in old_items:
            stock[item.product_id] += item.quantity

        _check_catalog(items, products)
        _check_stock(items, stock)
        await _reserve(conn, items)

        amount = _amount(items, products)
        promo_id, discount = order["promo_code_id"], Decimal("0.00")
        if promo_id is not None:
            promo = await conn.fetchrow("SELECT * FROM promo_codes WHERE id = $1 FOR UPDATE", promo_id)
            if _promo_usable(promo, now) and amount >= promo["min_order_amount"]:
                discount = discount_for(amount, _promo(promo))
            else:
                await conn.execute("UPDATE promo_codes SET current_uses = current_uses - 1 WHERE id = $1", promo_id)
                promo_id = None

        await conn.execute("DELETE FROM order_items WHERE order_id = $1", order_id)
        await _insert_items(conn, order_id, items, products)
        await conn.execute(
            "UPDATE orders SET promo_code_id = $2, total_amount = $3, discount_amount = $4 WHERE id = $1",
            order_id, promo_id, amount - discount, discount,
        )
        await conn.execute("INSERT INTO user_operations (user_id, operation_type) VALUES ($1, 'UPDATE_ORDER')", actor_id)
        return await _load(conn, order_id)


async def cancel(principal: TokenModel, order_id: uuid.UUID) -> OrderView:
    async with runtime.transaction() as conn:
        order = await _lock_order(conn, order_id)
        _check_owner(principal, order)
        if order["status"] not in CANCELABLE:
            raise _invalid_transition(order["status"], "CANCELED")

        items = [Item(row["product_id"], row["quantity"]) for row in
                 await conn.fetch("SELECT product_id, quantity FROM order_items WHERE order_id = $1", order_id)]
        await _lock_products(conn, (item.product_id for item in items))
        await _release(conn, items)
        if order["promo_code_id"] is not None:
            await conn.execute("UPDATE promo_codes SET current_uses = current_uses - 1 WHERE id = $1",
                               order["promo_code_id"])
        await conn.execute("UPDATE orders SET status = 'CANCELED' WHERE id = $1", order_id)
        return await _load(conn, order_id)


async def change_status(order_id: uuid.UUID, target: str) -> OrderView:
    async with runtime.transaction() as conn:
        order = await _lock_order(conn, order_id)
        if NEXT_STATUS.get(order["status"]) != target:
            raise _invalid_transition(order["status"], target)
        await conn.execute("UPDATE orders SET status = $2 WHERE id = $1", order_id, target)
        return await _load(conn, order_id)


async def _lock_user(conn: asyncpg.Connection, user_id: uuid.UUID) -> datetime:
    now = await conn.fetchval("SELECT now() FROM users WHERE id = $1 FOR UPDATE", user_id)
    if now is None:
        raise ApiError(ErrorCode.TOKEN_INVALID, "Token subject does not exist")
    return now


async def _lock_order(conn: asyncpg.Connection, order_id: uuid.UUID) -> asyncpg.Record:
    order = await conn.fetchrow("SELECT * FROM orders WHERE id = $1 FOR UPDATE", order_id)
    if order is None:
        raise _order_not_found(order_id)
    return order


async def _lock_products(conn: asyncpg.Connection, product_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, asyncpg.Record]:
    rows = await conn.fetch(
        "SELECT id, price, stock, status FROM products WHERE id = ANY($1::uuid[]) ORDER BY id FOR UPDATE",
        sorted(set(product_ids)),
    )
    return {row["id"]: row for row in rows}


async def _check_rate_limit(conn: asyncpg.Connection, user_id: uuid.UUID, operation: str, now: datetime) -> None:
    last = await conn.fetchval(
        "SELECT max(created_at) FROM user_operations WHERE user_id = $1 AND operation_type = $2", user_id, operation
    )
    limit = runtime.settings.order_rate_limit
    if last is not None and now - last < limit:
        raise ApiError(ErrorCode.ORDER_LIMIT_EXCEEDED, "Too many order operations, retry later", {
            "operation_type": operation,
            "retry_after_seconds": math.ceil((limit - (now - last)).total_seconds()),
        })


def _check_owner(principal: TokenModel, order: asyncpg.Record) -> None:
    if principal.role != "ADMIN" and str(order["user_id"]) != principal.sub:
        raise ApiError(ErrorCode.ORDER_OWNERSHIP_VIOLATION, "Order belongs to another user")


def _check_catalog(items: list[Item], products: dict[uuid.UUID, asyncpg.Record]) -> None:
    missing = [str(item.product_id) for item in items if item.product_id not in products]
    if missing:
        raise ApiError(ErrorCode.PRODUCT_NOT_FOUND, "Some products do not exist", {"product_ids": missing})
    inactive = [str(item.product_id) for item in items if products[item.product_id]["status"] != "ACTIVE"]
    if inactive:
        raise ApiError(ErrorCode.PRODUCT_INACTIVE, "Some products are not available for ordering",
                       {"product_ids": inactive})


def _check_stock(items: list[Item], stock: dict[uuid.UUID, int]) -> None:
    shortages = [
        {"product_id": str(item.product_id), "requested": item.quantity, "available": stock[item.product_id]}
        for item in items if stock[item.product_id] < item.quantity
    ]
    if shortages:
        raise ApiError(ErrorCode.INSUFFICIENT_STOCK, "Insufficient stock for some products", {"items": shortages})


async def _reserve(conn: asyncpg.Connection, items: list[Item]) -> None:
    await conn.executemany("UPDATE products SET stock = stock - $2 WHERE id = $1",
                           [(item.product_id, item.quantity) for item in items])


async def _release(conn: asyncpg.Connection, items: list[Item]) -> None:
    await conn.executemany("UPDATE products SET stock = stock + $2 WHERE id = $1",
                           [(item.product_id, item.quantity) for item in items])


def _amount(items: list[Item], products: dict[uuid.UUID, asyncpg.Record]) -> Decimal:
    amount = subtotal((products[item.product_id]["price"], item.quantity) for item in items)
    if amount > MAX_AMOUNT:
        raise validation_error("items", f"Order total must not exceed {MAX_AMOUNT}")
    return amount


def _promo_usable(promo: asyncpg.Record, now: datetime) -> bool:
    return promo["active"] and promo["valid_from"] <= now <= promo["valid_until"]


def _promo(promo: asyncpg.Record) -> Promo:
    return Promo(promo["discount_type"], promo["discount_value"], promo["min_order_amount"])


async def _insert_items(conn: asyncpg.Connection, order_id: uuid.UUID, items: list[Item],
                        products: dict[uuid.UUID, asyncpg.Record]) -> None:
    await conn.executemany(
        "INSERT INTO order_items (order_id, product_id, quantity, price_at_order) VALUES ($1, $2, $3, $4)",
        [(order_id, item.product_id, item.quantity, products[item.product_id]["price"]) for item in items],
    )


async def _load(conn: asyncpg.Connection, order_id: uuid.UUID) -> OrderView | None:
    order = await conn.fetchrow(
        """
        SELECT o.*, p.code AS promo_code FROM orders o
        LEFT JOIN promo_codes p ON p.id = o.promo_code_id
        WHERE o.id = $1
        """,
        order_id,
    )
    if order is None:
        return None
    items = await conn.fetch(
        "SELECT id, product_id, quantity, price_at_order FROM order_items WHERE order_id = $1 ORDER BY product_id",
        order_id,
    )
    return OrderView(order, items)


def _order_not_found(order_id: uuid.UUID) -> ApiError:
    return ApiError(ErrorCode.ORDER_NOT_FOUND, "Order not found", {"order_id": str(order_id)})


def _invalid_transition(current: str, target: str) -> ApiError:
    return ApiError(ErrorCode.INVALID_STATE_TRANSITION, f"Transition from {current} to {target} is not allowed",
                    {"current_status": current, "target": target})
