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


class TestRequest(BaseModel):
    text: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("kataru_started", port=config.port, host=config.host)
    yield
    for session_id in list(active_sessions):
        active_sessions.pop(session_id, None)
    logger.info("kataru_stopped")


app = FastAPI(
    title="Kataru - Elderly Care Voice AI",
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


@app.get("/health")
async def health():
    return {
        "status": "running",
        "uptime": round(time.time() - start_time, 2),
        "agora_configured": bool(config.agora_app_id),
        "openai_configured": bool(config.openai_api_key),
    }


@app.get("/metrics")
async def metrics():
    return {
        "active_sessions": len(active_sessions),
        "total_sessions": len(active_sessions),
        "platform": "Kataru",
    }


@app.post("/test")
async def test_endpoint(req: TestRequest):
    call_id = str(uuid.uuid4())
    text = req.text.lower().strip()

    if not config.openai_api_key or config.openai_api_key.startswith("dummy"):
        if "hello" in text or "hi" in text or "namaste" in text:
            response = "Namaste! I am Kataru, your voice companion. How can I help you today? You can ask about medicines, daily tasks, or just chat."
        elif "medicine" in text or "dawaai" in text or "goli" in text:
            response = "I will remind you to take your medicine after lunch. Would you like me to set a reminder for 3 PM as well?"
        elif "emergency" in text or "help" in text or "bachao" in text:
            response = "This sounds urgent! Please call 112 immediately. I am here with you. Stay calm."
        elif "weather" in text or "mausam" in text:
            response = "Today is a beautiful day! Perfect for a short walk in the garden. Stay active and healthy!"
        elif "name" in text or "naam" in text or "who" in text:
            response = "I am Kataru, which means 'to speak' in Japanese. I am your elderly care voice companion."
        elif "lonely" in text or "akela" in text or "bored" in text:
            response = "I understand. I am here with you. Would you like to talk about your day, or shall I tell you a story?"
        elif "thank" in text or "dhanyavaad" in text:
            response = "You are welcome! I am always here for you. Is there anything else you need?"
        elif "bye" in text or "alvida" in text:
            response = "Goodbye! Take care of yourself. I will be here whenever you need me."
        else:
            response = f"I heard: '{req.text}'. I am here to help with medicines, daily tasks, or just chat. What would you like to talk about?"

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
            "Speak slowly and clearly. Use simple words. Keep responses under 30 words. "
            "Support Hindi, English, and Hinglish. "
            "If someone says they need emergency help, immediately tell them to call 112 or 911."
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

    try:
        while True:
            data = await websocket.receive_json()
            event = data.get("event")

            if event == "chat":
                text = data.get("text", "")
                if not config.openai_api_key or config.openai_api_key.startswith("dummy"):
                    response = f"I heard: '{text}'. This is demo mode."
                else:
                    try:
                        from openai import AsyncOpenAI

                        client = AsyncOpenAI(api_key=config.openai_api_key)
                        response_obj = await client.chat.completions.create(
                            model=config.openai_model,
                            messages=[
                                {"role": "system", "content": "You are Kataru, a caring voice assistant."},
                                {"role": "user", "content": text},
                            ],
                            max_tokens=100,
                        )
                        response = response_obj.choices[0].message.content
                    except:
                        response = "Sorry, I had trouble understanding."

                await websocket.send_json({"event": "response", "text": response})

            elif event == "ping":
                await websocket.send_json({"event": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("ws_error", error=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host=config.host, port=config.port)
