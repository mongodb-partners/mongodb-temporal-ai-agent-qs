"""Integration tests for api/main using FastAPI TestClient + real MongoDB.

Spec: .specs/test-coverage-fill/refactor.md (REQ-R-302)

The Temporal client is mocked because we don't want to boot a Temporal
server. The MongoDB client is the real container from conftest.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import Decimal128
from fastapi.testclient import TestClient


@pytest.fixture
def api_client(mongo_uri, monkeypatch):
    """FastAPI TestClient wired against the test Mongo container.

    AsyncMongoClient is bound to the event loop on which it was constructed,
    so we rely on FastAPI's lifespan to run the real ``startup_event``
    (which creates the async client on TestClient's loop). We monkey-patch
    the parts of startup we don't want — the Temporal client connection.
    """
    from utils.config import config

    db_name = "test_api_integration_db"
    monkeypatch.setattr(config, "MONGODB_URI", mongo_uri)
    monkeypatch.setattr(config, "MONGODB_DB_NAME", db_name)

    # Drop the DB before the test using a sync client; teardown drops too.
    from pymongo import MongoClient

    sync = MongoClient(mongo_uri)
    sync.drop_database(db_name)
    sync.close()

    from database import connection as db_conn

    db_conn._sync_client = None
    db_conn.db.client = None
    db_conn.db.database = None

    # Replace Temporal connect with a fake; it returns a fake client whose
    # start_workflow is an AsyncMock (so the route can `await` it).
    fake_temporal = MagicMock()
    fake_temporal.start_workflow = AsyncMock(return_value=MagicMock())

    from api import main as api_main
    from temporalio.client import Client as TemporalClient

    async def fake_connect(*a, **k):
        return fake_temporal

    monkeypatch.setattr(TemporalClient, "connect", fake_connect)

    # TestClient context-manager runs the app's lifespan -> our real
    # startup_event runs (creates AsyncMongoClient on the same loop).
    with TestClient(api_main.app, raise_server_exceptions=True) as client:
        yield client

    # Teardown
    sync = MongoClient(mongo_uri)
    sync.drop_database(db_name)
    sync.close()
    db_conn._sync_client = None


@pytest.fixture
def sync_db(mongo_uri):
    """Sync handle to the API integration DB for seeding fixtures."""
    from pymongo import MongoClient

    client = MongoClient(mongo_uri)
    yield client["test_api_integration_db"]
    client.close()


def test_health(api_client):
    resp = api_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["mongodb"] == "connected"
    assert body["temporal"] == "connected"
    assert "embedding" in body
    assert "available_models" in body["embedding"]


def test_post_transaction_starts_workflow(api_client):
    payload = {
        "transaction_type": "wire_transfer",
        "amount": 1000.0,
        "sender": {"name": "Alice", "country": "US"},
        "recipient": {"name": "Bob", "country": "GB"},
    }
    resp = api_client.post("/api/transaction", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["transaction_id"]
    assert body["workflow_id"].startswith("txn-processing-")
    assert body["status"] == "processing"


def test_post_transaction_invalid_amount_rejected(api_client):
    payload = {
        "transaction_type": "ach",
        "amount": "abc",  # not a decimal
        "sender": {"name": "A"},
        "recipient": {"name": "B"},
    }
    resp = api_client.post("/api/transaction", json=payload)
    assert resp.status_code == 422  # Pydantic validation


def test_post_transaction_handles_internal_failure(api_client, monkeypatch):
    """If the create_transaction call raises, we surface a 500."""
    from database.repositories import TransactionRepository

    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(TransactionRepository, "create_transaction", boom)
    payload = {
        "transaction_type": "ach",
        "amount": 1.0,
        "sender": {"name": "A"},
        "recipient": {"name": "B"},
    }
    resp = api_client.post("/api/transaction", json=payload)
    assert resp.status_code == 500
    assert "db down" in resp.json()["detail"]


def test_get_decision_returns_decision_when_present(api_client, sync_db):
    sync_db.transaction_decisions.insert_one({
        "decision_id": "DEC_T1",
        "transaction_id": "TXN_T1",
        "decision": "approve",
        "confidence_score": 95,
        "risk_score": 25,
        "processing_time_ms": 100,
        "reasoning": {"primary_reasoning": "ok", "risk_factors": ["x"]},
    })
    resp = api_client.get("/api/transaction/TXN_T1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "approve"
    assert body["confidence"] == 95


def test_get_decision_pending_returns_202(api_client, sync_db):
    sync_db.transactions.insert_one({
        "transaction_id": "TXN_PENDING",
        "status": "pending",
        "amount": Decimal128("100"),
        "transaction_type": "ach",
    })
    resp = api_client.get("/api/transaction/TXN_PENDING")
    assert resp.status_code == 202


def test_get_decision_rejected_returns_synthetic_decision(api_client, sync_db):
    sync_db.transactions.insert_one({
        "transaction_id": "TXN_REJ",
        "status": "rejected",
        "amount": Decimal128("100"),
        "transaction_type": "ach",
    })
    resp = api_client.get("/api/transaction/TXN_REJ")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "reject"
    assert "compliance_violation" in body["risk_factors"]


def test_get_decision_failed_returns_synthetic_decision(api_client, sync_db):
    sync_db.transactions.insert_one({
        "transaction_id": "TXN_FAIL",
        "status": "failed",
        "amount": Decimal128("100"),
        "transaction_type": "ach",
    })
    resp = api_client.get("/api/transaction/TXN_FAIL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "reject"
    assert "processing_failure" in body["risk_factors"]


def test_get_decision_unknown_transaction_returns_404(api_client):
    resp = api_client.get("/api/transaction/DOES_NOT_EXIST")
    assert resp.status_code == 404


def test_get_decision_internal_error(api_client, monkeypatch):
    from database.repositories import DecisionRepository

    async def boom(*a, **k):
        raise RuntimeError("decision query failed")

    monkeypatch.setattr(DecisionRepository, "get_decision_by_transaction", boom)
    resp = api_client.get("/api/transaction/anything")
    assert resp.status_code == 500


def test_metrics_endpoint(api_client, sync_db):
    """Insert metrics + decisions via the test DB and check aggregate output."""
    sync_db.transactions.insert_many([
        {
            "transaction_id": "TXN_M1",
            "transaction_type": "wire_transfer",
            "amount": Decimal128("100"),
            "status": "approved",
            "created_at": datetime.now(timezone.utc),
        },
        {
            "transaction_id": "TXN_M2",
            "transaction_type": "wire_transfer",
            "amount": Decimal128("50"),
            "status": "approved",
            "created_at": datetime.now(timezone.utc),
        },
    ])
    sync_db.transaction_decisions.insert_many([
        {
            "decision_id": "DEC_M1",
            "transaction_id": "TXN_M1",
            "decision": "approve",
            "confidence_score": 90,
            "risk_score": 20,
            "processing_time_ms": 100,
        },
        {
            "decision_id": "DEC_M2",
            "transaction_id": "TXN_M2",
            "decision": "reject",
            "confidence_score": 60,
            "risk_score": 80,
            "processing_time_ms": 200,
        },
    ])
    resp = api_client.get("/api/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_transactions"] >= 2
    assert "wire_transfer" in body["transactions_by_type"]
    assert {"approve", "reject"}.issubset(body["decisions_breakdown"].keys())


def test_metrics_endpoint_with_no_data(api_client):
    """Empty collections -> still returns sensible defaults."""
    resp = api_client.get("/api/metrics")
    assert resp.status_code == 200


def test_metrics_endpoint_handles_internal_failure(api_client, monkeypatch):
    """If the aggregation raises, the API surfaces a 500."""
    from database import connection as db_conn

    real_db = db_conn.db.database

    class Boom:
        def __getitem__(self, _):
            class C:
                async def count_documents(self, *a, **k):
                    raise RuntimeError("agg down")
                async def aggregate(self, *a, **k):
                    raise RuntimeError("agg down")
            return C()

    db_conn.db.database = Boom()
    try:
        resp = api_client.get("/api/metrics")
        assert resp.status_code == 500
    finally:
        db_conn.db.database = real_db


def test_health_when_disconnected(api_client, monkeypatch):
    """If db.client is None, the health endpoint reports 'disconnected'."""
    from database import connection as db_conn

    saved = db_conn.db.client
    db_conn.db.client = None
    try:
        resp = api_client.get("/health")
        body = resp.json()
        assert body["mongodb"] == "disconnected"
    finally:
        db_conn.db.client = saved
