"""Unit tests for temporal/shared.

Spec: .specs/test-coverage-fill/refactor.md (REQ-R-301)

Smoke tests for the dataclass payloads exchanged between workflow and
activities, and the custom exception types.
"""

from __future__ import annotations

import pytest

from temporal.shared import (
    InsufficientDataError,
    ProcessingResult,
    RiskAssessment,
    SystemError,
    TRANSACTION_PROCESSING_TASK_QUEUE,
    TransactionDetails,
)


def test_task_queue_constant():
    assert TRANSACTION_PROCESSING_TASK_QUEUE == "transaction-processing-queue"


def test_transaction_details_constructs_with_required_fields():
    td = TransactionDetails(
        transaction_id="TXN_1",
        transaction_type="wire_transfer",
        amount="1000.00",
        currency="USD",
        sender={"name": "A"},
        recipient={"name": "B"},
        reference_number="REF",
        risk_flags=[],
        metadata={},
    )
    assert td.transaction_id == "TXN_1"
    assert td.amount == "1000.00"
    assert td.risk_flags == []


def test_processing_result_optional_fields_default_to_none():
    pr = ProcessingResult(
        success=True,
        decision="approve",
        confidence=95.0,
        message="ok",
    )
    assert pr.decision_id is None
    assert pr.risk_score is None
    assert pr.processing_time_ms is None
    assert pr.workflow_id is None


def test_risk_assessment_carries_compliance_dict():
    ra = RiskAssessment(
        risk_score=42,
        risk_level="medium",
        risk_factors=["x"],
        requires_enhanced_diligence=False,
        compliance_checks={"sanctions_check": True},
    )
    assert ra.compliance_checks["sanctions_check"] is True


def test_insufficient_data_error_is_exception():
    with pytest.raises(InsufficientDataError):
        raise InsufficientDataError("missing")


def test_system_error_is_exception():
    with pytest.raises(SystemError):
        raise SystemError("boom")
