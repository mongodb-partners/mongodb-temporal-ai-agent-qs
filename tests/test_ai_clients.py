"""Unit tests for ai/bedrock_client, ai/groq_client, and ai/embedding_client.

Spec: .specs/test-coverage-fill/refactor.md (REQ-R-301, REQ-R-303)

External SDKs (boto3, voyageai, groq) are mocked. No network calls.
"""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# bedrock_client
# ---------------------------------------------------------------------------

@pytest.fixture
def bedrock():
    """Fresh BedrockClient with mocked boto3 client."""
    from ai.bedrock_client import BedrockClient
    bc = BedrockClient()
    bc.client = MagicMock()
    return bc


def _bedrock_invoke_response(text: str):
    """Build the nested response shape boto3.invoke_model returns."""
    body = json.dumps({"content": [{"text": text}]}).encode("utf-8")
    return {"body": io.BytesIO(body)}


def _bedrock_embedding_response(vector):
    body = json.dumps({"embeddings": [vector]}).encode("utf-8")
    return {"body": io.BytesIO(body)}


class TestBedrockClient:
    @pytest.mark.asyncio
    async def test_get_embedding_returns_vector(self, bedrock):
        bedrock.client.invoke_model.return_value = _bedrock_embedding_response([0.1, 0.2])
        result = await bedrock.get_embedding("hello")
        assert result == [0.1, 0.2]

    @pytest.mark.asyncio
    async def test_get_embedding_propagates_error(self, bedrock):
        bedrock.client.invoke_model.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            await bedrock.get_embedding("hello")

    @pytest.mark.asyncio
    async def test_analyze_transaction_parses_json_in_markdown(self, bedrock):
        text = '```json\n{"decision":"approve","confidence":95,"reasoning":"ok"}\n```'
        bedrock.client.invoke_model.return_value = _bedrock_invoke_response(text)
        result = await bedrock.analyze_transaction("prompt")
        assert result["decision"] == "approve"
        assert result["confidence"] == 95

    @pytest.mark.asyncio
    async def test_analyze_transaction_parses_raw_json(self, bedrock):
        text = '{"decision":"reject","confidence":90}'
        bedrock.client.invoke_model.return_value = _bedrock_invoke_response(text)
        result = await bedrock.analyze_transaction("prompt")
        assert result["decision"] == "reject"

    @pytest.mark.asyncio
    async def test_analyze_transaction_maps_flag_to_escalate(self, bedrock):
        text = '{"decision":"flag","confidence":50}'
        bedrock.client.invoke_model.return_value = _bedrock_invoke_response(text)
        result = await bedrock.analyze_transaction("prompt")
        assert result["decision"] == "escalate"

    @pytest.mark.asyncio
    async def test_analyze_transaction_normalises_string_confidence(self, bedrock):
        text = '{"decision":"approve","confidence":"95%"}'
        bedrock.client.invoke_model.return_value = _bedrock_invoke_response(text)
        result = await bedrock.analyze_transaction("prompt")
        assert result["confidence"] == 95.0

    @pytest.mark.asyncio
    async def test_analyze_transaction_falls_back_on_unparsable(self, bedrock):
        text = "DECISION: approve\nCONFIDENCE: 88%\nthis is not json"
        bedrock.client.invoke_model.return_value = _bedrock_invoke_response(text)
        result = await bedrock.analyze_transaction("prompt")
        assert result["decision"] == "approve"
        assert result["confidence"] == 88.0
        assert result["reasoning"]  # falls back to full text

    @pytest.mark.asyncio
    async def test_fallback_decision_lines(self, bedrock):
        # Cover all four fallback decision branches: approve, reject, flag, default.
        for text, expected in (
            ("DECISION: APPROVE THIS\nblah", "approve"),
            ("DECISION: REJECT THIS\nblah", "reject"),
            ("DECISION: FLAG THIS\nblah", "escalate"),
            ("DECISION: maybe?\nblah", "escalate"),
        ):
            bedrock.client.invoke_model.return_value = _bedrock_invoke_response(text)
            result = await bedrock.analyze_transaction("prompt")
            assert result["decision"] == expected

    @pytest.mark.asyncio
    async def test_fallback_confidence_parse_failure_swallowed(self, bedrock):
        text = "DECISION: approve\nCONFIDENCE: not-a-number\n"
        bedrock.client.invoke_model.return_value = _bedrock_invoke_response(text)
        result = await bedrock.analyze_transaction("prompt")
        assert result["decision"] == "approve"
        assert result["confidence"] == 50  # default

    @pytest.mark.asyncio
    async def test_analyze_transaction_propagates_invoke_error(self, bedrock):
        bedrock.client.invoke_model.side_effect = RuntimeError("aws down")
        with pytest.raises(RuntimeError):
            await bedrock.analyze_transaction("prompt")


