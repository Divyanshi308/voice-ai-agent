"""Comprehensive tests for the voice AI agent pipeline components."""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the project root is importable and that missing / env-dependent
# modules are available before any project imports happen.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Mock ``tts`` module – it does not exist in the repo yet.
_tts_stub = types.ModuleType("tts")


class _FakeTTS:
    async def pre_cache_common_phrases(self):
        pass

    async def stop(self):
        pass

    async def stream_speech(self, text, send_chunk, language="en"):
        pass


_tts_stub.TextToSpeech = _FakeTTS
sys.modules.setdefault("tts", _tts_stub)

# Mock ``config`` – the real one raises on missing env vars at import time.
_config_stub = types.ModuleType("config")


class _FakeSettings:
    deepgram_api_key = "test-key"
    openai_api_key = "test-key"
    elevenlabs_api_key = "test-key"
    telnyx_api_key = "test-key"
    telnyx_connection_id = "test-id"
    zendesk_subdomain = "test"
    zendesk_email = "test@test.com"
    zendesk_api_key = "test-key"
    twilio_account_sid = "test-sid"
    twilio_auth_token = "test-token"
    twilio_from_number = "+15551234567"
    redis_url = "redis://localhost:6379"
    database_url = "postgresql://localhost/test"
    max_call_duration_seconds = 300
    escalation_confidence_threshold = 0.5


_config_stub.config = _FakeSettings()  # type: ignore[attr-defined]
sys.modules.setdefault("config", _config_stub)

# Mock heavy third-party imports that aren't needed for unit tests.
for mod_name in ("structlog", "psycopg", "redis", "redis.asyncio", "httpx"):
    sys.modules.setdefault(mod_name, types.ModuleType(mod_name))

# Now safe to import project modules.
from dialogue import (  # noqa: E402
    DialogueManager,
    FIELD_QUESTIONS,
    OPTIONAL_FIELDS,
    PRIORITY_ORDER,
    REQUIRED_FIELDS,
)
from guardrails import Guardrails  # noqa: E402
from pipeline import AudioPipeline  # noqa: E402


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def dialogue():
    return DialogueManager()


@pytest.fixture
def guardrails():
    return Guardrails()


@pytest.fixture
def sample_state(dialogue):
    return dialogue.create_state(call_id="call-001", caller_id="+15551234567")


# ── TEST 1 ─────────────────────────────────────────────────────────────────


class TestDialogueStateCreation:
    def test_dialogue_state_creation(self, dialogue):
        state = dialogue.create_state(call_id="call-abc", caller_id="+1234567890")

        assert state["call_id"] == "call-abc"
        assert state["caller_id"] == "+1234567890"
        assert state["languages_used"] == []
        assert state["current_language"] == "en"
        assert state["turn_count"] == 0
        assert state["collected_fields"] == {}
        assert state["missing_required"] == REQUIRED_FIELDS.copy()
        assert state["missing_optional"] == OPTIONAL_FIELDS.copy()
        assert state["intent"] is None
        assert state["intent_confidence"] == 0.0
        assert state["asr_confidence_scores"] == []
        assert state["asr_confidence_avg"] == 0.0
        assert state["sentiment"] == "neutral"
        assert state["sentiment_history"] == []
        assert state["escalation_triggered"] is False
        assert state["escalation_reason"] is None
        assert state["conversation_history"] == []
        assert state["ai_responses"] == []
        assert state["current_question"] is None
        assert state["recovery_attempts"] == 0
        assert "timestamp_start" in state
        assert "last_activity" in state


# ── TEST 2 ─────────────────────────────────────────────────────────────────


