"""Pure computations. Money math uses Decimal — never binary floats.

Field names are deliberately neutral (premium_to_nav_pct, not anything
judgmental): this feed states facts, it does not rate them.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal


def to_decimal(value: float | int | str | Decimal) -> Decimal:
    """Convert via str so float artifacts (25.879999...) don't leak in."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def premium_to_nav_pct(market_price: object, nav_per_share: object) -> Decimal | None:
    """Percentage the market price sits above (+) or below (-) NAV.

    (price - nav) / nav * 100, rounded to 2 decimal places (banker's
    rounding). Returns None when either input is missing or NAV is zero —
    the feed marks the field unavailable rather than guessing.
    """
    if market_price is None or nav_per_share is None:
        return None
    price = to_decimal(market_price)  # type: ignore[arg-type]
    nav = to_decimal(nav_per_share)  # type: ignore[arg-type]
    if nav == 0:
        return None
    pct = (price - nav) / nav * Decimal("100")
    return pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def premium_calculation_note(
    market_price: object, nav_per_share: object, pct: Decimal | None
) -> str | None:
    """Human-readable audit trail for the premium/discount figure."""
    if pct is None:
        return None
    return (
        f"premium_to_nav_pct = (market_price {market_price} - nav_per_share "
        f"{nav_per_share}) / {nav_per_share} * 100 = {pct}%"
    )


def parse_ark_weight(raw: str) -> Decimal | None:
    """ARK weights arrive as '13.78%'. Returns Decimal percent or None."""
    cleaned = raw.strip().rstrip("%").strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    except ArithmeticError:
        return None
