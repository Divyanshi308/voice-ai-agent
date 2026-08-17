import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import structlog
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import config

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging := __import__("logging"), config.log_level.upper())
    ),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

active_sessions: dict[str, dict] = {}
start_time = time.time()


class AgentRequest(BaseModel):
    channel_name: str
    agent_uid: str = "ai-agent"
    remote_uids: list[str] = ["*"]
    greeting: str = ""
    system_prompt: str = ""
    stt_model: str = "nova-3"
    stt_language: str = "multi"
    llm_model: str = "gpt-4o-mini"
    tts_vendor: str = "elevenlabs"
    tts_voice_id: str = ""
    tts_model: str = "eleven_flash_v2_5"
    idle_timeout: int = 300


class TestRequest(BaseModel):
    text: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("voice_ai_agent_started", port=config.port, host=config.host)
    yield
    for session_id in list(active_sessions):
        active_sessions.pop(session_id, None)
    logger.info("voice_ai_agent_stopped")


app = FastAPI(
    title="Kataru (語る) - Elderly Care Voice AI Agent",
    description="Multilingual voice AI for elderly care powered by Agora Conversational AI",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    logger.error("unhandled_exception", path=request.url.path, error=str(exc))
    raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/health")
async def health():
    return {"status": "running", "uptime": round(time.time() - start_time, 2)}


@app.get("/metrics")
async def metrics():
    return {
        "active_sessions": len(active_sessions),
        "total_sessions": len(active_sessions),
        "system": "VoxAssist Elderly Care Agent",
        "platform": "Agora Conversational AI",
    }


@app.post("/agent/start")
async def start_agent(req: AgentRequest):
    try:
        from agora_agent import (
            Agent,
            Agora,
            Area,
            DeepgramSTT,
            OpenAI,
            ElevenLabsTTS,
            expires_in_hours,
        )

        app_id = config.agora_app_id
        app_certificate = config.agora_app_certificate

        if not app_id or not app_certificate:
            return {
                "status": "demo_mode",
                "message": "Agora credentials not configured. Set AGORA_APP_ID and AGORA_APP_CERTIFICATE in .env",
                "agent_id": "demo",
                "channel": req.channel_name,
            }

        client = Agora(
            area=Area.US,
            app_id=app_id,
            app_certificate=app_certificate,
        )

        system_prompt = req.system_prompt or (
            "You are Kataru, a caring multilingual voice assistant for elderly care. "
            "You help with medicine reminders, daily tasks, emergency calls, and companionship. "
            "Speak slowly and clearly. Use simple words. "
            "If someone says they need emergency help, immediately tell them to call 112 or 911. "
            "Always be patient, warm, and respectful. "
            "Switch languages naturally if the user switches."
        )

        greeting = req.greeting or (
            "Namaste! I am Kataru, your voice companion. "
            "How can I help you today? "
            "You can ask me about medicines, daily tasks, or just chat."
        )

        agent = Agent(
            client=client,
            turn_detection={"language": "hi-IN"},
        ).with_stt(
            DeepgramSTT(
                api_key=config.deepgram_api_key or None,
                model=req.stt_model,
                language=req.stt_language,
            )
        ).with_llm(
            OpenAI(
                api_key=config.openai_api_key or None,
                model=req.llm_model,
                system_messages=[{"role": "system", "content": system_prompt}],
                greeting_message=greeting,
                failure_message="I am sorry, I did not understand. Can you please repeat?",
                max_history=50,
                params={
                    "max_tokens": 200,
                    "temperature": 0.7,
                    "top_p": 0.95,
                },
            )
        )

        if req.tts_vendor == "elevenlabs" and config.elevenlabs_api_key:
            agent = agent.with_tts(
                ElevenLabsTTS(
                    key=config.elevenlabs_api_key,
                    model_id=req.tts_model,
                    voice_id=req.tts_voice_id or config.elevenlabs_voice_id,
                    base_url="wss://api.elevenlabs.io/v1",
                )
            )

        session_id = str(uuid.uuid4())
        session = agent.create_session(
            channel=req.channel_name,
            agent_uid=req.agent_uid,
            remote_uids=req.remote_uids,
            name=f"voxassist-{session_id}",
            idle_timeout=req.idle_timeout,
            expires_in=expires_in_hours(1),
            debug=False,
        )

        agent_id = session.start()

        active_sessions[session_id] = {
            "session_id": session_id,
            "agent_id": agent_id,
            "channel": req.channel_name,
            "status": "active",
            "created_at": time.time(),
        }

        logger.info("agent_started", session_id=session_id, agent_id=agent_id)

        return {
            "status": "started",
            "session_id": session_id,
            "agent_id": agent_id,
            "channel": req.channel_name,
        }

    except ImportError as e:
        logger.error("agora_agent_not_installed", error=str(e))
        return {
            "status": "error",
            "message": "agora-agents package not installed. Run: pip install agora-agents",
        }
    except Exception as e:
        logger.error("agent_start_failed", error=str(e))
        return {"status": "error", "message": str(e)}


@app.post("/agent/stop")
async def stop_agent(session_id: str):
    if session_id in active_sessions:
        session_data = active_sessions[session_id]
        try:
            from agora_agent import Agora, Area

            client = Agora(
                area=Area.US,
                app_id=config.agora_app_id,
                app_certificate=config.agora_app_certificate,
            )
            client.agents.stop(agent_id=session_data["agent_id"])
        except Exception as e:
            logger.error("agent_stop_error", error=str(e))

        del active_sessions[session_id]
        return {"status": "stopped", "session_id": session_id}

    return {"status": "not_found", "session_id": session_id}


@app.get("/agent/sessions")
async def list_sessions():
    return {
        "active_sessions": len(active_sessions),
        "sessions": [
            {
                "session_id": s["session_id"],
                "agent_id": s["agent_id"],
                "channel": s["channel"],
                "status": s["status"],
            }
            for s in active_sessions.values()
        ],
    }


@app.post("/test")
async def test_endpoint(req: TestRequest):
    call_id = str(uuid.uuid4())

    if not config.openai_api_key or config.openai_api_key.startswith("dummy"):
        demo_responses = {
            "hello": "Namaste! How can I help you today?",
            "medicine": "Please take your medicine after lunch. I will remind you again at 3 PM.",
            "emergency": "This sounds urgent! Please call 112 immediately.",
            "weather": "Today is a nice day. Perfect for a walk in the garden.",
            "name": "I am VoxAssist, your voice companion.",
        }

        response = demo_responses.get(
            req.text.lower(),
            f"I heard: '{req.text}'. This is a demo mode. Configure API keys for real responses.",
        )

        return {
            "input": req.text,
            "response": response,
            "call_id": call_id,
            "mode": "demo",
        }

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=config.openai_api_key)

        system_prompt = (
            "You are Kataru, a caring multilingual voice assistant for elderly care. "
            "You help with medicine reminders, daily tasks, emergency calls, and companionship. "
            "Speak slowly and clearly. Use simple words. Keep responses under 30 words."
        )

        response = await client.chat.completions.create(
            model=config.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": req.text},
            ],
            max_tokens=150,
            temperature=0.7,
        )

        return {
            "input": req.text,
            "response": response.choices[0].message.content,
            "call_id": call_id,
            "mode": "live",
        }

    except Exception as e:
        logger.error("test_endpoint_error", error=str(e))
        return {
            "input": req.text,
            "response": "I am sorry, something went wrong. Please try again.",
            "call_id": call_id,
            "mode": "error",
        }


@app.websocket("/ws/voice")
async def ws_voice(websocket: WebSocket):
    await websocket.accept()
    session_id = str(uuid.uuid4())

    logger.info("websocket_connected", session_id=session_id)

    try:
        while True:
            data = await websocket.receive_json()
            event = data.get("event")

            if event == "start_agent":
                channel = data.get("channel", f"voxassist-{session_id}")
                response = await start_agent(
                    AgentRequest(
                        channel_name=channel,
                        agent_uid="ai-agent",
                        greeting=data.get("greeting", ""),
                        system_prompt=data.get("system_prompt", ""),
                    )
                )
                await websocket.send_json({"event": "agent_started", **response})

            elif event == "stop_agent":
                await stop_agent(session_id)
                await websocket.send_json({"event": "agent_stopped"})

            elif event == "ping":
                await websocket.send_json({"event": "pong"})

    except WebSocketDisconnect:
        logger.info("websocket_disconnected", session_id=session_id)
        if session_id in active_sessions:
            await stop_agent(session_id)
    except Exception as e:
        logger.error("websocket_error", session_id=session_id, error=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host=config.host, port=config.port)
