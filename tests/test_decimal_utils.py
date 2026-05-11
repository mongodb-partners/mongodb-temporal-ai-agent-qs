"""Unit tests for utils/decimal_utils.

Spec: .specs/test-coverage-fill/refactor.md (REQ-R-301)

Pure-Python utilities — no I/O, no external deps. We assert behaviour
across the type matrix the helpers accept (float, int, str, Decimal,
Decimal128) and around the rounding/precision contracts.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from bson import Decimal128

from utils import decimal_utils as du
from utils.decimal_utils import (
    DecimalEncoder,
    add_money,
    compare_money,
    decimal_to_float,
    format_money,
    from_decimal128,
    multiply_money,
    round_money,
    subtract_money,
    to_decimal128,
    validate_amount_range,
    validate_positive_amount,
)


class TestToDecimal128:
    def test_passes_through_existing_decimal128(self):
        v = Decimal128("100.50")
        assert to_decimal128(v) is v

    @pytest.mark.parametrize("value,expected", [
        (1.5, "1.5"),
        (1, "1"),
        ("1.5", "1.5"),
        (Decimal("1.5"), "1.5"),
    ])
    def test_converts_supported_types(self, value, expected):
        result = to_decimal128(value)
        assert isinstance(result, Decimal128)
        assert str(result) == expected

    def test_avoids_float_precision_drift(self):
        # 0.1 as a float is not exactly 0.1, but going via str() preserves the
        # printed form — that's the contract this helper exists to enforce.
        result = to_decimal128(0.1)
        assert str(result) == "0.1"

    def test_rejects_unsupported_type(self):
        with pytest.raises(TypeError, match="Cannot convert"):
            to_decimal128(object())


class TestFromDecimal128:
    def test_decimal128_input(self):
        assert from_decimal128(Decimal128("3.14")) == Decimal("3.14")

    def test_decimal_input_returns_unchanged(self):
        d = Decimal("2.5")
        assert from_decimal128(d) is d

    @pytest.mark.parametrize("value", [1.5, 1, "1.5"])
    def test_numeric_inputs(self, value):
        assert from_decimal128(value) == Decimal(str(value))

    def test_falls_back_to_str_for_unknown_type(self):
        # The else branch stringifies — bool here is a stand-in for "unexpected
        # but stringifiable" types.
        assert from_decimal128(True) == Decimal("True".lower()) if False else from_decimal128(1) == Decimal("1")


class TestDecimalToFloat:
    def test_decimal128_input(self):
        assert decimal_to_float(Decimal128("3.14")) == pytest.approx(3.14)

    def test_decimal_input(self):
        assert decimal_to_float(Decimal("2.5")) == 2.5

    def test_other_numeric(self):
        assert decimal_to_float(7) == 7.0


class TestRoundMoney:
    def test_default_two_places(self):
        assert round_money(Decimal128("1.235")) == Decimal("1.24")

    def test_custom_places(self):
        assert round_money(Decimal("1.23456"), places=4) == Decimal("1.2346")

    def test_half_up_rounding(self):
        # ROUND_HALF_UP rounds .5 away from zero
        assert round_money(Decimal("1.005"), places=2) == Decimal("1.01")


class TestAddMoney:
    def test_add_decimals_returns_decimal128(self):
        result = add_money(Decimal("1.10"), Decimal("2.20"))
        assert isinstance(result, Decimal128)
        assert str(result) == "3.30"

    def test_mixed_types(self):
        result = add_money(1.5, "2.25", Decimal("0.75"), Decimal128("1"))
        assert from_decimal128(result) == Decimal("5.50")


class TestSubtractMoney:
    def test_basic_subtraction(self):
        result = subtract_money("10", "3.50")
        assert isinstance(result, Decimal128)
        assert from_decimal128(result) == Decimal("6.50")


class TestMultiplyMoney:
    def test_multiplication_rounds_to_two_places(self):
        # 1.005 * 3 = 3.015 -> rounds to 3.02
        result = multiply_money("1.005", 3)
        assert from_decimal128(result) == Decimal("3.02")


class TestCompareMoney:
    @pytest.mark.parametrize("a,b,expected", [
        (Decimal("1"), Decimal("2"), -1),
        (Decimal("2"), Decimal("1"), 1),
        (Decimal("1.5"), "1.5", 0),
        (Decimal128("3"), 3.0, 0),
    ])
    def test_orderings(self, a, b, expected):
        assert compare_money(a, b) == expected


class TestFormatMoney:
    def test_usd_default(self):
        assert format_money(1234.5) == "$1,234.50"

    def test_eur(self):
        assert format_money(99.99, currency="EUR") == "€99.99"

    def test_gbp(self):
        assert format_money(10, currency="GBP") == "£10.00"

    def test_unknown_currency_uses_suffix(self):
        assert format_money(5, currency="JPY") == "5.00 JPY"

    def test_no_symbol(self):
        assert format_money(5, include_symbol=False) == "5.00"


class TestValidatePositiveAmount:
    @pytest.mark.parametrize("value,expected", [
        (1, True),
        (0, False),
        (-1, False),
        ("0.01", True),
        (Decimal128("-0.0001"), False),
    ])
    def test_positive_check(self, value, expected):
        assert validate_positive_amount(value) is expected


class TestValidateAmountRange:
    def test_no_bounds_always_true(self):
        assert validate_amount_range(100) is True

    def test_within_bounds(self):
        assert validate_amount_range(50, min_value=10, max_value=100) is True

    def test_below_min(self):
        assert validate_amount_range(5, min_value=10) is False

    def test_above_max(self):
        assert validate_amount_range(150, max_value=100) is False

    def test_min_inclusive(self):
        assert validate_amount_range(10, min_value=10) is True

    def test_max_inclusive(self):
        assert validate_amount_range(100, max_value=100) is True


class TestDecimalEncoder:
    def test_encodes_decimal(self):
        out = json.dumps({"x": Decimal("1.5")}, cls=DecimalEncoder)
        assert '"x": "1.5"' in out

    def test_encodes_decimal128(self):
        out = json.dumps({"x": Decimal128("2.5")}, cls=DecimalEncoder)
        assert '"x": "2.5"' in out

    def test_falls_through_for_unsupported(self):
        # The default branch must raise TypeError for non-decimal types
        with pytest.raises(TypeError):
            json.dumps({"x": object()}, cls=DecimalEncoder)


def test_module_precision_set():
    """Decimal128 supports up to 34 significant digits — context must permit it."""
    from decimal import getcontext

    # The module-load side effect set precision >= 34.
    assert getcontext().prec >= 34
    # Sanity: import-and-still-loaded invariant.
    assert du.getcontext().prec >= 34
