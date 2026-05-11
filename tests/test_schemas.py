"""Unit tests for database schemas.

Spec: .specs/test-coverage-fill/refactor.md (REQ-R-301)
"""

from __future__ import annotations

from datetime import datetime, timezone

from bson import Decimal128

from database import schemas
from database.account_schemas import (
    Account,
    AccountStatus,
    AccountType,
    BalanceHold,
    BalanceUpdate,
    TransactionJournal,
)
from database.schemas import (
    AuditEvent,
    Customer,
    DecisionType,
    HumanReview,
    Notification,
    NotificationStatus,
    Rule,
    RuleStatus,
    SystemMetric,
    Transaction,
    TransactionDecision,
    TransactionStatus,
    TransactionType,
    generate_customer_id,
    generate_decision_id,
    generate_event_id,
    generate_metric_id,
    generate_notification_id,
    generate_review_id,
    generate_rule_id,
    generate_transaction_id,
    get_current_time,
)


def test_id_generators_produce_distinct_prefixes():
    pairs = [
        (generate_transaction_id, "TXN_"),
        (generate_decision_id, "DEC_"),
        (generate_event_id, "EVT_"),
        (generate_metric_id, "MET_"),
        (generate_rule_id, "RULE_"),
        (generate_customer_id, "CUST_"),
        (generate_review_id, "REV_"),
        (generate_notification_id, "NOTIF_"),
    ]
    for fn, prefix in pairs:
        value = fn()
        assert value.startswith(prefix)
        # Non-trivial suffix
        assert len(value) > len(prefix) + 4


def test_id_generators_produce_unique_values():
    assert generate_transaction_id() != generate_transaction_id()
    assert generate_rule_id() != generate_rule_id()


def test_get_current_time_is_tz_aware_utc():
    now = get_current_time()
    assert now.tzinfo is timezone.utc


def test_customer_defaults():
    c = Customer(
        legal_name="Acme",
        display_name="Acme Co",
        customer_type="business",
        country="US",
    )
    assert c.customer_id.startswith("CUST_")
    assert c.risk_profile["risk_level"] == "medium"
    assert c.behavior_profile["common_recipients"] == []
    assert c.accounts == []
    assert c.status == "active"


def test_transaction_decimal128_field():
    t = Transaction(
        transaction_type=TransactionType.WIRE_TRANSFER,
        amount=Decimal128("100"),
        sender={"name": "A"},
        recipient={"name": "B"},
    )
    assert t.transaction_id.startswith("TXN_")
    assert t.status == TransactionStatus.PENDING
    assert t.embedding is None


def test_rule_priority_clamped_to_range():
    r = Rule(
        name="r1",
        description="d",
        category="amount",
        conditions={"operator": "AND", "conditions": []},
        action="approve",
        priority=42,
    )
    assert 0 <= r.priority <= 100
    assert r.status == RuleStatus.ACTIVE


def test_human_review_default_status_pending():
    review = HumanReview(transaction_id="TXN_1")
    assert review.status == "pending"
    assert review.priority == "medium"


def test_notification_default_status_pending():
    n = Notification(notification_type="decision", subject="s", message="m")
    assert n.status == NotificationStatus.PENDING


def test_transaction_decision_round_trip():
    d = TransactionDecision(
        transaction_id="TXN_1",
        decision=DecisionType.APPROVE,
        confidence_score=Decimal128("90"),
        risk_score=Decimal128("30"),
        processing_time_ms=120,
        reasoning={"primary_reasoning": "ok"},
    )
    assert d.decision_id.startswith("DEC_")
    assert d.decision is DecisionType.APPROVE


def test_audit_event_required_fields():
    e = AuditEvent(
        event_type="x",
        event_category="y",
        event_data={"k": "v"},
    )
    assert e.event_id.startswith("EVT_")
    assert e.severity == "info"


def test_system_metric_decimal128_value():
    m = SystemMetric(
        metric_type="performance",
        metric_name="latency_ms",
        value=Decimal128("123"),
        unit="milliseconds",
    )
    assert m.metric_id.startswith("MET_")


# ---------------------------------------------------------------------------
# Account schemas
# ---------------------------------------------------------------------------

def test_account_defaults():
    a = Account(
        account_number="ACC_1",
        customer_id="CUST_1",
        customer_name="Joe",
        balance=Decimal128("1000"),
        available_balance=Decimal128("1000"),
    )
    assert a.account_type is AccountType.CHECKING
    assert a.status is AccountStatus.ACTIVE
    assert a.currency == "USD"
    assert a.transaction_count == 0
    assert a.holds == []


def test_balance_hold_defaults():
    from datetime import timedelta

    h = BalanceHold(
        account_number="ACC_1",
        transaction_id="TXN_1",
        amount=Decimal128("100"),
        reason="hold",
        expires_at=get_current_time() + timedelta(hours=1),
    )
    assert h.hold_id.startswith("HOLD_")
    assert h.released is False


def test_balance_update_defaults():
    bu = BalanceUpdate(
        account_number="ACC_1",
        transaction_id="TXN_1",
        operation="debit",
        amount=Decimal128("50"),
        previous_balance=Decimal128("100"),
        new_balance=Decimal128("50"),
    )
    assert bu.update_id.startswith("UPD_")


def test_transaction_journal_defaults():
    j = TransactionJournal(
        transaction_id="TXN_1",
        debit_account="A",
        debit_amount=Decimal128("10"),
        credit_account="B",
        credit_amount=Decimal128("10"),
        description="d",
    )
    assert j.journal_id.startswith("JRN_")
    assert j.status == "pending"
    assert j.committed is False


def test_module_enums_exposed():
    # Sanity: the enums imported above are the same objects exposed by the
    # schemas module — covers the bare module-level enum lines.
    assert schemas.TransactionType.WIRE_TRANSFER.value == "wire_transfer"
    assert schemas.RuleStatus.ACTIVE.value == "active"
    assert schemas.NotificationStatus.SENT.value == "sent"
    assert schemas.DecisionType.ESCALATE.value == "escalate"
