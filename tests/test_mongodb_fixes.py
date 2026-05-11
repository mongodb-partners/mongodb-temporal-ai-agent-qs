"""Tests for MongoDB skill review fixes.

These tests guard the bugfixes documented in
.specs/mongodb-skill-fixes/bugfix.md. They are static / unit tests that do
not require a live MongoDB cluster.

Traceability:
  REQ-BUG-001 -> test_graph_network_uses_created_at_*
  REQ-BUG-002 -> test_acid_transfer_inc_uses_decimal128_*, test_place_hold_inc_uses_decimal128
  REQ-BUG-003 -> test_async_client_passes_pool_options, test_pymongo_client_passes_pool_options
  REQ-BUG-004 -> test_runtime_create_indexes_covers_full_set, test_setup_delegates_to_runtime_indexes
  REQ-BUG-005 -> test_compound_velocity_indexes_declared
  REQ-BUG-006 -> test_close_mongo_safe_when_uninitialized
  REQ-BUG-007 -> test_vector_search_filters_status, test_vector_search_numcandidates_ratio
"""

from __future__ import annotations

import ast
import inspect
import re
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from bson import Decimal128

ROOT = Path(__file__).resolve().parent.parent
REPOSITORIES = (ROOT / "database" / "repositories.py").read_text()
ACCOUNT_REPOSITORY = (ROOT / "database" / "account_repository.py").read_text()
CONNECTION = (ROOT / "database" / "connection.py").read_text()
SETUP_MONGODB = (ROOT / "scripts" / "setup_mongodb.py").read_text()


# ---------------------------------------------------------------------------
# REQ-BUG-001: graph_network_analysis must filter on created_at, not timestamp
# ---------------------------------------------------------------------------

def _extract_function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"function {name!r} not found")


def test_graph_network_uses_created_at_in_match():
    """REQ-BUG-001: $match stage filters on created_at, not timestamp."""
    fn = _extract_function_source(REPOSITORIES, "graph_network_analysis")
    # The aggregation pipeline must reference created_at for the time filter
    assert '"created_at"' in fn, "graph_network_analysis no longer references created_at"
    # And must NOT use the wrong field name
    assert '"timestamp": {"$gte"' not in fn, (
        "graph_network_analysis still uses 'timestamp' for the time-window filter"
    )


def test_graph_network_uses_created_at_in_graphlookup():
    """REQ-BUG-001: $graphLookup restrictSearchWithMatch uses created_at."""
    fn = _extract_function_source(REPOSITORIES, "graph_network_analysis")
    # restrictSearchWithMatch block must use created_at
    assert "restrictSearchWithMatch" in fn
    assert re.search(
        r"restrictSearchWithMatch.*created_at", fn, re.DOTALL
    ), "graph_network_analysis $graphLookup must filter on created_at"


def test_graph_indexes_use_created_at():
    """REQ-BUG-001: graph indexes index created_at, matching the query field."""
    # Single source of truth lives in database.connection.create_indexes()
    assert re.search(
        r'\("sender\.account_number", 1\),\s*\("created_at", -1\)',
        CONNECTION,
    ), "connection.create_indexes missing (sender.account_number, created_at) compound"
    assert re.search(
        r'\("recipient\.account_number", 1\),\s*\("created_at", -1\)',
        CONNECTION,
    ), "connection.create_indexes missing (recipient.account_number, created_at) compound"


# ---------------------------------------------------------------------------
# REQ-BUG-002: $inc must use Decimal128 for monetary fields
# ---------------------------------------------------------------------------

def test_acid_transfer_does_not_inc_with_float():
    """REQ-BUG-002: execute_transfer_with_acid must not pass float to $inc."""
    fn = _extract_function_source(ACCOUNT_REPOSITORY, "execute_transfer_with_acid")
    # Should not have float() inside total_withdrawals/total_deposits $inc values
    assert 'total_withdrawals": float(' not in fn, (
        "total_withdrawals $inc still uses float — corrupts Decimal128"
    )
    assert 'total_deposits": float(' not in fn, (
        "total_deposits $inc still uses float — corrupts Decimal128"
    )


def test_place_hold_inc_uses_decimal128():
    """REQ-BUG-002: place_hold_sync must $inc available_balance with Decimal128."""
    fn = _extract_function_source(ACCOUNT_REPOSITORY, "place_hold_sync")
    # The current bug is `-float(amount_decimal)` directly in $inc
    assert "-float(amount_decimal)" not in fn, (
        "place_hold_sync still passes -float(amount_decimal) to $inc"
    )


