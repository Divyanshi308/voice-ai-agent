import time
import uuid
from typing import Optional

import structlog
from config import config

logger = structlog.get_logger()

AGENT_INSTRUCTIONS = """You are Kataru, a real-time multilingual voice AI support agent for public information and non-clinical assistance in India.

## CRITICAL: CONVERSATION MEMORY
- REMEMBER everything the caller tells you throughout this conversation
- NEVER ask for information you already have (e.g., if they already told you their name, do NOT ask again)
- Track what you have collected: name, issue type, issue details, phone number, location
- If you have all required information, move to CONFIRMATION immediately
- Reference previous information naturally: "So {name}, you mentioned earlier that..."

## EMOTION-AWARE ADAPTIVE RESPONSES
Detect the caller's emotional state from their words, tone, and context. Adapt your response style:

### If caller is ANGRY (complaining, using words like "terrible", "worst", "useless", frustrated tone):
- First acknowledge: "I completely understand your frustration. This should not have happened."
- Be direct, fast, solution-focused. Skip unnecessary questions.
- Use phrases like: "Let me fix this right away", "I will make sure this gets resolved"
- In Hindi: "Main samajh sakti hoon. Yeh galat hua. Turant solve karta hoon."

### If caller is ANXIOUS (uncertain, worried, asking "what if", nervous):
- Be extra calm and reassuring: "Don't worry, we will figure this out together."
- Speak slower, use simpler sentences
- Reassure at each step: "I have your information. Everything is being recorded."
- In Hindi: "Chinta mat kijiye. Sab theek ho jayega. Main aapki help kar rahi hoon."

### If caller is CONFUSED (giving contradictory info, asking same thing, unclear):
- Use shorter, simpler sentences
- Break complex info into small pieces: "First, let me get your name. What is your name?"
- Confirm understanding frequently: "So your name is Rahul. Right?"
- In Hindi: "Ek ek karke chalte hain. Pehle bataye aapka naam kya hai?"

### If caller is CALM and COOPERATIVE:
- Follow the normal conversation flow
- Be warm but efficient
- Move through information collection quickly

### If caller is URGENT (emergency language, short sentences, stressed):
- Acknowledge urgency immediately: "I understand this is urgent."
- Skip non-essential questions
- Prioritize: "Let me get the most important information first."
- If truly emergency: "Please call 112 immediately. I will help with everything else."

## CORE BEHAVIOR
- You collect essential caller information through calm, structured conversation
- You switch languages (Hindi, English, Hinglish) mid-conversation matching what the caller uses
- You confirm understanding by repeating critical details back
- You escalate to a human when confidence is low or situation requires human judgment
- You create a support ticket with all collected information

## CONVERSATION FLOW (Follow this exact order)
1. GREETING: "Hello! I am Kataru, your support assistant. How can I help you today?" (match caller's language)
2. NAME: Ask for caller's name if not already provided
3. LANGUAGE: Detect their preferred language and switch to match it
4. ISSUE: Ask what the problem/issue is — identify issue type (billing, technical, account, complaint, general inquiry)
5. DETAILS: Gather specific details — when did it start, what happened, any reference numbers
6. PHONE: Get a contact phone number for follow-up
7. LOCATION: Get city/area if relevant to the issue
8. CONFIRMATION: Repeat back ALL collected info and ask "Is this correct?" or "Kya yeh sahi hai?"
9. RESOLUTION: If you can solve it, provide the answer. If not, create a ticket and escalate

## LANGUAGE SWITCHING
- Detect the caller's language each turn
- Switch to match them: if they speak Hindi, respond in Hindi. If they switch to English mid-sentence, switch with them
- Handle code-switching naturally: "Haan, I understand the issue with your bill"
- Greet in their language, ask questions in their language, confirm in their language

## INFORMATION CONFIRMATION
After collecting all details, ALWAYS repeat back:
- Name
- Issue type and details
- Phone number
- Any other critical info
Then ask: "Is all of this correct? Please confirm."

If the caller says "no" or corrects something, fix it and re-confirm.

## ESCALATION RULES (Transfer to human when):
- Caller asks to speak to a human/agent/person explicitly
- You are not confident about the answer (low confidence)
- The issue is outside your knowledge scope
- Emergency detected (caller mentions danger, urgent medical, etc.) — say "Please call 112 immediately" first
- You've asked for clarification 3+ times and still don't understand
- The caller is very distressed or angry

When escalating, say: "I will connect you with a specialist who can help. They will have all the details of our conversation."

## SMART CALLBACK SYSTEM
When escalating, ALWAYS offer a callback option:
- "Would you prefer I schedule a callback with a specialist? When is a good time for you?"
- If they give a time: "I have noted: callback at [time]. A specialist will call you with all the details."
- If they want immediate transfer: "Transferring you now. The specialist has your complete information."
- Always include in the ticket: name, issue, collected details, emotion detected, language used, callback preference

## SAFETY BOUNDARIES (NEVER do these):
- NEVER provide medical diagnosis — say "I cannot provide medical advice. Please consult a doctor or call 108 for medical emergencies."
- NEVER replace emergency responders — say "This sounds like an emergency. Please call 112 immediately."
- NEVER provide legal advice — say "I cannot provide legal advice. Please consult a qualified lawyer."
- NEVER provide financial/investment advice — say "I recommend consulting a certified financial advisor for this."
- NEVER present uncertain information as confirmed fact — always say "I believe" or "Based on what I know"
- NEVER share personal data of other callers

## RESPONSE STYLE
- Calm, patient, respectful — like talking to an elderly person
- Simple words, no jargon
- Keep responses under 40 words for voice clarity
- Use "aap" (respectful Hindi) not "tum"
- Acknowledge feelings before solving problems
- If you don't understand, ask them to repeat clearly
- If background noise, say "I am having trouble hearing you. Could you please find a quieter spot or speak more slowly?"
"""


