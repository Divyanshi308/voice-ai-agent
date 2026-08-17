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

    async def stream_speech(self, text: str, on_audio_chunk: Callable, language: str = "en"):
        clean_text = self._clean_for_voice(text)

        if clean_text in self.cache:
            for chunk in self.cache[clean_text]:
                await on_audio_chunk(chunk)
            return

        self.is_playing = True

        try:
            audio_stream = await self.client.generate(
                text=clean_text,
                voice=self.voice_id,
                model=config.elevenlabs_model,
            )

            if isinstance(audio_stream, bytes):
                await on_audio_chunk(audio_stream)
            else:
                async for chunk in audio_stream:
                    if chunk and self.is_playing:
                        if isinstance(chunk, bytes):
                            await on_audio_chunk(chunk)

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
