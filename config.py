import os
from typing import Optional, Tuple
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from dotenv import load_dotenv


load_dotenv(override=True)


def _mask(value: Optional[str]) -> str:
    if not value or len(value) < 4:
        return "****"
    return value[:4] + "****"


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # TELEPHONY (Telnyx)
    telnyx_api_key: str = Field(default="")
    telnyx_phone_number: str = Field(default="")
    telnyx_call_control_id: str = Field(default="")

    # SPEECH TO TEXT (Deepgram)
    deepgram_api_key: str = Field(default="")

    # AGORA (Real-Time Voice Platform)
    agora_app_id: str = Field(default="")
    agora_app_certificate: str = Field(default="")

    # LANGUAGE MODEL (OpenAI)
    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="gpt-4o")

    # TEXT TO SPEECH (ElevenLabs)
    elevenlabs_api_key: str = Field(default="")
    elevenlabs_voice_id: str = Field(default="rachel")
    elevenlabs_model: str = Field(default="eleven_turbo_v2_5")

    # TICKETING (Zendesk)
    zendesk_api_key: str = Field(default="")
    zendesk_email: str = Field(default="")
    zendesk_subdomain: str = Field(default="")

    # SMS NOTIFICATIONS (Twilio)
    twilio_account_sid: str = Field(default="")
    twilio_auth_token: str = Field(default="")
    twilio_phone_number: str = Field(default="+15559876543")

    # DATABASE
    redis_url: str = Field(default="redis://localhost:6379")
    database_url: str = Field(
        default="postgresql://user:password@localhost:5432/voice_agent"
    )

    # HUMAN ESCALATION
    human_transfer_number: str = Field(default="")
    human_agent_name: str = Field(default="Sarah")

    # SERVER
    port: int = Field(default=8000)
    host: str = Field(default="0.0.0.0")
    log_level: str = Field(default="INFO")
    environment: str = Field(default="development")

    # SAFETY
    escalation_confidence_threshold: float = Field(default=0.7)
    max_turns_before_escalation: int = Field(default=10)
    emergency_numbers: str = Field(default="112,911")

    @field_validator(
        "telnyx_phone_number",
        "human_transfer_number",
        "twilio_phone_number",
        mode="before",
    )
    @classmethod
    def phone_starts_with_plus(cls, v: str) -> str:
        if v is None:
            return v
        v = v.strip()
        if v and not v.startswith("+"):
            raise ValueError(f"Phone number must start with '+', got: {v}")
        return v

    def get_deepgram_url(self) -> str:
        return f"wss://api.deepgram.com/v1/listen?model=nova-2&language=en&smart_format=true&token={self.deepgram_api_key}"

    def get_elevenlabs_url(self) -> str:
        return (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{self.elevenlabs_voice_id}"
            f"/stream?model_id={self.elevenlabs_model}"
        )

    def get_zendesk_auth(self) -> Tuple[str, str]:
        return (self.zendesk_email, self.zendesk_api_key)

    def is_production(self) -> bool:
        return self.environment == "production"

    def __repr__(self) -> str:
        return (
            "Config(\n"
            "  telnyx_api_key={!r},\n"
            "  deepgram_api_key={!r},\n"
            "  openai_api_key={!r},\n"
            "  elevenlabs_api_key={!r},\n"
            "  zendesk_api_key={!r},\n"
            "  twilio_auth_token={!r},\n"
            "  environment={!r},\n"
            "  port={!r},\n"
            "  host={!r},\n"
            "  openai_model={!r},\n"
            "  elevenlabs_model={!r},\n"
            "  elevenlabs_voice_id={!r},\n"
            ")".format(
                _mask(self.telnyx_api_key),
                _mask(self.deepgram_api_key),
                _mask(self.openai_api_key),
                _mask(self.elevenlabs_api_key),
                _mask(self.zendesk_api_key),
                _mask(self.twilio_auth_token),
                self.environment,
                self.port,
                self.host,
                self.openai_model,
                self.elevenlabs_model,
                self.elevenlabs_voice_id,
            )
        )


try:
    config = Config()
except Exception as e:
    print(f"ERROR: Failed to load configuration from .env file: {e}")
    print("Ensure a valid .env file exists in the project root with all required variables.")
    raise
