from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("9999999999.99")
MAX_PERCENTAGE_DISCOUNT = Decimal("70")


def money(value: float | int | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Promo:
    discount_type: str
    discount_value: Decimal
    min_order_amount: Decimal


def subtotal(lines: Iterable[tuple[Decimal, int]]) -> Decimal:
    return money(sum((price * quantity for price, quantity in lines), Decimal("0")))


def discount_for(amount: Decimal, promo: Promo) -> Decimal:
    if promo.discount_type == "PERCENTAGE":
        if promo.discount_value > MAX_PERCENTAGE_DISCOUNT:
            return Decimal("0.00")
        return money(amount * promo.discount_value / 100)
    return min(promo.discount_value, amount)
