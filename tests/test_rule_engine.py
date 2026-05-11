"""Unit tests for services/rule_engine.

Spec: .specs/test-coverage-fill/refactor.md (REQ-R-301)

We mock RuleRepository.get_active_rules_sync so apply_rules can be
tested without a database.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from bson import Decimal128

from services.rule_engine import RuleEngine


class TestEvaluateCondition:
    def test_missing_field_returns_false(self):
        # No 'field' key -> False without raising.
        assert RuleEngine.evaluate_condition({"operator": "equals", "value": 1}, {}) is False

    def test_nested_path_traversal(self):
        cond = {"field": "sender.country", "operator": "equals", "value": "US"}
        assert RuleEngine.evaluate_condition(cond, {"sender": {"country": "US"}}) is True

    def test_nested_path_missing_intermediate(self):
        # Walks into a non-dict mid-path -> short-circuits to False.
        cond = {"field": "sender.country", "operator": "equals", "value": "US"}
        assert RuleEngine.evaluate_condition(cond, {"sender": "US"}) is False

    @pytest.mark.parametrize("operator,actual,value,expected", [
        ("equals", 5, 5, True),
        ("equals", 5, 6, False),
        ("not_equals", 5, 6, True),
        ("not_equals", 5, 5, False),
        ("in", "US", ["US", "GB"], True),
        ("in", "DE", ["US", "GB"], False),
        ("not_in", "DE", ["US", "GB"], True),
        ("contains", "Hello World", "World", True),
        ("contains", "Hello", "World", False),
        ("regex", "ABC123", r"ABC\d+", True),
        ("regex", "xyz", r"ABC\d+", False),
        ("exists", 5, None, True),
        ("exists", None, None, False),
        ("not_exists", None, None, True),
        ("not_exists", 5, None, False),
    ])
    def test_basic_operators(self, operator, actual, value, expected):
        cond = {"field": "x", "operator": operator, "value": value}
        assert RuleEngine.evaluate_condition(cond, {"x": actual}) is expected

    @pytest.mark.parametrize("operator,actual,value,expected", [
        ("greater_than", 10, 5, True),
        ("greater_than", 5, 10, False),
        ("less_than", 5, 10, True),
        ("less_than", 10, 5, False),
        ("greater_or_equal", 5, 5, True),
        ("greater_or_equal", 4, 5, False),
        ("less_or_equal", 5, 5, True),
        ("less_or_equal", 6, 5, False),
    ])
    def test_numeric_operators(self, operator, actual, value, expected):
        cond = {"field": "x", "operator": operator, "value": value}
        assert RuleEngine.evaluate_condition(cond, {"x": actual}) is expected

    @pytest.mark.parametrize("operator", [
        "greater_than", "less_than", "greater_or_equal", "less_or_equal",
    ])
    def test_numeric_operators_with_none_actual(self, operator):
        cond = {"field": "x", "operator": operator, "value": 5}
        assert RuleEngine.evaluate_condition(cond, {"x": None}) is False

    @pytest.mark.parametrize("operator", [
        "greater_than", "less_than", "greater_or_equal", "less_or_equal",
    ])
    def test_numeric_operators_with_none_value(self, operator):
        cond = {"field": "x", "operator": operator, "value": None}
        assert RuleEngine.evaluate_condition(cond, {"x": 5}) is False

    def test_numeric_with_decimal128(self):
        cond = {
            "field": "amount",
            "operator": "greater_than",
            "value": Decimal128("1000"),
        }
        assert RuleEngine.evaluate_condition(
            cond, {"amount": Decimal128("2000")}
        ) is True

    def test_unknown_operator_returns_false(self):
        cond = {"field": "x", "operator": "wat", "value": 1}
        assert RuleEngine.evaluate_condition(cond, {"x": 1}) is False

    def test_exception_inside_evaluation_logged_and_returns_false(self):
        # `in` with non-iterable value triggers TypeError, caught -> False.
        cond = {"field": "x", "operator": "in", "value": 1}
        assert RuleEngine.evaluate_condition(cond, {"x": 1}) is False


class TestEvaluateRule:
    def test_empty_conditions_returns_false(self):
        assert RuleEngine.evaluate_rule({"conditions": {}}, {}) is False

    def test_and_logic_all_pass(self):
        rule = {
            "conditions": {
                "operator": "AND",
                "conditions": [
                    {"field": "a", "operator": "equals", "value": 1},
                    {"field": "b", "operator": "equals", "value": 2},
                ],
            }
        }
        assert RuleEngine.evaluate_rule(rule, {"a": 1, "b": 2}) is True
        assert RuleEngine.evaluate_rule(rule, {"a": 1, "b": 3}) is False

    def test_or_logic_one_passes(self):
        rule = {
            "conditions": {
                "operator": "OR",
                "conditions": [
                    {"field": "a", "operator": "equals", "value": 1},
                    {"field": "b", "operator": "equals", "value": 2},
                ],
            }
        }
        assert RuleEngine.evaluate_rule(rule, {"a": 1, "b": 99}) is True
        assert RuleEngine.evaluate_rule(rule, {"a": 99, "b": 99}) is False

    def test_unknown_logic_operator_returns_false(self):
        rule = {
            "conditions": {
                "operator": "XOR",
                "conditions": [{"field": "a", "operator": "equals", "value": 1}],
            }
        }
        assert RuleEngine.evaluate_rule(rule, {"a": 1}) is False

    def test_exception_caught_returns_false(self):
        # Pass a non-dict conditions to trigger AttributeError inside try.
        rule = {"rule_id": "X", "conditions": "not-a-dict"}
        assert RuleEngine.evaluate_rule(rule, {}) is False


class TestApplyRules:
    @patch("services.rule_engine.RuleRepository.get_active_rules_sync")
    def test_no_rules_match_yields_empty_result(self, get_rules):
        get_rules.return_value = []
        result = RuleEngine.apply_rules({"transaction_id": "T1"})
        assert result == {
            "triggered_rules": [],
            "risk_flags": [],
            "recommended_action": None,
            "rule_count": 0,
        }

    @patch("services.rule_engine.RuleRepository.get_active_rules_sync")
    def test_triggered_rule_picks_highest_priority_action(self, get_rules):
        get_rules.return_value = [
            {
                "rule_id": "R1",
                "name": "low",
                "category": "amount",
                "action": "approve",
                "priority": 10,
                "conditions": {
                    "operator": "AND",
                    "conditions": [{"field": "x", "operator": "equals", "value": 1}],
                },
            },
            {
                "rule_id": "R2",
                "name": "high",
                "category": "geography",
                "action": "escalate",
                "priority": 90,
                "conditions": {
                    "operator": "AND",
                    "conditions": [{"field": "x", "operator": "equals", "value": 1}],
                },
            },
        ]
        result = RuleEngine.apply_rules({"x": 1, "transaction_id": "T2"})
        assert set(result["triggered_rules"]) == {"R1", "R2"}
        assert "rule_amount" in result["risk_flags"]
        assert "rule_geography" in result["risk_flags"]
        assert result["recommended_action"] == "escalate"
        assert result["rule_count"] == 2

    @patch("services.rule_engine.RuleRepository.get_active_rules_sync")
    def test_repository_failure_returns_safe_default(self, get_rules):
        get_rules.side_effect = RuntimeError("db down")
        result = RuleEngine.apply_rules({"x": 1})
        assert result["triggered_rules"] == []
        assert result["recommended_action"] is None

    @patch("services.rule_engine.RuleRepository.get_active_rules_sync")
    def test_rule_without_action_or_category(self, get_rules):
        get_rules.return_value = [
            {
                "rule_id": "R1",
                "name": "no-action",
                # No category, no action — must not crash.
                "priority": 5,
                "conditions": {
                    "operator": "AND",
                    "conditions": [{"field": "x", "operator": "equals", "value": 1}],
                },
            },
        ]
        result = RuleEngine.apply_rules({"x": 1})
        assert result["triggered_rules"] == ["R1"]
        assert result["risk_flags"] == []  # no category -> no flag
        assert result["recommended_action"] is None  # no action collected


class TestGetDefaultRules:
    def test_returns_at_least_six_rules(self):
        rules = RuleEngine.get_default_rules()
        assert len(rules) >= 6
        # Sanity: every rule is well-formed
        for r in rules:
            assert r.name
            assert r.action in {"approve", "reject", "escalate"}
            assert "operator" in r.conditions
            assert isinstance(r.conditions["conditions"], list)

    def test_rule_names_unique(self):
        names = [r.name for r in RuleEngine.get_default_rules()]
        assert len(names) == len(set(names))
