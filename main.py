import asyncio
import base64
import json
import os
import time
import uuid
import random
from contextlib import asynccontextmanager
from typing import Optional

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import config
from database import (
    create_user, authenticate_user, authenticate_oauth,
    get_user, update_user, save_chat_message, get_chat_history,
    save_user_context, get_user_context, delete_chat_session,
    get_user_stats, init_db,
)
from voice_pipeline import voice_pipeline, ConversationState
from agora_agent import agora_agent
from research import research_engine

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

    @property
    def uid(self) -> int:
        return int(self.user_id) if self.user_id else 0


class ContextRequest(BaseModel):
    key: str
    value: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("kataru_started", port=config.port, host=config.host)
    yield
    logger.info("kataru_stopped")


app = FastAPI(title="Kataru - Voice AI Agent", lifespan=lifespan)

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
    return {"status": "running", "uptime": round(time.time() - start_time, 2)}


@app.get("/metrics")
async def metrics():
    return {"active_sessions": len(active_sessions)}


@app.post("/api/auth/signup")
async def signup(req: SignupRequest):
    if len(req.username) < 3:
        return {"success": False, "error": "Username must be at least 3 characters"}
    if len(req.password) < 6:
        return {"success": False, "error": "Password must be at least 6 characters"}
    if "@" not in req.email:
        return {"success": False, "error": "Invalid email address"}
    return create_user(req.username, req.email, req.password, req.full_name)


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    return authenticate_user(req.identifier, req.password)


@app.post("/api/auth/oauth")
async def oauth_login(req: OAuthRequest):
    username = req.email.split("@")[0]
    return authenticate_oauth(username, req.email, req.full_name, req.provider, req.provider_id)


@app.get("/api/user/{user_id}")
async def get_user_profile(user_id: int):
    user = get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user": user, "stats": get_user_stats(user_id)}


@app.put("/api/user/{user_id}")
async def update_user_profile(user_id: int, req: ProfileUpdateRequest):
    return {"success": update_user(user_id, full_name=req.full_name, language=req.language,
            voice_speed=req.voice_speed, notifications_enabled=int(req.notifications_enabled),
            sound_enabled=int(req.sound_enabled), theme=req.theme)}


@app.get("/api/user/{user_id}/chats")
async def get_user_chats(user_id: int):
    return {"chats": get_chat_history(user_id)}


@app.get("/api/user/{user_id}/chats/{session_id}")
async def get_chat_session(user_id: int, session_id: str):
    return {"messages": get_chat_history(user_id, session_id)}


@app.delete("/api/user/{user_id}/chats/{session_id}")
async def delete_chat(user_id: int, session_id: str):
    return {"success": delete_chat_session(user_id, session_id)}


@app.get("/api/user/{user_id}/context")
async def get_context(user_id: int):
    return {"context": get_user_context(user_id)}


@app.post("/api/user/{user_id}/context")
async def save_context(user_id: int, req: ContextRequest):
    save_user_context(user_id, req.key, req.value)
    return {"success": True}


