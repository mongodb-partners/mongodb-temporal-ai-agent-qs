"""Unit tests for ai/prompts.

Spec: .specs/test-coverage-fill/refactor.md (REQ-R-301)

Pure formatting helpers. We assert that the rendered prompt contains
the expected sections for each transaction type, that similar-cases
formatting handles both populated and empty inputs, and that
Decimal128 amounts are dollar-formatted correctly.
"""

from __future__ import annotations

from bson import Decimal128

from ai.prompts import (
    create_risk_assessment_prompt,
    create_transaction_analysis_prompt,
)
from database.schemas import TransactionType


def _txn(ttype: str, amount=1000):
    return {
        "transaction_id": "TXN_1",
        "transaction_type": ttype,
        "amount": Decimal128(str(amount)),
        "currency": "USD",
        "sender": {"name": "Alice", "country": "US"},
        "recipient": {"name": "Bob", "country": "GB"},
        "reference_number": "REF",
        "risk_flags": ["new_recipient", "unusual_time"],
    }


def _customer_history(**overrides):
    base = {
        "total_transactions": 10,
        "avg_amount": Decimal128("500"),
        "total_amount": Decimal128("5000"),
        "risk_incidents": 1,
    }
    base.update(overrides)
    return base


class TestTransactionAnalysisPrompt:
    def test_wire_transfer_includes_wire_specific_context(self):
        prompt = create_transaction_analysis_prompt(
            _txn(TransactionType.WIRE_TRANSFER.value),
            similar_cases=[],
            customer_history=_customer_history(),
        )
        assert "WIRE TRANSFER" in prompt
        assert "Irreversible nature" in prompt

    def test_ach_includes_ach_specific_context(self):
        prompt = create_transaction_analysis_prompt(
            _txn(TransactionType.ACH.value),
            similar_cases=[],
            customer_history=_customer_history(),
        )
        assert "ACH TRANSFER" in prompt
        assert "Batch processing" in prompt

    def test_international_includes_international_specific_context(self):
        prompt = create_transaction_analysis_prompt(
            _txn(TransactionType.INTERNATIONAL.value),
            similar_cases=[],
            customer_history=_customer_history(),
        )
        assert "INTERNATIONAL TRANSFER" in prompt
        assert "Sanctions screening" in prompt

    def test_no_similar_cases_message(self):
        prompt = create_transaction_analysis_prompt(
            _txn(TransactionType.WIRE_TRANSFER.value),
            similar_cases=[],
            customer_history=_customer_history(),
        )
        assert "No similar historical cases found." in prompt

    def test_similar_cases_rendered_with_amount(self):
        cases = [
            {"amount": Decimal128("1000"), "decision": "approve", "risk_score": 30},
            {"amount": 2000.0, "decision": "reject", "risk_score": 80},
        ]
        prompt = create_transaction_analysis_prompt(
            _txn(TransactionType.WIRE_TRANSFER.value),
            similar_cases=cases,
            customer_history=_customer_history(),
        )
        assert "$1,000.00" in prompt
        assert "$2,000.00" in prompt
        assert "approve" in prompt
        assert "reject" in prompt

    def test_similar_cases_capped_at_five(self):
        # Provide six cases; only the first five should be rendered.
        cases = [
            {"amount": Decimal128(str(i * 100)), "decision": "approve", "risk_score": i}
            for i in range(1, 7)
        ]
        prompt = create_transaction_analysis_prompt(
            _txn(TransactionType.WIRE_TRANSFER.value),
            similar_cases=cases,
            customer_history=_customer_history(),
        )
        # The 6th case (amount=600) must NOT be rendered.
        assert "$600.00" not in prompt
        # First case (amount=100) must be rendered.
        assert "$100.00" in prompt

    def test_risk_flags_rendered_or_default_none(self):
        txn = _txn(TransactionType.WIRE_TRANSFER.value)
        prompt = create_transaction_analysis_prompt(
            txn, similar_cases=[], customer_history=_customer_history()
        )
        assert "new_recipient" in prompt
        assert "unusual_time" in prompt

        # When risk_flags is missing entirely the default 'None' literal is used.
        txn.pop("risk_flags")
        prompt = create_transaction_analysis_prompt(
            txn, similar_cases=[], customer_history=_customer_history()
        )
        assert "Risk Flags: None" in prompt

    def test_unknown_type_omits_type_specific_context(self):
        # An unrecognised transaction_type means no type_context branch fires.
        txn = _txn("savings_transfer")
        prompt = create_transaction_analysis_prompt(
            txn, similar_cases=[], customer_history=_customer_history()
        )
        assert "WIRE TRANSFER" not in prompt
        assert "ACH TRANSFER" not in prompt
        assert "INTERNATIONAL TRANSFER" not in prompt


class TestRiskAssessmentPrompt:
    def test_contains_amount_and_countries(self):
        prompt = create_risk_assessment_prompt(_txn(TransactionType.WIRE_TRANSFER.value))
        assert "$1,000.00" in prompt
        assert "Sender Country: US" in prompt
        assert "Recipient Country: GB" in prompt

    def test_returns_json_template(self):
        prompt = create_risk_assessment_prompt(_txn(TransactionType.WIRE_TRANSFER.value))
        assert "risk_score" in prompt
        assert "risk_level" in prompt
        assert "key_risk_factors" in prompt
