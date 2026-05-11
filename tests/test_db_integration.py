"""Integration tests for database/* against a real MongoDB.

Spec: .specs/test-coverage-fill/refactor.md (REQ-R-302)

Uses the ``mongo_container`` fixture (testcontainers). Tests are
skipped (not failed) if Docker is unavailable. Single-node replica
set is required for ACID transaction code paths.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from bson import Decimal128

from database.account_repository import (
    AccountNotFoundError,
    AccountRepository,
    InsufficientFundsError,
)
from database.account_schemas import Account, AccountStatus, AccountType
from database.repositories import (
    AuditRepository,
    CustomerRepository,
    DecisionRepository,
    HumanReviewRepository,
    MetricsRepository,
    NotificationRepository,
    RuleRepository,
    TransactionRepository,
    serialize_doc,
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
)
from utils.decimal_utils import to_decimal128


# ---------------------------------------------------------------------------
# serialize_doc: pure helper, gets exercised here once
# ---------------------------------------------------------------------------

def test_serialize_doc_returns_none_for_none():
    assert serialize_doc(None) is None


def test_serialize_doc_translates_known_types():
    from bson import ObjectId

    oid = ObjectId()
    now = datetime.now(timezone.utc)
    doc = {
        "_id": oid,
        "ref": ObjectId(),
        "amount": Decimal128("9.99"),
        "ts": now,
        "nested": {"x": now, "id": oid, "amt": Decimal128("1")},
        "list_field": [oid, now, Decimal128("2"), {"k": Decimal128("3")}, "raw"],
    }
    out = serialize_doc(doc)
    assert isinstance(out["_id"], str)
    assert isinstance(out["ref"], str)
    assert out["amount"] == "9.99"
    assert out["ts"] == now.isoformat()
    assert out["nested"]["amt"] == "1"
    assert out["list_field"][0] == str(oid)
    assert out["list_field"][1] == now.isoformat()
    assert out["list_field"][2] == "2"
    assert out["list_field"][3] == {"k": "3"}
    assert out["list_field"][4] == "raw"


# ---------------------------------------------------------------------------
# Connection: index creation idempotency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connect_creates_indexes_idempotently(async_mongo):
    """connect_to_mongo + create_indexes must be safe to call twice."""
    from database import connection as db_conn
    # async_mongo fixture already ran connect_to_mongo + create_indexes once.
    # Run create_indexes again — it must succeed without raising.
    await db_conn.create_indexes()


@pytest.mark.asyncio
async def test_close_mongo_connection_when_already_closed(monkeypatch):
    """close_mongo_connection logs and returns when client is None."""
    from database import connection as db_conn

    # Stash and clear current client, then re-set after.
    saved = db_conn.db.client
    db_conn.db.client = None
    try:
        await db_conn.close_mongo_connection()  # must not raise
    finally:
        db_conn.db.client = saved


@pytest.mark.asyncio
async def test_idempotent_index_helper_handles_stale_specs(async_mongo):
    """``_create_index_idempotent`` must drop and recreate when an index
    with the same name exists with a different key (code 86)."""
    from database import connection as db_conn

    coll = async_mongo["test_idempotent_helper"]
    await coll.create_index([("a", 1)], name="my_idx")
    # Now request the same name with a different key spec — code 86.
    await db_conn._create_index_idempotent(
        coll, [("b", 1)], name="my_idx",
    )
    cursor = await coll.list_indexes()
    indexes = await cursor.to_list(length=None)
    target = next((i for i in indexes if i["name"] == "my_idx"), None)
    assert target is not None
    assert list(target["key"].items()) == [("b", 1)]


@pytest.mark.asyncio
async def test_idempotent_index_helper_handles_options_conflict(async_mongo):
    """Same key but different name (code 85) -> drop existing, recreate."""
    from database import connection as db_conn

    coll = async_mongo["test_idempotent_options"]
    await coll.create_index([("c", 1)], name="auto_name")
    await db_conn._create_index_idempotent(
        coll, [("c", 1)], name="desired_name",
    )
    cursor = await coll.list_indexes()
    indexes = await cursor.to_list(length=None)
    names = {i["name"] for i in indexes}
    assert "desired_name" in names
    assert "auto_name" not in names


# ---------------------------------------------------------------------------
# CustomerRepository
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_customer_create_and_get(async_mongo):
    cust = Customer(
        legal_name="Alice", display_name="Alice", customer_type="individual", country="US",
    )
    cid = await CustomerRepository.create_customer(cust)
    fetched = await CustomerRepository.get_customer(cid)
    assert fetched["customer_id"] == cid


def test_customer_get_or_create_sync(mongo_db):
    cid = CustomerRepository.get_or_create_customer_sync({"name": "Acme Corp", "country": "US"})
    # Re-call returns the same id
    cid2 = CustomerRepository.get_or_create_customer_sync({"name": "Acme Corp"})
    assert cid == cid2


@pytest.mark.asyncio
async def test_customer_get_or_create_async(async_mongo):
    cid = await CustomerRepository.get_or_create_customer({"name": "Beta Ltd", "country": "GB"})
    cid2 = await CustomerRepository.get_or_create_customer({"name": "Beta Ltd"})
    assert cid == cid2


def test_customer_get_sync_missing_returns_none(mongo_db):
    assert CustomerRepository.get_customer_sync("MISSING") is None


@pytest.mark.asyncio
async def test_update_customer_profile(mongo_db):
    """update_customer_profile uses the sync client; we just need it not
    to raise and to upsert the document."""
    await CustomerRepository.update_customer_profile(
        customer_id="CUST_TEST",
        transaction_count=3,
        total_amount=300.0,
        avg_amount=100.0,
        last_transaction_date=datetime.now(timezone.utc),
    )
    doc = mongo_db.customers.find_one({"customer_id": "CUST_TEST"})
    assert doc["transaction_count"] == 3


# ---------------------------------------------------------------------------
# TransactionRepository
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transaction_create_and_status(async_mongo):
    txn = Transaction(
        transaction_type=TransactionType.WIRE_TRANSFER,
        amount=Decimal128("500"),
        sender={"name": "S", "country": "US"},
        recipient={"name": "R", "country": "GB"},
    )
    tid = await TransactionRepository.create_transaction(txn)
    fetched = await TransactionRepository.get_transaction(tid)
    assert fetched is not None
    await TransactionRepository.update_status(tid, "approved", substatus="ok")
    after = await TransactionRepository.get_transaction(tid)
    assert after["status"] == "approved"


def test_transaction_update_status_sync(mongo_db):
    mongo_db.transactions.insert_one({"transaction_id": "TXN_S", "status": "pending", "processing_stages": []})
    TransactionRepository.update_status_sync("TXN_S", "approved", substatus="ok")
    doc = mongo_db.transactions.find_one({"transaction_id": "TXN_S"})
    assert doc["status"] == "approved"
    assert doc["processing_stages"][0]["substatus"] == "ok"


@pytest.mark.asyncio
async def test_transaction_get_missing_returns_none(async_mongo):
    assert await TransactionRepository.get_transaction("MISSING") is None


@pytest.mark.asyncio
async def test_store_embedding_async_and_sync(async_mongo, mongo_db):
    txn = Transaction(
        transaction_type=TransactionType.ACH,
        amount=Decimal128("10"),
        sender={"name": "S"},
        recipient={"name": "R"},
    )
    tid = await TransactionRepository.create_transaction(txn)
    await TransactionRepository.store_embedding(tid, [0.1, 0.2], model="test-model")
    doc = await async_mongo.transactions.find_one({"transaction_id": tid})
    assert doc["embedding"] == [0.1, 0.2]
    assert doc["embedding_model"] == "test-model"


def test_store_embedding_sync(mongo_db):
    mongo_db.transactions.insert_one({"transaction_id": "TXN_E", "status": "pending"})
    TransactionRepository.store_embedding_sync("TXN_E", [0.5], model="m")
    doc = mongo_db.transactions.find_one({"transaction_id": "TXN_E"})
    assert doc["embedding"] == [0.5]


def test_get_customer_history_with_existing_customer(mongo_db):
    """Customer + 90-day transactions -> populated history dict."""
    now = datetime.now(timezone.utc)
    mongo_db.customers.insert_one({
        "customer_id": "CUST_H1",
        "created_at": now,
        "risk_profile": {"risk_level": "low", "kyc_status": "approved"},
        "behavior_profile": {
            "avg_transaction_amount": 100,
            "transaction_frequency": "weekly",
            "common_recipients": ["Bob"],
        },
    })
    mongo_db.transactions.insert_many([
        {
            "transaction_id": f"TXN_H_{i}",
            "sender": {"customer_id": "CUST_H1"},
            "recipient": {"name": f"Recipient_{i}"},
            "amount": Decimal128(str(100 * (i + 1))),
            "status": "approved" if i % 2 == 0 else "rejected",
            "created_at": now - timedelta(days=i),
        }
        for i in range(3)
    ])
    h = TransactionRepository.get_customer_history_sync("CUST_H1")
    assert h["total_transactions"] == 3
    assert h["risk_incidents"] == 1  # 1 rejected
    assert h["risk_level"] == "low"


def test_get_customer_history_unknown_customer(mongo_db):
    h = TransactionRepository.get_customer_history_sync("NOPE")
    assert h["risk_level"] == "unknown"
    assert h["total_transactions"] == 0


# ---------------------------------------------------------------------------
# DecisionRepository (non-vector paths)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_decision_create_and_get(async_mongo):
    d = TransactionDecision(
        transaction_id="TXN_D1",
        decision=DecisionType.APPROVE,
        confidence_score=Decimal128("90"),
        risk_score=Decimal128("30"),
        processing_time_ms=10,
        reasoning={"primary_reasoning": "ok"},
    )
    did = await DecisionRepository.create_decision(d)
    fetched = await DecisionRepository.get_decision_by_transaction("TXN_D1")
    assert fetched["decision_id"] == did


def test_decision_create_sync(mongo_db):
    d = TransactionDecision(
        transaction_id="TXN_D_S",
        decision=DecisionType.APPROVE,
        confidence_score=Decimal128("80"),
        risk_score=Decimal128("20"),
        processing_time_ms=5,
        reasoning={"primary_reasoning": "ok"},
    )
    did = DecisionRepository.create_decision_sync(d)
    assert did
    assert mongo_db.transaction_decisions.count_documents({"transaction_id": "TXN_D_S"}) == 1


def test_hybrid_search_traditional_only_path(mongo_db):
    """No embedding => the pipeline takes the traditional-only branch."""
    mongo_db.transactions.insert_many([
        {
            "transaction_id": "TXN_HS_1",
            "transaction_type": "wire_transfer",
            "amount": 1000,
            "status": "approved",
            "sender": {"country": "US"},
            "recipient": {"country": "GB"},
            "created_at": datetime.now(timezone.utc),
        },
    ])
    mongo_db.transaction_decisions.insert_one({
        "transaction_id": "TXN_HS_1",
        "decision": "approve",
        "confidence_score": 95,
        "risk_score": 20,
        "risk_factors": [],
    })
    results = asyncio.run(DecisionRepository.hybrid_search_similar_transactions(
        embedding=None,
        transaction_details={
            "transaction_type": "wire_transfer",
            "amount": 1000,
            "sender": {"country": "US"},
            "recipient": {"country": "GB"},
        },
        limit=5,
    ))
    assert len(results) >= 1


def test_graph_network_analysis_no_data(mongo_db):
    """No matching transactions -> 'No transaction networks' fast-path."""
    out = asyncio.run(DecisionRepository.graph_network_analysis(account_id="UNKNOWN"))
    assert out["total_networks_found"] == 0


def test_graph_network_analysis_with_chain(mongo_db):
    now = datetime.now(timezone.utc)
    mongo_db.transactions.insert_many([
        {
            "transaction_id": "GR_1",
            "sender": {"account_number": "A1"},
            "recipient": {"account_number": "A2"},
            "amount": 500,
            "created_at": now,
            "status": "approved",
        },
        {
            "transaction_id": "GR_2",
            "sender": {"account_number": "A2"},
            "recipient": {"account_number": "A3"},
            "amount": 400,
            "created_at": now,
            "status": "approved",
        },
    ])
    out = asyncio.run(DecisionRepository.graph_network_analysis(
        account_id="A1", max_depth=2, time_window_days=7,
    ))
    # The pipeline returns aggregate stats on the rooted account.
    assert "account_id" in out
    assert out["account_id"] == "A1"


# ---------------------------------------------------------------------------
# RuleRepository
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rule_create_and_active_async(async_mongo):
    r = Rule(
        name="r1", description="d", category="amount",
        conditions={"operator": "AND", "conditions": []},
        action="approve", priority=10, status=RuleStatus.ACTIVE,
    )
    rid = await RuleRepository.create_rule(r)
    rules = await RuleRepository.get_active_rules()
    assert any(x["rule_id"] == rid for x in rules)


def test_rule_get_active_sync(mongo_db):
    mongo_db.rules.insert_one({
        "rule_id": "RULE_X", "name": "x", "status": "active",
        "category": "amount", "action": "approve", "priority": 5,
        "conditions": {"operator": "AND", "conditions": []},
    })
    rules = RuleRepository.get_active_rules_sync()
    assert any(r["rule_id"] == "RULE_X" for r in rules)


@pytest.mark.asyncio
async def test_rule_update_metrics_async(async_mongo):
    r = Rule(
        name="r2", description="d", category="amount",
        conditions={"operator": "AND", "conditions": []},
        action="approve",
    )
    rid = await RuleRepository.create_rule(r)
    await RuleRepository.update_rule_metrics(rid, triggered=True, correct=True)
    doc = await async_mongo.rules.find_one({"rule_id": rid})
    assert doc["metrics"]["triggered_count"] == 1
    assert doc["metrics"]["true_positives"] == 1


def test_rule_update_metrics_sync(mongo_db):
    mongo_db.rules.insert_one({
        "rule_id": "RULE_M", "metrics": {
            "triggered_count": 0, "true_positives": 0, "false_positives": 0,
        },
    })
    RuleRepository.update_rule_metrics_sync("RULE_M", triggered=True, correct=False)
    doc = mongo_db.rules.find_one({"rule_id": "RULE_M"})
    assert doc["metrics"]["triggered_count"] == 1
    assert doc["metrics"]["false_positives"] == 1


# ---------------------------------------------------------------------------
# HumanReviewRepository / NotificationRepository / AuditRepository / MetricsRepository
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_human_review_lifecycle(async_mongo):
    review = HumanReview(transaction_id="TXN_HR")
    rid = await HumanReviewRepository.create_review(review)
    pending = await HumanReviewRepository.get_pending_reviews(limit=10)
    assert any(r["review_id"] == rid for r in pending)
    await HumanReviewRepository.update_review(rid, "approve", "ok", "alice")
    doc = await async_mongo.human_reviews.find_one({"review_id": rid})
    assert doc["status"] == "completed"
    assert doc["human_decision"]["reviewer"] == "alice"


def test_human_review_sync_methods(mongo_db):
    review = HumanReview(transaction_id="TXN_HR_S")
    rid = HumanReviewRepository.create_review_sync_obj(review)
    rid_dict = HumanReviewRepository.create_review_sync({
        "review_id": "REV_X",
        "transaction_id": "TXN_HR_S2",
    })
    HumanReviewRepository.complete_review_sync(rid, "reject", "bob", notes="n")
    doc = mongo_db.human_reviews.find_one({"review_id": rid})
    assert doc["status"] == "completed"
    assert rid_dict == "REV_X"


@pytest.mark.asyncio
async def test_notification_lifecycle(async_mongo):
    n = Notification(
        notification_type="decision",
        subject="s", message="m",
        recipients=[{"type": "email", "identifier": "x@y"}],
    )
    nid = await NotificationRepository.create_notification(n)
    pending = await NotificationRepository.get_pending_notifications()
    assert any(p["notification_id"] == nid for p in pending)
    await NotificationRepository.mark_as_sent(nid)
    doc = await async_mongo.notifications.find_one({"notification_id": nid})
    assert doc["status"] == NotificationStatus.SENT.value


def test_notification_sync_methods(mongo_db):
    n = Notification(notification_type="alert", subject="s", message="m")
    nid = NotificationRepository.create_notification_sync_obj(n)
    nid_dict = NotificationRepository.create_notification_sync({
        "notification_id": "NOTIF_X", "subject": "s", "message": "m",
        "status": "pending",
    })
    NotificationRepository.mark_as_sent_sync(nid)
    doc = mongo_db.notifications.find_one({"notification_id": nid})
    assert doc["status"] == NotificationStatus.SENT.value
    assert nid_dict == "NOTIF_X"


@pytest.mark.asyncio
async def test_audit_lifecycle(async_mongo):
    e = AuditEvent(event_type="t", event_category="c", event_data={})
    eid = await AuditRepository.create_audit_event(e)
    events = await AuditRepository.get_recent_events(limit=10)
    assert any(ev["event_id"] == eid for ev in events)
    # With transaction_id filter
    e2 = AuditEvent(event_type="t", event_category="c", event_data={}, transaction_id="TXN_AUD")
    await AuditRepository.create_audit_event(e2)
    filtered = await AuditRepository.get_recent_events(transaction_id="TXN_AUD")
    assert all(ev.get("transaction_id") == "TXN_AUD" for ev in filtered)


def test_audit_sync(mongo_db):
    e = AuditEvent(event_type="t", event_category="c", event_data={})
    eid = AuditRepository.create_audit_event_sync(e)
    assert mongo_db.audit_events.count_documents({"event_id": eid}) == 1


@pytest.mark.asyncio
async def test_metrics_record_and_query(async_mongo):
    m = SystemMetric(
        metric_type="performance", metric_name="latency", value=Decimal128("100"),
        unit="ms",
    )
    await MetricsRepository.record_metric(m)
    recent = await MetricsRepository.get_recent_metrics("latency", minutes=60)
    assert any(r["metric_id"] == m.metric_id for r in recent)
    agg = await MetricsRepository.get_aggregated_metrics(hours=1)
    assert "latency" in agg


def test_metrics_record_sync(mongo_db):
    m = SystemMetric(
        metric_type="performance", metric_name="ms", value=Decimal128("1"), unit="ms",
    )
    MetricsRepository.record_metric_sync(m)
    assert mongo_db.system_metrics.count_documents({"metric_id": m.metric_id}) == 1


# ---------------------------------------------------------------------------
# AccountRepository (sync; ACID transfer requires replica set)
# ---------------------------------------------------------------------------

def test_account_get_or_create_and_balance(mongo_db):
    acct = AccountRepository.get_or_create_account_sync(
        account_number="ACC_X", customer_name="Joe",
        initial_balance=1000.0,
    )
    assert isinstance(acct, Account)
    bal = AccountRepository.get_account_balance_sync("ACC_X")
    assert bal["balance"] is not None

    # Re-call returns existing account
    acct2 = AccountRepository.get_or_create_account_sync(
        account_number="ACC_X", customer_name="Joe", initial_balance=99,
    )
    assert acct2.account_number == "ACC_X"


def test_account_balance_missing_raises(mongo_db):
    with pytest.raises(AccountNotFoundError):
        AccountRepository.get_account_balance_sync("NOPE")


def test_get_account_sync_returns_full_account(mongo_db):
    AccountRepository.get_or_create_account_sync(
        account_number="ACC_FULL", customer_name="Joe", initial_balance=1000.0,
    )
    a = AccountRepository.get_account_sync("ACC_FULL")
    assert a.account_number == "ACC_FULL"


def test_get_account_sync_missing_raises(mongo_db):
    with pytest.raises(AccountNotFoundError):
        AccountRepository.get_account_sync("NOPE")


def test_check_sufficient_funds(mongo_db):
    AccountRepository.get_or_create_account_sync(
        account_number="ACC_F", customer_name="Joe", initial_balance=1000.0,
    )
    has, available = AccountRepository.check_sufficient_funds_sync("ACC_F", 500)
    assert has is True
    assert available >= 500
    has2, _ = AccountRepository.check_sufficient_funds_sync("ACC_F", 10000)
    assert has2 is False


def test_check_sufficient_funds_missing_account(mongo_db):
    with pytest.raises(AccountNotFoundError):
        AccountRepository.check_sufficient_funds_sync("NOPE", 1)


def test_place_and_release_hold(mongo_db):
    AccountRepository.get_or_create_account_sync(
        account_number="ACC_H", customer_name="Joe", initial_balance=1000.0,
    )
    hold_id = AccountRepository.place_hold_sync(
        account_number="ACC_H", amount=200, transaction_id="TXN_H1",
    )
    assert hold_id.startswith("HOLD_")
    bal = AccountRepository.get_account_balance_sync("ACC_H")
    # Hold reduces available
    from utils.decimal_utils import from_decimal128

    assert from_decimal128(bal["available_balance"]) < from_decimal128(bal["balance"])

    # Release
    assert AccountRepository.release_hold_sync(hold_id) is True
    # Releasing again returns False (already released)
    assert AccountRepository.release_hold_sync(hold_id) is False
    # Releasing an unknown hold returns False
    assert AccountRepository.release_hold_sync("HOLD_MISSING") is False


def test_place_hold_insufficient_funds(mongo_db):
    AccountRepository.get_or_create_account_sync(
        account_number="ACC_I", customer_name="Joe", initial_balance=10.0,
    )
    with pytest.raises(InsufficientFundsError):
        AccountRepository.place_hold_sync("ACC_I", 1000, "TXN_X")


def test_place_hold_missing_account(mongo_db):
    with pytest.raises(AccountNotFoundError):
        AccountRepository.place_hold_sync("NOPE", 1, "TXN_X")


def test_execute_transfer_with_acid(mongo_db):
    AccountRepository.get_or_create_account_sync(
        account_number="ACC_TR_S", customer_name="Sender", initial_balance=1000.0,
    )
    AccountRepository.get_or_create_account_sync(
        account_number="ACC_TR_R", customer_name="Recipient", initial_balance=100.0,
    )
    ok = AccountRepository.execute_transfer_with_acid(
        sender_account="ACC_TR_S",
        recipient_account="ACC_TR_R",
        amount=200.0,
        transaction_id="TXN_TR_OK",
    )
    assert ok is True

    s_bal = AccountRepository.get_account_balance_sync("ACC_TR_S")
    r_bal = AccountRepository.get_account_balance_sync("ACC_TR_R")
    from utils.decimal_utils import from_decimal128
    assert from_decimal128(s_bal["balance"]) == 800
    assert from_decimal128(r_bal["balance"]) == 300


def test_execute_transfer_insufficient_funds(mongo_db):
    AccountRepository.get_or_create_account_sync(
        account_number="ACC_I_S", customer_name="S", initial_balance=10.0,
    )
    AccountRepository.get_or_create_account_sync(
        account_number="ACC_I_R", customer_name="R", initial_balance=10.0,
    )
    with pytest.raises(InsufficientFundsError):
        AccountRepository.execute_transfer_with_acid(
            sender_account="ACC_I_S",
            recipient_account="ACC_I_R",
            amount=1000,
            transaction_id="TXN_INSUF",
        )


def test_execute_transfer_missing_sender(mongo_db):
    AccountRepository.get_or_create_account_sync(
        account_number="ACC_RR", customer_name="R", initial_balance=10.0,
    )
    with pytest.raises(AccountNotFoundError):
        AccountRepository.execute_transfer_with_acid(
            sender_account="MISSING", recipient_account="ACC_RR",
            amount=1, transaction_id="TXN_M",
        )


def test_execute_transfer_missing_recipient(mongo_db):
    AccountRepository.get_or_create_account_sync(
        account_number="ACC_SS", customer_name="S", initial_balance=100.0,
    )
    with pytest.raises(AccountNotFoundError):
        AccountRepository.execute_transfer_with_acid(
            sender_account="ACC_SS", recipient_account="MISSING",
            amount=1, transaction_id="TXN_M2",
        )


def test_execute_transfer_subtract_money_failure_logs_and_raises(mongo_db, monkeypatch):
    """Defensive try/except around subtract_money inside the ACID callback."""
    AccountRepository.get_or_create_account_sync(
        account_number="ACC_SUB_S", customer_name="S", initial_balance=1000.0,
    )
    AccountRepository.get_or_create_account_sync(
        account_number="ACC_SUB_R", customer_name="R", initial_balance=10.0,
    )

    from database import account_repository as ar_mod

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(ar_mod, "subtract_money", boom)
    with pytest.raises(RuntimeError, match="synthetic"):
        AccountRepository.execute_transfer_with_acid(
            sender_account="ACC_SUB_S", recipient_account="ACC_SUB_R",
            amount=10, transaction_id="TXN_SYN",
        )


def test_hybrid_search_fallback_to_vector_on_failure(mongo_db, monkeypatch):
    """When the hybrid pipeline raises, hybrid_search falls back to
    vector_search_similar_transactions if an embedding was provided."""
    from database import repositories as repo_mod
    from unittest.mock import patch, MagicMock, AsyncMock

    fake_collection = MagicMock()
    fake_collection.aggregate.side_effect = RuntimeError("vectorSearch unsupported")

    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection

    monkeypatch.setattr(repo_mod, "get_sync_db", lambda: fake_db)
    with patch.object(
        repo_mod.DecisionRepository,
        "vector_search_similar_transactions",
        AsyncMock(return_value=[{"transaction_id": "FAKE"}]),
    ) as mock_fallback:
        results = asyncio.run(repo_mod.DecisionRepository.hybrid_search_similar_transactions(
            embedding=[0.1] * 8,
            transaction_details={
                "transaction_type": "wire_transfer",
                "amount": 100,
                "sender": {"country": "US"},
                "recipient": {"country": "GB"},
            },
            limit=4,
        ))
    mock_fallback.assert_awaited_once()
    assert results == [{"transaction_id": "FAKE"}]


def test_hybrid_search_no_embedding_propagates_failure(mongo_db, monkeypatch):
    """No embedding -> hybrid pipeline failure re-raises (no fallback)."""
    from database import repositories as repo_mod
    from unittest.mock import MagicMock

    fake_collection = MagicMock()
    fake_collection.aggregate.side_effect = RuntimeError("agg down")
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection

    monkeypatch.setattr(repo_mod, "get_sync_db", lambda: fake_db)
    with pytest.raises(RuntimeError, match="agg down"):
        asyncio.run(repo_mod.DecisionRepository.hybrid_search_similar_transactions(
            embedding=None,
            transaction_details={
                "transaction_type": "wire_transfer", "amount": 100,
                "sender": {}, "recipient": {},
            },
            limit=4,
        ))


def test_vector_search_propagates_pipeline_error(mongo_db, monkeypatch):
    """vector_search_similar_transactions raises when aggregate fails."""
    from database import repositories as repo_mod
    from unittest.mock import MagicMock

    fake_collection = MagicMock()
    fake_collection.aggregate.side_effect = RuntimeError("no $vectorSearch")
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection
    monkeypatch.setattr(repo_mod, "get_sync_db", lambda: fake_db)

    with pytest.raises(RuntimeError, match="no \\$vectorSearch"):
        asyncio.run(repo_mod.DecisionRepository.vector_search_similar_transactions(
            embedding=[0.1], transaction_type="wire_transfer", limit=2,
        ))


def test_vector_search_returns_serialised_results(mongo_db, monkeypatch):
    from database import repositories as repo_mod
    from unittest.mock import MagicMock

    fake_collection = MagicMock()
    fake_collection.aggregate.return_value = iter([
        {"transaction_id": "T1", "amount": 100},
    ])
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection
    monkeypatch.setattr(repo_mod, "get_sync_db", lambda: fake_db)

    out = asyncio.run(repo_mod.DecisionRepository.vector_search_similar_transactions(
        embedding=[0.1], transaction_type="ach", limit=1,
    ))
    assert out == [{"transaction_id": "T1", "amount": 100}]


def test_graph_network_analysis_propagates_pipeline_error(mongo_db, monkeypatch):
    from database import repositories as repo_mod
    from unittest.mock import MagicMock

    fake_collection = MagicMock()
    fake_collection.aggregate.side_effect = RuntimeError("graph agg down")
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection
    monkeypatch.setattr(repo_mod, "get_sync_db", lambda: fake_db)

    with pytest.raises(RuntimeError, match="graph agg down"):
        asyncio.run(repo_mod.DecisionRepository.graph_network_analysis(
            account_id="A1", max_depth=2, time_window_days=7,
        ))


@pytest.mark.asyncio
async def test_update_customer_profile_propagates_failure(monkeypatch):
    """The except branch in update_customer_profile re-raises."""
    from database import repositories as repo_mod
    from unittest.mock import MagicMock

    fake_collection = MagicMock()
    fake_collection.update_one.side_effect = RuntimeError("write fail")
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection
    monkeypatch.setattr(repo_mod, "get_sync_db", lambda: fake_db)

    with pytest.raises(RuntimeError, match="write fail"):
        await repo_mod.CustomerRepository.update_customer_profile(
            customer_id="X", transaction_count=1,
            total_amount=1, avg_amount=1,
            last_transaction_date=datetime.now(timezone.utc),
        )


@pytest.mark.asyncio
async def test_connect_to_mongo_propagates_failure(monkeypatch):
    """connect_to_mongo wraps the AsyncMongoClient construction in try/except
    that re-raises after logging."""
    from database import connection as db_conn

    saved_client = db_conn.db.client
    saved_db = db_conn.db.database
    db_conn.db.client = None
    db_conn.db.database = None

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("dns explode")

    monkeypatch.setattr(db_conn, "AsyncMongoClient", Boom)
    try:
        with pytest.raises(RuntimeError, match="dns explode"):
            await db_conn.connect_to_mongo()
    finally:
        db_conn.db.client = saved_client
        db_conn.db.database = saved_db


@pytest.mark.asyncio
async def test_idempotent_index_helper_reraises_unrelated_failure():
    """_create_index_idempotent only handles codes 85 and 86; other codes
    re-raise."""
    from unittest.mock import AsyncMock, MagicMock
    from pymongo.errors import OperationFailure

    from database import connection as db_conn

    coll = MagicMock()

    async def raise_other(*a, **k):
        raise OperationFailure("nope", code=42)

    coll.create_index = AsyncMock(side_effect=raise_other)
    with pytest.raises(OperationFailure):
        await db_conn._create_index_idempotent(coll, [("x", 1)], name="idx")


@pytest.mark.asyncio
async def test_close_mongo_connection_when_real_client(monkeypatch):
    """close_mongo_connection awaits client.close(); covers the active path."""
    from database import connection as db_conn

    if db_conn.db.client is None:
        # Connect then close
        await db_conn.connect_to_mongo()
    await db_conn.close_mongo_connection()
    assert db_conn.db.client is None


def test_get_transaction_history_sync(mongo_db):
    mongo_db.balance_updates.insert_many([
        {
            "account_number": "ACC_TH",
            "operation": "credit",
            "amount": Decimal128("10"),
            "timestamp": datetime.now(timezone.utc) - timedelta(minutes=i),
        }
        for i in range(3)
    ])
    history = AccountRepository.get_transaction_history_sync("ACC_TH", limit=2)
    assert len(history) == 2
