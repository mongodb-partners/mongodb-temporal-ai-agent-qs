"""Tests for the voyage-4 embedding migration and real seed embeddings.

Spec: .specs/voyage-4-embeddings/

Traceability:
  REQ-E-101 -> test_voyage_model_is_voyage_4
  REQ-E-102 -> test_voyage_call_passes_output_dimension,
               test_voyage_output_dimension_constant_is_1024
  REQ-E-103 -> test_setup_mongodb_no_random_seed_embeddings,
               test_seed_helper_uses_real_embedding
  REQ-E-104 -> test_seed_helper_returns_model_name
  REQ-E-105 -> test_seed_helper_skips_on_provider_failure
  INV-101   -> test_vector_dimension_unchanged
  INV-102   -> test_embedding_result_shape_unchanged
  INV-104   -> test_prepare_transaction_text_unchanged_format
"""

from __future__ import annotations

import asyncio
import importlib
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
SETUP_SOURCE = (ROOT / "scripts" / "setup_mongodb.py").read_text()
EMBEDDING_SOURCE = (ROOT / "ai" / "embedding_client.py").read_text()


def _fresh_config():
    sys.modules.pop("utils.config", None)
    return importlib.import_module("utils.config").config


def _fresh_embedding_client():
    sys.modules.pop("ai.embedding_client", None)
    return importlib.import_module("ai.embedding_client")


# ---------------------------------------------------------------------------
# REQ-E-101: voyage-4 is the configured model
# ---------------------------------------------------------------------------

def test_voyage_model_is_voyage_4():
    cfg = _fresh_config()
    assert cfg.VOYAGE_MODEL == "voyage-4", (
        f"VOYAGE_MODEL must default to 'voyage-4' (env-overridable), got {cfg.VOYAGE_MODEL!r}"
    )


# ---------------------------------------------------------------------------
# REQ-E-102: explicit 1024-dim output forwarded to the Voyage SDK
# ---------------------------------------------------------------------------

def test_voyage_output_dimension_constant_is_1024():
    cfg = _fresh_config()
    assert getattr(cfg, "VOYAGE_OUTPUT_DIMENSION", None) == 1024, (
        "VOYAGE_OUTPUT_DIMENSION constant must be 1024 to match the Atlas index"
    )


def test_voyage_call_passes_output_dimension():
    """REQ-E-102: _get_voyage_embedding forwards output_dimension to the SDK."""
    ec_mod = _fresh_embedding_client()
    client = ec_mod.EmbeddingClient.__new__(ec_mod.EmbeddingClient)
    voyage_stub = MagicMock()
    voyage_stub.embed.return_value.embeddings = [[0.1] * 1024]
    client._voyage_client = voyage_stub
    client._bedrock_client = None

    asyncio.run(client._get_voyage_embedding("hello world"))

    kwargs = voyage_stub.embed.call_args.kwargs
    assert kwargs.get("output_dimension") == 1024, (
        f"Voyage embed() must be called with output_dimension=1024, got kwargs={kwargs}"
    )
    assert kwargs.get("model") == "voyage-4", (
        f"Voyage embed() must use voyage-4 model, got {kwargs.get('model')!r}"
    )


# ---------------------------------------------------------------------------
# REQ-E-103: setup_mongodb does not write random seed embeddings
# ---------------------------------------------------------------------------

def test_setup_mongodb_no_random_seed_embeddings():
    """REQ-E-103: no `[random.random() for _ in range(...)]` in seed code."""
    bad = re.findall(
        r"\[\s*random\.random\(\)\s+for\s+_\s+in\s+range\(",
        SETUP_SOURCE,
    )
    assert not bad, (
        f"setup_mongodb still constructs random vectors ({len(bad)} remaining). "
        f"REQ-E-103: seed transactions must use embedding_client.get_embedding()."
    )


