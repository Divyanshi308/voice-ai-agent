import asyncio
import time
import hashlib
import hmac
import json
import uuid
from typing import Optional, Callable
from enum import Enum

import structlog
from config import config

logger = structlog.get_logger()


class AgentState(Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    ESCALATING = "escalating"
    DISCONNECTED = "disconnected"


AGENT_SYSTEM_PROMPT = """You are Kataru, a multilingual customer support voice AI agent.
You handle calls for a public information and non-clinical elderly care support line.

CRITICAL BOUNDARIES:
- NEVER provide medical diagnosis (say "Please consult your doctor")
- NEVER replace trained emergency responders (say "Call 112 immediately")
- NEVER provide legal, financial, or emergency instructions as authoritative advice
- NEVER present uncertain AI-generated information as confirmed fact

CONVERSATION FLOW:
1. Greet warmly in detected language
2. Ask how you can help
3. Collect: name, issue type, details, date, address, phone number
4. Confirm understanding by repeating back: "Let me confirm: [summary]"
5. If confidence is low or issue needs human judgment, offer transfer
6. When transferring, provide concise summary of collected information

INTERRUPTION HANDLING:
- If user speaks while you are speaking, STOP immediately
- Acknowledge: "I am listening. Please go ahead."
- Continue from where they left off

BACKCHANNELING:
- While user speaks, use brief acknowledgments: "I see", "Go on", "Uh-huh"
- Do not interrupt the user's sentence
- Wait for a natural pause before responding

LOW CONFIDENCE:
- If speech is unclear, ask: "I did not catch that. Could you please repeat?"
- If you are unsure about the answer, say: "I want to make sure I give you accurate information. Let me connect you with a specialist."

INFORMATION COLLECTION PRIORITY:
1. Emergency check (is this urgent?)
2. Name
3. Issue type (billing, account, technical, general)
4. Details (what happened, when)
5. Contact info (phone, address)
6. Confirmation of all details

ESCALATION TRIGGERS:
- Medical symptoms → "Please consult your doctor or call 108"
- Legal matters → "Please consult a lawyer"
- Financial advice needed → "Please consult a financial advisor"
- Complex account issues → Transfer to human
- User explicitly requests human → Transfer immediately
- Low confidence after 2 attempts → Transfer to human

SPEAKING STYLE:
- Calm, patient, respectful tone
- Simple words, no jargon
- Under 25 words per response for voice
- Use "aap" (respectful Hindi) not "tum"
- Acknowledge emotions before solving: "I understand this is frustrating"
"""


class AgoraTokenGenerator:
    @staticmethod
    def generate_rtc_token(
        app_id: str,
        app_certificate: str,
        channel_name: str,
        uid: int,
        role: int = 1,
        expire: int = 3600,
    ) -> str:
        current_time = int(time.time())
        salt = current_time + expire

        msg = app_id + channel_name + str(uid) + str(salt)
        signature = hmac.new(
            app_certificate.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        import base64
        token = base64.b64encode(signature).decode("utf-8")

        token_data = {
            "token": token,
            "salt": salt,
            "expire": expire,
        }

        return base64.b64encode(json.dumps(token_data).encode("utf-8")).decode("utf-8")

    @staticmethod
    def generate_simple_token(
        app_id: str,
        channel_name: str,
        uid: int,
        expire: int = 3600,
    ) -> str:
        current_time = int(time.time())
        salt = current_time + expire

        msg = f"{app_id}:{channel_name}:{uid}:{salt}"
        token = hashlib.sha256(msg.encode()).hexdigest()

        return f"{app_id}.{channel_name}.{uid}.{salt}.{token[:32]}"


class AgoraAgentConfig:
    def __init__(self):
        self.app_id = config.agora_app_id
        self.app_certificate = config.agora_app_certificate
        self.agent_name = "Kataru"
        self.system_prompt = AGENT_SYSTEM_PROMPT

    def is_configured(self) -> bool:
        return bool(self.app_id and self.app_certificate)

    def get_stt_config(self) -> dict:
        return {
            "provider": "deepgram",
            "model": "nova-2",
            "language": ["hi", "en"],
            "smart_format": True,
            "punctuate": True,
            "profanity_filter": False,
            "redact": False,
            "utterances": True,
            "utterance_end_ms": 1000,
            "endpointing": 300,
            "interim_results": True,
            "vad_events": True,
            "diarize": False,
        }

    def get_tts_config(self) -> dict:
        return {
            "provider": "elevenlabs",
            "model": config.elevenlabs_model or "eleven_flash_v2_5",
            "voice_id": config.elevenlabs_voice_id or "rachel",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.5,
                "use_speaker_boost": True,
            },
            "output_format": "mp3_44100_128",
            "chunk_length_schedule": [120, 160, 250, 290],
        }

    def get_llm_config(self) -> dict:
        return {
            "provider": "openai",
            "model": config.openai_model or "gpt-4o-mini",
            "max_tokens": 150,
            "temperature": 0.7,
            "system_prompt": self.system_prompt,
        }

    def get_agent_full_config(self) -> dict:
        return {
            "name": self.agent_name,
            "system_prompt": self.system_prompt,
            "stt": self.get_stt_config(),
            "tts": self.get_tts_config(),
            "llm": self.get_llm_config(),
            "interruption": {
                "enabled": True,
                "sensitivity": 0.5,
                "silence_timeout_ms": 2000,
                "min_speech_duration_ms": 500,
            },
            "backchanneling": {
                "enabled": True,
                "frequency": 0.3,
                "acknowledgments": ["uh-huh", "I see", "go on", "yes", "right"],
            },
            "vad": {
                "enabled": True,
                "silence_duration_ms": 500,
                "speech_threshold": 0.5,
            },
            "features": {
                "interruption_handling": True,
                "background_noise_resilience": True,
                "low_confidence_detection": True,
                "human_escalation": True,
                "context_preservation": True,
                "multilingual_code_switching": True,
                "sentiment_detection": True,
                "information_collection": True,
                "confirmation_flow": True,
            },
            "safety": {
                "no_medical_diagnosis": True,
                "no_emergency_replacement": True,
                "no_legal_advice": True,
                "no_financial_advice": True,
                "no_uncertain_facts": True,
            },
        }


class AgoraAgentSession:
    def __init__(self, session_id: str, channel_name: str, user_id: int = 0):
        self.session_id = session_id
        self.channel_name = channel_name
        self.user_id = user_id
        self.state = AgentState.IDLE
        self.started_at = time.time()
        self.ended_at = None
        self.messages = []
        self.collected_info = {}
        self.interruption_count = 0
        self.backchannel_count = 0
        this = self

    def add_message(self, role: str, text: str, **kwargs):
        msg = {
            "role": role,
            "content": text,
            "timestamp": time.time(),
            **kwargs,
        }
        self.messages.append(msg)

    def collect_info(self, key: str, value: str):
        self.collected_info[key] = value

    def get_conversation_summary(self) -> str:
        user_msgs = [m["content"] for m in self.messages if m["role"] == "user"]
        ai_msgs = [m["content"] for m in self.messages if m["role"] == "assistant"]

        summary_parts = []
        summary_parts.append(f"Duration: {self.get_duration()}s")
        summary_parts.append(f"Turns: {len(user_msgs)}")
        summary_parts.append(f"Interruptions: {self.interruption_count}")

        if self.collected_info:
            summary_parts.append(f"Collected: {json.dumps(self.collected_info)}")

        if user_msgs:
            summary_parts.append("User said: " + " | ".join(user_msgs[-3:]))

        if ai_msgs:
            summary_parts.append("Agent said: " + " | ".join(ai_msgs[-3:]))

        return ". ".join(summary_parts)

    def get_duration(self) -> float:
        end = self.ended_at or time.time()
        return round(end - self.started_at, 1)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "channel_name": self.channel_name,
            "user_id": self.user_id,
            "state": self.state.value,
            "started_at": self.started_at,
            "duration": self.get_duration(),
            "message_count": len(self.messages),
            "collected_info": self.collected_info,
            "interruption_count": self.interruption_count,
            "backchannel_count": self.backchannel_count,
        }