# ---------------------------------------------------------------------------
# groq_client
# ---------------------------------------------------------------------------

@pytest.fixture
def groq():
    from ai.groq_client import GroqClient
    gc = GroqClient()
    gc.client = MagicMock()
    gc.async_client = MagicMock()
    return gc


def _groq_chat_response(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestGroqClient:
    @pytest.mark.asyncio
    async def test_analyze_transaction_async_parses_json(self, groq):
        groq.async_client.chat.completions.create = AsyncMock(
            return_value=_groq_chat_response('{"decision":"approve","confidence":92}')
        )
        result = await groq.analyze_transaction("prompt")
        assert result["decision"] == "approve"
        assert result["confidence"] == 92

    @pytest.mark.asyncio
    async def test_analyze_transaction_async_propagates_error(self, groq):
        groq.async_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("no"))
        with pytest.raises(RuntimeError):
            await groq.analyze_transaction("prompt")

    def test_analyze_transaction_sync(self, groq):
        groq.client.chat.completions.create.return_value = _groq_chat_response(
            '{"decision":"reject","confidence":80}'
        )
        result = groq.analyze_transaction_sync("prompt")
        assert result["decision"] == "reject"

    def test_analyze_transaction_sync_propagates_error(self, groq):
        groq.client.chat.completions.create.side_effect = RuntimeError("nope")
        with pytest.raises(RuntimeError):
            groq.analyze_transaction_sync("prompt")

    @pytest.mark.asyncio
    async def test_generate_completion_with_system_prompt(self, groq):
        groq.async_client.chat.completions.create = AsyncMock(
            return_value=_groq_chat_response("the answer")
        )
        out = await groq.generate_completion(
            "Q?",
            system_prompt="You are X.",
            temperature=0.5,
            max_tokens=50,
        )
        assert out == "the answer"
        called_with = groq.async_client.chat.completions.create.await_args.kwargs
        # System message present
        assert any(m["role"] == "system" for m in called_with["messages"])

    @pytest.mark.asyncio
    async def test_generate_completion_propagates_error(self, groq):
        groq.async_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("x"))
        with pytest.raises(RuntimeError):
            await groq.generate_completion("q")

    def test_parse_markdown_wrapped_json(self, groq):
        text = '```json\n{"decision":"approve","confidence":99}\n```'
        result = groq._parse_llm_response(text)
        assert result["decision"] == "approve"

    def test_parse_invalid_decision_defaults_to_escalate(self, groq):
        text = '{"decision":"hold","confidence":50}'
        result = groq._parse_llm_response(text)
        assert result["decision"] == "escalate"

    def test_parse_flag_mapped_to_escalate(self, groq):
        text = '{"decision":"flag","confidence":50}'
        result = groq._parse_llm_response(text)
        assert result["decision"] == "escalate"

    def test_parse_string_confidence_normalised(self, groq):
        text = '{"decision":"approve","confidence":"95%"}'
        result = groq._parse_llm_response(text)
        assert result["confidence"] == 95.0

    def test_parse_missing_confidence_defaults(self, groq):
        text = '{"decision":"approve"}'
        result = groq._parse_llm_response(text)
        assert result["confidence"] == 50

    def test_parse_invalid_json_uses_fallback(self, groq):
        text = "DECISION: APPROVE\nCONFIDENCE: 70\nRISK: amount, geo"
        result = groq._parse_llm_response(text)
        assert result["decision"] == "approve"
        assert result["confidence"] == 70.0
        assert "amount" in result["risk_factors"]

    def test_parse_unexpected_exception_uses_fallback(self, groq, monkeypatch):
        """Force a non-JSONDecodeError exception inside the parse try-block to
        cover the generic ``except Exception`` branch that delegates to the
        fallback parser. We do this by stubbing json.loads to raise something
        other than JSONDecodeError."""
        from ai import groq_client as gc_mod

        def boom(_):
            raise ValueError("unexpected")

        monkeypatch.setattr(gc_mod.json, "loads", boom)
        result = groq._parse_llm_response('{"decision":"approve"}')
        assert result["decision"] == "escalate"
        assert result["confidence"] == 50

    def test_fallback_decision_branches(self, groq):
        for text, expected in (
            ("DECISION: APPROVE\n", "approve"),
            ("DECISION: REJECT\n", "reject"),
            ("DECISION: SOMETHING\n", "escalate"),
        ):
            assert groq._fallback_parse(text)["decision"] == expected

    def test_fallback_confidence_parse_failure(self, groq):
        text = "DECISION: APPROVE\nCONFIDENCE: not-a-number\n"
        result = groq._fallback_parse(text)
        assert result["confidence"] == 50  # default

    @pytest.mark.asyncio
    async def test_stream_completion_yields_content(self, groq):
        async def fake_stream():
            for chunk_text in ("hello", " ", "world"):
                delta = MagicMock()
                delta.content = chunk_text
                choice = MagicMock()
                choice.delta = delta
                chunk = MagicMock()
                chunk.choices = [choice]
                yield chunk

        groq.async_client.chat.completions.create = AsyncMock(return_value=fake_stream())
        chunks = []
        async for c in groq.stream_completion("q"):
            chunks.append(c)
        assert "".join(chunks) == "hello world"

    @pytest.mark.asyncio
    async def test_stream_completion_skips_empty_deltas(self, groq):
        async def fake_stream():
            for chunk_text in (None, "x"):
                delta = MagicMock()
                delta.content = chunk_text
                choice = MagicMock()
                choice.delta = delta
                chunk = MagicMock()
                chunk.choices = [choice]
                yield chunk

        groq.async_client.chat.completions.create = AsyncMock(return_value=fake_stream())
        out = "".join([c async for c in groq.stream_completion("q")])
        assert out == "x"

    @pytest.mark.asyncio
    async def test_stream_completion_propagates_error(self, groq):
        groq.async_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("x"))
        with pytest.raises(RuntimeError):
            async for _ in groq.stream_completion("q"):
                pass


