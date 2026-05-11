"""Data access layer for MongoDB operations."""

from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta, timezone
from bson import ObjectId, Decimal128
from decimal import Decimal
from database.connection import get_sync_db, db
from utils.decimal_utils import to_decimal128, from_decimal128, decimal_to_float
from database.schemas import (
    Transaction, TransactionDecision, AuditEvent, SystemMetric,
    Customer, Rule, HumanReview, Notification, RuleStatus, NotificationStatus
)
from utils.config import config
import logging

logger = logging.getLogger(__name__)

def serialize_doc(doc: Dict) -> Dict:
    """Convert MongoDB document to JSON-serializable format."""
    if doc is None:
        return None
    
    result = doc.copy()
    
    # Convert ObjectId to string
    if '_id' in result and isinstance(result['_id'], ObjectId):
        result['_id'] = str(result['_id'])
    
    # Convert datetime objects to ISO format strings and Decimal128 to string
    for key, value in result.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, ObjectId):
            result[key] = str(value)
        elif isinstance(value, Decimal128):
            # Convert Decimal128 to string for JSON serialization
            result[key] = str(value.to_decimal())
        elif isinstance(value, dict):
            result[key] = serialize_doc(value)
        elif isinstance(value, list):
            result[key] = [
                serialize_doc(item) if isinstance(item, dict)
                else item.isoformat() if isinstance(item, datetime)
                else str(item) if isinstance(item, ObjectId)
                else str(item.to_decimal()) if isinstance(item, Decimal128)
                else item
                for item in value
            ]
    
    return result

class CustomerRepository:
    """Repository for customer operations."""
    
    @staticmethod
    async def create_customer(customer: Customer) -> str:
        """Create a new customer."""
        result = await db.database[config.CUSTOMERS_COLLECTION].insert_one(
            customer.model_dump()
        )
        return customer.customer_id
    
    @staticmethod
    async def get_customer(customer_id: str) -> Optional[Dict]:
        """Get customer by ID."""
        doc = await db.database[config.CUSTOMERS_COLLECTION].find_one(
            {"customer_id": customer_id}
        )
        return serialize_doc(doc)
    
    @staticmethod
    def get_customer_sync(customer_id: str) -> Optional[Dict]:
        """Get customer by ID (synchronous)."""
        db_sync = get_sync_db()
        doc = db_sync[config.CUSTOMERS_COLLECTION].find_one(
            {"customer_id": customer_id}
        )
        return serialize_doc(doc)

    @staticmethod
    async def update_customer_profile(
        customer_id: str,
        transaction_count: int,
        total_amount: float,
        avg_amount: float,
        last_transaction_date: datetime
    ):
        """Update customer profile with transaction statistics."""
        try:
            # Use sync client for Temporal activities
            db_sync = get_sync_db()
            collection = db_sync[config.CUSTOMERS_COLLECTION]
            
            result = collection.update_one(
                {"customer_id": customer_id},
                {
                    "$set": {
                        "transaction_count": transaction_count,
                        "total_amount": to_decimal128(total_amount),
                        "average_transaction_amount": to_decimal128(avg_amount),
                        "last_transaction_date": last_transaction_date,
                        "updated_at": datetime.now(timezone.utc)
                    },
                    "$setOnInsert": {
                        "customer_id": customer_id,
                        "created_at": datetime.now(timezone.utc)
                    }
                },
                upsert=True
            )
            
            logger.info(f"Updated customer profile for {customer_id}: "
                       f"modified={result.modified_count}, upserted={result.upserted_id is not None}")
            
            return result
            
        except Exception as e:
            logger.error(f"Error updating customer profile for {customer_id}: {e}")
            raise

    @staticmethod
    async def get_or_create_customer(customer_data: Dict) -> str:
        """Get existing customer or create new one."""
        # Check if customer exists
        existing = await db.database[config.CUSTOMERS_COLLECTION].find_one(
            {"legal_name": customer_data.get("name")}
        )
        
        if existing:
            return existing["customer_id"]
        
        # Create new customer
        customer = Customer(
            legal_name=customer_data.get("name", "Unknown"),
            display_name=customer_data.get("name", "Unknown"),
            customer_type="business" if "Corp" in customer_data.get("name", "") else "individual",
            country=customer_data.get("country", "US")
        )
        
        return await CustomerRepository.create_customer(customer)
    
    @staticmethod
    def get_or_create_customer_sync(customer_data: Dict) -> str:
        """Get existing customer or create new one (synchronous)."""
        db_sync = get_sync_db()
        # Check if customer exists
        existing = db_sync[config.CUSTOMERS_COLLECTION].find_one(
            {"legal_name": customer_data.get("name")}
        )
        
        if existing:
            return existing["customer_id"]
        
        # Create new customer
        customer = Customer(
            legal_name=customer_data.get("name", "Unknown"),
            display_name=customer_data.get("name", "Unknown"),
            customer_type="business" if "Corp" in customer_data.get("name", "") else "individual",
            country=customer_data.get("country", "US")
        )
        
        db_sync[config.CUSTOMERS_COLLECTION].insert_one(customer.model_dump())
        return customer.customer_id

