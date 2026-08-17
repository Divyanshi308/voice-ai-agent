import asyncio
import base64
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import config
from pipeline import AudioPipeline
from agora_integration import AgoraTokenGenerator, AgoraVoiceEngine

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

pipeline: AudioPipeline = None  # type: ignore[assignment]
agora_engine: AgoraVoiceEngine = None  # type: ignore[assignment]
agora_token_gen: AgoraTokenGenerator = None  # type: ignore[assignment]
start_time = time.time()


class AgoraTokenRequest(BaseModel):
    channel_name: str
    uid: str = "0"
    role: int = 1


class AgoraAudioRequest(BaseModel):
    session_id: str
    audio_data: str


class TestRequest(BaseModel):
    text: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline, agora_engine, agora_token_gen

    pipeline = AudioPipeline()
    await pipeline.initialize()

    agora_engine = AgoraVoiceEngine()
    agora_token_gen = AgoraTokenGenerator(
        app_id=config.agora_app_id,
        app_certificate=config.agora_app_certificate,
    )

    logger.info("voice_ai_agent_started", port=config.port, host=config.host)
    yield
    await pipeline.shutdown()
    logger.info("voice_ai_agent_stopped")


app = FastAPI(title="Voice AI Agent", lifespan=lifespan)

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
    agora_stats = agora_engine.get_stats() if agora_engine else {}
    return {
        "active_calls": len(pipeline.active_calls),
        "total_calls": pipeline.analytics._total_calls if hasattr(pipeline.analytics, "_total_calls") else len(pipeline.active_calls),
        "active_agora_sessions": agora_stats.get("active_sessions", 0),
        "total_agora_sessions": agora_stats.get("total_sessions", 0),
    }


@app.post("/agora/token")
async def generate_agora_token(req: AgoraTokenRequest):
    if not config.agora_app_id:
        return {
            "token": "",
            "app_id": "",
            "channel": req.channel_name,
            "uid": req.uid,
            "message": "No Agora credentials configured. Using demo mode.",
        }

    token = agora_token_gen.generate_rtc_token(
        channel_name=req.channel_name,
        uid=req.uid,
        role=req.role,
    )
    return {
        "token": token,
        "app_id": config.agora_app_id,
        "channel": req.channel_name,
        "uid": req.uid,
    }


@app.post("/agora/session/start")
async def start_agora_session(req: AgoraTokenRequest):
    import uuid
    session_id = str(uuid.uuid4())

    async def on_transcript(text, confidence, language, is_final):
        if is_final:
            logger.info("agora_transcript", session_id=session_id, text=text)

    session = agora_engine.create_session(
        session_id=session_id,
        channel_name=req.channel_name,
        uid=req.uid,
        on_transcript=on_transcript,
    )

    call_id = session_id
    async def send_audio_fn(chunk: str):
        logger.debug("agora_audio_out", session_id=session_id, size=len(chunk))

    await pipeline.handle_incoming_call(call_id, req.uid, send_audio_fn)

    return {
        "session_id": session_id,
        "channel_name": req.channel_name,
        "status": "active",
    }


@app.post("/agora/session/{session_id}/audio")
async def process_agora_audio(session_id: str, req: AgoraAudioRequest):
    if session_id not in pipeline.active_calls:
        raise HTTPException(status_code=404, detail="Session not found")

    import base64
    audio_bytes = base64.b64decode(req.audio_data)
    await pipeline.asr.send_audio(audio_bytes)

    return {"status": "received"}


@app.post("/agora/session/{session_id}/stop")
async def stop_agora_session(session_id: str):
    if session_id in pipeline.active_calls:
        await pipeline.end_call(session_id)
    agora_engine.end_session(session_id)
    return {"status": "ended"}


@app.post("/test")
async def test_endpoint(req: TestRequest):
    call_id = str(uuid4())
    
    if not config.openai_api_key or config.openai_api_key.startswith("dummy"):
        return {
            "input": req.text, 
            "response": f"I received your message: '{req.text}'. This is a demo response. To get real AI responses, add your OpenAI API key to the .env file.", 
            "call_id": call_id
        }

    results: dict = {"response": "", "done": False}

    async def send_audio_fn(chunk: str):
        results["response"] += chunk

    state = pipeline.dialogue.create_state(call_id, "test_caller")
    pipeline.active_calls[call_id] = {
        "state": state,
        "send_audio": send_audio_fn,
        "transcript_log": [],
        "ticket_id": None,
    }

    try:
        full_response = ""
        history = state["conversation_history"][-10:]
        async for token in pipeline.llm.get_response(req.text, history, state):
            full_response += token

        await pipeline.end_call(call_id)
        return {"input": req.text, "response": full_response, "call_id": call_id}
    except Exception as e:
        logger.error("test_endpoint_error", call_id=call_id, error=str(e))
        await pipeline.end_call(call_id)
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/audio")
async def ws_audio(websocket: WebSocket):
    await websocket.accept()
    call_id = str(uuid4())

    caller_id = websocket.query_params.get("caller_id", "unknown")
    logger.info("ws_connection_opened", call_id=call_id, caller_id=caller_id)

    async def send_audio_fn(encoded_chunk: str):
        try:
            await websocket.send_json({
                "event": "media",
                "media": {"payload": encoded_chunk},
            })
        except Exception:
            pass

    try:
        await pipeline.handle_incoming_call(call_id, caller_id, send_audio_fn)

        while True:
            try:
                data = await websocket.receive_json()
            except Exception:
                break

            event = data.get("event")

            if event == "media":
                audio_payload = data.get("media", {}).get("payload", "")
                if audio_payload:
                    audio_bytes = base64.b64decode(audio_payload)
                    await pipeline.asr.send_audio(audio_bytes)
            elif event == "stop":
                break

    except WebSocketDisconnect:
        logger.info("ws_disconnect", call_id=call_id)
    except Exception as e:
        logger.error("ws_error", call_id=call_id, error=str(e))
    finally:
        await pipeline.end_call(call_id)
        logger.info("ws_connection_closed", call_id=call_id)


if __name__ == "__main__":
    uvicorn.run(app, host=config.host, port=config.port)