def _make_seed_helper_module():
    """Import scripts.setup_mongodb fresh so we can pick up the helper."""
    sys.modules.pop("scripts.setup_mongodb", None)
    return importlib.import_module("scripts.setup_mongodb")


def test_seed_helper_uses_real_embedding():
    """REQ-E-103/104: helper returns embedding + model from embedding_client."""
    setup_mod = _make_seed_helper_module()
    assert hasattr(setup_mod, "_seed_embedding"), (
        "scripts/setup_mongodb must expose a `_seed_embedding(transaction)` helper"
    )

    fake_result = MagicMock()
    fake_result.embedding = [0.42] * 1024
    fake_result.model = "voyage-4"
    fake_result.dimensions = 1024

    captured_text = {}

    class FakeClient:
        def prepare_transaction_text(self, txn, enriched=None):
            captured_text["text"] = "TXT::" + txn.get("transaction_id", "")
            return captured_text["text"]

        async def get_embedding(self, text):
            captured_text["called_with"] = text
            return fake_result

    setup_mod.embedding_client = FakeClient()

    txn = {"transaction_id": "TXN_TEST_001", "transaction_type": "ach", "amount": 100}
    result = asyncio.run(setup_mod._seed_embedding(txn))

    assert result is not None, "seed helper returned None for a successful call"
    embedding, model = result
    assert embedding == [0.42] * 1024
    assert model == "voyage-4"
    assert captured_text["called_with"] == "TXT::TXN_TEST_001"


def test_seed_helper_returns_model_name():
    """REQ-E-104: helper output includes the model name actually used."""
    setup_mod = _make_seed_helper_module()
    fake_result = MagicMock()
    fake_result.embedding = [0.1] * 1024
    fake_result.model = "cohere.embed-english-v3"
    fake_result.dimensions = 1024

    class FakeClient:
        def prepare_transaction_text(self, txn, enriched=None):
            return "irrelevant"

        async def get_embedding(self, text):
            return fake_result

    setup_mod.embedding_client = FakeClient()
    embedding, model = asyncio.run(setup_mod._seed_embedding({"transaction_id": "T"}))
    assert model == "cohere.embed-english-v3"


def test_seed_helper_skips_on_provider_failure():
    """REQ-E-105: if both providers fail, helper returns None and does not raise."""
    setup_mod = _make_seed_helper_module()

    class FailingClient:
        def prepare_transaction_text(self, txn, enriched=None):
            return "irrelevant"

        async def get_embedding(self, text):
            raise RuntimeError("Voyage and Cohere both failed")

    setup_mod.embedding_client = FailingClient()
    result = asyncio.run(setup_mod._seed_embedding({"transaction_id": "T"}))
    assert result is None, (
        "seed helper must return None on provider failure (no random fallback)"
    )


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------

def test_vector_dimension_unchanged():
    """INV-101: VECTOR_DIMENSION stays at 1024 to keep the Atlas index valid."""
    cfg = _fresh_config()
    assert cfg.VECTOR_DIMENSION == 1024


def test_embedding_result_shape_unchanged():
    """INV-102: EmbeddingResult fields unchanged."""
    ec_mod = _fresh_embedding_client()
    fields = {f.name for f in __import__("dataclasses").fields(ec_mod.EmbeddingResult)}
    assert fields == {"embedding", "model", "dimensions"}


def test_prepare_transaction_text_unchanged_format():
    """INV-104: prepare_transaction_text still emits the same labelled multi-line text."""
    ec_mod = _fresh_embedding_client()
    client = ec_mod.EmbeddingClient.__new__(ec_mod.EmbeddingClient)
    txn = {
        "transaction_type": "ach",
        "amount": 1000,
        "currency": "USD",
        "sender": {"country": "US"},
        "recipient": {"country": "US"},
    }
    text = client.prepare_transaction_text(txn)
    for label in ("Transaction Type:", "Amount:", "Geographic Risk:", "Payment Method:"):
        assert label in text, f"prepare_transaction_text dropped label {label!r}"