class CustomerServiceAgent:
    def __init__(self):
        self.conversation_stages = {
            "greeting": 0,
            "name_collection": 1,
            "issue_identification": 2,
            "details_collection": 3,
            "confirmation": 4,
            "resolution": 5,
            "escalation": 6,
        }

        self.collected_info = {}

        self.hindi_responses = {
            "greeting": [
                "Namaste! Main Kataru hoon, aapki voice assistant. Aapki kya madad kar sakti hoon?",
                "Namaste! Main Kataru se baat kar rahi hoon. Bataiye, kya problem hai?",
                "Namaste! Aapki seva mein haazir hoon. Kya madad chahiye?",
            ],
            "ask_name": [
                "Aapka naam kya hai?",
                "Mein aapko kaise bulaun? Aapka naam bataiye.",
                "Pehle mujhe aapka naam bata dijiye.",
            ],
            "ask_issue": [
                "Aapko kya problem hai? Dhire se bataiye.",
                "Bataiye kya ho raha hai? Main sun rahi hoon.",
                "Aapki kya pareshaani hai?",
            ],
            "ask_details": [
                "Aur detail mein bataiye.",
                "Kya aur kuch hai jo mujhe batana chahiye?",
                "Theek hai, aur kya?",
            ],
            "confirm": [
                "Toh main samjhi, aapki problem yeh hai: {summary}. Sahi hai?",
                "Kya yeh sahi hai: {summary}?",
                "Maine yeh samjha: {summary}. Correct hai?",
            ],
            "escalation": [
                "Main aapko human agent se connect karti hoon. Ek minute please.",
                "Aapki baat ke liye mujhe expert se baat karni padegi. Rukiye.",
                "Main aapko specialist ke paas bhej rahi hoon.",
            ],
            "farewell": [
                "Dhanyavaad! Kisi aur cheez ki zaroorat ho toh bataiye.",
                "Theek hai, aur kuch ho toh zaroor bataiye.",
                "Achha, apna khayal rakhiye!",
            ],
        }

        self.english_responses = {
            "greeting": [
                "Hello! I am Kataru, your voice assistant. How can I help you today?",
                "Hi there! I am Kataru. What can I do for you?",
                "Welcome! I am here to help. What do you need?",
            ],
            "ask_name": [
                "What is your name?",
                "May I know your name?",
                "Please tell me your name.",
            ],
            "ask_issue": [
                "What problem are you facing? Please explain.",
                "Tell me what is happening. I am listening.",
                "What issue would you like help with?",
            ],
            "ask_details": [
                "Can you provide more details?",
                "Is there anything else I should know?",
                "Please tell me more.",
            ],
            "confirm": [
                "So I understand your issue is: {summary}. Is that correct?",
                "Let me confirm: {summary}. Right?",
                "I heard: {summary}. Is this accurate?",
            ],
            "escalation": [
                "I will connect you with a human agent. One moment please.",
                "Let me transfer you to a specialist who can help better.",
                "I am connecting you with an expert now.",
            ],
            "farewell": [
                "Thank you! Let me know if you need anything else.",
                "Is there anything else I can help with?",
                "Take care! I am here if you need me.",
            ],
        }

        self.hinglish_responses = {
            "greeting": [
                "Namaste! Main Kataru hoon, aapki assistant. Kya help chahiye?",
                "Hi! Main Kataru se baat kar rahi hoon. Batao kya problem hai?",
                "Hello! Main yahan hoon aapki help ke liye. Bolo kya ho raha hai?",
            ],
            "ask_name": [
                "Tumhara naam kya hai?",
                "Naam bata do please.",
                "Kaise bulaun tumhe?",
            ],
            "ask_issue": [
                "Kya ho raha hai? Batao.",
                "Problem kya hai? Dhire se bolo.",
                "Batao kya issue hai.",
            ],
            "ask_details": [
                "Aur batao.",
                "Kya aur hai jo batana chahiye?",
                "Theek hai, aur kuch?",
            ],
            "confirm": [
                "Toh samjhi, problem yeh hai: {summary}. Sahi hai?",
                "Yeh sahi hai: {summary}?",
                "Maine samjha: {summary}. Correct?",
            ],
            "escalation": [
                "Main human se connect karti hoon. Ek minute.",
                "Ruko, specialist se baat karwati hoon.",
                "Expert ke paas bhej rahi hoon.",
            ],
            "farewell": [
                "Thanks! Aur kuch ho toh batao.",
                "Theek hai, apna khayal rakhna.",
                "Bye! Zaroorat pade toh batao.",
            ],
        }

    def detect_language(self, text: str) -> str:
        text_lower = text.lower()
        hindi_words = ["namaste", "kya", "hai", "hain", "mera", "meri", "aap", "aapka", "bataiye",
                       "bolo", "samjhi", "dhanyavaad", "madad", "problem", "pareshaani", "dawaai",
                       "dawai", "doctor", "hospital", "ambulance", "bachao", "help", "please",
                       "rukiye", "ek", "minute", "theek", "hai", "nahi", "haan", "ji", "nahin",
                       "karo", "karna", "chahiye", "zaroorat", "seva", "haazir", "sun", "rahi",
                       "hoon", "raha", "hain", "main", "tum", "woh", "yeh", "woh", "kaise",
                       "kaun", "kab", "kahan", "kyun", "ka", "ki", "ke", "ko", "se", "mein",
                       "par", "aur", "ya", "toh", "phir", "lekin", "agar", "bhi", "sirf",
                       "abhi", "kal", "aaj", "kal", "subah", "shaam", "raat", "din"]

        hindi_count = sum(1 for word in hindi_words if word in text_lower)

        if hindi_count >= 2:
            return "hindi"
        elif any(c in text for c in "अआइईउऊएऐओऔकखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसह"):
            return "hindi"
        else:
            return "english"

    def get_response(self, text: str, language: str = "auto") -> tuple[str, str]:
        import re
        text_lower = text.lower().strip()

        words = set(re.findall(r'\w+', text_lower))

        if language == "auto":
            detected_lang = self.detect_language(text)
        else:
            detected_lang = language

        if detected_lang == "hindi":
            responses = self.hindi_responses
        elif detected_lang == "hinglish":
            responses = self.hinglish_responses
        else:
            responses = self.english_responses

        emergency_words = {"emergency", "bachao", "ambulance", "112", "911", "urgent"}
        if words & emergency_words or "help me" in text_lower or "madad karo" in text_lower:
            if detected_lang == "hindi":
                return ("emergency", "Yeh emergency lag raha hai! Please turant 112 par call karein. "
                        "Main yahan hoon. Please shaant rahein.")
            else:
                return ("emergency", "This sounds like an emergency! Please call 112 immediately. "
                        "I am here with you. Please stay calm.")

        medical_words = {"doctor", "hospital", "sick", "illness", "bimar", "dawaai", "medicine", "medication", "fever", "pain"}
        if words & medical_words:
            if detected_lang == "hindi":
                return ("medical", "Main medical advice nahi de sakti. Please apne doctor se baat karein "
                        "ya 112 par call karein agar emergency hai.")
            else:
                return ("medical", "I cannot provide medical advice. Please consult your doctor "
                        "or call 112 if this is an emergency.")

        legal_words = {"legal", "court", "lawyer", "sue", "lawsuit"}
        if words & legal_words:
            if detected_lang == "hindi":
                return ("legal", "Main legal advice nahi de sakti. Please ek lawyer se baat karein.")
            else:
                return ("legal", "I cannot provide legal advice. Please consult with a lawyer.")

        financial_words = {"finance", "invest", "loan", "interest", "trading", "stock"}
        if words & financial_words:
            if detected_lang == "hindi":
                return ("financial", "Main financial advice nahi de sakti. Please ek financial advisor se baat karein.")
            else:
                return ("financial", "I cannot provide financial advice. Please consult with a financial advisor.")

        greeting_words = {"hello", "hi", "hey", "namaste", "namaskar", "good morning", "good evening"}
        if words & greeting_words or text_lower in ["hello", "hi", "hey", "namaste"]:
            if detected_lang == "hindi":
                return ("greeting", random.choice(responses["greeting"]))
            else:
                return ("greeting", random.choice(responses["greeting"]))

        name_words = {"naam", "who are you", "kaun ho", "tumhara naam"}
        if words & name_words or "my name" in text_lower or "i am" in text_lower or "mera naam" in text_lower:
            if "my name is" in text_lower or "mera naam" in text_lower or "i am" in text_lower:
                parts = text_lower.replace("my name is", "").replace("mera naam hai", "").replace("mera naam", "").replace("i am", "").strip()
                name = parts.title() if parts else ""
                if name:
                    if detected_lang == "hindi":
                        return ("greeting", f"Namaste {name}! Aapki kya madad kar sakti hoon?")
                    else:
                        return ("greeting", f"Hello {name}! How can I help you today?")
            if detected_lang == "hindi":
                return ("ask_name", random.choice(responses["ask_name"]))
            else:
                return ("ask_name", random.choice(responses["ask_name"]))

        issue_words = {"problem", "issue", "complaint", "pareshaani", "grievance", "help"}
        if words & issue_words:
            if detected_lang == "hindi":
                return ("ask_issue", random.choice(responses["ask_issue"]))
            else:
                return ("ask_issue", random.choice(responses["ask_issue"]))

        thanks_words = {"thank", "dhanyavaad", "shukriya", "thanks"}
        if words & thanks_words:
            if detected_lang == "hindi":
                return ("farewell", random.choice(responses["farewell"]))
            else:
                return ("farewell", random.choice(responses["farewell"]))

        bye_words = {"bye", "alvida", "goodbye", "goodnight"}
        if words & bye_words:
            if detected_lang == "hindi":
                return ("farewell", "Alvida! Apna khayal rakhiye. Zaroorat ho toh wapas aaiye.")
            else:
                return ("farewell", "Goodbye! Take care. Come back if you need help.")

        if "bill" in words or "payment" in words:
            if detected_lang == "hindi":
                return ("billing", "Aapka bill payment karna hai? Bataiye kis cheez ka bill hai aur kitna amount hai.")
            else:
                return ("billing", "You want to pay a bill? Please tell me what the bill is for and the amount.")

        if "account" in words:
            if detected_lang == "hindi":
                return ("account", "Aapka account number kya hai? Please bataiye.")
            else:
                return ("account", "What is your account number? Please provide it.")

        if "address" in words or "pata" in words:
            if detected_lang == "hindi":
                return ("address", "Aapka address kya hai? Please pura address bataiye.")
            else:
                return ("address", "What is your address? Please provide your full address.")

        if "phone" in words or "number" in words or "contact" in words:
            if detected_lang == "hindi":
                return ("contact", "Aapka phone number kya hai? Please bataiye.")
            else:
                return ("contact", "What is your phone number? Please provide it.")

        if "date" in words or "kab" in words or "when" in words:
            if detected_lang == "hindi":
                return ("date", "Kab hua yeh? Date bataiye.")
            else:
                return ("date", "When did this happen? Please provide the date.")

        if words & {"yes", "haan", "ji", "correct", "sahi", "theek"}:
            if detected_lang == "hindi":
                return ("confirm", "Bahut achha! Koi aur cheez hai jo mujhe batani chahiye?")
            else:
                return ("confirm", "Great! Is there anything else you need to tell me?")

        if words & {"no", "nahi", "nahin", "bas"}:
            if detected_lang == "hindi":
                return ("resolution", "Theek hai! Main aapki help kar deti hoon. Ek minute please.")
            else:
                return ("resolution", "Alright! Let me help you with that. One moment please.")

        escalate_words = {"transfer", "human", "agent", "person", "representative", "specialist"}
        if words & escalate_words:
            if detected_lang == "hindi":
                return ("escalation", random.choice(responses["escalation"]))
            else:
                return ("escalation", random.choice(responses["escalation"]))

        if "sorry" in words or "maaf" in words or "pata nahi" in text_lower:
            if detected_lang == "hindi":
                return ("ask_details", "Koi baat nahi. Please aur detail mein bataiye, main samajhne ki koshish karungi.")
            else:
                return ("ask_details", "No problem. Please provide more details, I will try to understand.")

        if "remember" in words or "yaad" in words:
            if detected_lang == "hindi":
                return ("general", "Main aapki baatein yaad rakh sakti hoon. Bataiye kya yaad rakhna hai?")
            else:
                return ("general", "I can remember things for you. What would you like me to remember?")

        if "lonely" in words or "akela" in words or "bored" in words:
            if detected_lang == "hindi":
                return ("general", "Main hoon na aapke saath! Baat karte hain. Aapka din kaisa guzra?")
            else:
                return ("general", "I am here with you! Let us talk. How was your day?")

        if len(text.split()) < 3:
            if detected_lang == "hindi":
                return ("ask_details", random.choice(responses["ask_details"]))
            else:
                return ("ask_details", random.choice(responses["ask_details"]))

        if detected_lang == "hindi":
            return ("general", "Main samajh gayi. Please aur detail mein bataiye taaki main aapki behtar madad kar sakoony.")
        else:
            return ("general", "I understand. Please provide more details so I can help you better.")


