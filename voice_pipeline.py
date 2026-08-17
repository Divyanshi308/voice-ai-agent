import asyncio
import json
import re
import time
import uuid
from typing import Optional
from enum import Enum

import structlog
from config import config

logger = structlog.get_logger()


class ConversationPhase(Enum):
    GREETING = "greeting"
    NAME_COLLECTION = "name_collection"
    ISSUE_IDENTIFICATION = "issue_identification"
    DETAILS_COLLECTION = "details_collection"
    CONFIRMATION = "confirmation"
    RESOLUTION = "resolution"
    ESCALATION = "escalation"
    FAREWELL = "farewell"


class VoiceState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"


class ConversationState:
    def __init__(self, session_id: str, user_id: int = 0):
        self.session_id = session_id
        self.user_id = user_id
        self.messages = []
        self.state = VoiceState.IDLE
        self.phase = ConversationPhase.GREETING
        self.language = "auto"
        self.detected_language = "english"
        self.confidence = 1.0
        self.started_at = time.time()
        self.last_user_input = ""
        self.last_ai_response = ""
        self.last_ai_timestamp = 0
        self.interruption_count = 0
        self.backchannel_count = 0
        self.clarification_count = 0
        self.low_confidence_count = 0
        self.silence_count = 0

        self.collected_info = {
            "name": "",
            "phone": "",
            "address": "",
            "issue_type": "",
            "issue_details": "",
            "date": "",
            "urgency": "",
            "sentiment": "neutral",
        }

        self.escalation_triggers = []
        self.conversation_context = []

    def add_user_message(self, text: str, confidence: float = 1.0):
        self.messages.append({
            "role": "user",
            "content": text,
            "timestamp": time.time(),
            "confidence": confidence,
            "phase": self.phase.value,
        })
        self.last_user_input = text

    def add_ai_message(self, text: str, is_backchannel: bool = False):
        self.messages.append({
            "role": "assistant",
            "content": text,
            "timestamp": time.time(),
            "is_backchannel": is_backchannel,
            "phase": self.phase.value,
        })
        self.last_ai_response = text
        self.last_ai_timestamp = time.time()

    def collect_info(self, key: str, value: str):
        if key in self.collected_info:
            self.collected_info[key] = value

    def get_history(self, limit: int = 10):
        return self.messages[-limit:]

    def get_summary(self) -> dict:
        user_msgs = [m["content"] for m in self.messages if m["role"] == "user"]
        ai_msgs = [m["content"] for m in self.messages if m["role"] == "assistant"]
        return {
            "total_turns": len(user_msgs),
            "user_messages": user_msgs[-5:],
            "ai_messages": ai_msgs[-5:],
            "collected_info": {k: v for k, v in self.collected_info.items() if v},
            "language": self.detected_language,
            "phase": self.phase.value,
            "duration": round(time.time() - self.started_at, 1),
            "interruptions": self.interruption_count,
            "backchannels": self.backchannel_count,
            "clarifications": self.clarification_count,
            "escalation_triggers": self.escalation_triggers,
        }

    def get_info_collection_progress(self) -> dict:
        required = ["name", "phone", "issue_type", "issue_details"]
        collected = [k for k in required if self.collected_info.get(k)]
        return {
            "required": required,
            "collected": collected,
            "progress": len(collected) / len(required) if required else 0,
            "missing": [k for k in required if not self.collected_info.get(k)],
        }

    def get_conversation_summary(self) -> str:
        user_msgs = [m["content"] for m in self.messages if m["role"] == "user"]
        ai_msgs = [m["content"] for m in self.messages if m["role"] == "assistant"]
        collected = {k: v for k, v in self.collected_info.items() if v}
        return (
            f"Duration: {round(time.time() - self.started_at, 1)}s. "
            f"Turns: {len(user_msgs)}. "
            f"Interruptions: {self.interruption_count}. "
            f"Collected: {json.dumps(collected)}. "
            f"User: {' | '.join(user_msgs[-3:])}. "
            f"Agent: {' | '.join(ai_msgs[-3:])}."
        )


