"""Unit tests for services/risk_engine.

Spec: .specs/test-coverage-fill/refactor.md (REQ-R-301)
"""

from __future__ import annotations

import pytest

from database.schemas import RiskLevel, TransactionType
from services.risk_engine import RiskEngine


class TestCalculateBaseRisk:
    @pytest.mark.parametrize("ttype,expected", [
        (TransactionType.ACH, 10),
        (TransactionType.WIRE_TRANSFER, 30),
        (TransactionType.INTERNATIONAL, 50),
    ])
    def test_base_per_type_at_low_amount(self, ttype, expected):
        # Amount below all thresholds -> base score equals type score.
        assert RiskEngine.calculate_base_risk(ttype, 100) == expected

    def test_unknown_type_falls_through_to_default(self):
        assert RiskEngine.calculate_base_risk("unknown", 100) == 25

    @pytest.mark.parametrize("amount,expected_bump", [
        (5000, 0),       # below 10k
        (15000, 10),     # 10k–50k
        (60000, 20),     # 50k–100k
        (500000, 30),    # >100k
    ])
    def test_amount_bumps(self, amount, expected_bump):
        # ACH = 10 + bump
        assert (
            RiskEngine.calculate_base_risk(TransactionType.ACH, amount)
            == 10 + expected_bump
        )

    def test_clamped_to_100(self):
        # International (50) + max amount bump (30) = 80 — won't trigger clamp.
        # Force clamp via apply_risk_factors stacking instead.
        assert (
            RiskEngine.calculate_base_risk(TransactionType.INTERNATIONAL, 999999) == 80
        )


class TestApplyRiskFactors:
    def test_no_flags_returns_base(self):
        assert RiskEngine.apply_risk_factors(40, []) == 40

    def test_known_flag_adds_adjustment(self):
        assert RiskEngine.apply_risk_factors(40, ["new_recipient"]) == 55

    def test_unknown_flag_ignored(self):
        assert RiskEngine.apply_risk_factors(40, ["totally_unknown"]) == 40

    def test_multiple_flags_stack(self):
        # high_risk_country (25) + structuring (30) = +55 over 50 = 105 -> clamp 100
        score = RiskEngine.apply_risk_factors(50, ["high_risk_country", "structuring"])
        assert score == 100

    def test_clamp_to_100(self):
        score = RiskEngine.apply_risk_factors(80, ["high_risk_country"])
        assert score == 100  # 80 + 25 clamped


class TestDetermineRiskLevel:
    @pytest.mark.parametrize("score,expected", [
        (0, RiskLevel.LOW),
        (25, RiskLevel.LOW),
        (26, RiskLevel.MEDIUM),
        (50, RiskLevel.MEDIUM),
        (51, RiskLevel.HIGH),
        (75, RiskLevel.HIGH),
        (76, RiskLevel.VERY_HIGH),
        (100, RiskLevel.VERY_HIGH),
    ])
    def test_thresholds(self, score, expected):
        assert RiskEngine.determine_risk_level(score) == expected


class TestCheckPatterns:
    def test_high_velocity(self):
        history = [{"days_ago": 0, "amount": 1} for _ in range(6)]
        patterns = RiskEngine.check_patterns({"amount": 1}, history)
        assert "high_velocity" in patterns

    def test_no_high_velocity_with_old_history(self):
        history = [{"days_ago": 5, "amount": 1} for _ in range(10)]
        patterns = RiskEngine.check_patterns({"amount": 1}, history)
        assert "high_velocity" not in patterns

    def test_potential_splitting(self):
        # Four similar amounts (within 10%) of $10000 -> trigger
        history = [{"amount": 10500} for _ in range(4)]
        patterns = RiskEngine.check_patterns({"amount": 10000}, history)
        assert "potential_splitting" in patterns

    def test_no_splitting_when_dissimilar(self):
        history = [{"amount": 100}, {"amount": 999}, {"amount": 50000}]
        patterns = RiskEngine.check_patterns({"amount": 10000}, history)
        assert "potential_splitting" not in patterns

    def test_empty_history(self):
        assert RiskEngine.check_patterns({"amount": 100}, []) == []
