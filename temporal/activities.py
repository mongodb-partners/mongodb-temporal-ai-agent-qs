"""Activities for transaction processing workflow."""

import random
import asyncio
import uuid
from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timedelta, timezone
from temporalio import activity

from database.repositories import (
    TransactionRepository,
    DecisionRepository,
    AuditRepository,
    MetricsRepository,
    CustomerRepository,
    RuleRepository,
    HumanReviewRepository,
    NotificationRepository,
)
from database.account_repository import (
    AccountRepository,
    InsufficientFundsError,
    AccountNotFoundError,
)
from database.schemas import (
    TransactionDecision,
    AuditEvent,
    SystemMetric,
    DecisionType,
    TransactionType,
    HumanReview,
    Notification,
    NotificationStatus,
)
from ai.bedrock_client import bedrock_client
from ai.groq_client import groq_client
from ai.embedding_client import embedding_client
from ai.prompts import create_transaction_analysis_prompt, create_risk_assessment_prompt
from services.risk_engine import RiskEngine
from services.rule_engine import RuleEngine
from temporal.shared import TransactionDetails, RiskAssessment, InsufficientDataError
from utils.logger import transaction_logger, logger
from utils.config import config
from utils.decimal_utils import to_decimal128, from_decimal128, decimal_to_float
from utils.temporal_serialization import prepare_activity_result
from decimal import Decimal


