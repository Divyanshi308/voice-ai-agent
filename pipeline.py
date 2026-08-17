import asyncio
import time
import uuid
import structlog

from config import config
from asr import SpeechToText
from llm import LLMEngine
from tts import TextToSpeech
from guardrails import Guardrails
from dialogue import DialogueManager
from ticketing import TicketingManager
from notifications import NotificationManager
from analytics import AnalyticsLogger

logger = structlog.get_logger()

MAX_CALL_DURATION_SECONDS = 300


class AudioPipeline:
    def __init__(self) -> None:
        self.asr = SpeechToText(config.deepgram_api_key)
        self.llm = LLMEngine()
        self.tts = TextToSpeech()
        self.guardrails = Guardrails()
        self.dialogue = DialogueManager()
        self.ticketing = TicketingManager(
            subdomain=config.zendesk_subdomain,
            email=config.zendesk_email,
            api_key=config.zendesk_api_key,
        )
        self.notifications = NotificationManager()
        self.analytics = AnalyticsLogger()
        self.active_calls: dict[str, dict] = {}
        self._call_timers: dict[str, asyncio.Task] = {}

    async def initialize(self) -> None:
        await self.analytics.connect()
        await self.tts.pre_cache_common_phrases()
        logger.info("pipeline_initialized")

    async def shutdown(self) -> None:
        for call_id in list(self.active_calls):
            await self.end_call(call_id)
        await self.tts.stop()
        await self.ticketing.close()
        await self.analytics.close()
        logger.info("pipeline_shutdown")

    async def handle_incoming_call(
        self, call_id: str, caller_id: str, send_audio_fn
    ) -> None:
        state = self.dialogue.create_state(call_id, caller_id)

        self.active_calls[call_id] = {
            "state": state,
            "send_audio": send_audio_fn,
            "transcript_log": [],
            "ticket_id": None,
        }

        await self.analytics.log_call_start(call_id, caller_id)

        await self.asr.start_streaming(
            on_transcript=lambda text, confidence, language, is_final: asyncio.ensure_future(
                self._on_transcript(call_id, text, confidence, language, is_final)
            )
        )

        self._call_timers[call_id] = asyncio.create_task(
            self._call_timeout(call_id)
        )

        greeting = "Hello! How can I help you today?"
        state["conversation_history"].append(
            {"role": "assistant", "content": greeting}
        )
        self.dialogue.store_ai_response(state, greeting)

        language = state.get("current_language", "en")
        await self._speak(call_id, greeting, language)

        logger.info("call_started", call_id=call_id, caller_id=caller_id)

    async def _call_timeout(self, call_id: str) -> None:
        try:
            await asyncio.sleep(MAX_CALL_DURATION_SECONDS)
            if call_id in self.active_calls:
                logger.warning("call_timeout", call_id=call_id)
                state = self.active_calls[call_id]["state"]
                state["escalation_triggered"] = True
                state["escalation_reason"] = "max_duration_reached"
                await self._speak(
                    call_id,
                    "I'm sorry, our call time limit has been reached. Let me connect you with a specialist who can continue helping you.",
                    state.get("current_language", "en"),
                )
                await self._escalate(call_id)
        except asyncio.CancelledError:
            pass

    async def _on_transcript(
        self,
        call_id: str,
        text: str,
        confidence: float,
        language: str,
        is_final: bool,
    ) -> None:
        if not is_final:
            return

        if call_id not in self.active_calls:
            return

        call_data = self.active_calls[call_id]
        state = call_data["state"]

        call_data["transcript_log"].append(
            {
                "role": "caller",
                "text": text,
                "language": language,
                "confidence": confidence,
                "timestamp": time.time(),
            }
        )
        logger.info(
            "caller_utterance",
            call_id=call_id,
            text=text,
            language=language,
            confidence=confidence,
        )

        self.tts.stop()

        safety = self.guardrails.check_input(text)
        if safety["triggered"]:
            logger.warning(
                "guardrail_input_triggered",
                call_id=call_id,
                type=safety["type"],
                text=text,
            )
            await self.analytics.log_guardrail_trigger(
                call_id, safety["type"], text, "input_blocked"
            )
            state["escalation_triggered"] = True
            state["escalation_reason"] = f"guardrail_{safety['type']}"
            await self._speak(call_id, safety["response"], language)
            await self._escalate(call_id)
            return

        state = self.dialogue.update_state(state, text, confidence, language)

        sentiment = await self.llm.detect_sentiment(text, "")
        state["sentiment"] = sentiment
        state["sentiment_history"].append(sentiment)

        should_esc, reason = self.dialogue.should_escalate(state)
        if should_esc:
            logger.info(
                "escalation_triggered",
                call_id=call_id,
                reason=reason,
            )
            state["escalation_triggered"] = True
            state["escalation_reason"] = reason
            await self._escalate(call_id)
            return

        next_question = self.dialogue.get_next_question(state)
        if next_question and state["turn_count"] > 0:
            collected = state.get("collected_fields", {})
            collected_str = ", ".join(
                f"{k}: {v}" for k, v in collected.items() if v
            )
            prompt = (
                f"Caller said: {text}. "
                f"Context — collected fields: {collected_str}. "
                f"Ask the caller: {next_question}"
            )
        else:
            prompt = text

        full_response = ""
        try:
            history = state["conversation_history"][-10:]
            async for token in self.llm.get_response(prompt, history, state):
                full_response += token
        except Exception as e:
            logger.error("llm_error", call_id=call_id, error=str(e))
            full_response = "I'm sorry, could you repeat that?"

        logger.info("ai_response", call_id=call_id, response=full_response)

        output_safety = self.guardrails.check_output(full_response)
        final_response = output_safety.get("safe_response", full_response)
        if output_safety.get("blocked"):
            logger.warning(
                "guardrail_output_blocked",
                call_id=call_id,
                reason=output_safety.get("reason"),
            )
            await self.analytics.log_guardrail_trigger(
                call_id,
                "output_blocked",
                full_response,
                output_safety.get("reason", "unknown"),
            )

        self.dialogue.store_ai_response(state, final_response)

        await self._speak(call_id, final_response, state.get("current_language", "en"))

    async def _speak(self, call_id: str, text: str, language: str = "en") -> None:
        if call_id not in self.active_calls:
            return

        send_audio_fn = self.active_calls[call_id]["send_audio"]

        async def send_chunk(chunk: str) -> None:
            await send_audio_fn(chunk)

        await self.tts.stream_speech(text, send_chunk, language)

    async def _escalate(self, call_id: str) -> None:
        if call_id not in self.active_calls:
            return

        call_data = self.active_calls[call_id]
        state = call_data["state"]

        if state.get("_escalation_in_progress"):
            return
        state["_escalation_in_progress"] = True

        if self._call_timers.get(call_id):
            self._call_timers[call_id].cancel()

        history = state.get("conversation_history", [])
        transcript_lines = []
        for entry in history:
            role = entry.get("role", "unknown").capitalize()
            transcript_lines.append(f"{role}: {entry.get('content', '')}")
        full_transcript = "\n".join(transcript_lines)

        try:
            summary = await self.llm.generate_summary(full_transcript)
        except Exception as e:
            logger.error("summary_generation_failed", call_id=call_id, error=str(e))
            summary = f"Call from {state.get('caller_id', 'unknown')} — escalation requested. Reason: {state.get('escalation_reason', 'unknown')}."

        packet = self.dialogue.generate_handoff_packet(state, summary)

        ticket_id = None
        try:
            ticket_id = await self.ticketing.create_ticket(
                summary, packet, full_transcript
            )
            call_data["ticket_id"] = ticket_id
            logger.info(
                "ticket_created", call_id=call_id, ticket_id=ticket_id
            )
        except Exception as e:
            logger.error("ticket_creation_failed", call_id=call_id, error=str(e))

        caller_phone = state.get("collected_fields", {}).get("contact") or state.get(
            "caller_id", ""
        )
        if caller_phone and ticket_id and caller_phone.startswith("+"):
            try:
                await self.notifications.send_sms(
                    phone_number=caller_phone,
                    case_number=str(ticket_id),
                    summary=summary,
                    language=state.get("current_language", "en"),
                    name=state.get("collected_fields", {}).get("name", "Customer"),
                )
                logger.info(
                    "sms_sent", call_id=call_id, phone=caller_phone
                )
            except Exception as e:
                logger.error("sms_failed", call_id=call_id, error=str(e))

        await self.analytics.log_guardrail_trigger(
            call_id,
            state.get("escalation_reason", "unknown"),
            "",
            "escalation",
        )

        handoff_str = f"Summary: {summary}\nIntent: {packet.get('intent', 'unknown')}\nUrgency: {packet.get('urgency', 'unknown')}"
        await self._speak(
            call_id,
            "I'm connecting you with a specialist. They have all your details. One moment please.",
            state.get("current_language", "en"),
        )

        logger.info(
            "escalation_complete",
            call_id=call_id,
            reason=state.get("escalation_reason"),
            ticket_id=ticket_id,
        )

    async def end_call(self, call_id: str) -> None:
        if call_id not in self.active_calls:
            return

        call_data = self.active_calls[call_id]
        state = call_data["state"]

        if self._call_timers.get(call_id):
            self._call_timers[call_id].cancel()
            del self._call_timers[call_id]

        ticket_id = call_data.get("ticket_id")

        if not state.get("escalation_triggered") and not ticket_id:
            history = state.get("conversation_history", [])
            transcript_lines = []
            for entry in history:
                role = entry.get("role", "unknown").capitalize()
                transcript_lines.append(f"{role}: {entry.get('content', '')}")
            full_transcript = "\n".join(transcript_lines)

            try:
                summary = await self.llm.generate_summary(full_transcript)
                packet = self.dialogue.generate_handoff_packet(state, summary)
                ticket_id = await self.ticketing.create_ticket(
                    summary, packet, full_transcript
                )
                call_data["ticket_id"] = ticket_id
            except Exception as e:
                logger.error(
                    "final_ticket_failed", call_id=call_id, error=str(e)
                )

        try:
            await self.analytics.log_call_end(call_id, state, ticket_id)
        except Exception as e:
            logger.error("analytics_log_end_failed", call_id=call_id, error=str(e))

        try:
            await self.asr.close()
        except Exception as e:
            logger.error("asr_close_failed", call_id=call_id, error=str(e))

        del self.active_calls[call_id]
        logger.info("call_ended", call_id=call_id, ticket_id=ticket_id)