def test_release_hold_inc_uses_decimal128():
    """REQ-BUG-002: release_hold_sync must $inc available_balance with Decimal128."""
    fn = _extract_function_source(ACCOUNT_REPOSITORY, "release_hold_sync")
    # The pattern "$inc": {"available_balance": amount_float} is the bug
    assert '"available_balance": amount_float' not in fn, (
        "release_hold_sync still passes float to $inc"
    )


def test_decimal128_inc_payload_shape():
    """REQ-BUG-002: end-to-end shape — $inc payload contains Decimal128 instances."""
    # Re-import account_repository so we capture calls into a mocked sync DB.
    import sys
    sys.modules.pop("database.account_repository", None)

    from database import account_repository as ar
    from database.account_schemas import Account

    captured = {}

    class FakeCollection:
        def __init__(self, kind):
            self.kind = kind

        def find_one(self, filt, session=None, projection=None):
            if self.kind == "accounts":
                if filt.get("account_number") == "ACC_S":
                    return {
                        "account_number": "ACC_S",
                        "balance": Decimal128(Decimal("1000")),
                        "available_balance": Decimal128(Decimal("1000")),
                    }
                return {
                    "account_number": "ACC_R",
                    "balance": Decimal128(Decimal("500")),
                    "available_balance": Decimal128(Decimal("500")),
                }
            return None

        def update_one(self, filt, update, session=None):
            captured.setdefault("updates", []).append(update)
            return MagicMock()

        def insert_one(self, doc, session=None):
            return MagicMock()

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def with_transaction(self, callback, **kw):
            return callback(self)

    class FakeClient:
        def start_session(self):
            return FakeSession()

    class FakeDB:
        client = FakeClient()

        def __getitem__(self, _):
            return FakeCollection("accounts")

    with patch.object(ar, "get_sync_db", return_value=FakeDB()):
        ar.AccountRepository.execute_transfer_with_acid(
            sender_account="ACC_S",
            recipient_account="ACC_R",
            amount=Decimal("100"),
            transaction_id="TXN_TEST",
        )

    inc_payloads = [u.get("$inc", {}) for u in captured.get("updates", []) if "$inc" in u]
    assert inc_payloads, "no $inc updates captured"
    for payload in inc_payloads:
        for key in ("total_withdrawals", "total_deposits"):
            if key in payload:
                assert isinstance(payload[key], Decimal128), (
                    f"$inc.{key} must be Decimal128, got {type(payload[key]).__name__}"
                )


# ---------------------------------------------------------------------------
# REQ-BUG-003: connection clients must be configured with pool/timeout options
# ---------------------------------------------------------------------------

REQUIRED_POOL_OPTS = [
    "maxPoolSize",
    "minPoolSize",
    "serverSelectionTimeoutMS",
    "socketTimeoutMS",
    "connectTimeoutMS",
    "waitQueueTimeoutMS",
    "retryWrites",
    "retryReads",
]


def test_pool_options_constant_defines_required_keys():
    """REQ-BUG-003: a single MONGO_CLIENT_OPTIONS dict declares the tuned options."""
    import importlib
    import sys
    sys.modules.pop("database.connection", None)
    conn = importlib.import_module("database.connection")
    opts = getattr(conn, "MONGO_CLIENT_OPTIONS", None)
    assert isinstance(opts, dict), "MONGO_CLIENT_OPTIONS dict not exported"
    for opt in REQUIRED_POOL_OPTS:
        assert opt in opts, f"MONGO_CLIENT_OPTIONS missing required key: {opt}"


def test_async_client_passes_pool_options():
    """REQ-BUG-003 (post motor→pymongo migration): the async client is
    constructed with the tuned options. The class is now
    `pymongo.AsyncMongoClient` rather than motor's `AsyncIOMotorClient`."""
    async_call = re.search(r"AsyncMongoClient\(([^)]*)\)", CONNECTION, re.DOTALL)
    assert async_call, "AsyncMongoClient(...) call not found"
    call = async_call.group(1)
    # Either every option is named explicitly OR the options dict is splatted in
    has_splat = "**MONGO_CLIENT_OPTIONS" in call
    if not has_splat:
        for opt in REQUIRED_POOL_OPTS:
            assert opt in call, f"AsyncMongoClient missing required option: {opt}"