agent = CustomerServiceAgent()


@app.post("/test")
async def test_endpoint(req: ChatRequest):
    call_id = str(uuid.uuid4())
    session_id = req.session_id or call_id
    text = req.text.strip()

    user_id = 0
    try:
        user_id = int(req.user_id) if req.user_id else 0
    except (ValueError, TypeError):
        user_id = 0

    user_context = {}
    if user_id > 0:
        try:
            user_context = get_user_context(user_id)
            save_chat_message(user_id, session_id, "user", req.text)
        except Exception:
            pass

    user_name = user_context.get("name", "")

    if not config.openai_api_key or config.openai_api_key.startswith("dummy"):
        research_result = await research_engine.research(text)
        response = research_result["answer"]

        if user_id > 0:
            try:
                save_chat_message(user_id, session_id, "user", text)
                save_chat_message(user_id, session_id, "ai", response)
            except Exception:
                pass

        return {
            "input": req.text,
            "response": response,
            "call_id": call_id,
            "session_id": session_id,
            "mode": "research",
            "sources": research_result.get("sources", []),
        }

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=config.openai_api_key)

        system_prompt = (
            "You are Kataru, a multilingual customer support voice AI agent for elderly care. "
            "You help with general inquiries, information collection, and companionship. "
            "RULES:\n"
            "1. Respond in the EXACT language the user used (Hindi, English, or Hinglish)\n"
            "2. Keep responses under 30 words - this is a voice call\n"
            "3. NEVER provide medical diagnosis - say 'Please consult your doctor'\n"
            "4. NEVER provide legal advice - say 'Please consult a lawyer'\n"
            "5. NEVER provide financial advice - say 'Please consult a financial advisor'\n"
            "6. For emergencies, say 'Please call 112 immediately'\n"
            "7. Collect information: name, issue, details, date, address, phone\n"
            "8. Confirm understanding by repeating back\n"
            "9. Be calm, patient, and respectful\n"
            "10. Use simple words\n"
            "11. Never say you are AI - say 'I am a support assistant'\n"
            "12. If confidence is low, offer to transfer to human agent"
        )

        if user_name:
            system_prompt += f"\n\nThe user's name is {user_name}. Use it in conversation."

        messages = [{"role": "system", "content": system_prompt}]
        if user_id > 0:
            try:
                history = get_chat_history(user_id, session_id, limit=10)
                for msg in reversed(history):
                    role = "user" if msg["message_role"] == "user" else "assistant"
                    messages.append({"role": role, "content": msg["message_text"]})
            except Exception:
                pass
        messages.append({"role": "user", "content": req.text})

        response_obj = await client.chat.completions.create(
            model=config.openai_model,
            messages=messages,
            max_tokens=150,
            temperature=0.7,
        )

        response = response_obj.choices[0].message.content

        if user_id > 0:
            try:
                save_chat_message(user_id, session_id, "ai", response)
            except Exception:
                pass

        return {
            "input": req.text,
            "response": response,
            "call_id": call_id,
            "session_id": session_id,
            "mode": "live",
        }

    except Exception as e:
        logger.error("test_endpoint_error", error=str(e))
        intent, fallback_response = agent.get_response(text)
        return {
            "input": req.text,
            "response": fallback_response,
            "call_id": call_id,
            "session_id": session_id,
            "mode": "fallback",
        }