class AgoraAgentManager:
    def __init__(self):
        self.config = AgoraAgentConfig()
        self.active_sessions: dict[str, AgoraAgentSession] = {}
        self.completed_sessions: list[dict] = []

    def get_token(self, channel_name: str, uid: int = 0) -> dict:
        if not self.config.is_configured():
            return {
                "success": False,
                "error": "Agora credentials not configured",
                "demo": True,
                "token": AgoraTokenGenerator.generate_simple_token(
                    "demo_app_id", channel_name, uid or 1
                ),
                "channel": channel_name,
                "uid": uid or 1,
                "app_id": "demo_app_id",
            }

        try:
            token = AgoraTokenGenerator.generate_rtc_token(
                app_id=self.config.app_id,
                app_certificate=self.config.app_certificate,
                channel_name=channel_name,
                uid=uid or 1,
            )

            return {
                "success": True,
                "token": token,
                "channel": channel_name,
                "uid": uid or 1,
                "app_id": self.config.app_id,
            }

        except Exception as e:
            logger.error("token_generation_error", error=str(e))
            return {"success": False, "error": str(e)}

    def start_session(self, channel_name: str = None, user_id: int = 0) -> dict:
        session_id = str(uuid.uuid4())
        channel = channel_name or f"kataru-{session_id[:8]}"

        if session_id in self.active_sessions:
            return {"success": False, "error": "Session already active"}

        token_result = self.get_token(channel, user_id)

        session = AgoraAgentSession(session_id, channel, user_id)
        session.state = AgentState.CONNECTING
        self.active_sessions[session_id] = session

        session.state = AgentState.LISTENING

        return {
            "success": True,
            "session_id": session_id,
            "channel": channel,
            "token": token_result.get("token"),
            "app_id": self.config.app_id or "demo_app_id",
            "agent_config": self.config.get_agent_full_config(),
            "demo": not self.config.is_configured(),
            "state": session.state.value,
        }

    def end_session(self, session_id: str) -> dict:
        if session_id not in self.active_sessions:
            return {"success": False, "error": "Session not found"}

        session = self.active_sessions.pop(session_id)
        session.state = AgentState.DISCONNECTED
        session.ended_at = time.time()

        summary = session.get_conversation_summary()
        session_dict = session.to_dict()
        session_dict["summary"] = summary
        self.completed_sessions.append(session_dict)

        return {
            "success": True,
            "session_id": session_id,
            "duration": session.get_duration(),
            "summary": summary,
            "collected_info": session.collected_info,
        }

    def handle_interruption(self, session_id: str) -> dict:
        if session_id not in self.active_sessions:
            return {"success": False, "error": "Session not found"}

        session = self.active_sessions[session_id]
        session.interruption_count += 1
        session.state = AgentState.INTERRUPTED

        return {
            "success": True,
            "session_id": session_id,
            "interruption_count": session.interruption_count,
            "message": "I am listening. Please go ahead.",
        }

    def handle_backchannel(self, session_id: str) -> dict:
        if session_id not in self.active_sessions:
            return {"success": False, "error": "Session not found"}

        session = self.active_sessions[session_id]
        session.backchannel_count += 1

        acknowledgments = ["uh-huh", "I see", "go on", "yes", "right", "haan", "ji"]
        import random
        ack = random.choice(acknowledgments)

        return {
            "success": True,
            "session_id": session_id,
            "acknowledgment": ack,
            "backchannel_count": session.backchannel_count,
        }

    def collect_information(self, session_id: str, key: str, value: str) -> dict:
        if session_id not in self.active_sessions:
            return {"success": False, "error": "Session not found"}

        session = self.active_sessions[session_id]
        session.collect_info(key, value)

        return {
            "success": True,
            "session_id": session_id,
            "collected_info": session.collected_info,
        }

    def get_session_status(self, session_id: str) -> dict:
        if session_id not in self.active_sessions:
            return {"active": False}

        session = self.active_sessions[session_id]
        return {
            "active": True,
            **session.to_dict(),
        }

    def get_all_sessions(self) -> list:
        return [s.to_dict() for s in self.active_sessions.values()]

    def get_completed_sessions(self) -> list:
        return self.completed_sessions[-20:]


agora_agent = AgoraAgentManager()
