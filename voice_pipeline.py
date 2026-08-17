import asyncio
import io
import json
import time
import uuid
from typing import Optional
from enum import Enum

import structlog
from config import config

logger = structlog.get_logger()


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
        self.collected_info = {}
        self.language = "auto"
        self.confidence = 1.0
        self.started_at = time.time()
        self.last_user_input = ""
        self.last_ai_response = ""
        self.interruption_count = 0

    def add_user_message(self, text: str):
        self.messages.append({"role": "user", "content": text})
        self.last_user_input = text

    def add_ai_message(self, text: str):
        self.messages.append({"role": "assistant", "content": text})
        self.last_ai_response = text

    def get_history(self, limit: int = 10):
        return self.messages[-limit:]

    def get_summary(self):
        user_msgs = [m["content"] for m in self.messages if m["role"] == "user"]
        ai_msgs = [m["content"] for m in self.messages if m["role"] == "assistant"]
        return {
            "total_turns": len(user_msgs),
            "user_messages": user_msgs[-5:],
            "ai_messages": ai_msgs[-5:],
            "collected_info": self.collected_info,
            "language": self.language,
            "duration": round(time.time() - self.started_at, 1),
        }


SYSTEM_PROMPT = """You are Kataru, a multilingual customer support voice AI agent for elderly care.
You handle calls for a public information and non-clinical support line.

CRITICAL RULES:
1. Respond in the EXACT language the user speaks (Hindi, English, or Hinglish)
2. Keep responses under 25 words - this is a VOICE call, brevity matters
3. NEVER provide medical diagnosis - say "Please consult your doctor"
4. NEVER replace emergency responders - say "Please call 112 immediately"
5. NEVER provide legal advice - say "Please consult a lawyer"
6. NEVER provide financial advice - say "Please consult a financial advisor"
7. NEVER present uncertain information as confirmed fact

CONVERSATION FLOW:
1. Greet warmly and ask how you can help
2. Collect: name, issue type, details, date, address, phone
3. Confirm understanding by repeating back
4. If confidence is low or issue needs human judgment, offer to transfer

ESCALATION TRIGGERS (offer human agent when):
- User is distressed or angry
- Medical symptoms described
- Legal or financial matters
- Complex account issues
- Low confidence in understanding
- User explicitly requests

SPEAKING STYLE:
- Calm, patient, respectful tone
- Simple words, no jargon
- Speak slowly and clearly
- Use "aap" not "tum" in Hindi (respectful)
- Acknowledge emotions: "I understand this is frustrating"

COLLECTED INFORMATION FORMAT:
When you have enough info, confirm: "Let me confirm: [summary]. Is this correct?"
If user confirms, say: "I will connect you with a specialist who can help."
"""


