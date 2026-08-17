import asyncio
import json
import time
from typing import Callable, Optional

import structlog
import websockets
from websockets.exceptions import ConnectionClosed

logger = structlog.get_logger()

DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"


class SpeechToText:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.connection: Optional[websockets.WebSocketClientProtocol] = None
        self.audio_buffer: bytes = b""
        self.BUFFER_SIZE = 1600
        self.language_tracking: dict[str, int] = {}
        self.on_transcript: Optional[Callable] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._connected = False
        self._total_utterances = 0

    async def start_streaming(self, on_transcript: Callable):
        self.on_transcript = on_transcript
        await self._connect()

    async def _connect(self):
        headers = {"Authorization": f"Token {self.api_key}"}
        config = {
            "model": "nova-3",
            "language": "multi",
            "punctuate": True,
            "interim_results": True,
            "utterance_end_ms": 1000,
            "vad_events": True,
            "encoding": "mulaw",
            "sample_rate": 8000,
            "channels": 1,
            "endpointing": 300,
        }

        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                self.connection = await websockets.connect(
                    DEEPGRAM_WS_URL,
                    extra_headers=headers,
                    ping_interval=10,
                    ping_timeout=5,
                )
                await self.connection.send(json.dumps(config))
                self._connected = True
                logger.info("deepgram.connected")
                self._listener_task = asyncio.create_task(self._listen())
                return
            except Exception as e:
                retries += 1
                wait = 2 ** retries
                logger.warning(
                    "deepgram.connection_failed",
                    attempt=retries,
                    max_retries=max_retries,
                    error=str(e),
                )
                if retries >= max_retries:
                    logger.error("deepgram.max_retries_exceeded")
                    raise
                await asyncio.sleep(wait)

    async def send_audio(self, audio_chunk: bytes):
        self.audio_buffer += audio_chunk
        if len(self.audio_buffer) >= self.BUFFER_SIZE:
            data_to_send = self.audio_buffer
            self.audio_buffer = b""
            retries = 0
            max_retries = 3
            while retries < max_retries:
                try:
                    if self.connection and self._connected:
                        await self.connection.send(data_to_send)
                        return
                    else:
                        await self._connect()
                        return
                except ConnectionClosed:
                    retries += 1
                    wait = 2 ** retries
                    logger.warning(
                        "deepgram.send_failed_reconnecting",
                        attempt=retries,
                        error="connection_closed",
                    )
                    if retries >= max_retries:
                        logger.error("deepgram.send_max_retries_exceeded")
                        return
                    await asyncio.sleep(wait)
                    await self._connect()
                except Exception as e:
                    retries += 1
                    wait = 2 ** retries
                    logger.warning(
                        "deepgram.send_error",
                        attempt=retries,
                        error=str(e),
                    )
                    if retries >= max_retries:
                        logger.error("deepgram.send_max_retries_exceeded")
                        return
                    await asyncio.sleep(wait)
                    await self._connect()

    async def _listen(self):
        try:
            async for message in self.connection:
                if isinstance(message, bytes):
                    continue
                self._handle_message(message)
        except ConnectionClosed as e:
            logger.warning("deepgram.connection_closed", code=e.code, reason=e.reason)
            self._connected = False
            await self._connect()
        except Exception as e:
            logger.error("deepgram.listener_error", error=str(e))
            self._connected = False

    def _handle_message(self, message: str):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.error("deepgram.invalid_json", message=message[:200])
            return

        msg_type = data.get("type")

        if msg_type == "transcript":
            transcript_obj = data.get("channel", {}).get("alternatives", [{}])[0]
            transcript_text = transcript_obj.get("transcript", "")
            confidence = transcript_obj.get("confidence", 0.0)
            language = data.get("channel", {}).get("detected_language", "unknown")
            is_final = data.get("is_final", False)

            if transcript_text.strip():
                self.language_tracking[language] = (
                    self.language_tracking.get(language, 0) + 1
                )

                if is_final:
                    self._total_utterances += 1

                logger.debug(
                    "deepgram.transcript",
                    text=transcript_text,
                    confidence=confidence,
                    language=language,
                    is_final=is_final,
                )

                if self.on_transcript:
                    self.on_transcript(
                        text=transcript_text,
                        confidence=confidence,
                        language=language,
                        is_final=is_final,
                    )

        elif msg_type == "utterance_end":
            logger.debug("deepgram.utterance_end")

        elif msg_type == "error":
            err = data.get("error", {})
            logger.error("deepgram.error_message", error=err)

    def get_dominant_language(self) -> str:
        if not self.language_tracking:
            return "unknown"
        return max(self.language_tracking, key=self.language_tracking.get)

    async def close(self):
        if self.connection and self._connected:
            try:
                await self.connection.close()
            except Exception:
                pass
        self._connected = False
        self.audio_buffer = b""
        logger.info(
            "deepgram.closed",
            total_utterances=self._total_utterances,
            language_distribution=self.language_tracking,
            dominant_language=self.get_dominant_language(),
        )
