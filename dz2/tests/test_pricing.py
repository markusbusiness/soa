from decimal import Decimal

from marketplace.services.pricing import Promo, discount_for, money, subtotal


def test_subtotal_uses_exact_decimal_arithmetic():
    assert subtotal([(Decimal("0.10"), 3), (Decimal("199.99"), 2)]) == Decimal("400.28")


def test_percentage_discount_rounds_to_cents():
    assert discount_for(Decimal("33.33"), Promo("PERCENTAGE", Decimal("15"), Decimal("0"))) == Decimal("5.00")


def test_percentage_discount_up_to_70_is_applied():
    assert discount_for(Decimal("100.00"), Promo("PERCENTAGE", Decimal("70"), Decimal("0"))) == Decimal("70.00")


def test_percentage_discount_above_70_is_not_applied():
    assert discount_for(Decimal("100.00"), Promo("PERCENTAGE", Decimal("70.01"), Decimal("0"))) == Decimal("0.00")


def test_fixed_discount_is_capped_by_amount():
    assert discount_for(Decimal("40.00"), Promo("FIXED_AMOUNT", Decimal("50"), Decimal("0"))) == Decimal("40.00")
    assert discount_for(Decimal("80.00"), Promo("FIXED_AMOUNT", Decimal("50"), Decimal("0"))) == Decimal("50")


def test_money_from_json_float():
    assert money(19.99) == Decimal("19.99") and money(5) == Decimal("5.00")
