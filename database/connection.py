"""MongoDB connection management.

Pool/timeout values follow the `mongodb-connection` skill's
"High-Traffic / Bursty" OLTP scenario: this app runs API + Streamlit + a
Temporal worker, each holding both an async client (PyMongo's native
``AsyncMongoClient``) and a sync client, so we want bounded pools and
fast failover.

The async client used to come from ``motor``; motor has been deprecated
in favour of PyMongo 4.16+'s built-in async API. See
``.specs/motor-to-pymongo-async/refactor.md``.
"""

import logging

from pymongo import AsyncMongoClient, MongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure

from utils.config import config

logger = logging.getLogger(__name__)


async def _create_index_idempotent(collection, keys, **kwargs):
    """Create an index, reconciling stale specs from earlier schema versions.

    Handles two related migration errors:
      * 86 (IndexKeySpecsConflict): an index with the same NAME but a different
        KEY exists. Drop by name and recreate.
      * 85 (IndexOptionsConflict): an index with the same KEY but a different
        NAME exists. Drop by key spec (looking up the existing name) and recreate
        under the desired name.
    """
    try:
        return await collection.create_index(keys, **kwargs)
    except OperationFailure as exc:
        index_name = kwargs.get("name")
        if exc.code == 86 and index_name:
            logger.warning(
                "Index %r exists with stale key spec; dropping and recreating.",
                index_name,
            )
            await collection.drop_index(index_name)
            return await collection.create_index(keys, **kwargs)
        if exc.code == 85 and index_name:
            cursor = await collection.list_indexes()
            existing = await cursor.to_list(length=None)
            target_key = list(keys)
            for idx in existing:
                if list(idx.get("key", {}).items()) == target_key:
                    stale_name = idx["name"]
                    if stale_name != index_name:
                        logger.warning(
                            "Index with key %s exists as %r; dropping and "
                            "recreating as %r.",
                            target_key,
                            stale_name,
                            index_name,
                        )
                        await collection.drop_index(stale_name)
                        return await collection.create_index(keys, **kwargs)
        raise

# Tuned for OLTP + bursty Temporal activity. Two clients per process means
# the effective max is 2 * maxPoolSize per process — keep this conservative.
MONGO_CLIENT_OPTIONS: dict = {
    "maxPoolSize": 50,
    "minPoolSize": 5,
    "serverSelectionTimeoutMS": 5000,
    "socketTimeoutMS": 30000,
    "connectTimeoutMS": 10000,
    "waitQueueTimeoutMS": 3000,
    "maxIdleTimeMS": 300000,  # 5 min
    "retryWrites": True,
    "retryReads": True,
}


class MongoDB:
    client: AsyncMongoClient | None = None
    database: AsyncDatabase | None = None


db = MongoDB()


async def connect_to_mongo():
    """Create database connection."""
    try:
        db.client = AsyncMongoClient(config.MONGODB_URI, **MONGO_CLIENT_OPTIONS)
        db.database = db.client[config.MONGODB_DB_NAME]

        # Create indexes (single source of truth)
        await create_indexes()
        logger.info("Connected to MongoDB")
    except Exception as e:
        logger.error(f"Could not connect to MongoDB: {e}")
        raise


async def close_mongo_connection():
    """Close database connection. Safe to call when client was never set.

    AsyncMongoClient.close() is a coroutine — unlike motor's synchronous
    client.close() — so it must be awaited.
    """
    if db.client is None:
        logger.info("MongoDB client was not initialized; skipping close")
        return
    await db.client.close()
    db.client = None
    logger.info("Disconnected from MongoDB")


