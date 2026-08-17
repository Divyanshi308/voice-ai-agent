"""Dialogue state management and conversation flow control."""

import re
import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

MAX_TURNS_BEFORE_ESCALATION = 10

REQUIRED_FIELDS = ["intent", "name", "contact", "issue_description"]
OPTIONAL_FIELDS = ["location", "urgency", "callback_time", "additional_info"]

PRIORITY_ORDER = {
    1: ["intent"],
    2: ["name", "contact"],
    3: ["issue_description"],
    4: ["location", "urgency"],
}

FIELD_QUESTIONS = {
    "intent": "How can I help you today?",
    "name": "May I have your name, please?",
    "contact": "What's the best phone number to reach you?",
    "issue_description": "Can you describe the issue you're having?",
    "location": "What's your location or address?",
    "urgency": "How urgent is this issue? Low, medium, or high?",
    "callback_time": "When is the best time to call you back?",
    "additional_info": "Is there anything else I should know?",
}

PHONE_PATTERN = re.compile(r"\+?\d{10,}")
EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
LOCATION_KEYWORDS = [
    "address", "street", "road", "avenue", "drive", "lane",
    "blvd", "boulevard", "highway", "suite", "apt", "floor",
    "building", "city", "state", "zip", "postal", "country",
]


class DialogueManager:
    """Manages conversation state, field collection, and flow decisions."""

    def create_state(self, call_id: str, caller_id: str) -> dict:
        """Create a fresh conversation state dictionary."""
        now = datetime.now().isoformat()
        state = {
            "call_id": call_id,
            "caller_id": caller_id,
            "languages_used": [],
            "current_language": "en",
            "turn_count": 0,
            "collected_fields": {},
            "missing_required": REQUIRED_FIELDS.copy(),
            "missing_optional": OPTIONAL_FIELDS.copy(),
            "intent": None,
            "intent_confidence": 0.0,
            "asr_confidence_scores": [],
            "asr_confidence_avg": 0.0,
            "sentiment": "neutral",
            "sentiment_history": [],
            "escalation_triggered": False,
            "escalation_reason": None,
            "conversation_history": [],
            "ai_responses": [],
            "timestamp_start": now,
            "last_activity": now,
            "current_question": None,
            "recovery_attempts": 0,
        }
        logger.info("state_created call_id=%s caller_id=%s", call_id, caller_id)
        return state

    def update_state(
        self, state: dict, transcript: str, confidence: float, language: str
    ) -> dict:
        """Update state with new user utterance and extract available fields."""
        state["turn_count"] += 1
        state["last_activity"] = datetime.now().isoformat()

        if language and language not in state["languages_used"]:
            state["languages_used"].append(language)
        state["current_language"] = language or state["current_language"]

        state["asr_confidence_scores"].append(confidence)
        scores = state["asr_confidence_scores"]
        state["asr_confidence_avg"] = sum(scores) / len(scores) if scores else 0.0

        state["conversation_history"].append({
            "role": "user",
            "content": transcript,
            "language": language,
            "confidence": confidence,
        })

        self._extract_fields(state, transcript)

        logger.info(
            "state_updated call_id=%s turn=%d confidence=%.2f lang=%s",
            state["call_id"], state["turn_count"], confidence, language,
        )
        return state

    def _extract_fields(self, state: dict, transcript: str) -> None:
        """Auto-extract fields from transcript using pattern matching."""
        collected = state["collected_fields"]

        if "contact" not in collected:
            phone_match = PHONE_PATTERN.search(transcript)
            if phone_match:
                collected["contact"] = phone_match.group()
                self._remove_from_missing(state, "contact", "required")
                logger.info("auto_extracted field=contact value=%s", collected["contact"])

        if "contact_email" not in collected:
            email_match = EMAIL_PATTERN.search(transcript)
            if email_match:
                collected["contact_email"] = email_match.group()
                logger.info("auto_extracted field=contact_email value=%s", collected["contact_email"])

        if "location" not in collected:
            lower = transcript.lower()
            if any(kw in lower for kw in LOCATION_KEYWORDS):
                collected["location"] = transcript.strip()
                self._remove_from_missing(state, "location", "optional")
                logger.info("auto_extracted field=location")

    def _remove_from_missing(self, state: dict, field: str, category: str) -> None:
        """Remove a field from the appropriate missing list."""
        key = f"missing_{category}"
        if field in state[key]:
            state[key].remove(field)

    def store_ai_response(self, state: dict, response_text: str) -> None:
        """Record an AI-generated response in conversation history."""
        state["conversation_history"].append({
            "role": "assistant",
            "content": response_text,
        })
        state["ai_responses"].append(response_text)
        logger.debug("ai_response_stored call_id=%s len=%d", state["call_id"], len(response_text))

    def should_escalate(self, state: dict) -> tuple[bool, str | None]:
        """Determine if the call should be escalated to a human agent."""
        if state["escalation_triggered"]:
            return True, state["escalation_reason"]

        if state["turn_count"] >= 3 and state["asr_confidence_avg"] < 0.5:
            return True, "low_asr_confidence"

        if state["recovery_attempts"] >= 2:
            return True, "recovery_failed"

        history = state["sentiment_history"]
        if len(history) >= 3 and all(s == "frustrated" for s in history[-3:]):
            return True, "escalating_frustration"

        if len(history) >= 2 and all(s == "angry" for s in history[-2:]):
            return True, "caller_angry"

        if state["turn_count"] >= MAX_TURNS_BEFORE_ESCALATION:
            return True, "max_turns_reached"

        return False, None

    def get_next_question(self, state: dict) -> str | None:
        """Return the next question to ask based on priority and missing fields."""
        collected = set(state["collected_fields"].keys())

        for priority in sorted(PRIORITY_ORDER.keys()):
            for field in PRIORITY_ORDER[priority]:
                if field not in collected:
                    return FIELD_QUESTIONS[field]

        return None

    def confirm_field(self, state: dict, field_name: str, field_value: Any) -> str:
        """Generate a confirmation prompt for a collected field."""
        return f"Just to confirm, your {field_name} is {field_value}. Is that correct?"

    def generate_handoff_packet(self, state: dict, summary: str) -> dict:
        """Build a structured handoff packet for a human agent."""
        packet = {
            "call_id": state["call_id"],
            "caller_id": state["caller_id"],
            "summary": summary,
            "collected_fields": dict(state["collected_fields"]),
            "missing_fields": {
                "required": list(state["missing_required"]),
                "optional": list(state["missing_optional"]),
            },
            "intent": state["intent"],
            "intent_confidence": state["intent_confidence"],
            "sentiment": state["sentiment"],
            "sentiment_history": list(state["sentiment_history"]),
            "escalation_reason": state["escalation_reason"],
            "asr_confidence_avg": state["asr_confidence_avg"],
            "turn_count": state["turn_count"],
            "languages_used": list(state["languages_used"]),
            "recovery_attempts": state["recovery_attempts"],
            "transcript": [
                {k: v for k, v in entry.items()}
                for entry in state["conversation_history"]
            ],
            "timestamp_start": state["timestamp_start"],
            "timestamp_handoff": datetime.now().isoformat(),
        }
        logger.info(
            "handoff_packet_generated call_id=%s reason=%s",
            state["call_id"], state["escalation_reason"],
        )
        return packet

    def serialize_state(self, state: dict) -> str:
        """Serialize state to JSON string for Redis storage."""
        return json.dumps(state, default=str)

    def deserialize_state(self, raw: str) -> dict:
        """Deserialize state from a JSON string retrieved from Redis."""
        try:
            state = json.loads(raw)
            logger.info("state_deserialized call_id=%s", state.get("call_id"))
            return state
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error("state_deserialization_failed error=%s", exc)
            raise ValueError(f"Invalid state data: {exc}") from exc

    def update_sentiment(self, state: dict, sentiment: str) -> None:
        """Update sentiment tracking in state."""
        state["sentiment"] = sentiment
        state["sentiment_history"].append(sentiment)
        logger.debug("sentiment_updated call_id=%s sentiment=%s", state["call_id"], sentiment)

    def set_escalation(self, state: dict, reason: str) -> None:
        """Manually trigger escalation."""
        state["escalation_triggered"] = True
        state["escalation_reason"] = reason
        logger.warning("escalation_triggered call_id=%s reason=%s", state["call_id"], reason)

    def increment_recovery(self, state: dict) -> None:
        """Track a recovery attempt (e.g., asking user to repeat)."""
        state["recovery_attempts"] += 1
        logger.info(
            "recovery_attempt call_id=%s attempt=%d",
            state["call_id"], state["recovery_attempts"],
        )

    def set_intent(self, state: dict, intent: str, confidence: float) -> None:
        """Store the classified intent and its confidence."""
        state["intent"] = intent
        state["intent_confidence"] = confidence
        if intent and "intent" not in state["collected_fields"]:
            state["collected_fields"]["intent"] = intent
            self._remove_from_missing(state, "intent", "required")
        logger.info(
            "intent_set call_id=%s intent=%s confidence=%.2f",
            state["call_id"], intent, confidence,
        )

    def record_field(self, state: dict, field_name: str, value: Any) -> None:
        """Manually record a collected field."""
        state["collected_fields"][field_name] = value
        if field_name in state["missing_required"]:
            state["missing_required"].remove(field_name)
        if field_name in state["missing_optional"]:
            state["missing_optional"].remove(field_name)
        logger.info(
            "field_recorded call_id=%s field=%s", state["call_id"], field_name,
        )

    def get_collection_progress(self, state: dict) -> dict:
        """Return a summary of field collection progress."""
        total_required = len(REQUIRED_FIELDS)
        collected_required = total_required - len(state["missing_required"])
        total_optional = len(OPTIONAL_FIELDS)
        collected_optional = total_optional - len(state["missing_optional"])
        return {
            "required_collected": collected_required,
            "required_total": total_required,
            "optional_collected": collected_optional,
            "optional_total": total_optional,
            "percent_complete": round(
                (collected_required / total_required) * 100 if total_required else 0, 1
            ),
        }
