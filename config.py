import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from dotenv import load_dotenv


load_dotenv(override=True)


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # AGORA (REQUIRED - Primary Voice Platform)
    agora_app_id: str = Field(default="")
    agora_app_certificate: str = Field(default="")

    # SPEECH TO TEXT (Deepgram - BYOK)
    deepgram_api_key: str = Field(default="")

    # LANGUAGE MODEL (OpenAI - BYOK)
    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="gpt-4o-mini")

    # GROQ (FREE - fast LLM)
    groq_api_key: str = Field(default="")
    groq_model: str = Field(default="groq/compound-mini")

    # TEXT TO SPEECH (ElevenLabs - BYOK)
    elevenlabs_api_key: str = Field(default="")
    elevenlabs_voice_id: str = Field(default="rachel")
    elevenlabs_model: str = Field(default="eleven_flash_v2_5")

    # TICKETING (Zendesk)
    zendesk_api_key: str = Field(default="")
    zendesk_email: str = Field(default="")
    zendesk_subdomain: str = Field(default="")

    # SMS NOTIFICATIONS (Twilio)
    twilio_account_sid: str = Field(default="")
    twilio_auth_token: str = Field(default="")
    twilio_phone_number: str = Field(default="+15559876543")

    # SERVER
    port: int = Field(default=8000)
    host: str = Field(default="0.0.0.0")
    log_level: str = Field(default="INFO")
    environment: str = Field(default="development")

    def __repr__(self) -> str:
        return (
            "Config(\n"
            f"  agora_app_id={self.agora_app_id[:4] + '****' if self.agora_app_id else 'not set'},\n"
            f"  openai_api_key={'set' if self.openai_api_key else 'not set'},\n"
            f"  elevenlabs_api_key={'set' if self.elevenlabs_api_key else 'not set'},\n"
            f"  deepgram_api_key={'set' if self.deepgram_api_key else 'not set'},\n"
            f"  environment={self.environment},\n"
            f"  port={self.port},\n"
            f")"
        )


try:
    config = Config()
except Exception as e:
    print(f"ERROR: Failed to load configuration: {e}")
    raise
