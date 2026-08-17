import time
import hashlib
import hmac
import json
from typing import Optional

import structlog
from config import config

logger = structlog.get_logger()


AGENT_SYSTEM_PROMPT = """You are Kataru, a multilingual customer support voice AI agent.
You handle calls for a public information and non-clinical elderly care support line.

CRITICAL BOUNDARIES:
- NEVER provide medical diagnosis
- NEVER replace trained emergency responders (say "Call 112 immediately")
- NEVER provide legal, financial, or emergency instructions as authoritative advice
- NEVER present uncertain AI-generated information as confirmed fact

CAPABILITIES:
- Multilingual support: Hindi, English, Hinglish (code-switching)
- Natural interruption handling: if user speaks over you, stop and listen
- Information collection: name, issue, details, date, address, phone
- Confirmation: repeat back collected information for verification
- Low-confidence detection: if unclear, ask user to repeat or clarify
- Prioritized question flow: emergency > safety > issue > details > resolution
- Background-noise resilience: focus on speaker, ignore background
- Human escalation: transfer to human when confidence is low or situation requires judgment

SPEAKING STYLE:
- Calm, patient, respectful
- Simple words, no jargon
- Under 25 words per response
- Use "aap" (respectful Hindi) not "tum"
- Acknowledge emotions before solving
"""


class AgoraAgentConfig:
    def __init__(self):
        self.app_id = config.agora_app_id
        self.app_certificate = config.agora_app_certificate
        self.agent_name = "Kataru"
        self.system_prompt = AGENT_SYSTEM_PROMPT

    def is_configured(self) -> bool:
        return bool(self.app_id and self.app_certificate)


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
        import hmac
        import hashlib
        import time

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


class AgoraAgentManager:
    def __init__(self):
        self.config = AgoraAgentConfig()
        self.active_channels: dict[str, dict] = {}

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

    def create_agent_config(self) -> dict:
        return {
            "name": self.config.agent_name,
            "system_prompt": self.config.system_prompt,
            "voice": {
                "provider": "elevenlabs",
                "model": config.elevenlabs_model,
                "voice_id": config.elevenlabs_voice_id,
                "language": "auto",
                "speed": 1.0,
            },
            "stt": {
                "provider": "deepgram",
                "model": "nova-2",
                "languages": ["hi", "en"],
                "smart_format": True,
                "punctuate": True,
                "utterances": True,
                "endpointing": 300,
                "interim_results": True,
            },
            "llm": {
                "provider": "openai",
                "model": config.openai_model,
                "max_tokens": 80,
                "temperature": 0.7,
            },
            "features": {
                "interruption_handling": True,
                "background_noise_resilience": True,
                "low_confidence_detection": True,
                "human_escalation": True,
                "context_preservation": True,
                "multilingual_code_switching": True,
            },
            "safety": {
                "no_medical_diagnosis": True,
                "no_emergency_replacement": True,
                "no_legal_advice": True,
                "no_financial_advice": True,
                "no_uncertain_facts": True,
            },
            "conversation_flow": {
                "greeting": "Warm greeting in detected language",
                "issue_identification": "Ask what problem the caller faces",
                "information_collection": "Collect name, issue, details, date, address, phone",
                "confirmation": "Repeat back collected information",
                "escalation": "Transfer to human when confidence is low",
                "farewell": "Warm farewell with offer to help again",
            },
        }

    def start_channel(self, channel_name: str, user_id: int = 0) -> dict:
        if channel_name in self.active_channels:
            return {"success": False, "error": "Channel already active"}

        token_result = self.get_token(channel_name, user_id)

        self.active_channels[channel_name] = {
            "started_at": time.time(),
            "user_id": user_id,
            "agent_config": self.create_agent_config(),
            "token": token_result.get("token"),
            "state": "active",
        }

        return {
            "success": True,
            "channel": channel_name,
            "token": token_result.get("token"),
            "app_id": self.config.app_id or "demo_app_id",
            "agent": self.create_agent_config(),
            "demo": not self.config.is_configured(),
        }

    def end_channel(self, channel_name: str) -> dict:
        if channel_name not in self.active_channels:
            return {"success": False, "error": "Channel not found"}

        channel = self.active_channels.pop(channel_name)
        duration = round(time.time() - channel["started_at"], 1)

        return {
            "success": True,
            "channel": channel_name,
            "duration": duration,
            "summary": {
                "started_at": channel["started_at"],
                "ended_at": time.time(),
                "duration": duration,
                "state": "ended",
            },
        }

    def get_channel_status(self, channel_name: str) -> dict:
        if channel_name not in self.active_channels:
            return {"active": False}

        channel = self.active_channels[channel_name]
        return {
            "active": True,
            "channel": channel_name,
            "state": channel["state"],
            "duration": round(time.time() - channel["started_at"], 1),
        }


agora_agent = AgoraAgentManager()
