import asyncio
import re
from typing import Callable, Optional
from elevenlabs.client import AsyncElevenLabs
from config import config


class TextToSpeech:
    def __init__(self):
        self.client = AsyncElevenLabs(api_key=config.elevenlabs_api_key)
        self.voice_id = config.elevenlabs_voice_id
        self.current_stream = None
        self.is_playing = False
        self.cache = {}

        self.emotion_settings = {
            "calm": {"stability": 0.65, "clarity": 0.75, "style": 0.3},
            "stressed": {"stability": 0.5, "clarity": 0.85, "style": 0.15},
            "frustrated": {"stability": 0.45, "clarity": 0.9, "style": 0.1},
            "angry": {"stability": 0.4, "clarity": 0.95, "style": 0.05},
            "happy": {"stability": 0.7, "clarity": 0.7, "style": 0.5},
        }

    async def stream_speech(
        self,
        text: str,
        on_audio_chunk: Callable,
        language: str = "en",
        sentiment: str = "calm",
    ):
        clean_text = self._clean_for_voice(text)

        cache_key = f"{clean_text}_{sentiment}"
        if cache_key in self.cache:
            for chunk in self.cache[cache_key]:
                await on_audio_chunk(chunk)
            return

        self.is_playing = True

        try:
            settings = self.emotion_settings.get(
                sentiment, self.emotion_settings["calm"]
            )

            audio_stream = await self.client.generate(
                text=clean_text,
                voice=self.voice_id,
                model=config.elevenlabs_model,
                voice_settings=settings,
            )

            chunks = []
            if isinstance(audio_stream, bytes):
                await on_audio_chunk(audio_stream)
                chunks.append(audio_stream)
            else:
                async for chunk in audio_stream:
                    if chunk and self.is_playing:
                        if isinstance(chunk, bytes):
                            await on_audio_chunk(chunk)
                            chunks.append(chunk)

            self.cache[cache_key] = chunks

        except Exception as e:
            print(f"TTS error: {e}")
        finally:
            self.is_playing = False
            self.current_stream = None

    def stop(self):
        if self.is_playing:
            self.is_playing = False
            self.current_stream = None

    def _clean_for_voice(self, text: str) -> str:
        text = re.sub(r'[*_`#]', '', text)
        text = re.sub(r'http\S+', '', text)
        text = re.sub(r'[<>{}\[\]]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        sentences = text.split('.')
        if len(sentences) > 3:
            text = '.'.join(sentences[:3]) + '.'
        return text

    async def pre_cache_common_phrases(self):
        phrases = [
            "Hello! How can I help you today?",
            "One moment please.",
            "I'm connecting you with a specialist.",
            "Can you please repeat that?",
            "Thank you for your patience.",
            "Let me check that for you...",
            "I understand, let me help you right away...",
            "Got it, one moment...",
        ]
        for phrase in phrases:
            try:
                audio = await self.client.generate(
                    text=phrase,
                    voice=self.voice_id,
                    model=config.elevenlabs_model,
                )
                self.cache[phrase] = [audio] if isinstance(audio, bytes) else []
            except Exception as e:
                print(f"Cache error: {e}")
