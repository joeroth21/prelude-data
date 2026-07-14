from decimal import Decimal

from prelude_data.compute import (
    parse_ark_weight,
    premium_calculation_note,
    premium_to_nav_pct,
    to_decimal,
)


class TestPremiumToNav:
    def test_dxyz_style_premium_exact(self):
        # price 25.88 vs NAV 24.56 -> (25.88-24.56)/24.56*100 = 5.374592...
        assert premium_to_nav_pct(25.88, 24.56) == Decimal("5.37")

    def test_deep_premium_exact(self):
        # the classic 4x scenario: price 25.00 vs NAV 5.00 -> +400.00%
        assert premium_to_nav_pct(25.00, 5.00) == Decimal("400.00")

    def test_discount_exact(self):
        # BDC below NAV: price 11.50 vs NAV 14.24 -> -19.2415...
        assert premium_to_nav_pct(11.50, 14.24) == Decimal("-19.24")

    def test_parity_is_zero(self):
        assert premium_to_nav_pct(10, 10) == Decimal("0.00")

    def test_float_artifacts_do_not_leak(self):
        # 0.1/0.3 style floats must be handled via str conversion
        assert premium_to_nav_pct(0.3, 0.1) == Decimal("200.00")

    def test_bankers_rounding_half_even(self):
        # (100.125 - 100)/100*100 = 0.125 -> rounds to 0.12 (half-even)
        assert premium_to_nav_pct("100.125", "100") == Decimal("0.12")
        # 0.135 -> 0.14 (half rounds to even neighbour 4)
        assert premium_to_nav_pct("100.135", "100") == Decimal("0.14")

    def test_missing_inputs_return_none(self):
        assert premium_to_nav_pct(None, 24.56) is None
        assert premium_to_nav_pct(25.88, None) is None
        assert premium_to_nav_pct(None, None) is None

    def test_zero_nav_returns_none_not_crash(self):
        assert premium_to_nav_pct(25.88, 0) is None

    def test_negative_price_still_computes(self):
        # nonsense economically, but math must be deterministic
        assert premium_to_nav_pct(0, 10) == Decimal("-100.00")


class TestCalculationNote:
    def test_note_shows_the_math(self):
        pct = premium_to_nav_pct(25.88, 24.56)
        note = premium_calculation_note(25.88, 24.56, pct)
        assert note == (
            "premium_to_nav_pct = (market_price 25.88 - nav_per_share 24.56)"
            " / 24.56 * 100 = 5.37%"
        )

    def test_no_note_when_no_computation(self):
        assert premium_calculation_note(None, 24.56, None) is None


class TestArkWeight:
    def test_parses_percent_string(self):
        assert parse_ark_weight("13.78%") == Decimal("13.78")

    def test_parses_without_percent_sign(self):
        assert parse_ark_weight("4.59") == Decimal("4.59")

    def test_blank_and_garbage_return_none(self):
        assert parse_ark_weight("") is None
        assert parse_ark_weight("  ") is None
        assert parse_ark_weight("n/a") is None


class TestToDecimal:
    def test_float_via_str(self):
        assert to_decimal(25.88) == Decimal("25.88")

    def test_decimal_passthrough(self):
        d = Decimal("1.23")
        assert to_decimal(d) is d
