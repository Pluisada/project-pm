"""Exact conversion of Salcomp Bid revenue between reais and stored cents.

card_bid_details keeps annual revenue as an INTEGER number of cents: SQLite
has no exact decimal type (a NUMERIC column silently rounds past ~15
significant digits), while a 64-bit integer is exact. Convert with these
helpers only - never through float - and turn cents back into reais only for
display.

None means "not informed" and stays None in both directions; it is never
stored as 0 (a real zero revenue is 0 cents).
"""
from decimal import Decimal, Inexact, InvalidOperation, localcontext

# 16 integer digits plus 2 decimal places, well inside SQLite's signed 64-bit
# INTEGER (max 9223372036854775807).
MAX_BID_REVENUE_CENTS = 10**18 - 1


class InvalidMoneyValue(ValueError):
    """A revenue value that can't be stored exactly as cents. `reason` is the
    message without the value, for callers that must not echo it."""

    def __init__(self, value, reason: str):
        super().__init__(f"Revenue {value!r} {reason}")
        self.reason = reason


def reais_to_cents(value: Decimal | int | str | None) -> int | None:
    """Reais (Decimal, int or a decimal string such as "1234.50") to cents.

    Rejected, never rounded: floats and bools, text that isn't a plain
    decimal number (no thousands separators, no "R$"), NaN/Infinity, more than
    two decimal places ("10.005"; trailing zeros as in "10.500" are fine),
    negative values and anything above MAX_BID_REVENUE_CENTS.
    """
    if value is None:
        return None
    if isinstance(value, (float, bool)) or not isinstance(value, (Decimal, int, str)):
        raise InvalidMoneyValue(type(value).__name__, "must be a Decimal, int or decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise InvalidMoneyValue(value, "is not a decimal number") from None
    if not amount.is_finite():
        raise InvalidMoneyValue(value, "is not a finite number")
    if amount < 0:
        raise InvalidMoneyValue(value, "is negative")
    with localcontext() as ctx:
        ctx.traps[Inexact] = True
        try:
            cents = amount * 100
        except Inexact:
            raise InvalidMoneyValue(value, "has more digits than can be stored exactly") from None
    if cents != cents.to_integral_value():
        raise InvalidMoneyValue(value, "has more than two decimal places")
    if cents > MAX_BID_REVENUE_CENTS:
        raise InvalidMoneyValue(value, f"exceeds the maximum of {cents_to_reais(MAX_BID_REVENUE_CENTS)}")
    return int(cents)


def cents_to_reais(cents: int | None) -> Decimal | None:
    """Stored cents back to reais, with exactly two decimal places."""
    if cents is None:
        return None
    return Decimal(cents).scaleb(-2)
