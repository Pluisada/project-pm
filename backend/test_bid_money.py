"""Part 22: exact reais <-> cents conversion for Bid revenue (bid_money.py)."""
from decimal import Decimal

import pytest

from bid_money import MAX_BID_REVENUE_CENTS, InvalidMoneyValue, cents_to_reais, reais_to_cents


@pytest.mark.parametrize("value, cents", [
    (None, None),
    (0, 0),
    ("0", 0),
    (Decimal("0.00"), 0),
    (1234, 123400),
    ("1234.5", 123450),
    ("1234.56", 123456),
    (Decimal("1234.56"), 123456),
    ("0.01", 1),
    ("10.500", 1050),           # trailing zeros are exact
    (" 99.90 ", 9990),
    ("1E3", 100000),
    ("9999999999999999.99", MAX_BID_REVENUE_CENTS),
])
def test_valid_values(value, cents):
    assert reais_to_cents(value) == cents


def test_large_value_is_exact_where_numeric_was_not():
    # NUMERIC(18, 2) in SQLite stored this as 12345678901234568.
    assert reais_to_cents("1234567890123456.78") == 123456789012345678
    assert cents_to_reais(123456789012345678) == Decimal("1234567890123456.78")


@pytest.mark.parametrize("value, message", [
    ("10.005", "more than two decimal places"),
    ("0.001", "more than two decimal places"),
    (Decimal("1.999"), "more than two decimal places"),
    ("1.0000000000000000000000000001", "more digits"),
    ("10000000000000000", "exceeds the maximum"),
    ("9999999999999999.991", "more than two decimal places"),
    ("-1", "negative"),
    ("-0.01", "negative"),
    ("NaN", "finite"),
    ("Infinity", "finite"),
    ("", "not a decimal number"),
    ("1.234,56", "not a decimal number"),
    ("R$ 10", "not a decimal number"),
    ("abc", "not a decimal number"),
])
def test_invalid_values_are_rejected_not_rounded(value, message):
    with pytest.raises(InvalidMoneyValue, match=message):
        reais_to_cents(value)


@pytest.mark.parametrize("value", [10.5, 0.1, True, [1], b"1"])
def test_non_decimal_types_are_rejected(value):
    with pytest.raises(InvalidMoneyValue, match="must be a Decimal"):
        reais_to_cents(value)


@pytest.mark.parametrize("cents, reais", [
    (None, None),
    (0, "0.00"),
    (1, "0.01"),
    (123450, "1234.50"),
    (MAX_BID_REVENUE_CENTS, "9999999999999999.99"),
])
def test_cents_to_reais(cents, reais):
    result = cents_to_reais(cents)
    assert result is None if reais is None else str(result) == reais


def test_round_trip():
    for text in ("0.00", "0.01", "1.10", "1234567.89", "9999999999999999.99"):
        assert str(cents_to_reais(reais_to_cents(text))) == text
