from datetime import datetime, timezone
from typing import Optional

import psycopg
import psycopg.rows
import redis.asyncio as redis
import structlog

from config import config

logger = structlog.get_logger(__name__)

CALLS_TABLE = """\
CREATE TABLE IF NOT EXISTS calls (
    id SERIAL PRIMARY KEY,
    call_id UUID UNIQUE NOT NULL,
    caller_id VARCHAR(50),
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    duration_seconds INTEGER,
    languages_used TEXT[],
    primary_language VARCHAR(10),
    intent VARCHAR(50),
    intent_confidence FLOAT,
    asr_confidence_avg FLOAT,
    sentiment VARCHAR(20),
    escalated BOOLEAN DEFAULT FALSE,
    escalation_reason VARCHAR(100),
    turns_count INTEGER,
    ticket_id VARCHAR(50),
    summary TEXT,
    guardrail_triggers INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);"""

GUARDRAILS_TABLE = """\
CREATE TABLE IF NOT EXISTS guardrail_triggers (
    id SERIAL PRIMARY KEY,
    call_id UUID NOT NULL,
    trigger_type VARCHAR(50) NOT NULL,
    trigger_text TEXT,
    action_taken VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);"""


class AnalyticsLogger:

    def __init__(self) -> None:
        self._pg_pool: Optional[psycopg.AsyncConnection] = None
        self._redis: Optional[redis.Redis] = None
        self._pg_ready = False
        self._redis_ready = False

    async def connect(self) -> None:
        try:
            self._pg_pool = await psycopg.AsyncConnection.connect(
                config.database_url,
                autocommit=True,
                row_factory=psycopg.rows.DictRow,
            )
            await self._pg_pool.execute(CALLS_TABLE)
            await self._pg_pool.execute(GUARDRAILS_TABLE)
            self._pg_ready = True
            logger.info("analytics_pg_connected")
        except Exception as exc:
            logger.error("analytics_pg_connect_failed", error=str(exc))
            self._pg_ready = False

        try:
            self._redis = redis.from_url(
                config.redis_url, decode_responses=True
            )
            await self._redis.ping()
            self._redis_ready = True
            logger.info("analytics_redis_connected")
        except Exception as exc:
            logger.error("analytics_redis_connect_failed", error=str(exc))
            self._redis_ready = False

    async def close(self) -> None:
        if self._pg_pool:
            try:
                await self._pg_pool.close()
            except Exception:
                pass
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
        logger.info("analytics_connections_closed")

    async def log_call_start(self, call_id: str, caller_id: str) -> None:
        now = datetime.now(timezone.utc)

        if self._pg_ready:
            try:
                await self._pg_pool.execute(
                    """\
                    INSERT INTO calls (call_id, caller_id, start_time)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (call_id) DO UPDATE SET
                        caller_id = EXCLUDED.caller_id,
                        start_time = EXCLUDED.start_time
                    """,
                    (call_id, caller_id, now),
                )
            except Exception as exc:
                logger.error(
                    "analytics_log_start_failed",
                    call_id=call_id,
                    error=str(exc),
                )

        if self._redis_ready:
            try:
                await self._redis.incr("active_calls")
            except Exception as exc:
                logger.error(
                    "analytics_redis_incr_failed",
                    key="active_calls",
                    call_id=call_id,
                    error=str(exc),
                )

        logger.info(
            "call_started",
            call_id=call_id,
            caller_id=caller_id,
        )

    async def log_call_end(
        self,
        call_id: str,
        state: dict,
        ticket_id: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc)

        start_time = state.get("timestamp_start")
        duration_seconds = 0
        if start_time:
            try:
                if isinstance(start_time, str):
                    start_dt = datetime.fromisoformat(start_time)
                else:
                    start_dt = start_time
                duration_seconds = int((now - start_dt).total_seconds())
            except (ValueError, TypeError):
                duration_seconds = 0

        languages_used = state.get("languages_used", [])
        primary_language = state.get("current_language", "en")
        intent = state.get("intent")
        intent_confidence = state.get("intent_confidence", 0.0)
        asr_confidence_avg = state.get("asr_confidence_avg", 0.0)
        sentiment = state.get("sentiment", "neutral")
        escalated = state.get("escalation_triggered", False)
        escalation_reason = state.get("escalation_reason")
        turns_count = state.get("turn_count", 0)
        summary = state.get("summary", "")

        if self._pg_ready:
            try:
                await self._pg_pool.execute(
                    """\
                    UPDATE calls SET
                        end_time = %s,
                        duration_seconds = %s,
                        languages_used = %s,
                        primary_language = %s,
                        intent = %s,
                        intent_confidence = %s,
                        asr_confidence_avg = %s,
                        sentiment = %s,
                        escalated = %s,
                        escalation_reason = %s,
                        turns_count = %s,
                        ticket_id = %s,
                        summary = %s
                    WHERE call_id = %s
                    """,
                    (
                        now,
                        duration_seconds,
                        languages_used,
                        primary_language,
                        intent,
                        intent_confidence,
                        asr_confidence_avg,
                        sentiment,
                        escalated,
                        escalation_reason,
                        turns_count,
                        ticket_id,
                        summary,
                        call_id,
                    ),
                )
            except Exception as exc:
                logger.error(
                    "analytics_log_end_failed",
                    call_id=call_id,
                    error=str(exc),
                )

        if self._redis_ready:
            try:
                pipe = self._redis.pipeline()
                pipe.decr("active_calls")
                pipe.incr("total_calls_today")
                await pipe.execute()
            except Exception as exc:
                logger.error(
                    "analytics_redis_counters_failed",
                    call_id=call_id,
                    error=str(exc),
                )

        logger.info(
            "call_ended",
            call_id=call_id,
            duration_seconds=duration_seconds,
            escalated=escalated,
            ticket_id=ticket_id,
        )

    async def log_guardrail_trigger(
        self,
        call_id: str,
        trigger_type: str,
        text: str,
        action: str,
    ) -> None:
        if self._pg_ready:
            try:
                await self._pg_pool.execute(
                    """\
                    INSERT INTO guardrail_triggers
                        (call_id, trigger_type, trigger_text, action_taken)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (call_id, trigger_type, text, action),
                )
                await self._pg_pool.execute(
                    """\
                    UPDATE calls
                    SET guardrail_triggers = guardrail_triggers + 1
                    WHERE call_id = %s
                    """,
                    (call_id,),
                )
            except Exception as exc:
                logger.error(
                    "analytics_guardrail_log_failed",
                    call_id=call_id,
                    trigger_type=trigger_type,
                    error=str(exc),
                )

        if self._redis_ready:
            try:
                await self._redis.incr(f"guardrail_triggers_{trigger_type}")
            except Exception as exc:
                logger.error(
                    "analytics_redis_guardrail_incr_failed",
                    call_id=call_id,
                    trigger_type=trigger_type,
                    error=str(exc),
                )

        logger.info(
            "guardrail_triggered",
            call_id=call_id,
            trigger_type=trigger_type,
            action=action,
        )

    async def get_metrics(self) -> dict:
        metrics = {
            "total_calls_today": 0,
            "active_calls": 0,
            "avg_confidence": 0.0,
            "escalation_rate": 0.0,
            "guardrail_triggers": 0,
        }

        if self._redis_ready:
            try:
                metrics["total_calls_today"] = int(
                    await self._redis.get("total_calls_today") or 0
                )
                metrics["active_calls"] = int(
                    await self._redis.get("active_calls") or 0
                )
            except Exception as exc:
                logger.error("analytics_redis_metrics_failed", error=str(exc))

        if self._pg_ready:
            try:
                row = await self._pg_pool.fetchrow(
                    """\
                    SELECT
                        COALESCE(AVG(intent_confidence), 0.0) AS avg_conf,
                        CASE
                            WHEN COUNT(*) = 0 THEN 0.0
                            ELSE COUNT(*) FILTER (WHERE escalated)::float / COUNT(*)::float
                        END AS esc_rate,
                        COALESCE(SUM(guardrail_triggers), 0)::int AS total_triggers
                    FROM calls
                    WHERE created_at >= CURRENT_DATE
                    """
                )
                if row:
                    metrics["avg_confidence"] = round(float(row["avg_conf"]), 4)
                    metrics["escalation_rate"] = round(float(row["esc_rate"]), 4)
                    metrics["guardrail_triggers"] = int(row["total_triggers"])
            except Exception as exc:
                logger.error("analytics_pg_metrics_failed", error=str(exc))

        return metrics
