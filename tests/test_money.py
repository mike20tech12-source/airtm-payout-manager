from decimal import Decimal

import pytest

from app.money import calculate, calculate_with_fees


def test_split_and_remainder():
    shares, remainder = calculate(
        "100.00",
        [(1, "30"), (2, "20"), (3, "10")],
    )

    assert shares == [
        (1, Decimal("30.00")),
        (2, Decimal("20.00")),
        (3, Decimal("10.00")),
    ]
    assert remainder == Decimal("40.00")


def test_rounding_preserves_remainder():
    shares, remainder = calculate(
        "10.00",
        [(1, "33.33"), (2, "33.33")],
    )

    assert sum(v for _, v in shares) + remainder == Decimal("10.00")


def test_full_calculation_with_flat_fee():
    shares, fees, business = calculate_with_fees(
        "250.00",
        [(1, "30"), (2, "20"), (3, "10")],
        [("service", "10.00")],
    )

    assert shares == [
        (1, Decimal("75.00")),
        (2, Decimal("50.00")),
        (3, Decimal("25.00")),
    ]
    assert fees == [
        ("service", Decimal("10.00")),
    ]
    assert business == Decimal("90.00")


def test_full_calculation_reconciles():
    shares, fees, business = calculate_with_fees(
        "73.42",
        [(1, "33.33"), (2, "33.33")],
        [("fee", "2.00")],
    )

    total = (
        sum(value for _, value in shares)
        + sum(value for _, value in fees)
        + business
    )

    assert total == Decimal("73.42")


def test_rejects_percentage_over_100():
    with pytest.raises(ValueError):
        calculate("100.00", [(1, "60"), (2, "41")])


def test_rejects_negative_percentage():
    with pytest.raises(ValueError):
        calculate("100.00", [(1, "-1")])


def test_rejects_negative_money():
    with pytest.raises(ValueError):
        calculate("-10.00", [(1, "50")])


def test_rejects_fees_exceeding_amount():
    with pytest.raises(ValueError):
        calculate_with_fees(
            "10.00",
            [(1, "50")],
            [("fee", "6.00")],
        )


def test_zero_percentage_is_valid():
    shares, remainder = calculate(
        "100.00",
        [(1, "0")],
    )

    assert shares == [(1, Decimal("0.00"))]
    assert remainder == Decimal("100.00")