class VoicePipeline:
    def __init__(self):
        self.sessions: dict[str, ConversationState] = {}

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
                "error": "No Deepgram API key configured"
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
                    alternatives = result.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])
                    if alternatives:
                        transcript = alternatives[0].get("transcript", "")
                        confidence = alternatives[0].get("confidence", 0.0)
                        return {
                            "text": transcript,
                            "confidence": confidence,
                            "language": result.get("results", {}).get("channels", [{}])[0].get("detected_language", language),
                        }
                else:
                    logger.error("deepgram_error", status=response.status_code)
                    return {"text": "", "confidence": 0.0, "language": language, "error": f"Deepgram error: {response.status_code}"}

        except Exception as e:
            logger.error("transcription_error", error=str(e))
            return {"text": "", "confidence": 0.0, "language": language, "error": str(e)}

    async def generate_response(self, session: ConversationState) -> str:
        if not config.openai_api_key or config.openai_api_key.startswith("dummy"):
            return self._generate_demo_response(session)

        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=config.openai_api_key)

            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            history = session.get_history(limit=10)
            messages.extend(history)

            response_obj = await client.chat.completions.create(
                model=config.openai_model,
                messages=messages,
                max_tokens=80,
                temperature=0.7,
            )

            response = response_obj.choices[0].message.content
            session.add_ai_message(response)
            return response

        except Exception as e:
            logger.error("llm_error", error=str(e))
            return self._generate_demo_response(session)

    def _generate_demo_response(self, session: ConversationState) -> str:
        if not session.messages:
            response = "Namaste! I am Kataru, your support assistant. How can I help you today?"
            session.add_ai_message(response)
            return response

        last_msg = session.last_user_input.lower() if session.last_user_input else ""

        import re
        words = set(re.findall(r'\w+', last_msg))

        emergency = {"emergency", "bachao", "ambulance", "112", "911", "urgent"}
        if words & emergency:
            response = "This sounds urgent! Please call 112 immediately. I am here with you."
            session.add_ai_message(response)
            return response

        medical = {"doctor", "hospital", "sick", "medicine", "fever", "pain"}
        if words & medical:
            response = "I cannot provide medical advice. Please consult your doctor or call 112 if emergency."
            session.add_ai_message(response)
            return response

        legal = {"legal", "court", "lawyer"}
        if words & legal:
            response = "I cannot provide legal advice. Please consult with a lawyer."
            session.add_ai_message(response)
            return response

        greeting = {"hello", "hi", "hey", "namaste"}
        if words & greeting:
            response = "Namaste! How can I help you today?"
            session.add_ai_message(response)
            return response

        name_words = {"naam", "name"}
        if words & name_words or "my name" in last_msg:
            parts = last_msg.replace("my name is", "").replace("mera naam", "").strip()
            name = parts.title() if parts else ""
            if name:
                response = f"Hello {name}! How can I help you today?"
            else:
                response = "What is your name?"
            session.add_ai_message(response)
            return response

        issue_words = {"problem", "issue", "help", "complaint"}
        if words & issue_words:
            response = "Please tell me more about your issue. I am here to help."
            session.add_ai_message(response)
            return response

        thanks_words = {"thank", "dhanyavaad"}
        if words & thanks_words:
            response = "You are welcome! Is there anything else I can help with?"
            session.add_ai_message(response)
            return response

        bye_words = {"bye", "goodbye"}
        if words & bye_words:
            response = "Goodbye! Take care. I am here if you need help."
            session.add_ai_message(response)
            return response

        escalate = {"human", "agent", "person", "transfer"}
        if words & escalate:
            response = "I will connect you with a human specialist. One moment please."
            session.add_ai_message(response)
            return response

        response = "I understand. Please tell me more details so I can help you better."
        session.add_ai_message(response)
        return response

    async def synthesize_speech(self, text: str, voice_id: str = "rachel") -> Optional[bytes]:
        if not config.elevenlabs_api_key or config.elevenlabs_api_key.startswith("dummy"):
            return None

        try:
            import httpx

            voice = config.elevenlabs_voice_id or voice_id

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                    headers={
                        "xi-api-key": config.elevenlabs_api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "text": text,
                        "model_id": config.elevenlabs_model,
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

    async def process_audio_chunk(
        self,
        session: ConversationState,
        audio_data: bytes,
        is_final: bool = False,
    ) -> dict:
        if not is_final:
            return {"type": "interim", "state": session.state.value}

        session.state = VoiceState.PROCESSING

        transcript = await self.transcribe_audio(audio_data, session.language)

        if not transcript.get("text"):
            session.state = VoiceState.IDLE
            return {"type": "no_speech", "state": "idle"}

        confidence = transcript.get("confidence", 0.0)
        if confidence < 0.3:
            session.state = VoiceState.IDLE
            return {
                "type": "low_confidence",
                "text": transcript["text"],
                "confidence": confidence,
                "response": "I did not catch that clearly. Could you please repeat?",
                "state": "idle",
            }

        session.add_user_message(transcript["text"])

        response_text = await self.generate_response(session)

        audio_bytes = await self.synthesize_speech(response_text)

        session.state = VoiceState.IDLE

        result = {
            "type": "response",
            "transcript": transcript["text"],
            "confidence": confidence,
            "response": response_text,
            "state": "idle",
        }

        if audio_bytes:
            import base64
            result["audio"] = base64.b64encode(audio_bytes).decode("utf-8")
            result["audio_format"] = "mp3"

        return result

    async def process_text_input(self, session: ConversationState, text: str) -> dict:
        session.add_user_message(text)
        response_text = await self.generate_response(session)
        return {
            "type": "response",
            "transcript": text,
            "response": response_text,
            "state": "idle",
        }

    def handle_interruption(self, session: ConversationState):
        session.interruption_count += 1
        session.state = VoiceState.INTERRUPTED
        return {
            "type": "interrupted",
            "message": "I am listening. Please go ahead.",
            "interruption_count": session.interruption_count,
        }

    def end_session(self, session_id: str) -> dict:
        if session_id in self.sessions:
            session = self.sessions[session_id]
            summary = session.get_summary()
            del self.sessions[session_id]
            return {"type": "session_ended", "summary": summary}
        return {"type": "session_ended", "summary": {}}


voice_pipeline = VoicePipeline()