class VoiceSessionRequest(BaseModel):
    session_id: str = ""
    user_id: int = 0
    language: str = "auto"


class VoiceTextInput(BaseModel):
    session_id: str
    text: str
    user_id: int = 0


@app.post("/api/voice/session")
async def create_voice_session(req: VoiceSessionRequest):
    session_id = req.session_id or str(uuid.uuid4())
    session = voice_pipeline.get_or_create_session(session_id, req.user_id)
    session.language = req.language
    return {
        "success": True,
        "session_id": session_id,
        "state": session.state.value,
        "language": session.language,
    }


@app.post("/api/voice/text")
async def voice_text_input(req: VoiceTextInput):
    session = voice_pipeline.get_or_create_session(req.session_id, req.user_id)
    result = await voice_pipeline.process_text_input(session, req.text)

    if req.user_id > 0:
        try:
            save_chat_message(req.user_id, req.session_id, "user", req.text)
            save_chat_message(req.user_id, req.session_id, "ai", result["response"])
        except Exception:
            pass

    return result


@app.post("/api/voice/interrupt/{session_id}")
async def voice_interrupt(session_id: str):
    session = voice_pipeline.get_or_create_session(session_id)
    result = voice_pipeline.handle_interruption(session)
    return result


@app.post("/api/voice/end/{session_id}")
async def voice_end(session_id: str):
    result = voice_pipeline.end_session(session_id)
    return result