class ConversationFlowEngine:
    def __init__(self):
        self.phase_handlers = {
            ConversationPhase.GREETING: self._handle_greeting,
            ConversationPhase.NAME_COLLECTION: self._handle_name_collection,
            ConversationPhase.ISSUE_IDENTIFICATION: self._handle_issue_identification,
            ConversationPhase.DETAILS_COLLECTION: self._handle_details_collection,
            ConversationPhase.CONFIRMATION: self._handle_confirmation,
            ConversationPhase.RESOLUTION: self._handle_resolution,
            ConversationPhase.ESCALATION: self._handle_escalation,
            ConversationPhase.FAREWELL: self._handle_farewell,
        }

    def detect_language(self, text: str) -> str:
        text_lower = text.lower()

        hindi_words = {
            "namaste", "kya", "hai", "hain", "mera", "meri", "aap", "aapka",
            "bataiye", "bolo", "samjhi", "dhanyavaad", "madad", "problem",
            "dawaai", "doctor", "hospital", "ambulance", "bachao", "rukiye",
            "ji", "nahin", "haan", "theek", "chahiye", "zaroorat", "main",
            "tum", "woh", "yeh", "kaise", "kaun", "kab", "kahan", "kyun",
        }

        words = set(re.findall(r'\w+', text_lower))
        hindi_count = len(words & hindi_words)

        if hindi_count >= 2:
            return "hindi"
        elif any(c in text for c in "अआइईउऊएऐओऔकखगघङचछjzhञटठडढणतथदधनपफबभमयरलवशषसह"):
            return "hindi"
        elif any(w in text_lower for w in ["yaar", "bhai", "arre", "accha", "chal"]):
            return "hinglish"
        else:
            return "english"

    def detect_sentiment(self, text: str) -> str:
        text_lower = text.lower()

        negative_words = {"angry", "frustrated", "upset", "terrible", "horrible",
                         "worst", "hate", "useless", "stupid", "waste", "gussa",
                         "pareshan", "dukhi", "naraz"}

        positive_words = {"happy", "great", "excellent", "wonderful", "thank",
                         "good", "nice", "best", "accha", "dhanyavaad"}

        urgent_words = {"urgent", "emergency", "immediately", "help", "bachao",
                       "turant", "abhi"}

        words = set(re.findall(r'\w+', text_lower))

        if words & urgent_words:
            return "urgent"
        if words & negative_words:
            return "negative"
        if words & positive_words:
            return "positive"
        return "neutral"

    def detect_issue_type(self, text: str) -> str:
        text_lower = text.lower()

        if any(w in text_lower for w in ["bill", "payment", "biil"]):
            return "billing"
        if any(w in text_lower for w in ["account", "balance", "profile"]):
            return "account"
        if any(w in text_lower for w in ["technical", "bug", "error", "not working"]):
            return "technical"
        if any(w in text_lower for w in ["complaint", "problem", "issue"]):
            return "complaint"
        if any(w in text_lower for w in ["information", "info", "detail", "kaise"]):
            return "inquiry"
        return "general"

    def should_escalate(self, session: ConversationState) -> tuple[bool, str]:
        text = session.last_user_input.lower()

        if any(w in text for w in ["emergency", "bachao", "ambulance", "112", "911", "urgent"]):
            return True, "Emergency detected - connecting to emergency services"

        if any(w in text for w in ["doctor", "hospital", "sick", "medicine", "fever", "pain"]):
            return True, "Medical issue detected - consulting doctor recommended"

        if any(w in text for w in ["legal", "court", "lawyer"]):
            return True, "Legal matter detected - lawyer consultation recommended"

        if any(w in text for w in ["human", "agent", "person", "transfer"]):
            return True, "User requested human agent"

        if session.low_confidence_count >= 2:
            return True, "Low confidence - human agent needed"

        if session.clarification_count >= 3:
            return True, "Multiple clarifications needed - human agent recommended"

        return False, ""

    def get_next_phase(self, session: ConversationState) -> ConversationPhase:
        info = session.collected_info

        should_esc, _ = self.should_escalate(session)
        if should_esc:
            return ConversationPhase.ESCALATION

        if not info.get("name"):
            return ConversationPhase.NAME_COLLECTION
        if not info.get("issue_type"):
            return ConversationPhase.ISSUE_IDENTIFICATION
        if not info.get("issue_details"):
            return ConversationPhase.DETAILS_COLLECTION

        progress = session.get_info_collection_progress()
        if progress["progress"] >= 0.75:
            return ConversationPhase.CONFIRMATION

        return ConversationPhase.DETAILS_COLLECTION

    def process_turn(self, session: ConversationState, user_text: str, confidence: float = 1.0) -> str:
        # Always check for phase-independent keywords first
        text_lower = user_text.lower()

        # Handle "thanks" from any phase
        if any(w in text_lower for w in ["thank", "dhanyavaad", "shukriya"]):
            session.add_user_message(user_text, confidence)
            if session.detected_language == "hindi":
                return "Aapka swagat hai! Aur kuch ho toh zaroor bataiye."
            elif session.detected_language == "hinglish":
                return "Thanks! Let me know if you need anything else."
            else:
                return "You are welcome! Let me know if you need anything else."

        # Handle "bye" from any phase  
        if any(w in text_lower for w in ["bye", "alvida", "goodbye"]):
            session.add_user_message(user_text, confidence)
            session.phase = ConversationPhase.FAREWELL
            if session.detected_language == "hindi":
                return "Alvida! Apna khayal rakhiye. Zaroorat ho toh wapas aaiye."
            else:
                return "Goodbye! Take care. I am here whenever you need help."

        session.add_user_message(user_text, confidence)

        detected_lang = self.detect_language(user_text)
        session.detected_language = detected_lang

        if confidence < 0.4:
            session.low_confidence_count += 1
            session.clarification_count += 1
            if session.detected_language == "hindi":
                return "Aapki awaaz saaf nahi aayi. Kya aap dobara bol sakte hain?"
            else:
                return "I could not hear you clearly. Could you please repeat?"

        should_esc, esc_reason = self.should_escalate(session)
        if should_esc:
            session.escalation_triggers.append(esc_reason)
            session.phase = ConversationPhase.ESCALATION
            return self._handle_escalation(session)

        if confidence < 0.6:
            session.low_confidence_count += 1
            if session.detected_language == "hindi":
                return "Mujhe samajh nahi aaya. Kya aap thodo aur clearly bol sakte hain?"
            else:
                return "I did not understand clearly. Could you rephrase that?"

        phase = self.get_next_phase(session)
        session.phase = phase

        handler = self.phase_handlers.get(phase, self._handle_general)
        response = handler(session)

        return response

    def _handle_greeting(self, session: ConversationState) -> str:
        session.phase = ConversationPhase.NAME_COLLECTION
        if session.detected_language == "hindi":
            return "Namaste! Main Kataru hoon. Aapki kya madad kar sakti hoon? Bataiye, aapka naam kya hai?"
        elif session.detected_language == "hinglish":
            return "Namaste! Main Kataru hoon. Kya help chahiye? Pehle batao naam kya hai?"
        else:
            return "Hello! I am Kataru, your support assistant. How can I help you today? May I know your name?"

    def _handle_name_collection(self, session: ConversationState) -> str:
        text = session.last_user_input.lower()
        
        # Words that are common and shouldn't be treated as names
        skip_words = {
            "hello", "hi", "hey", "namaste", "help", "yes", "no", "haan", "nahin",
            "theek", "accha", "bolo", "ji", "ok", "okay", "sure", "please",
            "main", "i", "me", "my", "mera", "meri", "want", "need", "have",
            "problem", "issue", "bill", "account", "help me", "details",
            "information", "about", "so", "just", "really", "actually",
            "kya", "kaise", "kahan", "kyun", "kaun", "kon"
        }
        
        name = None
        
        # Pattern 1: "my name is X", "i am X", "mera naam X", "main X hoon"
        for pattern in [
            r"my name is\s+(\w+)",
            r"i am\s+(\w+)", 
            r"mera naam\s+(\w+)",
            r"mera naam hai\s+(\w+)",
            r"main\s+(\w+)\s+hoon",
        ]:
            match = re.search(pattern, text)
            if match:
                candidate = match.group(1).lower().strip(".,;!")
                if candidate and candidate not in skip_words and len(candidate) >= 2:
                    name = match.group(1).title()
                    break
        
        # Pattern 2: If the sentence contains just one meaningful word (after removing skip words)
        if not name:
            words = re.findall(r'\b(\w+)\b', text)
            meaningful_words = [w for w in words if w.lower() not in skip_words and len(w) >= 2]
            if len(meaningful_words) == 1:
                name = meaningful_words[0].title()
        
        if name:
            # Additional check: make sure the name wasn't just "hello" or common greetings
            if name.lower() not in ["hello", "hi", "hey", "namaste"] and len(name) >= 2:
                session.collect_info("name", name)
                session.phase = ConversationPhase.ISSUE_IDENTIFICATION
                if session.detected_language == "hindi":
                    return f"Namaste {name}! Aapki kya madad kar sakti hoon? Bataiye kya problem hai."
                elif session.detected_language == "hinglish":
                    return f"Accha {name}! Batao kya ho raha hai? Main help karta hoon."
                else:
                    return f"Nice to meet you, {name}! What can I help you with today?"
        
        # If no name detected, ask for it
        if session.detected_language == "hindi":
            return "Aapka naam kya hai? Please batayen."
        else:
            return "What is your name? Please tell me."

    def _handle_issue_identification(self, session: ConversationState) -> str:
        issue_type = self.detect_issue_type(session.last_user_input)
        session.collect_info("issue_type", issue_type)
        session.collect_info("issue_details", session.last_user_input)
        session.phase = ConversationPhase.DETAILS_COLLECTION

        name = session.collected_info.get("name", "")

        if session.detected_language == "hindi":
            if name:
                return f"{name}, main samajh gayi. Aur detail mein bataiye kya hua? Kab hua yeh?"
            else:
                return "Main samajh gayi. Aur detail mein bataiye kya hua? Kab hua yeh?"
        elif session.detected_language == "hinglish":
            return f"Theek hai{(' ' + name) if name else ''}. Aur batao kya hua? Kab se ho raha hai?"
        else:
            if name:
                return f"I understand, {name}. Could you tell me more details? When did this happen?"
            else:
                return "I understand. Could you tell me more details? When did this happen?"

    def _handle_details_collection(self, session: ConversationState) -> str:
        text = session.last_user_input.lower()

        date_match = re.search(r'(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})', text)
        if date_match:
            session.collect_info("date", date_match.group(1))

        phone_match = re.search(r'(\d{10})', text)
        if phone_match:
            session.collect_info("phone", phone_match.group(1))

        if any(w in text for w in ["address", "pata", "ghar", "home"]):
            session.collect_info("address", session.last_user_input)

        name = session.collected_info.get("name", "")
        progress = session.get_info_collection_progress()

        if progress["progress"] >= 0.75:
            session.phase = ConversationPhase.CONFIRMATION
            return self._handle_confirmation(session)

        missing = progress["missing"]
        if "phone" in missing:
            if session.detected_language == "hindi":
                return "Aapka phone number kya hai? Please bataiye."
            else:
                return "What is your phone number? Please provide it."
        if "issue_details" in missing:
            if session.detected_language == "hindi":
                return "Kya aur kuch hai jo mujhe batana chahiye? Details dijiye."
            else:
                return "Is there anything else I should know? Please provide more details."

        if session.detected_language == "hindi":
            return "Aur kuch? Please poori details bataiye."
        else:
            return "Anything else? Please share all the details."

    def _handle_confirmation(self, session: ConversationState) -> str:
        text = session.last_user_input.lower()

        if any(w in text for w in ["yes", "haan", "ji", "correct", "sahi", "theek"]):
            session.phase = ConversationPhase.RESOLUTION
            name = session.collected_info.get("name", "")
            if session.detected_language == "hindi":
                return f"{name}, aapki request process ho rahi hai. Kuch aur madad chahiye?"
            else:
                return f"Great{(' ' + name) if name else ''}! Let me help you with that. One moment please."

        if any(w in text for w in ["no", "nahi", "nahin", "galat"]):
            if session.detected_language == "hindi":
                return "Kya galat hai? Please bataiye, main theek karti hoon."
            else:
                return "What is incorrect? Please tell me, I will fix it."

        info = session.collected_info
        summary_parts = []
        if info.get("name"):
            summary_parts.append(f"Name: {info['name']}")
        if info.get("issue_type"):
            summary_parts.append(f"Issue: {info['issue_type']}")
        if info.get("issue_details"):
            summary_parts.append(f"Details: {info['issue_details'][:80]}")
        if info.get("phone"):
            summary_parts.append(f"Phone: {info['phone']}")
        if info.get("date"):
            summary_parts.append(f"Date: {info['date']}")

        summary = ". ".join(summary_parts) if summary_parts else "No details collected yet"

        if session.detected_language == "hindi":
            return f"Main yeh confirm karna chahti hoon: {summary}. Yeh sahi hai?"
        elif session.detected_language == "hinglish":
            return f"Confirm karta hoon: {summary}. Sahi hai?"
        else:
            return f"Let me confirm: {summary}. Is this correct?"

    def _handle_resolution(self, session: ConversationState) -> str:
        session.phase = ConversationPhase.FAREWELL
        name = session.collected_info.get("name", "")

        if session.detected_language == "hindi":
            if name:
                return f"{name}, aapki request process ho rahi hai. Kuch aur madad chahiye?"
            else:
                return "Aapki request process ho rahi hai. Kuch aur madad chahiye?"
        elif session.detected_language == "hinglish":
            return "Request process ho rahi hai. Aur kuch chahiye?"
        else:
            if name:
                return f"Your request is being processed, {name}. Is there anything else I can help with?"
            else:
                return "Your request is being processed. Is there anything else I can help with?"

    def _handle_escalation(self, session: ConversationState) -> str:
        name = session.collected_info.get("name", "")

        summary = session.get_conversation_summary()

        if session.detected_language == "hindi":
            if name:
                return f"{name}, main aapko human agent se connect karti hoon. Ek minute please. Unhe saari details mil jayengi."
            else:
                return "Main aapko human agent se connect karti hoon. Ek minute please. Unhe saari details mil jayengi."
        elif session.detected_language == "hinglish":
            return "Ruko, specialist se baat karwati hoon. Woh aapki help karenge."
        else:
            if name:
                return f"I will connect you with a specialist, {name}. They will have all the details. One moment please."
            else:
                return "I will connect you with a specialist who can help better. One moment please."

    def _handle_farewell(self, session: ConversationState) -> str:
        text = session.last_user_input.lower()

        if any(w in text for w in ["bye", "goodbye", "alvida"]):
            session.phase = ConversationPhase.FAREWELL
            if session.detected_language == "hindi":
                return "Alvida! Apna khayal rakhiye. Zaroorat ho toh wapas aaiye."
            else:
                return "Goodbye! Take care. I am here whenever you need help."

        if any(w in text for w in ["thank", "dhanyavaad", "shukriya"]):
            if session.detected_language == "hindi":
                return "Aapka swagat hai! Aur kuch ho toh zaroor bataiye."
            else:
                return "You are welcome! Let me know if you need anything else."

        session.phase = ConversationPhase.NAME_COLLECTION
        return self._handle_greeting(session)

    def _handle_general(self, session: ConversationState) -> str:
        if session.detected_language == "hindi":
            return "Main samajh gayi. Please aur detail mein bataiye."
        else:
            return "I understand. Please provide more details so I can help you better."


