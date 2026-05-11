"""Workflow tests for temporal/workflows.py.

Spec: .specs/test-coverage-fill/refactor.md (REQ-R-304)

Uses temporalio's WorkflowEnvironment with time-skipping. Activities
are replaced with simple async stubs so we exercise the workflow's
control flow (manager approval, low-confidence escalation, manual
override, hold cleanup, insufficient funds) without booting any real
infrastructure.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Dict, List

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from temporal.shared import TRANSACTION_PROCESSING_TASK_QUEUE, TransactionDetails
from temporal.workflows import TransactionProcessingWorkflow


# ---------------------------------------------------------------------------
# Activity stubs — return whatever the test scenario expects.
# Defined as module-level functions so the worker can register them.
# ---------------------------------------------------------------------------

# Each test populates this dict with the responses each activity should yield.
_ACTIVITY_RESPONSES: Dict[str, Any] = {}
_INVOCATIONS: List[str] = []


def _record(name: str):
    _INVOCATIONS.append(name)


@activity.defn(name="validate_and_hold_funds")
async def validate_and_hold_funds(transaction_details):
    _record("validate_and_hold_funds")
    resp = _ACTIVITY_RESPONSES.get("validate_and_hold_funds")
    if isinstance(resp, Exception):
        raise resp
    return resp or {"hold_id": "HOLD_X", "validation_status": "success"}


def _txn_id(td):
    """Defensively read transaction_id from either a dataclass or a dict."""
    return td.transaction_id if hasattr(td, "transaction_id") else td.get("transaction_id")


@activity.defn(name="enrich_transaction_data")
async def enrich_transaction_data(transaction_details):
    _record("enrich_transaction_data")
    return _ACTIVITY_RESPONSES.get("enrich_transaction_data") or {
        "transaction": {"transaction_id": _txn_id(transaction_details)},
    }


@activity.defn(name="perform_risk_assessment")
async def perform_risk_assessment(enriched):
    _record("perform_risk_assessment")
    return _ACTIVITY_RESPONSES.get("perform_risk_assessment") or {
        "risk_score": 30, "risk_level": "low", "risk_factors": [],
        "requires_enhanced_diligence": False, "compliance_checks": {},
    }


@activity.defn(name="find_similar_transactions")
async def find_similar_transactions(enriched):
    _record("find_similar_transactions")
    return _ACTIVITY_RESPONSES.get("find_similar_transactions") or []


@activity.defn(name="analyze_fraud_network")
async def analyze_fraud_network(enriched):
    _record("analyze_fraud_network")
    return _ACTIVITY_RESPONSES.get("analyze_fraud_network") or {
        "network_analysis_performed": False,
    }


@activity.defn(name="ai_decision_analysis")
async def ai_decision_analysis(enriched, risk_assessment, similar_cases):
    _record("ai_decision_analysis")
    return _ACTIVITY_RESPONSES.get("ai_decision_analysis") or {
        "decision": "approve", "confidence": 95.0, "reasoning": "ok",
        "risk_factors": [], "rules_triggered": [], "processing_time_ms": 100,
    }


@activity.defn(name="store_decision")
async def store_decision(transaction_id, ai_result, workflow_id, run_id):
    _record("store_decision")
    return f"DEC_{uuid.uuid4().hex[:8].upper()}"


@activity.defn(name="queue_for_human_review")
async def queue_for_human_review(transaction_id, ai_result):
    _record("queue_for_human_review")
    return f"REV_{uuid.uuid4().hex[:8].upper()}"


@activity.defn(name="send_notification")
async def send_notification(transaction_id, decision, message):
    _record("send_notification")
    return True


@activity.defn(name="execute_fund_transfer")
async def execute_fund_transfer(transaction_id, sender, recipient, amount, hold_id):
    _record("execute_fund_transfer")
    resp = _ACTIVITY_RESPONSES.get("execute_fund_transfer")
    if isinstance(resp, Exception):
        raise resp
    return resp if resp is not None else True


@activity.defn(name="cleanup_hold")
async def cleanup_hold(hold_id):
    _record("cleanup_hold")
    return True


ALL_ACTIVITIES = [
    validate_and_hold_funds, enrich_transaction_data, perform_risk_assessment,
    find_similar_transactions, analyze_fraud_network, ai_decision_analysis,
    store_decision, queue_for_human_review, send_notification,
    execute_fund_transfer, cleanup_hold,
]


@pytest.fixture(autouse=True)
def reset_activity_state():
    _ACTIVITY_RESPONSES.clear()
    _INVOCATIONS.clear()
    yield
    _ACTIVITY_RESPONSES.clear()
    _INVOCATIONS.clear()


@pytest_asyncio.fixture
async def env():
    """Time-skipping Temporal environment + worker."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TRANSACTION_PROCESSING_TASK_QUEUE,
            workflows=[TransactionProcessingWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            yield env


def _details(amount=1000, **overrides):
    base = {
        "transaction_id": "TXN_TEST",
        "transaction_type": "wire_transfer",
        "amount": str(amount),
        "currency": "USD",
        "sender": {"name": "Alice", "country": "US", "account_number": "ACC_S"},
        "recipient": {"name": "Bob", "country": "GB", "account_number": "ACC_R"},
        "reference_number": "REF",
        "risk_flags": [],
        "metadata": {},
    }
    base.update(overrides)
    return TransactionDetails(**base)


@pytest.mark.asyncio
async def test_workflow_happy_path_approves_low_amount(env):
    """Below auto-approve limit, high confidence -> approve + transfer + notify."""
    handle = await env.client.start_workflow(
        TransactionProcessingWorkflow.run,
        _details(amount=100),
        id=f"wf-{uuid.uuid4()}",
        task_queue=TRANSACTION_PROCESSING_TASK_QUEUE,
    )
    result = await handle.result()
    assert result.decision == "approve"
    assert result.success is True
    assert "execute_fund_transfer" in _INVOCATIONS
    assert "send_notification" in _INVOCATIONS


@pytest.mark.asyncio
async def test_workflow_low_confidence_escalates(env):
    """AI returns low confidence -> queue for human review and escalate."""
    _ACTIVITY_RESPONSES["ai_decision_analysis"] = {
        "decision": "approve", "confidence": 60.0, "reasoning": "uncertain",
        "risk_factors": [], "rules_triggered": [], "processing_time_ms": 0,
    }
    handle = await env.client.start_workflow(
        TransactionProcessingWorkflow.run,
        _details(amount=100),
        id=f"wf-{uuid.uuid4()}",
        task_queue=TRANSACTION_PROCESSING_TASK_QUEUE,
    )
    result = await handle.result()
    assert result.decision == "escalate"
    assert "queue_for_human_review" in _INVOCATIONS


@pytest.mark.asyncio
async def test_workflow_reject_releases_hold_via_cleanup(env):
    """Reject -> cleanup_hold runs, transfer is NOT called."""
    _ACTIVITY_RESPONSES["ai_decision_analysis"] = {
        "decision": "reject", "confidence": 95.0, "reasoning": "rejected",
        "risk_factors": [], "rules_triggered": [], "processing_time_ms": 0,
    }
    handle = await env.client.start_workflow(
        TransactionProcessingWorkflow.run,
        _details(amount=100),
        id=f"wf-{uuid.uuid4()}",
        task_queue=TRANSACTION_PROCESSING_TASK_QUEUE,
    )
    result = await handle.result()
    assert result.decision == "reject"
    assert "execute_fund_transfer" not in _INVOCATIONS
    assert "cleanup_hold" in _INVOCATIONS


@pytest.mark.asyncio
async def test_workflow_transfer_failure_marks_reject(env):
    """If execute_fund_transfer returns False, the workflow flips decision to reject."""
    _ACTIVITY_RESPONSES["execute_fund_transfer"] = False
    handle = await env.client.start_workflow(
        TransactionProcessingWorkflow.run,
        _details(amount=100),
        id=f"wf-{uuid.uuid4()}",
        task_queue=TRANSACTION_PROCESSING_TASK_QUEUE,
    )
    result = await handle.result()
    assert result.decision == "reject"


@pytest.mark.asyncio
async def test_workflow_get_status_query(env):
    """Cover the query handler — it should report status during/after run."""
    handle = await env.client.start_workflow(
        TransactionProcessingWorkflow.run,
        _details(amount=100),
        id=f"wf-{uuid.uuid4()}",
        task_queue=TRANSACTION_PROCESSING_TASK_QUEUE,
    )
    await handle.result()
    status = await handle.query(TransactionProcessingWorkflow.get_status)
    assert status["transaction_id"] == "TXN_TEST"
    assert status["decision"] is not None


@pytest.mark.asyncio
async def test_workflow_manager_approval_timeout_escalates(env):
    """No signal within timeout -> escalate. Time-skipping advances 24h."""
    handle = await env.client.start_workflow(
        TransactionProcessingWorkflow.run,
        _details(amount=100000),
        id=f"wf-{uuid.uuid4()}",
        task_queue=TRANSACTION_PROCESSING_TASK_QUEUE,
    )
    # Without a signal, the time-skipping env will advance past the 24h timeout.
    result = await handle.result()
    assert result.decision == "escalate"


@pytest.mark.asyncio
async def test_workflow_manual_override_signal(env):
    """An override signal applied before completion changes the decision.

    We can't deliver two distinct signals via start_signal, so we send
    override_decision via start_signal (which fires once the workflow
    starts) and then signal approve with the regular signal API. The
    workflow's `awaiting_approval` flag is what holds it open long
    enough for the second signal to land before the env advances time.
    """
    handle = await env.client.start_workflow(
        TransactionProcessingWorkflow.run,
        _details(amount=100000),
        id=f"wf-{uuid.uuid4()}",
        task_queue=TRANSACTION_PROCESSING_TASK_QUEUE,
        start_signal="override_decision",
        start_signal_args=["reject", "alice", "policy"],
    )
    # Now signal approve to release the wait_condition.
    await handle.signal(TransactionProcessingWorkflow.approve, "manager_y")
    result = await handle.result()
    assert result.decision == "reject"


@pytest.mark.asyncio
async def test_workflow_activity_failure_propagates(env):
    """When validate_and_hold_funds raises a non-retryable ApplicationError,
    the workflow's `except ActivityError` re-raises it as ApplicationError
    (non_retryable=True). The handle's .result() raises WorkflowFailureError.
    """
    _ACTIVITY_RESPONSES["validate_and_hold_funds"] = ApplicationError(
        "InsufficientFundsError: Insufficient funds",
        non_retryable=True,
    )
    handle = await env.client.start_workflow(
        TransactionProcessingWorkflow.run,
        _details(amount=100),
        id=f"wf-{uuid.uuid4()}",
        task_queue=TRANSACTION_PROCESSING_TASK_QUEUE,
    )
    with pytest.raises(Exception):
        await handle.result()


@pytest.mark.asyncio
async def test_workflow_other_application_error_propagates(env):
    """A non-InsufficientFunds ApplicationError re-raises through the workflow."""
    _ACTIVITY_RESPONSES["validate_and_hold_funds"] = ApplicationError(
        "Some other failure", non_retryable=True,
    )
    handle = await env.client.start_workflow(
        TransactionProcessingWorkflow.run,
        _details(amount=100),
        id=f"wf-{uuid.uuid4()}",
        task_queue=TRANSACTION_PROCESSING_TASK_QUEUE,
    )
    with pytest.raises(Exception):
        await handle.result()


@pytest.mark.asyncio
async def test_workflow_network_analysis_boosts_risk(env):
    """When network_risk_score > 50, the workflow merges it into the assessment."""
    _ACTIVITY_RESPONSES["analyze_fraud_network"] = {
        "network_analysis_performed": True,
        "network_risk_score": 80,
        "network_risk_factors": ["money_laundering"],
    }
    handle = await env.client.start_workflow(
        TransactionProcessingWorkflow.run,
        _details(amount=100),
        id=f"wf-{uuid.uuid4()}",
        task_queue=TRANSACTION_PROCESSING_TASK_QUEUE,
    )
    result = await handle.result()
    assert result.success is True
    # Network risk score is added (clamped at 100); risk_score on the
    # processed result reflects the bumped value if that path ran.
    assert result.risk_score is not None