@app.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket):
    await websocket.accept()

    session_id = None
    session = None

    try:
        while True:
            data = await websocket.receive()

            if data["type"] == "websocket.receive":
                if "text" in data:
                    message = json.loads(data["text"])

                    if message.get("type") == "init":
                        session_id = message.get("session_id", str(uuid.uuid4()))
                        user_id = message.get("user_id", 0)
                        language = message.get("language", "auto")
                        session = voice_pipeline.get_or_create_session(session_id, user_id)
                        session.language = language

                        await websocket.send_json({
                            "type": "ready",
                            "session_id": session_id,
                            "state": "listening",
                            "language": language,
                        })

                    elif message.get("type") == "text":
                        if session:
                            text = message.get("text", "")
                            if text:
                                result = await voice_pipeline.process_text_input(session, text)
                                await websocket.send_json(result)

                                if session.user_id > 0:
                                    try:
                                        save_chat_message(session.user_id, session_id, "user", text)
                                        save_chat_message(session.user_id, session_id, "ai", result["response"])
                                    except Exception:
                                        pass

                    elif message.get("type") == "interrupt":
                        if session:
                            result = voice_pipeline.handle_interruption(session)
                            await websocket.send_json(result)

                    elif message.get("type") == "end":
                        if session:
                            result = voice_pipeline.end_session(session_id)
                            await websocket.send_json(result)
                        break

                elif "bytes" in data:
                    if session:
                        audio_data = data["bytes"]
                        is_final = message.get("is_final", False) if "message" in data else False

                        result = await voice_pipeline.process_audio_chunk(session, audio_data, is_final)
                        await websocket.send_json(result)

    except WebSocketDisconnect:
        if session_id:
            voice_pipeline.end_session(session_id)
    except Exception as e:
        logger.error("voice_websocket_error", error=str(e))
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/api/agora/token")
async def agora_token(channel: str = "kataru-voice", uid: int = 0):
    return agora_agent.get_token(channel, uid)


