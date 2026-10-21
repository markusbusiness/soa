from datetime import datetime, timezone

from marketplace.errors import validation_error
from marketplace.services import promo_codes
from marketplace.services.pricing import money
from marketplace_api.apis.promo_codes_api_base import BasePromoCodesApi
from marketplace_api.models.discount_type import DiscountType
from marketplace_api.models.extra_models import TokenModel
from marketplace_api.models.promo_code_create import PromoCodeCreate
from marketplace_api.models.promo_code_response import PromoCodeResponse


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class PromoCodesHandler(BasePromoCodesApi):
    async def create_promo_code(self, token_bearerAuth: TokenModel, promo_code_create: PromoCodeCreate) -> PromoCodeResponse:
        body = promo_code_create
        valid_from, valid_until = _aware(body.valid_from), _aware(body.valid_until)
        if valid_until <= valid_from:
            raise validation_error("valid_until", "valid_until must be later than valid_from")
        if body.discount_type == DiscountType.PERCENTAGE and body.discount_value > 100:
            raise validation_error("discount_value", "Percentage discount must not exceed 100")

        row = await promo_codes.create(promo_codes.PromoCodeFields(
            code=body.code,
            discount_type=body.discount_type.value,
            discount_value=money(body.discount_value),
            min_order_amount=money(body.min_order_amount or 0),
            max_uses=body.max_uses,
            valid_from=valid_from,
            valid_until=valid_until,
            active=body.active if body.active is not None else True,
        ))
        return PromoCodeResponse(
            id=row["id"],
            code=row["code"],
            discount_type=row["discount_type"],
            discount_value=float(row["discount_value"]),
            min_order_amount=float(row["min_order_amount"]),
            max_uses=row["max_uses"],
            current_uses=row["current_uses"],
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            active=row["active"],
        )