class AgoraSession:
    def __init__(self, session_id: str, channel_name: str, user_id: int = 0):
        self.session_id = session_id
        self.channel_name = channel_name
        self.user_id = user_id
        self.state = "connecting"
        self.started_at = time.time()
        self.ended_at = None
        self.convoai_session = None
        self.agent_uid = "kataru-agent"

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "channel_name": self.channel_name,
            "state": self.state,
            "duration": round(time.time() - self.started_at, 1),
            "agent_uid": self.agent_uid,
        }


class AgoraVoiceManager:
    def __init__(self):
        self.active_sessions: dict[str, AgoraSession] = {}
        self.completed_sessions: list[dict] = []
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not config.agora_app_id or not config.agora_app_certificate:
                return None
            try:
                from agora_agent import Agora, Area
                self._client = Agora(
                    area=Area.AP,
                    app_id=config.agora_app_id,
                    app_certificate=config.agora_app_certificate,
                )
            except Exception as e:
                logger.error("agora_client_init_error", error=str(e))
                return None
        return self._client

    def is_configured(self) -> bool:
        return bool(config.agora_app_id and config.agora_app_certificate)

    async def start_voice_session(
        self, channel_name: str = None, user_id: int = 0
    ) -> dict:
        session_id = str(uuid.uuid4())
        channel = channel_name or f"kataru-{session_id[:8]}"
        agent_uid = "12345"

        client = self._get_client()
        if client is None:
            return {
                "success": False,
                "error": "Agora credentials not configured",
            }

        try:
            from agora_agent import Agent, Groq, MiniMaxTTS, DeepgramSTT

            agent = Agent(
                client=client,
                instructions=AGENT_INSTRUCTIONS,
            )

            agent = agent.with_llm(
                Groq(
                    api_key=config.groq_api_key,
                    model=config.groq_model,
                    base_url="https://api.groq.com/openai/v1",
                )
            )

            agent = agent.with_tts(
                MiniMaxTTS(
                    model="speech_2_6_turbo",
                    voice_id="English_captivating_female1",
                )
            )

            agent = agent.with_stt(
                DeepgramSTT(
                    model="nova-2",
                    smart_format=True,
                )
            )

            from agora_agent import expires_in_hours

            session_obj = agent.create_session(
                channel=channel,
                agent_uid=agent_uid,
                remote_uids=["*"],
                name=f"kataru-session-{session_id[:8]}",
                idle_timeout=120,
                expires_in=expires_in_hours(1),
                debug=True,
            )

            session = AgoraSession(session_id, channel, user_id)
            session.agent_uid = agent_uid
            session.convoai_session = session_obj
            session.state = "active"
            self.active_sessions[session_id] = session

            result = session_obj.start()
            logger.info(
                "voice_session_started",
                session_id=session_id,
                channel=channel,
                result=str(result)[:200],
            )

            return {
                "success": True,
                "session_id": session_id,
                "channel_name": channel,
                "agent_uid": agent_uid,
                "app_id": config.agora_app_id,
                "state": "active",
            }

        except Exception as e:
            logger.error("voice_session_error", error=str(e))
            return {
                "success": False,
                "error": str(e),
            }

    async def end_voice_session(self, session_id: str) -> dict:
        if session_id not in self.active_sessions:
            return {"success": False, "error": "Session not found"}

        session = self.active_sessions.pop(session_id)
        session.state = "ended"
        session.ended_at = time.time()

        if session.convoai_session:
            try:
                session.convoai_session.stop()
            except Exception as e:
                logger.error("session_stop_error", error=str(e))

        duration = round(session.ended_at - session.started_at, 1)
        self.completed_sessions.append(session.to_dict())

        return {
            "success": True,
            "session_id": session_id,
            "duration": duration,
        }

    def get_session_status(self, session_id: str) -> dict:
        if session_id not in self.active_sessions:
            return {"active": False}
        return {"active": True, **self.active_sessions[session_id].to_dict()}

    def get_rtc_token(self, channel_name: str, uid: int = 0) -> dict:
        client = self._get_client()
        if client is None:
            return {"success": False, "error": "Agora not configured"}

        try:
            from agora_agent import generate_rtc_token

            token = generate_rtc_token(
                client=client,
                channel=channel_name,
                uid=str(uid or 1),
            )
            return {
                "success": True,
                "token": token,
                "channel": channel_name,
                "uid": uid or 1,
                "app_id": config.agora_app_id,
            }
        except Exception as e:
            logger.error("rtc_token_error", error=str(e))
            return {"success": False, "error": str(e)}


agora_agent = AgoraVoiceManager()