@app.get("/api/agora/agent")
async def agora_agent_config():
    return {
        "configured": agora_agent.config.is_configured(),
        "agent": agora_agent.create_agent_config(),
        "features": {
            "voice": True,
            "multilingual": True,
            "interruption_handling": True,
            "background_noise_resilience": True,
            "low_confidence_detection": True,
            "human_escalation": True,
            "context_preservation": True,
        },
        "safety": {
            "no_medical_diagnosis": True,
            "no_emergency_replacement": True,
            "no_legal_advice": True,
            "no_financial_advice": True,
            "no_uncertain_facts": True,
        },
    }


@app.post("/api/agora/channel/start")
async def agora_channel_start(req: VoiceSessionRequest):
    channel_name = req.session_id or f"kataru-{uuid.uuid4().hex[:8]}"
    return agora_agent.start_channel(channel_name, req.user_id)


@app.post("/api/agora/channel/end/{channel_name}")
async def agora_channel_end(channel_name: str):
    return agora_agent.end_channel(channel_name)


@app.get("/api/agora/channel/{channel_name}")
async def agora_channel_status(channel_name: str):
    return agora_agent.get_channel_status(channel_name)


@app.get("/api/voice/status")
async def voice_status():
    return {
        "pipeline": "active",
        "sessions": len(voice_pipeline.sessions),
        "agora_configured": agora_agent.config.is_configured(),
        "stt": "deepgram" if config.deepgram_api_key and not config.deepgram_api_key.startswith("dummy") else "browser",
        "llm": "openai" if config.openai_api_key and not config.openai_api_key.startswith("dummy") else "research",
        "tts": "elevenlabs" if config.elevenlabs_api_key and not config.elevenlabs_api_key.startswith("dummy") else "browser",
    }


@app.post("/api/research")
async def research_endpoint(req: ChatRequest):
    result = await research_engine.research(req.text)
    return result


if __name__ == "__main__":
    uvicorn.run(app, host=config.host, port=config.port)