class TransactionActivities:
    """Activities for transaction processing."""

    @activity.defn
    async def validate_and_hold_funds(
        self, transaction_details: TransactionDetails
    ) -> Dict[str, Any]:
        """Validate accounts and place hold on funds."""
        try:
            # Create or get accounts for both parties
            # Determine initial balance based on transaction amount for demo
            # Ensure accounts have enough funds for larger transactions
            # Convert amount from string to Decimal for calculations
            amount_decimal = Decimal(str(transaction_details.amount))
            initial_balance = max(Decimal("500000"), amount_decimal * 5)
            activity.logger.info(
                f"Amount: {transaction_details.amount}, Decimal: {amount_decimal}, Initial balance: {initial_balance}"
            )

            sender_account = AccountRepository.get_or_create_account_sync(
                account_number=transaction_details.sender.get("account_number"),
                customer_name=transaction_details.sender.get("name"),
                initial_balance=initial_balance,  # Dynamic demo balance
            )

            recipient_account = AccountRepository.get_or_create_account_sync(
                account_number=transaction_details.recipient.get("account_number"),
                customer_name=transaction_details.recipient.get("name"),
                initial_balance=50000.0,  # Demo initial balance
            )

            # Check sufficient funds
            has_funds, available_balance = (
                AccountRepository.check_sufficient_funds_sync(
                    sender_account.account_number, amount_decimal
                )
            )

            if not has_funds:
                transaction_logger.log_insufficient_funds(
                    account_number=sender_account.account_number,
                    transaction_id=transaction_details.transaction_id,
                    requested_amount=float(amount_decimal),
                    available_balance=available_balance,
                )
                raise InsufficientFundsError(
                    f"Insufficient funds: Available ${available_balance:.2f}, "
                    f"Requested ${float(amount_decimal):.2f}"
                )

            # Place hold on funds
            hold_id = AccountRepository.place_hold_sync(
                account_number=sender_account.account_number,
                amount=amount_decimal,
                transaction_id=transaction_details.transaction_id,
                reason="Transaction processing",
            )

            transaction_logger.log_transaction(
                transaction_id=transaction_details.transaction_id,
                event="FUNDS_VALIDATION",
                details={
                    "sender_balance": str(
                        sender_account.balance
                    ),  # Convert to string for JSON
                    "amount": str(
                        transaction_details.amount
                    ),  # Already a string, but ensure it
                    "hold_id": hold_id,
                    "status": "funds_held",
                },
            )

            return prepare_activity_result(
                {
                    "validation_status": "success",
                    "hold_id": hold_id,
                    "sender_balance": sender_account.balance,
                    "recipient_balance": recipient_account.balance,
                }
            )

        except (InsufficientFundsError, AccountNotFoundError) as e:
            activity.logger.error(f"Funds validation failed: {e}")
            raise
        except Exception as e:
            activity.logger.error(f"Error in funds validation: {e}")
            raise

    @activity.defn
    async def enrich_transaction_data(
        self, transaction_details: TransactionDetails
    ) -> Dict[str, Any]:
        """Enrich transaction with customer data, rules, and risk indicators."""
        start_time = datetime.now(timezone.utc)

        try:
            # Get customer data
            customer_id = transaction_details.sender.get("customer_id")
            if customer_id:
                customer_history = TransactionRepository.get_customer_history_sync(
                    customer_id
                )
            else:
                # Try to find/create customer
                customer_id = CustomerRepository.get_or_create_customer_sync(
                    transaction_details.sender
                )
                transaction_details.sender["customer_id"] = customer_id
                customer_history = TransactionRepository.get_customer_history_sync(
                    customer_id
                )

            # Build transaction dict for rule evaluation
            # Convert amount string to float for rule evaluation
            amount_value = float(Decimal(str(transaction_details.amount)))
            transaction_dict = {
                "transaction_id": transaction_details.transaction_id,
                "transaction_type": transaction_details.transaction_type,
                "amount": amount_value,
                "currency": transaction_details.currency,
                "sender": transaction_details.sender,
                "recipient": transaction_details.recipient,
                "reference_number": transaction_details.reference_number,
                "metadata": transaction_details.metadata,
            }

            # Apply rules
            rule_results = RuleEngine.apply_rules(transaction_dict)

            # Combine risk flags
            risk_flags = (
                transaction_details.risk_flags.copy()
                if transaction_details.risk_flags
                else []
            )
            risk_flags.extend(rule_results["risk_flags"])

            # Additional risk flag checks
            if amount_value > 50000:
                risk_flags.append("high_amount")

            # Check for structuring pattern (amounts just under $5000 threshold)
            if 4900 <= amount_value < 5000:
                risk_flags.append("structuring_pattern")
                risk_flags.append("suspicious_amount")
                activity.logger.warning(
                    f"Potential structuring detected: Amount ${amount_value} "
                    f"just under $5000 reporting threshold"
                )

            # Check time-based risks
            current_hour = datetime.now(timezone.utc).hour
            if current_hour < 6 or current_hour > 22:
                risk_flags.append("unusual_time")
                transaction_dict["metadata"]["unusual_time"] = True

            # Check for international transactions
            if transaction_details.transaction_type == "international":
                risk_flags.append("cross_border")

                recipient_country = transaction_details.recipient.get("country", "")
                if recipient_country in config.HIGH_RISK_COUNTRIES:
                    risk_flags.append("high_risk_country")

            # Check for new recipient
            if customer_history.get("common_recipients"):
                if (
                    transaction_details.recipient.get("name")
                    not in customer_history["common_recipients"]
                ):
                    risk_flags.append("new_recipient")

            # Calculate velocity metrics
            velocity_data = self._calculate_velocity_metrics(
                customer_id=customer_id,
                sender_account=transaction_details.sender.get("account_number"),
            )

            # Add velocity data to metadata for rule evaluation
            if not transaction_dict["metadata"]:
                transaction_dict["metadata"] = {}
            transaction_dict["metadata"].update(velocity_data)

            # Check velocity thresholds
            if velocity_data.get("velocity_1h", 0) > 3:
                risk_flags.append("high_velocity_1h")
                activity.logger.warning(
                    f"High velocity detected for customer {customer_id}: "
                    f"{velocity_data['velocity_1h']} transactions in last hour"
                )

            if velocity_data.get("velocity_24h", 0) > 10:
                risk_flags.append("high_velocity_24h")

            if velocity_data.get("total_amount_1h", 0) > 100000:
                risk_flags.append("high_amount_velocity")

            # Re-apply rules with updated metadata including velocity
            rule_results = RuleEngine.apply_rules(transaction_dict)

            # Create enriched data
            enriched_data = {
                "transaction": transaction_dict,
                "customer_history": customer_history,
                "rule_results": rule_results,
                "risk_flags": list(set(risk_flags)),  # Deduplicate
                "enrichment_time_ms": int(
                    (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                ),
            }

            # Update customer behavior profile
            # Note: This is commented out to avoid the async call issue
            # The customer profile updates will be handled separately if needed

            # Log enrichment
            activity.logger.info(
                f"Enriched transaction {transaction_details.transaction_id} "
                f"with {len(risk_flags)} risk flags and {rule_results['rule_count']} rules triggered"
            )

            return prepare_activity_result(enriched_data)

        except Exception as e:
            activity.logger.error(f"Error enriching transaction: {e}")
            raise InsufficientDataError(f"Failed to enrich transaction data: {str(e)}")

    def _calculate_velocity_metrics(
        self, customer_id: str, sender_account: str
    ) -> Dict[str, Any]:
        """Calculate transaction velocity metrics for a customer/account."""
        try:
            from database.connection import get_sync_db

            db_sync = get_sync_db()

            now = datetime.now(timezone.utc)
            one_hour_ago = now - timedelta(hours=1)
            twenty_four_hours_ago = now - timedelta(hours=24)

            # Query transactions in last hour
            transactions_1h = list(
                db_sync[config.TRANSACTIONS_COLLECTION].find(
                    {
                        "$or": [
                            {"sender.customer_id": customer_id},
                            {"sender.account_number": sender_account},
                        ],
                        "created_at": {"$gte": one_hour_ago},
                    }
                )
            )

            # Query transactions in last 24 hours
            transactions_24h = list(
                db_sync[config.TRANSACTIONS_COLLECTION].find(
                    {
                        "$or": [
                            {"sender.customer_id": customer_id},
                            {"sender.account_number": sender_account},
                        ],
                        "created_at": {"$gte": twenty_four_hours_ago},
                    }
                )
            )

            # Calculate metrics
            velocity_1h = len(transactions_1h)
            velocity_24h = len(transactions_24h)
            # Convert Decimal128 to float for sum operations
            # Handle missing or None amounts by providing 0 as default
            total_amount_1h = sum(
                (
                    decimal_to_float(t.get("amount"))
                    if t.get("amount") is not None
                    else 0.0
                )
                for t in transactions_1h
            )
            total_amount_24h = sum(
                (
                    decimal_to_float(t.get("amount"))
                    if t.get("amount") is not None
                    else 0.0
                )
                for t in transactions_24h
            )

            # Calculate time since last transaction
            time_since_last = None
            if transactions_1h:
                last_transaction_time = max(
                    t.get("created_at") for t in transactions_1h if t.get("created_at")
                )
                if last_transaction_time:
                    # Ensure both datetimes are timezone-aware
                    if last_transaction_time.tzinfo is None:
                        last_transaction_time = last_transaction_time.replace(
                            tzinfo=timezone.utc
                        )
                    time_since_last = (now - last_transaction_time).total_seconds()

            return {
                "velocity_1h": velocity_1h,
                "velocity_24h": velocity_24h,
                "total_amount_1h": total_amount_1h,
                "total_amount_24h": total_amount_24h,
                "time_since_last_seconds": time_since_last,
                "velocity_calculated_at": now.isoformat(),
            }
        except Exception as e:
            activity.logger.warning(f"Failed to calculate velocity metrics: {e}")
            # Return default values if calculation fails
            return {
                "velocity_1h": 0,
                "velocity_24h": 0,
                "total_amount_1h": 0,
                "total_amount_24h": 0,
                "time_since_last_seconds": None,
                "velocity_calculated_at": datetime.now(timezone.utc).isoformat(),
            }

    @activity.defn
    async def perform_risk_assessment(
        self, enriched_data: Dict[str, Any]
    ) -> RiskAssessment:
        """Perform risk assessment on the transaction."""
        start_time = datetime.now(timezone.utc)

        try:
            transaction = enriched_data["transaction"]
            rule_results = enriched_data.get("rule_results", {})

            # Get risk score from AI
            risk_prompt = create_risk_assessment_prompt(transaction)
            if config.LLM_PROVIDER == "groq":
                risk_analysis = await groq_client.analyze_transaction(risk_prompt)
            else:
                risk_analysis = await bedrock_client.analyze_transaction(risk_prompt)
            activity.logger.info(
                f"Risk Analysis: {risk_analysis}",
                "\n LLM_PROVIDER: ",
                config.LLM_PROVIDER,
            )  # Debug print

            # Adjust risk score based on rules
            base_risk = risk_analysis.get("risk_score", 50)
            if rule_results.get("recommended_action") == "reject":
                base_risk = max(base_risk, 90)
            elif rule_results.get("recommended_action") == "escalate":
                base_risk = max(base_risk, 70)

            # Perform compliance checks
            # Check KYC status from sender data or customer history
            kyc_status = transaction.get("sender", {}).get("kyc_status")
            if not kyc_status:
                kyc_status = enriched_data["customer_history"].get(
                    "kyc_status", "pending"
                )

            compliance_checks = {
                "sanctions_check": True,  # Mock - would call sanctions API
                "aml_check": True,
                "kyc_verified": kyc_status == "approved",
            }

            # Special checks for international transactions
            if transaction["transaction_type"] == "international":
                compliance_checks["ofac_check"] = (
                    transaction["recipient"].get("country")
                    not in config.HIGH_RISK_COUNTRIES
                )
                compliance_checks["fatf_check"] = True

            # Determine if enhanced due diligence is required
            requires_edd = (
                base_risk > 70
                or transaction["amount"] > 100000
                or "high_risk_country" in enriched_data.get("risk_flags", [])
                or rule_results.get("recommended_action") in ["escalate", "reject"]
            )

            assessment = RiskAssessment(
                risk_score=base_risk,
                risk_level=RiskEngine.determine_risk_level(base_risk).value,
                risk_factors=risk_analysis.get("key_risk_factors", [])
                + enriched_data.get("risk_flags", []),
                requires_enhanced_diligence=requires_edd,
                compliance_checks=compliance_checks,
            )

            # Record metrics
            try:
                metric = SystemMetric(
                    metric_type="performance",
                    metric_name="risk_assessment_time_ms",
                    value=int(
                        (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                    ),
                    unit="milliseconds",
                    dimensions={
                        "transaction_type": transaction["transaction_type"],
                        "risk_level": assessment.risk_level,
                        "rules_triggered": len(rule_results.get("triggered_rules", [])),
                    },
                )
                MetricsRepository.record_metric_sync(metric)
            except Exception as metric_error:
                activity.logger.warning(f"Failed to record metric: {metric_error}")

            activity.logger.info(
                f"Risk assessment complete: score={assessment.risk_score}, "
                f"level={assessment.risk_level}, edd_required={requires_edd}"
            )

            # Convert dataclass to dict and sanitize
            return prepare_activity_result(
                {
                    "risk_score": assessment.risk_score,
                    "risk_level": assessment.risk_level,
                    "risk_factors": assessment.risk_factors,
                    "requires_enhanced_diligence": assessment.requires_enhanced_diligence,
                    "compliance_checks": assessment.compliance_checks,
                }
            )

        except Exception as e:
            activity.logger.error(f"Error in risk assessment: {e}")
            return RiskAssessment(
                risk_score=75.0,
                risk_level="high",
                risk_factors=["assessment_error"],
                requires_enhanced_diligence=True,
                compliance_checks={"error": False},
            )

    @activity.defn
    async def find_similar_transactions(
        self, enriched_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Find similar historical transactions using hybrid search approach."""
        try:
            transaction = enriched_data["transaction"]

            # Try to generate embedding using Voyage finance-2 with Cohere fallback
            embedding = None
            embedding_model = None
            try:
                # Prepare transaction text for embedding
                embedding_text = embedding_client.prepare_transaction_text(
                    transaction, enriched_data
                )

                # Get embedding using dual provider strategy
                embedding_result = await embedding_client.get_embedding(embedding_text)
                embedding = embedding_result.embedding
                embedding_model = embedding_result.model

                # Store embedding for this transaction
                TransactionRepository.store_embedding_sync(
                    transaction["transaction_id"], embedding, embedding_model
                )

                activity.logger.info(
                    f"Generated {embedding_result.dimensions}D embedding using {embedding_model}"
                )

            except Exception as embed_error:
                activity.logger.warning(f"Could not generate embedding: {embed_error}")
                # Continue with traditional search only

            # Use hybrid search combining vector and traditional indexes
            similar_cases = await DecisionRepository.hybrid_search_similar_transactions(
                embedding, transaction, limit=config.MAX_SIMILAR_CASES
            )

            # Filter by similarity threshold. similarity_score may come back as a
            # string when the aggregation produced Decimal128 components (the shared
            # serialize_doc helper stringifies Decimal128).
            def _score(case):
                raw = case.get("similarity_score", 0)
                try:
                    return float(raw) if raw is not None else 0.0
                except (TypeError, ValueError):
                    return 0.0

            filtered_cases = [
                case
                for case in similar_cases
                if _score(case) >= config.SIMILARITY_THRESHOLD
            ]

            activity.logger.info(
                f"Found {len(filtered_cases)} similar transactions "
                f"(from {len(similar_cases)} candidates)"
            )

            return prepare_activity_result(filtered_cases[:5])  # Return top 5

        except Exception as e:
            activity.logger.error(f"Error finding similar transactions: {e}")
            return prepare_activity_result([])

    @activity.defn
    async def ai_decision_analysis(
        self,
        enriched_data: Dict[str, Any],
        risk_assessment: RiskAssessment,
        similar_cases: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Perform AI analysis for decision making."""
        start_time = datetime.now(timezone.utc)

        try:
            transaction = enriched_data["transaction"]
            customer_history = enriched_data["customer_history"]
            rule_results = enriched_data.get("rule_results", {})

            # Check for compliance violations first
            compliance_checks = (
                risk_assessment.compliance_checks
                if hasattr(risk_assessment, "compliance_checks")
                else {}
            )
            if compliance_checks and not all(compliance_checks.values()):
                failed_checks = [k for k, v in compliance_checks.items() if not v]
                # Check for critical compliance failures (OFAC, sanctions)
                critical_failures = [
                    check
                    for check in failed_checks
                    if check in ["ofac_check", "sanctions_check"]
                ]
                if critical_failures:
                    # Return rejection decision instead of raising error
                    activity.logger.error(
                        f"Critical compliance checks failed: {', '.join(critical_failures)}"
                    )
                    return {
                        "decision": "reject",
                        "confidence": 100.0,
                        "reasoning": f"Transaction rejected due to compliance violation: {', '.join(critical_failures)}",
                        "risk_factors": ["compliance_violation", "sanctions_risk"]
                        + enriched_data.get("risk_flags", []),
                        "compliance_notes": f"Failed compliance checks: {', '.join(critical_failures)}. Transaction blocked for regulatory compliance.",
                        "rules_triggered": rule_results.get("triggered_rules", []),
                        "risk_assessment": {
                            "risk_score": 100,
                            "risk_level": "critical",
                            "risk_factors": ["compliance_violation"],
                        },
                        "processing_time_ms": int(
                            (datetime.now(timezone.utc) - start_time).total_seconds()
                            * 1000
                        ),
                    }
                # Log other non-critical failures but continue processing
                if failed_checks:
                    logger.warning(
                        f"Non-critical compliance checks failed: {', '.join([c for c in failed_checks if c not in critical_failures])}"
                    )

            # Check if rules recommend rejection
            if rule_results.get("recommended_action") == "reject":
                return {
                    "decision": "reject",
                    "confidence": 95.0,
                    "reasoning": f"Transaction rejected by rules: {', '.join(rule_results.get('triggered_rules', []))}",
                    "risk_factors": enriched_data.get("risk_flags", []),
                    "compliance_notes": "Automatic rejection based on rule engine",
                    "rules_triggered": rule_results.get("triggered_rules", []),
                }

            # Create AI prompt with rule context
            prompt = create_transaction_analysis_prompt(
                transaction, similar_cases, customer_history
            )

            # Add rule information to prompt
            if rule_results.get("triggered_rules"):
                prompt += (
                    f"\n\nRULES TRIGGERED: {', '.join(rule_results['triggered_rules'])}"
                )
                prompt += f"\nRULE RECOMMENDATION: {rule_results.get('recommended_action', 'none')}"

            # Get AI decision
            if config.LLM_PROVIDER == "groq":
                ai_result = await groq_client.analyze_transaction(prompt)
            else:
                ai_result = await bedrock_client.analyze_transaction(prompt)
            activity.logger.info(
                f"AI Result: {ai_result}", "\n LLM_PROVIDER: ", config.LLM_PROVIDER
            )  # Debug print

            # Override with rule recommendation if AI confidence is low
            if ai_result.get("confidence", 0) < 70 and rule_results.get(
                "recommended_action"
            ):
                # Map rule actions to valid decision types
                action_mapping = {
                    "flag": "escalate",  # Map flag to escalate
                    "approve": "approve",
                    "reject": "reject",
                    "escalate": "escalate",
                    "hold": "escalate",  # Map hold to escalate
                }
                mapped_action = action_mapping.get(
                    rule_results["recommended_action"], "escalate"
                )
                ai_result["decision"] = mapped_action
                ai_result[
                    "reasoning"
                ] += f" | Overridden by rule engine: {rule_results['recommended_action']} (mapped to {mapped_action})"

            # Enhance AI result
            ai_result["risk_assessment"] = {
                "risk_score": (
                    risk_assessment.risk_score
                    if hasattr(risk_assessment, "risk_score")
                    else 50
                ),
                "risk_level": (
                    risk_assessment.risk_level
                    if hasattr(risk_assessment, "risk_level")
                    else "medium"
                ),
                "risk_factors": (
                    risk_assessment.risk_factors
                    if hasattr(risk_assessment, "risk_factors")
                    else []
                ),
            }
            ai_result["similar_case_ids"] = [
                {
                    "transaction_id": case.get("transaction_id"),
                    "similarity": case.get("similarity_score", 0),
                }
                for case in similar_cases[:3]
            ]
            ai_result["processing_time_ms"] = int(
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
            ai_result["rules_triggered"] = rule_results.get("triggered_rules", [])

            # Record decision metric
            try:
                metric = SystemMetric(
                    metric_type="ai_decision",
                    metric_name="decision_confidence",
                    value=ai_result.get("confidence", 0),
                    unit="percentage",
                    dimensions={
                        "decision": ai_result["decision"],
                        "transaction_type": transaction["transaction_type"],
                    },
                )
                MetricsRepository.record_metric_sync(metric)
            except Exception as metric_error:
                activity.logger.warning(f"Failed to record metric: {metric_error}")

            activity.logger.info(
                f"AI analysis complete: decision={ai_result['decision']}, "
                f"confidence={ai_result.get('confidence', 0):.1f}%, "
                f"rules_triggered={len(rule_results.get('triggered_rules', []))}"
            )

            return ai_result

        except Exception as e:
            activity.logger.error(f"Error in AI analysis: {e}")
            return {
                "decision": "escalate",
                "confidence": 0,
                "reasoning": f"Unable to complete AI analysis: {str(e)}",
                "risk_factors": ["system_error"],
                "compliance_notes": "Manual review required due to system error",
                "risk_assessment": {
                    "risk_score": 100,
                    "risk_level": "very_high",
                    "risk_factors": ["analysis_failed"],
                },
                "rules_triggered": rule_results.get("triggered_rules", []),
            }

    @activity.defn
    async def store_decision(
        self,
        transaction_id: str,
        ai_result: Dict[str, Any],
        workflow_id: str,
        temporal_run_id: str,
    ) -> str:
        """Store the AI decision in the database."""
        llm_model_version = ""
        if config.LLM_PROVIDER == "groq":
            llm_model_version = config.GROQ_MODEL_ID
        else:
            llm_model_version = config.BEDROCK_MODEL_VERSION
        try:
            # Create decision record with rules information
            decision = TransactionDecision(
                transaction_id=transaction_id,
                decision=DecisionType(ai_result["decision"]),
                confidence_score=ai_result.get("confidence", 0),
                risk_score=ai_result.get("risk_assessment", {}).get("risk_score", 50),
                model_version=llm_model_version,
                processing_time_ms=ai_result.get("processing_time_ms", 0),
                reasoning={
                    "primary_reasoning": ai_result.get("reasoning", ""),
                    "risk_factors": ai_result.get("risk_factors", []),
                    "compliance_notes": ai_result.get("compliance_notes", ""),
                },
                similar_cases=ai_result.get("similar_case_ids", []),
                rules_triggered=ai_result.get("rules_triggered", []),
                workflow_id=workflow_id,
                temporal_run_id=temporal_run_id,
            )

            # Store decision
            decision_id = DecisionRepository.create_decision_sync(decision)

            # Create comprehensive audit event
            try:
                audit_event = AuditEvent(
                    event_type="ai_decision_made",
                    event_category="transaction_processing",
                    transaction_id=transaction_id,
                    decision_id=decision_id,
                    event_data={
                        "decision": ai_result["decision"],
                        "confidence": ai_result.get("confidence", 0),
                        "risk_score": ai_result.get("risk_assessment", {}).get(
                            "risk_score", 50
                        ),
                        "rules_triggered": ai_result.get("rules_triggered", []),
                        "similar_cases_used": len(
                            ai_result.get("similar_case_ids", [])
                        ),
                    },
                )
                AuditRepository.create_audit_event_sync(audit_event)
            except Exception as audit_error:
                activity.logger.warning(f"Failed to create audit event: {audit_error}")

            # Update transaction status
            status_map = {
                "approve": "approved",
                "reject": "rejected",
                "escalate": "pending_review",
            }

            try:
                TransactionRepository.update_status_sync(
                    transaction_id, status_map.get(ai_result["decision"], "processing")
                )
            except Exception as status_error:
                activity.logger.warning(
                    f"Failed to update transaction status: {status_error}"
                )

            # Update rule metrics
            for rule_id in ai_result.get("rules_triggered", []):
                try:
                    # Mark rule as triggered and assume correct for now
                    RuleRepository.update_rule_metrics_sync(rule_id, True, True)
                except Exception as rule_error:
                    activity.logger.warning(
                        f"Failed to update rule metrics: {rule_error}"
                    )

            activity.logger.info(
                f"Stored decision {decision_id} for transaction {transaction_id}"
            )

            return decision_id

        except Exception as e:
            activity.logger.error(f"Error storing decision: {e}")
            return f"DEC_ERROR_{uuid.uuid4().hex[:8].upper()}"

    @activity.defn
    async def queue_for_human_review(
        self, transaction_id: str, ai_result: Dict[str, Any]
    ) -> str:
        """Queue transaction for human review."""
        try:
            # Determine priority based on risk score
            risk_score = ai_result.get("risk_assessment", {}).get("risk_score", 50)
            if risk_score > 80:
                priority = "urgent"
            elif risk_score > 60:
                priority = "high"
            elif risk_score > 40:
                priority = "medium"
            else:
                priority = "low"

            # Create human review record
            review = HumanReview(
                transaction_id=transaction_id,
                priority=priority,
                sla_deadline=datetime.now(timezone.utc)
                + timedelta(hours=4 if priority == "urgent" else 24),
                ai_recommendation={
                    "decision": ai_result.get("decision", "escalate"),
                    "confidence": ai_result.get("confidence", 0),
                    "reasoning": ai_result.get("reasoning", ""),
                    "risk_factors": ai_result.get("risk_factors", []),
                    "rules_triggered": ai_result.get("rules_triggered", []),
                },
                status="pending",
            )

            # Store in database
            review_id = HumanReviewRepository.create_review_sync_obj(review)

            activity.logger.info(
                f"Queued transaction {transaction_id} for human review "
                f"with {priority} priority (review_id: {review_id})"
            )

            # Create audit event
            try:
                audit_event = AuditEvent(
                    event_type="escalated_to_human",
                    event_category="transaction_processing",
                    transaction_id=transaction_id,
                    event_data={
                        "reason": (
                            "low_confidence"
                            if ai_result.get("confidence", 0) < 70
                            else "high_risk"
                        ),
                        "ai_confidence": ai_result.get("confidence", 0),
                        "priority": priority,
                        "review_id": review_id,
                    },
                )
                AuditRepository.create_audit_event_sync(audit_event)
            except Exception as audit_error:
                activity.logger.warning(f"Failed to create audit event: {audit_error}")

            return review_id

        except Exception as e:
            activity.logger.error(f"Error queuing for review: {e}")
            return f"REV_ERROR_{uuid.uuid4().hex[:8].upper()}"

    @activity.defn
    async def send_notification(
        self, transaction_id: str, decision: str, message: str
    ) -> bool:
        """Send and store notification about transaction decision."""
        try:
            # Create notification record
            notification = Notification(
                notification_type="decision",
                priority="high" if decision == "reject" else "medium",
                status=NotificationStatus.PENDING,
                recipients=[
                    {"type": "dashboard", "identifier": "all_users"},
                    {"type": "email", "identifier": "risk-team@example.com"},
                ],
                subject=f"Transaction {decision.upper()}: {transaction_id}",
                message=message,
                metadata={
                    "decision": decision,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                transaction_id=transaction_id,
            )

            # Store notification
            notification_id = NotificationRepository.create_notification_sync_obj(
                notification
            )

            NotificationRepository.mark_as_sent_sync(notification_id)

            activity.logger.info(
                f"Notification {notification_id} created and sent for "
                f"transaction {transaction_id}: {decision}"
            )

            return True

        except Exception as e:
            activity.logger.error(f"Error sending notification: {e}")
            return False

    @activity.defn
    async def execute_fund_transfer(
        self,
        transaction_id: str,
        sender_account: str,
        recipient_account: str,
        amount: Union[float, str],  # Accept both float and string
        hold_id: str,
    ) -> bool:
        """Execute the actual fund transfer using ACID transactions."""
        try:
            # Convert amount to float if it's a string
            amount_value = float(amount) if isinstance(amount, str) else amount
            # Release the hold first
            AccountRepository.release_hold_sync(hold_id)

            # Execute ACID transfer
            result = AccountRepository.execute_transfer_with_acid(
                sender_account=sender_account,
                recipient_account=recipient_account,
                amount=amount_value,
                transaction_id=transaction_id,
                description=f"Transfer for transaction {transaction_id}",
            )

            if result:
                transaction_logger.log_transaction(
                    transaction_id=transaction_id,
                    event="TRANSFER_COMPLETED",
                    details={
                        "sender": sender_account,
                        "recipient": recipient_account,
                        "amount": amount_value,
                        "status": "success",
                    },
                )

            return result

        except Exception as e:
            activity.logger.error(f"Fund transfer failed: {e}")
            # Release hold if transfer fails
            try:
                AccountRepository.release_hold_sync(hold_id)
            except:
                pass
            return False

    @activity.defn
    async def cleanup_hold(self, hold_id: str) -> bool:
        """Cleanup/release a hold if it exists."""
        try:
            if hold_id:
                AccountRepository.release_hold_sync(hold_id)
                activity.logger.info(f"Cleaned up hold {hold_id}")
                return True
        except Exception as e:
            activity.logger.warning(f"Failed to cleanup hold {hold_id}: {e}")
        return False

    @activity.defn
    async def analyze_fraud_network(
        self, enriched_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze transaction networks for potential fraud patterns using graph traversal."""
        try:
            transaction = enriched_data["transaction"]

            # Extract account identifiers
            sender_account = transaction.get("sender", {}).get("account_number")
            recipient_account = transaction.get("recipient", {}).get("account_number")

            if not sender_account:
                return {
                    "network_analysis_performed": False,
                    "reason": "No sender account number available",
                }

            # Perform graph network analysis for sender
            sender_network = await DecisionRepository.graph_network_analysis(
                account_id=sender_account, max_depth=3, time_window_days=30
            )

            # Perform graph network analysis for recipient if available
            recipient_network = None
            if recipient_account:
                recipient_network = await DecisionRepository.graph_network_analysis(
                    account_id=recipient_account, max_depth=3, time_window_days=30
                )

            # Combine network insights
            network_risk_score = 0
            network_risk_factors = []

            # Analyze sender network
            if sender_network.get("risk_indicators", {}).get("has_suspicious_patterns"):
                network_risk_score += 30
                network_risk_factors.append(
                    "Sender involved in suspicious transaction patterns"
                )

            if sender_network.get("risk_indicators", {}).get("large_network_detected"):
                network_risk_score += 20
                network_risk_factors.append("Sender part of large transaction network")

            if sender_network.get("risk_indicators", {}).get("high_value_network"):
                network_risk_score += 25
                network_risk_factors.append("High-value transactions in sender network")

            # Analyze recipient network
            if recipient_network:
                if recipient_network.get("risk_indicators", {}).get(
                    "has_suspicious_patterns"
                ):
                    network_risk_score += 20
                    network_risk_factors.append(
                        "Recipient involved in suspicious patterns"
                    )

                if recipient_network.get("unique_accounts_in_networks", 0) > 20:
                    network_risk_score += 15
                    network_risk_factors.append("Recipient connected to many accounts")

            # Check for circular patterns (money coming back to sender)
            if (
                sender_network.get("risk_indicators", {}).get("has_suspicious_patterns")
                and recipient_network
                and recipient_network.get("risk_indicators", {}).get(
                    "has_suspicious_patterns"
                )
            ):
                network_risk_score += 30
                network_risk_factors.append(
                    "Potential money laundering network detected"
                )

            activity.logger.info(
                f"Network analysis complete for transaction {transaction['transaction_id']}: "
                f"risk_score={network_risk_score}, factors={len(network_risk_factors)}"
            )

            return {
                "network_analysis_performed": True,
                "network_risk_score": min(network_risk_score, 100),  # Cap at 100
                "network_risk_factors": network_risk_factors,
                "sender_network": sender_network,
                "recipient_network": recipient_network,
                "combined_unique_accounts": (
                    sender_network.get("unique_accounts_in_networks", 0)
                    + (
                        recipient_network.get("unique_accounts_in_networks", 0)
                        if recipient_network
                        else 0
                    )
                ),
                "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            activity.logger.error(f"Error in fraud network analysis: {e}")
            return {"network_analysis_performed": False, "error": str(e)}