async def create_indexes():
    """Create the full production index set.

    This is the single source of truth — `scripts/setup_mongodb` delegates
    here. Vector-search index creation lives in the setup script because it
    requires Atlas-only APIs and should not run on every startup.
    """
    txn = db.database[config.TRANSACTIONS_COLLECTION]
    decisions = db.database[config.DECISIONS_COLLECTION]
    customers = db.database[config.CUSTOMERS_COLLECTION]
    audits = db.database[config.AUDIT_EVENTS_COLLECTION]
    metrics = db.database[config.SYSTEM_METRICS_COLLECTION]
    rules = db.database[config.RULES_COLLECTION]
    reviews = db.database[config.HUMAN_REVIEWS_COLLECTION]
    notifications = db.database[config.NOTIFICATIONS_COLLECTION]
    accounts = db.database[config.ACCOUNTS_COLLECTION]
    journal = db.database[config.JOURNAL_COLLECTION]
    balance_updates = db.database[config.BALANCE_UPDATES_COLLECTION]
    holds = db.database[config.HOLDS_COLLECTION]

    # Customers
    await customers.create_index([("customer_id", 1)], unique=True)
    await customers.create_index([("legal_name", 1)])
    await customers.create_index([("status", 1)])

    # Transactions — base + ESR-aware compound for velocity / customer history
    await txn.create_index([("transaction_id", 1)], unique=True)
    await txn.create_index([("status", 1), ("created_at", -1)])
    await txn.create_index([("transaction_type", 1)])
    await txn.create_index([("amount", 1)])
    await txn.create_index([("created_at", -1)])
    await txn.create_index([("sender.customer_id", 1)])
    # Compound indexes for velocity / customer-history queries (ESR: equality, sort).
    # The (sender.account_number, created_at) compound also serves graph traversal
    # filtered by created_at (REQ-BUG-001), so a single index covers both query
    # shapes. The (recipient.account_number, created_at) compound likewise covers
    # the recipient-side graph join. The idempotent helper drops any stale
    # `timestamp`-keyed index that pre-dates the field rename.
    await txn.create_index(
        [("sender.customer_id", 1), ("created_at", -1)],
        name="velocity_by_customer_index",
    )
    await _create_index_idempotent(
        txn,
        [("sender.account_number", 1), ("created_at", -1)],
        name="graph_sender_time_index",
    )
    await _create_index_idempotent(
        txn,
        [("recipient.account_number", 1), ("created_at", -1)],
        name="graph_recipient_time_index",
    )
    # Hybrid-search compound (used by repositories.hybrid_search_similar_transactions)
    await txn.create_index(
        [
            ("transaction_type", 1),
            ("amount", 1),
            ("sender.country", 1),
            ("recipient.country", 1),
        ],
        name="hybrid_search_index",
    )

    # Decisions
    await decisions.create_index([("transaction_id", 1)])
    await decisions.create_index([("decision", 1), ("created_at", -1)])
    await decisions.create_index([("confidence_score", 1)])
    await decisions.create_index([("risk_score", 1)])

    # Rules
    await rules.create_index([("rule_id", 1)], unique=True)
    await rules.create_index([("status", 1), ("priority", -1)])
    await rules.create_index([("category", 1)])

    # Human reviews
    await reviews.create_index([("transaction_id", 1)])
    await reviews.create_index([("status", 1), ("priority", -1)])
    await reviews.create_index([("assigned_to", 1)])
    await reviews.create_index([("sla_deadline", 1)])

    # Notifications
    await notifications.create_index([("notification_id", 1)], unique=True)
    await notifications.create_index([("status", 1), ("created_at", 1)])
    await notifications.create_index([("transaction_id", 1)])

    # Audit
    await audits.create_index([("timestamp", -1)])
    await audits.create_index([("transaction_id", 1)])
    await audits.create_index([("event_type", 1)])

    # Metrics — TTL 30 days
    await metrics.create_index([("timestamp", 1)], expireAfterSeconds=2592000)
    await metrics.create_index([("metric_name", 1), ("timestamp", -1)])

    # Accounts
    await accounts.create_index([("account_number", 1)], unique=True)
    await accounts.create_index([("customer_id", 1)])
    await accounts.create_index([("status", 1)])

    # Journal
    await journal.create_index([("journal_id", 1)], unique=True)
    await journal.create_index([("transaction_id", 1)])
    await journal.create_index([("account_number", 1), ("timestamp", -1)])
    await journal.create_index([("status", 1)])

    # Balance updates
    await balance_updates.create_index([("update_id", 1)], unique=True)
    await balance_updates.create_index([("account_number", 1), ("timestamp", -1)])
    await balance_updates.create_index([("transaction_id", 1)])

    # Holds
    await holds.create_index([("hold_id", 1)], unique=True)
    await holds.create_index([("account_number", 1), ("status", 1)])
    await holds.create_index([("transaction_id", 1)])
    await holds.create_index([("expires_at", 1)])


# Global sync client for Temporal activities
_sync_client = None


def get_sync_db():
    """Get synchronous MongoDB client for Temporal activities."""
    global _sync_client
    if _sync_client is None:
        _sync_client = MongoClient(config.MONGODB_URI, **MONGO_CLIENT_OPTIONS)
    return _sync_client[config.MONGODB_DB_NAME]