class TestDialogueStateUpdate:
    def test_dialogue_state_update(self, dialogue, sample_state):
        updated = dialogue.update_state(
            sample_state, transcript="My name is Alice", confidence=0.92, language="en"
        )

        assert updated["turn_count"] == 1
        assert updated["current_language"] == "en"
        assert updated["asr_confidence_scores"] == [0.92]
        assert updated["asr_confidence_avg"] == pytest.approx(0.92)
        assert len(updated["conversation_history"]) == 1
        entry = updated["conversation_history"][0]
        assert entry["role"] == "user"
        assert entry["content"] == "My name is Alice"
        assert entry["language"] == "en"
        assert entry["confidence"] == 0.92

    def test_update_tracks_multiple_languages(self, dialogue, sample_state):
        dialogue.update_state(sample_state, "Hello", 0.9, "en")
        dialogue.update_state(sample_state, "Hola", 0.8, "es")

        assert "en" in sample_state["languages_used"]
        assert "es" in sample_state["languages_used"]
        assert sample_state["current_language"] == "es"

    def test_update_averages_asr_confidence(self, dialogue, sample_state):
        dialogue.update_state(sample_state, "first", 0.8, "en")
        dialogue.update_state(sample_state, "second", 0.6, "en")

        assert sample_state["asr_confidence_avg"] == pytest.approx(0.7)


# ── TEST 3 ─────────────────────────────────────────────────────────────────


class TestGuardrailsMedicalAdvice:
    def test_guardrails_blocks_medical_advice(self, guardrails):
        result = guardrails.check_input("you should take aspirin for your chest pain")
        assert result["triggered"] is True
        assert result["type"] == "medical"
        assert "emergency" in result["response"].lower() or "medical" in result["response"].lower()


# ── TEST 4 ─────────────────────────────────────────────────────────────────


class TestGuardrailsLegalAdvice:
    def test_guardrails_blocks_legal_advice(self, guardrails):
        result = guardrails.check_input("you should sue them")
        assert result["triggered"] is True
        assert result["type"] == "legal"
        assert "legal" in result["response"].lower()


# ── TEST 5 ─────────────────────────────────────────────────────────────────


class TestGuardrailsNormalResponse:
    def test_guardrails_allows_normal_response(self, guardrails):
        result = guardrails.check_input("I'll check your bill")
        assert result["triggered"] is False


# ── TEST 6 ─────────────────────────────────────────────────────────────────


class TestEmergencyDetection:
    def test_emergency_detection(self, guardrails):
        assert guardrails.is_emergency("I'm having a heart attack") is True

    def test_emergency_detection_negative(self, guardrails):
        assert guardrails.is_emergency("I'd like to check my account balance") is False

    def test_emergency_input_triggers_guardrails(self, guardrails):
        result = guardrails.check_input("I'm having a heart attack")
        assert result["triggered"] is True
        assert result["type"] == "emergency"
        assert "911" in result["response"] or "112" in result["response"]


# ── TEST 7 ─────────────────────────────────────────────────────────────────


class TestEscalationLowConfidence:
    def test_escalation_on_low_confidence(self, dialogue, sample_state):
        sample_state["turn_count"] = 5
        sample_state["asr_confidence_avg"] = 0.3

        should_esc, reason = dialogue.should_escalate(sample_state)
        assert should_esc is True
        assert reason == "low_asr_confidence"

    def test_no_escalation_below_three_turns(self, dialogue, sample_state):
        sample_state["turn_count"] = 2
        sample_state["asr_confidence_avg"] = 0.1

        should_esc, _ = dialogue.should_escalate(sample_state)
        assert should_esc is False


# ── TEST 8 ─────────────────────────────────────────────────────────────────


class TestEscalationFrustration:
    def test_escalation_on_frustration(self, dialogue, sample_state):
        sample_state["sentiment_history"] = ["frustrated", "frustrated", "frustrated"]

        should_esc, reason = dialogue.should_escalate(sample_state)
        assert should_esc is True
        assert reason == "escalating_frustration"

    def test_escalation_on_anger(self, dialogue, sample_state):
        sample_state["sentiment_history"] = ["angry", "angry"]

        should_esc, reason = dialogue.should_escalate(sample_state)
        assert should_esc is True
        assert reason == "caller_angry"

    def test_no_escalation_two_frustrated(self, dialogue, sample_state):
        sample_state["sentiment_history"] = ["frustrated", "frustrated"]

        should_esc, _ = dialogue.should_escalate(sample_state)
        assert should_esc is False


