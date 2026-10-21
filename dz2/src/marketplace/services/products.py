import uuid
from dataclasses import dataclass
from decimal import Decimal

import asyncpg

from marketplace.errors import ApiError
from marketplace.runtime import runtime
from marketplace_api.models.error_code import ErrorCode
from marketplace_api.models.extra_models import TokenModel

PRODUCT_COLUMNS = "id, name, description, price, stock, category, status, seller_id, created_at, updated_at"


@dataclass(frozen=True)
class ProductFields:
    name: str
    description: str | None
    price: Decimal
    stock: int
    category: str
    status: str


def product_not_found(product_id: uuid.UUID) -> ApiError:
    return ApiError(ErrorCode.PRODUCT_NOT_FOUND, "Product not found", {"product_id": str(product_id)})


async def create(principal: TokenModel, fields: ProductFields) -> asyncpg.Record:
    return await runtime.pool.fetchrow(
        f"""
        INSERT INTO products (name, description, price, stock, category, status, seller_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING {PRODUCT_COLUMNS}
        """,
        fields.name, fields.description, fields.price, fields.stock, fields.category, fields.status,
        uuid.UUID(principal.sub),
    )


async def get(product_id: uuid.UUID) -> asyncpg.Record:
    row = await runtime.pool.fetchrow(f"SELECT {PRODUCT_COLUMNS} FROM products WHERE id = $1", product_id)
    if row is None:
        raise product_not_found(product_id)
    return row


async def list_page(page: int, size: int, status: str | None, category: str | None) -> tuple[list[asyncpg.Record], int]:
    where = "($1::product_status IS NULL OR status = $1) AND ($2::varchar IS NULL OR category = $2)"
    async with runtime.pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM products WHERE {where}", status, category)
        rows = await conn.fetch(
            f"SELECT {PRODUCT_COLUMNS} FROM products WHERE {where} ORDER BY created_at DESC, id LIMIT $3 OFFSET $4",
            status, category, size, page * size,
        )
    return rows, total


async def _lock_owned(conn: asyncpg.Connection, principal: TokenModel, product_id: uuid.UUID) -> None:
    seller_id = await conn.fetchval("SELECT seller_id FROM products WHERE id = $1 FOR UPDATE", product_id)
    if seller_id is None:
        raise product_not_found(product_id)
    if principal.role != "ADMIN" and str(seller_id) != principal.sub:
        raise ApiError(ErrorCode.ACCESS_DENIED, "Sellers can manage only their own products")


async def update(principal: TokenModel, product_id: uuid.UUID, fields: ProductFields) -> asyncpg.Record:
    async with runtime.transaction() as conn:
        await _lock_owned(conn, principal, product_id)
        return await conn.fetchrow(
            f"""
            UPDATE products SET name = $2, description = $3, price = $4, stock = $5, category = $6, status = $7
            WHERE id = $1
            RETURNING {PRODUCT_COLUMNS}
            """,
            product_id, fields.name, fields.description, fields.price, fields.stock, fields.category, fields.status,
        )


async def archive(principal: TokenModel, product_id: uuid.UUID) -> asyncpg.Record:
    async with runtime.transaction() as conn:
        await _lock_owned(conn, principal, product_id)
        return await conn.fetchrow(
            f"UPDATE products SET status = 'ARCHIVED' WHERE id = $1 RETURNING {PRODUCT_COLUMNS}",
            product_id,
        )
