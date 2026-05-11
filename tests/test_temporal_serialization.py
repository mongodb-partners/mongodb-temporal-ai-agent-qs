"""Unit tests for utils/temporal_serialization.

Spec: .specs/test-coverage-fill/refactor.md (REQ-R-301)

Validates that Decimal128 and Decimal values nested at arbitrary depth
are stringified for Temporal's JSON-only payload contract, while other
types pass through unchanged.
"""

from __future__ import annotations

from decimal import Decimal

from bson import Decimal128

from utils.temporal_serialization import (
    prepare_activity_result,
    sanitize_for_json,
)


def test_sanitize_decimal128_to_string():
    assert sanitize_for_json(Decimal128("1.50")) == "1.50"


def test_sanitize_decimal_to_string():
    assert sanitize_for_json(Decimal("2.25")) == "2.25"


def test_sanitize_dict_recurses():
    out = sanitize_for_json({"a": Decimal("1"), "b": {"c": Decimal128("2")}})
    assert out == {"a": "1", "b": {"c": "2"}}


def test_sanitize_list_recurses():
    out = sanitize_for_json([Decimal("1"), {"x": Decimal128("3")}])
    assert out == ["1", {"x": "3"}]


def test_sanitize_tuple_returns_tuple():
    # Tuples are preserved as tuples (not converted to lists), per the impl.
    out = sanitize_for_json((Decimal("1"), "x"))
    assert out == ("1", "x")
    assert isinstance(out, tuple)


def test_sanitize_passthrough_for_primitives():
    assert sanitize_for_json("text") == "text"
    assert sanitize_for_json(42) == 42
    assert sanitize_for_json(3.14) == 3.14
    assert sanitize_for_json(None) is None
    assert sanitize_for_json(True) is True


def test_prepare_activity_result_delegates_to_sanitize():
    result = {"amount": Decimal128("100"), "ok": True, "items": [Decimal("1")]}
    out = prepare_activity_result(result)
    assert out == {"amount": "100", "ok": True, "items": ["1"]}