# ── TEST 9 ─────────────────────────────────────────────────────────────────


class TestNoEscalationGoodState:
    def test_no_escalation_on_good_state(self, dialogue, sample_state):
        sample_state["turn_count"] = 3
        sample_state["asr_confidence_avg"] = 0.9
        sample_state["sentiment_history"] = ["calm", "calm"]

        should_esc, reason = dialogue.should_escalate(sample_state)
        assert should_esc is False
        assert reason is None


# ── TEST 10 ────────────────────────────────────────────────────────────────


class TestPriorityQuestionFlow:
    def test_priority_question_intent_first(self, dialogue, sample_state):
        question = dialogue.get_next_question(sample_state)
        assert question == FIELD_QUESTIONS["intent"]

    def test_priority_question_after_intent(self, dialogue, sample_state):
        dialogue.set_intent(sample_state, "billing", 0.95)
        question = dialogue.get_next_question(sample_state)
        assert question == FIELD_QUESTIONS["name"]

    def test_priority_question_after_name(self, dialogue, sample_state):
        dialogue.set_intent(sample_state, "billing", 0.95)
        dialogue.record_field(sample_state, "name", "Alice")
        question = dialogue.get_next_question(sample_state)
        assert question == FIELD_QUESTIONS["contact"]

    def test_priority_question_after_name_and_contact(self, dialogue, sample_state):
        dialogue.set_intent(sample_state, "billing", 0.95)
        dialogue.record_field(sample_state, "name", "Alice")
        dialogue.record_field(sample_state, "contact", "+15551234567")
        question = dialogue.get_next_question(sample_state)
        assert question == FIELD_QUESTIONS["issue_description"]

    def test_priority_question_none_when_all_required_collected(
        self, dialogue, sample_state
    ):
        dialogue.set_intent(sample_state, "billing", 0.95)
        dialogue.record_field(sample_state, "name", "Alice")
        dialogue.record_field(sample_state, "contact", "+15551234567")
        dialogue.record_field(sample_state, "issue_description", "cannot log in")

        question = dialogue.get_next_question(sample_state)
        assert question is None


# ── TEST 11 ────────────────────────────────────────────────────────────────


class TestHandoffPacketGeneration:
    def test_handoff_packet_generation(self, dialogue, sample_state):
        dialogue.set_intent(sample_state, "technical", 0.88)
        dialogue.record_field(sample_state, "name", "Bob")
        dialogue.update_sentiment(sample_state, "stressed")
        summary = "Caller reported login issues."

        packet = dialogue.generate_handoff_packet(sample_state, summary)

        assert packet["call_id"] == "call-001"
        assert packet["caller_id"] == "+15551234567"
        assert packet["summary"] == summary
        assert "intent" in packet["collected_fields"]
        assert "name" in packet["collected_fields"]
        assert "required" in packet["missing_fields"]
        assert "optional" in packet["missing_fields"]
        assert packet["intent"] == "technical"
        assert packet["intent_confidence"] == pytest.approx(0.88)
        assert packet["sentiment"] == "stressed"
        assert packet["sentiment_history"] == ["stressed"]
        assert packet["escalation_reason"] is None
        assert packet["asr_confidence_avg"] == pytest.approx(0.0)
        assert packet["turn_count"] == 0
        assert packet["languages_used"] == []
        assert packet["recovery_attempts"] == 0
        assert isinstance(packet["transcript"], list)
        assert "timestamp_start" in packet
        assert "timestamp_handoff" in packet


# ── TEST 12 ────────────────────────────────────────────────────────────────


