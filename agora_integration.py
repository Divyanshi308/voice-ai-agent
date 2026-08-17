import time
import hmac
import hashlib
import base64
from typing import Optional

import structlog

logger = structlog.get_logger()


class AgoraTokenGenerator:
    def __init__(self, app_id: str, app_certificate: str):
        self.app_id = app_id
        self.app_certificate = app_certificate

    def generate_rtc_token(
        self,
        channel_name: str,
        uid: str,
        role: int = 1,
        expire_time: int = 3600,
    ) -> str:
        now = int(time.time())
        expire = now + expire_time

        token_data = {
            "channel_name": channel_name,
            "uid": uid,
            "role": role,
            "expire": expire,
            "salt": now,
            "create_time": now,
        }

        raw_value = (
            f"{self.app_id}{channel_name}{uid}{role}{expire}{now}"
        )

        if self.app_certificate:
            signature = hmac.new(
                self.app_certificate.encode("utf-8"),
                raw_value.encode("utf-8"),
                hashlib.sha256,
            ).digest()
            token = base64.b64encode(signature).decode("utf-8")
        else:
            token = base64.b64encode(raw_value.encode("utf-8")).decode("utf-8")

        logger.info("agora_token_generated", channel=channel_name, uid=uid)
        return token


class AgoraVoiceEngine:
    def __init__(self):
        self.active_sessions: dict[str, dict] = {}
        self._total_sessions = 0

    def create_session(
        self,
        session_id: str,
        channel_name: str,
        uid: str,
        on_transcript=None,
        on_audio=None,
    ) -> dict:
        session = {
            "session_id": session_id,
            "channel_name": channel_name,
            "uid": uid,
            "created_at": time.time(),
            "is_active": True,
            "on_transcript": on_transcript,
            "on_audio": on_audio,
            "state": {},
        }
        self.active_sessions[session_id] = session
        self._total_sessions += 1
        logger.info(
            "agora_session_created",
            session_id=session_id,
            channel=channel_name,
        )
        return session

    def get_session(self, session_id: str) -> Optional[dict]:
        return self.active_sessions.get(session_id)

    def update_session_state(self, session_id: str, state: dict) -> None:
        if session_id in self.active_sessions:
            self.active_sessions[session_id]["state"].update(state)

    def end_session(self, session_id: str) -> None:
        if session_id in self.active_sessions:
            self.active_sessions[session_id]["is_active"] = False
            del self.active_sessions[session_id]
            logger.info("agora_session_ended", session_id=session_id)

    def get_stats(self) -> dict:
        return {
            "active_sessions": len(self.active_sessions),
            "total_sessions": self._total_sessions,
        }
