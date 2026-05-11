"""Unit tests for temporal/activities.

Spec: .specs/test-coverage-fill/refactor.md (REQ-R-301, REQ-R-303)

Activities are tested as plain async functions (not via the Temporal
runtime). All repository and AI-client interactions are mocked so the
tests are fast and deterministic.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import Decimal128

from temporal.activities import TransactionActivities
from temporal.shared import (
    InsufficientDataError,
    RiskAssessment,
    TransactionDetails,
)


def _details(**overrides):
    base = {
        "transaction_id": "TXN_TEST",
        "transaction_type": "wire_transfer",
        "amount": "1000",
        "currency": "USD",
        "sender": {
            "name": "Alice", "country": "US",
            "account_number": "ACC_S", "customer_id": "CUST_1",
        },
        "recipient": {
            "name": "Bob", "country": "GB",
            "account_number": "ACC_R",
        },
        "reference_number": "REF",
        "risk_flags": [],
        "metadata": {},
    }
    base.update(overrides)
    return TransactionDetails(**base)


# ---------------------------------------------------------------------------
# validate_and_hold_funds
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_account_repo():
    with patch("temporal.activities.AccountRepository") as mock:
        sender_acct = MagicMock(account_number="ACC_S", balance=Decimal128("500000"))
        recipient_acct = MagicMock(account_number="ACC_R", balance=Decimal128("50000"))
        mock.get_or_create_account_sync.side_effect = [sender_acct, recipient_acct]
        mock.check_sufficient_funds_sync.return_value = (True, 500000.0)
        mock.place_hold_sync.return_value = "HOLD_X"
        yield mock


@pytest.mark.asyncio
async def test_validate_and_hold_funds_success(mock_account_repo):
    a = TransactionActivities()
    result = await a.validate_and_hold_funds(_details())
    assert result["validation_status"] == "success"
    assert result["hold_id"] == "HOLD_X"


@pytest.mark.asyncio
async def test_validate_and_hold_funds_insufficient(mock_account_repo):
    """Import InsufficientFundsError from temporal.activities's own binding so
    we match the same class instance the activity catches/raises, even when an
    earlier test has reloaded `database.account_repository`."""
    from temporal.activities import InsufficientFundsError
    mock_account_repo.check_sufficient_funds_sync.return_value = (False, 1.0)
    a = TransactionActivities()
    with pytest.raises(InsufficientFundsError):
        await a.validate_and_hold_funds(_details())


@pytest.mark.asyncio
async def test_validate_and_hold_funds_unexpected_error(mock_account_repo):
    mock_account_repo.get_or_create_account_sync.side_effect = RuntimeError("db down")
    a = TransactionActivities()
    with pytest.raises(RuntimeError):
        await a.validate_and_hold_funds(_details())


# ---------------------------------------------------------------------------
# enrich_transaction_data
# ---------------------------------------------------------------------------

@pytest.fixture
def enrich_mocks():
    with patch("temporal.activities.TransactionRepository") as txn_repo, \
         patch("temporal.activities.CustomerRepository") as cust_repo, \
         patch("temporal.activities.RuleEngine") as rule_engine:
        txn_repo.get_customer_history_sync.return_value = {
            "common_recipients": ["Bob"], "kyc_status": "approved",
        }
        cust_repo.get_or_create_customer_sync.return_value = "CUST_X"
        rule_engine.apply_rules.return_value = {
            "triggered_rules": [], "risk_flags": [], "recommended_action": None, "rule_count": 0,
        }
        yield {"txn_repo": txn_repo, "cust_repo": cust_repo, "rule_engine": rule_engine}


@pytest.mark.asyncio
async def test_enrich_basic_path_no_customer_id(enrich_mocks, monkeypatch):
    a = TransactionActivities()
    monkeypatch.setattr(a, "_calculate_velocity_metrics", lambda **kw: {"velocity_1h": 0, "velocity_24h": 0, "total_amount_1h": 0})
    details = _details(sender={"name": "Alice", "country": "US", "account_number": "ACC_S"})
    result = await a.enrich_transaction_data(details)
    assert "transaction" in result
    assert result["transaction"]["transaction_id"] == "TXN_TEST"


@pytest.mark.asyncio
async def test_enrich_high_amount_flag(enrich_mocks, monkeypatch):
    a = TransactionActivities()
    monkeypatch.setattr(a, "_calculate_velocity_metrics", lambda **kw: {"velocity_1h": 0})
    result = await a.enrich_transaction_data(_details(amount="60000"))
    assert "high_amount" in result["risk_flags"]


@pytest.mark.asyncio
async def test_enrich_structuring_pattern(enrich_mocks, monkeypatch):
    a = TransactionActivities()
    monkeypatch.setattr(a, "_calculate_velocity_metrics", lambda **kw: {"velocity_1h": 0})
    result = await a.enrich_transaction_data(_details(amount="4950"))
    assert "structuring_pattern" in result["risk_flags"]


@pytest.mark.asyncio
async def test_enrich_high_velocity_flag(enrich_mocks, monkeypatch):
    a = TransactionActivities()
    monkeypatch.setattr(a, "_calculate_velocity_metrics", lambda **kw: {
        "velocity_1h": 5, "velocity_24h": 12, "total_amount_1h": 200000,
    })
    result = await a.enrich_transaction_data(_details())
    assert "high_velocity_1h" in result["risk_flags"]
    assert "high_velocity_24h" in result["risk_flags"]
    assert "high_amount_velocity" in result["risk_flags"]


@pytest.mark.asyncio
async def test_enrich_international_high_risk_country(enrich_mocks, monkeypatch):
    a = TransactionActivities()
    monkeypatch.setattr(a, "_calculate_velocity_metrics", lambda **kw: {"velocity_1h": 0})
    details = _details(
        transaction_type="international",
        recipient={"name": "X", "country": "RU", "account_number": "ACC_X"},
    )
    result = await a.enrich_transaction_data(details)
    assert "cross_border" in result["risk_flags"]
    assert "high_risk_country" in result["risk_flags"]


@pytest.mark.asyncio
async def test_enrich_new_recipient(enrich_mocks, monkeypatch):
    enrich_mocks["txn_repo"].get_customer_history_sync.return_value = {
        "common_recipients": ["Bob"], "kyc_status": "approved",
    }
    a = TransactionActivities()
    monkeypatch.setattr(a, "_calculate_velocity_metrics", lambda **kw: {"velocity_1h": 0})
    details = _details(recipient={"name": "Charlie", "country": "GB", "account_number": "ACC_NEW"})
    result = await a.enrich_transaction_data(details)
    assert "new_recipient" in result["risk_flags"]


@pytest.mark.asyncio
async def test_enrich_failure_raises_insufficient_data(enrich_mocks, monkeypatch):
    a = TransactionActivities()
    enrich_mocks["txn_repo"].get_customer_history_sync.side_effect = RuntimeError("boom")
    with pytest.raises(InsufficientDataError):
        await a.enrich_transaction_data(_details())


@pytest.mark.asyncio
async def test_enrich_unusual_time(enrich_mocks, monkeypatch):
    """Force enrich path through the unusual-time branch by patching `datetime.now`
    in the activities module."""
    a = TransactionActivities()
    monkeypatch.setattr(a, "_calculate_velocity_metrics", lambda **kw: {"velocity_1h": 0})

    import temporal.activities as act_mod

    class FakeDT:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 5, 11, 3, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(act_mod, "datetime", FakeDT)
    result = await a.enrich_transaction_data(_details())
    assert "unusual_time" in result["risk_flags"]


# ---------------------------------------------------------------------------
# _calculate_velocity_metrics
# ---------------------------------------------------------------------------

def test_velocity_metrics_with_data(monkeypatch):
    a = TransactionActivities()
    fake_collection = MagicMock()
    now = datetime.now(timezone.utc)
    fake_collection.find.return_value = [
        {"amount": Decimal128("50"), "created_at": now - timedelta(minutes=30)},
        {"amount": Decimal128("60"), "created_at": now - timedelta(minutes=10)},
    ]
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection

    import database.connection as conn

    monkeypatch.setattr(conn, "get_sync_db", lambda: fake_db)
    metrics = a._calculate_velocity_metrics(customer_id="C1", sender_account="ACC_S")
    assert metrics["velocity_1h"] == 2
    assert metrics["velocity_24h"] == 2


def test_velocity_metrics_failure_returns_defaults(monkeypatch):
    a = TransactionActivities()
    import database.connection as conn

    monkeypatch.setattr(conn, "get_sync_db", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    out = a._calculate_velocity_metrics(customer_id="C1", sender_account="ACC_S")
    assert out["velocity_1h"] == 0


def test_velocity_metrics_handles_naive_datetime(monkeypatch):
    """An older transaction record may have a naive datetime; the function
    coerces it to UTC before subtraction."""
    a = TransactionActivities()
    fake_collection = MagicMock()
    naive = datetime(2026, 5, 11, 12, 0)  # no tzinfo
    fake_collection.find.return_value = [
        {"amount": Decimal128("10"), "created_at": naive},
    ]
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection

    import database.connection as conn

    monkeypatch.setattr(conn, "get_sync_db", lambda: fake_db)
    out = a._calculate_velocity_metrics(customer_id="C1", sender_account="ACC_S")
    assert out["time_since_last_seconds"] is not None


def test_velocity_metrics_with_missing_amount(monkeypatch):
    """Records with no amount must not break sum()."""
    a = TransactionActivities()
    fake_collection = MagicMock()
    fake_collection.find.return_value = [
        {"amount": None, "created_at": datetime.now(timezone.utc)},
    ]
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection

    import database.connection as conn

    monkeypatch.setattr(conn, "get_sync_db", lambda: fake_db)
    out = a._calculate_velocity_metrics(customer_id="C1", sender_account="ACC_S")
    assert out["total_amount_1h"] == 0


# ---------------------------------------------------------------------------
# perform_risk_assessment
# ---------------------------------------------------------------------------

def _enriched(**overrides):
    base = {
        "transaction": {
            "transaction_id": "TXN_TEST",
            "transaction_type": "wire_transfer",
            "amount": 1000,
            "currency": "USD",
            "sender": {"country": "US", "kyc_status": "approved"},
            "recipient": {"country": "GB"},
        },
        "rule_results": {"recommended_action": None, "triggered_rules": []},
        "customer_history": {"kyc_status": "approved"},
        "risk_flags": [],
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_risk_assessment_uses_groq_when_provider_groq(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(
        act_mod.groq_client, "analyze_transaction",
        AsyncMock(return_value={"risk_score": 40, "key_risk_factors": ["amount"]}),
    )
    monkeypatch.setattr(act_mod.MetricsRepository, "record_metric_sync", lambda *_: None)
    out = await a.perform_risk_assessment(_enriched())
    assert out["risk_score"] == 40


@pytest.mark.asyncio
async def test_risk_assessment_uses_bedrock_when_provider_other(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "bedrock")
    monkeypatch.setattr(
        act_mod.bedrock_client, "analyze_transaction",
        AsyncMock(return_value={"risk_score": 60, "key_risk_factors": []}),
    )
    monkeypatch.setattr(act_mod.MetricsRepository, "record_metric_sync", lambda *_: None)
    out = await a.perform_risk_assessment(_enriched())
    assert out["risk_score"] == 60


@pytest.mark.asyncio
async def test_risk_assessment_rule_reject_clamps_score(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(
        act_mod.groq_client, "analyze_transaction",
        AsyncMock(return_value={"risk_score": 30}),
    )
    monkeypatch.setattr(act_mod.MetricsRepository, "record_metric_sync", lambda *_: None)
    enriched = _enriched(rule_results={"recommended_action": "reject", "triggered_rules": ["R1"]})
    out = await a.perform_risk_assessment(enriched)
    assert out["risk_score"] >= 90


@pytest.mark.asyncio
async def test_risk_assessment_rule_escalate_clamps_score(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(
        act_mod.groq_client, "analyze_transaction",
        AsyncMock(return_value={"risk_score": 30}),
    )
    monkeypatch.setattr(act_mod.MetricsRepository, "record_metric_sync", lambda *_: None)
    enriched = _enriched(rule_results={"recommended_action": "escalate", "triggered_rules": []})
    out = await a.perform_risk_assessment(enriched)
    assert out["risk_score"] >= 70


@pytest.mark.asyncio
async def test_risk_assessment_international_runs_extra_compliance(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(
        act_mod.groq_client, "analyze_transaction",
        AsyncMock(return_value={"risk_score": 30}),
    )
    monkeypatch.setattr(act_mod.MetricsRepository, "record_metric_sync", lambda *_: None)
    enriched = _enriched()
    enriched["transaction"]["transaction_type"] = "international"
    enriched["transaction"]["recipient"]["country"] = "RU"
    out = await a.perform_risk_assessment(enriched)
    assert out["compliance_checks"]["ofac_check"] is False
    assert out["compliance_checks"]["fatf_check"] is True


@pytest.mark.asyncio
async def test_risk_assessment_metric_failure_swallowed(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(
        act_mod.groq_client, "analyze_transaction",
        AsyncMock(return_value={"risk_score": 30}),
    )
    monkeypatch.setattr(
        act_mod.MetricsRepository, "record_metric_sync",
        lambda *_: (_ for _ in ()).throw(RuntimeError("metric")),
    )
    out = await a.perform_risk_assessment(_enriched())
    assert out["risk_score"] == 30


@pytest.mark.asyncio
async def test_risk_assessment_full_failure_returns_high_risk(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(
        act_mod.groq_client, "analyze_transaction",
        AsyncMock(side_effect=RuntimeError("llm down")),
    )
    out = await a.perform_risk_assessment(_enriched())
    assert out.risk_score == 75
    assert out.risk_level == "high"


# ---------------------------------------------------------------------------
# find_similar_transactions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_similar_transactions_uses_embedding(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(
        act_mod.embedding_client, "prepare_transaction_text", lambda *a, **k: "text",
    )
    monkeypatch.setattr(
        act_mod.embedding_client, "get_embedding",
        AsyncMock(return_value=MagicMock(embedding=[0.1] * 8, model="m", dimensions=8)),
    )
    monkeypatch.setattr(
        act_mod.TransactionRepository, "store_embedding_sync", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        act_mod.DecisionRepository, "hybrid_search_similar_transactions",
        AsyncMock(return_value=[
            {"transaction_id": "S1", "similarity_score": "0.9"},
            {"transaction_id": "S2", "similarity_score": "0.5"},  # below threshold
            {"transaction_id": "S3", "similarity_score": "not-a-number"},
        ]),
    )
    out = await a.find_similar_transactions(_enriched())
    ids = [c["transaction_id"] for c in out]
    assert "S1" in ids
    assert "S2" not in ids


@pytest.mark.asyncio
async def test_find_similar_transactions_embedding_failure_uses_traditional(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(
        act_mod.embedding_client, "prepare_transaction_text", lambda *a, **k: "text",
    )
    monkeypatch.setattr(
        act_mod.embedding_client, "get_embedding",
        AsyncMock(side_effect=RuntimeError("embed down")),
    )
    monkeypatch.setattr(
        act_mod.DecisionRepository, "hybrid_search_similar_transactions",
        AsyncMock(return_value=[]),
    )
    out = await a.find_similar_transactions(_enriched())
    assert out == []


@pytest.mark.asyncio
async def test_find_similar_transactions_outer_failure_returns_empty(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(
        act_mod.embedding_client, "prepare_transaction_text",
        MagicMock(side_effect=RuntimeError("inner fail")),
    )
    monkeypatch.setattr(
        act_mod.DecisionRepository, "hybrid_search_similar_transactions",
        AsyncMock(side_effect=RuntimeError("outer fail")),
    )
    out = await a.find_similar_transactions(_enriched())
    assert out == []


# ---------------------------------------------------------------------------
# ai_decision_analysis
# ---------------------------------------------------------------------------

def _risk_assessment_obj():
    return RiskAssessment(
        risk_score=50, risk_level="medium",
        risk_factors=["x"], requires_enhanced_diligence=False,
        compliance_checks={"sanctions_check": True, "aml_check": True, "kyc_verified": True},
    )


@pytest.mark.asyncio
async def test_ai_decision_compliance_violation_rejects(monkeypatch):
    a = TransactionActivities()
    ra = _risk_assessment_obj()
    ra.compliance_checks["ofac_check"] = False  # critical
    out = await a.ai_decision_analysis(_enriched(), ra, [])
    assert out["decision"] == "reject"
    assert out["confidence"] == 100.0


@pytest.mark.asyncio
async def test_ai_decision_non_critical_compliance_failure_continues(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    ra = _risk_assessment_obj()
    ra.compliance_checks["aml_check"] = False  # non-critical
    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(
        act_mod.groq_client, "analyze_transaction",
        AsyncMock(return_value={"decision": "approve", "confidence": 90, "reasoning": "ok"}),
    )
    monkeypatch.setattr(act_mod.MetricsRepository, "record_metric_sync", lambda *_: None)
    out = await a.ai_decision_analysis(_enriched(), ra, [])
    assert out["decision"] == "approve"


@pytest.mark.asyncio
async def test_ai_decision_rule_recommended_reject(monkeypatch):
    a = TransactionActivities()
    enriched = _enriched(rule_results={"recommended_action": "reject", "triggered_rules": ["R1"]})
    out = await a.ai_decision_analysis(enriched, _risk_assessment_obj(), [])
    assert out["decision"] == "reject"
    assert out["confidence"] == 95.0


@pytest.mark.asyncio
async def test_ai_decision_low_confidence_overridden_by_rules(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(
        act_mod.groq_client, "analyze_transaction",
        AsyncMock(return_value={"decision": "approve", "confidence": 50, "reasoning": "low"}),
    )
    monkeypatch.setattr(act_mod.MetricsRepository, "record_metric_sync", lambda *_: None)
    enriched = _enriched(rule_results={
        "recommended_action": "hold",
        "triggered_rules": ["R1"],
    })
    out = await a.ai_decision_analysis(enriched, _risk_assessment_obj(), [])
    assert out["decision"] == "escalate"  # mapped from "hold"


@pytest.mark.asyncio
async def test_ai_decision_uses_bedrock_when_provider_not_groq(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "bedrock")
    monkeypatch.setattr(
        act_mod.bedrock_client, "analyze_transaction",
        AsyncMock(return_value={"decision": "approve", "confidence": 90, "reasoning": "ok"}),
    )
    monkeypatch.setattr(act_mod.MetricsRepository, "record_metric_sync", lambda *_: None)
    out = await a.ai_decision_analysis(_enriched(), _risk_assessment_obj(), [])
    assert out["decision"] == "approve"


@pytest.mark.asyncio
async def test_ai_decision_metric_failure_swallowed(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(
        act_mod.groq_client, "analyze_transaction",
        AsyncMock(return_value={"decision": "approve", "confidence": 90, "reasoning": "ok"}),
    )
    monkeypatch.setattr(
        act_mod.MetricsRepository, "record_metric_sync",
        lambda *_: (_ for _ in ()).throw(RuntimeError("metric down")),
    )
    out = await a.ai_decision_analysis(_enriched(), _risk_assessment_obj(), [])
    assert out["decision"] == "approve"


@pytest.mark.asyncio
async def test_ai_decision_full_failure_returns_escalate(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(
        act_mod.groq_client, "analyze_transaction",
        AsyncMock(side_effect=RuntimeError("llm down")),
    )
    monkeypatch.setattr(
        act_mod, "create_transaction_analysis_prompt",
        MagicMock(side_effect=RuntimeError("prompt boom")),
    )
    out = await a.ai_decision_analysis(_enriched(), _risk_assessment_obj(), [])
    assert out["decision"] == "escalate"


# ---------------------------------------------------------------------------
# store_decision
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_store_decision_success(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(act_mod.config, "GROQ_MODEL_ID", "m")
    monkeypatch.setattr(act_mod.DecisionRepository, "create_decision_sync",
                        lambda d: d.decision_id)
    monkeypatch.setattr(act_mod.AuditRepository, "create_audit_event_sync", lambda *_: "EVT_X")
    monkeypatch.setattr(act_mod.TransactionRepository, "update_status_sync", lambda *_, **__: None)
    monkeypatch.setattr(act_mod.RuleRepository, "update_rule_metrics_sync", lambda *_, **__: None)
    out = await a.store_decision(
        "TXN_X",
        {
            "decision": "approve", "confidence": 90, "reasoning": "ok",
            "risk_factors": [], "compliance_notes": "", "rules_triggered": ["R1"],
            "similar_case_ids": [],
            "risk_assessment": {"risk_score": 30},
        },
        "WF_X", "RUN_X",
    )
    assert out.startswith("DEC_")


@pytest.mark.asyncio
async def test_store_decision_uses_bedrock_model_when_provider_not_groq(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "bedrock")
    monkeypatch.setattr(act_mod.config, "BEDROCK_MODEL_VERSION", "claude-X")
    monkeypatch.setattr(act_mod.DecisionRepository, "create_decision_sync", lambda d: d.decision_id)
    monkeypatch.setattr(act_mod.AuditRepository, "create_audit_event_sync", lambda *_: "EVT_X")
    monkeypatch.setattr(act_mod.TransactionRepository, "update_status_sync", lambda *_, **__: None)
    out = await a.store_decision("TXN_X", {
        "decision": "approve", "confidence": 90, "reasoning": "ok",
        "risk_factors": [], "compliance_notes": "", "rules_triggered": [],
        "similar_case_ids": [], "risk_assessment": {"risk_score": 30},
    }, "WF", "RUN")
    assert out.startswith("DEC_")


@pytest.mark.asyncio
async def test_store_decision_audit_failure_swallowed(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(act_mod.config, "GROQ_MODEL_ID", "m")
    monkeypatch.setattr(act_mod.DecisionRepository, "create_decision_sync", lambda d: d.decision_id)
    monkeypatch.setattr(
        act_mod.AuditRepository, "create_audit_event_sync",
        lambda *_: (_ for _ in ()).throw(RuntimeError("audit fail")),
    )
    monkeypatch.setattr(act_mod.TransactionRepository, "update_status_sync", lambda *_, **__: None)
    out = await a.store_decision("TXN_X", {
        "decision": "approve", "confidence": 90, "reasoning": "",
        "risk_factors": [], "compliance_notes": "", "rules_triggered": [],
        "similar_case_ids": [], "risk_assessment": {"risk_score": 0},
    }, "WF", "RUN")
    assert out.startswith("DEC_")


@pytest.mark.asyncio
async def test_store_decision_status_update_failure_swallowed(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(act_mod.config, "GROQ_MODEL_ID", "m")
    monkeypatch.setattr(act_mod.DecisionRepository, "create_decision_sync", lambda d: d.decision_id)
    monkeypatch.setattr(act_mod.AuditRepository, "create_audit_event_sync", lambda *_: "EVT_X")
    monkeypatch.setattr(
        act_mod.TransactionRepository, "update_status_sync",
        lambda *_, **__: (_ for _ in ()).throw(RuntimeError("status fail")),
    )
    out = await a.store_decision("TXN_X", {
        "decision": "approve", "confidence": 90, "reasoning": "",
        "risk_factors": [], "compliance_notes": "", "rules_triggered": [],
        "similar_case_ids": [], "risk_assessment": {"risk_score": 0},
    }, "WF", "RUN")
    assert out.startswith("DEC_")


@pytest.mark.asyncio
async def test_store_decision_rule_metric_failure_swallowed(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(act_mod.config, "GROQ_MODEL_ID", "m")
    monkeypatch.setattr(act_mod.DecisionRepository, "create_decision_sync", lambda d: d.decision_id)
    monkeypatch.setattr(act_mod.AuditRepository, "create_audit_event_sync", lambda *_: "EVT_X")
    monkeypatch.setattr(act_mod.TransactionRepository, "update_status_sync", lambda *_, **__: None)
    monkeypatch.setattr(
        act_mod.RuleRepository, "update_rule_metrics_sync",
        lambda *_, **__: (_ for _ in ()).throw(RuntimeError("rule fail")),
    )
    out = await a.store_decision("TXN_X", {
        "decision": "approve", "confidence": 90, "reasoning": "",
        "risk_factors": [], "compliance_notes": "", "rules_triggered": ["R1"],
        "similar_case_ids": [], "risk_assessment": {"risk_score": 0},
    }, "WF", "RUN")
    assert out.startswith("DEC_")


@pytest.mark.asyncio
async def test_store_decision_returns_error_id_on_failure(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(act_mod.config, "GROQ_MODEL_ID", "m")
    monkeypatch.setattr(
        act_mod.DecisionRepository, "create_decision_sync",
        lambda d: (_ for _ in ()).throw(RuntimeError("create fail")),
    )
    out = await a.store_decision("TXN_X", {
        "decision": "approve", "confidence": 90, "reasoning": "",
        "risk_factors": [], "compliance_notes": "", "rules_triggered": [],
        "similar_case_ids": [], "risk_assessment": {"risk_score": 0},
    }, "WF", "RUN")
    assert out.startswith("DEC_ERROR_")


# ---------------------------------------------------------------------------
# queue_for_human_review
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("risk_score,expected_priority", [
    (90, "urgent"),
    (70, "high"),
    (50, "medium"),
    (10, "low"),
])
@pytest.mark.asyncio
async def test_queue_for_review_priority(monkeypatch, risk_score, expected_priority):
    a = TransactionActivities()
    import temporal.activities as act_mod

    captured = {}

    def fake_create(review):
        captured["review"] = review
        return review.review_id

    monkeypatch.setattr(act_mod.HumanReviewRepository, "create_review_sync_obj", fake_create)
    monkeypatch.setattr(act_mod.AuditRepository, "create_audit_event_sync", lambda *_: "EVT")
    out = await a.queue_for_human_review("TXN_X", {
        "decision": "escalate", "confidence": 60, "reasoning": "r",
        "risk_factors": [], "rules_triggered": [],
        "risk_assessment": {"risk_score": risk_score},
    })
    assert out.startswith("REV_")
    assert captured["review"].priority == expected_priority


@pytest.mark.asyncio
async def test_queue_for_review_audit_failure_swallowed(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.HumanReviewRepository, "create_review_sync_obj", lambda r: r.review_id)
    monkeypatch.setattr(
        act_mod.AuditRepository, "create_audit_event_sync",
        lambda *_: (_ for _ in ()).throw(RuntimeError("x")),
    )
    out = await a.queue_for_human_review("TXN_X", {
        "decision": "escalate", "confidence": 60, "reasoning": "r",
        "risk_factors": [], "rules_triggered": [],
        "risk_assessment": {"risk_score": 50},
    })
    assert out.startswith("REV_")


@pytest.mark.asyncio
async def test_queue_for_review_failure_returns_error_id(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(
        act_mod.HumanReviewRepository, "create_review_sync_obj",
        lambda *_: (_ for _ in ()).throw(RuntimeError("review fail")),
    )
    out = await a.queue_for_human_review("TXN_X", {
        "decision": "escalate", "confidence": 60, "reasoning": "r",
        "risk_factors": [], "rules_triggered": [],
        "risk_assessment": {"risk_score": 90},
    })
    assert out.startswith("REV_ERROR_")


# ---------------------------------------------------------------------------
# send_notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_notification_reject_priority(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    captured = {}

    def fake_create(notif):
        captured["notif"] = notif
        return notif.notification_id

    monkeypatch.setattr(act_mod.NotificationRepository, "create_notification_sync_obj", fake_create)
    monkeypatch.setattr(act_mod.NotificationRepository, "mark_as_sent_sync", lambda *_: None)
    out = await a.send_notification("TXN_X", "reject", "denied")
    assert out is True
    assert captured["notif"].priority == "high"


@pytest.mark.asyncio
async def test_send_notification_other_decision_medium_priority(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    captured = {}

    def fake_create(notif):
        captured["notif"] = notif
        return notif.notification_id

    monkeypatch.setattr(act_mod.NotificationRepository, "create_notification_sync_obj", fake_create)
    monkeypatch.setattr(act_mod.NotificationRepository, "mark_as_sent_sync", lambda *_: None)
    out = await a.send_notification("TXN_X", "approve", "ok")
    assert out is True
    assert captured["notif"].priority == "medium"


@pytest.mark.asyncio
async def test_send_notification_failure_returns_false(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(
        act_mod.NotificationRepository, "create_notification_sync_obj",
        lambda *_: (_ for _ in ()).throw(RuntimeError("notif fail")),
    )
    out = await a.send_notification("TXN_X", "approve", "ok")
    assert out is False


# ---------------------------------------------------------------------------
# execute_fund_transfer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_fund_transfer_success(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.AccountRepository, "release_hold_sync", lambda *_: True)
    monkeypatch.setattr(act_mod.AccountRepository, "execute_transfer_with_acid", lambda **k: True)
    out = await a.execute_fund_transfer("TXN_X", "ACC_S", "ACC_R", 100.0, "HOLD_X")
    assert out is True


@pytest.mark.asyncio
async def test_execute_fund_transfer_string_amount(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    captured = {}

    def fake_transfer(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(act_mod.AccountRepository, "release_hold_sync", lambda *_: True)
    monkeypatch.setattr(act_mod.AccountRepository, "execute_transfer_with_acid", fake_transfer)
    await a.execute_fund_transfer("TXN_X", "ACC_S", "ACC_R", "200.50", "HOLD_X")
    assert captured["amount"] == 200.50  # converted from str


@pytest.mark.asyncio
async def test_execute_fund_transfer_failure_releases_hold(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    release_calls = []

    def fake_release(hold_id):
        release_calls.append(hold_id)
        return True

    monkeypatch.setattr(act_mod.AccountRepository, "release_hold_sync", fake_release)
    monkeypatch.setattr(
        act_mod.AccountRepository, "execute_transfer_with_acid",
        lambda **k: (_ for _ in ()).throw(RuntimeError("transfer fail")),
    )
    out = await a.execute_fund_transfer("TXN_X", "ACC_S", "ACC_R", 100.0, "HOLD_X")
    assert out is False
    # Hold released both pre-transfer and in failure cleanup
    assert release_calls.count("HOLD_X") >= 1


@pytest.mark.asyncio
async def test_execute_fund_transfer_failure_release_failure_swallowed(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(
        act_mod.AccountRepository, "release_hold_sync",
        lambda *_: (_ for _ in ()).throw(RuntimeError("release fail")),
    )
    monkeypatch.setattr(
        act_mod.AccountRepository, "execute_transfer_with_acid",
        lambda **k: (_ for _ in ()).throw(RuntimeError("transfer fail")),
    )
    out = await a.execute_fund_transfer("TXN_X", "ACC_S", "ACC_R", 100.0, "HOLD_X")
    assert out is False


# ---------------------------------------------------------------------------
# cleanup_hold
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_hold_success(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(act_mod.AccountRepository, "release_hold_sync", lambda *_: True)
    out = await a.cleanup_hold("HOLD_X")
    assert out is True


@pytest.mark.asyncio
async def test_cleanup_hold_no_hold_id(monkeypatch):
    a = TransactionActivities()
    out = await a.cleanup_hold(None)
    assert out is False


@pytest.mark.asyncio
async def test_cleanup_hold_failure(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    monkeypatch.setattr(
        act_mod.AccountRepository, "release_hold_sync",
        lambda *_: (_ for _ in ()).throw(RuntimeError("x")),
    )
    out = await a.cleanup_hold("HOLD_X")
    assert out is False


# ---------------------------------------------------------------------------
# analyze_fraud_network
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyze_fraud_network_no_sender_account(monkeypatch):
    a = TransactionActivities()
    enriched = {"transaction": {"transaction_id": "TXN_X", "sender": {}, "recipient": {}}}
    out = await a.analyze_fraud_network(enriched)
    assert out["network_analysis_performed"] is False


@pytest.mark.asyncio
async def test_analyze_fraud_network_combines_sender_and_recipient(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    sender_net = {
        "unique_accounts_in_networks": 25,
        "risk_indicators": {
            "has_suspicious_patterns": True,
            "large_network_detected": True,
            "high_value_network": True,
        },
    }
    recipient_net = {
        "unique_accounts_in_networks": 25,
        "risk_indicators": {
            "has_suspicious_patterns": True,
            "large_network_detected": False,
            "high_value_network": False,
        },
    }

    async def fake_graph(account_id, **kwargs):
        return sender_net if account_id == "ACC_S" else recipient_net

    monkeypatch.setattr(act_mod.DecisionRepository, "graph_network_analysis", fake_graph)
    enriched = {"transaction": {
        "transaction_id": "TXN_X",
        "sender": {"account_number": "ACC_S"},
        "recipient": {"account_number": "ACC_R"},
    }}
    out = await a.analyze_fraud_network(enriched)
    assert out["network_analysis_performed"] is True
    assert out["network_risk_score"] > 0
    assert any("money laundering" in f for f in out["network_risk_factors"])


@pytest.mark.asyncio
async def test_analyze_fraud_network_failure_returns_error(monkeypatch):
    a = TransactionActivities()
    import temporal.activities as act_mod

    async def boom(*a_, **k):
        raise RuntimeError("graph fail")

    monkeypatch.setattr(act_mod.DecisionRepository, "graph_network_analysis", boom)
    enriched = {"transaction": {
        "transaction_id": "TXN_X",
        "sender": {"account_number": "ACC_S"},
        "recipient": {},
    }}
    out = await a.analyze_fraud_network(enriched)
    assert out["network_analysis_performed"] is False
    assert "error" in out