class VoicePipeline:
    def __init__(self):
        self.sessions: dict[str, ConversationState] = {}
        self.flow_engine = ConversationFlowEngine()

    def get_or_create_session(self, session_id: str, user_id: int = 0) -> ConversationState:
        if session_id not in self.sessions:
            self.sessions[session_id] = ConversationState(session_id, user_id)
        return self.sessions[session_id]

    async def transcribe_audio(self, audio_data: bytes, language: str = "auto") -> dict:
        if not config.deepgram_api_key or config.deepgram_api_key.startswith("dummy"):
            return {
                "text": "",
                "confidence": 0.0,
                "language": language,
                "error": "No Deepgram API key configured",
            }

        try:
            import httpx

            lang_param = "hi,en" if language == "auto" else language

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.deepgram.com/v1/listen",
                    params={
                        "model": "nova-2",
                        "language": lang_param,
                        "smart_format": "true",
                        "diarize": "false",
                        "punctuate": "true",
                        "profanity_filter": "false",
                        "redact": "false",
                        "utterances": "true",
                        "endpointing": 300,
                        "interim_results": "true",
                    },
                    headers={
                        "Authorization": f"Token {config.deepgram_api_key}",
                        "Content-Type": "audio/raw",
                    },
                    content=audio_data,
                    timeout=10.0,
                )

                if response.status_code == 200:
                    result = response.json()
                    alternatives = (
                        result.get("results", {})
                        .get("channels", [{}])[0]
                        .get("alternatives", [{}])
                    )
                    if alternatives:
                        transcript = alternatives[0].get("transcript", "")
                        confidence = alternatives[0].get("confidence", 0.0)
                        return {
                            "text": transcript,
                            "confidence": confidence,
                            "language": result.get("results", {})
                            .get("channels", [{}])[0]
                            .get("detected_language", language),
                        }
                else:
                    logger.error("deepgram_error", status=response.status_code)
                    return {
                        "text": "",
                        "confidence": 0.0,
                        "language": language,
                        "error": f"Deepgram error: {response.status_code}",
                    }

        except Exception as e:
            logger.error("transcription_error", error=str(e))
            return {"text": "", "confidence": 0.0, "language": language, "error": str(e)}

    async def generate_response(self, session: ConversationState) -> str:
        if not config.openai_api_key or config.openai_api_key.startswith("dummy"):
            return self.flow_engine.process_turn(session, session.last_user_input, session.confidence)

        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=config.openai_api_key)

            info = session.collected_info
            collected_str = json.dumps({k: v for k, v in info.items() if v}, indent=2)
            progress = session.get_info_collection_progress()

            system_prompt = f"""You are Kataru, a multilingual customer support voice AI agent for elderly care.

CONVERSATION PHASE: {session.phase.value}
COLLECTED INFO: {collected_str}
INFO PROGRESS: {progress['progress'] * 100:.0f}%
MISSING INFO: {', '.join(progress['missing']) if progress['missing'] else 'none'}
LANGUAGE: {session.detected_language}
SENTIMENT: {info.get('sentiment', 'neutral')}
INTERRUPTIONS: {session.interruption_count}

CRITICAL RULES:
1. Respond in the EXACT language the user speaks (Hindi, English, or Hinglish)
2. Keep responses under 25 words - this is a VOICE call
3. NEVER provide medical diagnosis - say "Please consult your doctor"
4. NEVER replace emergency responders - say "Please call 112 immediately"
5. NEVER provide legal advice - say "Please consult a lawyer"
6. NEVER provide financial advice - say "Please consult a financial advisor"

CURRENT PHASE INSTRUCTIONS:
- GREETING: Welcome and ask name
- NAME_COLLECTION: Ask for name if not collected
- ISSUE_IDENTIFICATION: Ask what problem they face
- DETAILS_COLLECTION: Collect missing info (phone, date, details)
- CONFIRMATION: Confirm collected details
- RESOLUTION: Resolve their issue
- ESCALATION: Transfer to human with summary
- FAREWELL: Warm goodbye

IF USER INTERRUPTED: Say "I am listening. Please go ahead."
IF LOW CONFIDENCE: Ask to repeat
IF SENTIMENT NEGATIVE: Acknowledge feelings first"""

            messages = [{"role": "system", "content": system_prompt}]
            history = session.get_history(limit=8)
            for msg in history:
                role = "user" if msg["role"] == "user" else "assistant"
                messages.append({"role": role, "content": msg["content"]})

            response_obj = await client.chat.completions.create(
                model=config.openai_model,
                messages=messages,
                max_tokens=100,
                temperature=0.7,
            )

            response = response_obj.choices[0].message.content
            session.add_ai_message(response)
            return response

        except Exception as e:
            logger.error("llm_error", error=str(e))
            return self.flow_engine.process_turn(session, session.last_user_input, session.confidence)

    async def synthesize_speech(self, text: str) -> Optional[bytes]:
        if not config.elevenlabs_api_key or config.elevenlabs_api_key.startswith("dummy"):
            return None

        try:
            import httpx

            voice = config.elevenlabs_voice_id or "rachel"

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                    headers={
                        "xi-api-key": config.elevenlabs_api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "text": text,
                        "model_id": config.elevenlabs_model or "eleven_flash_v2_5",
                        "voice_settings": {
                            "stability": 0.5,
                            "similarity_boost": 0.75,
                            "style": 0.5,
                            "use_speaker_boost": True,
                        },
                    },
                    timeout=15.0,
                )

                if response.status_code == 200:
                    return response.content
                else:
                    logger.error("tts_error", status=response.status_code)
                    return None

        except Exception as e:
            logger.error("synthesis_error", error=str(e))
            return None

    async def process_text_input(self, session: ConversationState, text: str) -> dict:
        response_text = await self.generate_response(session)

        progress = session.get_info_collection_progress()
        audio_bytes = await self.synthesize_speech(response_text)

        result = {
            "type": "response",
            "transcript": text,
            "response": response_text,
            "phase": session.phase.value,
            "language": session.detected_language,
            "sentiment": session.collected_info.get("sentiment", "neutral"),
            "info_progress": progress["progress"],
            "missing_info": progress["missing"],
            "collected_info": {k: v for k, v in session.collected_info.items() if v},
            "state": "idle",
        }

        if audio_bytes:
            import base64
            result["audio"] = base64.b64encode(audio_bytes).decode("utf-8")
            result["audio_format"] = "mp3"

        return result

    async def process_audio_chunk(
        self, session: ConversationState, audio_data: bytes, is_final: bool = False
    ) -> dict:
        if not is_final:
            return {"type": "interim", "state": session.state.value}

        session.state = VoiceState.PROCESSING

        transcript = await self.transcribe_audio(audio_data, session.language)

        if not transcript.get("text"):
            session.state = VoiceState.IDLE
            return {"type": "no_speech", "state": "idle"}

        confidence = transcript.get("confidence", 0.0)

        session.add_user_message(transcript["text"], confidence)

        response_text = await self.generate_response(session)

        audio_bytes = await self.synthesize_speech(response_text)

        session.state = VoiceState.IDLE

        result = {
            "type": "response",
            "transcript": transcript["text"],
            "confidence": confidence,
            "response": response_text,
            "phase": session.phase.value,
            "language": session.detected_language,
            "state": "idle",
        }

        if audio_bytes:
            import base64
            result["audio"] = base64.b64encode(audio_bytes).decode("utf-8")
            result["audio_format"] = "mp3"

        return result

    def handle_interruption(self, session: ConversationState) -> dict:
        session.interruption_count += 1
        session.state = VoiceState.INTERRUPTED

        if session.detected_language == "hindi":
            message = "Main sun rahi hoon. Bataiye."
        elif session.detected_language == "hinglish":
            message = "Haan bolo, sun rahi hoon."
        else:
            message = "I am listening. Please go ahead."

        session.add_ai_message(message, is_backchannel=True)

        return {
            "type": "interrupted",
            "message": message,
            "interruption_count": session.interruption_count,
        }

    def handle_backchannel(self, session: ConversationState) -> dict:
        session.backchannel_count += 1

        acks = ["uh-huh", "I see", "go on", "yes", "right", "haan", "ji"]
        if session.detected_language == "hindi":
            acks = ["haan", "ji", "accha", "samajh gayi", "bataiye"]
        elif session.detected_language == "hinglish":
            acks = ["haan", "accha", "theek hai", "bolo"]

        ack = random.choice(acks)

        return {
            "type": "backchannel",
            "acknowledgment": ack,
            "backchannel_count": session.backchannel_count,
        }

    def end_session(self, session_id: str) -> dict:
        if session_id in self.sessions:
            session = self.sessions[session_id]
            summary = session.get_summary()
            del self.sessions[session_id]
            return {"type": "session_ended", "summary": summary}
        return {"type": "session_ended", "summary": {}}


voice_pipeline = VoicePipeline()