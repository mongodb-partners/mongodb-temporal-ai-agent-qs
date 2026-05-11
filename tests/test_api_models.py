"""Unit tests for api/models.

Spec: .specs/test-coverage-fill/refactor.md (REQ-R-301)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.models import (
    DecisionResponse,
    MetricsResponse,
    TransactionRequest,
    TransactionResponse,
)
from database.schemas import DecisionType, TransactionStatus, TransactionType


def _valid_request_kwargs(**overrides):
    base = {
        "transaction_type": TransactionType.WIRE_TRANSFER,
        "amount": 100.0,
        "sender": {"name": "Alice", "country": "US"},
        "recipient": {"name": "Bob", "country": "GB"},
    }
    base.update(overrides)
    return base


def test_transaction_request_accepts_float_amount():
    req = TransactionRequest(**_valid_request_kwargs(amount=100.5))
    assert req.amount == 100.5
    assert req.currency == "USD"  # default


def test_transaction_request_accepts_string_amount():
    req = TransactionRequest(**_valid_request_kwargs(amount="100.50"))
    assert req.amount == "100.50"


def test_transaction_request_rejects_non_numeric_amount():
    with pytest.raises(ValidationError) as exc_info:
        TransactionRequest(**_valid_request_kwargs(amount="not-a-number"))
    assert "valid decimal number" in str(exc_info.value)


def test_transaction_request_metadata_defaults_to_empty():
    req = TransactionRequest(**_valid_request_kwargs())
    assert req.metadata == {}
    assert req.reference_number is None
    assert req.description is None


def test_transaction_response_minimal():
    r = TransactionResponse(
        transaction_id="TXN_1",
        status=TransactionStatus.PROCESSING,
        message="ok",
    )
    assert r.workflow_id is None


def test_decision_response_construction():
    d = DecisionResponse(
        transaction_id="TXN_1",
        decision=DecisionType.APPROVE,
        confidence=95.0,
        risk_score=20.0,
        reasoning="ok",
        processing_time_ms=120,
        risk_factors=["amount"],
    )
    assert d.decision is DecisionType.APPROVE


def test_metrics_response_construction():
    m = MetricsResponse(
        total_transactions=100,
        transactions_by_type={"wire_transfer": 50},
        decisions_breakdown={"approve": 80},
        average_processing_time_ms=200.0,
        average_confidence=85.0,
        total_amount_processed=12345.67,
    )
    assert m.total_transactions == 100
