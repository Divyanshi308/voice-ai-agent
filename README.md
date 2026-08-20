<div align="center">

# Kataru (語る)

**Build AI That Speaks, Listens, and Acts**

*語る — Japanese for "to speak" / "to tell a story"*

---

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Agora](https://img.shields.io/badge/Agora%20ConvoAI-SDK-blue?style=for-the-badge)](https://www.agora.io)
[![Groq](https://img.shields.io/badge/Groq-LLM-FF6B00?style=for-the-badge)](https://groq.com)
[![Deepgram](https://img.shields.io/badge/Deepgram-STT-1E3A5F?style=for-the-badge)](https://deepgram.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)

*Real-time multilingual voice AI for customer assistance and non-clinical support*

**[Live Demo](https://voice-ai-agent-37b8.onrender.com)** · Built for [EchoSphere by KNOTiC](https://knotichq.com) · PS51

</div>

---

## The Problem

India has 600M+ phone-only users who call support lines daily. They face:

- **Language barriers** — Hindi speakers forced through English-only IVR menus
- **Lost context** — every call starts from zero; no memory of prior conversations
- **Emotional distress** — stressed callers get robotic, tone-deaf responses
- **No escalation path** — AI either solves it perfectly or fails completely
- **Code-switching** — real users mix Hindi and English mid-sentence ("Haan, I understand the billing issue")

Existing solutions are expensive human-only call centers or rigid chatbots that can't hold a real conversation.

## The Solution

Kataru is a **real-time multilingual voice AI agent** that handles the full call lifecycle — from greeting to resolution — in Hindi, English, or Hinglish. It detects emotion, follows a structured conversation flow, creates support tickets, escalates with full context, and never crosses safety boundaries.

---

## Architecture

```
                          ┌──────────────────────────────────────────────┐
                          │              Kataru (語る)                    │
                          │         Real-Time Voice AI Agent             │
                          └──────────────────────────────────────────────┘

  ┌────────────┐    ┌─────────────┐    ┌──────────────┐    ┌─────────────┐
  │   Caller   │◄──►│  Agora RTC  │◄──►│   Agora      │◄──►│   Groq      │
  │  (Phone /  │    │   SDK       │    │   ConvoAI    │    │   LLM       │
  │  Browser)  │    │  Real-time  │    │   Engine     │    │  (Response  │
  │            │    │  Audio      │    │  Orchestrator│    │   Gen)      │
  └────────────┘    └──────┬──────┘    └──────┬───────┘    └──────┬──────┘
                           │                   │                    │
                           │            ┌──────┴───────┐    ┌──────┴──────┐
                           │            │   Deepgram   │    │   MiniMax   │
                           │            │    STT       │    │    TTS      │
                           │            │  (Speech →   │    │  (Text →    │
                           │            │    Text)     │    │    Speech)  │
                           │            └──────────────┘    └─────────────┘
                           │
                    ┌──────┴──────────────────────────────────┐
                    │           FastAPI Backend                │
                    │  ┌──────────┐ ┌──────────┐ ┌─────────┐ │
                    │  │ Guardrails│ │ Dialogue │ │Ticketing│ │
                    │  │ (Safety) │ │ (Flow)   │ │(Zendesk)│ │
                    │  └──────────┘ └──────────┘ └─────────┘ │
                    │  ┌──────────┐ ┌──────────┐ ┌─────────┐ │
                    │  │ Analytics│ │Research  │ │  Auth   │ │
                    │  │ (Metrics)│ │(Web Srch)│ │(OAuth2) │ │
                    │  └──────────┘ └──────────┘ └─────────┘ │
                    └──────────────────┬──────────────────────┘
                                       │
                              ┌────────┴────────┐
                              │     SQLite      │
                              │  (Users, Chat,  │
                              │   Tickets,      │
                              │   Analytics)    │
                              └─────────────────┘
```

### How It Works

1. **Caller speaks** into their phone or browser
2. **Agora RTC** streams audio with ultra-low latency (<500ms)
3. **Agora ConvoAI Engine** orchestrates the pipeline:
   - Deepgram converts speech to text (Hindi/English/Hinglish)
   - Groq LLM generates an emotionally-aware, context-preserved response
   - MiniMax converts text to natural speech
4. **Guardrails** check the response for safety boundaries in real-time
5. **Response streams** back through Agora RTC
6. **Full context** is preserved — caller never repeats themselves

---

## Features

### Multilingual Intelligence
- **Hindi / English / Hinglish** — matches caller's language automatically
- **Code-switching** — handles "Haan, I understand the billing issue" seamlessly
- **Respectful tone** — uses "aap" (formal Hindi), never "tum"
- **Language detection** — identifies script and vocabulary patterns in real-time

### Guided Conversation Flow
- **8-phase structured flow** — Greeting → Name → Issue → Details → Confirmation → Resolution → Escalation → Farewell
- **Info collection** — name, phone, issue type, urgency, location
- **Confirmation loop** — repeats collected info back and asks "Is this correct?"

### Emotion-Aware Responses
- Detects **angry, anxious, confused, urgent, calm** emotional states
- Adapts tone, pacing, and word choice per emotion
- Acknowledges feelings before solving problems

### Ticketing System
- Creates support tickets with full conversation context
- Priority assignment based on urgency and escalation triggers
- Status tracking (open → escalated → resolved)

### Escalation with Context Preservation
- Transfers to human specialist with complete call summary
- **Smart callbacks** — offers to schedule a callback at the caller's preferred time
- Includes: name, issue, emotion detected, language, and all collected info

### Safety Guardrails
- **Medical** — blocks diagnoses, redirects to emergency services (108/112)
- **Legal** — refuses legal advice, suggests consulting a lawyer
- **Financial** — refuses investment advice, suggests certified advisors
- **Emergency** — auto-detects and immediately provides 112 / 100 / 108

### Real-Time Analytics Dashboard
- Active session count and call metrics
- Language distribution and emotion breakdown
- Escalation rates and resolution times
- Live conversation monitoring

### Authentication
- **Google OAuth 2.0** — one-click sign-in
- **Local accounts** — email/password signup
- **Session persistence** — chat history preserved across sessions

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Frontend** | HTML5, JavaScript, Three.js | 3D interactive dashboard with voice UI |
| **Backend** | Python 3.11+, FastAPI | REST API, WebSocket, session management |
| **Voice** | Agora RTC + ConvoAI SDK | Real-time audio streaming & pipeline orchestration |
| **LLM** | Groq (compound-mini) | Ultra-fast response generation (<100ms) |
| **STT** | Deepgram (nova-3) | Multilingual speech-to-text (Hindi/English) |
| **TTS** | MiniMax | Natural multilingual text-to-speech |
| **Database** | SQLite | Users, chat history, tickets, analytics |
| **Auth** | Google OAuth 2.0 | One-click sign-in |
| **Hosting** | Render | Cloud deployment |

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/Divyanshi308/voice-ai-agent.git
cd voice-ai-agent
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv venv
venv\Scripts\activate       # Windows
source venv/bin/activate    # macOS/Linux
pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file in the project root:

```env
# === REQUIRED ===
AGORA_APP_ID=your_agora_app_id
AGORA_APP_CERTIFICATE=your_agora_app_certificate
GROQ_API_KEY=your_groq_api_key

# === OPTIONAL (BYOK — Bring Your Own Keys) ===
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
DEEPGRAM_API_KEY=your_deepgram_api_key
ELEVENLABS_API_KEY=your_elevenlabs_api_key
OPENAI_API_KEY=your_openai_key
```

### 4. Run the server

```bash
python main.py
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

---

## Live Demo

| | |
|---|---|
| **URL** | [https://voice-ai-agent-37b8.onrender.com](https://voice-ai-agent-37b8.onrender.com) |
| **Username** | `demo` |
| **Password** | `demo123` |

### What to Try

1. **Start a voice session** — click the microphone button
2. **Say "Namaste"** — AI responds in Hindi
3. **Switch to English mid-sentence** — AI follows your language
4. **Interrupt the AI** — it stops and listens (barge-in)
5. **Say "I need help with my electricity bill"** — guided flow kicks in
6. **Check the dashboard** — real-time analytics update live

---

## API Endpoints

### Authentication
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/auth/signup` | Create a new account |
| `POST` | `/api/auth/login` | Email/password login |
| `POST` | `/api/auth/google` | Google OAuth sign-in |
| `GET`  | `/api/auth/google/redirect` | Redirect to Google OAuth |

### Voice
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/voice/session` | Create a voice session |
| `POST` | `/api/voice/text` | Send text to voice pipeline |
| `WS`   | `/ws/voice` | WebSocket for real-time voice |
| `POST` | `/api/voice/interrupt/{session_id}` | Handle interruption |
| `POST` | `/api/voice/end/{session_id}` | End voice session |

### Agora
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/api/agora/token` | Generate RTC token |
| `GET`  | `/api/agora/config` | Check Agora configuration |
| `POST` | `/api/agora/voice/start` | Start Agora voice session |
| `POST` | `/api/agora/voice/end/{session_id}` | End Agora voice session |

### Tickets & Support
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/tickets/create` | Create support ticket |
| `POST` | `/api/tickets/escalate` | Escalate to human |
| `POST` | `/api/tickets/{ticket_id}/resolve` | Resolve ticket |
| `GET`  | `/api/tickets/{user_id}` | Get user tickets |
| `POST` | `/api/callbacks/schedule` | Schedule a callback |

### User & Analytics
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/api/user/{user_id}` | Get user profile + stats |
| `PUT`  | `/api/user/{user_id}` | Update user profile |
| `GET`  | `/api/user/{user_id}/chats` | Get chat history |
| `GET`  | `/api/analytics` | Get analytics dashboard data |
| `GET`  | `/health` | Health check with uptime |
| `GET`  | `/metrics` | Active session count |

---

## Project Structure

```
voice-ai-agent/
├── main.py                 # FastAPI app, routes, SmartAgent chatbot
├── config.py               # Pydantic settings (env vars)
├── voice_pipeline.py       # Conversation state machine & voice flow
├── voice_manager.py        # Agora ConvoAI SDK integration
├── pipeline.py             # Audio pipeline: STT → LLM → TTS orchestration
├── asr.py                  # Deepgram speech-to-text streaming
├── tts.py                  # ElevenLabs text-to-speech streaming
├── llm.py                  # OpenAI/Groq LLM engine
├── guardrails.py           # Safety boundary enforcement
├── dialogue.py             # Dialogue state & field collection manager
├── ticketing.py            # Zendesk ticket management
├── notifications.py        # Twilio SMS notifications
├── analytics.py            # PostgreSQL analytics logger
├── research.py             # Web search engine (DuckDuckGo/Brave)
├── database.py             # SQLite: users, chats, tickets, context
├── static/
│   └── index.html          # Frontend: 3D dashboard with voice UI
├── tests/                  # Test suite
├── requirements.txt        # Python dependencies
├── Dockerfile              # Container build
├── docker-compose.yml      # Multi-service orchestration
├── railway.json            # Railway deployment config
├── nixpacks.toml           # Nixpacks build config
├── kataru.db               # SQLite database (auto-created)
└── .env                    # Environment variables (not committed)
```

---

## Project Name

**Kataru (語る)** — Japanese for "to speak" or "to tell a story"

The name reflects the project's core mission: giving voice to people who need help, and letting them tell their story to an AI that actually listens and remembers.

---

## The Hackathon

Built for **[EchoSphere 2026](https://unstop.com/hackathons/echosphere-agora-conversational-ai-hackathon-knotic-1723695)** — a conversational AI hackathon by **KNOTiC**, tackling **PS51**: Real-time multilingual voice AI for customer assistance.

---

## Acknowledgments

| Service | Role |
|---------|------|
| [Agora](https://www.agora.io) | ConvoAI SDK — voice pipeline orchestration |
| [Groq](https://groq.com) | Ultra-fast LLM inference |
| [Deepgram](https://deepgram.com) | Multilingual speech-to-text |
| [MiniMax](https://www.minimaxi.com) | Natural text-to-speech |
| [KNOTiC](https://knotichq.com) | Hackathon organizer |

---

## License

MIT

---

<div align="center">

**Kataru (語る)** — *Build AI That Speaks, Listens, and Acts*

</div>