# ---------------------------------------------------------------------------
# embedding_client
# ---------------------------------------------------------------------------

class TestEmbeddingClient:
    """The EmbeddingClient is constructed at import time; we test individual
    methods on fresh instances with mocked SDK clients.
    """

    def _make_client(self, *, voyage=True, bedrock=True):
        from ai.embedding_client import EmbeddingClient

        # Bypass __init__'s SDK initialisation entirely so we can wire
        # whichever clients we want.
        client = EmbeddingClient.__new__(EmbeddingClient)
        client._voyage_client = MagicMock() if voyage else None
        client._bedrock_client = MagicMock() if bedrock else None
        return client

    def test_initialise_without_voyage_key(self, monkeypatch):
        from ai import embedding_client as ec_mod

        monkeypatch.setattr(ec_mod.config, "VOYAGE_API_KEY", "")
        # Force boto3 to succeed
        with patch.object(ec_mod, "logger"):
            client = ec_mod.EmbeddingClient()
        assert client._voyage_client is None
        assert client._bedrock_client is not None

    def test_initialise_voyage_import_failure(self, monkeypatch):
        from ai import embedding_client as ec_mod

        # API key present but the import fails -> warning branch.
        monkeypatch.setattr(ec_mod.config, "VOYAGE_API_KEY", "key")
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

        def fake_import(name, *a, **k):
            if name == "voyageai":
                raise ImportError("not installed")
            return real_import(name, *a, **k)

        with patch("builtins.__import__", side_effect=fake_import):
            client = ec_mod.EmbeddingClient()
        assert client._voyage_client is None

    def test_initialise_voyage_other_failure(self, monkeypatch):
        """voyageai.Client raising a non-ImportError -> ``except Exception``
        branch fires and _voyage_client stays None.

        ``voyageai`` is imported *inside* _initialize_clients, so we patch
        the symbol on the cached module in sys.modules.
        """
        import sys
        import voyageai
        from ai import embedding_client as ec_mod

        monkeypatch.setattr(ec_mod.config, "VOYAGE_API_KEY", "key")
        monkeypatch.setattr(voyageai, "Client", MagicMock(side_effect=RuntimeError("auth")))
        # Ensure the module is the cached one the embed-client will pick up.
        assert sys.modules["voyageai"] is voyageai
        client = ec_mod.EmbeddingClient()
        assert client._voyage_client is None

    def test_initialise_bedrock_failure(self, monkeypatch):
        """boto3.client raising during init -> bedrock_client is None."""
        import boto3
        from ai import embedding_client as ec_mod

        monkeypatch.setattr(ec_mod.config, "VOYAGE_API_KEY", "")
        monkeypatch.setattr(boto3, "client", MagicMock(side_effect=RuntimeError("aws fail")))
        client = ec_mod.EmbeddingClient()
        assert client._bedrock_client is None

    @pytest.mark.asyncio
    async def test_get_embedding_uses_voyage_first(self):
        client = self._make_client()
        client._voyage_client.embed.return_value = MagicMock(
            embeddings=[[0.1] * 1024]
        )
        result = await client.get_embedding("hello")
        assert len(result.embedding) == 1024
        assert result.dimensions == 1024

    @pytest.mark.asyncio
    async def test_voyage_failure_falls_back_to_cohere(self):
        client = self._make_client()
        client._voyage_client.embed.side_effect = RuntimeError("voyage down")
        client._bedrock_client.invoke_model.return_value = _bedrock_embedding_response([0.2] * 1024)
        result = await client.get_embedding("hello")
        assert result.dimensions == 1024

    @pytest.mark.asyncio
    async def test_both_providers_fail_raises(self):
        client = self._make_client()
        client._voyage_client.embed.side_effect = RuntimeError("voyage down")
        client._bedrock_client.invoke_model.side_effect = RuntimeError("bedrock down")
        with pytest.raises(Exception, match="Both embedding providers failed"):
            await client.get_embedding("hello")

    @pytest.mark.asyncio
    async def test_no_providers_available_raises(self):
        client = self._make_client(voyage=False, bedrock=False)
        with pytest.raises(Exception, match="No embedding providers"):
            await client.get_embedding("hello")

    @pytest.mark.asyncio
    async def test_voyage_only_path(self):
        client = self._make_client(bedrock=False)
        client._voyage_client.embed.return_value = MagicMock(embeddings=[[0.5] * 1024])
        result = await client.get_embedding("text")
        assert result.dimensions == 1024

    @pytest.mark.asyncio
    async def test_cohere_only_path(self):
        client = self._make_client(voyage=False)
        client._bedrock_client.invoke_model.return_value = _bedrock_embedding_response([0.3] * 1024)
        result = await client.get_embedding("text")
        assert result.dimensions == 1024

    def test_prepare_transaction_text_includes_fields(self):
        client = self._make_client()
        text = client.prepare_transaction_text({
            "transaction_type": "wire_transfer",
            "amount": 1000,
            "currency": "USD",
            "sender": {"country": "US", "account_age_days": 30},
            "recipient": {"country": "GB"},
        })
        assert "wire_transfer" in text
        assert "1000" in text
        assert "30 days" in text

    def test_prepare_transaction_text_with_enriched(self):
        client = self._make_client()
        text = client.prepare_transaction_text(
            {"transaction_type": "ach", "amount": 100, "sender": {}, "recipient": {}},
            enriched_data={
                "risk_flags": ["x", "y"],
                "regulatory_flags": ["z"],
                "velocity_1h": 3,
            },
        )
        assert "Risk Flags: x, y" in text
        assert "Regulatory Flags: z" in text
        assert "Velocity Context: 3" in text

    def test_classify_time_pattern_branches(self):
        from datetime import datetime, timezone
        client = self._make_client()
        # Business hours
        bh = datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc)  # Mon 10:00
        assert client._classify_time_pattern(bh) == "business_hours"
        # Weekend
        we = datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc)  # Sat
        assert client._classify_time_pattern(we) == "weekend"
        # Unusual hours (early morning weekday)
        uh = datetime(2026, 5, 11, 3, 0, tzinfo=timezone.utc)  # Mon 03:00
        assert client._classify_time_pattern(uh) == "unusual_hours"
        # Off hours (e.g. 19:00 weekday)
        oh = datetime(2026, 5, 11, 19, 0, tzinfo=timezone.utc)  # Mon 19:00
        assert client._classify_time_pattern(oh) == "off_hours"

    def test_classify_time_pattern_handles_string_iso(self):
        client = self._make_client()
        assert client._classify_time_pattern("2026-05-11T10:00:00Z") == "business_hours"

    def test_classify_time_pattern_handles_none_and_invalid(self):
        client = self._make_client()
        assert client._classify_time_pattern(None) == "unknown"
        assert client._classify_time_pattern("garbage") == "unknown"

    def test_get_available_models(self):
        client = self._make_client()
        models = client.get_available_models()
        assert len(models) == 2
        client_no_voyage = self._make_client(voyage=False)
        assert len(client_no_voyage.get_available_models()) == 1

    def test_health_check(self):
        client = self._make_client()
        h = client.health_check()
        assert h["voyage_available"] is True
        assert h["cohere_available"] is True
        assert h["available_models"]