def test_pymongo_client_passes_pool_options():
    """REQ-BUG-003: the sync MongoClient is constructed with the tuned
    options. We isolate the sync construction by anchoring on the
    bare-word `MongoClient(` (excluding `AsyncMongoClient`)."""
    pymongo_call = re.search(
        r"(?<!Async)MongoClient\(([^)]*)\)", CONNECTION, re.DOTALL
    )
    assert pymongo_call, "sync MongoClient(...) call not found"
    call = pymongo_call.group(1)
    has_splat = "**MONGO_CLIENT_OPTIONS" in call
    if not has_splat:
        for opt in REQUIRED_POOL_OPTS:
            assert opt in call, f"MongoClient missing required option: {opt}"


# ---------------------------------------------------------------------------
# REQ-BUG-004: a single source of truth for index creation
# ---------------------------------------------------------------------------

def test_runtime_create_indexes_covers_full_set():
    """REQ-BUG-004: connection.create_indexes() declares the full production set."""
    fn = _extract_function_source(CONNECTION, "create_indexes")
    # Hybrid-search compound index
    assert "hybrid_search_index" in fn or all(
        p in fn for p in ('"transaction_type"', '"sender.country"', '"recipient.country"')
    )
    # Graph traversal indexes
    assert "sender.account_number" in fn
    assert "recipient.account_number" in fn
    # Accounts / journal / holds
    assert "ACCOUNTS_COLLECTION" in fn
    assert "JOURNAL_COLLECTION" in fn
    assert "HOLDS_COLLECTION" in fn
    # TTL index on metrics
    assert "expireAfterSeconds" in fn


def test_setup_delegates_to_runtime_indexes():
    """REQ-BUG-004: scripts/setup_mongodb reuses connection.create_indexes."""
    # The setup script's create_indexes function should call into runtime indexes
    # rather than duplicate the entire list. We accept either an explicit
    # delegation OR that the runtime function IS imported from connection.
    assert (
        "from database.connection import create_indexes" in SETUP_MONGODB
        or "database.connection.create_indexes" in SETUP_MONGODB
    ), "setup_mongodb must reuse connection.create_indexes (single source of truth)"


# ---------------------------------------------------------------------------
# REQ-BUG-005: compound indexes for velocity queries
# ---------------------------------------------------------------------------

def test_compound_velocity_indexes_declared():
    """REQ-BUG-005: compound indexes on (customer_id|account_number, created_at) exist.

    These compound indexes serve both velocity queries and graph traversal
    (same key shape, so a single index covers both query patterns).
    """
    fn = _extract_function_source(CONNECTION, "create_indexes")
    assert re.search(
        r'\("sender\.customer_id", 1\),\s*\("created_at", -1\)', fn
    ), "missing (sender.customer_id, created_at: -1) compound index"
    assert re.search(
        r'\("sender\.account_number", 1\),\s*\("created_at", -1\)', fn
    ), "missing (sender.account_number, created_at: -1) compound index"
    assert re.search(
        r'\("recipient\.account_number", 1\),\s*\("created_at", -1\)', fn
    ), "missing (recipient.account_number, created_at: -1) compound index"


# ---------------------------------------------------------------------------
# REQ-BUG-006: close_mongo_connection must be safe when client is unset
# ---------------------------------------------------------------------------

def test_close_mongo_safe_when_uninitialized():
    """REQ-BUG-006: close_mongo_connection() does not raise when client is None."""
    import sys
    sys.modules.pop("database.connection", None)
    from database import connection as conn
    import asyncio

    # Force client to None to simulate pre-init failure
    conn.db.client = None
    # Should not raise
    asyncio.run(conn.close_mongo_connection())


# ---------------------------------------------------------------------------
# REQ-BUG-007: vector search filters on status and uses 20x numCandidates ratio
# ---------------------------------------------------------------------------

def test_vector_search_filters_status():
    """REQ-BUG-007: vector_search_similar_transactions filters by status."""
    fn = _extract_function_source(REPOSITORIES, "vector_search_similar_transactions")
    assert '"status"' in fn, "vector search must filter on status"


def test_vector_search_numcandidates_ratio():
    """REQ-BUG-007: numCandidates uses ~20x limit per skill guidance."""
    fn = _extract_function_source(REPOSITORIES, "vector_search_similar_transactions")
    assert "limit * 20" in fn, "vector search numCandidates should be limit * 20"
