"""NQ price normalization using integer ticks.

Binary floats are intentionally not accepted on the hot/data-quality boundary: a
non-tick-aligned price must be rejected, never silently rounded into a valid level.
"""

from decimal import Decimal, InvalidOperation
from typing import NoReturn

NQ_TICK_SIZE = Decimal("0.25")
NQ_POINT_VALUE = Decimal("20")
NQ_TICK_VALUE = NQ_TICK_SIZE * NQ_POINT_VALUE


class PriceError(ValueError):
    """Raised when a price cannot be represented exactly as NQ integer ticks."""


def _reject_float(price: float) -> NoReturn:
    _ = price
    raise PriceError(
        "invalid price: binary floats are not accepted; use Decimal or string"
    )


def price_to_ticks(price: Decimal | int | str) -> int:
    """Convert a decimal-like NQ price to integer ticks with strict validation."""

    if isinstance(price, float):
        _reject_float(price)

    try:
        value = price if isinstance(price, Decimal) else Decimal(str(price))
    except (InvalidOperation, ValueError) as exc:
        raise PriceError("invalid price") from exc

    ticks = value / NQ_TICK_SIZE
    if ticks != ticks.to_integral_value():
        raise PriceError(f"price is not divisible by NQ tick size {NQ_TICK_SIZE}")
    return int(ticks)


def ticks_to_price(price_ticks: int) -> Decimal:
    """Convert integer ticks back to a display Decimal price."""

    return Decimal(price_ticks) * NQ_TICK_SIZE
