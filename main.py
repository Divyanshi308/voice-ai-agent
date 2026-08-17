import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

from config import config
from database import (
    create_user, authenticate_user, authenticate_oauth,
    get_user, update_user, save_chat_message, get_chat_history,
    save_user_context, get_user_context, delete_chat_session,
    get_user_stats, init_db,
)

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
user_sessions: dict[str, int] = {}
start_time = time.time()


class SignupRequest(BaseModel):
    username: str
    email: str
    password: str
    full_name: str = ""


class LoginRequest(BaseModel):
    identifier: str
    password: str


class OAuthRequest(BaseModel):
    provider: str
    provider_id: str
    email: str
    full_name: str
    avatar_url: str = ""


class ProfileUpdateRequest(BaseModel):
    full_name: str = ""
    language: str = ""
    voice_speed: float = 1.0
    notifications_enabled: bool = True
    sound_enabled: bool = True
    theme: str = "dark"


class ChatRequest(BaseModel):
    text: str
    user_id: int = 0
    session_id: str = ""


class ContextRequest(BaseModel):
    key: str
    value: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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
    }


@app.get("/metrics")
async def metrics():
    return {
        "active_sessions": len(active_sessions),
    }


@app.post("/api/auth/signup")
async def signup(req: SignupRequest):
    if len(req.username) < 3:
        return {"success": False, "error": "Username must be at least 3 characters"}
    if len(req.password) < 6:
        return {"success": False, "error": "Password must be at least 6 characters"}
    if "@" not in req.email:
        return {"success": False, "error": "Invalid email address"}

    result = create_user(
        username=req.username,
        email=req.email,
        password=req.password,
        full_name=req.full_name,
    )
    return result


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    result = authenticate_user(req.identifier, req.password)
    return result


@app.post("/api/auth/oauth")
async def oauth_login(req: OAuthRequest):
    username = req.email.split("@")[0]
    result = authenticate_oauth(
        username=username,
        email=req.email,
        full_name=req.full_name,
        provider=req.provider,
        provider_id=req.provider_id,
    )
    return result


@app.get("/api/user/{user_id}")
async def get_user_profile(user_id: int):
    user = get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    stats = get_user_stats(user_id)
    return {"user": user, "stats": stats}


@app.put("/api/user/{user_id}")
async def update_user_profile(user_id: int, req: ProfileUpdateRequest):
    success = update_user(
        user_id,
        full_name=req.full_name,
        language=req.language,
        voice_speed=req.voice_speed,
        notifications_enabled=int(req.notifications_enabled),
        sound_enabled=int(req.sound_enabled),
        theme=req.theme,
    )
    return {"success": success}


@app.get("/api/user/{user_id}/chats")
async def get_user_chats(user_id: int):
    chats = get_chat_history(user_id)
    return {"chats": chats}


@app.get("/api/user/{user_id}/chats/{session_id}")
async def get_chat_session(user_id: int, session_id: str):
    messages = get_chat_history(user_id, session_id)
    return {"messages": messages}


@app.delete("/api/user/{user_id}/chats/{session_id}")
async def delete_chat(user_id: int, session_id: str):
    deleted = delete_chat_session(user_id, session_id)
    return {"success": deleted}


@app.get("/api/user/{user_id}/context")
async def get_context(user_id: int):
    context = get_user_context(user_id)
    return {"context": context}


@app.post("/api/user/{user_id}/context")
async def save_context(user_id: int, req: ContextRequest):
    save_user_context(user_id, req.key, req.value)
    return {"success": True}


@app.post("/test")
async def test_endpoint(req: ChatRequest):
    call_id = str(uuid.uuid4())
    session_id = req.session_id or call_id
    text = req.text.lower().strip()

    user_context = {}
    if req.user_id:
        user_context = get_user_context(req.user_id)
        save_chat_message(req.user_id, session_id, "user", req.text)

    user_name = user_context.get("name", "")
    user_language = user_context.get("language", "english")

    if not config.openai_api_key or config.openai_api_key.startswith("dummy"):
        if "hello" in text or "hi" in text or "namaste" in text:
            if user_name:
                response = f"Hello {user_name}! It is good to see you again. How can I help you today?"
            else:
                response = "Namaste! I am Kataru, your voice companion. What is your name?"
        elif "my name is" in text or "i am" in text or "mera naam" in text:
            name = req.text.split("is")[-1].strip() if "is" in req.text else req.text.split("am")[-1].strip()
            if req.user_id:
                save_user_context(req.user_id, "name", name)
            response = f"Nice to meet you, {name}! I will remember your name. How can I help you today?"
        elif "medicine" in text or "dawaai" in text:
            if user_name:
                response = f"{user_name}, please take your medicine after lunch. Shall I remind you again at 3 PM?"
            else:
                response = "Please take your medicine after lunch. Would you like me to set a reminder for 3 PM?"
        elif "emergency" in text or "help" in text or "bachao" in text:
            response = "This sounds urgent! Please call 112 immediately. I am here with you. Stay calm."
        elif "weather" in text or "mausam" in text:
            response = "Today is a beautiful day! Perfect for a short walk in the garden."
        elif "who are you" in text or "what are you" in text:
            response = "I am Kataru, which means 'to speak' in Japanese. I am your elderly care voice companion."
        elif "lonely" in text or "akela" in text or "bored" in text:
            if user_name:
                response = f"{user_name}, I understand. I am here with you. Would you like to talk about your day?"
            else:
                response = "I understand. I am here with you. Would you like to talk about your day?"
        elif "thank" in text or "dhanyavaad" in text:
            response = "You are welcome! I am always here for you."
        elif "bye" in text or "alvida" in text:
            response = "Goodbye! Take care of yourself. I will be here whenever you need me."
        elif "remember" in text:
            if user_name:
                response = f"Yes {user_name}, I remember you! You told me your name before."
            else:
                response = "I do not know much about you yet. Tell me your name and I will remember it!"
        else:
            response = f"I heard: '{req.text}'. How can I help you today?"

        if req.user_id:
            save_chat_message(req.user_id, session_id, "ai", response)

        return {
            "input": req.text,
            "response": response,
            "call_id": call_id,
            "session_id": session_id,
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

        if user_name:
            system_prompt += f"\nThe user's name is {user_name}."
        if user_language:
            system_prompt += f"\nPrefer responding in {user_language}."

        messages = [{"role": "system", "content": system_prompt}]
        history = get_chat_history(req.user_id, session_id, limit=10) if req.user_id else []
        for msg in reversed(history):
            role = "user" if msg["message_role"] == "user" else "assistant"
            messages.append({"role": role, "content": msg["message_text"]})
        messages.append({"role": "user", "content": req.text})

        response_obj = await client.chat.completions.create(
            model=config.openai_model,
            messages=messages,
            max_tokens=150,
            temperature=0.7,
        )

        response = response_obj.choices[0].message.content

        if req.user_id:
            save_chat_message(req.user_id, session_id, "ai", response)

        return {
            "input": req.text,
            "response": response,
            "call_id": call_id,
            "session_id": session_id,
            "mode": "live",
        }

    except Exception as e:
        logger.error("test_endpoint_error", error=str(e))
        return {
            "input": req.text,
            "response": "I am sorry, something went wrong. Please try again.",
            "call_id": call_id,
            "session_id": session_id,
            "mode": "error",
        }


if __name__ == "__main__":
    uvicorn.run(app, host=config.host, port=config.port)
