import time
import uuid
from typing import Optional

import structlog
from config import config

logger = structlog.get_logger()

AGENT_INSTRUCTIONS = """You are Kataru, a friendly multilingual AI voice assistant for elderly care in India.

RULES:
- Respond in the EXACT language the user speaks (Hindi, English, or Hinglish)
- Keep responses short and clear (under 40 words for voice)
- Be warm, patient, and respectful
- For emergencies, say "Please call 112 immediately"
- For medical questions, give general info but always say "consult your doctor"
- Never give legal or financial advice - redirect to professionals
- Use "aap" (respectful Hindi) not "tum"
- Acknowledge feelings before solving problems
- If you don't understand, ask them to repeat clearly

SPEAKING STYLE:
- Calm, friendly, like talking to a grandparent
- Simple words, no jargon
- Natural pauses between sentences"""


class AgoraSession:
    def __init__(self, session_id: str, channel_name: str, user_id: int = 0):
        self.session_id = session_id
        self.channel_name = channel_name
        self.user_id = user_id
        self.state = "connecting"
        self.started_at = time.time()
        self.ended_at = None
        self.convoai_session = None
        self.agent_uid = "kataru-agent"

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "channel_name": self.channel_name,
            "state": self.state,
            "duration": round(time.time() - self.started_at, 1),
            "agent_uid": self.agent_uid,
        }


class AgoraVoiceManager:
    def __init__(self):
        self.active_sessions: dict[str, AgoraSession] = {}
        self.completed_sessions: list[dict] = []
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not config.agora_app_id or not config.agora_app_certificate:
                return None
            try:
                from agora_agent import Agora, Area
                self._client = Agora(
                    area=Area.AP,
                    app_id=config.agora_app_id,
                    app_certificate=config.agora_app_certificate,
                )
            except Exception as e:
                logger.error("agora_client_init_error", error=str(e))
                return None
        return self._client

    def is_configured(self) -> bool:
        return bool(config.agora_app_id and config.agora_app_certificate)

    async def start_voice_session(
        self, channel_name: str = None, user_id: int = 0
    ) -> dict:
        session_id = str(uuid.uuid4())
        channel = channel_name or f"kataru-{session_id[:8]}"
        agent_uid = f"agent-{session_id[:8]}"

        client = self._get_client()
        if client is None:
            return {
                "success": False,
                "error": "Agora credentials not configured",
            }

        try:
            from agora_agent import Agent, Groq, MiniMaxTTS, DeepgramSTT

            agent = Agent(
                client=client,
                instructions=AGENT_INSTRUCTIONS,
            )

            agent.with_llm(
                Groq(
                    api_key=config.groq_api_key,
                    model=config.groq_model,
                )
            )

            agent.with_tts(
                MiniMaxTTS(
                    model="speech_2_6_turbo",
                    voice_id="English_captivating_female1",
                )
            )

            agent.with_stt(
                DeepgramSTT(
                    model="nova-2",
                    language="hi",
                    smart_format=True,
                    punctuate=True,
                )
            )

            from agora_agent import expires_in_hours

            session_obj = agent.create_session(
                client,
                channel=channel,
                agent_uid=agent_uid,
                remote_uids=["*"],
                name=f"kataru-session-{session_id[:8]}",
                idle_timeout=120,
                expires_in=expires_in_hours(1),
                debug=True,
            )

            session = AgoraSession(session_id, channel, user_id)
            session.agent_uid = agent_uid
            session.convoai_session = session_obj
            session.state = "active"
            self.active_sessions[session_id] = session

            result = session_obj.start()
            logger.info(
                "voice_session_started",
                session_id=session_id,
                channel=channel,
                result=str(result)[:200],
            )

            return {
                "success": True,
                "session_id": session_id,
                "channel_name": channel,
                "agent_uid": agent_uid,
                "app_id": config.agora_app_id,
                "state": "active",
            }

        except Exception as e:
            logger.error("voice_session_error", error=str(e))
            return {
                "success": False,
                "error": str(e),
            }

    async def end_voice_session(self, session_id: str) -> dict:
        if session_id not in self.active_sessions:
            return {"success": False, "error": "Session not found"}

        session = self.active_sessions.pop(session_id)
        session.state = "ended"
        session.ended_at = time.time()

        if session.convoai_session:
            try:
                session.convoai_session.stop()
            except Exception as e:
                logger.error("session_stop_error", error=str(e))

        duration = round(session.ended_at - session.started_at, 1)
        self.completed_sessions.append(session.to_dict())

        return {
            "success": True,
            "session_id": session_id,
            "duration": duration,
        }

    def get_session_status(self, session_id: str) -> dict:
        if session_id not in self.active_sessions:
            return {"active": False}
        return {"active": True, **self.active_sessions[session_id].to_dict()}

    def get_rtc_token(self, channel_name: str, uid: int = 0) -> dict:
        client = self._get_client()
        if client is None:
            return {"success": False, "error": "Agora not configured"}

        try:
            from agora_agent import generate_rtc_token

            token = generate_rtc_token(
                client=client,
                channel=channel_name,
                uid=str(uid or 1),
            )
            return {
                "success": True,
                "token": token,
                "channel": channel_name,
                "uid": uid or 1,
                "app_id": config.agora_app_id,
            }
        except Exception as e:
            logger.error("rtc_token_error", error=str(e))
            return {"success": False, "error": str(e)}


agora_agent = AgoraVoiceManager()
