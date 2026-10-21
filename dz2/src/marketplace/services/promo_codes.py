from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import asyncpg

from marketplace.errors import ApiError
from marketplace.runtime import runtime
from marketplace_api.models.error_code import ErrorCode


@dataclass(frozen=True)
class PromoCodeFields:
    code: str
    discount_type: str
    discount_value: Decimal
    min_order_amount: Decimal
    max_uses: int
    valid_from: datetime
    valid_until: datetime
    active: bool


async def create(fields: PromoCodeFields) -> asyncpg.Record:
    try:
        return await runtime.pool.fetchrow(
            """
            INSERT INTO promo_codes
                (code, discount_type, discount_value, min_order_amount, max_uses, valid_from, valid_until, active)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING *
            """,
            fields.code, fields.discount_type, fields.discount_value, fields.min_order_amount, fields.max_uses,
            fields.valid_from, fields.valid_until, fields.active,
        )
    except asyncpg.UniqueViolationError:
        raise ApiError(ErrorCode.PROMO_CODE_ALREADY_EXISTS, "Promo code already exists", {"code": fields.code})
