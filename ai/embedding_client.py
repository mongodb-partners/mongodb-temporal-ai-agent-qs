"""Embedding client with Voyage AI primary and Cohere fallback."""

import json
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from utils.config import config

logger = logging.getLogger(__name__)

@dataclass
class EmbeddingResult:
    """Result of embedding generation."""
    embedding: List[float]
    model: str
    dimensions: int

class EmbeddingClient:
    """Unified embedding client with Voyage primary and Cohere fallback."""

    def __init__(self):
        """Initialize the embedding client."""
        self._voyage_client = None
        self._bedrock_client = None
        self._initialize_clients()

    def _initialize_clients(self):
        """Initialize embedding provider clients."""
        # Initialize Voyage client if API key is available
        if config.VOYAGE_API_KEY:
            try:
                import voyageai
                self._voyage_client = voyageai.Client(api_key=config.VOYAGE_API_KEY)
                logger.info("Voyage AI client initialized successfully")
            except ImportError:
                logger.warning("voyageai package not installed, falling back to Cohere only")
            except Exception as e:
                logger.warning(f"Failed to initialize Voyage client: {e}")
        else:
            logger.info("VOYAGE_API_KEY not set, using Cohere only")

        # Initialize Bedrock client for Cohere fallback
        try:
            import boto3
            self._bedrock_client = boto3.client(
                'bedrock-runtime',
                region_name=config.AWS_REGION,
                aws_access_key_id=config.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY
            )
            logger.info("Bedrock client initialized for Cohere fallback")
        except Exception as e:
            logger.error(f"Failed to initialize Bedrock client: {e}")

    async def get_embedding(self, text: str) -> EmbeddingResult:
        """
        Generate embedding using Voyage (primary) with Cohere fallback.

        Args:
            text: Text to embed

        Returns:
            EmbeddingResult containing embedding, model used, and dimensions

        Raises:
            Exception: If both Voyage and Cohere fail
        """
        # Try Voyage first if available
        if self._voyage_client:
            try:
                return await self._get_voyage_embedding(text)
            except Exception as voyage_error:
                logger.warning(f"Voyage embedding failed, falling back to Cohere: {voyage_error}")

        # Fallback to Cohere via Bedrock
        if self._bedrock_client:
            try:
                return await self._get_cohere_embedding(text)
            except Exception as cohere_error:
                logger.error(f"Cohere embedding failed: {cohere_error}")
                raise Exception(f"Both embedding providers failed. Voyage: {voyage_error if 'voyage_error' in locals() else 'Not attempted'}, Cohere: {cohere_error}")

        raise Exception("No embedding providers available")

    async def _get_voyage_embedding(self, text: str) -> EmbeddingResult:
        """Generate embedding using the configured Voyage model.

        Forwards `output_dimension` so the produced vector matches the Atlas
        vector index dimension (1024). Voyage 3-family models (including
        voyage-4) honour this parameter; older models that do not understand
        it will simply ignore it.
        """
        try:
            result = self._voyage_client.embed(
                texts=[text],
                model=config.VOYAGE_MODEL,
                input_type="document",
                output_dimension=config.VOYAGE_OUTPUT_DIMENSION,
            )

            embedding = result.embeddings[0]

            logger.debug(
                "Generated Voyage embedding (model=%s, dim=%d)",
                config.VOYAGE_MODEL,
                len(embedding),
            )

            return EmbeddingResult(
                embedding=embedding,
                model=config.VOYAGE_MODEL,
                dimensions=len(embedding)
            )

        except Exception as e:
            logger.error(f"Voyage embedding generation failed: {e}")
            raise

    async def _get_cohere_embedding(self, text: str) -> EmbeddingResult:
        """Generate embedding using Cohere via Bedrock."""
        try:
            response = self._bedrock_client.invoke_model(
                modelId=config.COHERE_MODEL,
                body=json.dumps({
                    "texts": [text],
                    "input_type": "search_document",
                    "truncate": "END"
                })
            )

            result = json.loads(response['body'].read())
            embedding = result['embeddings'][0]

            logger.debug(f"Successfully generated Cohere embedding with {len(embedding)} dimensions")

            return EmbeddingResult(
                embedding=embedding,
                model=config.COHERE_MODEL,
                dimensions=len(embedding)
            )

        except Exception as e:
            logger.error(f"Cohere embedding generation failed: {e}")
            raise

    def prepare_transaction_text(self, transaction: Dict[str, Any], enriched_data: Optional[Dict[str, Any]] = None) -> str:
        """
        Prepare transaction data for embedding generation with finance-specific features.

        Args:
            transaction: Transaction data
            enriched_data: Additional enriched data

        Returns:
            Formatted text optimized for financial domain embedding
        """
        # Build comprehensive text representation for financial analysis
        embedding_text = f"""
Transaction Type: {transaction.get('transaction_type', 'unknown')}
Amount: {transaction.get('amount', 0)} {transaction.get('currency', 'USD')}
Time Pattern: {self._classify_time_pattern(transaction.get('timestamp'))}
Geographic Risk: {transaction.get('sender', {}).get('country', 'Unknown')} to {transaction.get('recipient', {}).get('country', 'Unknown')}
Account Age: {transaction.get('sender', {}).get('account_age_days', 'Unknown')} days
Business Context: {transaction.get('business_purpose', 'personal')}
Payment Method: {transaction.get('payment_method', 'wire')}
"""

        # Add enriched data if available
        if enriched_data:
            risk_flags = enriched_data.get('risk_flags', [])
            if risk_flags:
                embedding_text += f"Risk Flags: {', '.join(risk_flags)}\n"

            regulatory_flags = enriched_data.get('regulatory_flags', [])
            if regulatory_flags:
                embedding_text += f"Regulatory Flags: {', '.join(regulatory_flags)}\n"

            velocity_context = enriched_data.get('velocity_1h', 0)
            if velocity_context:
                embedding_text += f"Velocity Context: {velocity_context} transactions in 1 hour\n"

        return embedding_text.strip()

    def _classify_time_pattern(self, timestamp) -> str:
        """Classify transaction timing pattern for embedding."""
        if not timestamp:
            return "unknown"

        try:
            from datetime import datetime
            if isinstance(timestamp, str):
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            else:
                dt = timestamp

            hour = dt.hour
            weekday = dt.weekday()

            # Business hours check
            if 9 <= hour <= 17 and weekday < 5:
                return "business_hours"
            elif weekday >= 5:
                return "weekend"
            elif hour < 6 or hour > 22:
                return "unusual_hours"
            else:
                return "off_hours"

        except Exception:
            return "unknown"

    def get_available_models(self) -> List[str]:
        """Get list of available embedding models."""
        models = []
        if self._voyage_client:
            models.append(config.VOYAGE_MODEL)
        if self._bedrock_client:
            models.append(config.COHERE_MODEL)
        return models

    def health_check(self) -> Dict[str, Any]:
        """Check health of embedding providers."""
        return {
            "voyage_available": self._voyage_client is not None,
            "cohere_available": self._bedrock_client is not None,
            "primary_model": config.VOYAGE_MODEL if self._voyage_client else config.COHERE_MODEL,
            "available_models": self.get_available_models()
        }

# Singleton instance
embedding_client = EmbeddingClient()