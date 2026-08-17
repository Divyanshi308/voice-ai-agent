import asyncio
import json
from typing import AsyncGenerator

import structlog
from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from config import config

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You are a calm, professional multilingual customer support agent.
RULES:
1. Respond in the EXACT language the caller used last
2. Keep responses under 30 words — this is a voice call, not a chat
3. NEVER give medical diagnosis — say 'Please call emergency services'
4. NEVER give legal advice — say 'Let me connect you with a specialist'
5. NEVER give financial advice — say 'I recommend consulting a professional'
6. If you don't know something, say 'I'm not certain, let me find out'
7. Always confirm understanding: repeat what you heard back to caller
8. Be patient and calm — the caller may be stressed or upset
9. Use simple words — the caller may not be a native speaker
10. Never say you are AI — say 'I'm a support assistant'
11. Never make promises about the organization — say 'I'll note that'
12. For emergencies, always say 'Please call [emergency number] immediately'"""


class LLMEngine:
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=config.openai_api_key)
        self.model = config.openai_model
        self.total_tokens_used = 0
        self.total_cost = 0.0
        self._call_tokens = 0

    def _track_tokens(self, prompt_tokens: int, completion_tokens: int) -> None:
        total = prompt_tokens + completion_tokens
        self._call_tokens += total
        self.total_tokens_used += total
        self.total_cost += (prompt_tokens * 0.0025 + completion_tokens * 0.01) / 1000

    async def get_response(
        self, transcript: str, history: list[dict], state: dict
    ) -> AsyncGenerator[str, None]:
        self._call_tokens = 0
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

        messages.extend(history[-10:])

        asr_confidence = state.get("asr_confidence", 1.0)
        if asr_confidence < 0.5:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Audio quality is poor. Ask caller to confirm what they said."
                    ),
                }
            )

        if state.get("same_question_repeated", False):
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Caller seems confused. Try a different approach."
                    ),
                }
            )

        messages.append({"role": "user", "content": transcript})

        retries = 1
        for attempt in range(retries + 1):
            try:
                stream = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=150,
                    temperature=0.7,
                    stream=True,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        yield delta.content
                    if chunk.usage:
                        self._track_tokens(
                            chunk.usage.prompt_tokens,
                            chunk.usage.completion_tokens,
                        )
                logger.info(
                    "llm_response_complete",
                    call_tokens=self._call_tokens,
                    total_tokens=self.total_tokens_used,
                    total_cost=round(self.total_cost, 6),
                )
                return
            except APITimeoutError:
                if attempt < retries:
                    logger.warning("llm_timeout_retrying", attempt=attempt + 1)
                    await asyncio.sleep(2)
                    continue
                logger.error("llm_timeout_fallback")
                yield "One moment please."
                return
            except RateLimitError:
                if attempt < retries:
                    logger.warning("llm_rate_limit_retrying", attempt=attempt + 1)
                    await asyncio.sleep(2)
                    continue
                logger.error("llm_rate_limit_fallback")
                yield "One moment please."
                return
            except APIError as e:
                logger.error("llm_api_error", error=str(e))
                yield "One moment please."
                return

    async def classify_intent(self, transcript: str) -> dict:
        retries = 1
        for attempt in range(retries + 1):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": "Classify the caller's intent.",
                        },
                        {"role": "user", "content": transcript},
                    ],
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": "classify_intent",
                                "description": "Classify the caller's intent and urgency",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "intent": {
                                            "type": "string",
                                            "enum": [
                                                "billing",
                                                "technical",
                                                "complaint",
                                                "general",
                                                "emergency",
                                            ],
                                        },
                                        "urgency": {
                                            "type": "string",
                                            "enum": ["low", "medium", "high"],
                                        },
                                        "confidence": {
                                            "type": "number",
                                            "minimum": 0.0,
                                            "maximum": 1.0,
                                        },
                                    },
                                    "required": ["intent", "urgency", "confidence"],
                                },
                            },
                        }
                    ],
                    tool_choice={"type": "function", "function": {"name": "classify_intent"}},
                    max_tokens=100,
                    temperature=0.3,
                )
                tool_call = response.choices[0].message.tool_calls[0]
                return json.loads(tool_call.function.arguments)
            except (APIError, APITimeoutError, RateLimitError, IndexError, KeyError) as e:
                if attempt < retries:
                    logger.warning("classify_intent_retrying", attempt=attempt + 1)
                    await asyncio.sleep(2)
                    continue
                logger.error("classify_intent_failed", error=str(e))
                return {"intent": "general", "urgency": "medium", "confidence": 0.0}

    async def generate_summary(self, full_transcript: str) -> str:
        retries = 1
        for attempt in range(retries + 1):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Summarize this call in exactly 3 sentences: "
                                "1. What was the caller's main issue? "
                                "2. What information was collected? "
                                "3. What happened at the end (resolved/escalated)?"
                            ),
                        },
                        {"role": "user", "content": full_transcript},
                    ],
                    max_tokens=200,
                    temperature=0.3,
                )
                summary = response.choices[0].message.content or ""
                if response.usage:
                    self._track_tokens(
                        response.usage.prompt_tokens,
                        response.usage.completion_tokens,
                    )
                return summary.strip()
            except (APIError, APITimeoutError, RateLimitError) as e:
                if attempt < retries:
                    logger.warning("generate_summary_retrying", attempt=attempt + 1)
                    await asyncio.sleep(2)
                    continue
                logger.error("generate_summary_failed", error=str(e))
                return "Summary generation failed."

    async def detect_sentiment(self, transcript: str, ai_response: str) -> str:
        retries = 1
        for attempt in range(retries + 1):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Analyze the caller's emotion from their words. "
                                "Return exactly one word: calm, stressed, frustrated, or angry."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"Caller said: {transcript}\nAI responded: {ai_response}",
                        },
                    ],
                    max_tokens=20,
                    temperature=0.3,
                )
                sentiment = (response.choices[0].message.content or "").strip().lower()
                if sentiment in ("calm", "stressed", "frustrated", "angry"):
                    return sentiment
                return "calm"
            except (APIError, APITimeoutError, RateLimitError) as e:
                if attempt < retries:
                    logger.warning("detect_sentiment_retrying", attempt=attempt + 1)
                    await asyncio.sleep(2)
                    continue
                logger.error("detect_sentiment_failed", error=str(e))
                return "calm"
