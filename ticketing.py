import asyncio
import json
import logging
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

MAX_REQUESTS_PER_MINUTE = 400
RETRY_ATTEMPTS = 1
RETRY_DELAY_BASE = 2


class TicketingManager:
    def __init__(self, subdomain: str, email: str, api_key: str) -> None:
        self.base_url = f"https://{subdomain}.zendesk.com/api/v2"
        self.auth = (email, api_key)
        self.client = httpx.AsyncClient(
            auth=self.auth,
            timeout=30.0,
            headers={"Content-Type": "application/json"},
        )
        self._request_timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def _rate_limit(self) -> None:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - 60.0
            self._request_timestamps = [t for t in self._request_timestamps if t > cutoff]
            if len(self._request_timestamps) >= MAX_REQUESTS_PER_MINUTE:
                wait_until = self._request_timestamps[0] + 60.0
                wait_time = wait_until - now
                if wait_time > 0:
                    logger.warning("Rate limit reached, sleeping %.2fs", wait_time)
                    await asyncio.sleep(wait_time)
            self._request_timestamps.append(time.monotonic())

    def _get_priority(self, packet: dict[str, Any]) -> str:
        if packet.get("escalation_reason"):
            return "high"
        confidence = packet.get("confidence", 1.0)
        if isinstance(confidence, (int, float)) and confidence < 0.5:
            return "high"
        if packet.get("urgency") == "high":
            return "high"
        return "normal"

    async def create_ticket(
        self,
        summary: str,
        handoff_packet: dict[str, Any],
        full_transcript: str,
    ) -> Optional[str]:
        collected_data = handoff_packet.get("collected_data", {})
        language = handoff_packet.get("language", "unknown")
        intent = handoff_packet.get("intent", "unknown")
        caller_id = handoff_packet.get("caller_id", "unknown")
        escalated = handoff_packet.get("escalation_reason") is not None

        tags = ["ai-agent", language, intent]
        if escalated:
            tags.append("escalated")

        ticket_body = {
            "ticket": {
                "subject": f"[AI Agent] {intent} - {caller_id}",
                "description": summary,
                "priority": self._get_priority(handoff_packet),
                "type": "incident",
                "tags": tags,
                "comment": {
                    "body": (
                        f"FULL TRANSCRIPT:\n\n{full_transcript}\n\n\n"
                        f"COLLECTED DATA:\n{json.dumps(collected_data, indent=2)}"
                    )
                },
            }
        }

        last_error: Optional[Exception] = None

        for attempt in range(RETRY_ATTEMPTS + 1):
            await self._rate_limit()

            try:
                resp = await self.client.post(
                    f"{self.base_url}/tickets.json",
                    json=ticket_body,
                )

                if resp.status_code == 201:
                    data = resp.json()
                    ticket_id = data["ticket"]["id"]
                    logger.info("Created ticket %s", ticket_id)
                    return str(ticket_id)

                logger.warning(
                    "Ticket creation returned %d: %s",
                    resp.status_code,
                    resp.text,
                )
                last_error = RuntimeError(
                    f"Zendesk returned {resp.status_code}: {resp.text}"
                )

            except httpx.HTTPError as exc:
                logger.warning("HTTP error on attempt %d: %s", attempt + 1, exc)
                last_error = exc

            if attempt < RETRY_ATTEMPTS:
                delay = RETRY_DELAY_BASE * (2 ** attempt)
                logger.info("Retrying in %ds", delay)
                await asyncio.sleep(delay)

        logger.error("Failed to create ticket after %d attempts: %s", RETRY_ATTEMPTS + 1, last_error)
        return None

    async def add_internal_note(self, ticket_id: str, note_text: str) -> bool:
        payload = {
            "ticket": {
                "comment": {"body": note_text, "public": False}
            }
        }

        last_error: Optional[Exception] = None

        for attempt in range(RETRY_ATTEMPTS + 1):
            await self._rate_limit()

            try:
                resp = await self.client.put(
                    f"{self.base_url}/tickets/{ticket_id}.json",
                    json=payload,
                )

                if resp.status_code == 200:
                    logger.info("Added internal note to ticket %s", ticket_id)
                    return True

                logger.warning(
                    "Add note returned %d: %s",
                    resp.status_code,
                    resp.text,
                )
                last_error = RuntimeError(
                    f"Zendesk returned {resp.status_code}: {resp.text}"
                )

            except httpx.HTTPError as exc:
                logger.warning("HTTP error on attempt %d: %s", attempt + 1, exc)
                last_error = exc

            if attempt < RETRY_ATTEMPTS:
                delay = RETRY_DELAY_BASE * (2 ** attempt)
                logger.info("Retrying in %ds", delay)
                await asyncio.sleep(delay)

        logger.error(
            "Failed to add note to ticket %s after %d attempts: %s",
            ticket_id,
            RETRY_ATTEMPTS + 1,
            last_error,
        )
        return False

    async def close(self) -> None:
        await self.client.aclose()