class TestFullPipelineFlow:
    @pytest.mark.asyncio
    async def test_full_pipeline_flow(self):
        pipeline = AudioPipeline()

        # Mock all external dependencies.
        pipeline.analytics.connect = AsyncMock()
        pipeline.analytics.log_call_start = AsyncMock()
        pipeline.analytics.log_call_end = AsyncMock()
        pipeline.analytics.log_guardrail_trigger = AsyncMock()
        pipeline.analytics.close = AsyncMock()

        pipeline.tts.pre_cache_common_phrases = AsyncMock()
        pipeline.tts.stop = AsyncMock()
        pipeline.tts.stream_speech = AsyncMock()

        pipeline.asr.start_streaming = AsyncMock()
        pipeline.asr.close = AsyncMock()

        pipeline.llm.detect_sentiment = AsyncMock(return_value="calm")
        pipeline.llm.generate_summary = AsyncMock(return_value="Test summary.")
        pipeline.llm.get_response = AsyncMock()
        # Make get_response an async generator.
        async def _fake_get_response(prompt, history, state):
            yield "I can help with that."
        pipeline.llm.get_response = _fake_get_response

        pipeline.ticketing.create_ticket = AsyncMock(return_value="ticket-42")
        pipeline.ticketing.close = AsyncMock()

        pipeline.notifications.send_sms = AsyncMock()

        send_audio = AsyncMock()

        # ── Step 1: Initialize & start call ──────────────────────────────
        await pipeline.initialize()
        call_id = "call-e2e"
        await pipeline.handle_incoming_call(call_id, "+15550001111", send_audio)

        assert call_id in pipeline.active_calls
        state = pipeline.active_calls[call_id]["state"]
        assert state["call_id"] == call_id
        assert state["caller_id"] == "+15550001111"

        # ── Step 2: Simulate first user utterance (interim – ignored) ────
        transcript_handler = pipeline.asr.start_streaming.call_args[1][
            "on_transcript"
        ] if pipeline.asr.start_streaming.call_args[1] else pipeline.asr.start_streaming.call_args.kwargs.get("on_transcript")

        await transcript_handler("hello", 0.85, "en", False)
        assert state["turn_count"] == 0  # interim – no update

        # ── Step 3: Simulate final user utterance ────────────────────────
        await transcript_handler("I need help with my bill", 0.9, "en", True)

        assert state["turn_count"] == 1
        assert state["sentiment"] == "calm"
        assert any(
            h["content"] == "I need help with my bill"
            for h in state["conversation_history"]
            if h.get("role") == "user"
        )
        # AI response stored.
        assert len(state["ai_responses"]) >= 1

        # ── Step 4: End call ─────────────────────────────────────────────
        await pipeline.end_call(call_id)

        assert call_id not in pipeline.active_calls
        pipeline.analytics.log_call_end.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_guardrail_escalates_immediately(self):
        pipeline = AudioPipeline()

        pipeline.analytics.connect = AsyncMock()
        pipeline.analytics.log_call_start = AsyncMock()
        pipeline.analytics.log_guardrail_trigger = AsyncMock()
        pipeline.analytics.log_call_end = AsyncMock()
        pipeline.analytics.close = AsyncMock()

        pipeline.tts.pre_cache_common_phrases = AsyncMock()
        pipeline.tts.stop = AsyncMock()
        pipeline.tts.stream_speech = AsyncMock()

        pipeline.asr.start_streaming = AsyncMock()
        pipeline.asr.close = AsyncMock()

        pipeline.llm.detect_sentiment = AsyncMock()
        pipeline.llm.generate_summary = AsyncMock(return_value="Medical escalation.")
        pipeline.llm.get_response = AsyncMock()
        pipeline.ticketing.create_ticket = AsyncMock(return_value="ticket-99")
        pipeline.ticketing.close = AsyncMock()
        pipeline.notifications.send_sms = AsyncMock()

        send_audio = AsyncMock()
        await pipeline.initialize()
        call_id = "call-guard"
        await pipeline.handle_incoming_call(call_id, "+15559999999", send_audio)

        transcript_handler = pipeline.asr.start_streaming.call_args.kwargs.get(
            "on_transcript"
        )

        # Utterance that triggers the medical guardrail.
        await transcript_handler("I have chest pain and can't breathe", 0.7, "en", True)

        state = pipeline.active_calls[call_id]["state"]
        assert state["escalation_triggered"] is True
        assert "guardrail_medical" in state["escalation_reason"]
        pipeline.ticketing.create_ticket.assert_awaited_once()