class TransactionRepository:
    """Repository for transaction operations."""
    
    @staticmethod
    async def create_transaction(transaction: Transaction) -> str:
        """Create a new transaction."""
        # Ensure customer records exist
        if "customer_id" not in transaction.sender:
            transaction.sender["customer_id"] = await CustomerRepository.get_or_create_customer(
                transaction.sender
            )
        
        result = await db.database[config.TRANSACTIONS_COLLECTION].insert_one(
            transaction.model_dump()
        )
        return transaction.transaction_id
    
    @staticmethod
    async def get_transaction(transaction_id: str) -> Optional[Dict]:
        """Get transaction by ID."""
        doc = await db.database[config.TRANSACTIONS_COLLECTION].find_one(
            {"transaction_id": transaction_id}
        )
        return serialize_doc(doc)
    
    @staticmethod
    async def update_status(transaction_id: str, status: str, substatus: Optional[str] = None):
        """Update transaction status."""
        update_doc = {
            "$set": {
                "status": status,
                "updated_at": datetime.now(timezone.utc)
            },
            "$push": {
                "processing_stages": {
                    "stage": status,
                    "timestamp": datetime.now(timezone.utc),
                    "substatus": substatus
                }
            }
        }
        
        await db.database[config.TRANSACTIONS_COLLECTION].update_one(
            {"transaction_id": transaction_id},
            update_doc
        )
    
    @staticmethod
    def update_status_sync(transaction_id: str, status: str, substatus: Optional[str] = None):
        """Update transaction status (synchronous)."""
        db_sync = get_sync_db()
        update_doc = {
            "$set": {
                "status": status,
                "updated_at": datetime.now(timezone.utc)
            },
            "$push": {
                "processing_stages": {
                    "stage": status,
                    "timestamp": datetime.now(timezone.utc),
                    "substatus": substatus
                }
            }
        }
        
        db_sync[config.TRANSACTIONS_COLLECTION].update_one(
            {"transaction_id": transaction_id},
            update_doc
        )
    
    @staticmethod
    async def store_embedding(transaction_id: str, embedding: List[float], model: str = "cohere"):
        """Store vector embedding for transaction."""
        await db.database[config.TRANSACTIONS_COLLECTION].update_one(
            {"transaction_id": transaction_id},
            {
                "$set": {
                    "embedding": embedding,
                    "embedding_model": model,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
    
    @staticmethod
    def store_embedding_sync(transaction_id: str, embedding: List[float], model: str = "cohere"):
        """Store vector embedding for transaction (synchronous)."""
        db_sync = get_sync_db()
        db_sync[config.TRANSACTIONS_COLLECTION].update_one(
            {"transaction_id": transaction_id},
            {
                "$set": {
                    "embedding": embedding,
                    "embedding_model": model,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
    
    @staticmethod
    def get_customer_history_sync(customer_id: str) -> Dict:
        """Get customer transaction history (synchronous for Temporal)."""
        db_sync = get_sync_db()
        
        # First try to get customer profile
        customer = db_sync[config.CUSTOMERS_COLLECTION].find_one(
            {"customer_id": customer_id}
        )
        
        if customer:
            # Use customer profile data
            base_data = {
                "customer_since": customer.get("created_at"),
                "risk_level": customer.get("risk_profile", {}).get("risk_level", "medium"),
                "kyc_status": customer.get("risk_profile", {}).get("kyc_status", "pending"),
                "avg_transaction_amount": customer.get("behavior_profile", {}).get("avg_transaction_amount", 0),
                "transaction_frequency": customer.get("behavior_profile", {}).get("transaction_frequency", "unknown"),
                "common_recipients": customer.get("behavior_profile", {}).get("common_recipients", [])
            }
        else:
            base_data = {
                "customer_since": None,
                "risk_level": "unknown",
                "kyc_status": "unknown"
            }
        
        # Get transaction history
        ninety_days_ago = datetime.now(timezone.utc) - timedelta(days=90)
        transactions = list(db_sync[config.TRANSACTIONS_COLLECTION].find(
            {
                "sender.customer_id": customer_id,
                "created_at": {"$gte": ninety_days_ago}
            }
        ).limit(100))
        
        if transactions:
            # Handle Decimal128 values in sum operation
            from utils.decimal_utils import from_decimal128
            total_amount = sum(from_decimal128(t.get("amount", 0)) for t in transactions)
            risk_incidents = sum(1 for t in transactions if t.get("status") in ["rejected", "escalated"])
            
            recipients = []
            for t in transactions[:10]:
                if "recipient" in t and "name" in t["recipient"]:
                    recipients.append(t["recipient"]["name"])
            
            base_data.update({
                "total_transactions": len(transactions),
                "avg_amount": total_amount / len(transactions) if transactions else 0,
                "total_amount": total_amount,
                "risk_incidents": risk_incidents,
                "common_recipients": list(set(recipients)) if not base_data.get("common_recipients") else base_data["common_recipients"]
            })
        else:
            base_data.update({
                "total_transactions": 0,
                "avg_amount": 0,
                "total_amount": 0,
                "risk_incidents": 0
            })
        
        return base_data

class DecisionRepository:
    """Repository for decision operations."""
    
    @staticmethod
    async def create_decision(decision: TransactionDecision) -> str:
        """Create a new decision."""
        result = await db.database[config.DECISIONS_COLLECTION].insert_one(
            decision.model_dump()
        )
        return decision.decision_id
    
    @staticmethod
    def create_decision_sync(decision: TransactionDecision) -> str:
        """Create a new decision (synchronous)."""
        db_sync = get_sync_db()
        db_sync[config.DECISIONS_COLLECTION].insert_one(
            decision.model_dump()
        )
        return decision.decision_id
    
    @staticmethod
    async def get_decision_by_transaction(transaction_id: str) -> Optional[Dict]:
        """Get decision by transaction ID."""
        doc = await db.database[config.DECISIONS_COLLECTION].find_one(
            {"transaction_id": transaction_id}
        )
        return serialize_doc(doc)
    
    @staticmethod
    async def hybrid_search_similar_transactions(
        embedding: Optional[List[float]],
        transaction_details: Dict[str, Any],
        limit: int = 10
    ) -> List[Dict]:
        """Find similar transactions using hybrid search (vector + traditional indexes)."""

        # Extract search parameters
        transaction_type = transaction_details.get('transaction_type')
        amount = transaction_details.get('amount', 0)
        sender_country = transaction_details.get('sender', {}).get('country')
        recipient_country = transaction_details.get('recipient', {}).get('country')

        # Build aggregation pipeline with multiple search strategies
        pipeline = []

        # Stage 1: Traditional index-based search for exact matches.
        # Restrict to decided statuses so we don't surface in-flight transactions
        # as "similar cases" — those have no decision yet.
        match_stage = {
            "$match": {
                "status": {"$in": DecisionRepository._DECIDED_STATUSES},
                "$or": [
                    # Exact type and amount range match
                    {
                        "transaction_type": transaction_type,
                        "amount": {
                            "$gte": amount * 0.8,
                            "$lte": amount * 1.2
                        }
                    },
                    # Same sender/recipient countries
                    {
                        "$and": [
                            {"sender.country": sender_country},
                            {"recipient.country": recipient_country}
                        ]
                    }
                ]
            }
        }

        # Stage 2: Union with vector search if embedding provided
        if embedding:
            # Use $unionWith for combining results
            vector_pipeline = [
                {
                    "$vectorSearch": {
                        "index": config.VECTOR_SEARCH_INDEX,
                        "path": "embedding",
                        "queryVector": embedding,
                        "numCandidates": limit * 20,
                        "limit": limit // 2,
                        "filter": {
                            "transaction_type": transaction_type,
                            "status": {"$in": DecisionRepository._DECIDED_STATUSES},
                        }
                    }
                },
                {"$addFields": {"search_method": "vector", "vector_score": {"$meta": "vectorSearchScore"}}}
            ]

            # Combine traditional and vector search
            pipeline = [
                match_stage,
                {"$limit": limit // 2},
                {"$addFields": {"search_method": "traditional", "traditional_score": 1.0}},
                {
                    "$unionWith": {
                        "coll": config.TRANSACTIONS_COLLECTION,
                        "pipeline": vector_pipeline
                    }
                }
            ]
        else:
            pipeline = [match_stage, {"$limit": limit}]

        # Stage 3: Feature-based similarity scoring
        pipeline.extend([
            {
                "$addFields": {
                    "similarity_features": {
                        # Amount proximity score (0-1)
                        "amount_score": {
                            "$cond": [
                                {"$eq": [amount, 0]},
                                0,
                                {
                                    "$subtract": [
                                        1,
                                        {"$min": [
                                            1,
                                            {"$abs": {"$divide": [
                                                {"$subtract": ["$amount", amount]},
                                                amount
                                            ]}}
                                        ]}
                                    ]
                                }
                            ]
                        },
                        # Geographic risk correlation (1 if same countries)
                        "geo_score": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$eq": ["$sender.country", sender_country]},
                                        {"$eq": ["$recipient.country", recipient_country]}
                                    ]
                                },
                                1.0,
                                0.5
                            ]
                        },
                        # Transaction type match
                        "type_score": {
                            "$cond": [
                                {"$eq": ["$transaction_type", transaction_type]},
                                1.0,
                                0.3
                            ]
                        }
                    }
                }
            },
            {
                "$addFields": {
                    # Combine scores with weights, renormalising so a candidate
                    # found by only one search method (vector OR traditional) is
                    # not penalised for the missing branch. Without this, the
                    # max possible combined score is 0.8 for vector-only hits,
                    # which collides with SIMILARITY_THRESHOLD = 0.75.
                    "_raw_score": {
                        "$add": [
                            {"$multiply": [{"$ifNull": ["$vector_score", 0]}, 0.4]},
                            {"$multiply": [{"$ifNull": ["$traditional_score", 0]}, 0.2]},
                            {"$multiply": ["$similarity_features.amount_score", 0.2]},
                            {"$multiply": ["$similarity_features.geo_score", 0.1]},
                            {"$multiply": ["$similarity_features.type_score", 0.1]}
                        ]
                    },
                    "_active_weight": {
                        "$add": [
                            {"$cond": [{"$gt": [{"$ifNull": ["$vector_score", 0]}, 0]}, 0.4, 0]},
                            {"$cond": [{"$gt": [{"$ifNull": ["$traditional_score", 0]}, 0]}, 0.2, 0]},
                            0.2,  # amount_score is always present
                            0.1,  # geo_score is always present
                            0.1,  # type_score is always present
                        ]
                    },
                }
            },
            {
                "$addFields": {
                    "combined_score": {
                        "$cond": [
                            {"$gt": ["$_active_weight", 0]},
                            {"$divide": ["$_raw_score", "$_active_weight"]},
                            0,
                        ]
                    }
                }
            },
            # Sort by combined score
            {"$sort": {"combined_score": -1}},
            {"$limit": limit}
        ])

        # Stage 4: Join with decisions
        pipeline.extend([
            {
                "$lookup": {
                    "from": config.DECISIONS_COLLECTION,
                    "localField": "transaction_id",
                    "foreignField": "transaction_id",
                    "as": "decision"
                }
            },
            {
                "$unwind": {
                    "path": "$decision",
                    "preserveNullAndEmptyArrays": False
                }
            },
            {
                "$project": {
                    "transaction_id": 1,
                    "amount": 1,
                    "transaction_type": 1,
                    "sender": 1,
                    "recipient": 1,
                    "decision": "$decision.decision",
                    "confidence": "$decision.confidence_score",
                    "risk_score": "$decision.risk_score",
                    "risk_factors": "$decision.risk_factors",
                    "similarity_score": "$combined_score",
                    "similarity_features": 1,
                    "search_method": 1
                }
            }
        ])

        try:
            # Use sync connection for this method since it's called from sync context
            db_sync = get_sync_db()
            cursor = db_sync[config.TRANSACTIONS_COLLECTION].aggregate(pipeline, allowDiskUse=True)
            results = list(cursor)
            return [serialize_doc(doc) for doc in results]
        except Exception as e:
            logger.error(f"Hybrid search error: {e}")
            # Fallback to simple vector search if hybrid fails
            if embedding:
                return await DecisionRepository.vector_search_similar_transactions(
                    embedding, transaction_type, limit
                )
            raise e

    # Statuses representing a transaction that has reached a recorded decision
    # (and is therefore useful as a "similar case" precedent for the AI). We
    # exclude only `pending` and `processing` — those are still in-flight and
    # have no decision in the decisions collection. `escalated` and
    # `pending_review` DO have decisions attached (the human-review queue
    # persists the AI recommendation as a decision), so they are valid
    # precedents.
    _DECIDED_STATUSES = [
        "approved",
        "rejected",
        "completed",
        "escalated",
        "pending_review",
        "pending_manager_approval",
    ]

    @staticmethod
    async def vector_search_similar_transactions(
        embedding: List[float],
        transaction_type: str,
        limit: int = 10
    ) -> List[Dict]:
        """Fallback vector-only search for similar transactions.

        Filters on `status` (in addition to `transaction_type`) so we only
        compare against transactions that already have a recorded decision.
        Truly in-flight (`pending`, `processing`) transactions are excluded.
        `numCandidates` follows the `mongodb-search-and-ai` skill
        recommendation of 20x `limit`.
        """
        pipeline = [
            {
                "$vectorSearch": {
                    "index": config.VECTOR_SEARCH_INDEX,
                    "path": "embedding",
                    "queryVector": embedding,
                    "numCandidates": limit * 20,
                    "limit": limit,
                    "filter": {
                        "transaction_type": transaction_type,
                        "status": {"$in": DecisionRepository._DECIDED_STATUSES},
                    }
                }
            },
            {
                "$lookup": {
                    "from": config.DECISIONS_COLLECTION,
                    "localField": "transaction_id",
                    "foreignField": "transaction_id",
                    "as": "decision"
                }
            },
            {
                "$unwind": {
                    "path": "$decision",
                    "preserveNullAndEmptyArrays": False
                }
            },
            {
                "$project": {
                    "transaction_id": 1,
                    "amount": 1,
                    "transaction_type": 1,
                    "sender": 1,
                    "recipient": 1,
                    "decision": "$decision.decision",
                    "confidence": "$decision.confidence_score",
                    "risk_score": "$decision.risk_score",
                    "risk_factors": "$decision.risk_factors",
                    "similarity_score": {"$meta": "vectorSearchScore"}
                }
            }
        ]

        try:
            # Use sync connection for this method since it's often called from sync context
            db_sync = get_sync_db()
            cursor = db_sync[config.TRANSACTIONS_COLLECTION].aggregate(pipeline)
            results = list(cursor)
            return [serialize_doc(doc) for doc in results]
        except Exception as e:
            logger.error(f"Vector search error: {e}")
            raise e

    @staticmethod
    async def graph_network_analysis(
        account_id: str,
        max_depth: int = 3,
        time_window_days: int = 30
    ) -> Dict[str, Any]:
        """Analyze transaction networks for fraud detection using graph traversal."""

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=time_window_days)

        pipeline = [
            # Start with transactions involving the account
            {
                "$match": {
                    "$and": [
                        {
                            "$or": [
                                {"sender.account_number": account_id},
                                {"recipient.account_number": account_id}
                            ]
                        },
                        {"created_at": {"$gte": cutoff_date}}
                    ]
                }
            },
            # Graph lookup to find connected transactions
            {
                "$graphLookup": {
                    "from": config.TRANSACTIONS_COLLECTION,
                    "startWith": "$recipient.account_number",
                    "connectFromField": "recipient.account_number",
                    "connectToField": "sender.account_number",
                    "as": "transaction_chain",
                    "maxDepth": max_depth,
                    "depthField": "chain_depth",
                    "restrictSearchWithMatch": {
                        "created_at": {"$gte": cutoff_date}
                    }
                }
            },
            # Analyze the network
            {
                "$project": {
                    "root_transaction": {
                        "transaction_id": "$transaction_id",
                        "amount": "$amount",
                        "timestamp": "$created_at"
                    },
                    "network_size": {"$size": "$transaction_chain"},
                    "total_network_amount": {"$sum": "$transaction_chain.amount"},
                    "unique_accounts": {
                        "$setUnion": [
                            "$transaction_chain.sender.account_number",
                            "$transaction_chain.recipient.account_number"
                        ]
                    },
                    "chain_depths": "$transaction_chain.chain_depth",
                    "suspicious_patterns": {
                        # Rapid cycling detection
                        "rapid_cycling": {
                            "$gte": [
                                {
                                    "$size": {
                                        "$filter": {
                                            "input": "$transaction_chain",
                                            "cond": {
                                                "$and": [
                                                    {"$eq": ["$$this.chain_depth", max_depth]},
                                                    {"$eq": ["$$this.recipient.account_number", account_id]}
                                                ]
                                            }
                                        }
                                    }
                                },
                                1
                            ]
                        },
                        # Layering detection (many small transactions)
                        "potential_layering": {
                            "$gte": [
                                {
                                    "$size": {
                                        "$filter": {
                                            "input": "$transaction_chain",
                                            "cond": {"$lt": ["$$this.amount", 1000]}
                                        }
                                    }
                                },
                                5
                            ]
                        }
                    }
                }
            },
            # Group to get overall network statistics
            {
                "$group": {
                    "_id": None,
                    "total_transactions": {"$sum": 1},
                    "networks_found": {"$sum": 1},
                    "max_network_size": {"$max": "$network_size"},
                    "total_amount_in_networks": {"$sum": "$total_network_amount"},
                    "suspicious_networks": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$or": [
                                        "$suspicious_patterns.rapid_cycling",
                                        "$suspicious_patterns.potential_layering"
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },
                    "all_unique_accounts": {"$addToSet": "$unique_accounts"}
                }
            }
        ]

        try:
            # Use sync connection for this method since it's called from sync context
            db_sync = get_sync_db()
            cursor = db_sync[config.TRANSACTIONS_COLLECTION].aggregate(
                pipeline,
                allowDiskUse=True
            )
            results = list(cursor)

            if results:
                result = results[0]
                # Flatten unique accounts
                all_accounts = set()
                for account_list in result.get('all_unique_accounts', []):
                    if account_list:
                        all_accounts.update(account_list)

                return {
                    "account_id": account_id,
                    "analysis_period_days": time_window_days,
                    "max_depth_analyzed": max_depth,
                    "total_networks_found": result.get('networks_found', 0),
                    "max_network_size": result.get('max_network_size', 0),
                    "total_amount_in_networks": result.get('total_amount_in_networks', 0),
                    "suspicious_networks_count": result.get('suspicious_networks', 0),
                    "unique_accounts_in_networks": len(all_accounts),
                    "risk_indicators": {
                        "has_suspicious_patterns": result.get('suspicious_networks', 0) > 0,
                        "large_network_detected": result.get('max_network_size', 0) > 10,
                        "high_value_network": result.get('total_amount_in_networks', 0) > 100000
                    }
                }
            else:
                return {
                    "account_id": account_id,
                    "analysis_period_days": time_window_days,
                    "max_depth_analyzed": max_depth,
                    "total_networks_found": 0,
                    "message": "No transaction networks found for this account"
                }

        except Exception as e:
            logger.error(f"Graph network analysis error: {e}")
            raise e

class RuleRepository:
    """Repository for rule operations."""
    
    @staticmethod
    async def create_rule(rule: Rule) -> str:
        """Create a new rule."""
        result = await db.database[config.RULES_COLLECTION].insert_one(
            rule.model_dump()
        )
        return rule.rule_id
    
    @staticmethod
    async def get_active_rules() -> List[Dict]:
        """Get all active rules."""
        cursor = db.database[config.RULES_COLLECTION].find(
            {"status": RuleStatus.ACTIVE.value}
        ).sort("priority", -1)
        
        rules = await cursor.to_list(length=100)
        return [serialize_doc(r) for r in rules]
    
    @staticmethod
    def get_active_rules_sync() -> List[Dict]:
        """Get all active rules (synchronous)."""
        db_sync = get_sync_db()
        rules = list(db_sync[config.RULES_COLLECTION].find(
            {"status": RuleStatus.ACTIVE.value}
        ).sort("priority", -1))
        
        return [serialize_doc(r) for r in rules]
    
    @staticmethod
    async def update_rule_metrics(rule_id: str, triggered: bool, correct: bool):
        """Update rule effectiveness metrics."""
        update = {
            "$inc": {
                "metrics.triggered_count": 1 if triggered else 0,
                "metrics.true_positives": 1 if triggered and correct else 0,
                "metrics.false_positives": 1 if triggered and not correct else 0
            }
        }
        
        await db.database[config.RULES_COLLECTION].update_one(
            {"rule_id": rule_id},
            update
        )
    
    @staticmethod
    def update_rule_metrics_sync(rule_id: str, triggered: bool, correct: bool):
        """Update rule effectiveness metrics (synchronous)."""
        db_sync = get_sync_db()
        update = {
            "$inc": {
                "metrics.triggered_count": 1 if triggered else 0,
                "metrics.true_positives": 1 if triggered and correct else 0,
                "metrics.false_positives": 1 if triggered and not correct else 0
            }
        }
        
        db_sync[config.RULES_COLLECTION].update_one(
            {"rule_id": rule_id},
            update
        )

class HumanReviewRepository:
    """Repository for human review operations."""
    
    @staticmethod
    async def create_review(review: HumanReview) -> str:
        """Create human review record."""
        result = await db.database[config.HUMAN_REVIEWS_COLLECTION].insert_one(
            review.model_dump()
        )
        return review.review_id
    
    @staticmethod
    def create_review_sync_obj(review: HumanReview) -> str:
        """Create human review record (synchronous)."""
        db_sync = get_sync_db()
        db_sync[config.HUMAN_REVIEWS_COLLECTION].insert_one(
            review.model_dump()
        )
        return review.review_id
    
    @staticmethod
    def create_review_sync(review_data: Dict) -> str:
        """Create human review record (synchronous)."""
        db_sync = get_sync_db()
        result = db_sync[config.HUMAN_REVIEWS_COLLECTION].insert_one(review_data)
        return review_data.get("review_id")
    
    @staticmethod
    async def get_pending_reviews(limit: int = 10) -> List[Dict]:
        """Get pending human reviews."""
        cursor = db.database[config.HUMAN_REVIEWS_COLLECTION].find(
            {"status": "pending"}
        ).sort([("priority", -1), ("created_at", 1)]).limit(limit)
        
        reviews = await cursor.to_list(length=limit)
        return [serialize_doc(r) for r in reviews]
    
    @staticmethod
    async def update_review(
        review_id: str, 
        decision: str, 
        notes: str,
        reviewer: str
    ):
        """Update human review with decision."""
        await db.database[config.HUMAN_REVIEWS_COLLECTION].update_one(
            {"review_id": review_id},
            {
                "$set": {
                    "status": "completed",
                    "completed_at": datetime.now(timezone.utc),
                    "human_decision": {
                        "decision": decision,
                        "reviewer": reviewer,
                        "timestamp": datetime.now(timezone.utc)
                    },
                    "notes": notes
                }
            }
        )
    
    @staticmethod
    def complete_review_sync(
        review_id: str,
        decision: str,
        reviewer: str,
        notes: str = None
    ):
        """Complete human review with decision (synchronous)."""
        db_sync = get_sync_db()
        db_sync[config.HUMAN_REVIEWS_COLLECTION].update_one(
            {"review_id": review_id},
            {
                "$set": {
                    "status": "completed",
                    "completed_at": datetime.now(timezone.utc),
                    "human_decision": {
                        "decision": decision,
                        "reviewer": reviewer,
                        "timestamp": datetime.now(timezone.utc)
                    },
                    "notes": notes,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )

class NotificationRepository:
    """Repository for notification operations."""
    
    @staticmethod
    async def create_notification(notification: Notification) -> str:
        """Create notification record."""
        result = await db.database[config.NOTIFICATIONS_COLLECTION].insert_one(
            notification.model_dump()
        )
        return notification.notification_id
    
    @staticmethod
    def create_notification_sync_obj(notification: Notification) -> str:
        """Create notification record (synchronous)."""
        db_sync = get_sync_db()
        db_sync[config.NOTIFICATIONS_COLLECTION].insert_one(
            notification.model_dump()
        )
        return notification.notification_id
    
    @staticmethod
    def create_notification_sync(notification_data: Dict) -> str:
        """Create notification record (synchronous)."""
        db_sync = get_sync_db()
        result = db_sync[config.NOTIFICATIONS_COLLECTION].insert_one(notification_data)
        return notification_data.get("notification_id")
    
    @staticmethod
    async def get_pending_notifications() -> List[Dict]:
        """Get pending notifications."""
        cursor = db.database[config.NOTIFICATIONS_COLLECTION].find(
            {"status": NotificationStatus.PENDING.value}
        ).sort("created_at", 1).limit(100)
        
        notifications = await cursor.to_list(length=100)
        return [serialize_doc(n) for n in notifications]
    
    @staticmethod
    async def mark_as_sent(notification_id: str):
        """Mark notification as sent."""
        await db.database[config.NOTIFICATIONS_COLLECTION].update_one(
            {"notification_id": notification_id},
            {
                "$set": {
                    "status": NotificationStatus.SENT.value,
                    "sent_at": datetime.now(timezone.utc)
                }
            }
        )
    
    @staticmethod
    def mark_as_sent_sync(notification_id: str):
        """Mark notification as sent (synchronous)."""
        db_sync = get_sync_db()
        db_sync[config.NOTIFICATIONS_COLLECTION].update_one(
            {"notification_id": notification_id},
            {
                "$set": {
                    "status": NotificationStatus.SENT.value,
                    "sent_at": datetime.now(timezone.utc)
                }
            }
        )

class AuditRepository:
    """Repository for audit operations."""
    
    @staticmethod
    async def create_audit_event(event: AuditEvent) -> str:
        """Create audit event."""
        result = await db.database[config.AUDIT_EVENTS_COLLECTION].insert_one(
            event.model_dump()
        )
        return event.event_id
    
    @staticmethod
    def create_audit_event_sync(event: AuditEvent) -> str:
        """Create audit event (synchronous for Temporal)."""
        db_sync = get_sync_db()
        result = db_sync[config.AUDIT_EVENTS_COLLECTION].insert_one(
            event.model_dump()
        )
        return event.event_id
    
    @staticmethod
    async def get_recent_events(
        transaction_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """Get recent audit events."""
        query = {}
        if transaction_id:
            query["transaction_id"] = transaction_id
        
        cursor = db.database[config.AUDIT_EVENTS_COLLECTION].find(
            query
        ).sort("timestamp", -1).limit(limit)
        
        events = await cursor.to_list(length=limit)
        return [serialize_doc(e) for e in events]

class MetricsRepository:
    """Repository for metrics operations."""
    
    @staticmethod
    async def record_metric(metric: SystemMetric):
        """Record a system metric."""
        await db.database[config.SYSTEM_METRICS_COLLECTION].insert_one(
            metric.model_dump()
        )
    
    @staticmethod
    def record_metric_sync(metric: SystemMetric):
        """Record a system metric (synchronous)."""
        db_sync = get_sync_db()
        db_sync[config.SYSTEM_METRICS_COLLECTION].insert_one(
            metric.model_dump()
        )
    
    @staticmethod
    async def get_recent_metrics(metric_name: str, minutes: int = 60) -> List[Dict]:
        """Get recent metrics."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        
        cursor = db.database[config.SYSTEM_METRICS_COLLECTION].find({
            "metric_name": metric_name,
            "timestamp": {"$gte": cutoff}
        }).sort("timestamp", -1)
        
        metrics = await cursor.to_list(length=1000)
        return [serialize_doc(m) for m in metrics]
    
    @staticmethod
    async def get_aggregated_metrics(hours: int = 24) -> Dict:
        """Get aggregated metrics for dashboard."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        
        pipeline = [
            {"$match": {"timestamp": {"$gte": cutoff}}},
            {
                "$group": {
                    "_id": "$metric_name",
                    "avg_value": {"$avg": "$value"},
                    "min_value": {"$min": "$value"},
                    "max_value": {"$max": "$value"},
                    "count": {"$sum": 1}
                }
            }
        ]
        
        cursor = await db.database[config.SYSTEM_METRICS_COLLECTION].aggregate(pipeline)
        results = await cursor.to_list(length=100)
        
        return {
            metric["_id"]: {
                "avg": metric["avg_value"],
                "min": metric["min_value"],
                "max": metric["max_value"],
                "count": metric["count"]
            }
            for metric in results
        }